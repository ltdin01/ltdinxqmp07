from __future__ import annotations

import hashlib
import html
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .http import curl_requests
from .jsonio import read_json, write_json
from .specs import (
    clean_text,
    first_float,
    first_int,
    parse_cpu_psref,
    parse_display_psref,
    parse_gpu_psref,
    parse_memory_psref,
    parse_network_psref,
    parse_storage_psref,
)


MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
MENU_URL = "https://psref.lenovo.com/api/home/menu/info"
EXPORT_URL = "https://psref.lenovo.com/api/search/DefinitionFilterAndSearch/ShowModelExcelExport"
HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "accept": "application/json, text/plain, */*",
    "referer": "https://psref.lenovo.com/",
    "origin": "https://psref.lenovo.com",
    "x-requested-with": "XMLHttpRequest",
}
GPU_VENDOR_PRIORITY = {"NVIDIA": 100, "AMD": 60, "Intel": 30, "Qualcomm": 25, "Unknown": 0}
CPU_VENDOR_PRIORITY = {"Intel": 100, "AMD": 95, "Qualcomm": 70, "Unknown": 0}


def _request_bytes(url: str, accept: str | None = None) -> bytes:
    headers = dict(HEADERS)
    if accept:
        headers["accept"] = accept
    session = curl_requests()
    response = session.get(url, headers=headers, impersonate="chrome120", timeout=60)
    response.raise_for_status()
    return response.content


def safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", clean_text(value)).strip("_") or "unknown"


def stable_spec_id(prefix: str, raw_value: str) -> str:
    digest = hashlib.sha1(clean_text(raw_value).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def column_index(cell_ref: str) -> int:
    match = re.match(r"([A-Z]+)", cell_ref or "")
    if not match:
        return 0
    value = 0
    for ch in match.group(1):
        value = value * 26 + ord(ch) - 64
    return value - 1


def parse_xlsx_rows(blob: bytes) -> list[dict[str, str]]:
    with zipfile.ZipFile(io.BytesIO(blob)) as workbook:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in workbook.namelist():
            root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{MAIN_NS}si"):
                shared_strings.append("".join(t.text or "" for t in item.iter(f"{MAIN_NS}t")))

        wb = ET.fromstring(workbook.read("xl/workbook.xml"))
        first_sheet = wb.find(f"{MAIN_NS}sheets/{MAIN_NS}sheet")
        if first_sheet is None:
            return []
        rel_id = first_sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", "")

        rels = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
        target = "worksheets/sheet1.xml"
        for rel in rels.findall(f"{REL_NS}Relationship"):
            if rel.attrib.get("Id") == rel_id:
                target = rel.attrib.get("Target", target)
                break
        target = target.lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target

        ws = ET.fromstring(workbook.read(target))
        rows: list[list[str]] = []
        for row in ws.findall(f".//{MAIN_NS}row"):
            values: list[str] = []
            for cell in row.findall(f"{MAIN_NS}c"):
                idx = column_index(cell.attrib.get("r", "A1"))
                while len(values) <= idx:
                    values.append("")
                value_node = cell.find(f"{MAIN_NS}v")
                if value_node is None:
                    continue
                value = value_node.text or ""
                if cell.attrib.get("t") == "s" and value.isdigit():
                    value = shared_strings[int(value)]
                values[idx] = clean_text(value)
            if any(values):
                rows.append(values)

    if not rows:
        return []
    headers = rows[0]
    output_rows: list[dict[str, str]] = []
    for row in rows[1:]:
        padded = row + [""] * (len(headers) - len(row))
        output_rows.append({headers[idx]: padded[idx] for idx in range(len(headers))})
    return output_rows


def build_mt_map(menu_payload: Any) -> dict[str, dict[str, Any]]:
    data = menu_payload.get("data") if isinstance(menu_payload, dict) else menu_payload
    mt_map: dict[str, dict[str, Any]] = {}

    def walk(node: Any, parents: list[dict[str, Any]]) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child, parents)
            return
        if not isinstance(node, dict):
            return
        node_type = clean_text(node.get("type"))
        node_id = clean_text(node.get("id"))
        if node_type == "mt" and node_id:
            product = next((item for item in reversed(parents) if item.get("type") == "product"), {})
            series = next((item for item in reversed(parents) if item.get("type") == "series"), {})
            productline = next((item for item in reversed(parents) if item.get("type") == "productline"), {})
            classification = next((item for item in reversed(parents) if item.get("type") == "classification"), {})
            product_info = product.get("info") or {}
            mt_map[node_id.upper()] = {
                "machine_type": node_id.upper(),
                "product_key": product.get("id") or "",
                "product_name": product.get("name") or "",
                "marketing_name": product_info.get("MarketingName") or "",
                "product_id": product_info.get("ProductID") or "",
                "series_name": series.get("name") or "",
                "productline_name": productline.get("name") or "",
                "classification_name": classification.get("name") or "",
                "psref_href": (node.get("info") or {}).get("href") or "",
            }
        children = node.get("subcollection")
        if isinstance(children, list):
            parent = {"id": node_id, "name": clean_text(node.get("name")), "type": node_type, "info": node.get("info") or {}}
            for child in children:
                walk(child, [*parents, parent])

    walk(data, [])
    return mt_map


