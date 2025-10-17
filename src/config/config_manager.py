#!/usr/bin/env python3
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional

class ConfigManager:
    """Basic configuration manager without caching."""
    
    def __init__(self, service_name: str):
        self.service_name = service_name
        self.config_dir = Path.home() / '.clp'
        self.config_file = self.config_dir / f'{service_name}.json'

    def _ensure_config_dir(self):
        """Ensure the configuration directory exists."""
        self.config_dir.mkdir(exist_ok=True)

    def _ensure_config_file(self) -> bool:
        """Ensure the config file exists; return True if newly created."""
        self._ensure_config_dir()
        if not self.config_file.exists():
            if self.service_name == 'legacy':
                legacy_alias = self.config_dir / 'a4f.json'
                if legacy_alias.exists():
                    legacy_alias.rename(self.config_file)
                    return False
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump({}, f, ensure_ascii=False, indent=2)
            return True
        return False

    def ensure_config_file(self) -> Path:
        """Public helper to guarantee the config file exists."""
        self._ensure_config_file()
        return self.config_file

    def _load_configs(self) -> tuple[Dict[str, Dict[str, Any]], Optional[str]]:
        """Load configurations from disk without caching."""
        created_new = self._ensure_config_file()
        if created_new:
            return {}, None
            
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            configs = {}
            active_config = None
            
            # Parse the config entries
            for config_name, config_data in data.items():
                if 'base_url' in config_data and 'auth_token' in config_data:
                    configs[config_name] = {
                        'base_url': config_data['base_url'],
                        'auth_token': config_data['auth_token']
                    }

                    # Preserve optional api_key if present
                    if 'api_key' in config_data:
                        configs[config_name]['api_key'] = config_data['api_key']

                    # Parse optional weight fields
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
                    
                    # Mark the active config if specified
                    if config_data.get('active', False):
                        active_config = config_name
                        
        except (json.JSONDecodeError, OSError) as e:
            print(f"Failed to load configuration file: {e}")
            # Ensure an empty file exists to avoid repeat failures
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump({}, f, ensure_ascii=False, indent=2)
            return {}, None
            
        # Default to the first config if none marked active
        if not active_config and configs:
            active_config = list(configs.keys())[0]
            
        return configs, active_config

    @property
    def configs(self) -> Dict[str, Dict[str, Any]]:
        """Return every configuration defined."""
        configs, _ = self._load_configs()
        return configs.copy()

    @property
    def active_config(self) -> Optional[str]:
        """Return the name of the active configuration."""
        _, active_config = self._load_configs()
        return active_config

    def set_active_config(self, config_name: str) -> bool:
        """Set the active configuration."""
        configs, _ = self._load_configs()
        if config_name not in configs:
            return False
        
        try:
            self._save_configs(configs, config_name)
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

            # Persist optional weight values
            if 'weight' in config:
                data[name]['weight'] = config['weight']

            # Persist optional RPM limit
            if 'rpm_limit' in config and config['rpm_limit'] is not None:
                data[name]['rpm_limit'] = config['rpm_limit']
        
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError as e:
            print(f"Failed to write configuration file: {e}")
            raise

    def get_active_config_data(self) -> Optional[Dict[str, Any]]:
        """Return the data for the active configuration."""
        configs, active_config = self._load_configs()
        if not active_config:
            return None
        return configs.get(active_config)

# Global instances
claude_config_manager = ConfigManager('claude')
codex_config_manager = ConfigManager('codex')
legacy_config_manager = ConfigManager('legacy')
