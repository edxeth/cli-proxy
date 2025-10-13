# Use the cached configuration manager
from ..config.cached_config_manager import claude_config_manager

# Re-export convenience helpers from the config manager
def get_active_config():
    return claude_config_manager.active_config

def get_configs():
    return claude_config_manager.configs