def extract_platform_code(product_key: str) -> str:
    for part in reversed((product_key or "").split("_")):
        if re.fullmatch(r"\d{2}[A-Z]{3}\d{1,2}", part):
            return part
    match = re.search(r"(\d{2}[A-Z]{3}\d{1,2})", product_key or "")
    return match.group(1) if match else ""


def split_list(raw: str) -> list[str]:
    text = html.unescape(str(raw or ""))
    parts = re.split(r"\^\|\^|[•\n]+", text)
    if len([part for part in parts if clean_text(part)]) <= 1:
        compact = clean_text(text)
        parts = re.split(
            r"\s+(?=\d+x\s+(?:USB|HDMI|Headphone|Ethernet|Slim|Power|RJ|SD|microSD|Thunderbolt))",
            compact,
            flags=re.IGNORECASE,
        )
    return [clean_text(part) for part in parts if clean_text(part)]


def build_specs_from_row(row: dict[str, str]) -> dict[str, Any]:
    specs: dict[str, Any] = OrderedDict()
    specs["processor"] = parse_cpu_psref(row.get("Processor", ""))
    specs["graphics"] = parse_gpu_psref(row.get("Graphics", ""))
    specs["memory"] = parse_memory_psref(row.get("Memory", ""))
    specs["storage"] = parse_storage_psref(row.get("Storage", ""))
    specs["display"] = parse_display_psref(row.get("Display", ""))
    specs["network"] = parse_network_psref(row.get("WLAN + Bluetooth", ""))
    power_raw = clean_text(row.get("Power Adapter", ""))
    specs["power"] = {"raw": power_raw, "adapter": power_raw, "watt": first_int(row.get("Power Adapter", ""), r"(\d+)W")}
    battery_raw = clean_text(row.get("Battery", ""))
    specs["battery"] = {"raw": battery_raw, "capacity_wh": first_int(row.get("Battery", ""), r"(\d+)Wh")}
    ports_raw = row.get("Standard Ports", "")
    specs["ports"] = {"raw": clean_text(ports_raw), "items": split_list(ports_raw)}
    specs["memory_slots"] = {"raw": clean_text(row.get("Memory Slots", "")), "max_memory": clean_text(row.get("Max Memory", ""))}
    specs["storage_slots"] = {"raw": clean_text(row.get("Storage Slot", "")), "max_storage": clean_text(row.get("Max Storage Support", ""))}
    specs["camera"] = {"raw": clean_text(row.get("Camera", ""))}
    specs["audio"] = {
        "chip": clean_text(row.get("Audio Chip", "")),
        "speakers": clean_text(row.get("Speakers", "")),
        "microphone": clean_text(row.get("Microphone", "")),
    }
    specs["keyboard"] = {"raw": clean_text(row.get("Keyboard", ""))}
    specs["dimensions"] = {"raw": clean_text(row.get("Dimensions (WxDxH)", "")), "weight": clean_text(row.get("Weight", ""))}
    specs["build"] = {
        "color": clean_text(row.get("Case Color", "")),
        "material": clean_text(row.get("Case Material", "")),
        "surface": clean_text(row.get("Surface Treatment", "")),
    }
    specs["software"] = {"os": clean_text(row.get("Operating System", "")), "bundled": clean_text(row.get("Bundled Software", ""))}
    specs["security"] = {
        "chip": clean_text(row.get("Security Chip", "")),
        "fingerprint": clean_text(row.get("Fingerprint Reader", "")),
        "other": clean_text(row.get("Other Security", "")),
    }
    specs["warranty"] = {"base": clean_text(row.get("Base Warranty", "")), "upgrade": clean_text(row.get("Included Upgrade", ""))}
    specs["certifications"] = {
        "green": split_list(row.get("Green Certifications", "")),
        "mil_spec": clean_text(row.get("Mil-Spec Test", "")),
        "other": split_list(row.get("Other Certifications", "")),
    }
    return compact_specs(specs)


