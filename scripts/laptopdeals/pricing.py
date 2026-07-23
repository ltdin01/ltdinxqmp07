from __future__ import annotations

import concurrent.futures
import random
import time
from pathlib import Path
from typing import Any

from . import history
from .datafile import selected_products
from .ids import normalize_id
from .sources import bitbns, lenovo
from .timeutil import ist_stamp


def update_from_lenovo(
    data: Any,
    *,
    history_dir: Path,
    ids: set[str] | None = None,
    workers: int = 4,
    delay_min: float = 1.0,
    delay_max: float = 4.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    products = selected_products(data, ids)
    result = {"checked": 0, "changed": 0, "failed": 0, "products": []}

    def process(product: dict[str, Any]) -> dict[str, Any]:
        pid = normalize_id(product.get("id"))
        if not pid:
            return {"id": "", "status": "skip"}
        time.sleep(random.uniform(delay_min, delay_max))
        try:
            availability = lenovo.fetch_page_availability(product.get("store_link", ""))
            price, mrp = lenovo.fetch_current_price(pid)
            if not price:
                return {"id": pid, "status": "no_price"}
            new_history, changed = history.apply_current_price(
                history_dir,
                pid,
                price,
                date=ist_stamp(),
                dry_run=dry_run,
            )
            product["price"] = f"{price}.00 INR"
            if mrp:
                product["mrp"] = f"{mrp}.00 INR"
            product["availability"] = availability
            product["last_checked"] = ist_stamp()
            product.update(history.stats(new_history))
            return {"id": pid, "status": "changed" if changed else "stable", "price": price}
        except Exception as exc:
            return {"id": pid, "status": "failed", "error": str(exc)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(process, product) for product in products]
        for future in concurrent.futures.as_completed(futures):
            item = future.result()
            result["checked"] += 1
            if item["status"] == "changed":
                result["changed"] += 1
            if item["status"] == "failed":
                result["failed"] += 1
            result["products"].append(item)
            print(f"[lenovo] {item.get('id')} {item.get('status')} {item.get('price', '')}")
    return result


def update_from_bitbns(
    data: Any,
    *,
    history_dir: Path,
    ids: set[str] | None,
    mode: str,
    missing_history_only: bool = False,
    workers: int = 2,
    delay: float = 2.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    found_ids: set[str] = set()
    products = []
    for product in selected_products(data, ids):
        pid = normalize_id(product.get("id"))
        if pid:
            found_ids.add(pid)
        if missing_history_only and pid and history.load_history(history_dir, pid):
            continue
        products.append(product)

    if ids:
        for target_id in ids:
            pid = normalize_id(target_id)
            if pid and pid not in found_ids:
                if missing_history_only and history.load_history(history_dir, pid):
                    continue
                products.append({"id": pid})
    result = {"checked": 0, "changed": 0, "failed": 0, "products": []}

    def process(product: dict[str, Any]) -> dict[str, Any]:
        pid = normalize_id(product.get("id"))
        if not pid:
            return {"id": "", "status": "skip"}
        try:
            incoming = bitbns.fetch_history(pid, delay=delay)
            existing = history.load_history(history_dir, pid)
            merged = history.replace_points(incoming) if mode == "replace" else history.merge_points(existing, incoming)
            changed = merged != history.replace_points(existing)
            if changed and not dry_run:
                history.write_history(history_dir, pid, merged)
            history.sync_product_stats(product, merged)
            return {"id": pid, "status": "changed" if changed else "stable", "points": len(merged)}
        except Exception as exc:
            return {"id": pid, "status": "failed", "error": str(exc)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(process, product) for product in products]
        for future in concurrent.futures.as_completed(futures):
            item = future.result()
            result["checked"] += 1
            if item["status"] == "changed":
                result["changed"] += 1
            if item["status"] == "failed":
                result["failed"] += 1
            result["products"].append(item)
            print(f"[bitbns] {item.get('id')} {item.get('status')} points={item.get('points', 0)}")
    return result


def recalculate_stats(data: Any, *, history_dir: Path, ids: set[str] | None = None) -> dict[str, Any]:
    changed = 0
    checked = 0
    for product in selected_products(data, ids):
        pid = normalize_id(product.get("id"))
        if not pid:
            continue
        before = {key: product.get(key) for key in ("price_mean", "price_median", "price_usual", "has_history")}
        product.update(history.stats(history.load_history(history_dir, pid)))
        after = {key: product.get(key) for key in before}
        checked += 1
        changed += int(before != after)
    return {"checked": checked, "changed": changed}
