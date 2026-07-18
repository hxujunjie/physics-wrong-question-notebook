"""Backward-compatible export of multi-vendor AI settings."""
from . import ai_settings
from .ai_settings import (
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    PROVIDER_PRESETS,
    clear,
    get_api_key,
    get_connection,
    list_presets,
    make_client,
    save,
    settings_path,
    summary,
)

__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_PROVIDER",
    "PROVIDER_PRESETS",
    "ai_settings",
    "clear",
    "get_api_key",
    "get_connection",
    "list_presets",
    "make_client",
    "save",
    "settings_path",
    "summary",
]