def compact_specs(specs: Any) -> Any:
    if isinstance(specs, dict):
        output = OrderedDict()
        for key, value in specs.items():
            clean = compact_specs(value)
            if clean not in ("", None, [], {}):
                output[key] = clean
        return output
    if isinstance(specs, list):
        return [item for item in (compact_specs(item) for item in specs) if item not in ("", None, [], {})]
    return specs


def classify_cto_option(label: str) -> str:
    low = clean_text(label).lower()
    if "processor" in low:
        return "processor"
    if "graphic" in low or "gpu" in low or "video" in low:
        return "graphics"
    if "memory" in low or "ram" in low:
        return "memory"
    if "solid state" in low or "storage" in low or "drive" in low or "ssd" in low:
        return "storage"
    if "display" in low or "screen" in low:
        return "display"
    return ""


def feature_signature(category: str, parsed: dict[str, Any]) -> str:
    if category == "processor":
        return clean_text(parsed.get("model") or parsed.get("full_model"))
    if category == "graphics":
        return clean_text(parsed.get("model") or parsed.get("full_model"))
    if category == "memory":
        return clean_text(f"{parsed.get('amount_gb') or ''} {parsed.get('type') or ''} {parsed.get('speed_mhz') or ''}")
    if category == "storage":
        return clean_text(f"{parsed.get('capacity_gb') or ''} {parsed.get('type') or ''}")
    if category == "display":
        return clean_text(f"{parsed.get('size_inches') or ''} {parsed.get('resolution') or ''} {parsed.get('type') or ''} {parsed.get('refresh_hz') or ''}")
    return clean_text(parsed.get("raw") or "")


def choice_parser(category: str):
    return {
        "processor": parse_cpu_psref,
        "graphics": parse_gpu_psref,
        "memory": parse_memory_psref,
        "storage": parse_storage_psref,
        "display": parse_display_psref,
    }.get(category)


def build_cto_expectations(product: dict[str, Any], cto_config: dict[str, Any] | None) -> dict[str, Any]:
    expectations: dict[str, Any] = {"required": {}, "allowed": {}, "has_cto": bool(cto_config)}
    if not cto_config:
        return expectations
    for option in cto_config.get("options") or []:
        category = classify_cto_option(option.get("label", ""))
        parser = choice_parser(category)
        if not category or not parser:
            continue
        signatures: set[str] = set()
        default_signature = ""
        scored: list[tuple[float, str]] = []
        for choice in option.get("choices") or []:
            parsed = parser(choice.get("label", ""))
            signature = feature_signature(category, parsed)
            if not signature:
                continue
            signatures.add(signature)
            score = float(choice.get("gapPrice") or 0)
            scored.append((score, signature))
            if choice.get("isDefault"):
                default_signature = signature
        if signatures:
            expectations["allowed"][category] = signatures
        if default_signature:
            expectations["required"][category] = default_signature
        elif scored:
            expectations["required"][category] = max(scored)[1]
    return expectations


def row_sort_power(specs: dict[str, Any]) -> tuple:
    cpu = specs.get("processor") or {}
    gpu = specs.get("graphics") or {}
    mem = specs.get("memory") or {}
    sto = specs.get("storage") or {}
    dpy = specs.get("display") or {}
    return (
        GPU_VENDOR_PRIORITY.get(gpu.get("brand"), 0),
        gpu.get("tgp_w") or 0,
        gpu.get("vram_gb") or 0,
        CPU_VENDOR_PRIORITY.get(cpu.get("brand"), 0),
        cpu.get("boost_clock_ghz") or 0,
        cpu.get("cores") or 0,
        mem.get("amount_gb") or 0,
        sto.get("capacity_gb") or 0,
        dpy.get("refresh_hz") or 0,
        dpy.get("brightness_nits") or 0,
    )


