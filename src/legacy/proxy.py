#!/usr/bin/env python3
"""Legacy proxy service built on the shared base proxy infrastructure."""
import base64
import json
import logging
import mimetypes
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def _read_image_as_data_url(file_path: str) -> Optional[str]:
    if not file_path:
        return None

    try:
        with open(file_path, 'rb') as fh:
            binary = fh.read()
    except OSError:
        return None

    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = 'application/octet-stream'

    try:
        encoded = base64.b64encode(binary).decode('ascii')
    except Exception:
        return None

    return f"data:{mime_type};base64,{encoded}"


def _inject_image_tool_results(message_list: Any) -> Any:
    if not isinstance(message_list, list):
        return message_list

    try:
        messages_clone = json.loads(json.dumps(message_list))
    except Exception:
        messages_clone = message_list

    tool_call_map: Dict[str, Dict[str, Any]] = {}
    for message in messages_clone:
        if not isinstance(message, dict):
            continue
        if message.get('role') != 'assistant':
            continue
        tool_calls = message.get('tool_calls')
        if not isinstance(tool_calls, list):
            continue
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            call_id = call.get('id')
            if not call_id:
                continue
            function_block = call.get('function')
            if not isinstance(function_block, dict):
                continue
            name = function_block.get('name') or ''
            arguments = function_block.get('arguments')
            parsed_args: Dict[str, Any] = {}
            if isinstance(arguments, str):
                try:
                    parsed_args = json.loads(arguments)
                except Exception:
                    parsed_args = {}
            elif isinstance(arguments, dict):
                parsed_args = arguments
            tool_call_map[call_id] = {
                'name': name,
                'arguments': parsed_args
            }


    for message in messages_clone:
        if not isinstance(message, dict):
            continue
        if message.get('role') != 'tool':
            continue
        tool_call_id = message.get('tool_call_id')
        if not tool_call_id:
            continue
        tool_call = tool_call_map.get(tool_call_id)
        if not tool_call:
            continue
        if tool_call.get('name') != 'Read':
            continue


        existing_content = message.get('content')
        if isinstance(existing_content, str) and 'data:image' in existing_content:
            continue

        file_path = ''
        arguments = tool_call.get('arguments') or {}
        if isinstance(arguments, dict):
            file_path = arguments.get('file_path') or ''

        data_url = _read_image_as_data_url(file_path)
        if not data_url:
            continue

        human_summary = ''
        if isinstance(existing_content, str) and existing_content.strip():
            human_summary = existing_content.strip()
        elif isinstance(existing_content, list) and existing_content:
            parts = []
            for part in existing_content:
                if isinstance(part, dict) and 'text' in part and isinstance(part['text'], str):
                    parts.append(part['text'])
                elif isinstance(part, str):
                    parts.append(part)
            human_summary = '\n'.join(parts).strip()

        file_desc = Path(file_path).name if file_path else 'Image'
        summary_prefix = human_summary or f'Image file: {file_desc}'
        message['content'] = f"{summary_prefix}\n\n(IMAGE:DATA) {data_url}"

    return messages_clone


def _convert_image_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    image_obj = item.get('image') if isinstance(item.get('image'), dict) else {}
    base64_data = (
        image_obj.get('image_base64')
        or item.get('image_base64')
        or image_obj.get('b64_json')
        or item.get('b64_json')
    )
    image_url = image_obj.get('image_url') or item.get('image_url')
    mime_type = image_obj.get('mime_type') or item.get('mime_type') or 'image/png'
    detail = image_obj.get('detail') or item.get('detail')

    if base64_data:
        url_value = f"data:{mime_type};base64,{base64_data}"
    elif image_url:
        url_value = image_url
    else:
        return None

    payload: Dict[str, Any] = {'type': 'image_url', 'image_url': {'url': url_value}}
    if detail:
        payload['image_url']['detail'] = detail
    return payload


