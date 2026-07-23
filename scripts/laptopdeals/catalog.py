from __future__ import annotations

import concurrent.futures
import json
import re
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .datafile import product_index
from .history import load_history, latest_price, stats
from .ids import normalize_id
from .jsonio import read_json, write_json
from .sources import lenovo
from .specs import clean_text, parse_spec_codes
from .timeutil import iso_date


LIVE_FIELDS = ("price", "mrp", "availability", "price_mean", "price_median", "price_usual", "has_history", "last_checked")
TECHNICAL_NAME_RE = re.compile(r"^(?:LEN[0-9A-Z]+|[0-9A-Z]{8,})$")
MODEL_CODE_RE = re.compile(r"\b\d{1,3}[A-Z]{2,}\d{0,4}\b")
DESCRIPTIVE_WORDS_RE = re.compile(r"\b(?:Gen|Intel|AMD|Ryzen|Core|Ultra|Snapdragon|cm|inch|display|backlit|fingerprint|webcam|privacy)\b", re.IGNORECASE)
KNOWN_BRAND_RE = re.compile(r"\b(?:Lenovo|LOQ|Legion|ThinkPad|IdeaPad|Yoga|ThinkBook|Flex|Slim|Pro)\b", re.IGNORECASE)
GENERIC_TAGLINE_RE = re.compile(
    r"\b(?:laptop|pc|computer)\s+for\b|\bfor\s+(?:students|gamers|enthusiasts|professionals)\b|\bAI-tuned\s+gaming\b|^\d{1,2}(?:\.\d)?\s*[- ]*(?:inch|\")?\s*laptop\b",
    re.IGNORECASE,
)
RAW_INTERNAL_PREFIX_RE = re.compile(r"^(?:NB|LOIS|MNL)\s+", re.IGNORECASE)


def _normalize_psref_specs(specs: dict[str, Any]) -> dict[str, Any]:
    """Map PSREF detailed specs to frontend-displayable shape."""
    out: dict[str, Any] = {}
    proc = specs.get("processor") or {}
    if proc:
        out["processor"] = {
            "brand": proc.get("brand", ""),
            "model": proc.get("model", ""),
            "cores": proc.get("cores"),
            "threads": proc.get("threads"),
            "base_clock": f"{proc['base_clock_ghz']} GHz" if proc.get("base_clock_ghz") else "",
            "boost_clock": f"{proc['boost_clock_ghz']} GHz" if proc.get("boost_clock_ghz") else "",
        }
    gpu = specs.get("graphics") or {}
    if gpu:
        out["graphics"] = {
            "model": gpu.get("model", ""),
            "vram": f"{gpu['vram_gb']} GB" if gpu.get("vram_gb") else ("Shared" if not gpu.get("dedicated") else ""),
            "dedicated": gpu.get("dedicated", False),
            "boost_clock": f"{gpu['boost_clock_mhz']} MHz" if gpu.get("boost_clock_mhz") else "",
            "tgp": f"{gpu['tgp_w']}W" if gpu.get("tgp_w") else "",
            "ai_tops": gpu.get("ai_tops"),
        }
    mem = specs.get("memory") or {}
    if mem:
        out["memory"] = {
            "amount": mem.get("amount", ""),
            "type": mem.get("type", ""),
            "speed": mem.get("speed", ""),
            "slots_used": mem.get("slots_populated"),
            "soldered": mem.get("soldered", False),
        }
    sto = specs.get("storage") or {}
    if sto:
        out["storage"] = {"capacity": sto.get("capacity", ""), "type": sto.get("type", "")}
    dpy = specs.get("display") or {}
    if dpy:
        out["display"] = {
            "size": dpy.get("size", ""),
            "resolution": dpy.get("resolution_name", "") or dpy.get("resolution", ""),
            "type": dpy.get("type", ""),
            "refresh": dpy.get("refresh", ""),
            "brightness": dpy.get("brightness", ""),
            "color": dpy.get("color", ""),
            "touch": dpy.get("touch", ""),
            "surface": dpy.get("surface", ""),
        }
    net = specs.get("network") or {}
    if net:
        out["network"] = {"wifi": net.get("wifi", ""), "bluetooth": net.get("bluetooth", "")}
    pwr = specs.get("power") or {}
    if pwr:
        out["power"] = {"adapter": pwr.get("adapter", ""), "watt": pwr.get("watt")}
    batt = specs.get("battery") or {}
    if batt:
        out["battery"] = {"capacity": f"{batt['capacity_wh']}Wh" if batt.get("capacity_wh") else batt.get("raw", "")}
    ports = specs.get("ports") or {}
    if ports.get("items"):
        out["ports"] = {"items": ports["items"]}
    cam = specs.get("camera") or {}
    if cam.get("raw"):
        out["camera"] = {"model": cam["raw"]}
    kb = specs.get("keyboard") or {}
    if kb.get("raw"):
        out["keyboard"] = {"type": kb["raw"]}
    dim = specs.get("dimensions") or {}
    if dim:
        out["dimensions"] = {"size": dim.get("raw", ""), "weight": dim.get("weight", "")}
    build = specs.get("build") or {}
    if build:
        out["build"] = {k: v for k, v in build.items() if v}
    sw = specs.get("software") or {}
    if sw:
        out["software"] = {k: v for k, v in sw.items() if v}
    audio = specs.get("audio") or {}
    if audio:
        out["audio"] = {k: v for k, v in audio.items() if v}
    ms = specs.get("memory_slots") or {}
    if ms:
        out["memory_slots"] = {k: v for k, v in ms.items() if v}
    ss = specs.get("storage_slots") or {}
    if ss:
        out["storage_slots"] = {k: v for k, v in ss.items() if v}
    return out