def score_candidate_row(row: dict[str, str], expectations: dict[str, Any]) -> tuple[tuple, dict[str, Any]]:
    specs = build_specs_from_row(row)
    required_hits = 0
    allowed_hits = 0
    reject_reasons: list[str] = []
    for category, expected in (expectations.get("required") or {}).items():
        signature = feature_signature(category, specs.get(category) or {})
        if signature == expected:
            required_hits += 1
        else:
            reject_reasons.append(f"{category}:expected:{expected}:got:{signature}")
    for category, allowed in (expectations.get("allowed") or {}).items():
        signature = feature_signature(category, specs.get(category) or {})
        if signature in allowed:
            allowed_hits += 1
    country = clean_text(row.get("Country/Region"))
    country_priority = 10 if country.lower() == "india" else (6 if "india" in country.lower() else 0)
    diagnostics = {
        "required_hits": required_hits,
        "allowed_hits": allowed_hits,
        "reject_reasons": reject_reasons,
        "country_priority": country_priority,
        "power": row_sort_power(specs),
    }
    return (required_hits, allowed_hits, country_priority, *row_sort_power(specs)), diagnostics


def save_workbook_cache(output_dir: Path, prefix: str, mt_entry: dict[str, Any], refresh: bool) -> list[dict[str, str]]:
    product_key = mt_entry["product_key"]
    workbook_path = output_dir / "xlsx" / f"{prefix}_{safe_slug(product_key)}.xlsx"
    if refresh or not workbook_path.exists():
        url = f"{EXPORT_URL}?pageindex=0&pagesize=10000&product_key={quote(product_key)}"
        blob = _request_bytes(url, accept="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*")
        workbook_path.parent.mkdir(parents=True, exist_ok=True)
        workbook_path.write_bytes(blob)
    return parse_xlsx_rows(workbook_path.read_bytes())


def build_inventory(entries: list[dict[str, Any]]) -> dict[str, Any]:
    counters: dict[str, Counter] = {
        "processors": Counter(),
        "graphics": Counter(),
        "memory": Counter(),
        "storage": Counter(),
        "display": Counter(),
        "match_types": Counter(),
        "missing_fields": Counter(),
    }
    for entry in entries:
        counters["match_types"][entry.get("match_type") or "unknown"] += 1
        specs = entry.get("tech_specs") or {}
        for key, raw in _spec_raw_fields(specs).items():
            bucket_key = {"processor": "processors", "graphics": "graphics"}.get(key, key)
            if raw:
                counters[bucket_key][raw] += 1
            else:
                counters["missing_fields"][key] += 1
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {key: len(counter) for key, counter in counters.items()},
        **{key: dict(counter.most_common()) for key, counter in counters.items()},
    }


def _spec_raw_fields(specs: dict[str, Any]) -> dict[str, str]:
    return {
        "processor": (specs.get("processor") or {}).get("raw", ""),
        "graphics": (specs.get("graphics") or {}).get("raw", ""),
        "memory": (specs.get("memory") or {}).get("raw", ""),
        "storage": (specs.get("storage") or {}).get("raw", ""),
        "display": (specs.get("display") or {}).get("raw", ""),
    }


