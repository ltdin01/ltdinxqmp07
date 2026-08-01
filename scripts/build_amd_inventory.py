#!/usr/bin/env python3
"""
100% Dynamic AMD Mobile CPU & iGPU Hardware Inventory Builder.

Mirrors the Intel pipeline (build_intel_inventory.py): fully dynamic, zero hardcoded
series/CPU dictionaries. Everything is derived from the Wikipedia DOM:
  - Series/codename names come from the live Wikipedia h2/h3/h4 hierarchy (Mobile section only).
  - Full model names come from the Wikipedia-provided AMD product page URL slug when present,
    otherwise from the generic table cell pattern.
  - Individual spec enrichment uses the Wikipedia-provided AMD product page link when available
    (Ryzen 7000 series and newer), falling back to the official AMD driver support page link
    (older Ryzen 2000-6000 series).

Output Structure:
  - data/inventory/amd/cpus/<series_slug>/<code_name_slug>/<cpu_model_slug>.json
  - data/inventory/amd/igpus/<igpu_series_slug>/<igpu_model_slug>.json
  - data/amd_cpu_inventory.json (master standalone AMD CPU inventory)
  - data/amd_igpu_inventory.json (master standalone AMD iGPU inventory)
"""

from __future__ import annotations

import hashlib
import html as _html
import json
import random
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import sys
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.scripts.clean_hardware_inventory import clean_all_hardware_inventories
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from curl_cffi import requests

# Paths
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache" / "amd_specs"
AMD_INVENTORY_DIR = DATA_DIR / "inventory" / "amd"
AMD_CPUS_DIR = AMD_INVENTORY_DIR / "cpus"
AMD_IGPUS_DIR = AMD_INVENTORY_DIR / "igpus"
MASTER_AMD_CPU_INVENTORY = DATA_DIR / "amd_cpu_inventory.json"
MASTER_AMD_IGPU_INVENTORY = DATA_DIR / "amd_igpu_inventory.json"

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_AMD_Ryzen_processors"

# Official AMD processor specifications page. The full list is embedded as a
# `data-json` attribute on #product-specs-table; the individual product pages
# referenced there carry the canonical Series / Former Codename names.
AMD_SPECS_URL = "https://www.amd.com/en/products/specifications/processors.html"
AMD_SPECS_CACHE = DATA_DIR / "cache" / "amd_specs_endpoint.json"

# Families that belong in the mobile inventory. Form factors are filtered by the
# 'laptop' keyword (there are multiple form-factor labels carrying the word
# 'Laptops', e.g. 'Laptops + Desktops' or 'Gaming Laptops'), so only laptop-class
# processors from the ~740 endpoint entries are kept.
AMD_INVENTORY_FAMILIES = {"Ryzen", "Ryzen PRO"}


def _is_laptop_form_factor(form_factors: list[str]) -> bool:
    """True when any form factor string contains the word 'laptop'."""
    return any("laptop" in (f or "").lower() for f in form_factors)

# TLS Impersonation & Browser Header Rotation Pool
# Current-gen (2025/2026) browser fingerprints; Akamai bot-manager flags stale TLS
# fingerprints, so the profiles must track recent Chrome/Firefox releases.
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
        "impersonate": "chrome131",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"'
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
    text = str(val).replace("[™®]", "").replace("™", "").replace("®", "").replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"\[[0-9a-zA-Z]+\]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def slugify(text: str) -> str:
    s = clean_text(text).lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


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


def derive_amd_igpu_series(igpu_model: str) -> tuple[str, str]:
    """Dynamically derive an iGPU series from the iGPU model name (no hardcoded maps)."""
    text = clean_text(igpu_model)
    low = text.lower()
    if not text or low in ["none", "n/a", "-", "—n/a", "unknown", "?"]:
        return "Integrated Graphics", "integrated_graphics"

    if "radeon" in low or "vega" in low:
        m = re.search(r"(\d)(\d{2})M\b", text)  # e.g. 890M, 780M, 680M
        if m:
            name = f"Radeon {m.group(1)}00M Series"
            return name, slugify(name)
        m = re.search(r"\b(\d{4})\b", text)  # e.g. 8060S, 8050S (Strix Halo)
        if m and m.group(1)[0] in "456789":
            name = f"Radeon {m.group(1)[0]}00M Series"
            return name, slugify(name)
        if "vega" in low:
            return "Radeon RX Vega Series", "radeon_rx_vega_series"
        if low in ["amd radeon graphics", "radeon graphics", "integrated amd radeon", "radeon"]:
            return "Integrated Graphics", "integrated_graphics"

    if "vega" in low:
        return "Radeon RX Vega Series", "radeon_rx_vega_series"
    return f"{text} Series", slugify(f"{text}_series")


def parse_amd_igpu_model(raw: str) -> tuple[str, int | None]:
    """Normalize a Wikipedia iGPU cell (e.g. '780M12 CU', 'Vega7 CU', '8060S40 CUs') into a model name + CU count."""
    text = clean_text(raw)
    if not text or ":" in text:  # skip shader-config columns like '768:48:812 CUs'
        return "", None
    units = None
    cu_match = re.search(r"(\d{1,3})\s*CU", text, re.I)
    if cu_match:
        units = int(cu_match.group(1))
    m = re.search(r"(\d{3}M)", text)  # 780M12 CU, 680M, 610M
    if m:
        return f"AMD Radeon {m.group(1)}", units
    m = re.search(r"(\d{4}S)", text, re.I)  # 8060S, 8050S
    if m:
        return f"AMD Radeon {m.group(1)}", units
    m = re.search(r"\bVega\s*(\d+)\b", text, re.I)
    if m:
        return f"AMD Radeon RX Vega {m.group(1)}", units
    if re.search(r"[A-Za-z]", text) and re.search(r"CU|M\b|Vega|Radeon|Graphics", text, re.I):
        return text, units
    return "", None


def extract_amd_links(row_links: list[str]) -> tuple[str, str]:
    """Pick the AMD product page link and the AMD driver support link from a Wikipedia row.

    Returns (amd_product_url, amd_driver_url). Short '/en/product/NNNN' links are ignored
    because they only redirect to a generic specifications page.
    """
    product_url = ""
    driver_url = ""
    for l in row_links:
        if "amd.com" not in l:
            continue
        if not l.startswith("http"):
            l = urljoin("https://en.wikipedia.org", l)
        if "/en/products/" in l and not product_url:
            product_url = l
        elif "/support/downloads/drivers.html" in l and not driver_url:
            driver_url = l
    return product_url, driver_url