def existing_ids_from(paths: list[Path]) -> set[str]:
    ids: set[str] = set()
    for path in paths:
        payload = read_json(path, {})
        ids.update(product_index(payload).keys())
    return ids


def is_display_name(value: Any, sku: str = "") -> bool:
    text = lenovo.clean_text(value)
    if not text:
        return False
    upper = text.upper()
    if sku and upper == sku.upper():
        return False
    if TECHNICAL_NAME_RE.fullmatch(upper) and any(char.isdigit() for char in upper):
        return False
    if "_" in text:
        return False
    if text.lower() in {"home", "laptops"}:
        return False
    if re.search(r"\b(series|laptops?)\b", text, flags=re.IGNORECASE) and not re.search(r"\bgen\b|\d", text, flags=re.IGNORECASE):
        return False
    if GENERIC_TAGLINE_RE.search(text):
        return False
    if RAW_INTERNAL_PREFIX_RE.search(text):
        return False
    if MODEL_CODE_RE.search(upper) and not DESCRIPTIVE_WORDS_RE.search(text) and not KNOWN_BRAND_RE.search(text):
        return False
    if _is_doubled_text(text):
        return False
    return any(char.isalpha() for char in text)


def _score_title(t: str) -> int:
    if not t:
        return 0
    score = len(t)
    if "cms" in t or "inch" in t.lower() or "cm" in t:
        score += 50
    if "gen" in t.lower():
        score += 50
    if "intel" in t.lower() or "amd" in t.lower() or "ryzen" in t.lower():
        score += 30
    return score


def _is_doubled_text(text: str) -> bool:
    t = text.strip()
    mid = len(t) // 2
    if mid < 4:
        return False
    if len(t) % 2 == 0 and t[:mid] == t[mid:]:
        return True
    if mid + 1 < len(t) and t[:mid] == t[mid + 1:]:
        return True
    return False


def clean_page_title(value: str, sku: str) -> str:
    title = lenovo.clean_text(value)
    if not title:
        return ""
    parts = [part.strip() for part in title.split("|") if part.strip()]
    for part in parts:
        if sku and part.upper() == sku.upper():
            continue
        if part.lower() == "lenovo india":
            continue
        if is_display_name(part, sku):
            return part
    return title


def collapse_repeated_name(value: Any) -> str:
    text = lenovo.clean_text(value)
    if not text:
        return ""
    midpoint = len(text) // 2
    if len(text) % 2 == 0 and text[:midpoint] == text[midpoint:]:
        return text[:midpoint]
    for size in range(midpoint, 7, -1):
        if text[:size] == text[size : size * 2]:
            return text[:size]
    return text