def build_spec_database(entries: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    buckets: dict[str, dict[str, Any]] = {key: OrderedDict() for key in ["processors", "graphics", "memory", "storage", "displays"]}
    prefixes = {"processors": "cpu", "graphics": "gpu", "memory": "mem", "storage": "sto", "displays": "dpy"}
    spec_key_map = {"processors": "processor", "graphics": "graphics", "memory": "memory", "storage": "storage", "displays": "display"}
    index: dict[str, dict[str, str]] = {value: {} for value in spec_key_map.values()}

    for entry in entries:
        specs = entry.get("tech_specs") or {}
        for bucket_name, spec_key in spec_key_map.items():
            spec = specs.get(spec_key) or {}
            raw = clean_text(spec.get("raw") if isinstance(spec, dict) else "")
            if not raw:
                continue
            bucket = buckets[bucket_name]
            if raw not in bucket:
                bucket[raw] = {
                    "id": stable_spec_id(prefixes[bucket_name], raw),
                    "raw": raw,
                    "normalized": spec,
                    "count": 0,
                    "example_skus": [],
                }
            bucket[raw]["count"] += 1
            if len(bucket[raw]["example_skus"]) < 8:
                bucket[raw]["example_skus"].append(entry.get("id"))
            index[spec_key][raw] = bucket[raw]["id"]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **{name: list(values.values()) for name, values in buckets.items()},
    }, index


def build_final_sku_specs(entries: list[dict[str, Any]], spec_index: dict[str, dict[str, str]]) -> dict[str, Any]:
    final: OrderedDict[str, Any] = OrderedDict()
    for entry in entries:
        specs = entry.get("tech_specs") or {}
        spec_refs: dict[str, str] = {}
        missing_refs: list[str] = []
        for spec_key in ["processor", "graphics", "memory", "storage", "display"]:
            raw = clean_text((specs.get(spec_key) or {}).get("raw"))
            ref = spec_index.get(spec_key, {}).get(raw)
            if ref:
                spec_refs[spec_key] = ref
            elif raw:
                missing_refs.append(spec_key)
        payload = OrderedDict(entry)
        payload["spec_refs"] = spec_refs
        payload["missing_spec_refs"] = missing_refs
        final[entry["id"]] = payload
    return final