def model_from_amd_product_url(url: str) -> str:
    """Derive the full model name from the AMD product URL slug, e.g.:

    amd-ryzen-9-270.html        -> AMD Ryzen 9 270
    amd-ryzen-7-pro-250.html    -> AMD Ryzen 7 PRO 250
    amd-ryzen-ai-9-hx-pro-375.html -> AMD Ryzen AI 9 HX PRO 375
    amd-ryzen-ai-max-plus-395.html -> AMD Ryzen AI MAX+ 395
    amd-ryzen-9-6980hs.html     -> AMD Ryzen 9 6980HS
    """
    m = re.search(r"/([^/]+)\.html(?:$|#)", url)
    if not m:
        return ""
    slug = m.group(1)
    if slug.startswith("amd-"):
        slug = slug[4:]
    if not slug:
        return ""

    out: list[str] = ["AMD"]
    for p in slug.split("-"):
        if not p:
            continue
        if p == "ryzen":
            out.append("Ryzen")
        elif p == "ai":
            out.append("AI")
        elif p == "max":
            out.append("MAX")
        elif p == "plus":
            if out and out[-1] == "MAX":
                out[-1] = "MAX+"
            else:
                out.append("+")
        elif p == "pro":
            out.append("PRO")
        elif p == "threadripper":
            out.append("Threadripper")
        elif p == "embedded":
            out.append("Embedded")
        elif p == "extreme":
            out.append("Extreme")
        elif re.search(r"[a-z]", p) and re.search(r"\d", p):
            out.append(p.upper())  # 6980hs -> 6980HS, 9955hx3d -> 9955HX3D, z2 -> Z2
        elif re.fullmatch(r"[0-9]+", p):
            out.append(p)  # 270, 395, 40
        elif len(p) <= 3 and p.isalpha():
            out.append(p.upper())  # HX, HS, U, H
        else:
            out.append(p.capitalize())

    model = re.sub(r"\s+", " ", " ".join(out)).strip()
    if "Ryzen" not in model:
        model = re.sub(r"\bAMD\s+AI\b", "AMD Ryzen AI", model)
        if "Ryzen" not in model:
            model = "AMD Ryzen" + model[3:] if model.startswith("AMD") else f"AMD Ryzen {model}"
    return model


def extract_amd_model_from_cells(col0: str, col1: str) -> str:
    """Extract a model name from the first two Wikipedia table cells.

    Handles 'Ryzen 7 5700U', 'Ryzen 7 (PRO) 7730U', 'Ryzen AI 9 (PRO)HX 375',
    'Ryzen AI MAX+ (PRO)395', 'Ryzen 9 270', bare '5500', etc.
    """
    combined = clean_text(f"{col0} {col1}")
    combined = re.sub(r"\(\s*PRO\s*\)", "PRO", combined, flags=re.I)
    combined = re.sub(r"PRO(?=[A-Za-z0-9])", "PRO ", combined, flags=re.I)
    combined = re.sub(r"\s+", " ", combined).strip()

    m = re.search(
        r"\b(Ryzen(?:\s+AI)?(?:\s+MAX\+)?\s+(?:[3579]|MAX\+)\s*"
        r"(?:(?:PRO|HX|HS|U|H)\s*){0,2}\d{2,4}[A-Z]{0,4})\b",
        combined, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"\b(Ryzen\s+[3579]\s+\d{2,4}[A-Z]{0,4})\b", combined, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"\b(\d{4}[A-Z]{0,4})\b", combined)
    if m:
        return m.group(1)
    return ""


def derive_amd_spec_url(wiki_h3_series: str, full_model: str) -> str:
    """Purely dynamic official AMD driver spec page URL generator."""
    s_clean = clean_text(wiki_h3_series).lower()
    s_slug = re.sub(r"[^a-z0-9]+", "-", s_clean).strip("-")

    m_clean = clean_text(full_model).lower()
    if not m_clean.startswith("amd-"):
        m_clean = f"amd-{m_clean}" if not m_clean.startswith("amd ") else m_clean.replace("amd ", "amd-")
    m_slug = re.sub(r"[^a-z0-9]+", "-", m_clean).strip("-")

    return f"https://www.amd.com/en/support/downloads/drivers.html/processors/ryzen/{s_slug}/{m_slug}.html"


def parse_amd_spec_page_sections(html_text: str) -> dict[str, dict[str, str]]:
    """Parse official AMD spec sections from either a product page or a driver support page.

    Both share the same accordion-item layout: an h2/h3 heading followed by <dl>/<dt>/<dd>
    definition lists (and optional spec tables).
    """
    soup = BeautifulSoup(html_text, "html.parser")
    amd_sections: dict[str, dict[str, str]] = {}

    accordions = soup.find_all("div", class_=re.compile(r"accordion-item"))
    if not accordions:
        container = soup.find(id="amd_support_product_spec")
        accordions = [container] if container else []

    for item in accordions:
        if not item:
            continue
        heading = item.find(["h2", "h3"])
        sec_title = clean_text(heading.get_text()) if heading else ""
        if not sec_title or sec_title.lower() in ["collapse all", "expand all", "specifications", "contact", "feedback"]:
            continue

        sec_dict: dict[str, str] = {}
        for dl in item.find_all("dl"):
            dts = dl.find_all("dt")
            dds = dl.find_all("dd")
            for i, dt in enumerate(dts):
                k = clean_text(dt.get_text())
                v = clean_text(dds[i].get_text()) if i < len(dds) else ""
                if k and v:
                    sec_dict[k] = v

        for tr in item.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            if len(tds) >= 2:
                k = clean_text(tds[0].get_text())
                v = clean_text(tds[1].get_text())
                if k and v and k not in sec_dict:
                    sec_dict[k] = v

        if sec_dict:
            amd_sections[sec_title] = sec_dict

    return amd_sections


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


def fetch_amd_spec_page(url: str, cache_key: str) -> dict[str, dict[str, str]]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{cache_key}_{hashlib.md5(url.encode('utf-8')).hexdigest()[:8]}.json"

    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
                if cached_data and isinstance(cached_data, dict) and len(cached_data) > 0:
                    return cached_data
        except Exception:
            pass

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
            r = s.get(url, headers=headers, timeout=25, allow_redirects=True)
            if r.status_code in (429, 503):
                retry_after = 0
                try:
                    retry_after = int(r.headers.get("Retry-After", "0") or 0)
                except ValueError:
                    retry_after = 0
                time.sleep(max(retry_after, 4 * (attempt + 1)) + random.uniform(0, 2))
                continue
            if r.status_code == 200 and "Access Denied" not in r.text and len(r.text) > 500:
                sections_dict = parse_amd_spec_page_sections(r.text)
                if sections_dict:
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(sections_dict, f, indent=2)
                    return sections_dict
        except Exception:
            pass

        if attempt < 4:
            time.sleep(random.uniform(2.0, 4.0) * (attempt + 1))

    return {}


def amd_model_slug(full_model: str) -> str:
    """Build an AMD product-page slug from a model string, e.g. 'AMD Ryzen AI 7 PRO 450'
    -> 'amd-ryzen-ai-7-pro-450'."""
    m_clean = clean_text(full_model).lower()
    if not m_clean.startswith("amd-"):
        m_clean = m_clean.replace("amd ", "amd-") if m_clean.startswith("amd ") else f"amd-{m_clean}"
    return re.sub(r"[^a-z0-9]+", "-", m_clean).strip("-")


