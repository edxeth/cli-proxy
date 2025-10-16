#!/usr/bin/env python3
"""Legacy service controller built on the shared proxy infrastructure."""
from ..core.base_proxy import BaseServiceController
from ..config.cached_config_manager import legacy_config_manager


class LegacyController(BaseServiceController):
    """Controller wrapper for the Legacy proxy service."""

    def __init__(self):
        super().__init__(
            service_name='legacy',
            port=3212,
            config_manager=legacy_config_manager,
            proxy_module_path='src.legacy.proxy'
        )


controller = LegacyController()


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


def set_active_config(config_name):
    """Set the active configuration."""
    if legacy_config_manager.set_active_config(config_name):
        print(f"Legacy config switched to: {config_name}")
        return True
    print(f"Config {config_name} does not exist")
    return False


def list_configs():
    """List every available configuration."""
    configs = legacy_config_manager.configs
    active = legacy_config_manager.active_config

    if not configs:
        print("Legacy: no configurations available")
        return

    print("Legacy configurations:")
    for name in configs:
        if name == active:
            print(f"  * {name} (active)")
        else:
            print(f"    {name}")