def _convert_input_blocks_to_messages(input_blocks: Any) -> list[Dict[str, Any]]:
    messages: list[Dict[str, Any]] = []

    if not isinstance(input_blocks, list):
        return messages

    for block in input_blocks:
        if not isinstance(block, dict):
            continue

        role = block.get('role', 'user')
        content_items = block.get('content')
        text_parts: list[str] = []
        content_entries: list[Dict[str, Any]] = []

        if isinstance(content_items, list):
            for item in content_items:
                if isinstance(item, dict):
                    item_type = item.get('type')
                    if item_type == 'input_text':
                        text = item.get('text')
                        if text is not None:
                            text_parts.append(str(text))
                    elif item_type == 'input_image':
                        image_entry = _convert_image_item(item)
                        if image_entry:
                            if text_parts:
                                combined = '\n'.join(text_parts).strip()
                                if combined:
                                    content_entries.append({'type': 'text', 'text': combined})
                                text_parts = []
                            content_entries.append(image_entry)
                elif isinstance(item, str):
                    text_parts.append(item)
        elif isinstance(content_items, str):
            text_parts.append(content_items)

        residual_text = '\n'.join(text_parts).strip()
        if residual_text:
            content_entries.append({'type': 'text', 'text': residual_text})

        if not content_entries:
            continue

        if len(content_entries) == 1 and content_entries[0].get('type') == 'text':
            message_content: Any = content_entries[0]['text']
        else:
            message_content = content_entries

        messages.append({'role': role, 'content': message_content})

    return messages


def _flatten_tool_messages(message_list: Any) -> list[Dict[str, Any]]:
    if not isinstance(message_list, list):
        return message_list

    flattened: list[Dict[str, Any]] = []

    for message in message_list:
        if not isinstance(message, dict):
            continue

        role = message.get('role')
        if role == 'assistant' and message.get('tool_calls'):
            # Preserve any textual content from the assistant message, but strip tool metadata
            content = message.get('content')
            if isinstance(content, str) and content.strip():
                flattened.append({'role': 'assistant', 'content': content})
            continue

        if role == 'tool':
            content = message.get('content')
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and isinstance(item.get('text'), str):
                        parts.append(item['text'])
                    elif isinstance(item, str):
                        parts.append(item)
                content = '\n'.join(parts)
            if not isinstance(content, str):
                content = ''

            flattened.append({'role': 'user', 'content': content})
            continue

        # For other roles ensure tool metadata is removed
        if 'tool_calls' in message:
            message = dict(message)
            message.pop('tool_calls', None)

        flattened.append(message)

    if not flattened:
        flattened.append({'role': 'user', 'content': ''})

    return flattened


def _convert_messages_to_legacy_function_format(message_list: Any) -> Any:
    if not isinstance(message_list, list):
        return message_list

    tool_call_meta: Dict[str, Dict[str, Any]] = {}

    for message in message_list:
        if not isinstance(message, dict):
            continue
        if message.get('role') != 'assistant':
            continue
        tool_calls = message.get('tool_calls')
        if not isinstance(tool_calls, list) or not tool_calls:
            continue

        first_call = tool_calls[0]
        if not isinstance(first_call, dict):
            continue

        function_block = first_call.get('function') if isinstance(first_call.get('function'), dict) else {}
        name = function_block.get('name')
        arguments = function_block.get('arguments')
        if not name:
            continue

        # Legacy format expects arguments as a JSON string
        if isinstance(arguments, dict):
            try:
                arguments = json.dumps(arguments)
            except Exception:
                arguments = json.dumps({})
        elif not isinstance(arguments, str):
            arguments = json.dumps({})

        message['function_call'] = {
            'name': name,
            'arguments': arguments
        }
        message['tool_calls'] = None
        message['content'] = message.get('content') or None

        call_id = first_call.get('id')
        if call_id:
            tool_call_meta[call_id] = {
                'name': name
            }

    for message in message_list:
        if not isinstance(message, dict):
            continue
        if message.get('role') != 'tool':
            continue

        call_id = message.get('tool_call_id')
        call_info = tool_call_meta.get(call_id, {})
        function_name = call_info.get('name') or ''

        # Legacy format expects the role to be 'function' with a name attribute
        message['role'] = 'function'
        if function_name:
            message['name'] = function_name

        content = message.get('content')
        if isinstance(content, list):
            # Flatten text blocks into a single string for the legacy API
            parts = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get('text'), str):
                    parts.append(item['text'])
                elif isinstance(item, str):
                    parts.append(item)
            message['content'] = '\n'.join(parts)
        elif content is None:
            message['content'] = ''

        message.pop('tool_call_id', None)

    # Clean up any None placeholders introduced above
    for message in message_list:
        if isinstance(message, dict) and message.get('tool_calls') is None:
            message.pop('tool_calls', None)

    return message_list