def corrected_amd_product_url(url: str, full_model: str) -> str:
    """Rebuild a product URL using the model-derived slug, keeping the directory path.

    Wikipedia occasionally carries stale AMD product slugs (e.g. 'amd-ryzen-ai-5-pro-450'
    for the Ryzen AI 7 PRO 450 page), so retry the same path with a corrected slug.
    """
    slug = amd_model_slug(full_model)
    base = url.rsplit("/", 1)[0]
    return f"{base}/{slug}.html"


def amd_driver_url_variants(url: str, full_model: str) -> list[str]:
    """Generate alternative official AMD driver URL candidates.

    AMD's real-world driver page naming has two quirks the synthetic scheme misses:
    - Microsoft Surface Edition SKUs carry a '-microsoft-surface-edition' suffix.
    - PRO SKUs are hosted under the 'ryzen-pro/ryzen-pro-<series>/' path.
    """
    variants: list[str] = []
    base, _, slug = url.rpartition("/")
    if slug.endswith(".html"):
        variants.append(f"{base}/{slug[:-5]}-microsoft-surface-edition.html")
    if "PRO" in full_model.upper():
        pro_path = url.replace("/processors/ryzen/ryzen-", "/processors/ryzen-pro/ryzen-pro-", 1)
        if pro_path != url:
            variants.insert(0, pro_path)
    return variants


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
            anchors = [(clean_text(a.get_text()), a["href"]) for a in cell.find_all("a", href=True)]
            cell_data = {"text": text, "links": links, "anchors": anchors, "is_header": is_header}

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
        r_text_low = r.get_text().lower()
        if r.find_all("th") and not any(k in r_text_low for k in ["ryzen 3", "ryzen 5", "ryzen 7", "ryzen 9", "ryzen ai", "z1", "z2"]):
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


def parse_amd_wikipedia_schema_row(row_map: dict[str, str]) -> dict[str, Any]:
    cores_info = {"total_cores": None, "total_threads": None, "zen_architecture": None, "lithography": None}
    freq_info = {"base_clock_ghz": None, "boost_clock_ghz": None}
    cache_info = {"l1_cache": None, "l2_cache": None, "l3_cache": None}
    power_info = {"processor_base_power": None, "configurable_tdp": None}
    launch_date = ""

    igpu_model = ""
    igpu_units = None
    igpu_base_mhz = None
    igpu_max_mhz = None
    igpu_clock_str = None

    for k, v in row_map.items():
        low_k = k.lower()
        v_clean = v.strip()
        if not v_clean or v_clean in ["—N/a", "N/A", "-", "—"]:
            continue

        if any(w in low_k for w in ["clock", "rate", "ghz"]) and "gpu" not in low_k:
            f_val = f"{v_clean} GHz" if not v_clean.endswith("GHz") and not v_clean.endswith("MHz") else v_clean
            if "base" in low_k:
                freq_info["base_clock_ghz"] = f_val
            elif any(w in low_k for w in ["boost", "turbo", "max"]):
                freq_info["boost_clock_ghz"] = f_val

        if "cores" in low_k or "threads" in low_k:
            c_match = re.search(r"\b(\d+)\s*\(\s*(\d+)\s*\)", v_clean)
            if c_match:
                cores_info["total_cores"] = int(c_match.group(1))
                cores_info["total_threads"] = int(c_match.group(2))

        if "l3" in low_k:
            cache_info["l3_cache"] = v_clean
        elif "l2" in low_k:
            cache_info["l2_cache"] = v_clean

        if "tdp" in low_k or "power" in low_k:
            if "–" in v_clean or "-" in v_clean or "cTDP" in low_k:
                power_info["configurable_tdp"] = v_clean
            power_info["processor_base_power"] = v_clean

        if "release" in low_k or "date" in low_k:
            launch_date = v_clean

        if "arch" in low_k or "microarch" in low_k or "zen" in v_clean.lower():
            if "zen" in v_clean.lower():
                cores_info["zen_architecture"] = v_clean

        if "fab" in low_k or "process" in low_k or "nm" in v_clean.lower():
            cores_info["lithography"] = v_clean

        if any(w in low_k for w in ["gpu", "graphics"]):
            if any(w in low_k for w in ["clock", "ghz", "mhz"]):
                igpu_clock_str = f"{v_clean} GHz" if not v_clean.endswith("GHz") and not v_clean.endswith("MHz") else v_clean
                mhz_nums = re.findall(r"\b(\d+(?:\.\d+)?)\b", v_clean)
                if len(mhz_nums) >= 1:
                    val = float(mhz_nums[0])
                    if val < 10:
                        igpu_max_mhz = int(val * 1000)
                    else:
                        igpu_max_mhz = int(val)
            else:
                model, units = parse_amd_igpu_model(v_clean)
                if model:
                    igpu_model = model
                    igpu_units = units or igpu_units

    series_name, series_slug = derive_amd_igpu_series(igpu_model)
    igpu_data = {
        "model": igpu_model or "AMD Radeon Graphics",
        "short_model": igpu_model.split("(")[0].strip() if igpu_model else "Radeon Graphics",
        "series": series_name,
        "series_slug": series_slug,
        "base_frequency_mhz": igpu_base_mhz,
        "max_dynamic_frequency_mhz": igpu_max_mhz,
        "clock_mhz": igpu_clock_str,
        "units": igpu_units
    }

    return {
        "cores": cores_info,
        "clock_speeds": freq_info,
        "cache": cache_info,
        "power": power_info,
        "launch_date": launch_date,
        "igpu": igpu_data
    }


def make_amd_proc_entry(row_map: dict[str, str], col0: str, col1: str, product_url: str,
                        driver_url: str, full_model: str, series_title: str, code_name: str) -> dict[str, Any]:
    """Build a single structured processor entry shared by normal and combined rows."""
    full_model = re.sub(r"\s+", " ", full_model).strip()
    if not full_model.startswith("AMD"):
        full_model = f"AMD {full_model}"

    parsed_specs = parse_amd_wikipedia_schema_row(row_map)
    year, month, raw_date = parse_year_month(parsed_specs["launch_date"])

    return {
        "full_model": full_model,
        "short_model": full_model.replace("AMD ", "").strip(),
        "brand": "AMD",
        "series": series_title,
        "series_slug": slugify(series_title),
        "code_name": code_name,
        "code_name_slug": slugify(code_name),
        "wikipedia_h3_series": series_title,
        "wikipedia_h4_codename": code_name,
        "launch_date": raw_date,
        "launch_year": year,
        "launch_month": month,
        "amd_product_url": product_url,
        "amd_driver_url": driver_url or derive_amd_spec_url(series_title, full_model),
        "cores": parsed_specs["cores"],
        "clock_speeds": parsed_specs["clock_speeds"],
        "cache": parsed_specs["cache"],
        "power": parsed_specs["power"],
        "igpu": parsed_specs["igpu"],
        "wikipedia_schema_map": row_map
    }


