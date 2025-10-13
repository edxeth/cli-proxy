#!/usr/bin/env python3
"""Claude service controller built on the optimized base classes."""
from ..core.base_proxy import BaseServiceController
from ..config.cached_config_manager import claude_config_manager

class ClaudeController(BaseServiceController):
    """Controller wrapper for the Claude proxy service."""
    def __init__(self):
        super().__init__(
            service_name='claude',
            port=3210,
            config_manager=claude_config_manager,
            proxy_module_path='src.claude.proxy'
        )
        # Keep the legacy PID file name for compatibility
        self.pid_file = self.config_dir / 'claude_code_proxy.pid'

# Create a global instance
controller = ClaudeController()

# Compatibility wrappers (preserve the legacy interface)
def get_pid():
    return controller.get_pid()

def is_running():
    return controller.is_running()

def start():
    return controller.start()

def stop():
    return controller.stop()

def restart():
    return controller.restart()

def status():
    controller.status()

# Legacy helpers kept for backwards compatibility
def start_daemon(port=3210):
    """Start the daemon process (legacy alias)."""
    return start()

def stop_handler(signum, frame):
    """Signal handler used by legacy stop routines."""
    stop()

# Export paths used by older tooling for compatibility
from pathlib import Path
config_dir = Path.home() / '.clp/run'
data_dir = Path.home() / '.clp/data'
PID_FILE = controller.pid_file
LOG_FILE = controller.log_file

# Additional helpers
def set_active_config(config_name):
    """Set the active configuration."""
    if claude_config_manager.set_active_config(config_name):
        print(f"Claude config switched to: {config_name}")
        return True
    else:
        print(f"Config {config_name} does not exist")
        return False

def list_configs():
    """List every available configuration."""
    configs = claude_config_manager.configs
    active = claude_config_manager.active_config
    
    if not configs:
        print("Claude: no configurations available")
        return
    
    print("Claude configurations:")
    for name in configs:
        if name == active:
            print(f"  * {name} (active)")
        else:
            print(f"    {name}")