def breadcrumb_model_candidates(breadcrumb: list[dict[str, str]]) -> list[str]:
    names = [collapse_repeated_name(item.get("name")) for item in breadcrumb]
    return [name for name in reversed(names[2:]) if is_display_name(name)]


def select_title(
    *,
    product_ld: dict[str, Any],
    breadcrumb: list[dict[str, str]],
    page_title: str,
    card: dict[str, Any],
    sku: str,
) -> str:
    # Priority order:
    # 1. card.summary — the listing API's curated marketing name, e.g.
    #    "Lenovo LOQ 14th Gen (15, Intel)" or "Legion 5 Gen 10, 38.86cms - AMD R7".
    #    This is the most consumer-friendly and consistent name source.
    # 2. Breadcrumb model segments from the PDP (deepest-first), e.g. "LOQ 15IRX9".
    # 3. Page <title> tag from the PDP, stripped of "| Lenovo India".
    # 4. Product JSON-LD "name" from the PDP.
    # 5. card.productName — often a technical internal string.
    candidates = [
        lenovo.clean_text(card.get("summary")),
        *breadcrumb_model_candidates(breadcrumb),
        clean_page_title(page_title, sku),
        collapse_repeated_name(product_ld.get("name")) if isinstance(product_ld, dict) else "",
        lenovo.clean_text(card.get("productName")),
    ]
    for candidate in candidates:
        if is_display_name(candidate, sku):
            return candidate
    return sku


def path_from_breadcrumb(breadcrumb: list[dict[str, str]], title: str, sku: str, fallback_series: str) -> list[str]:
    parts = [collapse_repeated_name(item.get("name")) for item in breadcrumb if lenovo.clean_text(item.get("name"))]
    if not parts:
        parts = ["Home", "Laptops", fallback_series]
    while len(parts) > 3 and not is_display_name(parts[-1], sku):
        parts.pop()
    if title and title.lower() not in {part.lower() for part in parts[-2:]}:
        parts.append(title)
    if parts and parts[-1].lower() == sku.lower() and title:
        parts[-1] = title
    if parts and parts[0].lower() != "home":
        parts.insert(0, "Home")
    return parts