def split_combined_amd_row(series_title: str, col0: str, col1: str,
                           anchors: list[tuple[str, str]]) -> list[dict[str, Any]] | None:
    """Split a Wikipedia '(PRO) X' combined row into consumer + PRO entries.

    Combined rows list both a PRO SKU and its consumer sibling in one cell using two
    AMD anchors (e.g. '(PRO) 7840HS': a 'PRO' anchor plus a '7840HS' model anchor).
    Returns a list of two {"full_model", "product_url", "driver_url"} entries (consumer
    first, PRO second), or None when the row is not a combined PRO/consumer row.
    """
    amd_anchors = [(t, urljoin("https://en.wikipedia.org", h)) for t, h in anchors if "amd.com" in h]
    pro_idx = next((i for i, (t, _) in enumerate(amd_anchors) if clean_text(t).upper() == "PRO"), -1)
    if pro_idx == -1 or len(amd_anchors) < 2:
        return None

    model_text, model_url = next((t, h) for i, (t, h) in enumerate(amd_anchors) if i != pro_idx)
    pro_text, pro_url = amd_anchors[pro_idx]

    consumer_model = model_from_amd_product_url(model_url) or extract_amd_model_from_cells(col0, model_text)
    if not consumer_model:
        return None
    consumer_model = f"AMD {consumer_model}" if not consumer_model.startswith("AMD") else consumer_model

    pro_model = model_from_amd_product_url(pro_url) or extract_amd_model_from_cells(col0, f"PRO {model_text}")
    if not pro_model:
        return None
    pro_model = f"AMD {pro_model}" if not pro_model.startswith("AMD") else pro_model

    return [
        {
            "full_model": consumer_model,
            "product_url": model_url if "/en/products/" in model_url else "",
            "driver_url": derive_amd_spec_url(series_title, consumer_model)
        },
        {
            "full_model": pro_model,
            "product_url": pro_url if "/en/products/" in pro_url else "",
            "driver_url": pro_url if "/support/downloads/drivers.html" in pro_url else derive_amd_spec_url(series_title, pro_model)
        }
    ]


def parse_amd_wikipedia_dom_hierarchy() -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    print(f"[wiki] Fetching {WIKI_URL}...")
    req = requests.get(WIKI_URL, headers={"User-Agent": PROFILES[0]["user_agent"]}, impersonate="chrome124", timeout=30)
    if req.status_code != 200:
        raise RuntimeError(f"Failed to fetch Wikipedia page: HTTP {req.status_code}")

    soup = BeautifulSoup(req.text, "html.parser")
    processors: list[dict[str, Any]] = []
    igpus_dict: dict[str, list[dict[str, Any]]] = {}

    mobile_h2 = soup.find(id="Mobile_processors")
    if mobile_h2 and mobile_h2.name != "h2":
        mobile_h2 = mobile_h2.find_parent("h2")

    if not mobile_h2:
        raise RuntimeError("Could not find Mobile_processors section on Wikipedia.")

    current_h3_title = "AMD Ryzen Mobile"
    current_h4_title = ""

    for elem in mobile_h2.find_all_next():
        if elem.name == "h2" and elem.get("id") in ["Handheld_gaming_PC_processors", "Embedded_processors", "See_also"]:
            break
        if elem.name == "h3":
            current_h3_title = clean_text(elem.get_text().replace("[edit]", ""))
            current_h4_title = ""
        elif elem.name == "h4":
            current_h4_title = clean_text(elem.get_text().replace("[edit]", ""))
        elif elem.name == "table" and "wikitable" in elem.get("class", []):
            schema, data_rows = parse_wikitable_grid_with_schema(elem)
            if not data_rows or not schema:
                continue

            for row in data_rows:
                if not row:
                    continue

                row_map = {schema[c]: row[c]["text"] if c < len(row) and row[c] else "" for c in range(len(schema))}
                col0 = row[0]["text"] if len(row) > 0 and row[0] else ""
                col1 = row[1]["text"] if len(row) > 1 and row[1] else ""

                cell_anchors = [(a[0], a[1]) for c in row if c for a in c.get("anchors", [])]
                combined = split_combined_amd_row(current_h3_title, col0, col1, cell_anchors)

                code_name = current_h4_title or current_h3_title

                if combined is not None:
                    proc_entries = [
                        make_amd_proc_entry(row_map, col0, col1, e["product_url"], e["driver_url"],
                                            e["full_model"], current_h3_title, code_name)
                        for e in combined
                    ]
                else:
                    row_links = [l for c in row if c for l in c["links"]]
                    product_url, driver_url = extract_amd_links(row_links)

                    cell_model = extract_amd_model_from_cells(col0, col1)
                    if cell_model.lower().startswith("ryzen"):
                        full_model = re.sub(r"\bPRO (HX|HS)\b", r"\1 PRO", cell_model, flags=re.I)
                    elif product_url:
                        full_model = model_from_amd_product_url(product_url)
                    elif cell_model:
                        full_model = cell_model
                    else:
                        continue

                    proc_entries = [
                        make_amd_proc_entry(row_map, col0, col1, product_url, driver_url,
                                            full_model, current_h3_title, code_name)
                    ]

                for proc_entry in proc_entries:
                    processors.append(proc_entry)

                    igpu = proc_entry["igpu"]
                    if igpu["model"]:
                        i_slug = igpu["series_slug"]
                        igpus_dict.setdefault(i_slug, []).append({
                            "igpu_model": igpu["model"],
                            "short_model": igpu["short_model"],
                            "series": igpu["series"],
                            "series_slug": i_slug,
                            "cpu_full_model": proc_entry["full_model"],
                            "cpu_series": proc_entry["series"],
                            "cpu_code_name": proc_entry["code_name"],
                            "base_frequency_mhz": igpu["base_frequency_mhz"],
                            "max_dynamic_frequency_mhz": igpu["max_dynamic_frequency_mhz"],
                            "clock_mhz": igpu["clock_mhz"],
                            "units": igpu["units"]
                        })

    return processors, igpus_dict


def _endpoint_element_value(el: dict[str, Any], key: str) -> Any:
    return (el.get(key) or {}).get("value")