from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request

from ..core.base_proxy import BaseProxyService
from ..config.cached_config_manager import legacy_config_manager


class LegacyProxy(BaseProxyService):
    """Legacy proxy implementation with conservative defaults for the upstream RPM limit."""

    def __init__(self):
        super().__init__(
            service_name='legacy',
            port=3212,
            config_manager=legacy_config_manager
        )

        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:3300", "http://127.0.0.1:3300"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        self.logger = logging.getLogger('legacy_proxy')
        self.logger.setLevel(logging.INFO)

        if not self.logger.handlers:
            log_file = Path.home() / '.clp/run/legacy_proxy.log'
            log_file.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(log_file, encoding='utf-8')
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.propagate = False

    @staticmethod
    def _wrap_inject_image_tool_results(messages: Any) -> Any:
        return _inject_image_tool_results(messages)

    @staticmethod
    def _wrap_convert_input_blocks(input_blocks: Any) -> list[Dict[str, Any]]:
        return _convert_input_blocks_to_messages(input_blocks)

    def default_rpm_limit(self) -> Optional[float]:
        """No built-in throttling; per-site limits control the pacing."""
        return None

    def build_target_param(
        self,
        path: str,
        request: Request,
        body: bytes
    ) -> Tuple[str, Dict, bytes, Optional[str]]:
        """Ensure baseline headers are present for the OpenAI-compatible Legacy API."""
        target_url, headers, modified_body, active_config_name = super().build_target_param(path, request, body)

        # Normalise headers expected by the Legacy upstream
        headers.setdefault('content-type', 'application/json')
        headers.setdefault('accept', 'application/json')
        headers.setdefault('user-agent', 'cli-proxy-legacy/1.0')

        # Get site streaming configuration
        configs = self.config_manager.configs
        config_data = configs.get(active_config_name, {})
        site_streaming = config_data.get('streaming')  # None (auto), True (force on), or False (force off)
        tool_calls_streaming = config_data.get('tool_calls_streaming')  # None (auto), True (allow), or False (disable)

        normalized_path = path.lstrip('/').lower()

        # Map /responses payloads into chat completions for better compatibility
        if normalized_path == 'responses':
            if target_url.endswith('/responses'):
                target_url = target_url[:-len('/responses')] + '/v1/chat/completions'
            request.state.legacy_responses = True

            try:
                payload = json.loads(modified_body.decode('utf-8')) if modified_body else {}
            except Exception:
                payload = {}

            messages = []

            input_blocks = payload.get('input')
            messages.extend(self._wrap_convert_input_blocks(input_blocks))

            if not messages and isinstance(payload.get('messages'), list):
                messages = payload['messages']

            if not messages and isinstance(payload.get('prompt'), str):
                messages = [{'role': 'user', 'content': payload['prompt']}]

            messages = self._wrap_inject_image_tool_results(messages)
            messages = _flatten_tool_messages(messages)

            request_body = {
                'model': payload.get('model'),
                'messages': messages or [{'role': 'user', 'content': ''}],
                'stream': False,
                'temperature': payload.get('temperature'),
                'top_p': payload.get('top_p'),
            }

            request_body = {k: v for k, v in request_body.items() if v is not None}
            modified_body = json.dumps(request_body, ensure_ascii=False).encode('utf-8')
            headers['accept'] = 'application/json'
        elif normalized_path.endswith('chat/completions'):
            try:
                payload = json.loads(modified_body.decode('utf-8')) if modified_body else {}
            except Exception:
                payload = {}

            if isinstance(payload, dict):
                stream_value = payload.get('stream')
                has_tools = bool(payload.get('tools'))

                # Determine what the client requested
                client_wants_streaming = False
                if isinstance(stream_value, bool):
                    client_wants_streaming = stream_value
                elif isinstance(stream_value, str):
                    client_wants_streaming = stream_value.strip().lower() not in {'', '0', 'false', 'no'}

                # Apply site streaming configuration
                # site_streaming: None (auto/default), True (force on), False (force off)
                # tool_calls_streaming: None (auto/default), True (allow streaming), False (disable streaming)

                # Determine if we should allow streaming when tools are present
                allow_tools_streaming = True
                if tool_calls_streaming is False:
                    # Explicitly disabled: never stream when tools present
                    allow_tools_streaming = False
                elif tool_calls_streaming is True:
                    # Explicitly enabled: stream when tools present (if A4F supports it in future)
                    allow_tools_streaming = True
                # else: auto (tool_calls_streaming is None) - default is to allow

                if has_tools:
                    # A4F API does NOT support streaming with tool calling
                    # Send stream=False to get tool_calls, ALWAYS transform to SSE for client
                    use_upstream_streaming = False
                    transform_to_sse = True
                elif site_streaming is True:
                    # Site forces streaming ON, no tools present - can stream
                    use_upstream_streaming = True
                    transform_to_sse = True
                elif site_streaming is False:
                    # Site forces streaming OFF - never stream
                    use_upstream_streaming = False
                    transform_to_sse = False
                else:
                    # Auto mode (site_streaming is None) - respect client preference
                    use_upstream_streaming = client_wants_streaming
                    transform_to_sse = client_wants_streaming

                # Update the payload with the final streaming decision
                payload['stream'] = use_upstream_streaming
                request.state.legacy_chatcompletions_stream = transform_to_sse

                # Set appropriate accept header
                # Always request JSON from upstream - if we need SSE, we'll transform it
                headers['accept'] = 'application/json'

                converted_messages = self._wrap_convert_input_blocks(payload.get('input'))
                if converted_messages:
                    payload['messages'] = converted_messages
                elif not isinstance(payload.get('messages'), list):
                    prompt_text = payload.get('prompt')
                    if isinstance(prompt_text, str):
                        payload['messages'] = [{'role': 'user', 'content': prompt_text}]
                    else:
                        payload['messages'] = [{'role': 'user', 'content': ''}]

                if 'input' in payload:
                    payload.pop('input', None)

                if isinstance(payload.get('messages'), list):
                    payload['messages'] = self._wrap_inject_image_tool_results(payload['messages'])
                    payload['messages'] = _flatten_tool_messages(payload['messages'])
                else:
                    payload['messages'] = self._wrap_inject_image_tool_results([{'role': 'user', 'content': ''}])
                    payload['messages'] = _flatten_tool_messages(payload['messages'])

                modified_body = json.dumps(payload, ensure_ascii=False).encode('utf-8')

                # Log streaming configuration details
                self.logger.info(
                    f"Sending to upstream - model: {payload.get('model')}, "
                    f"stream: {payload.get('stream')}, "
                    f"site_streaming: {site_streaming}, "
                    f"client_requested: {client_wants_streaming}, "
                    f"has_tools: {has_tools}, "
                    f"will_transform_sse: {transform_to_sse}, "
                    f"messages: {len(payload.get('messages', []))}"
                )
            else:
                request.state.legacy_chatcompletions_stream = False
        
        # Ensure bare /chat/completions adds /v1 prefix
        if normalized_path == 'chat/completions' and '/v1/chat/completions' not in target_url:
            if target_url.endswith('/chat/completions') and '/v1/' not in target_url:
                target_url = target_url.replace('/chat/completions', '/v1/chat/completions')

        return target_url, headers, modified_body, active_config_name

    def get_response_chunk_transformer(self, request, path, target_headers, target_body):
        normalized_path = path.lstrip('/').lower()
        if normalized_path == 'responses' and getattr(request.state, 'legacy_responses', False):
            return _ResponsesJsonTransformer()
        if normalized_path.endswith('chat/completions') and getattr(request.state, 'legacy_chatcompletions_stream', False):
            return _ChatCompletionsSseTransformer()
        return None

    def test_endpoint(
        self,
        model: str,
        base_url: str,
        auth_token: str = None,
        api_key: str = None,
        extra_params: dict = None
    ) -> dict:
        """Endpoint probing is not yet implemented for the Legacy proxy."""
        return {
            "status": "unsupported",
            "message": "Endpoint probing is not supported by the Legacy proxy."
        }