def scrape_catalog(
    *,
    series: list[str],
    output: Path,
    only_new: bool,
    existing_files: list[Path],
    new_ids_output: Path | None,
    limit_per_series: int | None,
    delay: tuple[float, float],
    workers: int,
    verbose: bool,
    ids: set[str] | None = None,
) -> dict[str, Any]:
    client = lenovo.LenovoCatalogClient(delay=delay, verbose=verbose)
    target_ids = {i.upper() for i in ids} if ids else set()
    known = existing_ids_from(existing_files) if (only_new and not target_ids) else set()
    output_existing = read_json(output, {}) if (only_new or target_ids) and output.exists() else {}
    products: list[dict[str, Any]] = []
    source_urls = {item: lenovo.get_results_url(item) for item in series}
    known_lock = threading.Lock()
    failed_series: list[str] = []

    def build_product(series_name: str, card: dict[str, Any]) -> OrderedDict[str, Any] | None:
        sku = lenovo.clean_text(card.get("productCode") or card.get("productNumber"))
        if not sku or lenovo.GROUP_CODE_RE.match(sku):
            return None
        sku_upper = sku.upper()
        if target_ids and sku_upper not in target_ids:
            return None

        with known_lock:
            if sku_upper in known and not target_ids:
                return None

        store_link = lenovo.absolute_url(card.get("url"))
        breadcrumb: list[dict[str, str]] = []
        product_ld: dict[str, Any] = {}
        page_title = ""
        detail_specs = ([], {}, {})
        detail_success = False

        if store_link:
            try:
                detail = client.detail(store_link)
                if len(detail) == 4:
                    breadcrumb, product_ld, detail_specs, page_title = detail
                else:
                    breadcrumb, product_ld, detail_specs = detail
                detail_success = True
            except Exception as exc:
                print(f"[detail-failed] {sku}: {exc}")

        # Verification for new models: visit PDP and check if actually real before committing
        offers = product_ld.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}

        raw_price = card.get("finalPrice") or offers.get("price")
        try:
            price_val = float(re.sub(r"[^\d.]", "", str(raw_price or "0")))
        except ValueError:
            price_val = 0.0

        ld_sku = lenovo.clean_text(product_ld.get("sku")).upper() if isinstance(product_ld, dict) else ""

        # Reject newly discovered items if PDP is unreachable, price is zero, or PDP is a model selector
        if not detail_success:
            print(f"[new-product-rejected] Discarding {sku} — PDP detail page fetch failed")
            return None
        if price_val <= 0:
            print(f"[new-product-rejected] Discarding {sku} — invalid/zero cost price: {raw_price}")
            return None
        if ld_sku and lenovo.GROUP_CODE_RE.match(ld_sku):
            print(f"[new-product-rejected] Discarding {sku} — PDP refers to model selector group code {ld_sku}")
            return None

        with known_lock:
            known.add(sku_upper)

        listing_specs = lenovo.spec_list_to_maps(card.get("classification") or [])
        spec_rows, by_label, by_code = lenovo.merge_specs(listing_specs, detail_specs)
        title = select_title(product_ld=product_ld, breadcrumb=breadcrumb, page_title=page_title, card=card, sku=sku)
        path_parts = path_from_breadcrumb(breadcrumb, title, sku, series_name)
        images = product_ld.get("image") or []
        if isinstance(images, str):
            images = [images]
        if not images:
            gallery = (card.get("media") or {}).get("gallery") or []
            images = [lenovo.absolute_url(item.get("imageAddress")) for item in gallery if item.get("imageAddress")]
        product = OrderedDict(
            [
                ("id", sku),
                ("product_code", sku),
                ("series_filter", series_name),
                ("title", title),
                ("summary", lenovo.clean_text(card.get("summary"))),
                ("product_name", lenovo.clean_text(card.get("productName"))),
                ("listing_date", lenovo.listing_date_from_images(images)),
                ("store_link", store_link),
                ("breadcrumb", path_parts),
                ("breadcrumb_path", " > ".join(path_parts)),
                ("listing_category_path", card.get("categoryPath") or []),
                ("price", card.get("finalPrice") or offers.get("price")),
                ("mrp", card.get("webPrice")),
                ("currency", card.get("currencyCode") or offers.get("priceCurrency")),
                ("availability", lenovo.normalize_availability(card.get("marketingStatus") or offers.get("availability"))),
                ("rating", card.get("ratingStar")),
                ("review_count", card.get("commentCount") or card.get("reviewCount")),
                ("coupon_code", card.get("couponCode")),
                ("images", [lenovo.absolute_url(image) for image in images if image]),
                ("specs", spec_rows),
                ("specs_by_label", by_label),
                ("specs_by_code", by_code),
                ("tech_specs", lenovo.build_tech_specs(by_label, by_code)),
            ]
        )
        if verbose:
            print(f"[catalog] {series_name} {sku}")
        return product

    for series_name in series:
        try:
            cards = client.listing_products(series_name, limit=limit_per_series)
            source_urls[series_name] = getattr(client, "result_urls", {}).get(series_name, source_urls[series_name])
        except Exception as exc:
            failed_series.append(series_name)
            print(f"[catalog-series-failed] {series_name}: {exc}")
            if not only_new:
                raise
            continue
        if workers <= 1:
            built = [build_product(series_name, card) for card in cards]
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(build_product, series_name, card) for card in cards]
                built = [future.result() for future in concurrent.futures.as_completed(futures)]
        for product in built:
            if product is not None:
                products.append(product)

    merge_mode = only_new or bool(target_ids)
    if merge_mode and isinstance(output_existing, dict) and isinstance(output_existing.get("groups"), dict):
        existing_map: dict[str, dict[str, Any]] = {}
        for group_path, items in output_existing["groups"].items():
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and item.get("id"):
                        existing_map[item["id"].upper()] = item
        for product in products:
            pid = product.get("id")
            if pid:
                existing_map[pid.upper()] = product
        grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        for product in existing_map.values():
            grouped.setdefault(product.get("breadcrumb_path") or "Uncategorized", []).append(product)
    else:
        grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        for product in products:
            grouped.setdefault(product.get("breadcrumb_path") or "Uncategorized", []).append(product)

    payload = OrderedDict(
        [
            ("generated_at", datetime.now(timezone.utc).isoformat()),
            ("source_urls", source_urls),
            ("total_products", sum(len(rows) for rows in grouped.values())),
            ("groups", grouped),
        ]
    )
    write_json(output, payload, indent=4)
    if new_ids_output:
        actual_existing = existing_ids_from(existing_files)
        new_ids = [
            normalize_id(item.get("id"))
            for item in products
            if normalize_id(item.get("id")) and normalize_id(item.get("id")) not in actual_existing
        ]
        write_json(new_ids_output, new_ids, indent=2)
    return {"products": len(products), "total": payload["total_products"], "failed_series": failed_series}


