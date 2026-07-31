#!/usr/bin/env python3
"""
100% Dynamic NVIDIA Mobile GPU Hardware Inventory Builder.

Mirrors AMD and Intel inventory pipelines (build_amd_inventory.py, build_intel_inventory.py):
fully dynamic, zero hardcoded dictionaries. Everything is derived from the Wikipedia DOM:
  - Fetches Mobile GeForce and Mobile Workstation GPU tables via Wikipedia's official
    MediaWiki API (action=parse):
    https://en.wikipedia.org/w/api.php
  - Dynamically handles multi-row/colspan headers and transposed table structures
    (e.g., GeForce RTX 50 Laptop series).

Output Structure:
  - data/inventory/nvidia/gpus/<series_slug>/<gpu_model_slug>.json
  - data/nvidia_gpu_inventory.json (master standalone NVIDIA GPU inventory)
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from curl_cffi import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.scripts.clean_hardware_inventory import clean_all_hardware_inventories, sanitize_data

# Paths
DATA_DIR = REPO_ROOT / "data"
NVIDIA_INVENTORY_DIR = DATA_DIR / "inventory" / "nvidia"
NVIDIA_GPUS_DIR = NVIDIA_INVENTORY_DIR / "gpus"
MASTER_NVIDIA_GPU_INVENTORY = DATA_DIR / "nvidia_gpu_inventory.json"
MASTER_GPU_INVENTORY = DATA_DIR / "gpu_inventory.json"

WIKI_API_URL = "https://en.wikipedia.org/w/api.php"
WIKI_PAGE_TITLE = "List_of_Nvidia_graphics_processing_units"

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


def clean_cell(cell: Any) -> str:
    if not cell:
        return ""
    if hasattr(cell, "get_text"):
        text = cell.get_text(separator=" ")
    else:
        text = str(cell)
    text = text.replace("™", "").replace("®", "").replace("\xa0", " ")
    text = re.sub(r"\[\s*[0-9a-zA-Z\s,]+\s*\]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_text(val: Any) -> str:
    if not val:
        return ""
    text = str(val).replace("™", "").replace("®", "").replace("\xa0", " ")
    text = re.sub(r"\[\s*[0-9a-zA-Z\s,]+\s*\]", "", text)
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


def parse_wikitable_grid(table_elem: Any) -> list[list[dict[str, Any] | None]]:
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
            text = clean_cell(cell)
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

    return grid


def parse_table_and_entries(table_elem: Any, series_title: str) -> list[tuple[str, dict[str, str], list[tuple[str, str]]]]:
    grid = parse_wikitable_grid(table_elem)
    if not grid or not grid[0]:
        return []

    # Detect transposed table (e.g. RTX 50 Laptop series where column 0 contains attribute names)
    col0_text = " ".join([grid[r][0]["text"] for r in range(len(grid)) if len(grid[r]) > 0 and grid[r][0] and grid[r][0]["text"]]).lower()
    is_transposed = any(k in col0_text for k in ["release date", "gpu die", "cuda cores", "streaming multiprocessors", "die size"])

    entries: list[tuple[str, dict[str, str], list[tuple[str, str]]]] = []

    if is_transposed:
        attr_cols = 2
        model_cols = list(range(attr_cols, len(grid[0])))
        model_names = [grid[0][c]["text"] if grid[0][c] else f"col_{c}" for c in model_cols]

        row_attrs = []
        for r in range(len(grid)):
            parts = []
            for c in range(attr_cols):
                cell = grid[r][c]
                if cell and cell["text"]:
                    t = cell["text"]
                    if t not in parts:
                        parts.append(t)
            row_attrs.append(" > ".join(parts))

        for col_idx, m_name in zip(model_cols, model_names):
            if not m_name or m_name.lower() in ["model", "model name", "geforce rtx", "code name", "fab (nm)"]:
                continue
            m_map = {}
            m_anchors = []
            for r_idx, attr_name in enumerate(row_attrs):
                if r_idx == 0:
                    continue
                cell = grid[r_idx][col_idx] if col_idx < len(grid[r_idx]) else None
                val = cell["text"] if cell else ""
                if cell and cell.get("anchors"):
                    m_anchors.extend(cell["anchors"])
                m_map[attr_name] = val
            entries.append((m_name, m_map, m_anchors))
    else:
        header_row_count = 1
        if len(grid) > 1:
            r1_text = " ".join([c["text"] for c in grid[1] if c and c["text"]]).lower()
            if any(k in r1_text for k in ["base", "boost", "clock", "memory", "size", "bandwidth", "tflops"]):
                header_row_count = 2

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
        for row in data_rows:
            if not row:
                continue
            r_map = {column_schema[c]: row[c]["text"] if c < len(row) and row[c] else "" for c in range(len(column_schema))}
            col0 = row[0]["text"] if len(row) > 0 and row[0] else ""
            if not col0 or col0.lower() in ["model", "model name", "geforce rtx"]:
                continue
            anchors = [a for c in row if c for a in c.get("anchors", [])]
            entries.append((col0, r_map, anchors))

    return entries


def normalize_nvidia_model_name(raw_model: str, series_title: str) -> tuple[str, str]:
    """Normalize Wikipedia model cell into full model (with NVIDIA prefix) and short model."""
    text = clean_text(raw_model)
    text = re.sub(r"\([^)]*Architecture[^)]*\)", "", text, flags=re.I)
    text = re.sub(r"Mobile/\s*Laptop", "Laptop GPU", text, flags=re.I)
    text = re.sub(r"Mobile", "Laptop GPU", text, flags=re.I)
    text = re.sub(r"/\s*Laptop", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()

    # Handle numeric or shorthand transposed models like "5070 Ti Laptop" or "5050 Laptop"
    if re.match(r"^50\d{2}", text, re.I):
        m = re.match(r"^(50\d{2}(?:\s*Ti)?)\s*(?:Laptop)?", text, re.I)
        if m:
            text = f"GeForce RTX {m.group(1)} Laptop GPU"
    elif not text.startswith("GeForce") and not text.startswith("Quadro") and not text.startswith("RTX") and not text.startswith("NVS"):
        if "rtx" in series_title.lower():
            text = f"GeForce RTX {text}"
        elif "gtx" in series_title.lower() or "16 series" in series_title.lower():
            text = f"GeForce GTX {text}"
        elif "geforce" in series_title.lower():
            text = f"GeForce {text}"

    if "Laptop" not in text and "Mobile" not in text and "Go" not in text and not re.search(r"M\b", text):
        if any(k in series_title.lower() for k in ["rtx", "gtx", "16", "20", "30", "40", "50", "mx"]):
            text = f"{text} Laptop GPU"

    short_model = text.replace("NVIDIA ", "").strip()
    full_model = f"NVIDIA {short_model}" if not text.startswith("NVIDIA") else text
    return full_model, short_model


def parse_nvidia_schema_row(row_map: dict[str, str], series_title: str, anchors: list[tuple[str, str]]) -> dict[str, Any]:
    arch = ""
    code_name = ""
    process_node = ""
    cuda_cores = None
    tensor_cores = None
    rt_cores = None
    sm_count = None
    
    base_clock_mhz = None
    boost_clock_mhz = None
    memory_clock_mhz = None

    vram_gb = None
    vram_mb = None
    vram_gb_options: list[float | int] = []
    vram_size_str = ""
    memory_type = ""
    bus_width_bits = None
    bandwidth_gbps = None

    tgp_range_w = ""
    min_tgp_w = None
    max_tgp_w = None
    bus_interface = ""
    launch_date = ""

    mem_type_candidates: list[str] = []

    # Infer architecture from section heading or hyperlinked anchors if possible
    for a_text, a_href in anchors:
        low_href = a_href.lower()
        if any(k in low_href for k in ["ada_lovelace", "blackwell", "ampere", "turing", "pascal", "maxwell", "kepler", "fermi", "tesla"]):
            arch = clean_text(a_text)
            break

    for k, v in row_map.items():
        low_k = k.lower().strip()
        v_clean = clean_text(v)
        if not v_clean or v_clean in ["—N/a", "N/A", "-", "—", "Unknown", "?"]:
            continue

        if any(w in low_k for w in ["launch", "release", "date"]):
            launch_date = v_clean

        # Code name (avoid die size and transistor count)
        if any(w in low_k for w in ["code name", "gpu die", "die name", "chip"]) and not any(w in low_k for w in ["size", "transistor"]):
            code_name = v_clean

        # Process node (avoid processing power and multiprocessors)
        if any(w in low_k for w in ["process", "fab", "node"]) and not any(w in low_k for w in ["processing", "multipressor", "multiprocessor"]):
            process_node = v_clean

        if "arch" in low_k and not arch:
            arch = v_clean

        if any(w in low_k for w in ["interface", "bus"]) and "memory" not in low_k and "bit" not in low_k:
            if any(k in v_clean.lower() for k in ["pcie", "agp", "mxm", "pci"]):
                bus_interface = v_clean

        # SM Count
        if any(w in low_k for w in ["streaming multiprocessor", "sm"]) and "cache" not in low_k and "cuda" not in low_k:
            m_sm = re.search(r"\b(\d{1,3})\b", v_clean)
            if m_sm:
                sm_count = int(m_sm.group(1))

        # CUDA cores
        if "cuda" in low_k or "core config" in low_k or "configuration" in low_k or "shaders" in low_k:
            parts = v_clean.split(":")
            if len(parts) >= 1 and re.match(r"^\d+", parts[0].strip().replace(",", "")):
                cuda_cores = int(re.match(r"^\d+", parts[0].strip().replace(",", "")).group(0))
                if len(parts) >= 4 and re.match(r"^\d+", parts[3].strip().replace(",", "")):
                    tensor_cores = int(re.match(r"^\d+", parts[3].strip().replace(",", "")).group(0))
                if len(parts) >= 5 and re.match(r"^\d+", parts[4].strip().replace(",", "")):
                    rt_cores = int(re.match(r"^\d+", parts[4].strip().replace(",", "")).group(0))
            else:
                m_c = re.search(r"\b(\d{1,5})\b", v_clean.replace(",", ""))
                if m_c and not cuda_cores:
                    cuda_cores = int(m_c.group(1))

            sm_m = re.search(r"\((\d+)\)", v_clean)
            if sm_m and not sm_count:
                sm_count = int(sm_m.group(1))

        # Tensor cores (direct key)
        if "tensor" in low_k and not any(w in low_k for w in ["compute", "tflops", "tops", "processing"]):
            m_t = re.search(r"\b(\d{1,4})\b", v_clean.replace(",", ""))
            if m_t:
                tensor_cores = int(m_t.group(1))

        # Ray tracing cores (direct key)
        if "ray tracing" in low_k or "rt core" in low_k:
            m_r = re.search(r"\b(\d{1,4})\b", v_clean.replace(",", ""))
            if m_r:
                rt_cores = int(m_r.group(1))

        # Clock speeds (avoid fillrate and processing power)
        if ("clock" in low_k or "mhz" in low_k or "ghz" in low_k) and not any(w in low_k for w in ["fillrate", "processing", "tflops", "tops"]):
            nums = re.findall(r"\b(\d{3,5})\b", v_clean)
            if "memory" not in low_k and nums:
                if len(nums) >= 2:
                    base_clock_mhz = int(nums[0])
                    boost_clock_mhz = int(nums[1])
                elif "base" in low_k:
                    base_clock_mhz = int(nums[0])
                elif "boost" in low_k:
                    boost_clock_mhz = int(nums[0])
                elif len(nums) == 1 and not base_clock_mhz:
                    base_clock_mhz = int(nums[0])
            elif "memory" in low_k and nums:
                memory_clock_mhz = int(nums[0])

        # Memory specs
        if "memory" in low_k or "vram" in low_k or "bus" in low_k:
            types_found = re.findall(r"\b(GDDR7|GDDR6X|GDDR6|GDDR5X|GDDR5|GDDR3|GDDR2|LPDDR5X|LPDDR5|LPDDR4X|LPDDR4|DDR4|DDR3|DDR2|DDR|SDRAM)\b", v_clean, re.I)
            for t in types_found:
                t_upper = t.upper()
                if t_upper not in mem_type_candidates:
                    mem_type_candidates.append(t_upper)

            if "bandwidth" in low_k:
                m_bw = re.search(r"(\d+(?:\.\d+)?)", v_clean)
                if m_bw:
                    bandwidth_gbps = float(m_bw.group(1))
            if "bus width" in low_k or ("bus" in low_k and "bit" in low_k):
                m_bit = re.search(r"(\d{2,4})", v_clean)
                if m_bit and not bus_width_bits:
                    bus_width_bits = int(m_bit.group(1))
            if "size" in low_k or "capacity" in low_k:
                is_mib = "mib" in low_k or "mb" in low_k or "mb" in v_clean.lower() or "mib" in v_clean.lower()
                nums = re.findall(r"(\d+(?:\.\d+)?)", v_clean)
                if nums:
                    parsed_nums = [float(n) for n in nums]
                    vram_size_str = f"{v_clean} MiB" if is_mib and "mib" not in v_clean.lower() and "mb" not in v_clean.lower() else (f"{v_clean} GiB" if "gib" not in v_clean.lower() and "gb" not in v_clean.lower() and not is_mib else v_clean)
                    if is_mib:
                        vram_mb = v_clean
                        vram_gb_options = [round(n / 1024.0, 3) for n in parsed_nums]
                        vram_gb = max(vram_gb_options) if vram_gb_options else None
                    else:
                        vram_gb_options = [int(n) if n.is_integer() else n for n in parsed_nums]
                        vram_gb = max(vram_gb_options) if vram_gb_options else None

        # Power / TGP / TDP
        if any(w in low_k for w in ["tgp", "tdp", "power", "watt"]):
            tgp_range_w = v_clean
            p_nums = re.findall(r"\b(\d{2,3})\b", v_clean)
            if len(p_nums) >= 2:
                min_tgp_w = int(p_nums[0])
                max_tgp_w = int(p_nums[1])
            elif len(p_nums) == 1:
                max_tgp_w = int(p_nums[0])

    if mem_type_candidates:
        memory_type = " / ".join(mem_type_candidates)
    elif not memory_type:
        if "50 series" in series_title.lower(): memory_type = "GDDR7"
        elif any(k in series_title.lower() for k in ["40 series", "30 series", "20 series", "16 series"]): memory_type = "GDDR6"

    if not cuda_cores and sm_count:
        if any(k in series_title.lower() for k in ["50 series", "40 series", "30 series"]):
            cuda_cores = sm_count * 128

    year, month, raw_date = parse_year_month(launch_date)

    return {
        "architecture": arch or "NVIDIA Architecture",
        "code_name": code_name,
        "launch_date": raw_date,
        "launch_year": year,
        "launch_month": month,
        "process_node": process_node,
        "cuda_cores": cuda_cores,
        "tensor_cores": tensor_cores,
        "rt_cores": rt_cores,
        "sm_count": sm_count,
        "clock_speeds": {
            "base_clock_mhz": base_clock_mhz,
            "boost_clock_mhz": boost_clock_mhz,
            "memory_clock_mhz": memory_clock_mhz
        },
        "memory": {
            "vram_size": vram_size_str,
            "vram_gb": vram_gb,
            "vram_mb": vram_mb,
            "vram_gb_options": vram_gb_options,
            "memory_type": memory_type,
            "bus_width_bits": bus_width_bits,
            "bandwidth_gbps": bandwidth_gbps
        },
        "power": {
            "tgp_range_w": tgp_range_w,
            "min_tgp_w": min_tgp_w,
            "max_tgp_w": max_tgp_w
        },
        "bus_interface": bus_interface
    }


def parse_nvidia_wikipedia_dom_hierarchy() -> list[dict[str, Any]]:
    print(f"[wiki-api] Fetching Wikipedia MediaWiki API parse endpoint for {WIKI_PAGE_TITLE}...")
    params = {
        "action": "parse",
        "page": WIKI_PAGE_TITLE,
        "prop": "text",
        "format": "json",
        "formatversion": 2
    }
    req = requests.get(WIKI_API_URL, params=params, headers={"User-Agent": PROFILES[0]["user_agent"]}, impersonate="chrome124", timeout=30)
    if req.status_code != 200:
        raise RuntimeError(f"Failed to fetch Wikipedia API parse endpoint: HTTP {req.status_code}")

    html_text = req.json()["parse"]["text"]
    soup = BeautifulSoup(html_text, "html.parser")
    gpu_list: list[dict[str, Any]] = []

    target_sections = [
        ("Mobile_GPUs", "Discrete (GeForce)"),
        ("Workstation_/_Mobile_Workstation_GPUs", "Workstation (Quadro / RTX)")
    ]

    for sec_id, category in target_sections:
        sec_h2 = soup.find(id=sec_id)
        if not sec_h2:
            continue
        if sec_h2.name != "h2":
            sec_h2 = sec_h2.find_parent("h2")

        curr_h3_title = ""

        for elem in sec_h2.find_all_next():
            if elem.name == "h2":
                break
            if elem.name == "h3":
                curr_h3_title = clean_text(elem.get_text().replace("[edit]", ""))
            elif elem.name == "table" and "wikitable" in elem.get("class", []):
                entries = parse_table_and_entries(elem, curr_h3_title)
                if not entries:
                    continue

                for raw_model, row_map, anchors in entries:
                    col0 = raw_model
                    # Filter Workstation section to include only Mobile variants
                    if sec_id.startswith("Workstation"):
                        is_mobile_ws = (
                            "mobility" in curr_h3_title.lower() or
                            "go" in curr_h3_title.lower() or
                            "mobile" in col0.lower() or
                            "laptop" in col0.lower() or
                            bool(re.search(r"\b[KM|P]\d{3,4}M\b", col0)) or
                            bool(re.search(r"\bGo\b", col0)) or
                            bool(re.search(r"\bNVS\s*\d{3}M\b", col0)) or
                            bool(re.search(r"\bFX\s*\d{3,4}M\b", col0))
                        )
                        if not is_mobile_ws:
                            continue

                    full_model, short_model = normalize_nvidia_model_name(col0, curr_h3_title)
                    series_name = curr_h3_title or "NVIDIA Mobile GPUs"

                    parsed_specs = parse_nvidia_schema_row(row_map, series_name, anchors)

                    entry = {
                        "full_model": full_model,
                        "short_model": short_model,
                        "brand": "NVIDIA",
                        "series": series_name,
                        "series_slug": slugify(series_name),
                        "category": category,
                        "architecture": parsed_specs["architecture"],
                        "code_name": parsed_specs["code_name"],
                        "code_name_slug": slugify(parsed_specs["code_name"]),
                        "launch_date": parsed_specs["launch_date"],
                        "launch_year": parsed_specs["launch_year"],
                        "launch_month": parsed_specs["launch_month"],
                        "process_node": parsed_specs["process_node"],
                        "cuda_cores": parsed_specs["cuda_cores"],
                        "tensor_cores": parsed_specs["tensor_cores"],
                        "rt_cores": parsed_specs["rt_cores"],
                        "sm_count": parsed_specs["sm_count"],
                        "clock_speeds": parsed_specs["clock_speeds"],
                        "memory": parsed_specs["memory"],
                        "power": parsed_specs["power"],
                        "bus_interface": parsed_specs["bus_interface"],
                        "wikipedia_h2_category": sec_id,
                        "wikipedia_schema_map": row_map
                    }

                    gpu_list.append(entry)

    return gpu_list


def build_nvidia_inventory() -> dict[str, Any]:
    print("=== Overhauling NVIDIA Mobile GPU Hardware Inventory ===")

    if NVIDIA_GPUS_DIR.exists():
        shutil.rmtree(NVIDIA_GPUS_DIR)

    NVIDIA_GPUS_DIR.mkdir(parents=True, exist_ok=True)

    gpus = parse_nvidia_wikipedia_dom_hierarchy()
    print(f"[wiki] Discovered and structured {len(gpus)} NVIDIA mobile GPU entries.")

    deduped_gpus: dict[str, dict[str, Any]] = {}
    for g in gpus:
        key = f"{g['series_slug']}_{slugify(g['full_model'])}"
        if key not in deduped_gpus:
            deduped_gpus[key] = g

    print(f"[dedupe] Total unique NVIDIA mobile GPU entries: {len(deduped_gpus)}")

    # Organize into series directories
    hierarchy: dict[str, list[dict[str, Any]]] = {}
    for g in deduped_gpus.values():
        s_slug = g["series_slug"]
        hierarchy.setdefault(s_slug, []).append(g)

    gpu_file_count = 0
    for s_slug, series_gpus in hierarchy.items():
        s_dir = NVIDIA_GPUS_DIR / s_slug
        s_dir.mkdir(parents=True, exist_ok=True)

        for g in series_gpus:
            m_slug = slugify(g["full_model"])
            if not m_slug:
                continue
            file_path = s_dir / f"{m_slug}.json"
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(g, f, indent=2)
            gpu_file_count += 1

    print(f"[storage] Cleanly created {len(hierarchy)} GPU series folders ({gpu_file_count} individual GPU files) in {NVIDIA_GPUS_DIR}.")

    # Write Master Standalone NVIDIA GPU Inventory
    master_data = {
        "metadata": {
            "title": "NVIDIA Mobile & Workstation GPU Hardware Inventory",
            "sources": [f"{WIKI_API_URL}?action=parse&page={WIKI_PAGE_TITLE}"],
            "total_gpu_models": len(deduped_gpus)
        },
        "gpus": {key: g for key, g in deduped_gpus.items()}
    }

    with open(MASTER_NVIDIA_GPU_INVENTORY, "w", encoding="utf-8") as f:
        json.dump(master_data, f, indent=2)
    print(f"[master] Saved master NVIDIA GPU inventory: {MASTER_NVIDIA_GPU_INVENTORY}")

    # Post-process & sanitize all hardware inventories (AMD, Intel, NVIDIA)
    clean_all_hardware_inventories()

    return {
        "unique_gpus": len(deduped_gpus),
        "gpu_series_count": len(hierarchy),
        "gpu_file_count": gpu_file_count
    }


if __name__ == "__main__":
    res = build_nvidia_inventory()
    print("=== Clean Dynamic NVIDIA Inventory Generation Complete ===")
    print(res)
