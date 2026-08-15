from __future__ import annotations

import importlib
from typing import Any

DEFAULT_PROVIDER = "lenovo"

_PROVIDERS: dict[str, Any] = {}


def register(name: str, module: Any) -> None:
    _PROVIDERS[name] = module


def get_provider(name: str = DEFAULT_PROVIDER) -> Any:
    """Return the provider adapter module for ``name``, lazily importing it."""
    if name not in _PROVIDERS:
        importlib.import_module(f"laptopdeals.providers.{name}")
    if name not in _PROVIDERS:
        raise KeyError(f"unknown provider: {name!r}")
    return _PROVIDERS[name]


def available_providers() -> list[str]:
    return sorted(_PROVIDERS)