def fetch_amd_specs_endpoint_items() -> list[dict[str, Any]]:
    """Fetch and return the AMD processor-specs endpoint items (embedded data-json).

    Cached on disk (AMD_SPECS_CACHE) so repeated `--append` runs do not re-download
    the ~10MB page. Returns the raw list of per-processor dicts.
    """
    AMD_SPECS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    if AMD_SPECS_CACHE.exists():
        try:
            with open(AMD_SPECS_CACHE, "r", encoding="utf-8") as f:
                cached = json.load(f)
                if isinstance(cached, list) and len(cached) > 100:
                    return cached
        except Exception:
            pass

    req = requests.get(
        AMD_SPECS_URL,
        headers={"User-Agent": PROFILES[0]["user_agent"], "Accept-Language": "en-US,en;q=0.9"},
        impersonate="chrome142",
        timeout=60,
    )
    if req.status_code != 200:
        raise RuntimeError(f"Failed to fetch AMD specs page: HTTP {req.status_code}")

    m = re.search(r'data-json="([^"]+)"', req.text)
    if not m:
        raise RuntimeError("Could not locate embedded data-json on the AMD specs page.")

    data = json.loads(_html.unescape(m.group(1)))
    items = data.get("items") or []
    with open(AMD_SPECS_CACHE, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2)
    return items


def parse_amd_specs_endpoint() -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Discover AMD mobile processors from the official AMD specs endpoint.

    The embedded data-json lists every AMD processor (≈740) with family / series /
    form factor / spec fields, but NOT the marketing codename (e.g. 'Hawk Point').
    The codename and the authoritative per-chip specs are resolved by the per-page
    enrichment pass (process_amd_spec_enrichment) using the product URL carried here.

    Returns the same (processors, igpu_groups) shape as the Wikipedia parser so the
    caller is source-agnostic.
    """
    items = fetch_amd_specs_endpoint_items()

    processors: list[dict[str, Any]] = []
    igpus_dict: dict[str, list[dict[str, Any]]] = {}

    for it in items:
        el = it.get("elements", {})
        family = _endpoint_element_value(el, "family") or []
        form_factors = _endpoint_element_value(el, "formFactor") or []
        if not set(family) & AMD_INVENTORY_FAMILIES:
            continue
        if not _is_laptop_form_factor(form_factors):
            continue

        raw_name = clean_text(_endpoint_element_value(el, "name") or "")
        if not raw_name:
            continue
        product_url = (it.get("productPages") or {}).get("en", "") or ""
        series_list = _endpoint_element_value(el, "series") or []
        series_title = clean_text(series_list[0]) if series_list else (
            "Ryzen PRO" if "PRO" in " ".join(family).upper() else "Ryzen")

        raw_name = re.sub(r"RyzenAI", "Ryzen AI", raw_name, flags=re.I)
        full_model = raw_name if raw_name.upper().startswith("AMD") else f"AMD {raw_name}"

        cores = {
            "total_cores": _endpoint_element_value(el, "numOfCpuCores"),
            "total_threads": _endpoint_element_value(el, "numOfThreads"),
            "zen_architecture": None,
            "lithography": (_endpoint_element_value(el, "processorTechnologyForCpuCores") or [None])[0],
        }

        base = _endpoint_element_value(el, "baseClock")
        boost = _endpoint_element_value(el, "maxBoostClock")

        def fmt_clock(v: Any) -> str:
            if v is None:
                return None
            return f"{float(v) / 1000:g} GHz"

        clock_speeds = {
            "base_clock_ghz": fmt_clock(base),
            "boost_clock_ghz": f"Up to {fmt_clock(boost)}" if boost is not None else None,
        }

        l2 = _endpoint_element_value(el, "l2Cache")
        l3 = _endpoint_element_value(el, "l3Cache")

        def fmt_cache(v: Any) -> str:
            if v is None:
                return None
            mb = float(v) / 1024.0
            return f"{mb:g} MB" if mb >= 1 else f"{float(v):g} KB"

        cache = {
            "l1_cache": None,
            "l2_cache": fmt_cache(l2),
            "l3_cache": fmt_cache(l3),
        }

        tdp = _endpoint_element_value(el, "defaultTdp")
        ctdp = _endpoint_element_value(el, "amdConfigurableTdpCtdp")
        power = {
            "processor_base_power": f"{tdp}W" if tdp is not None else None,
            "configurable_tdp": f"{ctdp}W" if ctdp is not None else None,
        }

        igpu_model = clean_text(_endpoint_element_value(el, "graphicsModel") or "")
        igpu_series, igpu_series_slug = derive_amd_igpu_series(igpu_model)
        gfx_freq = _endpoint_element_value(el, "graphicsFrequency")
        igpu = {
            "model": igpu_model or "AMD Radeon Graphics",
            "short_model": igpu_model.split("(")[0].strip() if igpu_model else "Radeon Graphics",
            "series": igpu_series,
            "series_slug": igpu_series_slug,
            "base_frequency_mhz": None,
            "max_dynamic_frequency_mhz": f"{gfx_freq} MHz" if gfx_freq is not None else None,
            "clock_mhz": f"{gfx_freq} MHz" if gfx_freq is not None else None,
            "units": int(float(_endpoint_element_value(el, "graphicsCoreCount"))) if _endpoint_element_value(el, "graphicsCoreCount") is not None else None,
        }

        launch_date = _endpoint_element_value(el, "launchDate") or ""
        year, month, raw_date = parse_year_month(launch_date)

        entry = {
            "full_model": full_model,
            "short_model": full_model.replace("AMD ", "").strip(),
            "brand": "AMD",
            "series": series_title,
            "series_slug": slugify(series_title),
            "code_name": series_title,
            "code_name_slug": slugify(series_title),
            "wikipedia_h3_series": series_title,
            "wikipedia_h4_codename": series_title,
            "launch_date": raw_date,
            "launch_year": year,
            "launch_month": month,
            "amd_product_url": product_url if "/en/products/" in product_url else "",
            "amd_driver_url": product_url if "/support/downloads/drivers.html" in product_url else "",
            "cores": cores,
            "clock_speeds": clock_speeds,
            "cache": cache,
            "power": power,
            "igpu": igpu,
            "memory": {
                "types": ", ".join(_endpoint_element_value(el, "systemMemoryType") or []),
                "max_channels": str(_endpoint_element_value(el, "memoryChannels") or ""),
                "max_memory_size": _endpoint_element_value(el, "systemMemorySpecification") or "",
            },
            "endpoint_source": "amd.com specs",
        }
        if not entry["amd_product_url"] and not entry["amd_driver_url"]:
            entry["amd_driver_url"] = derive_amd_spec_url(series_title, full_model)

        processors.append(entry)

        igpu_entry = {
            "igpu_model": igpu["model"],
            "short_model": igpu["short_model"],
            "series": igpu["series"],
            "series_slug": igpu["series_slug"],
            "cpu_full_model": entry["full_model"],
            "cpu_series": entry["series"],
            "cpu_code_name": entry["code_name"],
            "base_frequency_mhz": igpu["base_frequency_mhz"],
            "max_dynamic_frequency_mhz": igpu["max_dynamic_frequency_mhz"],
            "clock_mhz": igpu["clock_mhz"],
            "units": igpu["units"],
        }
        if igpu["model"] and "Radeon Graphics" not in igpu["model"]:
            igpus_dict.setdefault(igpu["series_slug"], []).append(igpu_entry)

    return processors, igpus_dict


def resolve_amd_placement(proc: dict[str, Any], existing: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Assign series / code-name slugs for an endpoint-discovered processor.

    The amd.com endpoint carries no marketing codename, so the authoritative
    Series + Former Codename come from the individual product page (amd_specs).
    A matching existing hierarchy folder (same series, code name prefix) is
    reused so PRO chips land beside their consumer siblings exactly like the
    current master layout. Falls back to deriving folders from the page.
    """
    specs = proc.get("amd_specs", {}).get("General Specifications", {})
    series_title = clean_text(specs.get("Series") or proc.get("series") or "")
    codename = clean_text(specs.get("Former Codename") or "")
    if not series_title and not codename:
        return proc

    base_series_slug = slugify(re.sub(r"\bPRO\b", "", series_title))
    codename_slug = slugify(codename)

    if existing:
        for e in existing.values():
            if e.get("series_slug") != base_series_slug:
                continue
            if codename_slug and (e.get("code_name_slug") or "").startswith(codename_slug):
                proc["series"] = e["series"]
                proc["series_slug"] = e["series_slug"]
                proc["code_name"] = e["code_name"]
                proc["code_name_slug"] = e["code_name_slug"]
                proc["wikipedia_h3_series"] = e.get("wikipedia_h3_series") or e["series"]
                proc["wikipedia_h4_codename"] = e.get("wikipedia_h4_codename") or e["code_name"]
                return proc

        if not codename_slug:
            sibling = next(
                (e for e in existing.values() if e.get("series_slug") == base_series_slug),
                None,
            )
            if sibling:
                proc["series"] = sibling["series"]
                proc["series_slug"] = sibling["series_slug"]
                proc["code_name"] = sibling["code_name"]
                proc["code_name_slug"] = sibling["code_name_slug"]
                proc["wikipedia_h3_series"] = sibling.get("wikipedia_h3_series") or sibling["series"]
                proc["wikipedia_h4_codename"] = sibling.get("wikipedia_h4_codename") or sibling["code_name"]
                return proc

    proc["series"] = series_title
    proc["series_slug"] = base_series_slug
    proc["code_name"] = codename or series_title
    proc["code_name_slug"] = slugify(codename or series_title)
    proc["wikipedia_h3_series"] = series_title
    proc["wikipedia_h4_codename"] = codename or series_title
    return proc


