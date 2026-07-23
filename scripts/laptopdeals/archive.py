from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .datafile import iter_products
from .ids import normalize_id
from .jsonio import read_json, write_json
from .pdp_fetcher import PDPFetcher
from .sources import lenovo
from .timeutil import ist_stamp


BAD_STATUS_PATTERNS = [
    r"\bout\s+of\s+stock\b",
    r"\bsold\s+out\b",
    r"\bunavailable\b",
    r"\bcurrently\s+not\s+available\b",
    r"\btemporarily\s+unavailable\b",
    r"\bavailable\s+soon\b",
    r"\bcoming\s+soon\b",
    r"\bend\s+of\s+life\b",
    r"\beol\b",
]


def archive_ids(archive: dict[str, Any]) -> set[str]:
    return {normalize_id(product.get("id")) for product in archive.get("products", []) if isinstance(product, dict)}


def is_archived(product: dict[str, Any]) -> bool:
    return bool(product.get("archived") or product.get("archived_at") or product.get("availability") == "out of stock")


def is_cto_product(pid: str) -> bool:
    """CTO (custom-to-order) products are configurable SKUs. Lenovo legitimately
    serves their PDPs as subseries/model-selector pages, so a model-selector page
    is their normal presentation and must not be treated as an archival signal."""
    return "CTO" in normalize_id(pid)


def bad_status(text: str) -> str:
    lower = lenovo.clean_text(text).lower()
    for pattern in BAD_STATUS_PATTERNS:
        match = re.search(pattern, lower)
        if match:
            return re.sub(r"[^a-z0-9]+", "_", match.group(0)).strip("_")
    return ""


def check_product(product: dict[str, Any], *, html_dir: Path | None = None) -> dict[str, Any]:
    pid = normalize_id(product.get("id"))
    url = str(product.get("store_link") or "")
    evidence = {"url": url, "status_code": 0}

    if not url:
        return {"archive": False, "reasons": [], "evidence": evidence}

    fetcher = PDPFetcher.get_instance()
    res = fetcher.fetch(url, pid)
    
    evidence["status_code"] = res.status_code
    reasons: list[str] = []
    
    if evidence["status_code"] in {404, 410}:
        reasons.append(f"http_{evidence['status_code']}")

    raw = res.raw_data or {}
    taxonomy_type = raw.get("taxonomy_type", "")
    page_type_name = raw.get("page_type_name", "")
    pdp_product_number = raw.get("pdp_product_number", "")
    ld_sku = raw.get("jsonld_sku", "")
    ld_mpn = raw.get("jsonld_mpn", "")
    meta_status = raw.get("meta_productstatus", "")
    
    evidence.update(
        {
            "taxonomy_type": taxonomy_type,
            "page_type_name": page_type_name,
            "pdp_product_number": pdp_product_number,
            "jsonld_sku": ld_sku,
            "jsonld_mpn": ld_mpn,
            "meta_productstatus": meta_status,
        }
    )
    availability = res.availability
    evidence["jsonld_availability"] = availability
    meta_bad = bad_status(meta_status)
    is_in_stock = (availability == "in stock") and not meta_bad

    # Model-selector detection: Only trigger if the product is NOT in stock.
    # If a product is in stock, resolving to a subseries/model-selector page is
    # standard presentation (especially for CTO models) and must not archive it.
    if not is_in_stock:
        if taxonomy_type.lower() == "subseriespage" or "subseries" in page_type_name.lower():
            reasons.append("converted_to_model_selector")
        if pdp_product_number and pdp_product_number.startswith("LEN"):
            reasons.append("converted_to_model_selector")
        if ld_sku and ld_sku != pid and ld_mpn == pid:
            reasons.append("converted_to_model_selector")

    if availability == "out of stock":
        reasons.append("not_in_stock")
    if meta_bad:
        reasons.append(f"bad_product_status:{meta_bad}")
    if not availability and not meta_status:
        status = bad_status(raw.get("text_snippet", ""))
        if status:
            reasons.append(f"bad_page_text:{status}")
    return {"archive": bool(reasons), "reasons": sorted(set(reasons)), "evidence": evidence}



