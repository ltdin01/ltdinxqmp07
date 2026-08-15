from __future__ import annotations

import re
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from laptopdeals.datafile import product_index
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


def title_from_url_slug(url: str) -> str:
    if not url:
        return ""
    parts = [p for p in url.strip("/").split("/") if p]
    if len(parts) >= 2:
        slug = parts[-2]
        # Ignore raw part codes like 88ips502083
        if re.match(r"^\d+[a-z0-9]+$", slug, re.I) and not re.search(r"(?:ideapad|thinkpad|thinkbook|loq|legion|yoga|v\d+)", slug, re.I):
            return ""
        words = slug.split("-")
        cleaned_words: list[str] = []
        for w in words:
            low = w.lower()
            if low == "ideapad":
                cleaned_words.append("IdeaPad")
            elif low == "thinkpad":
                cleaned_words.append("ThinkPad")
            elif low == "thinkbook":
                cleaned_words.append("ThinkBook")
            elif low == "loq":
                cleaned_words.append("LOQ")
            elif low == "legion":
                cleaned_words.append("Legion")
            elif low == "yoga":
                cleaned_words.append("Yoga")
            elif low == "intel":
                cleaned_words.append("Intel")
            elif low == "amd":
                cleaned_words.append("AMD")
            elif low == "snapdragon":
                cleaned_words.append("Snapdragon")
            elif low == "gen":
                cleaned_words.append("Gen")
            elif low == "inch":
                continue
            elif re.match(r"^\d+i$", w, re.I):
                cleaned_words.append(w.capitalize())
            elif re.match(r"^\d+$", w):
                cleaned_words.append(w)
            elif low in ("14", "15", "16", "13"):
                cleaned_words.append(w)
            else:
                cleaned_words.append(w.capitalize())
        return " ".join(cleaned_words)
    return ""


