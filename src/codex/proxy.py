#!/usr/bin/env python3
"""Codex proxy service built on the shared base proxy infrastructure."""
import aiohttp
import logging
import datetime
import time
import json
from pathlib import Path
from typing import Optional
from urllib import request as urllib_request, error as urllib_error
from urllib.parse import urlsplit, urlunsplit

from fastapi.middleware.cors import CORSMiddleware
from ..core.base_proxy import BaseProxyService
from ..config.cached_config_manager import codex_config_manager

_PROMPT_SOURCE_URL = "https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/gpt_5_codex_prompt.md"
_PROMPT_CACHE_FILE = Path.home() / ".clp" / "data" / "codex_prompt.md"
_PROMPT_BUNDLED_FILE = Path(__file__).parent / "codex_prompt.md"
_PROMPT_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours



def _load_codex_prompt() -> str:
    """Load the latest Codex prompt, with caching and a safe fallback."""
    logger = logging.getLogger("codex_proxy.prompt_loader")
    cache_path = _PROMPT_CACHE_FILE
    now = time.time()

    try:
        if cache_path.exists():
            if now - cache_path.stat().st_mtime <= _PROMPT_CACHE_TTL_SECONDS:
                return cache_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug("Failed to read Codex prompt cache metadata: %s", exc)

    try:
        with urllib_request.urlopen(_PROMPT_SOURCE_URL, timeout=10) as response:
            prompt_text = response.read().decode("utf-8")
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(prompt_text, encoding="utf-8")
            logger.debug("Fetched latest Codex prompt from %s", _PROMPT_SOURCE_URL)
            return prompt_text
    except (urllib_error.URLError, OSError, ValueError) as exc:
        logger.debug("Unable to fetch Codex prompt from upstream: %s", exc)

    try:
        if cache_path.exists():
            return cache_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug("Codex prompt cache unavailable, using bundled fallback: %s", exc)

    try:
        if _PROMPT_BUNDLED_FILE.exists():
            return _PROMPT_BUNDLED_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("Unable to read bundled Codex prompt: %s", exc)

    return ""


# Minimal CLI-style instructions used for helper builders and parity
INSTRUCTIONS_CLI = _load_codex_prompt()
PRIMARY_INSTRUCTION = (
    INSTRUCTIONS_CLI.splitlines()[0].strip()
    if INSTRUCTIONS_CLI.splitlines()
    else "You are Codex, based on GPT-5. You are running as a coding agent in the Codex CLI on a user's computer."
)
FULL_INSTRUCTIONS = INSTRUCTIONS_CLI.strip() or PRIMARY_INSTRUCTION

def _ensure_primary_instruction(text: Optional[str]) -> str:
    """
    Guarantee the CLI-style preamble is present and on the first line.
    Upstream still receives the full prompt, but client-facing instructions
    must always include the one-line identity header.
    """
    if not text:
        return PRIMARY_INSTRUCTION

    stripped = text.strip()
    if not stripped:
        return PRIMARY_INSTRUCTION
    if stripped.startswith(PRIMARY_INSTRUCTION):
        return stripped
    return f"{PRIMARY_INSTRUCTION}\n\n{stripped}"