class _ResponsesJsonTransformer:
    """Convert chat completion JSON into a Responses-style JSON payload."""

    def __init__(self):
        self._buffer = bytearray()
        self.strip_content_length = True
        self.override_response_headers = {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive'
        }
        self.override_status_code = 200

    def process(self, chunk: bytes) -> bytes:
        if chunk:
            self._buffer.extend(chunk)
        return b''

    def flush(self) -> bytes:
        if not self._buffer:
            return b''

        try:
            upstream = json.loads(self._buffer.decode('utf-8'))
        except Exception:
            return bytes(self._buffer)

        choices = upstream.get('choices') or []
        error_info = upstream.get('error') if isinstance(upstream.get('error'), dict) else None

        message = ''
        if choices:
            message = choices[0].get('message', {}).get('content', '') or ''
        elif error_info:
            message = str(error_info.get('message') or '').strip()

        response_id = upstream.get('id') or f"resp-{uuid.uuid4().hex}"
        created_at = upstream.get('created')
        if created_at is None:
            created_at = int(time.time())

        usage = upstream.get('usage') or {}

        status_final = 'completed'
        if error_info:
            status_final = 'failed'

        item_id = f"item-{uuid.uuid4().hex}"
        output_item = {
            'id': item_id,
            'type': 'message',
            'status': 'completed',
            'role': 'assistant',
            'content': [
                {
                    'type': 'output_text',
                    'text': message,
                    'annotations': []
                }
            ]
        }

        def _sse(event: str, payload: dict) -> str:
            return f"event: {event}\n" + f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        base_response = {
            'id': response_id,
            'object': 'response',
            'created': created_at,
            'created_at': created_at,
            'model': upstream.get('model'),
            'status': 'in_progress',
            'output': [],
            'output_text': '',
            'usage': usage
        }

        events = []
        events.append(_sse('response.created', {
            'type': 'response.created',
            'response': base_response
        }))

        events.append(_sse('response.output_item.done', {
            'type': 'response.output_item.done',
            'output_index': 0,
            'item': output_item
        }))

        completed_response = dict(base_response)
        completed_response['status'] = status_final
        completed_response['output'] = [
            {
                'id': item_id,
                'type': 'message',
                'status': 'completed',
                'role': 'assistant',
                'content': output_item['content']
            }
        ]
        completed_response['usage'] = usage
        completed_response['output_text'] = message
        if error_info:
            completed_response['error'] = error_info

        events.append(_sse('response.completed', {
            'type': 'response.completed',
            'response': completed_response
        }))

        events.append("event: done\ndata: [DONE]\n\n")

        return ''.join(events).encode('utf-8')


