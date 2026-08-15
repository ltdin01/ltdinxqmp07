from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import re
import threading
import time
import uuid
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from .http import curl_requests
from .jsonio import read_json, write_json
from .sources.lenovo import clean_text
from .specs import (
    first_float,
    first_int,
    parse_cpu_psref,
    parse_display_psref,
    parse_gpu_psref,
    parse_memory_psref,
    parse_network_psref,
    parse_storage_psref,
    parse_spec_codes,
)


MENU_URL = "https://psref.lenovo.com/api/home/menu/info?IsPreviewProduct=true"
SHOW_MODEL_URL = "https://psref.lenovo.com/api/search/DefinitionFilterAndSearch/ShowModel"
LOAD_SPEC_DATA_URL = "https://psref.lenovo.com/api/product/Compare/LoadSpecData"
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


class PsrefSessionManager:
    _instance = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._session: Any = None
        self._jwt: str | None = None
        self._sig_secret: str | None = None
        self._exp: float = 0.0

    @classmethod
    def get_instance(cls) -> PsrefSessionManager:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _init_session(self, retries: int = 4) -> None:
        token_path = Path("/tmp/psref_session.json")
        now = time.time()
        if token_path.exists():
            try:
                cached = json.loads(token_path.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and cached.get("exp", 0) > now + 60:
                    self._jwt = cached["jwt"]
                    self._sig_secret = cached["sig_secret"]
                    self._exp = float(cached["exp"])
                    self._session = curl_requests()
                    return
            except Exception:
                pass

        headers = {
            "Content-Type": "application/json",
            "User-Agent": HEADERS["user-agent"],
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://psref.lenovo.com",
            "Referer": "https://psref.lenovo.com/",
        }
        for attempt in range(retries):
            try:
                now = time.time()
                self._session = curl_requests()
                self._session.get("https://psref.lenovo.com/", headers={"User-Agent": HEADERS["user-agent"]}, impersonate="chrome120")
                time.sleep(1.0)
                res = self._session.post("https://psref.lenovo.com/api/home/auth/issue", json={}, headers=headers, impersonate="chrome120")
                res.raise_for_status()
                data = res.json()
                self._jwt = data["access_token"]

                payload_b64 = self._jwt.split(".")[1]
                payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
                payload_json = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
                self._sig_secret = payload_json["ss"]
                self._exp = float(payload_json.get("exp", now + 1800))

                try:
                    token_path.write_text(
                        json.dumps({"jwt": self._jwt, "sig_secret": self._sig_secret, "exp": self._exp}),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
                return
            except Exception as err:
                if attempt == retries - 1:
                    raise
                time.sleep(3.0 * (attempt + 1))

    def request_bytes(self, url: str, accept: str | None = None, retries: int = 3, timeout: int = 120) -> bytes:
        with self._lock:
            now = time.time()
            if self._session is None or self._jwt is None or now >= self._exp - 60:
                self._init_session()
            session = self._session
            jwt = self._jwt
            sig_secret = self._sig_secret

        parsed = urlparse(url)
        path_and_query = parsed.path
        if parsed.query:
            path_and_query += "?" + parsed.query

        body_hash = ""
        ts = str(int(time.time()))
        nonce = str(uuid.uuid4())
        to_sign = f"GET|{path_and_query}|{body_hash}|{ts}|{nonce}"
        sig = hmac.new(bytes.fromhex(sig_secret), to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        req_headers = {
            "User-Agent": HEADERS["user-agent"],
            "Accept": accept or "application/json, text/plain, */*",
            "Referer": "https://psref.lenovo.com/",
            "Origin": "https://psref.lenovo.com",
            "Authorization": f"Bearer {jwt}",
            "X-Ts": ts,
            "X-Nonce": nonce,
            "X-Sig": sig,
        }

        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                resp = session.get(url, headers=req_headers, impersonate="chrome120", timeout=timeout)
                if resp.status_code == 401 and attempt < retries - 1:
                    with self._lock:
                        self._init_session()
                        session = self._session
                        jwt = self._jwt
                        sig_secret = self._sig_secret
                    ts = str(int(time.time()))
                    nonce = str(uuid.uuid4())
                    to_sign = f"GET|{path_and_query}|{body_hash}|{ts}|{nonce}"
                    sig = hmac.new(bytes.fromhex(sig_secret), to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
                    req_headers["Authorization"] = f"Bearer {jwt}"
                    req_headers["X-Ts"] = ts
                    req_headers["X-Nonce"] = nonce
                    req_headers["X-Sig"] = sig
                    time.sleep(1.0)
                    continue
                resp.raise_for_status()
                return resp.content
            except Exception as err:
                last_err = err
                time.sleep(1.5 * (attempt + 1))
        if last_err:
            raise last_err
        raise RuntimeError(f"Failed to fetch {url}")


def _request_bytes(url: str, accept: str | None = None, retries: int = 3, timeout: int = 120) -> bytes:
    return PsrefSessionManager.get_instance().request_bytes(url, accept=accept, retries=retries, timeout=timeout)


def safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", clean_text(value)).strip("_") or "unknown"


def stable_spec_id(prefix: str, raw_value: str) -> str:
    digest = hashlib.sha1(clean_text(raw_value).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


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
    specs["battery"] = {"raw": battery_raw, "capacity_wh": first_float(row.get("Battery", ""), r"(\d+(?:\.\d+)?)\s*Wh")}
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


def hydrate_sku_specs(spec_refs: dict[str, str], spec_pool: dict[str, dict[str, Any]], platform_defaults: dict[str, Any]) -> dict[str, Any]:
    specs: dict[str, Any] = OrderedDict()
    pool_map = {
        "processor": spec_pool.get("processors", {}),
        "graphics": spec_pool.get("graphics", {}),
        "memory": spec_pool.get("memory", {}),
        "storage": spec_pool.get("storage", {}),
        "display": spec_pool.get("displays", {}),
    }
    for category in ["processor", "graphics", "memory", "storage", "display"]:
        ref_id = (spec_refs or {}).get(category, "")
        if ref_id and category in pool_map:
            item = pool_map[category].get(ref_id)
            if item:
                specs[category] = item.get("normalized") if isinstance(item, dict) and "normalized" in item else item
    for key, val in (platform_defaults or {}).items():
        if key not in specs and val:
            specs[key] = val
    return compact_specs(specs)


def fetch_mt_model_data_json(product_key: str, mt: str = "") -> tuple[list[dict[str, str]], dict[str, list[str]]]:
    pageindex = 1
    pagesize = 2000
    output_rows: list[dict[str, str]] = []
    cleaned_filters: dict[str, list[str]] = {}

    while True:
        url = f"{SHOW_MODEL_URL}?pageindex={pageindex}&pagesize={pagesize}&product_key={quote(product_key)}{'&mt=' + quote(mt) if mt else ''}&vertical=true"

        try:
            blob = _request_bytes(url, accept="application/json, text/plain, */*")
            if not blob or not blob.strip():
                break
            payload = json.loads(blob.decode("utf-8-sig"))
        except Exception:
            break

        if not isinstance(payload, dict):
            break
        data = payload.get("data")
        if not isinstance(data, dict):
            break
        cols = [clean_text(c) for c in (data.get("cols") or [])]
        rows = data.get("rows") or []
        filter_value_array = data.get("filter_value_array") or {}

        if not cleaned_filters and isinstance(filter_value_array, dict):
            for k, v in filter_value_array.items():
                if isinstance(v, list):
                    cleaned_filters[k] = [clean_text(x) for x in v if clean_text(x)]

        for row in rows:
            if isinstance(row, list):
                padded = row + [""] * (len(cols) - len(row))
                output_rows.append({cols[idx]: clean_text(padded[idx]) for idx in range(len(cols))})

        total = data.get("total") or len(output_rows)
        if len(output_rows) >= total or not rows:
            break
        pageindex += 1

    return output_rows, cleaned_filters


def resolve_mt_via_suggest(machine_type: str) -> dict[str, Any] | None:
    if not machine_type:
        return None
    mt = machine_type.strip().upper()

    for search_type in ["MT", ""]:
        url = f"https://psref.lenovo.com/api/search/DefinitionFilterAndSearch/Suggest?kw={quote(mt)}&limit=6&IsPreviewProduct=true"
        if search_type:
            url += f"&SearchType={search_type}"
        try:
            blob = _request_bytes(url, accept="application/json, text/plain, */*")
            if not blob or not blob.strip():
                continue
            payload = json.loads(blob.decode("utf-8-sig"))
            data = payload.get("data") or []
            for item in data:
                if clean_text(item.get("MachineType")).upper() == mt or not item.get("MachineType"):
                    pkey = item.get("ProductKey")
                    if pkey:
                        return {
                            "machine_type": mt,
                            "product_key": pkey,
                            "product_name": clean_text(item.get("ProductName")),
                            "marketing_name": clean_text(item.get("MarketingName")) or clean_text(item.get("ProductName")),
                            "product_id": str(item.get("ProductId") or ""),
                            "productline_name": clean_text(item.get("ProductLine")),
                            "classification_name": clean_text(item.get("ClassificationName")) or "Laptops",
                            "psref_href": (item.get("info") or {}).get("page") or f"https://psref.lenovo.com/l/Product/{pkey}",
                        }
            if data:
                item = data[0]
                pkey = item.get("ProductKey")
                if pkey:
                    return {
                        "machine_type": mt,
                        "product_key": pkey,
                        "product_name": clean_text(item.get("ProductName")),
                        "marketing_name": clean_text(item.get("MarketingName")) or clean_text(item.get("ProductName")),
                        "product_id": str(item.get("ProductId") or ""),
                        "productline_name": clean_text(item.get("ProductLine")),
                        "classification_name": clean_text(item.get("ClassificationName")) or "Laptops",
                        "psref_href": (item.get("info") or {}).get("page") or f"https://psref.lenovo.com/l/Product/{pkey}",
                    }
        except Exception:
            pass
    return None


def fetch_datasheet_for_mt(machine_type: str, mt_entry: dict[str, Any] | None = None) -> dict[str, Any] | None:
    mt = machine_type.strip().upper()
    entry = mt_entry or resolve_mt_via_suggest(mt)
    if not entry or not entry.get("product_key"):
        return None
    product_key = entry["product_key"]
    rows, filters = fetch_mt_model_data_json(product_key=product_key, mt=mt)
    if not rows and not filters:
        rows, filters = fetch_mt_model_data_json(product_key=product_key)
    return build_mt_datasheet(mt, rows, entry, filters)


def fetch_product_spec_data(product_key: str) -> dict[str, Any]:
    if not product_key:
        return {}
    url = f"{LOAD_SPEC_DATA_URL}?ProductKey={quote(product_key)}"
    try:
        blob = _request_bytes(url, accept="application/json, text/plain, */*")
        if not blob:
            return {}
        payload = json.loads(blob.decode("utf-8-sig"))
        gdata = payload.get("data", {}).get("GeneralSpecData") or []
        if not gdata:
            return {}
        raw_json = gdata[0].get("GeneralSpecJson")
        if not raw_json:
            return {}
        spec_obj = json.loads(raw_json)
        spec_data = spec_obj.get("data", {}).get("SpecData", [])
    except Exception:
        return {}

    flat: dict[str, list[str]] = {}
    for l1 in spec_data:
        for l2 in l1.get("L2", []):
            for feat in l2.get("Features", []):
                fname = clean_text(feat.get("FName"))
                items = []
                for fv in feat.get("FVs", []):
                    for fvg in fv.get("FVGs", []):
                        for item in fvg.get("FVGItem", []):
                            v = clean_text(item.get("AttV"))
                            if v:
                                items.append(html.unescape(v))
                if fname and items:
                    flat[fname] = items

    out: dict[str, Any] = OrderedDict()

    # 1. Ports
    ports_items = flat.get("Standard Ports") or []
    if ports_items:
        out["ports"] = {"raw": "^|^".join(ports_items), "items": ports_items}

    # 2. Dimensions & Weight
    dim_items = flat.get("Dimensions (WxDxH)") or []
    dim_val = dim_items[-1] if dim_items else ""
    wt_items = flat.get("Weight") or []
    wt_val = wt_items[-1] if wt_items else ""
    if dim_val or wt_val:
        out["dimensions"] = {"raw": dim_val, "size": dim_val, "weight": wt_val}

    # 3. Audio & Speakers
    chip_val = flat.get("Audio Chip", [""])[0]
    spk_val = flat.get("Speakers", [""])[0]
    mic_val = flat.get("Microphone", [""])[0]
    if chip_val or spk_val or mic_val:
        out["audio"] = {"chip": chip_val, "speakers": spk_val, "microphone": mic_val}

    # 4. Memory & Storage slots
    mslots_val = flat.get("Memory Slots", [""])[0]
    maxmem_val = flat.get("Max Memory", [""])[0]
    if mslots_val or maxmem_val:
        out["memory_slots"] = {"raw": mslots_val, "max_memory": maxmem_val}

    sslots_val = flat.get("Storage Slot", [""])[0]
    maxsto_val = flat.get("Storage Support", [""])[0]
    if sslots_val or maxsto_val:
        out["storage_slots"] = {"raw": sslots_val, "max_storage": maxsto_val}

    # 5. Build, Keyboard, Security, Warranty
    color_items = flat.get("Case Color") or []
    color_val = ", ".join(color_items) if color_items else ""
    mat_val = flat.get("Case Material", [""])[0]
    surf_val = flat.get("Surface Treatment", [""])[0]
    if color_val or mat_val or surf_val:
        out["build"] = {"color": color_val, "material": mat_val, "surface": surf_val}

    kbd_val = flat.get("Keyboard", [""])[0]
    if kbd_val:
        out["keyboard"] = {"raw": kbd_val}

    sec_chip = flat.get("Security Chip", [""])[0]
    fp_val = flat.get("Fingerprint Reader", [""])[0]
    other_sec = flat.get("Other Security", [""])[0]
    if sec_chip or fp_val or other_sec:
        out["security"] = {"chip": sec_chip, "fingerprint": fp_val, "other": other_sec}

    warr_val = flat.get("Base Warranty", [""])[0]
    if warr_val:
        out["warranty"] = {"base": warr_val}

    green_certs = flat.get("Green Certifications") or []
    mil_spec = flat.get("Mil-Spec Test", [""])[0]
    other_certs = flat.get("Other Certifications") or []
    if green_certs or mil_spec or other_certs:
        out["certifications"] = {"green": green_certs, "mil_spec": mil_spec, "other": other_certs}

    return compact_specs(out)


def build_mt_datasheet(prefix: str, prefix_rows: list[dict[str, str]], mt_entry: dict[str, Any], filter_options: dict[str, list[str]] | None = None) -> dict[str, Any]:
    filter_options = filter_options or {}
    spec_pool: dict[str, dict[str, Any]] = {
        "processors": OrderedDict(),
        "graphics": OrderedDict(),
        "memory": OrderedDict(),
        "storage": OrderedDict(),
        "displays": OrderedDict(),
    }
    platform_defaults: dict[str, Any] = OrderedDict()
    models: dict[str, Any] = OrderedDict()

    # Pre-populate spec_pool with filter_value_array options if available
    for raw_proc in filter_options.get("Processor") or []:
        cpu_id = stable_spec_id("cpu", raw_proc)
        if cpu_id not in spec_pool["processors"]:
            spec_pool["processors"][cpu_id] = {"id": cpu_id, "raw": raw_proc, "normalized": parse_cpu_psref(raw_proc)}

    for raw_gpu in filter_options.get("Graphics") or []:
        gpu_id = stable_spec_id("gpu", raw_gpu)
        if gpu_id not in spec_pool["graphics"]:
            spec_pool["graphics"][gpu_id] = {"id": gpu_id, "raw": raw_gpu, "normalized": parse_gpu_psref(raw_gpu)}

    for raw_mem in filter_options.get("Memory") or []:
        mem_id = stable_spec_id("mem", raw_mem)
        if mem_id not in spec_pool["memory"]:
            spec_pool["memory"][mem_id] = {"id": mem_id, "raw": raw_mem, "normalized": parse_memory_psref(raw_mem)}

    for raw_sto in filter_options.get("Storage") or []:
        sto_id = stable_spec_id("sto", raw_sto)
        if sto_id not in spec_pool["storage"]:
            spec_pool["storage"][sto_id] = {"id": sto_id, "raw": raw_sto, "normalized": parse_storage_psref(raw_sto)}

    for raw_dpy in filter_options.get("Display") or []:
        dpy_id = stable_spec_id("dpy", raw_dpy)
        if dpy_id not in spec_pool["displays"]:
            spec_pool["displays"][dpy_id] = {"id": dpy_id, "raw": raw_dpy, "normalized": parse_display_psref(raw_dpy)}

    # Map model rows
    for row in prefix_rows:
        row_specs = build_specs_from_row(row)
        sku = clean_text(row.get("Model")).upper()

        proc_raw = (row_specs.get("processor") or {}).get("raw", "")
        cpu_id = stable_spec_id("cpu", proc_raw) if proc_raw else ""
        if cpu_id and cpu_id not in spec_pool["processors"]:
            spec_pool["processors"][cpu_id] = {"id": cpu_id, "raw": proc_raw, "normalized": row_specs.get("processor")}

        gpu_raw = (row_specs.get("graphics") or {}).get("raw", "")
        gpu_id = stable_spec_id("gpu", gpu_raw) if gpu_raw else ""
        if gpu_id and gpu_id not in spec_pool["graphics"]:
            spec_pool["graphics"][gpu_id] = {"id": gpu_id, "raw": gpu_raw, "normalized": row_specs.get("graphics")}

        mem_raw = (row_specs.get("memory") or {}).get("raw", "")
        mem_id = stable_spec_id("mem", mem_raw) if mem_raw else ""
        if mem_id and mem_id not in spec_pool["memory"]:
            spec_pool["memory"][mem_id] = {"id": mem_id, "raw": mem_raw, "normalized": row_specs.get("memory")}

        sto_raw = (row_specs.get("storage") or {}).get("raw", "")
        sto_id = stable_spec_id("sto", sto_raw) if sto_raw else ""
        if sto_id and sto_id not in spec_pool["storage"]:
            spec_pool["storage"][sto_id] = {"id": sto_id, "raw": sto_raw, "normalized": row_specs.get("storage")}

        dpy_raw = (row_specs.get("display") or {}).get("raw", "")
        dpy_id = stable_spec_id("dpy", dpy_raw) if dpy_raw else ""
        if dpy_id and dpy_id not in spec_pool["displays"]:
            spec_pool["displays"][dpy_id] = {"id": dpy_id, "raw": dpy_raw, "normalized": row_specs.get("display")}

        if sku:
            models[sku] = {
                "country_region": clean_text(row.get("Country/Region")),
                "match_type": "exact",
                "psref_model": sku,
                "psref_product": clean_text(row.get("Product")),
                "spec_refs": {
                    "processor": cpu_id,
                    "graphics": gpu_id,
                    "memory": mem_id,
                    "storage": sto_id,
                    "display": dpy_id,
                },
            }

    # Merge platform defaults across all model rows
    for row in prefix_rows:
        row_specs = build_specs_from_row(row)
        for key, val in row_specs.items():
            if key in ("processor", "graphics", "memory", "storage", "display"):
                continue
            if not val:
                continue
            if key not in platform_defaults:
                platform_defaults[key] = val
            elif isinstance(val, dict) and isinstance(platform_defaults[key], dict):
                for sub_k, sub_v in val.items():
                    if sub_v and not platform_defaults[key].get(sub_k):
                        platform_defaults[key][sub_k] = sub_v

    # Load complete platform specs via LoadSpecData API for full platform defaults
    product_key = mt_entry.get("product_key") or ""
    if product_key:
        api_spec_data = fetch_product_spec_data(product_key)
        for key, val in api_spec_data.items():
            if not val:
                continue
            if key not in platform_defaults:
                platform_defaults[key] = val
            elif isinstance(val, dict) and isinstance(platform_defaults[key], dict):
                for sub_k, sub_v in val.items():
                    if sub_v and not platform_defaults[key].get(sub_k):
                        platform_defaults[key][sub_k] = sub_v

    return {
        "machine_type": prefix,
        "product_key": mt_entry.get("product_key"),
        "product_name": mt_entry.get("product_name"),
        "marketing_name": mt_entry.get("marketing_name"),
        "marketing_name_primary": (mt_entry.get("marketing_name") or "").split(" / ")[0],
        "platform_code": extract_platform_code(mt_entry.get("product_key") or ""),
        "product_id": mt_entry.get("product_id"),
        "psref_href": mt_entry.get("psref_href"),
        "series_name": mt_entry.get("series_name"),
        "productline_name": mt_entry.get("productline_name"),
        "classification_name": mt_entry.get("classification_name"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filter_options": filter_options,
        "spec_pool": spec_pool,
        "platform_defaults": platform_defaults,
        "models": models,
    }


def _extract_scraped_spec_strings(product: dict[str, Any]) -> dict[str, str]:
    specs_by_code = product.get("specs_by_code") or {}
    scraped: dict[str, str] = {
        "processor": str(specs_by_code.get("LOIS_SCA_CPU") or ""),
        "graphics": str(specs_by_code.get("LOIS_SCA_VIDEO") or ""),
        "memory": str(specs_by_code.get("LOIS_SCA_MEM") or ""),
        "storage": str(specs_by_code.get("LOIS_SCA_HDD") or ""),
        "display": str(specs_by_code.get("LOIS_SCA_DPY") or ""),
    }
    specs_list = product.get("specs")
    if isinstance(specs_list, list):
        for item in specs_list:
            if not isinstance(item, dict):
                continue
            lbl = clean_text(item.get("label")).lower()
            val = clean_text(item.get("value"))
            if "processor" in lbl and not scraped["processor"]:
                scraped["processor"] = val
            elif "graphic" in lbl and not scraped["graphics"]:
                scraped["graphics"] = val
            elif "memory" in lbl and not scraped["memory"]:
                scraped["memory"] = val
            elif "storage" in lbl and not scraped["storage"]:
                scraped["storage"] = val
            elif "display" in lbl and not scraped["display"]:
                scraped["display"] = val
    return scraped


def extract_default_cto_specs(cto_payload: dict[str, Any]) -> dict[str, Any]:
    specs: dict[str, Any] = {}
    for option in cto_payload.get("options") or []:
        choices = option.get("choices") or []
        default_choice = next((c for c in choices if c.get("isDefault")), choices[0] if choices else None)
        if default_choice and default_choice.get("specs"):
            for cat, val in default_choice["specs"].items():
                if val:
                    specs[cat] = val
    return specs


def match_product_against_mt(
    product: dict[str, Any],
    mt_datasheet: dict[str, Any],
    cto_dir: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    sku = clean_text(product.get("id") or product.get("product_code")).upper()
    models = mt_datasheet.get("models") or {}
    spec_pool = mt_datasheet.get("spec_pool") or {}
    platform_defaults = mt_datasheet.get("platform_defaults") or {}

    def _apply_cto_fallback(hydrated: dict[str, Any]) -> dict[str, Any]:
        if cto_dir and ("CTO" in sku or any(not hydrated.get(f) for f in ("processor", "graphics", "memory", "storage", "display"))):
            cto_file = cto_dir / f"{sku}.json"
            if cto_file.exists():
                cto_payload = read_json(cto_file, {})
                cto_specs = extract_default_cto_specs(cto_payload)
                for field in ("processor", "graphics", "memory", "storage", "display", "wireless"):
                    if not hydrated.get(field) and cto_specs.get(field):
                        hydrated[field] = cto_specs[field]
        return hydrated

    if sku in models:
        model_entry = models[sku]
        hydrated = hydrate_sku_specs(model_entry.get("spec_refs") or {}, spec_pool, platform_defaults)
        hydrated = _apply_cto_fallback(hydrated)
        return hydrated, model_entry

    scraped_strings = _extract_scraped_spec_strings(product)
    parsed_scraped = parse_spec_codes(product.get("specs_by_code") or {})

    if cto_dir:
        cto_file = cto_dir / f"{sku}.json"
        if cto_file.exists():
            cto_payload = read_json(cto_file, {})
            cto_specs = extract_default_cto_specs(cto_payload)
            for field in ("processor", "graphics", "memory", "storage", "display"):
                if not scraped_strings.get(field) and cto_specs.get(field):
                    val = cto_specs[field]
                    if isinstance(val, dict):
                        scraped_strings[field] = val.get("full_model") or val.get("model") or val.get("raw") or ""
                    elif isinstance(val, str):
                        scraped_strings[field] = val

    spec_refs: dict[str, str] = {}

    # Match CPU
    raw_cpu = scraped_strings.get("processor")
    matched_cpu_id = ""
    if raw_cpu:
        for cpu_id, item in (spec_pool.get("processors") or {}).items():
            item_norm = item.get("normalized") or {}
            cpu_model = clean_text(item_norm.get("model") or item_norm.get("full_model"))
            scraped_model = clean_text((parsed_scraped.get("processor") or {}).get("model"))
            if cpu_model and scraped_model and (cpu_model in raw_cpu or scraped_model in item.get("raw", "")):
                matched_cpu_id = cpu_id
                break
        if not matched_cpu_id:
            parsed_cpu = parse_cpu_psref(raw_cpu)
            matched_cpu_id = stable_spec_id("cpu", raw_cpu)
            spec_pool.setdefault("processors", {})[matched_cpu_id] = {
                "id": matched_cpu_id,
                "raw": raw_cpu,
                "normalized": parsed_cpu,
            }
    spec_refs["processor"] = matched_cpu_id

    # Match GPU
    raw_gpu = scraped_strings.get("graphics")
    matched_gpu_id = ""
    if raw_gpu:
        for gpu_id, item in (spec_pool.get("graphics") or {}).items():
            item_norm = item.get("normalized") or {}
            gpu_model = clean_text(item_norm.get("model") or item_norm.get("full_model"))
            scraped_gpu = clean_text((parsed_scraped.get("graphics") or {}).get("model"))
            if gpu_model and scraped_gpu and (gpu_model in raw_gpu or scraped_gpu in item.get("raw", "")):
                matched_gpu_id = gpu_id
                break
        if not matched_gpu_id:
            parsed_gpu = parse_gpu_psref(raw_gpu)
            matched_gpu_id = stable_spec_id("gpu", raw_gpu)
            spec_pool.setdefault("graphics", {})[matched_gpu_id] = {
                "id": matched_gpu_id,
                "raw": raw_gpu,
                "normalized": parsed_gpu,
            }
    spec_refs["graphics"] = matched_gpu_id

    # Match Memory
    raw_mem = scraped_strings.get("memory")
    matched_mem_id = ""
    if raw_mem:
        for mem_id, item in (spec_pool.get("memory") or {}).items():
            if clean_text(raw_mem) in clean_text(item.get("raw", "")):
                matched_mem_id = mem_id
                break
        if not matched_mem_id:
            parsed_mem = parse_memory_psref(raw_mem)
            matched_mem_id = stable_spec_id("mem", raw_mem)
            spec_pool.setdefault("memory", {})[matched_mem_id] = {
                "id": matched_mem_id,
                "raw": raw_mem,
                "normalized": parsed_mem,
            }
    spec_refs["memory"] = matched_mem_id

    # Match Storage
    raw_sto = scraped_strings.get("storage")
    matched_sto_id = ""
    if raw_sto:
        for sto_id, item in (spec_pool.get("storage") or {}).items():
            if clean_text(raw_sto) in clean_text(item.get("raw", "")):
                matched_sto_id = sto_id
                break
        if not matched_sto_id:
            parsed_sto = parse_storage_psref(raw_sto)
            matched_sto_id = stable_spec_id("sto", raw_sto)
            spec_pool.setdefault("storage", {})[matched_sto_id] = {
                "id": matched_sto_id,
                "raw": raw_sto,
                "normalized": parsed_sto,
            }
    spec_refs["storage"] = matched_sto_id

    # Match Display
    raw_dpy = scraped_strings.get("display")
    matched_dpy_id = ""
    if raw_dpy:
        for dpy_id, item in (spec_pool.get("displays") or {}).items():
            if clean_text(raw_dpy) in clean_text(item.get("raw", "")):
                matched_dpy_id = dpy_id
                break
        if not matched_dpy_id:
            parsed_dpy = parse_display_psref(raw_dpy)
            matched_dpy_id = stable_spec_id("dpy", raw_dpy)
            spec_pool.setdefault("displays", {})[matched_dpy_id] = {
                "id": matched_dpy_id,
                "raw": raw_dpy,
                "normalized": parsed_dpy,
            }
    spec_refs["display"] = matched_dpy_id

    model_entry = {
        "country_region": "India",
        "match_type": "mt_datasheet_matched",
        "psref_model": sku,
        "psref_product": mt_datasheet.get("product_name"),
        "spec_refs": spec_refs,
    }
    models[sku] = model_entry
    hydrated = hydrate_sku_specs(spec_refs, spec_pool, platform_defaults)
    hydrated = _apply_cto_fallback(hydrated)
    return hydrated, model_entry


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


def build_final_sku_specs(entries: list[dict[str, Any]]) -> dict[str, Any]:
    final: OrderedDict[str, Any] = OrderedDict()
    for entry in entries:
        payload = OrderedDict(entry)
        final[entry["id"]] = payload
    return final


def _process_prefix(prefix: str, mt_entry: dict[str, Any], datasheets_dir: Path, refresh: bool) -> tuple[str, dict[str, Any]]:
    datasheet_path = datasheets_dir / f"{prefix}.json"
    if refresh or not datasheet_path.exists():
        try:
            rows, filter_options = fetch_mt_model_data_json(mt_entry["product_key"], mt=prefix)
        except Exception:
            rows, filter_options = [], {}

        if rows:
            prefix_rows = [row for row in rows if clean_text(row.get("Machine Type")).upper() == prefix]
            if not prefix_rows and rows:
                prefix_rows = rows
            mt_datasheet = build_mt_datasheet(prefix, prefix_rows, mt_entry, filter_options=filter_options)
            write_json(datasheet_path, mt_datasheet)
        elif datasheet_path.exists():
            mt_datasheet = read_json(datasheet_path, {})
        else:
            mt_datasheet = build_mt_datasheet(prefix, [], mt_entry, filter_options={})
            write_json(datasheet_path, mt_datasheet)
    else:
        mt_datasheet = read_json(datasheet_path, {})
    return prefix, mt_datasheet


def build(
    *,
    catalog_path: Path,
    cto_dir: Path,
    output_dir: Path,
    refresh: bool = False,
    sku_filter: set[str] | None = None,
    write_sidecars: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    raw_catalog = read_json(catalog_path, {})
    products = _iter_catalog_products(raw_catalog)
    if sku_filter:
        products = [item for item in products if clean_text(item.get("id") or item.get("product_code")).upper() in sku_filter]

    menu_cache = output_dir / "menu.json"
    mt_cache = output_dir / "machine_type_map.json"
    datasheets_dir = output_dir / "datasheets"
    sidecar_dir = output_dir / "by_sku"
    report_path = output_dir / "report.json"

    datasheets_dir.mkdir(parents=True, exist_ok=True)

    try:
        menu_payload = _request_bytes(MENU_URL, accept="application/json, text/plain, */*")
        menu_payload = json.loads(menu_payload.decode("utf-8-sig"))
        write_json(menu_cache, menu_payload)
    except Exception:
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
    datasheet_meta: dict[str, Any] = {}

    # Parallelize fetching and creation of MTM datasheets
    valid_prefixes = [(prefix, mt_map[prefix]) for prefix in sorted(grouped.keys()) if prefix in mt_map]
    datasheets: dict[str, dict[str, Any]] = {}

    if verbose:
        print(f"[psref] Fetching MTM datasheets for {len(valid_prefixes)} Machine Types in parallel (workers=8)...")

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(_process_prefix, prefix, mt_entry, datasheets_dir, refresh)
            for prefix, mt_entry in valid_prefixes
        ]
        for future in as_completed(futures):
            pfx, ds = future.result()
            datasheets[pfx] = ds

    for prefix, prefix_products in sorted(grouped.items()):
        mt_entry = mt_map.get(prefix)
        if not mt_entry:
            for product in prefix_products:
                sku = clean_text(product.get("id") or product.get("product_code")).upper()
                entry = {"id": sku, "status": "missing", "match_type": "missing_machine_type", "machine_type": prefix, "tech_specs": {}}
                results.append(entry)
                missing.append(entry)
            continue

        mt_datasheet = datasheets[prefix]
        datasheet_path = datasheets_dir / f"{prefix}.json"
        datasheet_meta[prefix] = {
            "machine_type": prefix,
            "product_key": mt_entry["product_key"],
            "product_name": mt_entry.get("product_name"),
            "model_count": len(mt_datasheet.get("models") or {}),
        }

        datasheet_updated = False
        for product in prefix_products:
            sku = clean_text(product.get("id") or product.get("product_code")).upper()
            hydrated_specs, model_entry = match_product_against_mt(product, mt_datasheet, cto_dir=cto_dir)
            match_type = model_entry.get("match_type", "mt_datasheet_matched")
            if match_type == "mt_datasheet_matched":
                datasheet_updated = True

            entry = OrderedDict(
                [
                    ("id", sku),
                    ("status", "resolved"),
                    ("match_type", match_type),
                    ("is_cto", "CTO" in sku),
                    ("custom_model", "CTO" in sku),
                    ("machine_type", prefix),
                    ("product_key", mt_entry.get("product_key")),
                    ("product_name", mt_entry.get("product_name")),
                    ("marketing_name", mt_entry.get("marketing_name")),
                    ("marketing_name_primary", (mt_entry.get("marketing_name") or "").split(" / ")[0]),
                    ("platform_code", extract_platform_code(mt_entry.get("product_key") or "")),
                    ("product_id", mt_entry.get("product_id")),
                    ("psref_href", mt_entry.get("psref_href")),
                    ("psref_product", model_entry.get("psref_product") or mt_entry.get("product_name")),
                    ("series_name", mt_entry.get("series_name")),
                    ("productline_name", mt_entry.get("productline_name")),
                    ("classification_name", mt_entry.get("classification_name")),
                    ("country_region", model_entry.get("country_region", "India")),
                    ("psref_model", model_entry.get("psref_model", sku)),
                    ("spec_refs", model_entry.get("spec_refs", {})),
                    ("tech_specs", hydrated_specs),
                ]
            )
            results.append(entry)
            if write_sidecars:
                sidecar_dir.mkdir(parents=True, exist_ok=True)
                write_json(sidecar_dir / f"{sku}.json", entry)
            if verbose:
                print(f"[psref] {sku} {match_type}")

        if datasheet_updated:
            write_json(datasheet_path, mt_datasheet)

    final_path = output_dir / "final_sku_specs.json"
    existing_sidecar = read_json(final_path, {}) if final_path.exists() else {}
    new_sku_specs = build_final_sku_specs(results)

    if not results and existing_sidecar:
        print("[psref] No results to append; preserving existing final_sku_specs.json and inventory.")
        existing_total = len(existing_sidecar)
        existing_resolved = sum(1 for entry in existing_sidecar.values() if isinstance(entry, dict) and entry.get("status") == "resolved")
        existing_missing = sum(1 for entry in existing_sidecar.values() if isinstance(entry, dict) and entry.get("status") == "missing")
        return {"total": existing_total, "resolved": existing_resolved, "missing": existing_missing}

    merged_sidecar = {**existing_sidecar, **new_sku_specs}
    inventory = build_inventory(list(merged_sidecar.values()))

    write_json(output_dir / "inventory.json", inventory)
    write_json(final_path, merged_sidecar)
    write_json(
        report_path,
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "catalog": str(catalog_path),
            "total": len(merged_sidecar),
            "resolved": sum(1 for entry in merged_sidecar.values() if isinstance(entry, dict) and entry.get("status") == "resolved"),
            "missing": sum(1 for entry in merged_sidecar.values() if isinstance(entry, dict) and entry.get("status") == "missing"),
            "match_types": dict(Counter((entry.get("match_type") or "unknown") for entry in merged_sidecar.values() if isinstance(entry, dict))),
            "datasheets": {**(read_json(report_path, {}).get("datasheets") or {}), **datasheet_meta},
            "missing_entries": [entry for entry in merged_sidecar.values() if isinstance(entry, dict) and entry.get("status") == "missing"],
            "new_skus": sorted(set(new_sku_specs) - set(existing_sidecar)),
        },
    )
    merged_resolved = sum(1 for entry in merged_sidecar.values() if isinstance(entry, dict) and entry.get("status") == "resolved")
    merged_missing = sum(1 for entry in merged_sidecar.values() if isinstance(entry, dict) and entry.get("status") == "missing")
    print(f"Built PSREF specs: {merged_resolved}/{len(merged_sidecar)} resolved in sidecar ({len(new_sku_specs)} new this run)")
    return {"total": len(merged_sidecar), "resolved": merged_resolved, "missing": merged_missing}


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
