#!/usr/bin/env python3
"""
Dynamic Intel Mobile CPU & iGPU Hardware Inventory Builder.

Eliminates redundant rollup JSON files. Creates ONLY clean standalone individual processor files,
individual iGPU files, and master inventory files:
  - data/inventory/intel/cpus/<series_slug>/<code_name_slug>/<cpu_model_slug>.json
  - data/inventory/intel/igpus/<igpu_series_slug>/<igpu_model_slug>.json
  - data/intel_cpu_inventory.json (master standalone CPU inventory)
  - data/intel_igpu_inventory.json (master standalone iGPU inventory)
"""

from __future__ import annotations

import json
import random
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from curl_cffi import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.scripts.clean_hardware_inventory import clean_all_hardware_inventories

# Paths
DATA_DIR = REPO_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache" / "intel_specs"
INTEL_INVENTORY_DIR = DATA_DIR / "inventory" / "intel"
INTEL_CPUS_DIR = INTEL_INVENTORY_DIR / "cpus"
INTEL_IGPUS_DIR = INTEL_INVENTORY_DIR / "igpus"
MASTER_CPU_INVENTORY = DATA_DIR / "intel_cpu_inventory.json"
MASTER_IGPU_INVENTORY = DATA_DIR / "intel_igpu_inventory.json"

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_Intel_Core_mobile_processors"