def format_refurb_title(
    *,
    sku: str,
    raw_title: str,
    store_link: str,
    datasheet: dict[str, Any] | None,
    specs: dict[str, Any],
) -> str:
    psref_mkt = datasheet.get("marketing_name") if datasheet else None
    psref_prod = datasheet.get("product_name") if datasheet else None
    slug_title = title_from_url_slug(store_link)

    source = f"{store_link} {raw_title} {psref_mkt or ''} {psref_prod or ''} {slug_title}"

    # 1. Platform (AMD / Intel / Snapdragon)
    platform = ""
    proc_brand = ((specs.get("processor") or {}).get("brand") or "").upper()
    if "AMD" in proc_brand or re.search(r"\b(?:amd|ryzen)\b", source, re.I):
        platform = "AMD"
    elif "INTEL" in proc_brand or re.search(r"\b(?:intel|core|ultra)\b", source, re.I):
        platform = "Intel"
    elif "QUALCOMM" in proc_brand or "SNAPDRAGON" in proc_brand or re.search(r"\b(?:snapdragon|qualcomm|x\s*elite|x\s*plus)\b", source, re.I):
        platform = "Snapdragon"

    # 2. Screen size
    screen = ""
    dpy_size = str((specs.get("display") or {}).get("size") or "")
    m_screen_spec = re.search(r"(\d+(?:\.\d+)?)", dpy_size)
    if m_screen_spec:
        s_val = float(m_screen_spec.group(1))
        screen = "14" if 13.8 <= s_val <= 14.5 else ("15" if 15.0 <= s_val <= 15.8 else ("16" if 15.9 <= s_val <= 16.5 else ("13" if s_val < 13.8 else str(int(s_val)))))
    else:
        m_screen = re.search(r"\b(13(?:\.3|\.5)?|14(?:\.5)?|15(?:\.6)?|16|17(?:\.3)?)\b", source)
        if m_screen:
            s_val = float(m_screen.group(1))
            screen = "14" if 13.8 <= s_val <= 14.5 else ("15" if 15.0 <= s_val <= 15.8 else ("16" if 15.9 <= s_val <= 16.5 else ("13" if s_val < 13.8 else str(int(s_val)))))

    # 3. Generation
    gen = ""
    quote_match = re.search(r"\((\d+)\"\s*,\s*(\d+)\)", psref_mkt or "") or re.search(r"\((\d+)\"\s*,\s*(\d+)\)", raw_title)
    if quote_match:
        gen = f"Gen {quote_match.group(2)}"
        if not screen:
            screen = quote_match.group(1)
    else:
        m_gen = re.search(r"\bgen\s*(\d+)\b", source, re.I)
        if m_gen:
            gen = f"Gen {m_gen.group(1)}"
        else:
            m_g = re.search(r"\bG(\d+)\b", source, re.I)
            if m_g:
                gen = f"Gen {m_g.group(1)}"
            else:
                m_end_gen = re.search(r"\b(?:14|15|16)[A-Z]{3}(\d{1,2})\b", source, re.I)
                if m_end_gen:
                    gen = f"Gen {m_end_gen.group(1)}"

    # 4. Prefix / Model series
    prefix = ""
    if re.search(r"\bloq\b", source, re.I):
        prefix = "Lenovo LOQ"
    elif re.search(r"\blegion\s*pro\s*5\b", source, re.I):
        prefix = "Legion Pro 5i" if platform == "Intel" else "Legion Pro 5"
    elif re.search(r"\blegion\s*pro\s*7\b", source, re.I):
        prefix = "Legion Pro 7i" if platform == "Intel" else "Legion Pro 7"
    elif re.search(r"\blegion\s*7\b", source, re.I):
        prefix = "Legion 7i" if platform == "Intel" else "Legion 7"
    elif re.search(r"\blegion\s*5\b", source, re.I):
        prefix = "Legion 5i" if platform == "Intel" else "Legion 5"
    elif re.search(r"\b(?:gaming\s*3|ideapad\s*gaming\s*3)\b", source, re.I):
        prefix = "IdeaPad Gaming 3"
    elif re.search(r"\bideapad\s*slim\s*5x\b", source, re.I):
        prefix = "IdeaPad Slim 5x"
    elif re.search(r"\bideapad\s*slim\s*5i\b", source, re.I):
        prefix = "IdeaPad Slim 5i"
    elif re.search(r"\bideapad\s*slim\s*5\b", source, re.I):
        prefix = "IdeaPad Slim 5i" if platform == "Intel" else "IdeaPad Slim 5"
    elif re.search(r"\bideapad\s*slim\s*3x\b", source, re.I):
        prefix = "IdeaPad Slim 3x"
    elif re.search(r"\bideapad\s*slim\s*3i\b", source, re.I):
        prefix = "IdeaPad Slim 3i"
    elif re.search(r"\bideapad\s*slim\s*3\b", source, re.I):
        prefix = "IdeaPad Slim 3i" if platform == "Intel" else "IdeaPad Slim 3"
    elif re.search(r"\bideapad\s*flex\s*5\b", source, re.I):
        prefix = "IdeaPad Flex 5"
    elif re.search(r"\bideapad\s*1\b", source, re.I):
        prefix = "IdeaPad 1"
    elif re.search(r"\byoga\s*book\s*9i\b", source, re.I) or re.search(r"\byoga\s*book\s*9\b", source, re.I):
        prefix = "Yoga Book 9i"
    elif re.search(r"\byoga\s*pro\s*7i\b", source, re.I):
        prefix = "Yoga Pro 7i"
    elif re.search(r"\byoga\s*pro\s*7\b", source, re.I):
        prefix = "Yoga Pro 7i" if platform == "Intel" else "Yoga Pro 7"
    elif re.search(r"\byoga\s*slim\s*7x\b", source, re.I):
        prefix = "Yoga Slim 7x"
    elif re.search(r"\byoga\s*slim\s*7i\b", source, re.I):
        prefix = "Yoga Slim 7i"
    elif re.search(r"\byoga\s*slim\s*7\b", source, re.I):
        prefix = "Yoga Slim 7i" if platform == "Intel" else "Yoga Slim 7"
    elif re.search(r"\byoga\s*slim\s*6i\b", source, re.I) or re.search(r"\byoga\s*slim\s*6\b", source, re.I):
        prefix = "Yoga Slim 6i"
    elif re.search(r"\byoga\s*7i\s*2\s*in\s*1\b", source, re.I) or re.search(r"\byoga\s*7\s*2-in-1\b", source, re.I):
        prefix = "Yoga 7i 2-in-1" if platform == "Intel" else "Yoga 7 2-in-1"
    elif re.search(r"\byoga\s*7i\b", source, re.I) or re.search(r"\byoga\s*7\b", source, re.I):
        prefix = "Yoga 7i" if platform == "Intel" else "Yoga 7"
    elif re.search(r"\bthinkpad\s*x1\s*carbon\b", source, re.I):
        prefix = "ThinkPad X1 Carbon"
    elif re.search(r"\bthinkpad\s*x1\s*yoga\b", source, re.I):
        prefix = "ThinkPad X1 Yoga"
    elif re.search(r"\bthinkpad\s*t14\b", source, re.I):
        prefix = "ThinkPad T14"
    elif re.search(r"\bthinkpad\s*p16s\b", source, re.I):
        prefix = "ThinkPad P16s"
    elif re.search(r"\bthinkpad\s*p16v\b", source, re.I):
        prefix = "ThinkPad P16v"
    elif re.search(r"\bthinkpad\s*p16\b", source, re.I):
        prefix = "ThinkPad P16"
    elif re.search(r"\bthinkpad\s*e14\b", source, re.I):
        prefix = "ThinkPad E14"
    elif re.search(r"\bthinkpad\s*e15\b", source, re.I):
        prefix = "ThinkPad E15"
    elif re.search(r"\bthinkpad\s*e16\b", source, re.I):
        prefix = "ThinkPad E16"
    elif re.search(r"\bmotobook\s*60\b", source, re.I) or re.search(r"\bmotobook\b", source, re.I):
        prefix = "Motobook 60"
    elif re.search(r"\bthinkbook\s*14\b", source, re.I) or re.search(r"\bthinkbook\b", source, re.I):
        prefix = "ThinkBook 14"
    elif re.search(r"\blenovo\s*v14\b", source, re.I) or re.search(r"\bv14\b", source, re.I):
        prefix = "Lenovo V14"
    elif re.search(r"\blenovo\s*v15\b", source, re.I) or re.search(r"\bv15\b", source, re.I):
        prefix = "Lenovo V15"

    if prefix:
        parts = [prefix]
        if gen:
            parts.append(gen)
        details = []
        if screen:
            details.append(screen)
        if platform:
            details.append(platform)
        if details:
            parts.append(f"({', '.join(details)})")
        return " ".join(parts)

    return clean_refurb_title(raw_title, sku) or slug_title or sku


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
    verbose: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    client = lenovo_outlet.LenovoOutletClient(delay=delay, verbose=verbose)
    if verbose:
        print("[refurb] Fetching all laptop listings from Lenovo Outlet DLP API...")

    cards = client.fetch_all_laptops(limit=limit)
    skus = [lenovo_outlet.base_lenovo.clean_text(c.get("productCode")) for c in cards if c.get("productCode")]
    
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

    payload = OrderedDict(
        [
            ("generated_at", datetime.now(timezone.utc).isoformat()),
            ("source", "Lenovo Outlet India DLP API"),
            ("total_products", total_count),
            ("groups", grouped),
        ]
    )
    write_json(output, payload, indent=4)
    if verbose:
        print(f"[refurb] Saved raw catalog to {output} ({total_count} products across {len(grouped)} categories)")
    return {"total": total_count, "categories": len(grouped)}


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
    processed_skus: set[str] = set()

    for _, rows in (groups or {}).items():
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, dict):
                continue
            sku = normalize_id(item.get("id") or item.get("product_code"))
            if not sku:
                continue

            processed_skus.add(sku)
            specs = parse_spec_codes(item.get("specs_by_code") or {})
            category = category_from_product(item)
            store_link = item.get("store_link") or ""
            affiliate_link = item.get("affiliate_link") or lenovo_outlet.build_affiliate_link(store_link, sku)

            # Determine title & model_name with standardized format
            datasheet = psref_index.datasheet_for(sku[:4])
            title = format_refurb_title(
                sku=sku,
                raw_title=" ".join(filter(None, [str(item.get("summary") or ""), str(item.get("product_name") or ""), str(item.get("title") or "")])),
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

            row: dict[str, Any] = {
                "id": sku,
                "model_name": m_name,
                "internal_model_code": sku,
                "title": title,
                "description": item.get("card_summary") or item.get("summary") or "",
                "availability": item.get("availability", "unknown"),
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

            formatted.setdefault(category, []).append(row)
            count += 1

    # Preserve historical / out-of-stock items in archive & unarchive returned products
    archive_data = read_json(archive_path, {"products": []})
    existing_archive_prods = {
        normalize_id(p.get("id")): p
        for p in archive_data.get("products", [])
        if isinstance(p, dict) and p.get("id")
    }

    # If any returning SKU was previously archived, remove it from archive (unarchive)
    for sku in processed_skus:
        if sku in existing_archive_prods:
            existing_archive_prods.pop(sku, None)

    # Any previously existing product not in the current DLP scrape moves to archive
    now_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for sku, old_prod in existing.items():
        if sku not in processed_skus:
            archived_row = dict(old_prod)
            archived_row["archived"] = True
            archived_row["archived_at"] = now_date
            archived_row["availability"] = "out of stock"
            existing_archive_prods[sku] = archived_row

    if not dry_run:
        build_inventory_indices()
        write_json(output_path, formatted, indent=4)
        write_json(archive_path, {"products": list(existing_archive_prods.values())}, indent=4)

    return {
        "formatted": count,
        "categories": len(formatted),
        "psref_applied": psref_applied,
        "archived_count": len(existing_archive_prods),
    }


register("refurb", sys.modules[__name__])
