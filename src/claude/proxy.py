#!/usr/bin/env python3
"""Claude proxy service built on the shared base proxy infrastructure."""
import aiohttp
import logging
import datetime
import json
import secrets
import uuid
from typing import Dict, Optional, Tuple
import copy
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request
from ..core.base_proxy import BaseProxyService
from ..config.cached_config_manager import claude_config_manager

CLAUDE_CODE_SYSTEM_PROMPT = [
    {
        "type": "text",
        "text": "You are Claude Code, Anthropic's official CLI for Claude.",
        "cache_control": {"type": "ephemeral"},
    }
]

class ClaudeProxy(BaseProxyService):
    """Claude proxy service implementation."""

    def __init__(self):
        super().__init__(
            service_name='claude',
            port=3210,
            config_manager=claude_config_manager
        )

        # Allow the UI to connect through CORS
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:3300", "http://127.0.0.1:3300"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Configure a dedicated logger
        self.logger = logging.getLogger('claude_proxy')
        self.logger.setLevel(logging.INFO)

        # Add a file handler if one has not been configured yet
        if not self.logger.handlers:
            log_file = Path.home() / '.clp/run/claude_proxy.log'
            log_file.parent.mkdir(parents=True, exist_ok=True)

            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
            self.logger.propagate = False

        # Generate a stable metadata identifier so upstream sees a consistent session
        self._metadata_user_id = self._load_or_create_metadata_user_id()

    def build_target_param(
        self, path: str, request: Request, body: bytes
    ) -> Tuple[str, Dict, bytes, Optional[str]]:
        """Extend base routing with Claude-specific defaults."""
        target_url, headers, modified_body, active_config_name = super().build_target_param(path, request, body)

        parsed = urlsplit(target_url)
        query_items = parse_qsl(parsed.query, keep_blank_values=True)
        if not any(key.lower() == 'beta' for key, _ in query_items):
            query_items.append(('beta', 'true'))
        new_query = urlencode(query_items, doseq=True)
        if new_query != parsed.query:
            parsed = parsed._replace(query=new_query)
            target_url = urlunsplit(parsed)

        model_name = None
        if modified_body:
            try:
                model_name = json.loads(modified_body.decode('utf-8')).get('model')
            except Exception:
                model_name = None

        def find_header(name: str):
            lower_name = name.lower()
            for key, value in headers.items():
                if key.lower() == lower_name:
                    return key, value
            return None, None

        def set_header(name: str, value: str):
            key, _ = find_header(name)
            if key is None:
                headers[name] = value
            else:
                headers[key] = value

        def ensure_header(name: str, value: str):
            key, current = find_header(name)
            if key is None:
                headers[name] = value
            elif not (isinstance(current, str) and current.strip()):
                headers[key] = value

        def header_missing(name: str) -> bool:
            key, current = find_header(name)
            if key is None:
                return True
            if isinstance(current, str):
                return not current.strip()
            return current in (None, '')

        ensure_header('anthropic-version', '2023-06-01')
        ensure_header('x-app', 'cli')
        ensure_header('anthropic-dangerous-direct-browser-access', 'true')

        count_tokens = 'count_tokens' in path.lower()
        base_flags = [
            'interleaved-thinking-2025-05-14',
            'fine-grained-tool-streaming-2025-05-14',
        ]
        include_claude_code = count_tokens or (model_name and 'haiku' not in model_name.lower())

        required_flags = []
        if include_claude_code:
            required_flags.append('claude-code-20250219')
        required_flags.extend(base_flags)
        if count_tokens:
            required_flags.append('token-counting-2024-11-01')

        beta_key, beta_value = find_header('anthropic-beta')
        existing_flags = []
        if isinstance(beta_value, str) and beta_value.strip():
            existing_flags = [flag.strip() for flag in beta_value.split(',') if flag.strip()]

        ordered_flags = []

        def append_flag(flag: str):
            if flag not in ordered_flags:
                ordered_flags.append(flag)

        for flag in required_flags:
            append_flag(flag)
        for flag in existing_flags:
            append_flag(flag)

        if ordered_flags:
            set_header('anthropic-beta', ','.join(ordered_flags))

        # Canonicalize headers expected by Claude Code upstream
        set_header('user-agent', 'claude-cli/2.0.17 (external, cli)')
        set_header('accept-encoding', 'gzip, deflate')
        set_header('accept-language', '*')
        set_header('x-stainless-arch', 'x64')
        set_header('x-stainless-helper-method', 'stream')
        set_header('x-stainless-lang', 'js')
        set_header('x-stainless-os', 'Linux')
        set_header('x-stainless-package-version', '0.60.0')
        set_header('x-stainless-retry-count', '0')
        set_header('x-stainless-runtime', 'node')
        set_header('x-stainless-runtime-version', 'v24.8.0')
        set_header('x-stainless-timeout', '600')

        if not header_missing('x-api-key'):
            auth_key, _ = find_header('authorization')
            if auth_key:
                headers.pop(auth_key, None)

        modified_body = self._normalize_request_payload(modified_body)

        return target_url, headers, modified_body, active_config_name

    def _normalize_request_payload(self, body: bytes) -> bytes:
        """Ensure metadata and system prompts match Claude Code expectations."""
        if not body:
            return body

        try:
            payload = json.loads(body.decode('utf-8'))
        except Exception:
            return body

        if not isinstance(payload, dict):
            return body

        mutated = False

        metadata = payload.get('metadata')
        if not isinstance(metadata, dict):
            metadata = {}
        if not metadata.get('user_id'):
            metadata['user_id'] = self._default_metadata_user_id()
            payload['metadata'] = metadata
            mutated = True

        original_system = payload.get('system')
        normalized_system = copy.deepcopy(CLAUDE_CODE_SYSTEM_PROMPT)
        if isinstance(original_system, list):
            for entry in original_system:
                if isinstance(entry, dict) and entry not in normalized_system:
                    normalized_system.append(entry)

        if original_system != normalized_system:
            payload['system'] = normalized_system
            mutated = True

        if not mutated:
            return body

        try:
            return json.dumps(payload, ensure_ascii=False).encode('utf-8')
        except Exception:
            return body

    def _default_metadata_user_id(self) -> str:
        """Return a stable metadata user identifier for Claude Code."""
        return f'user_{self._metadata_account_id}_account__session_{self._metadata_session_id}'

    def _load_or_create_metadata_user_id(self) -> str:
        """Persist a realistic-looking metadata user id to align with Claude Code expectations."""
        meta_file = self.data_dir / 'claude_metadata.json'
        account_id = None
        session_id = None

        if meta_file.exists():
            try:
                data = json.loads(meta_file.read_text(encoding='utf-8'))
                account_id = data.get('account_id')
                session_id = data.get('session_id')
            except (OSError, json.JSONDecodeError):
                account_id = session_id = None

        if not account_id:
            account_id = secrets.token_hex(32)  # 64 hex characters similar to real account ids
        if not session_id:
            session_id = str(uuid.uuid4())

        try:
            meta_file.write_text(json.dumps({'account_id': account_id, 'session_id': session_id}, indent=2), encoding='utf-8')
        except OSError:
            pass

        self._metadata_account_id = account_id
        self._metadata_session_id = session_id
        return f'user_{account_id}_account__session_{session_id}'

    def test_endpoint(self, model: str, base_url: str, auth_token: str = None, api_key: str = None, extra_params: dict = None) -> dict:
        """Test connectivity against an upstream Claude API endpoint."""
        return {
            "status": "unsupported",
            "message": "Endpoint probing is not supported by this proxy."
        }

# Global singleton instance
proxy_service = ClaudeProxy()
app = proxy_service.app


def run_app(port=3210):
    """Launch the Claude proxy service."""
    proxy_service.run_app()

if __name__ == '__main__':
    # Run uvicorn directly when executing this module
    import uvicorn

    uvicorn.run(
        app,
        host='0.0.0.0',
        port=3210,
        log_level='info',
        timeout_keep_alive=60,
        http='h11'
    )