def process_amd_spec_enrichment(proc: dict[str, Any]) -> dict[str, Any]:
    """Enrich a CPU entry with official AMD specs.

    Tries the Wikipedia-provided product page URL first (Ryzen 7000 series and newer),
    then falls back to the AMD driver support page URL (older Ryzen 2000-6000 series).
    """
    time.sleep(random.uniform(0.3, 0.8))
    product_url = proc.get("amd_product_url") or ""
    driver_url = proc.get("amd_driver_url") or ""
    if not product_url and not driver_url:
        return proc

    cache_key = f"{proc['series_slug']}_{proc['code_name_slug']}_{slugify(proc['full_model'])}"
    amd_sections: dict[str, dict[str, str]] = {}
    for url in [product_url, driver_url]:
        if not url:
            continue
        amd_sections = fetch_amd_spec_page(url, cache_key)
        if amd_sections:
            break
        if url == product_url:
            corrected = corrected_amd_product_url(url, proc.get("full_model", ""))
            if corrected and corrected != url:
                amd_sections = fetch_amd_spec_page(corrected, cache_key)
                if amd_sections:
                    proc["amd_product_url"] = corrected
                    break
        elif url == driver_url:
            for variant in amd_driver_url_variants(url, proc.get("full_model", "")):
                if variant == url:
                    continue
                amd_sections = fetch_amd_spec_page(variant, cache_key)
                if amd_sections:
                    proc["amd_driver_url"] = variant
                    break
            if amd_sections:
                break
    if not amd_sections:
        return proc

    flat_specs: dict[str, str] = {}
    for sec_name, kv in amd_sections.items():
        for k, v in kv.items():
            flat_specs[k] = v

    if flat_specs.get("Launch Date"):
        year, month, _ = parse_year_month(flat_specs["Launch Date"])
        if year: proc["launch_year"] = year
        if month: proc["launch_month"] = month
        proc["launch_date"] = flat_specs["Launch Date"]

    if flat_specs.get("# of CPU Cores") and flat_specs["# of CPU Cores"].isdigit():
        proc["cores"]["total_cores"] = int(flat_specs["# of CPU Cores"])
    if flat_specs.get("# of Threads") and flat_specs["# of Threads"].isdigit():
        proc["cores"]["total_threads"] = int(flat_specs["# of Threads"])

    if flat_specs.get("Processor Architecture"):
        proc["cores"]["zen_architecture"] = flat_specs["Processor Architecture"]
    if flat_specs.get("Processor Technology for CPU Cores"):
        proc["cores"]["lithography"] = flat_specs["Processor Technology for CPU Cores"]

    if flat_specs.get("Base Clock"):
        proc["clock_speeds"]["base_clock_ghz"] = flat_specs["Base Clock"]
    if flat_specs.get("Max. Boost Clock"):
        proc["clock_speeds"]["boost_clock_ghz"] = flat_specs["Max. Boost Clock"]

    if flat_specs.get("L1 Cache"): proc["cache"]["l1_cache"] = flat_specs["L1 Cache"]
    if flat_specs.get("L2 Cache"): proc["cache"]["l2_cache"] = flat_specs["L2 Cache"]
    if flat_specs.get("L3 Cache"): proc["cache"]["l3_cache"] = flat_specs["L3 Cache"]

    if flat_specs.get("Default TDP"): proc["power"]["processor_base_power"] = flat_specs["Default TDP"]
    if flat_specs.get("AMD Configurable TDP (cTDP)"): proc["power"]["configurable_tdp"] = flat_specs["AMD Configurable TDP (cTDP)"]

    proc["memory"] = {
        "types": flat_specs.get("System Memory Type"),
        "max_channels": flat_specs.get("Memory Channels"),
        "max_memory_size": flat_specs.get("Max Memory Size by Type") or flat_specs.get("Max. Memory")
    }

    igpu_name = flat_specs.get("Graphics Model") or proc["igpu"].get("model") or ""
    series_name, series_slug = derive_amd_igpu_series(igpu_name)

    proc["igpu"] = {
        "model": igpu_name,
        "short_model": igpu_name.split("(")[0].strip() if igpu_name else proc["igpu"].get("short_model"),
        "series": series_name,
        "series_slug": series_slug,
        "base_frequency_mhz": proc["igpu"].get("base_frequency_mhz"),
        "max_dynamic_frequency": flat_specs.get("Graphics Frequency") or proc["igpu"].get("max_dynamic_frequency_mhz"),
        "execution_units": flat_specs.get("Graphics Core Count") or proc["igpu"].get("units")
    }

    proc["amd_specs"] = amd_sections
    return proc


