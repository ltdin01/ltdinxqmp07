from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable

from .ids import normalize_id
from .jsonio import read_json, write_json


def iter_products(data: Any):
    if isinstance(data, dict) and isinstance(data.get("groups"), dict):
        for group, products in data["groups"].items():
            if isinstance(products, list):
                for product in products:
                    if isinstance(product, dict):
                        yield group, product
        return
    if isinstance(data, dict):
        for group, products in data.items():
            if isinstance(products, list):
                for product in products:
                    if isinstance(product, dict):
                        yield group, product
    elif isinstance(data, list):
        for product in data:
            if isinstance(product, dict):
                yield "", product


def product_index(data: Any) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for _, product in iter_products(data):
        pid = normalize_id(product.get("id") or product.get("product_code"))
        if pid:
            index[pid] = product
    return index


def load_products(path: Path) -> Any:
    return read_json(path, {})


def save_products(path: Path, data: Any) -> None:
    write_json(path, data, indent=4)


def selected_products(data: Any, ids: set[str] | None = None) -> list[dict[str, Any]]:
    out = []
    wanted = ids or set()
    for _, product in iter_products(data):
        pid = normalize_id(product.get("id") or product.get("product_code"))
        if pid and (not wanted or pid in wanted):
            out.append(product)
    return out


def remove_ids(data: Any, ids_to_remove: set[str]) -> tuple[Any, list[dict[str, Any]]]:
    removed: list[dict[str, Any]] = []
    if not isinstance(data, dict):
        return data, removed
    new_data: OrderedDict[str, Any] = OrderedDict()
    for group, products in data.items():
        if not isinstance(products, list):
            new_data[group] = products
            continue
        kept = []
        for product in products:
            pid = normalize_id(product.get("id") if isinstance(product, dict) else "")
            if pid in ids_to_remove:
                removed.append(product)
            else:
                kept.append(product)
        if kept:
            new_data[group] = kept
    return new_data, removed