def remove_from_raw_catalog(raw: dict[str, Any], ids_to_remove: set[str]) -> tuple[dict[str, Any], int]:
    groups = raw.get("groups")
    if not isinstance(groups, dict):
        return raw, 0
    removed = 0
    new_groups = {}
    for group, products in groups.items():
        if not isinstance(products, list):
            new_groups[group] = products
            continue
        kept = []
        for product in products:
            pid = normalize_id(product.get("id") or product.get("product_code") if isinstance(product, dict) else "")
            if pid in ids_to_remove:
                removed += 1
            else:
                kept.append(product)
        if kept:
            new_groups[group] = kept
    raw["groups"] = new_groups
    raw["total_products"] = sum(len(items) for items in new_groups.values() if isinstance(items, list))
    raw["generated_at"] = datetime.now(timezone.utc).isoformat()
    return raw, removed


def archive_unavailable(
    *,
    data_path: Path,
    raw_catalog_path: Path,
    archive_path: Path,
    ids: set[str] | None,
    limit: int | None,
    max_archive: int,
    html_dir: Path | None,
    apply: bool,
) -> dict[str, Any]:
    data = read_json(data_path, {})
    archive = read_json(archive_path, {"generated_at": "", "products": []})
    candidates = []
    already_archived = set()
    for _, product in iter_products(data):
        pid = normalize_id(product.get("id"))
        if not pid or (ids and pid not in ids):
            continue
        if is_archived(product):
            already_archived.add(pid)
            continue
        if "lenovo.com" not in str(product.get("store_link") or product.get("affiliate_link") or "").lower():
            continue
        candidates.append(product)
        if limit and len(candidates) >= limit:
            break

    decisions = []
    for product in candidates:
        pid = normalize_id(product.get("id"))
        try:
            decision = check_product(product, html_dir=html_dir)
        except Exception as exc:
            decision = {"archive": False, "reasons": [f"check_failed:{exc}"], "evidence": {}}
        decisions.append({"product": product, **decision})
        print(f"[archive] {pid} {'ARCHIVE' if decision['archive'] else 'keep'} {','.join(decision['reasons'])}")

    to_archive = [item for item in decisions if item["archive"]]
    if apply and len(to_archive) > max_archive:
        raise SystemExit(f"Abort: {len(to_archive)} exceeds --max-archive {max_archive}")

    if apply:
        now = ist_stamp()
        existing = archive_ids(archive)
        archive.setdefault("products", [])
        for item in to_archive:
            product = item["product"]
            pid = normalize_id(product.get("id"))
            product["archived"] = True
            product["archived_at"] = now
            product["archive_reason"] = item["reasons"]
            product["archive_evidence"] = item["evidence"]
            product["availability"] = "out of stock"
            if pid not in existing:
                archive["products"].append(deepcopy(product))
        archive["generated_at"] = now
        write_json(data_path, data, indent=4)
        write_json(archive_path, archive, indent=4)
    return {"checked": len(candidates), "archive": len(to_archive), "already_archived": len(already_archived), "applied": apply}


def restore_ids(*, data_path: Path, archive_path: Path, ids: set[str], apply: bool) -> dict[str, Any]:
    archive = read_json(archive_path, {"products": []})
    products = archive.get("products") if isinstance(archive, dict) else []
    kept = []
    restored = []
    for product in products if isinstance(products, list) else []:
        if isinstance(product, dict) and normalize_id(product.get("id")) in ids:
            restored.append(product)
        else:
            kept.append(product)
    if apply:
        archive["products"] = kept
        archive["generated_at"] = ist_stamp()
        write_json(archive_path, archive, indent=4)
        data = read_json(data_path, {})
        for _, product in iter_products(data):
            pid = normalize_id(product.get("id"))
            if pid in ids:
                product.pop("archived", None)
                product.pop("archived_at", None)
                product.pop("archive_reason", None)
                product.pop("archive_evidence", None)
                product["availability"] = "in stock"
        write_json(data_path, data, indent=4)
    return {"requested": len(ids), "restored": len(restored), "applied": apply}
