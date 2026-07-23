from __future__ import annotations

import concurrent.futures
import json
import random
import re
import time
from pathlib import Path
from typing import Any

from .datafile import selected_products
from .ids import normalize_id
from .jsonio import read_json, write_json
from .sources import lenovo
from .specs import (
    parse_cpu_psref,
    parse_display_psref,
    parse_gpu_psref,
    parse_memory_psref,
    parse_network_psref,
    parse_storage_psref,
)

from .http import curl_requests

CTO_CATEGORY_PARSERS = {
    "processor": parse_cpu_psref,
    "graphics": parse_gpu_psref,
    "memory": parse_memory_psref,
    "storage": parse_storage_psref,
    "display": parse_display_psref,
    "wireless": parse_network_psref,
}

_RAW_KEYS_TO_DROP = {"raw"}


def classify_cto_option(label: str) -> str:
    low = label.lower().strip()
    if "processor" in low or "cpu" in low:
        return "processor"
    if "graphic" in low or "gpu" in low or "video" in low:
        return "graphics"
    if "memory" in low or "ram" in low:
        return "memory"
    if "solid state" in low or "storage" in low or "drive" in low or "ssd" in low:
        return "storage"
    if "display" in low or ("screen" in low and "protector" not in low) or "panel" in low:
        return "display"
    if "wireless" in low or "wifi" in low or "bluetooth" in low:
        return "wireless"
    return ""


def enrich_cto_options(config: dict[str, Any]) -> dict[str, Any]:
    for option in config.get("options") or []:
        category = classify_cto_option(option.get("label", ""))
        parser = CTO_CATEGORY_PARSERS.get(category)
        for choice in option.get("choices") or []:
            choice.pop("specs", None)
            if not parser:
                continue
            label = choice.get("label", "")
            if not label:
                continue
            parsed = parser(label)
            parsed.pop("raw", None)
            choice["specs"] = {category: parsed}
    return config


def require_requests():
    return curl_requests()


def headers(bundle_id: str) -> dict[str, str]:
    return {
        "User-Agent": random.choice(lenovo.USER_AGENTS),
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://www.lenovo.com",
        "Referer": f"https://www.lenovo.com/in/en/configurator/cto/index.html?bundleId={bundle_id}",
    }


def fetch_init_config(bundle_id: str) -> dict[str, Any] | None:
    req = require_requests()
    url = f"{lenovo.INIT_CONFIG_URL}?bundleId={bundle_id}&plant={lenovo.DEFAULT_PLANT}&vendorId={lenovo.DEFAULT_VENDOR_ID}"
    response = req.get(url, headers=headers(bundle_id), impersonate="chrome120", timeout=30)
    if response.status_code != 200:
        return None
    data = response.json().get("data") or {}
    return data if data.get("status") == "VALID" else None


def fetch_variant_prices(bundle_id: str, variant_keys: list[str]) -> dict[str, dict[str, int]]:
    req = require_requests()
    if not variant_keys:
        return {}
    url = f"{lenovo.CVLIST_URL}?bundleId={bundle_id}&variantKeys={','.join(variant_keys)}"
    response = req.get(url, headers=headers(bundle_id), impersonate="chrome120", timeout=30)
    if response.status_code != 200:
        return {}
    price_map = {}
    for item in response.json().get("data") or []:
        key = item.get("variantKey")
        if key:
            price_map[key] = {
                "price": int(item.get("price") or 0),
                "webPrice": int(item.get("webPrice") or 0),
            }
    return price_map


def fetch_config_price(bundle_id: str, current_cv_list: list[dict[str, Any]]) -> dict[str, Any]:
    req = require_requests()
    response = req.post(
        lenovo.CONFIG_PRICE_URL,
        data={"bundleId": bundle_id, "currentCVList": json.dumps(current_cv_list)},
        headers=headers(bundle_id),
        impersonate="chrome120",
        timeout=30,
    )
    if response.status_code != 200:
        return {}
    arr = response.json().get("data") or []
    if not isinstance(arr, list) or len(arr) <= 11:
        return {}
    pct_match = re.search(r"(\d+)", str(arr[11]))
    return {
        "basePrice": int(arr[5]) if str(arr[5]).isdigit() else 0,
        "finalPrice": int(arr[9]) if str(arr[9]).isdigit() else 0,
        "discountPercent": int(pct_match.group(1)) if pct_match else 0,
    }


def fetch_addons(bundle_id: str) -> list[dict[str, Any]]:
    req = require_requests()
    preselect_url = f"{lenovo.OPENAPI_BASE}/preselect/get?productNumber={bundle_id}"
    response = req.get(preselect_url, headers=headers(bundle_id), impersonate="chrome120", timeout=20)
    if response.status_code != 200:
        return []
    addons = []
    for item in response.json().get("data") or []:
        pn = item.get("preselectProductNumber")
        summary = item.get("summary", "")
        is_warranty = item.get("warrantyType") is not None or any(
            word in summary.upper() for word in ["WARRANTY", "CARE", "ONSITE", "ADP", "PROTECTION"]
        )
        if pn and pn != bundle_id and is_warranty:
            addons.append({"productNumber": pn, "label": summary, "isDefault": item.get("isLock") == "1"})
    if not addons:
        return []
    pns = ",".join(item["productNumber"] for item in addons)
    price_url = f"{lenovo.OPENAPI_BASE}/batch/product/builder/price?material={pns}&mainCode={bundle_id}&psdMapping="
    price_response = req.get(price_url, headers=headers(bundle_id), impersonate="chrome120", timeout=20)
    if price_response.status_code != 200:
        return addons
    prices = price_response.json().get("data") or {}
    for item in addons:
        arr = prices.get(item["productNumber"])
        if isinstance(arr, list) and len(arr) > 7:
            item["price"] = float(arr[5]) if arr[5] else 0
    return [item for item in addons if "price" in item]