class _ChatCompletionsSseTransformer:
    """Convert a non-streaming chat completion into SSE chunks for streaming clients.

    Handles both normal text responses and tool/function call responses, converting
    them into OpenAI-compatible SSE format for clients expecting streaming responses.
    Safely handles edge cases like missing messages or empty content.

    OPTIMIZATION: Process response immediately upon receiving complete JSON to avoid
    buffering delays that cause slow models (Opus) to timeout before first byte is sent.
    """

    def __init__(self):
        self._buffer = bytearray()
        self._processed = False  # Track if we've already converted and sent response
        self._is_upstream_sse = False  # Track if upstream is sending SSE (not JSON)
        self.strip_content_length = True
        self.override_response_headers = {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive'
        }
        self.override_status_code = 200
        self.logger = logging.getLogger('legacy_proxy')

    def _try_parse_json(self) -> Optional[Dict[str, Any]]:
        """Try to parse buffer as JSON, return parsed object or None if incomplete."""
        if not self._buffer:
            return None
        try:
            return json.loads(self._buffer.decode('utf-8'))
        except json.JSONDecodeError:
            # JSON not yet complete
            return None

    def process(self, chunk: bytes) -> bytes:
        """Process chunk and return SSE data immediately when response is complete."""
        if not chunk:
            return b''

        # If upstream is already sending SSE, pass chunks through directly
        if self._is_upstream_sse:
            return chunk

        self._buffer.extend(chunk)
        buffer_text = self._buffer.decode('utf-8', errors='ignore')

        # Detect if upstream is sending SSE format (not JSON)
        if buffer_text.lstrip().startswith('data: '):
            # Upstream is streaming SSE, not JSON - pass through directly
            self._is_upstream_sse = True
            self._processed = True
            self.logger.info(f"SSE: Detected upstream is already SSE streaming, passing through")
            return bytes(self._buffer)

        # Try to parse as JSON - if successful, we have a complete response
        upstream = self._try_parse_json()
        if upstream:
            # Response is complete JSON, convert to SSE immediately
            self._processed = True
            self.logger.info(f"SSE: Complete JSON received ({len(self._buffer)} bytes), converting to SSE")
            return self._convert_to_sse(upstream)

        # Not yet complete, wait for more chunks
        return b''

    def _convert_to_sse(self, upstream: Dict[str, Any]) -> bytes:
        """Convert parsed upstream response to SSE format."""
        # upstream is already parsed JSON, extract response data
        if not upstream:
            return bytes(self._buffer)

        error_info = upstream.get('error') if isinstance(upstream.get('error'), dict) else None

        created_at = upstream.get('created')
        if created_at is None:
            created_at = int(time.time())

        model_name = upstream.get('model')
        usage = upstream.get('usage') or {}

        def _chunk(delta: Dict[str, Any], finish_reason: Optional[str], include_usage: bool = False, extra: Optional[Dict[str, Any]] = None) -> str:
            payload = {
                'id': upstream.get('id') or f"chatcmpl-{uuid.uuid4().hex}",
                'object': 'chat.completion.chunk',
                'created': created_at,
                'model': model_name,
                'choices': [
                    {
                        'index': 0,
                        'delta': delta or {},
                        'finish_reason': finish_reason
                    }
                ]
            }
            if include_usage and usage:
                payload['usage'] = usage
            if extra:
                payload.update(extra)
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        events = []

        if error_info:
            error_message = str(error_info.get('message') or '').strip() or 'Upstream error'
            events.append(_chunk({'content': error_message}, 'error', include_usage=False, extra={'error': error_info}))
        else:
            choices = upstream.get('choices')
            first_choice = choices[0] if isinstance(choices, list) and choices else {}
            if not isinstance(first_choice, dict):
                first_choice = {}

            # Safely extract message block, defaulting to empty dict if None or invalid
            message_block = first_choice.get('message')
            if not isinstance(message_block, dict):
                message_block = {}

            role = message_block.get('role') or 'assistant'
            content = message_block.get('content')
            finish_reason = first_choice.get('finish_reason') or 'stop'

            delta: Dict[str, Any] = {'role': role}

            # Extract text content safely, handling various formats
            text_value = ''
            if isinstance(content, str):
                text_value = content
            elif isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and isinstance(item.get('text'), str):
                        parts.append(item['text'])
                    elif isinstance(item, str):
                        parts.append(item)
                text_value = ''.join(parts)
            # If content is None or any other type, text_value stays empty string

            # Always include content field for consistency, even if empty
            # Droid CLI expects this field in all chunks
            delta['content'] = text_value

            # Extract tool_calls if present and include in delta
            tool_calls = message_block.get('tool_calls')
            if isinstance(tool_calls, list) and tool_calls:
                delta['tool_calls'] = tool_calls
                self.logger.info(f"Response: tool_calls={len(tool_calls)} tools, content_len={len(text_value)}, delta_keys={list(delta.keys())}")

            events.append(_chunk(delta, None))
            events.append(_chunk({}, finish_reason, include_usage=True))

        events.append('data: [DONE]\n\n')
        return ''.join(events).encode('utf-8')

    def flush(self) -> bytes:
        """Called after stream ends. If not yet processed, process now."""
        if self._processed:
            # Already processed in process(), nothing more to do
            return b''

        if self._is_upstream_sse:
            # Already sent SSE passthrough, nothing more to do
            return b''

        # Fallback for edge cases where JSON wasn't complete
        # (shouldn't happen with normal upstream, but handle gracefully)
        if not self._buffer:
            return b''

        try:
            upstream = json.loads(self._buffer.decode('utf-8'))
            if upstream:
                self._processed = True
                self.logger.info(f"SSE: Flushing buffered response ({len(self._buffer)} bytes)")
                return self._convert_to_sse(upstream)
        except json.JSONDecodeError:
            pass

        # If all else fails, return buffer as-is
        return bytes(self._buffer)


proxy_service = LegacyProxy()
app = proxy_service.app


def run_app(port: int = 3212):
    """Launch the Legacy proxy service."""
    proxy_service.run_app()


if __name__ == '__main__':
    import uvicorn

    uvicorn.run(
        app,
        host='0.0.0.0',
        port=3212,
        log_level='info',
        timeout_keep_alive=60,
        http='h11'
    )
