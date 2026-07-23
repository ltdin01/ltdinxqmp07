from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_spec_hash(product: dict[str, Any]) -> str:
    """Compute deterministic hash of tech specs, cto configs, title, and images."""
    payload = {
        "title": product.get("name") or product.get("title") or "",
        "specs": product.get("tech_specs") or {},
        "cto": product.get("cto_options") or product.get("cto_config") or {},
        "images": product.get("images") or [],
    }
    dumped = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()[:16]


def compute_price_hash(product: dict[str, Any]) -> str:
    """Compute deterministic hash of price, mrp, and availability."""
    payload = {
        "price": str(product.get("price") or ""),
        "mrp": str(product.get("mrp") or ""),
        "availability": str(product.get("availability") or ""),
    }
    dumped = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()[:16]


def classify_update(old_product: dict[str, Any] | None, new_product: dict[str, Any]) -> str:
    """
    Classify an incoming product update.

    Returns:
      'new'          - Brand new laptop discovered
      'spec_changed' - Spec structural or feature change detected (requires PSREF/CTO re-enrichment)
      'price_changed'- Price/availability changed, specs identical (skip PSREF)
      'unchanged'    - Neither price nor specs changed
    """
    if not old_product:
        return "new"
    old_spec_hash = compute_spec_hash(old_product)
    new_spec_hash = compute_spec_hash(new_product)
    if old_spec_hash != new_spec_hash:
        return "spec_changed"
    old_price_hash = compute_price_hash(old_product)
    new_price_hash = compute_price_hash(new_product)
    if old_price_hash != new_price_hash:
        return "price_changed"
    return "unchanged"
