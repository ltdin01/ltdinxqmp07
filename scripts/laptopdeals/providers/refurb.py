from __future__ import annotations

import re
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from laptopdeals.datafile import iter_products, product_index
from laptopdeals.enrich_lenovo import PsrefIndex, enrich_lenovo_row
from laptopdeals.history import latest_price, load_history, stats, write_history
from laptopdeals.ids import normalize_id
from laptopdeals.jsonio import read_json, write_json
from laptopdeals.normalize_hardware import build_inventory_indices, normalize_product
from laptopdeals.paths import (
    APP_DATA_REFURB,
    ARCHIVE_REFURB,
    PRICE_HISTORY_REFURB,
    PSREF_DATASHEETS_DIR,
    PSREF_DIR,
)
from laptopdeals.providers import register
from laptopdeals.sources import lenovo as base_lenovo
from laptopdeals.sources import lenovo_outlet
from laptopdeals.specs import parse_spec_codes
from laptopdeals.timeutil import iso_date


def category_from_product(product: dict[str, Any]) -> str:
    url = str(product.get("url") or product.get("store_link") or "").lower()
    title = str(product.get("title") or product.get("summary") or "").lower()
    series_name = str(product.get("series_filter") or "").lower()
    combined = f"{url} {title} {series_name}"

    if "ideapad" in combined:
        return "Ideapad"
    if "legion" in combined:
        return "Legion Laptops"
    if "loq" in combined:
        return "Lenovo LOQ Laptops"
    if "thinkpad" in combined:
        return "ThinkPad"
    if "thinkbook" in combined or "motobook" in combined:
        return "Thinkbook"
    if "yoga" in combined:
        return "Yoga"
    if "v-series" in combined or "lenovo-v" in combined or re.search(r"\bv\d{2}\b", combined):
        return "Lenovo V-series"
    return "Other"


KNOWN_CASING = {
    "thinkpad": "ThinkPad",
    "ideapad": "IdeaPad",
    "thinkbook": "ThinkBook",
    "loq": "LOQ",
    "legion": "Legion",
    "yoga": "Yoga",
    "motobook": "Motobook",
    "intel": "Intel",
    "amd": "AMD",
    "snapdragon": "Snapdragon",
    "qualcomm": "Qualcomm",
    "vpro": "vPro",
    "gen": "Gen",
    "aura": "Aura",
    "edition": "Edition",
    "carbon": "Carbon",
    "pro": "Pro",
    "slim": "Slim",
    "flex": "Flex",
    "book": "Book",
    "extreme": "Extreme",
    "lenovo": "Lenovo",
    "gaming": "Gaming",
}


def format_word(w: str) -> str:
    low = w.lower()
    if low in KNOWN_CASING:
        return KNOWN_CASING[low]
    if re.match(r"^\d+[ix]$", low):
        return low
    m = re.match(r"^([a-z])(\d+)([a-z]*)$", low)
    if m:
        return f"{m.group(1).upper()}{m.group(2)}{m.group(3).lower()}"
    if re.match(r"^\d+[a-z]+$", low):
        return low.upper()
    return w.capitalize()


def title_from_url_slug(url: str) -> str:
    if not url:
        return ""
    parts = [p for p in url.strip("/").split("/") if p]
    if len(parts) >= 2:
        slug = parts[-2]
        if re.match(r"^\d+[a-z0-9]+$", slug, re.I) and not re.search(r"(?:ideapad|thinkpad|thinkbook|loq|legion|yoga|v\d+)", slug, re.I):
            return ""
        clean_slug = re.sub(r"\([^\)]*\)", "", slug)
        clean_slug = re.sub(r"[^a-zA-Z0-9\-]+", "-", clean_slug)
        tokens = [t for t in clean_slug.split("-") if t and t.lower() not in ("inch", "mobile", "workstation", "laptop", "laptops", "pdp", "hero")]
        gen = ""
        filtered_tokens = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            low_tok = tok.lower()
            if low_tok == "gen" and i + 1 < len(tokens) and tokens[i + 1].isdigit():
                gen = f"Gen {tokens[i + 1]}"
                i += 2
                continue
            elif low_tok in ("intel", "amd", "snapdragon"):
                i += 1
                continue
            elif low_tok in ("13", "14", "15", "16", "17") and not (i > 0 and tokens[i - 1].lower() in ("thinkbook", "gaming", "motobook", "loq", "legion", "v", "slim", "pro", "flex", "book", "plus")):
                i += 1
                continue
            elif low_tok == "lenovo" and i + 1 < len(tokens) and tokens[i + 1].lower() in ("thinkpad", "ideapad", "thinkbook", "legion", "yoga", "loq", "v14", "v15"):
                i += 1
                continue
            elif low_tok == "gaming" and any(t.lower() in ("legion", "loq") for t in filtered_tokens):
                i += 1
                continue
            elif low_tok == "2" and i + 2 < len(tokens) and tokens[i + 1].lower() in ("in", "in1") and tokens[i + 2] == "1":
                filtered_tokens.append("2-in-1")
                i += 3
                continue
            filtered_tokens.append(format_word(tok))
            i += 1

        main_title = " ".join(filtered_tokens)
        if gen:
            main_title = f"{main_title} {gen}"
        return main_title
    return ""