def apply_known_amd_corrections(processors: list[dict[str, Any]]) -> None:
    """Apply hardcoded silicon-level corrections from AMD's official product guides.

    8745HS is the 8845HS silicon without the Ryzen AI NPU. It is an OEM-only SKU with
    no public product page, so its specs are sourced from the 8845HS entry (minus the
    AI Engine section) rather than left empty.

    4600HS shares the 4600H silicon but defaults to a 35W TDP instead of 45W; its
    driver page exists but serves no server-rendered specs.
    """
    by_short = {p["short_model"]: p for p in processors}

    hs_src = by_short.get("Ryzen 7 8845HS")
    hs_dst = by_short.get("Ryzen 7 8745HS")
    if hs_src and hs_dst and hs_src.get("amd_specs") and not hs_dst.get("amd_specs"):
        specs = {k: dict(v) for k, v in hs_src["amd_specs"].items() if k != "AI Engine Capabilities"}
        if "General Specifications" in specs:
            specs["General Specifications"]["Name"] = "AMD Ryzen 7 8745HS"
        hs_dst["amd_specs"] = specs
        for f in ["cores", "clock_speeds", "cache", "power", "memory", "igpu"]:
            if f in hs_src:
                hs_dst[f] = json.loads(json.dumps(hs_src[f]))
        hs_dst["spec_source"] = "AMD Ryzen 7 8845HS (matching silicon, lacks Ryzen AI NPU)"

    u_src = by_short.get("Ryzen 5 4600H")
    u_dst = by_short.get("Ryzen 5 4600HS")
    if u_src and u_dst and u_src.get("amd_specs") and not u_dst.get("amd_specs"):
        specs = {k: dict(v) for k, v in u_src["amd_specs"].items()}
        if "General Specifications" in specs:
            specs["General Specifications"]["Name"] = "AMD Ryzen 5 4600HS"
            specs["General Specifications"]["Default TDP"] = "35W"
        u_dst["amd_specs"] = specs
        for f in ["cores", "clock_speeds", "cache", "power", "memory", "igpu"]:
            if f in u_src:
                u_dst[f] = json.loads(json.dumps(u_src[f]))
        u_dst["power"]["processor_base_power"] = "35W"
        u_dst["spec_source"] = "AMD Ryzen 5 4600H (matching silicon, 35W default TDP)"


def apply_hs_power_rules(processors: list[dict[str, Any]]) -> None:
    """HS-branded SKUs default to 35W TDP while their OEM 'H' rebrands run at 45W.

    Applied only to the normalized H<->HS pairs (HS entries carrying OEM aliases);
    8745HS is excluded because it maps to the 8845HS silicon (45W default).
    """
    for p in processors:
        if p.get("alias_status") == "oem_h_variant":
            p["power"]["processor_base_power"] = "45W"
        elif p.get("oem_aliases") and p["short_model"] != "Ryzen 7 8745HS":
            p["power"]["processor_base_power"] = "35W"


def normalize_oem_h_aliases(processors: list[dict[str, Any]]) -> int:
    """Map OEM 'H'-branded variants to their official 'HS' sibling.

    In the Ryzen 7000/8000 Phoenix/Rembrandt/Hawk families AMD only documents the
    'HS' SKU while OEMs commonly advertise the same silicon as an 'H' variant
    (e.g. Ryzen 9 7940H vs 7940HS). Only unenriched 'H' entries with a same-silicon
    'HS' sibling are treated as aliases; 8745HS is a genuine OEM-only SKU and is
    never normalized away.
    """
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for p in processors:
        stem = ""
        m = re.search(r"(\d{3,4})[A-Z]*$", p.get("short_model", ""))
        if m:
            stem = m.group(1)
        by_key.setdefault((p.get("series_slug", ""), stem), []).append(p)

    aliased = 0
    for p in processors:
        if p.get("amd_specs"):
            continue
        m = re.fullmatch(r"(.+?)\s+(\d{3,4})H$", p.get("short_model", ""))
        if not m:
            continue
        prefix, num = m.group(1), m.group(2)
        hs_siblings = [
            q for q in by_key.get((p.get("series_slug", ""), num), [])
            if q is not p and q.get("short_model", "").endswith(num + "HS")
        ]
        if not hs_siblings:
            continue
        hs_siblings.sort(key=lambda q: "PRO" in q.get("short_model", ""))
        ref = hs_siblings[0]
        canonical_short = f"{prefix} {num}HS"
        p["canonical_model"] = canonical_short
        p["canonical_full_model"] = f"AMD {canonical_short}"
        p["canonical_ref"] = ref.get("full_model", "")
        p["alias_status"] = "oem_h_variant"
        ref.setdefault("oem_aliases", [])
        if p["short_model"] not in ref["oem_aliases"]:
            ref["oem_aliases"].append(p["short_model"])
        if ref.get("amd_specs"):
            for f in ["amd_specs", "cores", "clock_speeds", "cache", "power", "memory", "igpu",
                      "launch_date", "launch_year", "launch_month"]:
                if ref.get(f) not in (None, "", {}):
                    p[f] = ref[f]
            p["canonical_spec_source"] = ref.get("full_model", "")
        aliased += 1
    return aliased


