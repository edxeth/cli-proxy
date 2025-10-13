#!/usr/bin/env python3
"""Base proxy service implementation shared by the Claude and Codex proxies."""
import asyncio
import base64
import json
import subprocess
import sys
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse

from ..utils.usage_parser import (
    extract_usage_from_response,
    normalize_usage_record,
    empty_metrics,
    merge_usage_metrics,
)
from ..utils.platform_helper import create_detached_process
from .realtime_hub import RealTimeRequestHub

class BaseProxyService(ABC):
    """Base proxy service implementation."""
    
    def __init__(self, service_name: str, port: int, config_manager):
        """
        Initialise the proxy service.

        Args:
            service_name: Service identifier (claude/codex)
            port: Service port
            config_manager: Configuration manager instance
        """
        self.service_name = service_name
        self.port = port
        self.config_manager = config_manager

        # Initialise runtime paths
        self.config_dir = Path.home() / '.clp/run'
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.pid_file = self.config_dir / f'{service_name}_proxy.pid'
        self.log_file = self.config_dir / f'{service_name}_proxy.log'

        # Data directory
        self.data_dir = Path.home() / '.clp/data'
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.traffic_log = self.data_dir / 'proxy_requests.jsonl'
        old_log = self.data_dir / 'traffic_statistics.jsonl'
        if not self.traffic_log.exists() and old_log.exists():
            try:
                old_log.rename(self.traffic_log)
            except OSError:
                # If renaming fails keep the old path as-is
                self.traffic_log = old_log

        # Routing configuration file
        self.routing_config_file = self.data_dir / 'model_router_config.json'
        self.routing_config = self._load_routing_config()
        self.routing_config_signature = self._get_file_signature(self.routing_config_file)

        # Load-balancing configuration file
        self.lb_config_file = self.data_dir / 'lb_config.json'
        self.lb_config = self._load_lb_config()
        self.lb_config_signature = self._get_file_signature(self.lb_config_file)

        # Create async HTTP client
        self.client = self._create_async_client()

        # Maximum response bytes to store in logs (protect memory during long streams)
        self.max_logged_response_bytes = 1024 * 1024  # 1MB

        # Real-time event hub
        self.realtime_hub = RealTimeRequestHub(service_name)

        # FastAPI application wiring
        self.app = FastAPI()
        self._setup_routes()
        self.app.add_event_handler("shutdown", self._shutdown_event)

        # Import request filters
        try:
            from ..filter.cached_request_filter import CachedRequestFilter
            self.request_filter = CachedRequestFilter()
        except ImportError:
            # Fallback to the non-cached version when the cached variant is unavailable
            from ..filter.request_filter import filter_request_data
            self.filter_request_data = filter_request_data
            self.request_filter = None
    
    def _create_async_client(self) -> httpx.AsyncClient:
        """Create and configure an httpx AsyncClient."""
        timeout = httpx.Timeout(  # Allow long-running streaming responses
            timeout=None,
            connect=30.0,
            read=None,
            write=30.0,
            pool=None,
        )
        limits = httpx.Limits(
            max_connections=200,
            max_keepalive_connections=100,
        )
        return httpx.AsyncClient(timeout=timeout, limits=limits, headers={"Connection": "keep-alive"})

    async def _shutdown_event(self):
        """FastAPI shutdown hook used to dispose the HTTP client."""
        await self.client.aclose()

    def _setup_routes(self):
        """Register the FastAPI routes."""
        @self.app.api_route(
            "/{path:path}",
            methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS']
        )
        async def proxy_route(path: str, request: Request):
            return await self.proxy(path, request)

        @self.app.websocket("/ws/realtime")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket endpoint for real-time events."""
            await self.realtime_hub.connect(websocket)
            try:
                # Keep the connection alive while waiting for client messages or disconnects
                while True:
                    # Accept ping messages from the client to keep the connection active
                    try:
                        await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                    except asyncio.TimeoutError:
                        # Send ping messages to keep the channel alive
                        await websocket.send_text('{"type":"ping"}')
            except WebSocketDisconnect:
                pass
            except Exception as e:
                print(f"WebSocket connection error: {e}")
            finally:
                self.realtime_hub.disconnect(websocket)

    async def log_request(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_ms: int,
        target_headers: Optional[Dict] = None,
        filtered_body: Optional[bytes] = None,
        original_headers: Optional[Dict] = None,
        original_body: Optional[bytes] = None,
        response_content: Optional[bytes] = None,
        channel: Optional[str] = None,
        usage: Optional[Dict[str, Any]] = None,
        response_truncated: bool = False,
        total_response_bytes: Optional[int] = None,
        target_url: Optional[str] = None,
        response_headers: Optional[Dict] = None,
    ):
        """Record a request log entry to the JSONL file (offloaded to a thread)."""

        def _write_log():
            try:
                log_entry = {
                    'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
                    'service': self.service_name,
                    'method': method,
                    'path': target_url if target_url else path,
                    'status_code': status_code,
                    'duration_ms': duration_ms,
                    'target_headers': target_headers or {}
                }

                if channel:
                    log_entry['channel'] = channel

                if filtered_body:
                    log_entry['filtered_body'] = base64.b64encode(filtered_body).decode('utf-8')

                if original_headers:
                    log_entry['original_headers'] = original_headers

                if original_body:
                    log_entry['original_body'] = base64.b64encode(original_body).decode('utf-8')

                usage_record = usage
                if usage_record is None:
                    usage_record = extract_usage_from_response(self.service_name, response_content)
                usage_record = normalize_usage_record(self.service_name, usage_record)
                log_entry['usage'] = usage_record

                if response_content:
                    log_entry['response_content'] = base64.b64encode(response_content).decode('utf-8')

                if response_headers:
                    log_entry['response_headers'] = response_headers

                if response_truncated:
                    log_entry['response_truncated'] = True

                if total_response_bytes is not None:
                    log_entry['response_bytes'] = total_response_bytes

                # Keep the log file capped at the configured maximum
                self._maintain_log_limit(log_entry)
            except Exception as exc:
                print(f"Failed to record request log: {exc}")

        await asyncio.to_thread(_write_log)

    def _save_discarded_logs_usage(self, discarded_logs: list[dict]) -> None:
        """Persist usage data from discarded logs into a historical record."""
        if not discarded_logs:
            return

        try:
            # Aggregate usage data from discarded entries
            aggregated: Dict[str, Dict[str, Dict[str, int]]] = {}
            for entry in discarded_logs:
                usage = entry.get('usage', {})
                metrics = usage.get('metrics', {})
                if not metrics:
                    continue

                service = usage.get('service') or entry.get('service') or 'unknown'
                channel = entry.get('channel') or 'unknown'

                service_bucket = aggregated.setdefault(service, {})
                channel_bucket = service_bucket.setdefault(channel, empty_metrics())
                merge_usage_metrics(channel_bucket, metrics)

            if not aggregated:
                return

            # Load existing historical data if available
            history_file = self.data_dir / 'history_usage.json'
            history_usage: Dict[str, Dict[str, Dict[str, int]]] = {}

            if history_file.exists():
                try:
                    with open(history_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)

                    # Normalise previously stored metrics
                    for service, channels in (data or {}).items():
                        if not isinstance(channels, dict):
                            continue
                        service_bucket: Dict[str, Dict[str, int]] = {}
                        for channel, metrics in channels.items():
                            normalized = empty_metrics()
                            if isinstance(metrics, dict):
                                merge_usage_metrics(normalized, metrics)
                            service_bucket[channel] = normalized
                        history_usage[service] = service_bucket
                except (json.JSONDecodeError, OSError):
                    pass

            # Merge aggregated usage back into the historical record
            for service, channels in aggregated.items():
                service_bucket = history_usage.setdefault(service, {})
                for channel, metrics in channels.items():
                    channel_bucket = service_bucket.setdefault(channel, empty_metrics())
                    merge_usage_metrics(channel_bucket, metrics)

            # Persist the updated history to disk
            serializable = {
                service: {
                    channel: {key: int(value) for key, value in metrics.items()}
                    for channel, metrics in channels.items()
                }
                for service, channels in history_usage.items()
            }

            with open(history_file, 'w', encoding='utf-8') as f:
                json.dump(serializable, f, ensure_ascii=False, indent=2)

        except Exception as exc:
            print(f"Failed to record usage from discarded logs: {exc}")

    def _maintain_log_limit(self, new_log_entry: dict):
        """Maintain a bounded log file by keeping only the most recent entries."""
        try:
            # Read the log limit from the system configuration file
            system_config_file = self.data_dir / 'system.json'
            max_logs = 50  # Default value
            try:
                if system_config_file.exists():
                    with open(system_config_file, 'r', encoding='utf-8') as f:
                        system_config = json.load(f)
                        max_logs = system_config.get('logLimit', 50)
            except (json.JSONDecodeError, OSError) as e:
                print(f"Failed to read system config for log limit, using default {max_logs}: {e}")

            # Load existing log entries
            existing_logs = []
            if self.traffic_log.exists():
                with open(self.traffic_log, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                log_data = json.loads(line)
                                existing_logs.append(log_data)
                            except json.JSONDecodeError:
                                continue

            # Append the new log entry
            existing_logs.append(new_log_entry)

            # Keep only the most recent entries up to the configured limit
            if len(existing_logs) > max_logs:
                # Persist usage metrics from the entries we are about to drop
                discarded_logs = existing_logs[:-max_logs]
                self._save_discarded_logs_usage(discarded_logs)

                existing_logs = existing_logs[-max_logs:]
            
            # Rewrite the log file with the trimmed list
            with open(self.traffic_log, 'w', encoding='utf-8') as f:
                for log_entry in existing_logs:
                    f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                    
        except Exception as exc:
            print(f"Failed to enforce log file limit: {exc}")
            # If trimming fails, append the entry instead of losing it
            try:
                with open(self.traffic_log, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(new_log_entry, ensure_ascii=False) + '\n')
            except Exception as fallback_exc:
                print(f"Fallback log write failed: {fallback_exc}")

    def _get_file_signature(self, file_path: Path) -> Tuple[int, int]:
        """Return a tuple signature of file mtime and size for change detection."""
        try:
            stat_result = file_path.stat()
            return stat_result.st_mtime_ns, stat_result.st_size
        except FileNotFoundError:
            return (0, 0)
        except OSError as exc:
            print(f"Failed to read file signature ({file_path}): {exc}")
            return (0, 0)

    def _ensure_routing_config_current(self):
        """Reload routing configuration if the file has changed."""
        current_signature = self._get_file_signature(self.routing_config_file)
        if current_signature != self.routing_config_signature:
            self.routing_config = self._load_routing_config()
            self.routing_config_signature = current_signature

    def _load_routing_config(self) -> dict:
        """Load routing configuration from disk."""
        try:
            if self.routing_config_file.exists():
                with open(self.routing_config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Failed to load routing configuration: {e}")

        # Default routing configuration
        return {
            'mode': 'default',
            'modelMappings': {
                'claude': [],
                'codex': []
            },
            'configMappings': {
                'claude': [],
                'codex': []
            }
        }

    def _default_lb_config(self) -> dict:
        """Build the default load-balancing configuration."""
        return {
            'mode': 'active-first',
            'services': {
                'claude': {
                    'failureThreshold': 3,
                    'currentFailures': {},
                    'excludedConfigs': []
                },
                'codex': {
                    'failureThreshold': 3,
                    'currentFailures': {},
                    'excludedConfigs': []
                }
            }
        }

    def _ensure_lb_service_section(self, config: dict, service: str):
        """Ensure the load-balancing config has a section for the given service."""
        services = config.setdefault('services', {})
        service_section = services.setdefault(service, {})
        service_section.setdefault('failureThreshold', 3)
        service_section.setdefault('currentFailures', {})
        service_section.setdefault('excludedConfigs', [])

    def _load_lb_config(self) -> dict:
        """Load the load-balancing config from disk."""
        try:
            if self.lb_config_file.exists():
                with open(self.lb_config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            else:
                data = self._default_lb_config()
        except Exception as exc:
            print(f"Failed to load load-balancing configuration: {exc}")
            data = self._default_lb_config()

        if 'mode' not in data:
            data['mode'] = 'active-first'

        self._ensure_lb_service_section(data, 'claude')
        self._ensure_lb_service_section(data, 'codex')
        return data

    def _ensure_lb_config_current(self):
        """Reload the load-balancing config if the file has changed."""
        current_signature = self._get_file_signature(self.lb_config_file)
        if current_signature != self.lb_config_signature:
            self.lb_config = self._load_lb_config()
            self.lb_config_signature = current_signature

    def _persist_lb_config(self):
        """Persist the current load-balancing configuration to disk."""
        try:
            with open(self.lb_config_file, 'w', encoding='utf-8') as f:
                json.dump(self.lb_config, f, ensure_ascii=False, indent=2)
            self.lb_config_signature = self._get_file_signature(self.lb_config_file)
        except OSError as exc:
            print(f"Failed to save load-balancing configuration: {exc}")

    def reload_lb_config(self):
        """Force a reload of the load-balancing configuration."""
        self.lb_config = self._load_lb_config()
        self.lb_config_signature = self._get_file_signature(self.lb_config_file)

    def _apply_model_routing(self, body: bytes) -> Tuple[bytes, Optional[str]]:
        """Apply model-routing rules and return (body, config override)."""
        routing_mode = self.routing_config.get('mode', 'default')
        
        if routing_mode == 'default':
            return body, None
        
        try:
            # Parse the request body
            if not body:
                return body, None
                
            body_str = body.decode('utf-8')
            body_json = json.loads(body_str)
            
            # Extract the model name
            model = body_json.get('model')
            if not model:
                return body, None
            
            if routing_mode == 'model-mapping':
                return self._apply_model_mapping(body_json, model, body)
            elif routing_mode == 'config-mapping':
                return self._apply_config_mapping(body_json, model, body)
                
        except Exception as e:
            print(f"Failed to apply model routing: {e}")
            
        return body, None

    def _apply_model_mapping(self, body_json: dict, model: str, original_body: bytes) -> Tuple[bytes, Optional[str]]:
        """Handle model-to-model and config-to-model mapping rules."""
        mappings = self.routing_config.get('modelMappings', {}).get(self.service_name, [])

        for mapping in mappings:
            source = mapping.get('source', '').strip()
            target = mapping.get('target', '').strip()
            source_type = mapping.get('source_type', 'model').strip()

            if not source or not target:
                continue

            if source_type == 'config':
                # Config-to-model mapping
                current_config = self._get_current_active_config()
                if current_config == source:
                    body_json['model'] = target
                    modified_body = json.dumps(body_json, ensure_ascii=False).encode('utf-8')
                    print(f"Config mapping: {source} -> {target}")
                    return modified_body, None
            elif source_type == 'model':
                # Model-to-model mapping
                if model == source:
                    body_json['model'] = target
                    modified_body = json.dumps(body_json, ensure_ascii=False).encode('utf-8')
                    print(f"Model mapping: {source} -> {target}")
                    return modified_body, None

        return original_body, None

    def _apply_config_mapping(self, body_json: dict, model: str, original_body: bytes) -> Tuple[bytes, Optional[str]]:
        """Handle model-to-config mapping rules."""
        mappings = self.routing_config.get('configMappings', {}).get(self.service_name, [])
        
        for mapping in mappings:
            mapped_model = mapping.get('model', '').strip()
            target_config = mapping.get('config', '').strip()
            
            if mapped_model and target_config and model == mapped_model:
                # Ensure the target config exists
                if target_config in self.config_manager.configs:
                    print(f"Model-to-config mapping: {model} -> {target_config}")
                    return original_body, target_config
                else:
                    print(f"Model-to-config mapping failed: config {target_config} not found")
        
        return original_body, None

    def _get_current_active_config(self) -> Optional[str]:
        """Return the currently active config name (after load balancing)."""
        configs = self.config_manager.configs
        return self._select_config_by_loadbalance(configs)

    def _select_config_by_loadbalance(self, configs: Dict[str, Dict[str, Any]]) -> Optional[str]:
        """Select a config based on the current load-balancing strategy."""
        self._ensure_lb_config_current()
        mode = self.lb_config.get('mode', 'active-first')
        if mode == 'weight-based':
            selected = self._select_weighted_config(configs)
            if selected:
                return selected
        return self.config_manager.active_config

    def _select_weighted_config(self, configs: Dict[str, Dict[str, Any]]) -> Optional[str]:
        """Select a config honouring weight and failure tracking."""
        if not configs:
            return None

        service_section = self.lb_config.get('services', {}).get(self.service_name, {})
        threshold = service_section.get('failureThreshold', 3)
        failures = service_section.get('currentFailures', {})
        excluded = set(service_section.get('excludedConfigs', []))

        sorted_configs = sorted(
            configs.items(),
            key=lambda item: (-float(item[1].get('weight', 0) or 0), item[0])
        )

        for name, _ in sorted_configs:
            if failures.get(name, 0) >= threshold:
                continue
            if name in excluded:
                continue
            return name

        active_config = self.config_manager.active_config
        if active_config in configs:
            return active_config
        return sorted_configs[0][0] if sorted_configs else None

    def reload_routing_config(self):
        """Force a reload of the routing configuration."""
        self.routing_config = self._load_routing_config()
        self.routing_config_signature = self._get_file_signature(self.routing_config_file)

    def _record_lb_result(self, config_name: Optional[str], status_code: int):
        """Update load-balancing state with the outcome of a request."""
        if not config_name:
            return

        self._ensure_lb_config_current()
        if self.lb_config.get('mode', 'active-first') != 'weight-based':
            return

        self._ensure_lb_service_section(self.lb_config, self.service_name)
        service_section = self.lb_config['services'][self.service_name]
        threshold = service_section.get('failureThreshold', 3)
        failures = service_section.setdefault('currentFailures', {})
        excluded = service_section.setdefault('excludedConfigs', [])

        changed = False
        is_success = status_code is not None and 200 <= int(status_code) < 300

        if is_success:
            if failures.get(config_name, 0) != 0:
                failures[config_name] = 0
                changed = True
            if config_name in excluded:
                excluded.remove(config_name)
                changed = True
        else:
            new_count = failures.get(config_name, 0) + 1
            if failures.get(config_name) != new_count:
                failures[config_name] = new_count
                changed = True
            if new_count >= threshold and config_name not in excluded:
                excluded.append(config_name)
                changed = True

        if changed:
            self._persist_lb_config()

    def build_target_param(self, path: str, request: Request, body: bytes) -> Tuple[str, Dict, bytes, Optional[str]]:
        """Build target request parameters.

        Returns:
            (target_url, headers, body, active_config_name)
        """
        # Ensure we are working with the latest routing configuration
        self._ensure_routing_config_current()

        # Apply routing rules to the body
        modified_body, config_override = self._apply_model_routing(body)

        # Cache configs locally to reduce repeated I/O
        configs = self.config_manager.configs

        # Resolve which configuration should be used
        if config_override:
            active_config_name = config_override
        else:
            active_config_name = self._select_config_by_loadbalance(configs)

        config_data = configs.get(active_config_name)
        if not config_data and active_config_name:
            # Cache might be stale; fetch again
            configs = self.config_manager.configs
            config_data = configs.get(active_config_name)

        if not config_data:
            fallback_name = self.config_manager.active_config
            configs = self.config_manager.configs
            config_data = configs.get(fallback_name)
            active_config_name = fallback_name

        if not config_data:
            raise ValueError(f"Active configuration not found: {active_config_name}")
        
        # Construct the upstream URL
        base_url = config_data['base_url'].rstrip('/')
        normalized_path = path.lstrip('/')
        target_url = f"{base_url}/{normalized_path}" if normalized_path else base_url

        raw_query = request.url.query
        if raw_query:
            target_url = f"{target_url}?{raw_query}"

        # Filter headers, skipping those we will override
        excluded_headers = {'x-api-key', 'authorization', 'host', 'content-length'}
        incoming_auth = request.headers.get('authorization')
        incoming_api_key = request.headers.get('x-api-key')
        headers = {k: v for k, v in request.headers.items() if k.lower() not in excluded_headers}
        headers['host'] = urlsplit(target_url).netloc
        headers.setdefault('connection', 'keep-alive')
        if incoming_api_key:
            headers['x-api-key'] = incoming_api_key
        elif config_data.get('api_key'):
            headers['x-api-key'] = config_data['api_key']
        if incoming_auth:
            headers['authorization'] = incoming_auth
        elif config_data.get('auth_token'):
            headers['authorization'] = f'Bearer {config_data["auth_token"]}'

        return target_url, headers, modified_body, active_config_name

    @abstractmethod
    def test_endpoint(self, model: str, base_url: str, auth_token: str = None, api_key: str = None, extra_params: dict = None) -> dict:
        """Probe an upstream endpoint to confirm connectivity.

        Args:
            model: Name of the model used for probing
            base_url: Upstream API base URL
            auth_token: Optional bearer token
            api_key: Optional API key
            extra_params: Optional additional parameters

        Returns:
            dict: A dictionary describing the probe result
        """
        pass

    def apply_request_filter(self, data: bytes) -> bytes:
        """Apply the configured request filter if present."""
        if self.request_filter:
            # Use the cached filter implementation
            return self.request_filter.apply_filters(data)
        else:
            # Fallback to the original request_filter module
            return self.filter_request_data(data)

    def get_response_chunk_transformer(
        self,
        request: Request,
        path: str,
        target_headers: Dict[str, Any],
        target_body: bytes,
    ):
        """Return a transformer used to mutate streaming response chunks."""
        return None
    
    async def proxy(self, path: str, request: Request):
        """Handle a proxied request from the gateway."""
        start_time = time.time()
        request_id = str(uuid.uuid4())

        original_headers = {k: v for k, v in request.headers.items()}
        original_body = await request.body()

        active_config_name: Optional[str] = None
        target_headers: Optional[Dict[str, str]] = None
        filtered_body: Optional[bytes] = None
        target_url: Optional[str] = None

        try:
            target_url, target_headers, target_body, active_config_name = self.build_target_param(path, request, original_body)

            # Notify listeners that the request has started
            await self.realtime_hub.request_started(
                request_id=request_id,
                method=request.method,
                path=path,
                channel=active_config_name or "unknown",
                headers=target_headers,
                target_url=target_url
            )

        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

        # Apply request filters off the event loop
        filtered_body = await asyncio.to_thread(self.apply_request_filter, target_body)

        # Detect whether the request expects streaming
        headers_lower = {k.lower(): v for k, v in original_headers.items()}
        x_stainless_helper_method = headers_lower.get('x-stainless-helper-method', '').lower()
        content_type = headers_lower.get('content-type', '').lower()
        accept = headers_lower.get('accept', '').lower()
        is_stream = (
            'text/event-stream' in accept or
            'text/event-stream' in content_type or
            'stream' in content_type or
            'application/x-ndjson' in content_type or
            "stream" in x_stainless_helper_method
        )

        try:
            request_out = self.client.build_request(
                method=request.method,
                url=target_url,
                headers=target_headers,
                content=filtered_body if filtered_body else None,
            )
            response = await self.client.send(request_out, stream=is_stream)

            status_code = response.status_code
            chunk_transformer = self.get_response_chunk_transformer(
                request=request,
                path=path,
                target_headers=target_headers,
                target_body=target_body,
            )
            lb_result_recorded = False

            if not (200 <= status_code < 300):
                await asyncio.to_thread(self._record_lb_result, active_config_name, status_code)
                lb_result_recorded = True

            # Build response headers and annotate removed ones for logging
            excluded_response_headers = {}
            response_headers = {}  # Actual response headers
            response_headers_for_log = {}  # Headers recorded in logs

            for k, v in response.headers.items():
                k_lower = k.lower()
                if k_lower in excluded_response_headers:
                    # Log the header as removed
                    response_headers_for_log[f"{k}[removed]"] = v
                else:
                    # Include header in both logs and response
                    response_headers[k] = v
                    response_headers_for_log[k] = v

            collected = bytearray()
            total_response_bytes = 0
            response_truncated = False
            first_chunk = True

            async def iterator():
                nonlocal response_truncated, total_response_bytes, first_chunk, lb_result_recorded
                try:
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue

                        if chunk_transformer:
                            chunk = chunk_transformer.process(chunk)
                            if not chunk:
                                continue

                        current_duration = int((time.time() - start_time) * 1000)

                        # Mark the request as streaming on the first chunk
                        if first_chunk:
                            await self.realtime_hub.request_streaming(request_id, current_duration)
                            first_chunk = False

                        # Attempt to decode the chunk for UI updates
                        try:
                            chunk_text = chunk.decode('utf-8', errors='ignore')
                            if chunk_text.strip():  # Only send meaningful chunks
                                await self.realtime_hub.response_chunk(
                                    request_id, chunk_text, current_duration
                                )
                        except Exception:
                            pass  # Ignore decode failures

                        total_response_bytes += len(chunk)
                        if len(collected) < self.max_logged_response_bytes:
                            remaining = self.max_logged_response_bytes - len(collected)
                            collected.extend(chunk[:remaining])
                            if len(chunk) > remaining:
                                response_truncated = True
                        else:
                            response_truncated = True
                        yield chunk

                    if chunk_transformer:
                        trailing_chunk = chunk_transformer.flush()
                        if trailing_chunk:
                            current_duration = int((time.time() - start_time) * 1000)
                            if first_chunk:
                                await self.realtime_hub.request_streaming(request_id, current_duration)
                                first_chunk = False
                            try:
                                trailing_text = trailing_chunk.decode('utf-8', errors='ignore')
                                if trailing_text.strip():
                                    await self.realtime_hub.response_chunk(
                                        request_id, trailing_text, current_duration
                                    )
                            except Exception:
                                pass
                            total_response_bytes += len(trailing_chunk)
                            if len(collected) < self.max_logged_response_bytes:
                                remaining = self.max_logged_response_bytes - len(collected)
                                collected.extend(trailing_chunk[:remaining])
                                if len(trailing_chunk) > remaining:
                                    response_truncated = True
                            else:
                                response_truncated = True
                            yield trailing_chunk
                finally:
                    final_duration = int((time.time() - start_time) * 1000)

                    # Signal that the request finished
                    await self.realtime_hub.request_completed(
                        request_id=request_id,
                        status_code=status_code,
                        duration_ms=final_duration,
                        success=200 <= status_code < 400
                    )

                    await response.aclose()

                    # Log the request details
                    response_content = bytes(collected) if collected else None
                    await self.log_request(
                        method=request.method,
                        path=path,
                        status_code=status_code,
                        duration_ms=final_duration,
                        target_headers=target_headers,
                        filtered_body=filtered_body,
                        original_headers=original_headers,
                        original_body=original_body,
                        response_content=response_content,
                        channel=active_config_name,
                        response_truncated=response_truncated,
                        total_response_bytes=total_response_bytes,
                        target_url=target_url,
                        response_headers=response_headers_for_log,
                    )

                    if not lb_result_recorded:
                        await asyncio.to_thread(self._record_lb_result, active_config_name, status_code)
                        lb_result_recorded = True

            return StreamingResponse(
                iterator(),
                status_code=status_code,
                headers=response_headers
            )
        except httpx.RequestError as exc:
            duration_ms = int((time.time() - start_time) * 1000)

            if isinstance(exc, httpx.ConnectTimeout):
                error_msg = "Connection timed out"
            elif isinstance(exc, httpx.ReadTimeout):
                error_msg = "Read timed out"
            elif isinstance(exc, httpx.ConnectError):
                error_msg = "Connection error"
            elif isinstance(exc, httpx.HTTPStatusError):
                error_msg = "Upstream returned an error status"
            else:
                error_msg = "Request failed"

            response_data = {"error": error_msg, "detail": str(exc)}
            status_code = 500

            # Notify listeners about the failure
            await self.realtime_hub.request_completed(
                request_id=request_id,
                status_code=status_code,
                duration_ms=duration_ms,
                success=False
            )

            await self.log_request(
                method=request.method,
                path=path,
                status_code=status_code,
                duration_ms=duration_ms,
                target_headers=target_headers,
                filtered_body=filtered_body,
                original_headers=original_headers,
                original_body=original_body,
                channel=active_config_name,
                target_url=target_url
            )

            await asyncio.to_thread(self._record_lb_result, active_config_name, status_code)

            return JSONResponse(response_data, status_code=status_code)

    def run_app(self):
        """Run the proxy synchronously (used by legacy entry points)."""
        import os
        # Switch to the project root
        project_root = Path(__file__).parent.parent.parent
        
        # Copy environment variables for the daemonised process
        env = os.environ.copy()
        
        try:
            with open(self.log_file, 'a') as log_file:
                uvicorn_cmd = [
                    sys.executable, '-m', 'uvicorn',
                    f'src.{self.service_name}.proxy:app',
                    '--host', '0.0.0.0',
                    '--port', str(self.port),
                    '--http', 'h11',
                    '--timeout-keep-alive', '60',
                    '--limit-concurrency', '500',
                ]
                subprocess.run(
                    uvicorn_cmd,
                    cwd=str(project_root),
                    env=env,
                    stdout=log_file,
                    stderr=log_file,
                    stdin=subprocess.DEVNULL
                )
                print(f"Started {self.service_name} proxy on port {self.port}")
        except Exception as e:
            print(f"Failed to start {self.service_name} proxy: {e}")


class BaseServiceController(ABC):
    """Base controller helper used by CLI commands."""
    
    def __init__(self, service_name: str, port: int, config_manager, proxy_module_path: str):
        """
        Initialise the service controller.

        Args:
            service_name: Service identifier
            port: Service port
            config_manager: Configuration manager instance
            proxy_module_path: Python module path to the proxy app (e.g. 'src.claude.proxy')
        """
        self.service_name = service_name
        self.port = port
        self.config_manager = config_manager
        self.proxy_module_path = proxy_module_path
        
        # Prepare runtime paths
        self.config_dir = Path.home() / '.clp/run'
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.pid_file = self.config_dir / f'{service_name}_proxy.pid'
        self.log_file = self.config_dir / f'{service_name}_proxy.log'
    
    def get_pid(self) -> Optional[int]:
        """Return the PID of the managed service if available."""
        if self.pid_file.exists():
            try:
                return int(self.pid_file.read_text().strip())
            except:
                return None
        return None
    
    def is_running(self) -> bool:
        """Check whether the managed service is running."""
        import psutil
        pid = self.get_pid()
        if pid:
            try:
                process = psutil.Process(pid)
                return process.is_running()
            except psutil.NoSuchProcess:
                return False
        return False
    
    def start(self) -> bool:
        """Start the managed service."""
        if self.is_running():
            print(f"{self.service_name} service is already running")
            return False
        
        config_file_path = None
        ensure_file_fn = getattr(self.config_manager, 'ensure_config_file', None)
        if callable(ensure_file_fn):
            config_file_path = ensure_file_fn()
        elif hasattr(self.config_manager, 'config_file'):
            config_file_path = getattr(self.config_manager, 'config_file')

        # Validate configuration availability
        configs = self.config_manager.configs
        if not configs:
            if config_file_path:
                print(f"Warning: {self.service_name} configuration is empty. Starting in placeholder mode. Edit {config_file_path} and restart.")
            else:
                print(f"Warning: No {self.service_name} configuration file detected. Starting in placeholder mode.")
        
        import os
        project_root = Path(__file__).parent.parent.parent
        env = os.environ.copy()
        
        uvicorn_cmd = [
            sys.executable, '-m', 'uvicorn',
            f'{self.proxy_module_path}:app',
            '--host', '0.0.0.0',
            '--port', str(self.port),
            '--http', 'h11',
            '--timeout-keep-alive', '60',
            '--limit-concurrency', '500',
        ]
        with open(self.log_file, 'a') as log_handle:
            # Launch in a detached process group so console signals do not terminate it
            process = create_detached_process(
                uvicorn_cmd,
                log_handle,
                cwd=str(project_root),
                env=env,
            )

        # Persist the PID
        self.pid_file.write_text(str(process.pid))

        # Allow the process time to boot
        time.sleep(1)

        if self.is_running():
            print(f"{self.service_name} service started (port: {self.port})")
            return True
        else:
            print(f"Failed to start {self.service_name} service")
            return False
    
    def stop(self) -> bool:
        """Stop the managed service."""
        import psutil
        
        if not self.is_running():
            print(f"{self.service_name} service is not running")
            return False
        
        pid = self.get_pid()
        if pid:
            try:
                process = psutil.Process(pid)
                process.terminate()
                process.wait(timeout=5)
            except psutil.TimeoutExpired:
                process.kill()
            except psutil.NoSuchProcess:
                pass
            
            # Remove the PID file
            if self.pid_file.exists():
                self.pid_file.unlink()
            
            print(f"{self.service_name} service stopped")
            return True
        
        return False
    
    def restart(self) -> bool:
        """Restart the managed service."""
        self.stop()
        time.sleep(1)
        return self.start()
    
    def status(self):
        """Print the service status to stdout."""
        if self.is_running():
            pid = self.get_pid()
            active_config = self.config_manager.active_config
            print(f"{self.service_name} service: running (PID: {pid}, config: {active_config})")
        else:
            print(f"{self.service_name} service: stopped")