def format_refurb_title(
    *,
    sku: str,
    raw_title: str,
    store_link: str,
    datasheet: dict[str, Any] | None,
    specs: dict[str, Any],
) -> str:
    psref_mkt = datasheet.get("marketing_name") if datasheet else ""
    psref_prod = datasheet.get("product_name") if datasheet else ""
    slug_title = title_from_url_slug(store_link)

    # 1. Platform (AMD / Intel / Snapdragon)
    platform = ""
    proc_val = specs.get("processor") or {}
    proc_str = proc_val if isinstance(proc_val, str) else proc_val.get("brand", "")
    source_text = f"{store_link} {raw_title} {psref_mkt} {psref_prod} {slug_title} {proc_str}".lower()
    if "amd" in source_text or re.search(r"\b(?:amd|ryzen)\b", source_text):
        platform = "AMD"
    elif "intel" in source_text or re.search(r"\b(?:intel|core|ultra)\b", source_text):
        platform = "Intel"
    elif "snapdragon" in source_text or re.search(r"\b(?:snapdragon|qualcomm|x\s*elite|x\s*plus)\b", source_text):
        platform = "Snapdragon"

    # 2. Screen size
    screen = ""
    dpy_val = specs.get("display") or {}
    dpy_size = dpy_val if isinstance(dpy_val, str) else dpy_val.get("size", "")
    m_screen = re.search(r"(\d+(?:\.\d+)?)", str(dpy_size)) or re.search(r"\b(13(?:\.3|\.5)?|14(?:\.5)?|15(?:\.6)?|16|17(?:\.3)?)\b", source_text)
    if m_screen:
        s_val = float(m_screen.group(1))
        screen = "14" if 13.8 <= s_val <= 14.5 else ("15" if 15.0 <= s_val <= 15.8 else ("16" if 15.9 <= s_val <= 16.5 else ("13" if s_val < 13.8 else str(int(s_val)))))

    if slug_title:
        details = []
        if screen:
            details.append(screen)
        if platform:
            details.append(platform)
        if details:
            return f"{slug_title} ({', '.join(details)})"
        return slug_title

    if psref_mkt:
        return psref_mkt
    return clean_refurb_title(raw_title, sku) or sku


def clean_refurb_title(title: str, sku: str) -> str:
    clean = base_lenovo.clean_text(title)
    if not clean or clean.upper() == sku.upper():
        return ""
    if re.match(r"^\d+[a-z0-9]+$", clean, re.I) and not re.search(r"(?:ideapad|thinkpad|thinkbook|motobook|loq|legion|yoga|v\d+)", clean, re.I):
        return ""
    # Strip any trailing " | Lenovo India"
    parts = [p.strip() for p in clean.split("|") if p.strip()]
    for part in parts:
        if part.upper() != sku.upper() and part.lower() != "lenovo india":
            return part
    return clean


def model_name_from_title(title: str) -> str:
    if not title:
        return ""
    if " - " in title:
        return title.split(" - ")[0].strip()
    return title.strip()