def category_from_product(product: dict[str, Any]) -> str:
    combined = " ".join(str(x).lower() for x in [product.get("breadcrumb_path"), product.get("series_filter")])
    if "ideapad" in combined:
        return "Ideapad"
    if "legion" in combined:
        return "Legion Laptops"
    if "loq" in combined:
        return "Lenovo LOQ Laptops"
    if "thinkpad" in combined:
        return "ThinkPad"
    if "thinkbook" in combined:
        return "Thinkbook"
    if "yoga" in combined:
        return "Yoga"
    return "Other"


def model_name(title: str) -> str:
    parts = [part.strip() for part in str(title or "").split(",")]
    return ", ".join(parts[:2]).split(" - ")[0].strip() or title or "Unknown Model"


def breadcrumb_from_product(product: dict[str, Any]) -> list[dict[str, str]]:
    breadcrumb = product.get("breadcrumb")
    if isinstance(breadcrumb, list):
        rows = []
        for item in breadcrumb:
            if isinstance(item, dict):
                rows.append({"name": lenovo.clean_text(item.get("name"))})
            elif isinstance(item, str):
                rows.append({"name": lenovo.clean_text(item)})
        return rows
    path = lenovo.clean_text(product.get("breadcrumb_path"))
    return [{"name": part.strip()} for part in path.split(">") if part.strip()]


