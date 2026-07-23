from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON: {path}: {exc}") from exc


def normalize_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: normalize_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_json_value(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def write_json(path: Path, payload: Any, *, indent: int | None = 4) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = normalize_json_value(payload)
    text = json.dumps(payload, indent=indent, ensure_ascii=False)
    if path.exists():
        current = path.read_text(encoding="utf-8")
        try:
            if normalize_json_value(json.loads(current)) == payload:
                return
        except json.JSONDecodeError:
            pass
        if current.endswith("\n"):
            text += "\n"
        if current == text:
            return
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