def build(
    *,
    catalog_path: Path,
    cto_dir: Path,
    output_dir: Path,
    refresh: bool = False,
    sku_filter: set[str] | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    raw_catalog = read_json(catalog_path, {})
    products = _iter_catalog_products(raw_catalog)
    if sku_filter:
        products = [item for item in products if clean_text(item.get("id") or item.get("product_code")).upper() in sku_filter]

    menu_cache = output_dir / "menu.json"
    mt_cache = output_dir / "machine_type_map.json"
    sidecar_dir = output_dir / "by_sku"
    report_path = output_dir / "report.json"

    # Always fetch fresh menu to discover new machine types (small API call).
    # Xlsx workbooks are still cached per-prefix — only missing ones are downloaded.
    try:
        menu_payload = _request_bytes(MENU_URL, accept="application/json, text/plain, */*")
        menu_payload = __import__("json").loads(menu_payload.decode("utf-8-sig"))
        write_json(menu_cache, menu_payload)
    except Exception:
        # Fall back to cached menu if PSREF API is unreachable
        menu_payload = read_json(menu_cache, {})

    mt_map = build_mt_map(menu_payload)
    write_json(mt_cache, mt_map)

    grouped: dict[str, list[dict[str, Any]]] = OrderedDict()
    for product in products:
        sku = clean_text(product.get("id") or product.get("product_code")).upper()
        if not sku:
            continue
        grouped.setdefault(sku[:4], []).append(product)

    results: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    workbook_meta: dict[str, Any] = {}

    for prefix, prefix_products in sorted(grouped.items()):
        mt_entry = mt_map.get(prefix)
        if not mt_entry:
            for product in prefix_products:
                sku = clean_text(product.get("id") or product.get("product_code")).upper()
                entry = {"id": sku, "status": "missing", "match_type": "missing_machine_type", "machine_type": prefix, "tech_specs": {}}
                results.append(entry)
                missing.append(entry)
            continue

        rows = save_workbook_cache(output_dir, prefix, mt_entry, refresh)
        prefix_rows = [row for row in rows if clean_text(row.get("Machine Type")).upper() == prefix]
        country_counts = Counter(clean_text(row.get("Country/Region")) for row in prefix_rows)
        workbook_meta[prefix] = {
            "machine_type": prefix,
            "product_key": mt_entry["product_key"],
            "product_name": mt_entry.get("product_name"),
            "row_count": len(prefix_rows),
            "country_counts": dict(country_counts.most_common()),
        }
        model_map = {clean_text(row.get("Model")).upper(): row for row in prefix_rows}

        for product in prefix_products:
            sku = clean_text(product.get("id") or product.get("product_code")).upper()
            exact = model_map.get(sku)
            match_type = "exact"
            chosen = exact
            diagnostics: dict[str, Any] = {}

            if chosen is None:
                cto_config = read_json(cto_dir / f"{sku}.json", None)
                expectations = build_cto_expectations(product, cto_config)
                scored = []
                for row in prefix_rows:
                    score, diag = score_candidate_row(row, expectations)
                    scored.append((score, diag, row))
                scored.sort(key=lambda item: item[0], reverse=True)
                if scored:
                    chosen = scored[0][2]
                    diagnostics = scored[0][1]
                    match_type = "cto_heuristic" if "CTO" in sku else "heuristic"

            if chosen is None:
                entry = {
                    "id": sku,
                    "status": "missing",
                    "match_type": "missing",
                    "machine_type": prefix,
                    "product_key": mt_entry.get("product_key"),
                    "product_name": mt_entry.get("product_name"),
                    "marketing_name": mt_entry.get("marketing_name"),
                    "marketing_name_primary": (mt_entry.get("marketing_name") or "").split(" / ")[0],
                    "platform_code": extract_platform_code(mt_entry.get("product_key") or ""),
                    "product_id": mt_entry.get("product_id"),
                    "psref_href": mt_entry.get("psref_href"),
                    "tech_specs": {},
                }
                results.append(entry)
                missing.append(entry)
                continue

            specs = build_specs_from_row(chosen)
            entry = OrderedDict(
                [
                    ("id", sku),
                    ("status", "resolved"),
                    ("match_type", match_type),
                    ("machine_type", prefix),
                    ("product_key", mt_entry.get("product_key")),
                    ("product_name", mt_entry.get("product_name")),
                    ("marketing_name", mt_entry.get("marketing_name")),
                    ("marketing_name_primary", (mt_entry.get("marketing_name") or "").split(" / ")[0]),
                    ("platform_code", extract_platform_code(mt_entry.get("product_key") or "")),
                    ("product_id", mt_entry.get("product_id")),
                    ("psref_href", mt_entry.get("psref_href")),
                    ("psref_product", clean_text(chosen.get("Product"))),
                    ("series_name", mt_entry.get("series_name")),
                    ("productline_name", mt_entry.get("productline_name")),
                    ("classification_name", mt_entry.get("classification_name")),
                    ("country_region", clean_text(chosen.get("Country/Region"))),
                    ("psref_model", clean_text(chosen.get("Model"))),
                    ("diagnostics", diagnostics),
                    ("tech_specs", specs),
                    ("raw_psref", chosen),
                ]
            )
            results.append(entry)
            write_json(sidecar_dir / f"{sku}.json", entry)
            if verbose:
                print(f"[psref] {sku} {match_type}")

    inventory = build_inventory(results)
    spec_db, spec_index = build_spec_database(results)
    final_sku_specs = build_final_sku_specs(results, spec_index)
    for entry in final_sku_specs.values():
        entry.pop("raw_psref", None)

    write_json(output_dir / "inventory.json", inventory)
    write_json(output_dir / "spec_database.json", spec_db)
    write_json(output_dir / "final_sku_specs.json", final_sku_specs)
    write_json(
        report_path,
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "catalog": str(catalog_path),
            "total": len(results),
            "resolved": sum(1 for item in results if item.get("status") == "resolved"),
            "missing": len(missing),
            "match_types": dict(Counter(item.get("match_type") for item in results)),
            "workbooks": workbook_meta,
            "missing_entries": missing,
        },
    )
    resolved = sum(1 for item in results if item.get("status") == "resolved")
    print(f"Built PSREF specs: {resolved}/{len(results)} resolved")
    return {"total": len(results), "resolved": resolved, "missing": len(missing)}


def _iter_catalog_products(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    groups = payload.get("groups")
    rows: list[dict[str, Any]] = []
    if isinstance(groups, dict):
        for items in groups.values():
            if isinstance(items, list):
                rows.extend(item for item in items if isinstance(item, dict))
    else:
        for items in payload.values():
            if isinstance(items, list):
                rows.extend(item for item in items if isinstance(item, dict))
    return rows