def format_catalog(
    *,
    input_path: Path,
    output_path: Path,
    history_dir: Path,
    cto_dir: Path,
    existing_data: Path | None,
    dry_run: bool = False,
    psref_dir: Path | None = None,
    psref_map: Path | None = None,
) -> dict[str, Any]:
    raw = read_json(input_path, {})
    existing = product_index(read_json(existing_data or output_path, {}))
    groups = raw.get("groups") if isinstance(raw, dict) else {}
    psref_sku_map: dict[str, Any] = {}
    if psref_map and psref_map.exists():
        psref_sku_map = read_json(psref_map, {})
    formatted: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    psref_applied = 0
    count = 0
    for _, rows in (groups or {}).items():
        if not isinstance(rows, list):
            continue
        for product in rows:
            if not isinstance(product, dict):
                continue
            sku = normalize_id(product.get("id") or product.get("product_code"))
            specs = parse_spec_codes(product.get("specs_by_code") or {})
            spec_source = "lenovo_india"
            psref_meta: dict[str, Any] | None = None
            if sku and sku in psref_sku_map:
                psref_entry = psref_sku_map[sku]
                if psref_entry.get("status") == "resolved" and psref_entry.get("tech_specs"):
                    specs = _normalize_psref_specs(psref_entry["tech_specs"])
                    spec_source = "psref"
                    psref_applied += 1
                    psref_meta = {k: v for k, v in psref_entry.items() if k not in ("tech_specs", "raw_psref", "diagnostics")}
            hist = load_history(history_dir, sku) if sku else []
            hist_stats = stats(hist)
            current = latest_price(hist)
            existing_product = existing.get(sku, {}) if sku else {}
            title = select_title(product_ld={"name": product.get("title")}, breadcrumb=[], page_title="", card=product, sku=sku)
            if not is_display_name(product.get("title"), sku):
                existing_title = clean_text(existing_product.get("title") or existing_product.get("model_name"))
                if is_display_name(existing_title, sku):
                    title = existing_title
            full_category = " > ".join(path_from_breadcrumb(breadcrumb_from_product(product), title, sku, category_from_product(product)))
            store_link = product.get("store_link", "")
            affiliate = ""
            if store_link and sku:
                affiliate = f"https://lenovo-in.zlvv.net/c/5890822/608695/9634?prodsku={sku}&u={quote(store_link, safe='')}&intsrc=CATF_4639"
            row = {
                "id": sku,
                "model_name": model_name(title),
                "internal_model_code": title,
                "title": title,
                "description": product.get("summary", ""),
                "availability": product.get("availability", "unknown"),
                "price": f"{current}.00 INR" if current else 0,
                "mrp": 0,
                "image": (product.get("images") or [""])[0],
                "affiliate_link": affiliate,
                "store_link": store_link,
                "full_category": full_category,
                "listing_category_path": product.get("listing_category_path", []),
                "listing_date": iso_date(product.get("listing_date")) or lenovo.listing_date_from_images(product.get("images")),
                "tech_specs": specs,
                "spec_source": spec_source,
                "data_status": "Enriched",
                **hist_stats,
            }
            if psref_meta:
                row["psref"] = psref_meta
            if sku in existing:
                for field in LIVE_FIELDS:
                    if field in existing[sku]:
                        row[field] = existing[sku][field]
            cto_path = cto_dir / f"{sku}.json"
            if "CTO" in sku and cto_path.exists():
                row["cto_options"] = {key: value for key, value in read_json(cto_path, {}).items() if key != "lastFetched"}
            category = category_from_product(product)
            formatted.setdefault(category, []).append(row)
            count += 1

    # Preserve all existing products from existing data that weren't re-scraped
    processed_ids = {normalize_id(row.get("id")) for rows in formatted.values() for row in rows}
    for sku, existing_product in existing.items():
        if sku in processed_ids:
            continue
        cat = "Uncategorized"
        fc = existing_product.get("full_category", "")
        parts = [x.strip() for x in fc.split(">")]
        if len(parts) >= 3:
            cat = parts[2]
        row = dict(existing_product)
        # Apply PSREF specs if available
        if sku in psref_sku_map:
            psref_entry = psref_sku_map[sku]
            if psref_entry.get("status") == "resolved" and psref_entry.get("tech_specs"):
                row["tech_specs"] = _normalize_psref_specs(psref_entry["tech_specs"])
                row["spec_source"] = "psref"
                psref_applied += 1
                row["psref"] = {k: v for k, v in psref_entry.items() if k not in ("tech_specs", "raw_psref", "diagnostics")}
        formatted.setdefault(cat, []).append(row)
        count += 1

    # Post-process: normalize titles by PSREF product_key group so products
    # sharing the same model series get a consistent consumer-friendly name.
    pk_best_title: dict[str, str] = {}
    for rows in formatted.values():
        for row in rows:
            sku = row.get("id", "")
            entry = psref_sku_map.get(sku, {})
            pk = entry.get("product_key", "")
            if not pk:
                continue
            title = row.get("title", "")
            if is_display_name(title, sku):
                existing = pk_best_title.get(pk, "")
                if not existing or _score_title(title) > _score_title(existing):
                    pk_best_title[pk] = title
    for rows in formatted.values():
        for row in rows:
            sku = row.get("id", "")
            entry = psref_sku_map.get(sku, {})
            pk = entry.get("product_key", "")
            if not pk or pk not in pk_best_title:
                continue
            best = pk_best_title[pk]
            title = row.get("title", "")
            if _score_title(best) > _score_title(title):
                row["title"] = best
                row["model_name"] = model_name(best)

    if not dry_run:
        write_json(output_path, formatted, indent=4)
    return {"formatted": count, "categories": len(formatted), "psref_applied": psref_applied}
