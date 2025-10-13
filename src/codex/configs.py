# Use the cached configuration manager
from ..config.cached_config_manager import codex_config_manager

# Re-export convenience helpers from the config manager
def get_active_config():
    return codex_config_manager.active_config

def get_configs():
    return codex_config_manager.configs