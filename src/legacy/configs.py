"""Convenience helpers for accessing Legacy configurations."""
from ..config.cached_config_manager import legacy_config_manager


def get_active_config():
    return legacy_config_manager.active_config


def get_configs():
    return legacy_config_manager.configs