# TLS Impersonation & Browser Header Rotation Pool
PROFILES = [
    {
        "impersonate": "chrome142",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Not(A:Brand";v="99", "Chromium";v="142", "Google Chrome";v="142"'
    },
    {
        "impersonate": "chrome136",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Not(A:Brand";v="99", "Chromium";v="136", "Google Chrome";v="136"'
    },
    {
        "impersonate": "chrome124",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Not-A.Brand";v="99", "Chromium";v="124", "Google Chrome";v="124"'
    },
    {
        "impersonate": "firefox144",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0",
        "sec_ch_ua": None
    }
]


def clean_text(val: Any) -> str:
    if not val:
        return ""
    text = str(val).replace("[™®]", "").replace("™", "").replace("®", "").replace("\xa0", " ")
    text = re.sub(r"\[[0-9a-zA-Z]+\]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def slugify(text: str) -> str:
    s = clean_text(text).lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def derive_intel_series(full_model: str, h2_title: str, h3_title: str) -> tuple[str, str]:
    low_m = full_model.lower()
    low_h2 = h2_title.lower()
    low_h3 = h3_title.lower()

    if "ultra" in low_m or "ultra" in low_h2:
        if "series 1" in low_h2 or "meteor lake" in low_h3:
            return "Intel Core Ultra Series 1", "core_ultra_series_1"
        elif "series 2" in low_h2 or "lunar lake" in low_h3 or "arrow lake" in low_h3:
            return "Intel Core Ultra Series 2", "core_ultra_series_2"
        elif "series 3" in low_h2 or "panther lake" in low_h3:
            return "Intel Core Ultra Series 3", "core_ultra_series_3"
        return "Intel Core Ultra Series", "core_ultra_series"

    if re.search(r"\bcore\s+[3579]\s+1\d{2}[u|h|hx]?\b", low_m, re.I) or ("series 1" in low_h2 and "ultra" not in low_m):
        return "Intel Core Series 1", "core_series_1"
    if re.search(r"\bcore\s+[3579]\s+2\d{2}[u|h|hx]?\b", low_m, re.I) or ("series 2" in low_h2 and "ultra" not in low_m):
        return "Intel Core Series 2", "core_series_2"
    if re.search(r"\bcore\s+[3579]\s+3\d{2}[u|h|hx]?\b", low_m, re.I) or ("series 3" in low_h2 and "ultra" not in low_m):
        return "Intel Core Series 3", "core_series_3"

    m_gen = re.search(r"(\d+)(?:st|nd|rd|th)\s+generation", low_h2)
    if m_gen:
        g = m_gen.group(1)
        return f"{g}th Generation Intel Core", f"{g}th_generation_intel_core"

    clean_h2 = clean_text(h2_title.replace("microarchitecture", "").replace("(mobile)", ""))
    return clean_h2 or "Intel Core Processors", slugify(clean_h2 or "Intel Core Processors")


def parse_year_month(date_str: str) -> tuple[int | None, int | None, str]:
    text = clean_text(date_str)
    if not text:
        return None, None, ""

    year = None
    month = None

    y_match = re.search(r"\b(20\d{2}|19\d{2})\b", text)
    if y_match:
        year = int(y_match.group(1))

    months_map = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "june": 6, "jun": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
        "nov": 11, "november": 11, "dec": 12, "december": 12
    }
    q_map = {"q1": 1, "q2": 4, "q3": 7, "q4": 10}

    for m_name, m_num in months_map.items():
        if re.search(r"\b" + m_name + r"\b", text, re.I):
            month = m_num
            break

    if month is None:
        for q_name, q_num in q_map.items():
            if re.search(r"\b" + q_name + r"\b", text, re.I) or q_name.upper() in text:
                month = q_num
                break

    return year, month, text


def derive_igpu_series(igpu_model: str) -> tuple[str, str]:
    text = clean_text(igpu_model)
    low_text = text.lower()
    if not text or low_text in ["none", "n/a", "-", "—n/a"]:
        return "Integrated Graphics", "integrated_graphics"
    
    if any(k in low_text for k in ["140v", "130v", "arc v"]):
        return "Intel Arc V-Series", "intel_arc_v_series"
    if "arc" in low_text or "b390" in low_text or "140t" in low_text or "130t" in low_text:
        return "Intel Arc Graphics Series", "intel_arc_graphics_series"
    if "iris" in low_text:
        return "Intel Iris Xe Series", "intel_iris_xe_series"
    if any(k in low_text for k in ["uhd", "620", "630", "hd graphics", "intel graphics"]):
        return "Intel UHD Graphics Series", "intel_uhd_graphics_series"
        
    return f"{text} Series", slugify(f"{text}_series")


def extract_sku_id_from_url(url: str) -> str:
    match = re.search(r"/products/(?:sku/)?(\d+)", url)
    return match.group(1) if match else ""


def parse_intel_page_specs_and_sections_by_h3(html_text: str) -> dict[str, dict[str, str]]:
    soup = BeautifulSoup(html_text, "html.parser")
    sections_dict: dict[str, dict[str, str]] = {}

    for label_div in soup.find_all("div", class_=re.compile(r"tech-label|label", re.I)):
        label = clean_text(label_div.get_text().replace("‡", ""))
        next_div = label_div.find_next_sibling("div")
        if not next_div and label_div.parent:
            next_div = label_div.parent.find("div", class_=re.compile(r"tech-data|value", re.I))
        val = clean_text(next_div.get_text()) if next_div else ""
        
        if label and val:
            prev_h = label_div.find_previous(["h3", "h2"])
            sec_name = clean_text(prev_h.get_text()) if prev_h else "Essentials"
            if not sec_name or "intel" in sec_name.lower() or sec_name.lower() in ["specifications", "feedback"]:
                sec_name = "Essentials"
            sections_dict.setdefault(sec_name, {})[label] = val

    for tr in soup.find_all("tr"):
        tds = tr.find_all(["td", "th"])
        if len(tds) >= 2:
            k = clean_text(tds[0].get_text().replace("‡", ""))
            v = clean_text(tds[1].get_text())
            if k and v and len(k) < 100:
                prev_h = tr.find_previous(["h3", "h2"])
                sec_name = clean_text(prev_h.get_text()) if prev_h else "Essentials"
                if not sec_name or "intel" in sec_name.lower() or sec_name.lower() in ["specifications", "feedback"]:
                    sec_name = "Essentials"
                sections_dict.setdefault(sec_name, {})[k] = v

    return sections_dict


_RATE_LOCK = threading.Lock()
_RATE_LAST_TS = 0.0
RATE_MIN_INTERVAL = 0.35  # seconds between requests, enforced globally across the whole worker pool


def _throttle_request() -> None:
    """Sleep as needed so the combined worker pool never exceeds ~3 requests/sec."""
    global _RATE_LAST_TS
    with _RATE_LOCK:
        now = time.monotonic()
        wait = RATE_MIN_INTERVAL - (now - _RATE_LAST_TS)
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _RATE_LAST_TS = now


def fetch_intel_page_specs_robust(url: str) -> dict[str, dict[str, str]]:
    sku_id = extract_sku_id_from_url(url)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    if sku_id:
        cache_file = CACHE_DIR / f"{sku_id}.json"
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                    if cached_data and isinstance(cached_data, dict) and "intel_specs" in cached_data:
                        return cached_data["intel_specs"]
                    elif cached_data and isinstance(cached_data, dict) and "sections_dict" in cached_data:
                        return cached_data["sections_dict"]
            except Exception:
                pass

    candidate_urls = []
    if sku_id:
        candidate_urls.append(f"https://www.intel.com/content/www/us/en/products/sku/{sku_id}/specifications.html")
    candidate_urls.append(url)

    for c_url in candidate_urls:
        for attempt in range(5):
            prof = random.choice(PROFILES)
            s = requests.Session(impersonate=prof["impersonate"])
            headers = {
                "User-Agent": prof["user_agent"],
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.google.com/",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "cross-site",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1"
            }
            if prof["sec_ch_ua"]:
                headers["Sec-Ch-Ua"] = prof["sec_ch_ua"]
                headers["Sec-Ch-Ua-Mobile"] = "?0"
                headers["Sec-Ch-Ua-Platform"] = '"Windows"'

            try:
                _throttle_request()
                r = s.get(c_url, headers=headers, timeout=25, allow_redirects=True)
                if r.status_code in (429, 503):
                    retry_after = 0
                    try:
                        retry_after = int(r.headers.get("Retry-After", "0") or 0)
                    except ValueError:
                        retry_after = 0
                    time.sleep(max(retry_after, 4 * (attempt + 1)) + random.uniform(0, 2))
                    continue
                if r.status_code == 200 and "Access Denied" not in r.text and "404Redirector" not in r.url:
                    sections_dict = parse_intel_page_specs_and_sections_by_h3(r.text)
                    if len(sections_dict) > 0:
                        if sku_id:
                            cache_file = CACHE_DIR / f"{sku_id}.json"
                            with open(cache_file, "w", encoding="utf-8") as f:
                                json.dump({"intel_specs": sections_dict}, f, indent=2)
                        return sections_dict
            except Exception:
                pass

            time.sleep(random.uniform(0.2, 0.4))

    return {}


def parse_wikitable_grid_with_schema(table_elem: Any) -> tuple[list[str], list[list[dict[str, Any] | None]]]:
    grid: list[list[dict[str, Any] | None]] = []
    rows = table_elem.find_all("tr")
    
    for r_idx, tr in enumerate(rows):
        if r_idx >= len(grid):
            grid.append([])
        col_idx = 0
        for cell in tr.find_all(["th", "td"]):
            while col_idx < len(grid[r_idx]) and grid[r_idx][col_idx] is not None:
                col_idx += 1

            rowspan = int(cell.get("rowspan", 1))
            colspan = int(cell.get("colspan", 1))
            text = clean_text(cell.get_text())
            is_header = cell.name == "th"
            links = [a["href"] for a in cell.find_all("a", href=True)]
            cell_data = {"text": text, "links": links, "is_header": is_header}

            for r_offset in range(rowspan):
                curr_r = r_idx + r_offset
                while len(grid) <= curr_r:
                    grid.append([])
                for c_offset in range(colspan):
                    curr_c = col_idx + c_offset
                    while len(grid[curr_r]) <= curr_c:
                        grid[curr_r].append(None)
                    grid[curr_r][curr_c] = cell_data

            col_idx += colspan

    if not grid:
        return [], []

    header_row_count = 0
    for r in rows:
        r_text_low = clean_text(r.get_text()).lower()
        is_header_row = any(attr in r_text_low for attr in ["processor branding", "processorbranding", "clock rate", "smartcache", "release date", "releasedate"]) and not re.search(r"\b\d{3}[u|h|v|hx]?\b", r_text_low)
        if is_header_row:
            header_row_count += 1
        else:
            break
            
    if header_row_count == 0:
        header_row_count = 1

    max_cols = max(len(row) for row in grid[:header_row_count]) if grid else 0
    column_schema = []
    
    for c in range(max_cols):
        header_parts = []
        for r in range(min(header_row_count, len(grid))):
            if c < len(grid[r]):
                cell = grid[r][c]
                if cell and cell["text"]:
                    t = cell["text"]
                    if t not in header_parts and not t.isdigit():
                        header_parts.append(t)
        column_schema.append(" > ".join(header_parts))

    data_rows = grid[header_row_count:]
    return column_schema, data_rows


def parse_wikipedia_schema_row(row_map: dict[str, str]) -> dict[str, Any]:
    cores_info = {
        "total_cores": None,
        "total_threads": None,
        "performance_cores": None,
        "efficient_cores": None,
        "low_power_efficient_cores": None
    }
    
    freq_info = {
        "base_clock_ghz": None,
        "boost_clock_ghz": None,
        "p_core_base_ghz": None,
        "p_core_max_turbo_ghz": None,
        "e_core_base_ghz": None,
        "e_core_max_turbo_ghz": None
    }

    cache_info = {
        "l3_cache": None,
        "l2_cache": None
    }

    power_info = {
        "processor_base_power": None,
        "maximum_turbo_power": None
    }

    launch_date = ""
    igpu_data = {
        "model": "",
        "short_model": "",
        "series": "Integrated Graphics",
        "series_slug": "integrated_graphics",
        "base_frequency_mhz": None,
        "max_dynamic_frequency_mhz": None,
        "clock_mhz": "",
        "units": ""
    }

    for k, v in row_map.items():
        low_k = k.lower().strip()
        v_clean = clean_text(v)
        if not v_clean or v_clean in ["—N/a", "N/A", "-", "—", "Unknown", "?"]:
            continue

        if any(w in low_k for w in ["launch", "release", "date"]):
            launch_date = v_clean

        # Cores & Threads (Avoid clock speeds)
        if ("core" in low_k or "thread" in low_k) and not any(w in low_k for w in ["clock", "ghz", "mhz", "fillrate", "frequency"]):
            if "p-core" in low_k or "performance" in low_k:
                m_p = re.search(r"\b(\d+)\b", v_clean)
                if m_p: cores_info["performance_cores"] = int(m_p.group(1))
            elif "e-core" in low_k or "efficient" in low_k:
                m_e = re.search(r"\b(\d+)\b", v_clean)
                if m_e: cores_info["efficient_cores"] = int(m_e.group(1))
            elif "lpe-core" in low_k or "low power" in low_k:
                m_lp = re.search(r"\b(\d+)\b", v_clean)
                if m_lp: cores_info["low_power_efficient_cores"] = int(m_lp.group(1))
            elif "cores" in low_k or "threads" in low_k:
                parts = re.findall(r"\d+", v_clean)
                if len(parts) >= 2:
                    cores_info["total_cores"] = int(parts[0])
                    cores_info["total_threads"] = int(parts[1])
                elif len(parts) == 1:
                    cores_info["total_cores"] = int(parts[0])

        # Clocks
        if "clock" in low_k or "ghz" in low_k or "mhz" in low_k:
            if "p-core" in low_k:
                nums = re.findall(r"(\d+(?:\.\d+)?)", v_clean)
                if len(nums) >= 2:
                    freq_info["p_core_base_ghz"] = nums[0] + " GHz"
                    freq_info["p_core_max_turbo_ghz"] = nums[1] + " GHz"
                elif len(nums) == 1:
                    freq_info["p_core_max_turbo_ghz"] = nums[0] + " GHz"
            elif "e-core" in low_k:
                nums = re.findall(r"(\d+(?:\.\d+)?)", v_clean)
                if len(nums) >= 2:
                    freq_info["e_core_base_ghz"] = nums[0] + " GHz"
                    freq_info["e_core_max_turbo_ghz"] = nums[1] + " GHz"
                elif len(nums) == 1:
                    freq_info["e_core_max_turbo_ghz"] = nums[0] + " GHz"
            elif "gpu" not in low_k:
                nums = re.findall(r"(\d+(?:\.\d+)?)", v_clean)
                if len(nums) >= 2:
                    freq_info["base_clock_ghz"] = nums[0] + " GHz"
                    freq_info["boost_clock_ghz"] = nums[1] + " GHz"
                elif len(nums) == 1:
                    freq_info["boost_clock_ghz"] = nums[0] + " GHz"

        # Cache
        if "cache" in low_k or "l3" in low_k or "smartcache" in low_k:
            cache_info["l3_cache"] = v_clean

        # Power / TDP
        if "tdp" in low_k or "power" in low_k or "watt" in low_k:
            if "base" in low_k:
                power_info["processor_base_power"] = v_clean
            elif "turbo" in low_k or "max" in low_k:
                power_info["maximum_turbo_power"] = v_clean
            elif not power_info["processor_base_power"]:
                power_info["processor_base_power"] = v_clean

        # iGPU
        if "gpu" in low_k or "graphics" in low_k or "arc" in low_k or "iris" in low_k:
            if "clock" in low_k or "mhz" in low_k:
                igpu_data["clock_mhz"] = v_clean
                m_freq = re.search(r"(\d{3,4})", v_clean)
                if m_freq: igpu_data["max_dynamic_frequency_mhz"] = int(m_freq.group(1))
            else:
                igpu_data["model"] = v_clean
                s_name, s_slug = derive_igpu_series(v_clean)
                igpu_data["series"] = s_name
                igpu_data["series_slug"] = s_slug

    if (cores_info["performance_cores"] is not None or cores_info["efficient_cores"] is not None) and not cores_info["total_cores"]:
        p = cores_info["performance_cores"] or 0
        e = cores_info["efficient_cores"] or 0
        lp = cores_info["low_power_efficient_cores"] or 0
        cores_info["total_cores"] = p + e + lp

    return {
        "cores": cores_info,
        "clock_speeds": freq_info,
        "cache": cache_info,
        "power": power_info,
        "launch_date": launch_date,
        "igpu": igpu_data
    }


def parse_wikipedia_dom_hierarchy() -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    print(f"[wiki] Fetching {WIKI_URL}...")
    req = requests.get(WIKI_URL, headers={"User-Agent": PROFILES[0]["user_agent"]}, impersonate="chrome124", timeout=30)
    if req.status_code != 200:
        raise RuntimeError(f"Failed to fetch Wikipedia page: HTTP {req.status_code}")
        
    soup = BeautifulSoup(req.text, "html.parser")
    processors: list[dict[str, Any]] = []
    igpus_dict: dict[str, list[dict[str, Any]]] = {}
    
    content_div = soup.find("div", id="mw-content-text")
    if not content_div:
        raise RuntimeError("Could not find div#mw-content-text in Wikipedia HTML.")
        
    current_h2_title = ""
    current_h3_title = ""
    current_h4_title = ""
    
    for elem in content_div.find_all(["h2", "h3", "h4", "table"]):
        if elem.name == "h2":
            title = clean_text(elem.get_text().replace("[edit]", ""))
            if title in ["Contents", "See also", "References", "External links", "Notes"]:
                current_h2_title = ""
                continue
            current_h2_title = title
            current_h3_title = ""
            current_h4_title = ""
        elif elem.name == "h3" and current_h2_title:
            current_h3_title = clean_text(elem.get_text().replace("[edit]", ""))
            current_h4_title = ""
        elif elem.name == "h4" and current_h2_title:
            current_h4_title = clean_text(elem.get_text().replace("[edit]", ""))
        elif elem.name == "table" and "wikitable" in elem.get("class", []) and current_h2_title:
            schema, data_rows = parse_wikitable_grid_with_schema(elem)
            if not data_rows or not schema:
                continue
                
            for row in data_rows:
                if not row:
                    continue
                    
                row_map = {schema[c]: row[c]["text"] if c < len(row) and row[c] else "" for c in range(len(schema))}
                row_links = [l for c in row if c for l in c["links"]]
                ark_link = next((l for l in row_links if "intel.com" in l or "ark.intel" in l), "")
                if ark_link and not ark_link.startswith("http"):
                    ark_link = urljoin("https://en.wikipedia.org", ark_link)

                col0 = row_map.get(schema[0], "") if len(schema) > 0 else ""
                col1 = row_map.get(schema[1], "") if len(schema) > 1 else ""
                
                sku_candidate = f"{col0} {col1}".strip()
                sku_match = re.search(r"\b((?:Intel\s+)?(?:Core\s+(?:Ultra\s+)?[X]?[3579]\s+[A-Z0-9]+|Core\s+Ultra\s+\d+|i[3579][-\s]?\d{4,5}[A-Z0-9]*|N\d{3}))\b", sku_candidate, re.I)
                if not sku_match:
                    continue
                    
                matched_sku = sku_match.group(1).strip()
                full_model = f"Intel {matched_sku}" if not matched_sku.startswith("Intel") else matched_sku

                full_model = re.sub(r"\s+", " ", full_model).strip()
                short_model = full_model.replace("Intel ", "").strip()

                series_name, series_slug = derive_intel_series(full_model, current_h2_title, current_h3_title)
                
                code_name = current_h3_title or current_h4_title or current_h2_title
                code_name_slug = slugify(code_name)

                parsed_specs = parse_wikipedia_schema_row(row_map)
                year, month, raw_date = parse_year_month(parsed_specs["launch_date"])
                
                proc_entry = {
                    "full_model": full_model,
                    "short_model": short_model,
                    "brand": "Intel",
                    "series": series_name,
                    "series_slug": series_slug,
                    "code_name": code_name,
                    "code_name_slug": code_name_slug,
                    "wikipedia_h2_series": current_h2_title,
                    "wikipedia_h3_codename": current_h3_title,
                    "wikipedia_h4_subcodename": current_h4_title,
                    "launch_date": raw_date,
                    "launch_year": year,
                    "launch_month": month,
                    "intel_ark_url": ark_link,
                    "cores": parsed_specs["cores"],
                    "clock_speeds": parsed_specs["clock_speeds"],
                    "cache": parsed_specs["cache"],
                    "power": parsed_specs["power"],
                    "igpu": parsed_specs["igpu"],
                    "wikipedia_schema_map": row_map
                }
                
                processors.append(proc_entry)
                
                igpu = parsed_specs["igpu"]
                if igpu["model"]:
                    i_slug = igpu["series_slug"]
                    igpus_dict.setdefault(i_slug, []).append({
                        "igpu_model": igpu["model"],
                        "short_model": igpu["short_model"],
                        "series": igpu["series"],
                        "series_slug": i_slug,
                        "cpu_full_model": full_model,
                        "cpu_series": series_name,
                        "cpu_code_name": code_name,
                        "base_frequency_mhz": igpu["base_frequency_mhz"],
                        "max_dynamic_frequency_mhz": igpu["max_dynamic_frequency_mhz"],
                        "clock_mhz": igpu["clock_mhz"],
                        "units": igpu["units"]
                    })

    return processors, igpus_dict


def process_intel_ark_enrichment(proc: dict[str, Any]) -> dict[str, Any]:
    url = proc.get("intel_ark_url")
    if not url:
        return proc

    intel_sections = fetch_intel_page_specs_robust(url)
    if not intel_sections:
        return proc

    proc["intel_official_specs"] = intel_sections
    proc["intel_specs"] = intel_sections

    intel_specs: dict[str, str] = {}
    for sec_name, kv in intel_sections.items():
        for k, v in kv.items():
            intel_specs[k] = v

    launch_date_str = intel_specs.get("Launch Date") or proc.get("launch_date") or ""
    year, month, _ = parse_year_month(launch_date_str)
    if year: proc["launch_year"] = year
    if month: proc["launch_month"] = month
    if launch_date_str: proc["launch_date"] = launch_date_str

    if intel_specs.get("Code Name"):
        proc["code_name"] = clean_text(intel_specs["Code Name"].replace("Products formerly", ""))

    total_cores = int(intel_specs.get("Total Cores")) if intel_specs.get("Total Cores", "").isdigit() else proc["cores"]["total_cores"]
    total_threads = int(intel_specs.get("Total Threads")) if intel_specs.get("Total Threads", "").isdigit() else proc["cores"]["total_threads"]
    p_cores = int(intel_specs.get("# of Performance-cores")) if intel_specs.get("# of Performance-cores", "").isdigit() else proc["cores"]["performance_cores"]
    e_cores = int(intel_specs.get("# of Efficient-cores")) if intel_specs.get("# of Efficient-cores", "").isdigit() else proc["cores"]["efficient_cores"]
    lpe_cores = int(intel_specs.get("# of Low Power Efficient-cores")) if intel_specs.get("# of Low Power Efficient-cores", "").isdigit() else proc["cores"]["low_power_efficient_cores"]

    proc["cores"] = {
        "total_cores": total_cores,
        "total_threads": total_threads,
        "performance_cores": p_cores,
        "efficient_cores": e_cores,
        "low_power_efficient_cores": lpe_cores
    }

    proc["clock_speeds"] = {
        "base_clock_ghz": intel_specs.get("Processor Base Frequency") or proc["clock_speeds"].get("base_clock_ghz"),
        "boost_clock_ghz": intel_specs.get("Max Turbo Frequency") or proc["clock_speeds"].get("boost_clock_ghz"),
        "p_core_base_ghz": intel_specs.get("Performance-core Base Frequency") or proc["clock_speeds"].get("p_core_base_ghz"),
        "p_core_max_turbo_ghz": intel_specs.get("Performance-core Max Turbo Frequency") or proc["clock_speeds"].get("p_core_max_turbo_ghz"),
        "e_core_base_ghz": intel_specs.get("Efficient-core Base Frequency") or proc["clock_speeds"].get("e_core_base_ghz"),
        "e_core_max_turbo_ghz": intel_specs.get("Efficient-core Max Turbo Frequency") or proc["clock_speeds"].get("e_core_max_turbo_ghz"),
        "lpe_core_base_ghz": intel_specs.get("Low Power Efficient-core Base Frequency"),
        "lpe_core_max_turbo_ghz": intel_specs.get("Low Power Efficient-core Max Turbo Frequency")
    }

    l3_c = intel_specs.get("Cache") or intel_specs.get("Intel® Smart Cache") or proc["cache"].get("l3_cache")
    proc["cache"]["l3_cache"] = l3_c

    proc["power"] = {
        "processor_base_power": intel_specs.get("Processor Base Power") or intel_specs.get("TDP") or proc["power"].get("processor_base_power"),
        "maximum_turbo_power": intel_specs.get("Maximum Turbo Power") or proc["power"].get("maximum_turbo_power"),
        "minimum_assured_power": intel_specs.get("Minimum Assured Power")
    }

    proc["memory"] = {
        "types": intel_specs.get("Memory Types"),
        "max_channels": intel_specs.get("Max # of Memory Channels"),
        "max_memory_size": intel_specs.get("Max Memory Size (dependent on memory type)")
    }

    igpu_name = intel_specs.get("GPU Name") or proc["igpu"].get("model") or ""
    series_name, series_slug = derive_igpu_series(igpu_name)
    
    proc["igpu"] = {
        "model": igpu_name,
        "short_model": igpu_name.split("(")[0].strip() if igpu_name else proc["igpu"].get("short_model"),
        "series": series_name,
        "series_slug": series_slug,
        "base_frequency_mhz": intel_specs.get("Graphics Base Frequency") or proc["igpu"].get("base_frequency_mhz"),
        "max_dynamic_frequency": intel_specs.get("Graphics Max Dynamic Frequency") or proc["igpu"].get("max_dynamic_frequency_mhz"),
        "execution_units": intel_specs.get("Execution Units") or intel_specs.get("Graphics Execution Units") or proc["igpu"].get("units"),
        "xe_cores": intel_specs.get("Xe-cores") or intel_specs.get("Graphics Xe-cores")
    }

    return proc


def build_intel_inventory(parallel_workers: int = 16) -> dict[str, Any]:
    print("=== Overhauling Intel Mobile CPU & iGPU Hardware Inventory (Clean Non-Redundant Structure) ===")
    
    if INTEL_CPUS_DIR.exists():
        shutil.rmtree(INTEL_CPUS_DIR)
    if INTEL_IGPUS_DIR.exists():
        shutil.rmtree(INTEL_IGPUS_DIR)

    INTEL_CPUS_DIR.mkdir(parents=True, exist_ok=True)
    INTEL_IGPUS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    processors, igpu_groups = parse_wikipedia_dom_hierarchy()
    print(f"[wiki] Discovered and structured {len(processors)} Intel mobile processor entries.")

    deduped_cpus: dict[str, dict[str, Any]] = {}
    for p in processors:
        key = f"{p['series_slug']}_{p['code_name_slug']}_{slugify(p['full_model'])}"
        if key not in deduped_cpus or (p.get("intel_ark_url") and not deduped_cpus[key].get("intel_ark_url")):
            deduped_cpus[key] = p

    print(f"[dedupe] Total unique Intel mobile processor entries: {len(deduped_cpus)}")

    ark_targets = [p for p in deduped_cpus.values() if p.get("intel_ark_url")]
    print(f"[scrape-pool] Processing official Intel Product Pages for {len(ark_targets)} processors...")
    
    start_time = time.time()
    enriched_procs_map: dict[str, dict[str, Any]] = {}
    
    with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
        future_map = {executor.submit(process_intel_ark_enrichment, proc): proc["full_model"] for proc in ark_targets}
        success_count = 0
        for future in as_completed(future_map):
            model_name = future_map[future]
            try:
                enriched = future.result()
                key = f"{enriched['series_slug']}_{enriched['code_name_slug']}_{slugify(enriched['full_model'])}"
                enriched_procs_map[key] = enriched
                if enriched.get("intel_specs"):
                    success_count += 1
            except Exception:
                pass

    duration = time.time() - start_time
    print(f"[scrape-pool] Finished in {duration:.2f} seconds.")

    for key, enriched in enriched_procs_map.items():
        deduped_cpus[key] = enriched

    hierarchy: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for p in deduped_cpus.values():
        s_slug = p["series_slug"]
        c_slug = p["code_name_slug"]
        hierarchy.setdefault(s_slug, {}).setdefault(c_slug, []).append(p)

    cpu_file_count = 0
    for s_slug, codenames in hierarchy.items():
        s_dir = INTEL_CPUS_DIR / s_slug
        s_dir.mkdir(parents=True, exist_ok=True)
        
        for c_slug, procs in codenames.items():
            c_dir = s_dir / c_slug
            c_dir.mkdir(parents=True, exist_ok=True)
            
            for p in procs:
                m_slug = slugify(p["full_model"])
                if not m_slug: continue
                file_path = c_dir / f"{m_slug}.json"
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(p, f, indent=2)
                cpu_file_count += 1

    print(f"[storage] Cleanly created {len(hierarchy)} CPU series folders ({cpu_file_count} individual CPU files) in {INTEL_CPUS_DIR}.")

    igpu_file_count = 0
    for i_slug, igpus in igpu_groups.items():
        i_dir = INTEL_IGPUS_DIR / i_slug
        i_dir.mkdir(parents=True, exist_ok=True)
        
        deduped_igpus: dict[str, dict[str, Any]] = {}
        for ig in igpus:
            g_slug = slugify(f"{ig['igpu_model']}_{ig['max_dynamic_frequency_mhz'] or ''}")
            if g_slug and g_slug not in deduped_igpus:
                deduped_igpus[g_slug] = ig
                
        for g_slug, ig in deduped_igpus.items():
            file_path = i_dir / f"{g_slug}.json"
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(ig, f, indent=2)
            igpu_file_count += 1

    print(f"[storage] Cleanly created {len(igpu_groups)} iGPU series folders ({igpu_file_count} individual iGPU files) in {INTEL_IGPUS_DIR}.")

    master_cpu_data = {"processors": {f"{p['series_slug']}_{p['code_name_slug']}_{slugify(p['full_model'])}": p for p in deduped_cpus.values()}}
    with open(MASTER_CPU_INVENTORY, "w", encoding="utf-8") as f:
        json.dump(master_cpu_data, f, indent=2)
    print(f"[master] Saved master CPU inventory: {MASTER_CPU_INVENTORY}")

    # Post-process & sanitize all hardware inventories (AMD, Intel, NVIDIA)
    clean_all_hardware_inventories()

    # Auto-clean scratch cache directory
    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)
        print(f"[cache-cleanup] Auto-cleaned scratch cache directory: {CACHE_DIR}")

    return {
        "unique_processors": len(deduped_cpus),
        "categorized_official_sections": success_count,
        "cpu_series_count": len(hierarchy),
        "igpu_series_count": len(igpu_groups),
        "cpu_file_count": cpu_file_count,
        "igpu_file_count": igpu_file_count
    }


if __name__ == "__main__":
    res = build_intel_inventory(parallel_workers=4)
    print("=== Clean Dynamic Intel Inventory Generation Complete ===")
    print(res)