def discount_from_product(product: dict[str, Any]) -> int:
    def number(value: Any) -> float:
        match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
        return float(match.group(0)) if match else 0

    price = number(product.get("price"))
    mrp = number(product.get("mrp"))
    return round(((mrp - price) / mrp) * 100) if mrp > price > 0 else 0


def fetch_config(bundle_id: str, product: dict[str, Any] | None = None) -> dict[str, Any] | None:
    time.sleep(random.uniform(1.0, 3.0))
    config_data = fetch_init_config(bundle_id)
    if not config_data:
        return None
    variant_keys: list[str] = []
    visible_options: list[dict[str, Any]] = []
    current_cv_list: list[dict[str, Any]] = []

    for conf in config_data.get("configurations") or []:
        char_name = conf.get("charName", "")
        if not conf.get("isVisible") and char_name not in lenovo.VISIBLE_CATEGORIES and "DISPLAY" not in char_name:
            continue
        choices = []
        for cv in conf.get("charValues") or []:
            is_visible = cv.get("isVisible")
            is_default = cv.get("isDefault")
            is_orig = cv.get("isOriginalSelected")
            if is_visible or is_default or is_orig:
                variant_key = cv.get("variantKey")
                choices.append({
                    "label": cv.get("charValueDesc", ""),
                    "variantKey": variant_key,
                    "isDefault": bool(is_default),
                })
                if variant_key:
                    variant_keys.append(variant_key)
            if is_default or is_orig:
                variant_key = cv.get("variantKey")
                if variant_key:
                    current_cv_list.append({"charName": char_name, "default": [{"variantKey": variant_key, "quantity": 1}]})
        if choices:
            visible_options.append({"label": lenovo.VISIBLE_CATEGORIES.get(char_name, conf.get("charDesc", char_name)), "choices": choices})

    time.sleep(random.uniform(1.0, 2.5))
    price_map = fetch_variant_prices(bundle_id, sorted(set(variant_keys)))
    price_info = fetch_config_price(bundle_id, current_cv_list)
    discount = price_info.get("discountPercent") or (discount_from_product(product or {}) if product else 0)
    addons = fetch_addons(bundle_id)

    clean_options = []
    for option in visible_options:
        enriched = []
        default_price = 0
        for choice in option["choices"]:
            raw = price_map.get(choice.get("variantKey"), {}).get("price", 0)
            final = int(raw * (1 - discount / 100)) if discount else raw
            choice["finalPrice"] = final
            if choice.get("isDefault"):
                default_price = final
        for choice in option["choices"]:
            enriched.append({
                "label": choice.get("label", ""),
                "isDefault": choice.get("isDefault", False),
                "gapPrice": choice.get("finalPrice", 0) - default_price,
            })
        if len(enriched) > 1:
            clean_options.append({"label": option["label"], "choices": enriched})

    return {
        "bundleId": bundle_id,
        "discountPercent": discount,
        "options": clean_options,
        "addons": [{"label": item.get("label", ""), "isDefault": item.get("isDefault", False), "price": item.get("price", 0)} for item in addons],
        **{key: price_info[key] for key in ("basePrice", "finalPrice") if key in price_info},
    }


def stable_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "lastFetched"}


def refresh_cto_configs(
    data: Any,
    *,
    output_dir: Path,
    ids: set[str] | None = None,
    workers: int = 4,
    dry_run: bool = False,
) -> dict[str, Any]:
    products = [p for p in selected_products(data, ids) if "CTO" in normalize_id(p.get("id"))]
    result = {"checked": 0, "changed": 0, "failed": 0}

    def process(product: dict[str, Any]) -> tuple[str, dict[str, Any] | None, str | None]:
        pid = normalize_id(product.get("id"))
        try:
            return pid, fetch_config(pid, product), None
        except Exception as exc:
            return pid, None, str(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(process, product) for product in products]
        for future in concurrent.futures.as_completed(futures):
            pid, payload, error = future.result()
            result["checked"] += 1
            if error or not payload:
                result["failed"] += 1
                print(f"[cto] {pid} failed {error or 'empty'}")
                continue
            payload = stable_payload(payload)
            payload = enrich_cto_options(payload)
            existing_path = output_dir / f"{pid}.json"
            existing = stable_payload(read_json(existing_path, {}) if existing_path.exists() else {})
            if existing == payload:
                print(f"[cto] {pid} unchanged")
                continue
            result["changed"] += 1
            if not dry_run:
                write_json(existing_path, payload, indent=4)
            print(f"[cto] {pid} changed")
    return result


def reenrich_all_cto(cto_dir: Path, force: bool = False) -> dict[str, Any]:
    enriched = 0
    skipped = 0
    for path in sorted(cto_dir.glob("*.json")):
        config = read_json(path, {})
        if "options" not in config:
            skipped += 1
            continue
        if not force:
            has_any_specs = any(
                "specs" in choice
                for option in config.get("options", [])
                for choice in option.get("choices", [])
            )
            if has_any_specs:
                skipped += 1
                continue
        enrich_cto_options(config)
        write_json(path, config, indent=4)
        enriched += 1
        print(f"[cto] enriched {path.stem}")
    return {"enriched": enriched, "skipped": skipped}