def build_amd_inventory(parallel_workers: int = 16, source: str = "wikipedia",
                        append: bool = False) -> dict[str, Any]:
    print(f"=== Overhauling AMD Mobile CPU & iGPU Hardware Inventory (source={source}, append={append}) ===")

    existing: dict[str, dict[str, Any]] = {}
    if append and MASTER_AMD_CPU_INVENTORY.exists():
        try:
            with open(MASTER_AMD_CPU_INVENTORY, "r", encoding="utf-8") as f:
                existing = json.load(f).get("processors", {})
            print(f"[append] Loaded {len(existing)} existing processors from master inventory.")
        except Exception as e:
            print(f"[append-warning] Could not load existing master inventory: {e}")

    if not append:
        if AMD_CPUS_DIR.exists():
            shutil.rmtree(AMD_CPUS_DIR)
        if AMD_IGPUS_DIR.exists():
            shutil.rmtree(AMD_IGPUS_DIR)

    AMD_CPUS_DIR.mkdir(parents=True, exist_ok=True)
    AMD_IGPUS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if source == "amd":
        processors, igpu_groups = parse_amd_specs_endpoint()
        print(f"[amd-endpoint] Discovered and structured {len(processors)} AMD mobile processor entries.")
    else:
        processors, igpu_groups = parse_amd_wikipedia_dom_hierarchy()
        print(f"[wiki] Discovered and structured {len(processors)} AMD mobile processor entries.")

    if append and existing:
        existing_shorts = {p["short_model"].lower() for p in existing.values()}
        before = len(processors)
        processors = [p for p in processors if p["short_model"].lower() not in existing_shorts]
        print(f"[append] {before} discovered, {before - len(processors)} already present, {len(processors)} new to add.")

    deduped_cpus: dict[str, dict[str, Any]] = {}
    for p in processors:
        key = f"{p['series_slug']}_{p['code_name_slug']}_{slugify(p['full_model'])}"
        if key not in deduped_cpus:
            deduped_cpus[key] = p

    print(f"[dedupe] Total unique AMD mobile processor entries: {len(deduped_cpus)}")

    spec_targets = list(deduped_cpus.values())
    print(f"[scrape-pool] Processing official AMD Product & Driver Spec Pages for {len(spec_targets)} processors...")

    start_time = time.time()
    enriched_procs_map: dict[str, dict[str, Any]] = {}

    with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
        future_map = {executor.submit(process_amd_spec_enrichment, proc): proc["full_model"] for proc in spec_targets}
        success_count = 0
        for future in as_completed(future_map):
            model_name = future_map[future]
            try:
                enriched = future.result()
                if source == "amd":
                    resolve_amd_placement(enriched, existing)
                key = f"{enriched['series_slug']}_{enriched['code_name_slug']}_{slugify(enriched['full_model'])}"
                enriched_procs_map[key] = enriched
                if enriched.get("amd_specs"):
                    success_count += 1
            except Exception:
                pass

    duration = time.time() - start_time
    print(f"[scrape-pool] Finished in {duration:.2f} seconds. ({success_count}/{len(spec_targets)} enriched with official AMD section categories)")

    # Re-key by full_model first so placement-slug changes can't leave stale
    # pre-enrichment entries behind, then rebuild the slug keys.
    by_model: dict[str, dict[str, Any]] = {}
    for key, proc in deduped_cpus.items():
        m_key = proc["full_model"].lower()
        if m_key not in by_model or proc.get("amd_specs"):
            by_model[m_key] = proc
    for key, enriched in enriched_procs_map.items():
        m_key = enriched["full_model"].lower()
        if m_key not in by_model or enriched.get("amd_specs"):
            by_model[m_key] = enriched
    deduped_cpus = {
        f"{p['series_slug']}_{p['code_name_slug']}_{slugify(p['full_model'])}": p
        for p in by_model.values()
    }
    print(f"[dedupe] Post-enrichment unique processors (by full model): {len(deduped_cpus)}")

    if append and existing:
        for key, proc in existing.items():
            if proc.get("endpoint_source") == "amd.com specs":
                resolve_amd_placement(proc, existing)
            key = f"{proc['series_slug']}_{proc['code_name_slug']}_{slugify(proc['full_model'])}"
            deduped_cpus.setdefault(key, proc)
        print(f"[append] Merged {len(existing)} existing + {len(processors)} new processors = {len(deduped_cpus)} total.")

    apply_known_amd_corrections(list(deduped_cpus.values()))
    aliased_count = normalize_oem_h_aliases(list(deduped_cpus.values()))
    if aliased_count:
        print(f"[canonicalize] Marked {aliased_count} OEM 'H' variants as aliases of their official 'HS' SKUs.")
    apply_hs_power_rules(list(deduped_cpus.values()))
    success_count = sum(1 for p in deduped_cpus.values() if p.get("amd_specs"))
    print(f"[canonicalize] Entries with official specs after alias inheritance: {success_count}/{len(deduped_cpus)}")

    hierarchy: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for p in deduped_cpus.values():
        s_slug = p["series_slug"]
        c_slug = p["code_name_slug"]
        hierarchy.setdefault(s_slug, {}).setdefault(c_slug, []).append(p)

    cpu_file_count = 0
    for s_slug, codenames in hierarchy.items():
        s_dir = AMD_CPUS_DIR / s_slug
        s_dir.mkdir(parents=True, exist_ok=True)

        for c_slug, procs in codenames.items():
            c_dir = s_dir / c_slug
            c_dir.mkdir(parents=True, exist_ok=True)

            for p in procs:
                m_slug = slugify(p["full_model"])
                if not m_slug: continue
                file_path = c_dir / f"{m_slug}.json"
                if append and file_path.exists():
                    continue
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(p, f, indent=2)
                cpu_file_count += 1

    for s_slug, codenames in hierarchy.items():
        s_dir = AMD_CPUS_DIR / s_slug
        for c_slug, procs in codenames.items():
            c_dir = s_dir / c_slug
            for f in c_dir.glob("*.json"):
                if f.stem not in {slugify(p["full_model"]) for p in procs}:
                    f.unlink()

    valid_pairs = {(s_slug, c_slug) for s_slug, codenames in hierarchy.items() for c_slug in codenames}
    for s_dir in AMD_CPUS_DIR.iterdir():
        if not s_dir.is_dir():
            continue
        for c_dir in s_dir.iterdir():
            if not c_dir.is_dir():
                continue
            if (s_dir.name, c_dir.name) not in valid_pairs:
                shutil.rmtree(c_dir)
                continue
            for f in c_dir.glob("*.json"):
                if f.stem not in {slugify(p["full_model"]) for p in hierarchy[s_dir.name][c_dir.name]}:
                    f.unlink()
        if not any(s_dir.iterdir()):
            s_dir.rmdir()

    print(f"[storage] {len(hierarchy)} AMD CPU series folders ({cpu_file_count} CPU files written) in {AMD_CPUS_DIR}.")

    igpu_file_count = 0
    for i_slug, igpus in igpu_groups.items():
        i_dir = AMD_IGPUS_DIR / i_slug
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

    print(f"[storage] Cleanly created {len(igpu_groups)} AMD iGPU series folders ({igpu_file_count} individual iGPU files) in {AMD_IGPUS_DIR}.")

    master_amd_cpu_data = {"processors": {f"{p['series_slug']}_{p['code_name_slug']}_{slugify(p['full_model'])}": p for p in deduped_cpus.values()}}
    with open(MASTER_AMD_CPU_INVENTORY, "w", encoding="utf-8") as f:
        json.dump(master_amd_cpu_data, f, indent=2)
    print(f"[master] Saved master AMD CPU inventory: {MASTER_AMD_CPU_INVENTORY}")

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
    import argparse
    parser = argparse.ArgumentParser(description="Build AMD mobile CPU/iGPU hardware inventory.")
    parser.add_argument("--source", choices=["wikipedia", "amd"], default="wikipedia",
                        help="Discovery source: 'wikipedia' (default) or 'amd' (official AMD specs endpoint).")
    parser.add_argument("--append", action="store_true",
                        help="Append-only mode: merge newly discovered processors into the existing master inventory.")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel enrichment workers.")
    args = parser.parse_args()

    res = build_amd_inventory(parallel_workers=args.workers, source=args.source, append=args.append)
    print("=== Clean Dynamic AMD Inventory Generation Complete ===")
    print(res)
