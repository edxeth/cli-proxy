#!/usr/bin/env python3
"""Cached configuration manager that reduces file I/O by caching results."""
import json
import time
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

class CachedConfigManager:
    """Configuration manager with an in-memory cache."""
    
    def __init__(self, service_name: str, cache_ttl: float = 5.0):
        """
        Initialize the cached configuration manager.

        Args:
            service_name: Name of the service (claude/codex)
            cache_ttl: Cache expiration window in seconds (default 5s)
        """
        self.service_name = service_name
        self.cache_ttl = cache_ttl
        self.config_dir = Path.home() / '.clp'
        self.config_file = self.config_dir / f'{service_name}.json'
        
        # Cache state
        self._configs_cache = {}
        self._active_config_cache = None
        self._cache_time = 0
        self._file_mtime = 0
        self._lock = threading.RLock()

    def _ensure_config_dir(self):
        """Ensure the configuration directory exists."""
        self.config_dir.mkdir(exist_ok=True)

    def _ensure_config_file(self) -> bool:
        """Ensure the config file exists; return True if newly created."""
        self._ensure_config_dir()
        if not self.config_file.exists():
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump({}, f, ensure_ascii=False, indent=2)
            return True
        return False
    
    def ensure_config_file(self) -> Path:
        """Public helper to guarantee the config file exists."""
        self._ensure_config_file()
        return self.config_file
        
    def _should_reload(self) -> bool:
        """
        Determine whether the configuration should be reloaded
        based on file modification time or cache TTL.
        """
        try:
            # Check the file modification time
            current_mtime = self.config_file.stat().st_mtime
            if current_mtime != self._file_mtime:
                return True

            # Check whether the cache expired
            if time.time() - self._cache_time > self.cache_ttl:
                return True

            return False
        except (OSError, FileNotFoundError):
            # File missing or inaccessible; force a reload
            return True
    
    def _load_configs_from_file(self) -> Tuple[Dict[str, Dict[str, Any]], Optional[str]]:
        """Load configurations from disk (internal helper)."""
        created_new = self._ensure_config_file()
        if created_new:
            return {}, None
            
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            configs = {}
            active_config = None
            
            # Parse the config format
            for config_name, config_data in data.items():
                if 'base_url' in config_data and 'auth_token' in config_data:
                    configs[config_name] = {
                        'base_url': config_data['base_url'],
                        'auth_token': config_data['auth_token']
                    }
                    # Preserve optional api_key if present
                    if 'api_key' in config_data:
                        configs[config_name]['api_key'] = config_data['api_key']

                    # Parse weight values
                    weight_value = config_data.get('weight', 0)
                    try:
                        weight_value = float(weight_value)
                    except (TypeError, ValueError):
                        weight_value = 0
                    configs[config_name]['weight'] = weight_value

                    # Parse optional RPM limit (requests per minute)
                    rpm_value = config_data.get('rpm_limit')
                    if rpm_value is None:
                        rpm_value = config_data.get('requests_per_minute')
                    try:
                        rpm_value = float(rpm_value) if rpm_value is not None else None
                        if rpm_value is not None and rpm_value <= 0:
                            rpm_value = None
                    except (TypeError, ValueError):
                        rpm_value = None
                    if rpm_value is not None:
                        configs[config_name]['rpm_limit'] = rpm_value

                    # Parse optional streaming preference (true/false/auto)
                    # true = always use streaming, false = never use streaming, auto/None = let client decide
                    streaming_value = config_data.get('streaming')
                    if streaming_value is not None:
                        if isinstance(streaming_value, bool):
                            configs[config_name]['streaming'] = streaming_value
                        elif isinstance(streaming_value, str):
                            streaming_lower = streaming_value.strip().lower()
                            if streaming_lower in ('true', '1', 'yes', 'on'):
                                configs[config_name]['streaming'] = True
                            elif streaming_lower in ('false', '0', 'no', 'off'):
                                configs[config_name]['streaming'] = False
                            elif streaming_lower == 'auto':
                                configs[config_name]['streaming'] = None

                    # Parse optional tool_calls_streaming preference (true/false/auto)
                    # true = allow streaming with tools, false = disable streaming with tools, auto/None = default behavior
                    tool_calls_streaming_value = config_data.get('tool_calls_streaming')
                    if tool_calls_streaming_value is not None:
                        if isinstance(tool_calls_streaming_value, bool):
                            configs[config_name]['tool_calls_streaming'] = tool_calls_streaming_value
                        elif isinstance(tool_calls_streaming_value, str):
                            tool_calls_streaming_lower = tool_calls_streaming_value.strip().lower()
                            if tool_calls_streaming_lower in ('true', '1', 'yes', 'on'):
                                configs[config_name]['tool_calls_streaming'] = True
                            elif tool_calls_streaming_lower in ('false', '0', 'no', 'off'):
                                configs[config_name]['tool_calls_streaming'] = False
                            elif tool_calls_streaming_lower == 'auto':
                                configs[config_name]['tool_calls_streaming'] = None

                    # Capture the active marker if set
                    if config_data.get('active', False):
                        active_config = config_name
                        
        except (json.JSONDecodeError, OSError) as e:
            print(f"Failed to load configuration file: {e}")
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump({}, f, ensure_ascii=False, indent=2)
            return {}, None
            
        # Fall back to the first config if none were marked active
        if not active_config and configs:
            active_config = list(configs.keys())[0]
            
        return configs, active_config
    
    def _refresh_cache(self):
        """Refresh the in-memory cache (internal helper)."""
        configs, active_config = self._load_configs_from_file()
        self._configs_cache = configs
        self._active_config_cache = active_config
        self._cache_time = time.time()
        
        # Update the cached file modification timestamp
        try:
            self._file_mtime = self.config_file.stat().st_mtime
        except (OSError, FileNotFoundError):
            self._file_mtime = 0
    
    def _get_cached_data(self) -> Tuple[Dict[str, Dict[str, Any]], Optional[str]]:
        """Return cached configuration data."""
        with self._lock:
            if self._should_reload():
                self._refresh_cache()
            return self._configs_cache.copy(), self._active_config_cache
    
    @property
    def configs(self) -> Dict[str, Dict[str, Any]]:
        """Return all configurations (served from cache)."""
        configs, _ = self._get_cached_data()
        return configs
    
    @property
    def active_config(self) -> Optional[str]:
        """Return the active configuration name (served from cache)."""
        _, active_config = self._get_cached_data()
        return active_config
    
    def set_active_config(self, config_name: str) -> bool:
        """
        Set the active configuration.
        Note: this immediately writes to disk and refreshes the cache.
        """
        with self._lock:
            # Refresh cache first to ensure we have fresh data
            self._refresh_cache()
            
            if config_name not in self._configs_cache:
                return False
            
            try:
                self._save_configs(self._configs_cache, config_name)
                # Refresh cache immediately after persisting
                self._refresh_cache()
                return True
            except Exception as e:
                print(f"Failed to save configuration: {e}")
                return False
    
    def _save_configs(self, configs: Dict[str, Dict[str, Any]], active_config: str):
        """Persist configurations to disk."""
        if not configs:
            return
            
        self._ensure_config_dir()
        
        # Build the payload to persist
        data = {}
        for name, config in configs.items():
            data[name] = {
                'base_url': config['base_url'],
                'auth_token': config['auth_token'],
                'active': name == active_config
            }
            # Persist optional api_key if provided
            if 'api_key' in config:
                data[name]['api_key'] = config['api_key']

            if 'weight' in config:
                data[name]['weight'] = config['weight']

            if 'rpm_limit' in config and config['rpm_limit'] is not None:
                data[name]['rpm_limit'] = config['rpm_limit']

            if 'streaming' in config:
                data[name]['streaming'] = config['streaming']

            if 'tool_calls_streaming' in config:
                data[name]['tool_calls_streaming'] = config['tool_calls_streaming']
        
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError as e:
            print(f"Failed to write configuration file: {e}")
            raise
    
    def get_active_config_data(self) -> Optional[Dict[str, Any]]:
        """Return the data for the active configuration (cached)."""
        configs, active_config = self._get_cached_data()
        if not active_config:
            return None
        return configs.get(active_config)
    
    def force_reload(self):
        """Force a reload of the configuration, bypassing the cache."""
        with self._lock:
            self._refresh_cache()

# Global cached instances for convenience
claude_config_manager = CachedConfigManager('claude')
codex_config_manager = CachedConfigManager('codex')
legacy_config_manager = CachedConfigManager('legacy')
