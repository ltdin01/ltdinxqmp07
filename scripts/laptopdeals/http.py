from __future__ import annotations

from importlib import import_module
from typing import Any


def curl_requests() -> Any:
    try:
        return import_module("curl_cffi.requests")
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("curl-cffi is required for network operations. Install dependencies from requirements.txt.") from exc

