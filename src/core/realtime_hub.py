#!/usr/bin/env python3
"""Real-time request event hub that manages WebSocket connections."""
import asyncio
import json
import uuid
from typing import Dict, List, Set, Optional
from fastapi import WebSocket
from dataclasses import dataclass, asdict
from datetime import datetime
import logging
import traceback

@dataclass
class RealTimeRequest:
    """Data structure representing the state of a real-time request."""
    request_id: str
    service: str
    channel: str
    method: str
    path: str
    start_time: str  # ISO formatted timestamp
    status: str  # PENDING/STREAMING/COMPLETED/FAILED
    duration_ms: int = 0
    status_code: Optional[int] = None
    request_headers: Optional[Dict] = None
    response_chunks: Optional[List[str]] = None
    response_truncated: bool = False
    target_url: Optional[str] = None

    def __post_init__(self):
        if self.response_chunks is None:
            self.response_chunks = []
        if self.request_headers is None:
            self.request_headers = {}

class RealTimeRequestHub:
    """Real-time event hub for broadcasting proxy request lifecycle updates."""

    def __init__(self, service_name: str, max_requests: int = 100):
        self.service_name = service_name
        self.max_requests = max_requests
        self.connections: Set[WebSocket] = set()
        self.active_requests: Dict[str, RealTimeRequest] = {}
        self.logger = logging.getLogger(f"realtime.{service_name}")

        # Configure logging only once per service
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    async def connect(self, websocket: WebSocket):
        """Accept a new WebSocket connection."""
        try:
            await websocket.accept()
            self.connections.add(websocket)
            self.logger.info(f"WebSocket connected, total: {len(self.connections)}")

            # Send a snapshot of currently active requests
            await self._send_snapshot(websocket)
        except Exception as e:
            self.logger.error(f"WebSocket connection failed: {e}")
            raise

    def disconnect(self, websocket: WebSocket):
        """Handle a disconnected WebSocket."""
        self.connections.discard(websocket)
        self.logger.info(f"WebSocket disconnected, total: {len(self.connections)}")

    async def _send_snapshot(self, websocket: WebSocket):
        """Send the current active-request snapshot to the client."""
        if not self.active_requests:
            return

        try:
            for request in list(self.active_requests.values()):
                await websocket.send_text(json.dumps({
                    "type": "snapshot",
                    **asdict(request)
                }, ensure_ascii=False))
        except Exception as e:
            self.logger.error(f"Failed to send snapshot: {e}")

    async def broadcast_event(self, event_type: str, request_id: str, **data):
        """Broadcast an event to every connected WebSocket."""
        if not self.connections:
            return

        event_data = {
            "type": event_type,
            "request_id": request_id,
            "service": self.service_name,
            "timestamp": datetime.now().isoformat(),
            **data
        }

        message = json.dumps(event_data, ensure_ascii=False)
        disconnected = set()

        for i, connection in enumerate(self.connections):
            try:
                await connection.send_text(message)
            except Exception as e:
                self.logger.warning(f"Failed to send message: {e}")
                disconnected.add(connection)

        # Remove any connections that failed while sending
        if disconnected:
            self.connections -= disconnected
            self.logger.info(f"Removed {len(disconnected)} disconnected connections")


    async def request_started(self, request_id: str, method: str, path: str,
                            channel: str, headers: Dict, target_url: str = None):
        """Record the start of a request and broadcast the event."""
        try:

            request = RealTimeRequest(
                request_id=request_id,
                service=self.service_name,
                channel=channel,
                method=method,
                path=path,
                start_time=datetime.now().isoformat(),
                status="PENDING",
                request_headers=self._sanitize_headers(headers),
                target_url=target_url
            )

            self.active_requests[request_id] = request
            self._cleanup_old_requests()

            # Avoid double-including request_id in the payload
            request_data = asdict(request)
            request_data.pop('request_id', None)
            await self.broadcast_event("started", request_id, **request_data)
            self.logger.debug(f"Request started: {request_id} - {method} {path}")
        except Exception as e:
            self.logger.error(f"Failed to record request start: {e}\n{traceback.format_exc()}")

    async def request_streaming(self, request_id: str, duration_ms: int):
        """Mark a request as streaming and broadcast progress."""
        try:
            if request_id in self.active_requests:
                self.active_requests[request_id].status = "STREAMING"
                self.active_requests[request_id].duration_ms = duration_ms

                await self.broadcast_event("progress", request_id,
                                         status="STREAMING", duration_ms=duration_ms)
                self.logger.debug(f"Request streaming: {request_id} - {duration_ms}ms")
        except Exception as e:
            self.logger.error(f"Failed to update streaming state: {e}")

    async def response_chunk(self, request_id: str, chunk: str, duration_ms: int):
        """Append a response chunk and broadcast incremental progress."""
        try:
            if request_id not in self.active_requests:
                return

            request = self.active_requests[request_id]

            # Cap the total buffered response length to avoid unbounded memory usage
            current_length = sum(len(c) for c in request.response_chunks)
            if current_length < 2 * 1024 * 1024:  # 2MB limit
                request.response_chunks.append(chunk)
            else:
                if not request.response_truncated:
                    request.response_truncated = True
                    request.response_chunks.append("[...response too long; content truncated...]")

            request.duration_ms = duration_ms

            # Only broadcast meaningful chunks
            if chunk.strip():
                await self.broadcast_event("progress", request_id,
                                         response_delta=chunk,
                                         duration_ms=duration_ms,
                                         response_truncated=request.response_truncated)
        except Exception as e:
            self.logger.error(f"Failed to process response chunk: {e}")

    async def request_warning(self, request_id: str, message: str, duration_ms: int):
        """Broadcast a warning event for a long-running request."""
        try:
            if request_id in self.active_requests:
                self.active_requests[request_id].duration_ms = duration_ms
            await self.broadcast_event(
                "warning",
                request_id,
                status="PENDING",
                duration_ms=duration_ms,
                warning=message,
            )
            self.logger.warning(f"Request warning: {request_id} - {message}")
        except Exception as e:
            self.logger.error(f"Failed to emit request warning: {e}")

    async def request_completed(self, request_id: str, status_code: int,
                              duration_ms: int, success: bool = True):
        """Mark a request as completed or failed and broadcast the result."""
        try:
            if request_id not in self.active_requests:
                return

            request = self.active_requests[request_id]
            request.status = "COMPLETED" if success else "FAILED"
            request.status_code = status_code
            request.duration_ms = duration_ms

            await self.broadcast_event("completed" if success else "failed",
                                     request_id,
                                     status=request.status,
                                     status_code=status_code,
                                     duration_ms=duration_ms)

            self.logger.debug(f"Request completed: {request_id} - {status_code} - {duration_ms}ms")

            # Delay clean-up so the UI has time to display the result
            asyncio.create_task(self._delayed_cleanup(request_id, 30))
        except Exception as e:
            self.logger.error(f"Failed to mark request complete: {e}")

    async def _delayed_cleanup(self, request_id: str, delay_seconds: int):
        """Remove a completed request after the given delay."""
        try:
            await asyncio.sleep(delay_seconds)
            if request_id in self.active_requests:
                self.active_requests.pop(request_id, None)
                self.logger.debug(f"Cleaned request: {request_id}")
        except Exception as e:
            self.logger.error(f"Failed to clean up request after delay: {e}")

    def _cleanup_old_requests(self):
        """Discard the oldest requests when exceeding the retention limit."""
        try:
            if len(self.active_requests) > self.max_requests:
                # Keep only the most recent requests
                sorted_requests = sorted(
                    self.active_requests.items(),
                    key=lambda x: x[1].start_time,
                    reverse=True
                )

                old_count = len(self.active_requests)
                self.active_requests = dict(sorted_requests[:self.max_requests])
                cleaned_count = old_count - len(self.active_requests)

                if cleaned_count > 0:
                    self.logger.info(f"Removed {cleaned_count} old requests")
        except Exception as e:
            self.logger.error(f"Failed to purge old requests: {e}")

    def _sanitize_headers(self, headers: Dict) -> Dict:
        """Mask sensitive headers before broadcasting."""
        if not headers:
            return {}

        try:
            sensitive_headers = {'authorization', 'x-api-key', 'cookie'}
            return {
                k: v if k.lower() not in sensitive_headers else "[hidden]"
                for k, v in headers.items()
            }
        except Exception as e:
            self.logger.error(f"Failed to sanitize headers: {e}")
            return {}

    def get_connection_count(self) -> int:
        """Return the number of active WebSocket connections."""
        return len(self.connections)

    def get_active_request_count(self) -> int:
        """Return the number of currently tracked requests."""
        return len(self.active_requests)