def scrape_catalog(
    *,
    output: Path,
    limit: int | None = None,
    delay: tuple[float, float] = (0.5, 1.5),
    existing_files: list[Path] | None = None,
    new_ids_output: Path | None = None,
    verbose: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    client = lenovo_outlet.LenovoOutletClient(delay=delay, verbose=verbose)
    if verbose:
        print("[refurb] Fetching all laptop listings from Lenovo Outlet DLP API...")

    cards = client.fetch_all_laptops(limit=limit)
    skus = [lenovo_outlet.base_lenovo.clean_text(c.get("productCode")) for c in cards if c.get("productCode")]
    
    known_ids: set[str] = set()
    if existing_files:
        for ef in existing_files:
            if ef.exists():
                for _, p in iter_products(read_json(ef, {})):
                    pid = normalize_id(p.get("id") or p.get("product_code"))
                    if pid:
                        known_ids.add(pid)
    new_ids = sorted(sku for sku in skus if sku and normalize_id(sku) not in known_ids)
    if new_ids_output:
        write_json(new_ids_output, new_ids, indent=4)
        if verbose:
            print(f"[refurb] Wrote {len(new_ids)} new IDs to {new_ids_output}")

    if verbose:
        print(f"[refurb] Pre-fetching batch inventory for {len(skus)} SKUs...")
    batch_inventory = client.fetch_batch_inventory(skus)

    if verbose:
        print(f"[refurb] Pre-fetching batch prices for {len(skus)} SKUs...")
    batch_prices = client.fetch_batch_prices(skus)

    grouped: dict[str, list[dict[str, Any]]] = OrderedDict()
    total_count = 0

    for card in cards:
        sku = lenovo_outlet.base_lenovo.clean_text(card.get("productCode"))
        if not sku:
            continue

        store_link = lenovo_outlet.absolute_outlet_url(card.get("url") or f"/p/{sku.lower()}")
        affiliate_link = lenovo_outlet.build_affiliate_link(store_link, sku)
        
        # Inventory availability
        availability = batch_inventory.get(sku)
        if not availability:
            availability = client.get_availability(sku, store_link, preloaded_batch=batch_inventory)
        if availability == "unknown":
            # Check card purchaseFlag
            availability = "in stock" if card.get("purchaseFlag") else "out of stock"

        # Price & MRP from batch prices, with fallback to card fields
        price_pair = batch_prices.get(sku)
        price_val, mrp_val = price_pair if price_pair else (None, None)
        if price_val is None:
            raw_final = str(card.get("finalPrice") or "0")
            try:
                price_val = int(float(re.sub(r"[^\d.]", "", raw_final)))
            except ValueError:
                price_val = None
        if mrp_val is None:
            raw_mrp = str(card.get("webPrice") or card.get("marketingPrice") or "0")
            try:
                mrp_val = int(float(re.sub(r"[^\d.]", "", raw_mrp)))
            except ValueError:
                mrp_val = None

        # Images
        images: list[str] = []
        media = card.get("media") or {}
        if media.get("heroImage", {}).get("imageAddress"):
            images.append(lenovo_outlet.absolute_outlet_url(media["heroImage"]["imageAddress"]))
        for g in media.get("gallery") or []:
            addr = g.get("imageAddress")
            if addr:
                full_addr = lenovo_outlet.absolute_outlet_url(addr)
                if full_addr not in images:
                    images.append(full_addr)
        if not images and media.get("thumbnail", {}).get("imageAddress"):
            images.append(lenovo_outlet.absolute_outlet_url(media["thumbnail"]["imageAddress"]))

        # Specs
        classification = card.get("classification") or []
        spec_rows, by_label, by_code = lenovo_outlet.classification_to_spec_maps(classification)
        tech_specs = base_lenovo.build_tech_specs(by_label, by_code)

        slug_title = title_from_url_slug(card.get("url") or "")
        title = slug_title or clean_refurb_title(card.get("summary") or card.get("productName") or sku, sku)
        category = category_from_product(card)

        product = OrderedDict(
            [
                ("id", sku),
                ("product_code", sku),
                ("title", title),
                ("model_name", model_name_from_title(title)),
                ("summary", base_lenovo.clean_text(card.get("summary"))),
                ("product_name", base_lenovo.clean_text(card.get("productName"))),
                ("card_summary", base_lenovo.clean_text(card.get("cardSummary"))),
                ("product_condition", card.get("productCondition") or "CERTIFIED REFURBISHED"),
                ("store_link", store_link),
                ("affiliate_link", affiliate_link),
                ("listing_date", base_lenovo.listing_date_from_images(images)),
                ("price", price_val),
                ("mrp", mrp_val),
                ("currency", card.get("currencyCode") or "INR"),
                ("availability", availability),
                ("rating", card.get("ratingStar")),
                ("review_count", card.get("commentCount")),
                ("images", images),
                ("specs", spec_rows),
                ("specs_by_label", by_label),
                ("specs_by_code", by_code),
                ("tech_specs", tech_specs),
                ("vendor", "lenovo"),
                ("brand", "Lenovo"),
                ("series", category),
            ]
        )

        grouped.setdefault(category, []).append(product)
        total_count += 1

    in_stock_cnt = sum(1 for rows in grouped.values() for p in rows if p.get("availability") == "in stock")
    oos_cnt = sum(1 for rows in grouped.values() for p in rows if p.get("availability") != "in stock")
    payload = OrderedDict(
        [
            ("generated_at", datetime.now(timezone.utc).isoformat()),
            ("source", "Lenovo Outlet India DLP API"),
            ("total_products", total_count),
            ("in_stock_count", in_stock_cnt),
            ("out_of_stock_count", oos_cnt),
            ("groups", grouped),
        ]
    )
    write_json(output, payload, indent=4)
    if verbose:
        print(f"[refurb] Saved raw catalog to {output} ({in_stock_cnt} in stock, {oos_cnt} out of stock across {len(grouped)} categories)")
    return {"total": total_count, "in_stock": in_stock_cnt, "out_of_stock": oos_cnt, "categories": len(grouped)}


def format_catalog(
    *,
    input_path: Path,
    output_path: Path = APP_DATA_REFURB,
    archive_path: Path = ARCHIVE_REFURB,
    history_dir: Path = PRICE_HISTORY_REFURB,
    existing_data: Path | None = None,
    dry_run: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    raw = read_json(input_path, {})
    existing = product_index(read_json(existing_data or output_path, {}))
    groups = raw.get("groups") if isinstance(raw, dict) else {}

    # Load PSREF index for platform default spec enrichment
    psref_index = PsrefIndex.from_directory(
        PSREF_DATASHEETS_DIR,
        machine_type_map=PSREF_DIR / "machine_type_map.json",
    )

    # Collect all machine types to fetch missing PSREF datasheets on demand
    all_raw_skus: set[str] = set()
    for _, rows in (groups or {}).items():
        if isinstance(rows, list):
            for item in rows:
                if isinstance(item, dict):
                    s = normalize_id(item.get("id") or item.get("product_code"))
                    if s:
                        all_raw_skus.add(s)

    force_psref = bool(kwargs.get("force_psref") or kwargs.get("refresh_specs"))
    if force_psref:
        target_skus = all_raw_skus
    else:
        # Only SKUs that are not in existing catalog or existing product has empty/incomplete tech_specs
        target_skus = {
            s for s in all_raw_skus
            if s not in existing or not (existing[s].get("tech_specs", {}).get("ports", {}).get("items"))
        }

    all_mts = {sku[:4].upper() for sku in target_skus if len(sku) >= 4}
    if all_mts:
        try:
            fetched = psref_index.fetch_missing_datasheets(write_dir=PSREF_DATASHEETS_DIR, only=all_mts)
            if fetched:
                print(f"[refurb] On-demand scraped {len(fetched)} PSREF datasheets: {', '.join(fetched)}")
        except Exception as exc:
            print(f"[refurb] Note: on-demand PSREF fetch skipped or failed: {exc}")

    formatted: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    count = 0
    psref_applied = 0
    seen_live_skus: set[str] = set()
    archive_path = output_path.parent / "archive-refurb.json" if output_path else paths.resolve("apps/web/archive-refurb.json")
    now_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    archived_map: dict[str, dict[str, Any]] = {}

    # 1. Load existing archive-refurb.json
    archive_data = read_json(archive_path, {"products": []})
    for archived_prod in archive_data.get("products", []):
        if not isinstance(archived_prod, dict):
            continue
        sku = normalize_id(archived_prod.get("id"))
        if sku:
            archived_map[sku] = archived_prod

    for _, rows in (groups or {}).items():
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, dict):
                continue
            sku = normalize_id(item.get("id") or item.get("product_code"))
            if not sku or sku in seen_live_skus:
                continue

            seen_live_skus.add(sku)
            specs = parse_spec_codes(item.get("specs_by_code") or {})
            category = category_from_product(item)
            store_link = item.get("store_link") or ""
            affiliate_link = item.get("affiliate_link") or lenovo_outlet.build_affiliate_link(store_link, sku)

            # Determine title & model_name with standardized format
            datasheet = psref_index.datasheet_for(sku[:4])
            title = format_refurb_title(
                sku=sku,
                raw_title=str(item.get("product_name") or item.get("summary") or item.get("title") or ""),
                store_link=store_link,
                datasheet=datasheet,
                specs=specs,
            )
            m_name = model_name_from_title(title)

            # Price from history or item
            hist = load_history(history_dir, sku)
            raw_price = item.get("price")
            hist_current = latest_price(hist)
            current_price = hist_current if hist_current else (raw_price if isinstance(raw_price, int) else None)
            if not hist and current_price and isinstance(current_price, (int, float)) and current_price > 0:
                ld = iso_date(item.get("listing_date"))
                dt = f"{ld} 00:00:00" if ld else datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                hist = [{"date": dt, "price": int(current_price)}]
                write_history(history_dir, sku, hist)
            hist_stats = stats(hist)
            mrp_val = item.get("mrp")

            avail_raw = str(item.get("availability") or "in stock").strip().lower()
            is_oos = "out" in avail_raw

            row: dict[str, Any] = {
                "id": sku,
                "model_name": m_name,
                "internal_model_code": sku,
                "title": title,
                "description": item.get("card_summary") or item.get("summary") or "",
                "availability": "out of stock" if is_oos else "in stock",
                "price": f"{current_price}.00 INR" if current_price else "0",
                "mrp": f"{mrp_val}.00 INR" if mrp_val else "0",
                "image": (item.get("images") or [""])[0],
                "affiliate_link": affiliate_link,
                "store_link": store_link,
                "listing_date": iso_date(item.get("listing_date")) or "",
                "tech_specs": specs,
                "spec_source": "lenovo_outlet",
                "vendor": "lenovo",
                "brand": "Lenovo",
                "series": category,
                "product_condition": item.get("product_condition", "CERTIFIED REFURBISHED"),
                "product_metadata": {
                    "brand": "Lenovo",
                    "series": category,
                    "part_number": sku,
                    "model_number": sku,
                    "manufacturer": "Lenovo",
                    "warranty": "1 Year Courier or Carry-in Warranty",
                    "condition": "Certified Refurbished",
                },
                **hist_stats,
            }

            # If not forcing refresh, and existing product already has complete specs, reuse them
            existing_prod = existing.get(sku)
            if not force_psref and existing_prod and existing_prod.get("tech_specs", {}).get("ports", {}).get("items"):
                row["tech_specs"] = existing_prod["tech_specs"]
                if "spec_origin" in existing_prod:
                    row["spec_origin"] = existing_prod["spec_origin"]
                if "psref_enrichment" in existing_prod:
                    row["psref_enrichment"] = existing_prod["psref_enrichment"]
                psref_applied += 1
            else:
                # Enrich with PSREF platform defaults (battery, power, ports, memory slots, etc.)
                if enrich_lenovo_row(row, psref_index=psref_index):
                    psref_applied += 1

            # Fallback for display size if missing
            dpy = row.get("tech_specs", {}).get("display") or {}
            if not dpy.get("size") or dpy.get("size") == "Unknown":
                raw_dpy_str = str((item.get("specs_by_code") or {}).get("LOIS_SCA_DPY") or "")
                size_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:\"|inch|\'\')", raw_dpy_str, flags=re.I)
                if size_match:
                    dpy["size"] = f"{size_match.group(1)}\""
                elif "14" in title or "14" in m_name:
                    dpy["size"] = "14\""
                elif "15" in title or "15" in m_name:
                    dpy["size"] = "15.6\""
                elif "16" in title or "16" in m_name:
                    dpy["size"] = "16\""

            # Normalize hardware specs
            try:
                normalize_product(row)
            except Exception:
                pass

            if is_oos:
                row["archived"] = True
                row["archived_at"] = now_date
                archived_map[sku] = row
                if sku in existing and not bool(existing[sku].get("archived")):
                    print(f"[refurb-archive] Out-of-stock model moved to archive: {sku} - {title}")
            else:
                if sku in archived_map:
                    print(f"[refurb-restore] Restoring model from archive -> active: {sku} - {title} ({row.get('price')})")
                archived_map.pop(sku, None)
                formatted.setdefault(category, []).append(row)
                count += 1

    # Preserve all out-of-stock/archived models in archive-refurb.json
    # Add dropped products from previous data-refurb.json that were not re-scraped
    for sku, existing_product in existing.items():
        if sku in seen_live_skus or sku in archived_map:
            continue
        row = dict(existing_product)
        row["archived"] = True
        row["archived_at"] = row.get("archived_at") or now_date
        row["availability"] = "out of stock"
        archived_map[sku] = row
        print(f"[refurb-archive] Delisted model moved to archive: {sku} - {existing_product.get('title')}")

    archived_list = list(archived_map.values())

    if not dry_run:
        build_inventory_indices()
        write_json(output_path, formatted, indent=4)
        write_json(archive_path, {"products": archived_list}, indent=4)

    print(f"[refurb-summary] Active in-stock catalog: {count} laptops across {len(formatted)} categories. Archived: {len(archived_list)} laptops.")

    return {
        "formatted": count,
        "categories": len(formatted),
        "psref_applied": psref_applied,
        "archived_count": len(archived_list),
    }


register("refurb", sys.modules[__name__])