class CodexProxy(BaseProxyService):
    """Codex proxy service implementation."""

    def __init__(self):
        super().__init__(
            service_name='codex',
            port=3211,
            config_manager=codex_config_manager,
            public_path_prefixes=['v1'],
            require_public_prefix=True
        )

        # Allow the UI to connect via CORS
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:3300", "http://127.0.0.1:3300"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Configure a dedicated logger
        self.logger = logging.getLogger('codex_proxy')
        self.logger.setLevel(logging.INFO)

        # Add a file handler when none is registered yet
        if not self.logger.handlers:
            log_file = Path.home() / '.clp/run/codex_proxy.log'
            log_file.parent.mkdir(parents=True, exist_ok=True)

            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
            self.logger.propagate = False

    # To support clients (e.g., Droid/Factory) that omit required Responses headers/fields,
    # force-fill the headers and JSON payload before forwarding.
    def build_target_param(self, path: str, request, body: bytes):  # type: ignore[override]
        target_url, headers, modified_body, active_config_name = super().build_target_param(path, request, body)

        payload = None
        if modified_body:
            try:
                payload = json.loads(modified_body.decode('utf-8'))
            except (ValueError, UnicodeDecodeError):
                payload = None

        client_instruction_override: Optional[str] = None

        def _compose_responses_path(base_path: str) -> str:
            base = (base_path or '').rstrip('/')
            if base and not base.startswith('/'):
                base = '/' + base
            if not base:
                return '/v1/responses'
            if base.endswith('/v1/responses'):
                return base
            if base.endswith('/v1'):
                return f"{base}/responses"
            return f"{base}/v1/responses"

        try:
            normalized_path = path.lstrip('/').lower()
            is_chat_completion_payload = (
                isinstance(payload, dict) and isinstance(payload.get('messages'), list)
            )

            configs_snapshot = self.config_manager.configs
            config_data = configs_snapshot.get(active_config_name or '', {})
            base_url = (config_data.get('base_url') or '').rstrip('/')
            base_path = urlsplit(base_url).path if base_url else ''

            if is_chat_completion_payload and request.method.upper() == 'POST':
                instructions_value = payload.get('instructions')
                combined_instruction = _ensure_primary_instruction(instructions_value if isinstance(instructions_value, str) else None)
                client_instruction_override = combined_instruction
                payload['instructions'] = FULL_INSTRUCTIONS

                messages_payload = payload.get('messages') or []
                normalized_messages = []
                for message in messages_payload:
                    if not isinstance(message, dict):
                        continue
                    role = message.get('role', 'user')
                    content_block = message.get('content')
                    normalized_content = []
                    if isinstance(content_block, str):
                        normalized_content.append({
                            'type': 'input_text',
                            'text': content_block
                        })
                    elif isinstance(content_block, list):
                        for item in content_block:
                            if isinstance(item, dict):
                                text_value = item.get('text')
                                if isinstance(text_value, str):
                                    normalized_content.append({
                                        'type': 'input_text',
                                        'text': text_value
                                    })
                            elif isinstance(item, str):
                                normalized_content.append({
                                    'type': 'input_text',
                                    'text': item
                                })
                    elif content_block is not None:
                        normalized_content.append({
                            'type': 'input_text',
                            'text': str(content_block)
                        })
                    normalized_messages.append({
                        'role': role,
                        'content': normalized_content or [{
                            'type': 'input_text',
                            'text': ''
                        }]
                    })

                payload['input'] = normalized_messages
                payload.pop('messages', None)
                payload.setdefault('stream', True)
                payload.setdefault('store', False)
                path = 'responses'
                normalized_path = 'responses'
                parsed_url = urlsplit(target_url)
                ensured_path = _compose_responses_path(base_path or parsed_url.path)
                parsed_url = parsed_url._replace(path=ensured_path)
                target_url = urlunsplit(parsed_url)
                modified_body = json.dumps(payload, ensure_ascii=False).encode('utf-8')

            if normalized_path.endswith('responses') or (
                request.method.upper() == 'POST'
                and not normalized_path
                and isinstance(payload, dict)
                and payload.get('input') is not None
            ):
                # Force mandatory Responses headers
                headers['openai-beta'] = 'responses=experimental'
                headers['accept'] = 'text/event-stream'
                # Avoid upstream compression (zstd/gzip) that downstream clients cannot decode
                headers['accept-encoding'] = 'identity'
                headers.setdefault('content-type', 'application/json')

                # If the upstream base_url lacks /v1 while the client calls /responses,
                # rewrite the path to /v1/responses (e.g. https://.../responses -> .../v1/responses)
                try:
                    parsed_target = urlsplit(target_url)
                    ensured_path = _compose_responses_path(base_path or parsed_target.path)
                    parsed_target = parsed_target._replace(path=ensured_path)
                    target_url = urlunsplit(parsed_target)
                except Exception:
                    pass

                # Ensure required JSON fields exist: store=false, stream=true, instructions
                if modified_body:
                    try:
                        changed = False
                        if not isinstance(payload, dict):
                            payload = {}
                            changed = True
                        input_block = payload.get('input')
                        if isinstance(input_block, str) and input_block.strip():
                            payload['input'] = [{
                                'role': 'user',
                                'content': [{
                                    'type': 'input_text',
                                    'text': input_block
                                }]
                            }]
                            changed = True
                        elif isinstance(input_block, dict):
                            payload['input'] = [input_block]
                            changed = True
                        elif isinstance(input_block, list):
                            # Ensure list entries are dictionaries with required fields
                            normalized_list = []
                            list_changed = False
                            for item in input_block:
                                if isinstance(item, str):
                                    normalized_list.append({
                                        'role': 'user',
                                        'content': [{
                                            'type': 'input_text',
                                            'text': item
                                        }]
                                    })
                                    list_changed = True
                                elif isinstance(item, dict):
                                    normalized_list.append(item)
                                else:
                                    list_changed = True
                            if list_changed:
                                payload['input'] = normalized_list
                                changed = True

                        instructions_value = payload.get('instructions')
                        current_instruction = instructions_value if isinstance(instructions_value, str) else ''
                        combined_instruction = _ensure_primary_instruction(current_instruction)

                        if client_instruction_override is None:
                            client_instruction_override = combined_instruction
                        else:
                            client_instruction_override = _ensure_primary_instruction(client_instruction_override)

                        if current_instruction.strip() != FULL_INSTRUCTIONS:
                            payload['instructions'] = FULL_INSTRUCTIONS
                            changed = True

                        instructions_text = client_instruction_override

                        if instructions_text.strip():
                            messages = payload.get('input')
                            if not isinstance(messages, list):
                                messages = []
                            system_message = {
                                'role': 'system',
                                'content': [{
                                    'type': 'input_text',
                                    'text': instructions_text
                                }]
                            }
                            prepend = True
                            if messages:
                                first = messages[0]
                                first_content = first.get('content')
                                if (
                                    first.get('role') == 'system'
                                    and isinstance(first_content, list)
                                    and first_content
                                    and first_content[0].get('text') == instructions_text
                                ):
                                    prepend = False
                            if prepend:
                                messages = [system_message] + messages
                                payload['input'] = messages
                                changed = True

                        if payload.get('store') is None:
                            payload['store'] = False
                            changed = True
                        if payload.get('stream') is None:
                            payload['stream'] = True
                            changed = True
                        if payload.get('tool_choice') is None:
                            payload['tool_choice'] = 'auto'
                            changed = True

                        model_name = payload.get('model') or ''
                        default_effort = self._get_default_effort(model_name)
                        reasoning_block = payload.get('reasoning') if isinstance(payload.get('reasoning'), dict) else {}
                        if default_effort and reasoning_block.get('effort') != default_effort:
                            reasoning_block['effort'] = default_effort
                            changed = True

                        summary_value = ''
                        if isinstance(reasoning_block, dict):
                            raw_summary = reasoning_block.get('summary')
                            if isinstance(raw_summary, str):
                                normalized_summary = raw_summary.strip().lower()
                                if normalized_summary in {'auto', 'detailed'}:
                                    if normalized_summary != raw_summary:
                                        reasoning_block['summary'] = normalized_summary
                                        changed = True
                                    summary_value = normalized_summary
                                elif normalized_summary in {'off', ''}:
                                    if 'summary' in reasoning_block:
                                        reasoning_block.pop('summary', None)
                                        changed = True
                                else:
                                    if 'summary' in reasoning_block:
                                        reasoning_block.pop('summary', None)
                                        changed = True
                            elif raw_summary is not None:
                                reasoning_block.pop('summary', None)
                                changed = True

                        if not summary_value:
                            default_summary = self._get_default_summary(model_name)
                            if default_summary:
                                reasoning_block['summary'] = default_summary
                                summary_value = default_summary
                                changed = True
                            elif 'summary' in reasoning_block:
                                reasoning_block.pop('summary', None)
                                changed = True

                        if reasoning_block:
                            payload['reasoning'] = reasoning_block
                        elif 'reasoning' in payload:
                            payload['reasoning'] = reasoning_block

                        default_verbosity = self._get_default_verbosity(model_name)
                        text_settings = payload.get('text') if isinstance(payload.get('text'), dict) else {}
                        if text_settings.get('format') != {'type': 'text'}:
                            text_settings['format'] = {'type': 'text'}
                            changed = True
                        if default_verbosity and text_settings.get('verbosity') != default_verbosity:
                            text_settings['verbosity'] = default_verbosity
                            changed = True
                        if text_settings:
                            payload['text'] = text_settings
                        # Remove optional fields that upstream rejects (added by some clients),
                        # e.g. max_output_tokens, service_tier, etc.
                        # Apply a whitelist to stay within the minimal/commonly supported schema.
                        allowed_keys = {
                            'model', 'instructions', 'input', 'tool_choice',
                            'parallel_tool_calls', 'reasoning', 'store', 'stream',
                            'include', 'prompt_cache_key', 'tools', 'text'
                        }
                        # Preserve legacy chat payloads when present so the upstream can validate them
                        if 'messages' in payload:
                            allowed_keys.add('messages')

                        filtered = {k: v for k, v in payload.items() if k in allowed_keys}
                        if filtered != payload:
                            payload = filtered
                            changed = True

                        if changed:
                            modified_body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
                    except Exception:
                        # If parsing fails, leave the body untouched and rely on header adjustments
                        pass
        except Exception:
            pass

        if client_instruction_override:
            try:
                request.state.codex_original_instructions = client_instruction_override
            except AttributeError:
                pass

        return target_url, headers, modified_body, active_config_name

    def _get_default_effort(self, model: str) -> str:
        if not model:
            return 'medium'
        try:
            import json
            cfg_file = self.data_dir / 'system.json'
            if not cfg_file.exists():
                return 'medium'
            with open(cfg_file, 'r', encoding='utf-8') as handle:
                data = json.load(handle) or {}
            effort_map = (
                data.get('codexDefaults', {})
                    .get('reasoningEffortByModel', {})
            )
            if not isinstance(effort_map, dict):
                return 'medium'
            effort = effort_map.get(model)
            value = effort.lower().strip() if isinstance(effort, str) else 'medium'
            if value not in {'minimal', 'low', 'medium', 'high'}:
                value = 'medium'
            if model == 'gpt-5-codex' and value == 'minimal':
                value = 'medium'
            return value
        except Exception:
            return 'medium'

    def _get_default_verbosity(self, model: str) -> str:
        if not model:
            return 'auto'
        try:
            import json
            cfg_file = self.data_dir / 'system.json'
            if not cfg_file.exists():
                return 'auto'
            with open(cfg_file, 'r', encoding='utf-8') as handle:
                data = json.load(handle) or {}
            verb_map = (
                data.get('codexDefaults', {})
                    .get('verbosityByModel', {})
            )
            if not isinstance(verb_map, dict):
                return ''
            verb = verb_map.get(model)
            verb_value = verb.lower().strip() if isinstance(verb, str) else ''
            return verb_value if verb_value in {'low', 'medium', 'high'} else ''
        except Exception:
            return ''

    def _get_default_summary(self, model: str) -> str:
        if not model:
            return ''
        try:
            import json
            cfg_file = self.data_dir / 'system.json'
            if not cfg_file.exists():
                return ''
            with open(cfg_file, 'r', encoding='utf-8') as handle:
                data = json.load(handle) or {}
            summary_map = (
                data.get('codexDefaults', {})
                    .get('summaryByModel', {})
            )
            if not isinstance(summary_map, dict):
                return 'auto'
            summary = summary_map.get(model)
            summary_value = summary.lower().strip() if isinstance(summary, str) else ''
            return summary_value if summary_value in {'auto', 'detailed'} else 'auto'
        except Exception:
            return 'auto'

    def get_response_chunk_transformer(self, request, path, target_headers, target_body):
        original_instructions = getattr(getattr(request, "state", object()), "codex_original_instructions", None)
        if not original_instructions:
            return None

        if not original_instructions.strip():
            return None

        class _InstructionChunkTransformer:
            def __init__(self, replacement: str):
                self.replacement = replacement
                self.buffer = ''

            def _process_line(self, line: str) -> str:
                if line.startswith('data: '):
                    try:
                        payload = json.loads(line[6:])
                        response_obj = payload.get('response')
                        if isinstance(response_obj, dict):
                            response_obj['instructions'] = self.replacement
                            payload['response'] = response_obj
                            return 'data: ' + json.dumps(payload, ensure_ascii=False)
                    except json.JSONDecodeError:
                        pass
                return line

            def process(self, chunk: bytes) -> bytes:
                if chunk:
                    self.buffer += chunk.decode('utf-8', errors='ignore')

                output_lines = []
                while True:
                    newline_index = self.buffer.find('\n')
                    if newline_index == -1:
                        break
                    line, self.buffer = self.buffer[:newline_index], self.buffer[newline_index + 1:]
                    output_lines.append(self._process_line(line))

                if output_lines:
                    return ('\n'.join(output_lines) + '\n').encode('utf-8')
                return b''

            def flush(self) -> bytes:
                if self.buffer:
                    line = self._process_line(self.buffer)
                    self.buffer = ''
                    return (line + '\n').encode('utf-8')
                return b''

        return _InstructionChunkTransformer(original_instructions)

    def test_endpoint(self, model: str, base_url: str, auth_token: str = None, api_key: str = None, extra_params: dict = None) -> dict:
        """Return a standard response for unsupported endpoint probes."""
        return {
            "status": "unsupported",
            "message": "Endpoint probing is not supported by the Codex proxy."
        }

# Global singleton instance
proxy_service = CodexProxy()
app = proxy_service.app

# Routes are registered by BaseProxyService._setup_routes

# build_target_param is implemented in the base class

# log_request is implemented in the base class

def run_app(port=3211):
    """Launch the Codex proxy service."""
    proxy_service.run_app()

if __name__ == '__main__':
    # Run uvicorn directly for local debugging
    import uvicorn

    uvicorn.run(
        app,
        host='0.0.0.0',
        port=3211,
        log_level='info',
        timeout_keep_alive=60,
        http='h11'
    )
