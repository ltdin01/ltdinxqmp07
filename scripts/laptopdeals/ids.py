from __future__ import annotations

import json
import re
from argparse import Namespace
from pathlib import Path
from typing import Any, Iterable


def normalize_id(value: Any) -> str:
    return str(value or "").strip().upper()


def split_ids(values: Iterable[str] | None) -> set[str]:
    ids: set[str] = set()
    for value in values or []:
        for part in re.split(r"[\s,]+", str(value)):
            part = normalize_id(part)
            if part:
                ids.add(part)
    return ids


def read_ids_file(path: str | Path | None) -> set[str]:
    if not path:
        return set()
    target = Path(path)
    if not target.exists():
        print(f"[warn] IDs file not found: {target}")
        return set()
    payload = json.loads(target.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, dict):
        values = payload.get("ids") or payload.get("new_ids") or []
    else:
        values = []
    return {pid for pid in (normalize_id(item) for item in values) if pid}


def ids_from_args(args: Namespace) -> set[str]:
    return split_ids(getattr(args, "id", [])) | read_ids_file(getattr(args, "ids_file", ""))

