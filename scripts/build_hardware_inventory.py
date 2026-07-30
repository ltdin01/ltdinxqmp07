#!/usr/bin/env python3
"""
Hardware Inventory Builder for LaptopDeals.

Fetches and extracts mobile processor specs (AMD Ryzen & Intel 8th Gen+),
integrated GPUs (iGPUs), and discrete mobile/workstation GPUs (NVIDIA GeForce 16+ & Ada Workstation)
from Wikipedia and authoritative sources.
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "data"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}


def clean_text(text: str) -> str:
    if not text:
        return ""
    res = re.sub(r"\[[0-9a-zA-Z]+\]", "", str(text))
    res = res.replace("\xa0", " ").replace("â\u0084¢", "").replace("â\u00ae", "")
    res = re.sub(r"[™®]", "", res)
    return re.sub(r"\s+", " ", res).strip()


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode("utf-8")


def parse_float(val: Any) -> float | None:
    if val is None:
        return None
    text = clean_text(str(val))
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None


def parse_int(val: Any) -> int | None:
    if val is None:
        return None
    text = clean_text(str(val))
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) if match else None


def normalize_series_name(name: str) -> str:
    n = name.strip()
    n = re.sub(r"\bseries\b", "Series", n, flags=re.I)
    n = re.sub(r"\bgen\b", "Gen", n, flags=re.I)
    return n


# ==============================================================================
# iGPU Inventory Builder
# ==============================================================================

def enrich_igpu_inventory(igpu_data: dict[str, Any]) -> None:
    """
    Enrich iGPU inventory with exact specs (architecture, EUs/Xe-Cores/CUs, base & boost clocks).
    """
    igpu_specs_map = {
        "Intel": {
            "Intel Arc V-Series": [
                {"model": "Intel Arc 140V (8 Xe-Cores)", "short_model": "Intel Arc 140V", "series": "Intel Arc V-Series", "architecture": "Battlemage / Xe2-LPG", "xe_cores": 8, "eus": 128, "base_clock_mhz": 800, "boost_clock_mhz": 2050, "brand": "Intel"},
                {"model": "Intel Arc 130V (7 Xe-Cores)", "short_model": "Intel Arc 130V", "series": "Intel Arc V-Series", "architecture": "Battlemage / Xe2-LPG", "xe_cores": 7, "eus": 112, "base_clock_mhz": 800, "boost_clock_mhz": 1850, "brand": "Intel"}
            ],
            "Intel Arc Graphics Series": [
                {"model": "Intel Arc Graphics (8 Xe-Cores)", "short_model": "Intel Arc Graphics 8-Core", "series": "Intel Arc Graphics Series", "architecture": "Alchemist / Xe-LPG", "xe_cores": 8, "eus": 128, "base_clock_mhz": 500, "boost_clock_mhz": 2250, "brand": "Intel"},
                {"model": "Intel Arc Graphics (7 Xe-Cores)", "short_model": "Intel Arc Graphics 7-Core", "series": "Intel Arc Graphics Series", "architecture": "Alchemist / Xe-LPG", "xe_cores": 7, "eus": 112, "base_clock_mhz": 500, "boost_clock_mhz": 2200, "brand": "Intel"},
                {"model": "Intel Graphics (4 Xe-Cores)", "short_model": "Intel Graphics 4-Core", "series": "Intel Arc Graphics Series", "architecture": "Alchemist / Xe-LPG", "xe_cores": 4, "eus": 64, "base_clock_mhz": 300, "boost_clock_mhz": 2000, "brand": "Intel"}
            ],
            "Intel Iris Xe Series": [
                {"model": "Intel Iris Xe", "short_model": "Intel Iris Xe", "series": "Intel Iris Xe Series", "architecture": "Gen12 / Xe-LP", "eus": 96, "base_clock_mhz": 300, "boost_clock_mhz": 1500, "brand": "Intel"},
                {"model": "Intel Iris Xe Graphics 96EU", "short_model": "Intel Iris Xe 96EU", "series": "Intel Iris Xe Series", "architecture": "Gen12 / Xe-LP", "eus": 96, "base_clock_mhz": 300, "boost_clock_mhz": 1500, "brand": "Intel"},
                {"model": "Intel Iris Xe Graphics 80EU", "short_model": "Intel Iris Xe 80EU", "series": "Intel Iris Xe Series", "architecture": "Gen12 / Xe-LP", "eus": 80, "base_clock_mhz": 300, "boost_clock_mhz": 1400, "brand": "Intel"}
            ],
            "Intel UHD Graphics Series": [
                {"model": "Intel UHD Graphics", "short_model": "Intel UHD Graphics", "series": "Intel UHD Graphics Series", "architecture": "Gen9 / Gen12", "eus": 32, "base_clock_mhz": 300, "boost_clock_mhz": 1250, "brand": "Intel"},
                {"model": "Intel UHD Graphics 620", "short_model": "Intel UHD 620", "series": "Intel UHD Graphics Series", "architecture": "Gen9.5", "eus": 24, "base_clock_mhz": 300, "boost_clock_mhz": 1150, "brand": "Intel"},
                {"model": "Intel UHD Graphics 630", "short_model": "Intel UHD 630", "series": "Intel UHD Graphics Series", "architecture": "Gen9.5", "eus": 24, "base_clock_mhz": 350, "boost_clock_mhz": 1200, "brand": "Intel"},
                {"model": "Intel Graphics", "short_model": "Intel Graphics", "series": "Intel UHD Graphics Series", "architecture": "Intel Architecture", "eus": 32, "base_clock_mhz": 300, "boost_clock_mhz": 1200, "brand": "Intel"}
            ]
        },
        "AMD": {
            "Radeon 800M Series": [
                {"model": "Radeon 890M", "short_model": "Radeon 890M", "series": "Radeon 800M Series", "architecture": "RDNA 3.5", "cus": 16, "base_clock_mhz": 800, "boost_clock_mhz": 2900, "brand": "AMD"},
                {"model": "Radeon 880M", "short_model": "Radeon 880M", "series": "Radeon 800M Series", "architecture": "RDNA 3.5", "cus": 12, "base_clock_mhz": 800, "boost_clock_mhz": 2900, "brand": "AMD"},
                {"model": "Radeon 860M", "short_model": "Radeon 860M", "series": "Radeon 800M Series", "architecture": "RDNA 3.5", "cus": 8, "base_clock_mhz": 800, "boost_clock_mhz": 2800, "brand": "AMD"},
                {"model": "Radeon 840M", "short_model": "Radeon 840M", "series": "Radeon 800M Series", "architecture": "RDNA 3.5", "cus": 6, "base_clock_mhz": 800, "boost_clock_mhz": 2700, "brand": "AMD"}
            ],
            "Radeon 700M Series": [
                {"model": "Radeon 780M", "short_model": "Radeon 780M", "series": "Radeon 700M Series", "architecture": "RDNA 3", "cus": 12, "base_clock_mhz": 800, "boost_clock_mhz": 2700, "brand": "AMD"},
                {"model": "Radeon 760M", "short_model": "Radeon 760M", "series": "Radeon 700M Series", "architecture": "RDNA 3", "cus": 8, "base_clock_mhz": 800, "boost_clock_mhz": 2600, "brand": "AMD"},
                {"model": "Radeon 740M", "short_model": "Radeon 740M", "series": "Radeon 700M Series", "architecture": "RDNA 3", "cus": 4, "base_clock_mhz": 800, "boost_clock_mhz": 2500, "brand": "AMD"},
                {"model": "Radeon 610M", "short_model": "Radeon 610M", "series": "Radeon 700M Series", "architecture": "RDNA 2", "cus": 2, "base_clock_mhz": 400, "boost_clock_mhz": 1900, "brand": "AMD"}
            ],
            "Radeon 600M Series": [
                {"model": "Radeon 680M", "short_model": "Radeon 680M", "series": "Radeon 600M Series", "architecture": "RDNA 2", "cus": 12, "base_clock_mhz": 500, "boost_clock_mhz": 2200, "brand": "AMD"},
                {"model": "Radeon 660M", "short_model": "Radeon 660M", "series": "Radeon 600M Series", "architecture": "RDNA 2", "cus": 6, "base_clock_mhz": 500, "boost_clock_mhz": 1900, "brand": "AMD"}
            ],
            "Radeon RX Vega Series": [
                {"model": "Radeon RX Vega 8", "short_model": "Vega 8", "series": "Radeon RX Vega Series", "architecture": "GCN 5.0", "cus": 8, "base_clock_mhz": 300, "boost_clock_mhz": 2000, "brand": "AMD"},
                {"model": "Radeon RX Vega 7", "short_model": "Vega 7", "series": "Radeon RX Vega Series", "architecture": "GCN 5.0", "cus": 7, "base_clock_mhz": 300, "boost_clock_mhz": 1800, "brand": "AMD"},
                {"model": "Radeon RX Vega 6", "short_model": "Vega 6", "series": "Radeon RX Vega Series", "architecture": "GCN 5.0", "cus": 6, "base_clock_mhz": 300, "boost_clock_mhz": 1500, "brand": "AMD"},
                {"model": "Integrated AMD Radeon", "short_model": "Radeon Graphics", "series": "Radeon RX Vega Series", "architecture": "GCN", "cus": 6, "base_clock_mhz": 300, "boost_clock_mhz": 1600, "brand": "AMD"}
            ]
        },
        "Qualcomm": {
            "Qualcomm Adreno Series": [
                {"model": "Qualcomm Adreno GPU", "short_model": "Adreno GPU", "series": "Qualcomm Adreno Series", "architecture": "Adreno 740 / 750", "base_clock_mhz": 800, "boost_clock_mhz": 1250, "brand": "Qualcomm"}
            ]
        }
    }

    for b, s_dict in igpu_specs_map.items():
        igpu_data.setdefault(b, {})
        for s_name, models in s_dict.items():
            igpu_data[b].setdefault(s_name, [])
            existing_models = {m.get("model") for m in igpu_data[b][s_name]}
            for item in models:
                if item["model"] not in existing_models:
                    igpu_data[b][s_name].append(item)


def build_igpu_inventory() -> dict[str, Any]:
    print("[1/3] Building iGPU Inventory...")
    
    intel_url = "https://en.wikipedia.org/wiki/List_of_Intel_graphics_processing_units"
    amd_url = "https://en.wikipedia.org/wiki/List_of_AMD_graphics_processing_units"
    
    igpu_data: dict[str, Any] = {
        "Intel": {},
        "AMD": {},
        "Qualcomm": {}
    }
    
    from bs4 import BeautifulSoup

    # Scrape Intel iGPUs
    try:
        intel_html = fetch_html(intel_url)
        intel_soup = BeautifulSoup(intel_html, "html.parser")
        for table in intel_soup.find_all("table", class_="wikitable"):
            for r in table.find_all("tr"):
                cols = [clean_text(td.get_text()) for td in r.find_all(["th", "td"])]
                if not cols:
                    continue
                name = cols[0]
                if any(k in name for k in ["Arc", "Iris", "UHD", "HD Graphics"]):
                    series_name = "Intel Arc V-Series" if "140V" in name or "130V" in name else ("Intel Arc Graphics Series" if "Arc" in name else ("Intel Iris Xe Series" if "Iris" in name else "Intel UHD Graphics Series"))
                    igpu_data["Intel"].setdefault(series_name, [])
                    if not any(m.get("model") == name for m in igpu_data["Intel"][series_name]):
                        igpu_data["Intel"][series_name].append({
                            "model": name,
                            "series": series_name,
                            "brand": "Intel",
                            "architecture": "Intel Architecture"
                        })
    except Exception as e:
        print(f"    Notice: Intel Wikipedia iGPU scrape fallback ({e})")

    # Scrape AMD iGPUs
    try:
        amd_html = fetch_html(amd_url)
        amd_soup = BeautifulSoup(amd_html, "html.parser")
        for table in amd_soup.find_all("table", class_="wikitable"):
            for r in table.find_all("tr"):
                cols = [clean_text(td.get_text()) for td in r.find_all(["th", "td"])]
                if not cols:
                    continue
                row_str = " ".join(cols)
                m = re.search(r"\b(Radeon\s+(?:8\d{2}M|7\d{2}M|6\d{2}M|RX\s+Vega\s+\d+|Vega\s+\d+))\b", row_str, re.I)
                if m:
                    model_name = m.group(1)
                    series_name = "Radeon 800M Series" if "8" in model_name and "800M" not in model_name else ("Radeon 700M Series" if "7" in model_name else ("Radeon 600M Series" if "6" in model_name else "Radeon RX Vega Series"))
                    igpu_data["AMD"].setdefault(series_name, [])
                    if not any(item.get("model") == model_name for item in igpu_data["AMD"][series_name]):
                        igpu_data["AMD"][series_name].append({
                            "model": model_name,
                            "series": series_name,
                            "brand": "AMD",
                            "architecture": "RDNA / Vega"
                        })
    except Exception as e:
        print(f"    Notice: AMD Wikipedia iGPU scrape fallback ({e})")

    enrich_igpu_inventory(igpu_data)

    total_igpus = sum(len(models) for brand in igpu_data.values() for models in brand.values())
    print(f"    Compiled {total_igpus} iGPU models across Intel, AMD, and Qualcomm series.")
    
    return {
        "metadata": {
            "title": "Integrated GPU (iGPU) Hardware Inventory",
            "sources": [intel_url, amd_url],
            "total_igpu_models": total_igpus
        },
        "igpus": igpu_data
    }



# ==============================================================================
# GPU Inventory Builder (NVIDIA GeForce 16+ & Ada Workstation)
# ==============================================================================

def build_gpu_inventory() -> dict[str, Any]:
    print("[2/3] Building dGPU Inventory...")
    url = "https://en.wikipedia.org/wiki/List_of_Nvidia_graphics_processing_units"
    html = fetch_html(url)
    
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    
    gpu_data: dict[str, Any] = {
        "Discrete (GeForce)": {},
        "Workstation (Ada Generation)": {}
    }
    
    mobile_series_map = [
        ("GeForce 16 series", "GeForce GTX 16 Series"),
        ("GeForce RTX 20 series", "GeForce RTX 20 Series"),
        ("GeForce RTX 30 series", "GeForce RTX 30 Series"),
        ("GeForce RTX 40 series", "GeForce RTX 40 Series"),
        ("GeForce RTX 50 series", "GeForce RTX 50 Series"),
    ]
    
    headings = soup.find_all(['h2', 'h3', 'h4', 'div'], class_=re.compile(r'mw-heading'))
    
    for h in headings:
        h_text = clean_text(h.get_text())
        for term, series_name in mobile_series_map:
            if term.lower() in h_text.lower():
                node = h
                while node:
                    node = node.next_element
                    if not node:
                        break
                    tag = getattr(node, 'name', None)
                    if tag in ['h2', 'h3'] and term.lower() not in clean_text(node.get_text()).lower():
                        break
                    if tag == 'table' and 'wikitable' in node.get('class', []):
                        rows = node.find_all('tr')
                        parse_geforce_mobile_table(rows, series_name, gpu_data["Discrete (GeForce)"])
                        break
                        
    for t in soup.find_all('table', class_='wikitable'):
        t_text = t.get_text()
        if 'Ada' in t_text and ('Mobile' in t_text or 'RTX 500 Mobile' in t_text or 'RTX 1000 Mobile' in t_text or 'RTX 2000 Mobile' in t_text):
            rows = t.find_all('tr')
            parse_ada_workstation_table(rows, gpu_data["Workstation (Ada Generation)"])

    enrich_gpu_inventory(gpu_data)

    total_gpus = sum(len(models) for series in gpu_data.values() for models in series.values())
    print(f"    Extracted {total_gpus} dGPU models.")
    
    return {
        "metadata": {
            "title": "NVIDIA Mobile & Workstation GPU Inventory",
            "sources": [url],
            "total_gpu_models": total_gpus,
            "filter_rules": "GeForce 16 Series+ and Ada Generation Workstation Mobile GPUs only"
        },
        "gpus": gpu_data
    }


def parse_geforce_mobile_table(rows: list[Any], series_name: str, target_dict: dict[str, Any]) -> None:
    if not rows:
        return
    target_dict.setdefault(series_name, {})
    
    for r in rows:
        cols = [clean_text(c.get_text()) for c in r.find_all(['th', 'td'])]
        if not cols:
            continue
        model_candidate = cols[0]
        if not any(k in model_candidate for k in ['GeForce', 'GTX 16', 'RTX 20', 'RTX 30', 'RTX 40', 'RTX 50', '5050', '5060', '5070', '5080', '5090']):
            continue
        if model_candidate.lower() in ['model', 'branding and model', 'model name', 'release date']:
            continue
            
        name = model_candidate
        if not name.startswith("NVIDIA"):
            name = f"NVIDIA {name}"
        if "Laptop" not in name and "Mobile" not in name and "Max-Q" not in name:
            name += " Laptop GPU"
            
        short_name = name.replace("NVIDIA ", "").replace(" Mobile/Laptop", "").replace(" Mobile", "").replace(" Laptop GPU", "").strip()
        
        col_str = " ".join(cols)
        code_name = next((c for c in cols if re.search(r'\b(TU\d+|GA\d+|AD\d+|GB\d+)\b', c)), "NVIDIA Architecture")
        cuda_match = re.search(r'\b(\d{3,5})\b', col_str)
        cuda_cores = int(cuda_match.group(1)) if cuda_match else None
        vram_match = re.search(r'\b(\d{1,2})\s*(?:GB|GiB)\b', col_str)
        vram_gb = int(vram_match.group(1)) if vram_match else None
        
        tgp_match = re.search(r'(\d{2,3}\s*[-–]\s*\d{2,3}\s*W|\d{2,3}\s*W)', col_str)
        tgp = tgp_match.group(1) if tgp_match else "35-115W"

        target_dict[series_name][short_name] = {
            "model": name,
            "short_model": short_name,
            "brand": "NVIDIA",
            "series": series_name,
            "architecture": code_name,
            "cuda_cores": cuda_cores,
            "memory": {
                "vram_gb_options": [vram_gb] if vram_gb else [4, 6, 8, 12, 16],
                "memory_type": "GDDR7" if "50 Series" in series_name else "GDDR6",
            },
            "power": {
                "tgp_range": tgp
            }
        }


def parse_ada_workstation_table(rows: list[Any], target_dict: dict[str, Any]) -> None:
    series_name = "RTX Ada Generation Workstation"
    target_dict.setdefault(series_name, {})
    
    for r in rows:
        cols = [clean_text(c.get_text()) for c in r.find_all(['th', 'td'])]
        if not cols:
            continue
        model_candidate = cols[0]
        if 'RTX' in model_candidate and 'Ada' in model_candidate and 'Mobile' in model_candidate:
            short_name = model_candidate.replace(" Mobile", "").replace(" Generation", " Ada").strip()
            full_name = f"NVIDIA {model_candidate}"
            if "Laptop" not in full_name:
                full_name += " Laptop GPU"
                
            code_name = next((c for c in cols if re.search(r'\bAD\d+\b', c)), "AD107/AD106/AD104/AD103")
            cuda_match = re.search(r'(\d{4})\s*:\s*\d+', " ".join(cols))
            cuda_cores = int(cuda_match.group(1)) if cuda_match else None
            vram_match = re.search(r'\b(\d{1,2})\s*(?:\(ECC\)|GB|GiB)?\b', " ".join(cols[4:]))
            vram_gb = int(vram_match.group(1)) if vram_match else None
            
            target_dict[series_name][short_name] = {
                "model": full_name,
                "short_model": short_name,
                "brand": "NVIDIA",
                "series": series_name,
                "architecture": f"Ada Lovelace ({code_name})",
                "cuda_cores": cuda_cores,
                "memory": {
                    "vram_gb_options": [vram_gb] if vram_gb else [4, 6, 8, 12, 16],
                    "memory_type": "GDDR6 ECC"
                },
                "power": {
                    "tgp_range": "35-175W"
                }
            }


def enrich_gpu_inventory(data: dict[str, Any]) -> None:
    defaults = {
        "Discrete (GeForce)": {
            "GeForce GTX 16 Series": [
                ("GeForce GTX 1650", "NVIDIA GeForce GTX 1650 Laptop GPU", 1024, 4, "GDDR6", "35-50W"),
                ("GeForce GTX 1650 Ti", "NVIDIA GeForce GTX 1650 Ti Laptop GPU", 1024, 4, "GDDR6", "35-55W"),
                ("GeForce GTX 1660 Ti", "NVIDIA GeForce GTX 1660 Ti Laptop GPU", 1536, 6, "GDDR6", "60-80W")
            ],
            "GeForce RTX 20 Series": [
                ("GeForce RTX 2050", "NVIDIA GeForce RTX 2050 Laptop GPU", 2048, 4, "GDDR6", "30-45W"),
                ("GeForce RTX 2060", "NVIDIA GeForce RTX 2060 Laptop GPU", 1920, 6, "GDDR6", "80-115W"),
                ("GeForce RTX 2070", "NVIDIA GeForce RTX 2070 Laptop GPU", 2304, 8, "GDDR6", "115W"),
                ("GeForce RTX 2070 Super", "NVIDIA GeForce RTX 2070 Super Laptop GPU", 2560, 8, "GDDR6", "115W"),
                ("GeForce RTX 2080", "NVIDIA GeForce RTX 2080 Laptop GPU", 2944, 8, "GDDR6", "150W"),
                ("GeForce RTX 2080 Super", "NVIDIA GeForce RTX 2080 Super Laptop GPU", 3072, 8, "GDDR6", "150W")
            ],
            "GeForce RTX 30 Series": [
                ("GeForce RTX 3050", "NVIDIA GeForce RTX 3050 Laptop GPU", 2048, 4, "GDDR6", "35-80W"),
                ("GeForce RTX 3050 6GB", "NVIDIA GeForce RTX 3050 6GB Laptop GPU", 2560, 6, "GDDR6", "35-95W"),
                ("GeForce RTX 3050 Ti", "NVIDIA GeForce RTX 3050 Ti Laptop GPU", 2560, 4, "GDDR6", "35-80W"),
                ("GeForce RTX 3060", "NVIDIA GeForce RTX 3060 Laptop GPU", 3840, 6, "GDDR6", "60-130W"),
                ("GeForce RTX 3070", "NVIDIA GeForce RTX 3070 Laptop GPU", 5120, 8, "GDDR6", "80-140W"),
                ("GeForce RTX 3070 Ti", "NVIDIA GeForce RTX 3070 Ti Laptop GPU", 5888, 8, "GDDR6", "80-150W"),
                ("GeForce RTX 3080", "NVIDIA GeForce RTX 3080 Laptop GPU", 6144, 16, "GDDR6", "80-165W"),
                ("GeForce RTX 3080 Ti", "NVIDIA GeForce RTX 3080 Ti Laptop GPU", 7424, 16, "GDDR6", "80-175W")
            ],
            "GeForce RTX 40 Series": [
                ("GeForce RTX 4050", "NVIDIA GeForce RTX 4050 Laptop GPU", 2560, 6, "GDDR6", "35-115W"),
                ("GeForce RTX 4060", "NVIDIA GeForce RTX 4060 Laptop GPU", 3072, 8, "GDDR6", "35-115W"),
                ("GeForce RTX 4070", "NVIDIA GeForce RTX 4070 Laptop GPU", 4608, 8, "GDDR6", "35-115W"),
                ("GeForce RTX 4080", "NVIDIA GeForce RTX 4080 Laptop GPU", 7424, 12, "GDDR6", "60-150W"),
                ("GeForce RTX 4090", "NVIDIA GeForce RTX 4090 Laptop GPU", 9728, 16, "GDDR6", "80-150W")
            ],
            "GeForce RTX 50 Series": [
                ("GeForce RTX 5050", "NVIDIA GeForce RTX 5050 Laptop GPU", 3072, 8, "GDDR7", "50-100W"),
                ("GeForce RTX 5060", "NVIDIA GeForce RTX 5060 Laptop GPU", 3840, 8, "GDDR7", "45-100W"),
                ("GeForce RTX 5070", "NVIDIA GeForce RTX 5070 Laptop GPU", 4608, 8, "GDDR7", "50-100W"),
                ("GeForce RTX 5070 Ti", "NVIDIA GeForce RTX 5070 Ti Laptop GPU", 5888, 12, "GDDR7", "60-115W"),
                ("GeForce RTX 5080", "NVIDIA GeForce RTX 5080 Laptop GPU", 7680, 16, "GDDR7", "80-150W"),
                ("GeForce RTX 5090", "NVIDIA GeForce RTX 5090 Laptop GPU", 10496, 24, "GDDR7", "100-175W")
            ]
        },
        "Workstation (Ada Generation)": {
            "RTX Ada Generation Workstation": [
                ("RTX 500 Ada Generation", "NVIDIA RTX 500 Mobile Ada Generation Laptop GPU", 2048, 4, "GDDR6", "35-60W"),
                ("RTX 1000 Ada Generation", "NVIDIA RTX 1000 Mobile Ada Generation Laptop GPU", 2560, 6, "GDDR6", "35-100W"),
                ("RTX 2000 Ada Generation", "NVIDIA RTX 2000 Mobile Ada Generation Laptop GPU", 3072, 8, "GDDR6", "35-100W"),
                ("RTX 3000 Ada Generation", "NVIDIA RTX 3000 Mobile Ada Generation Laptop GPU", 4608, 8, "GDDR6 ECC", "40-115W"),
                ("RTX 3500 Ada Generation", "NVIDIA RTX 3500 Mobile Ada Generation Laptop GPU", 5120, 12, "GDDR6 ECC", "60-145W"),
                ("RTX 4000 Ada Generation", "NVIDIA RTX 4000 Mobile Ada Generation Laptop GPU", 7424, 12, "GDDR6 ECC", "80-175W"),
                ("RTX 5000 Ada Generation", "NVIDIA RTX 5000 Mobile Ada Generation Laptop GPU", 9728, 16, "GDDR6 ECC", "80-175W")
            ]
        }
    }
    
    for category, series_dict in defaults.items():
        data.setdefault(category, {})
        for series, item_list in series_dict.items():
            data[category].setdefault(series, {})
            for short_name, full_name, cuda, vram, mtype, tgp in item_list:
                if short_name not in data[category][series]:
                    data[category][series][short_name] = {
                        "model": full_name,
                        "short_model": short_name,
                        "brand": "NVIDIA",
                        "series": series,
                        "architecture": "Ada Lovelace" if "Ada" in series or "40 Series" in series else ("Blackwell" if "50 Series" in series else ("Ampere" if "30 Series" in series else "Turing")),
                        "cuda_cores": cuda,
                        "memory": {
                            "vram_gb_options": [vram],
                            "memory_type": mtype
                        },
                        "power": {
                            "tgp_range": tgp
                        }
                    }


# ==============================================================================
# CPU Inventory Builder (AMD Ryzen & Intel 8th Gen+)
# ==============================================================================

def build_cpu_inventory() -> dict[str, Any]:
    print("[3/3] Building CPU Inventory...")
    
    cpu_data: dict[str, Any] = {
        "AMD": {},
        "Intel": {}
    }
    
    parse_amd_ryzen_cpus(cpu_data["AMD"])
    parse_intel_mobile_cpus(cpu_data["Intel"])
    cleanup_cpu_inventory(cpu_data)

    total_cpus = sum(len(models) for brand in cpu_data.values() for series in brand.values() for models in series.values())
    print(f"    Extracted {total_cpus} mobile CPU models.")
    
    return {
        "metadata": {
            "title": "Mobile CPU Hardware Inventory (AMD & Intel)",
            "total_cpu_models": total_cpus,
            "amd_series_count": len(cpu_data["AMD"]),
            "intel_series_count": len(cpu_data["Intel"]),
            "rules": "AMD Ryzen 2000-9000/AI 300-400 & Intel 8th Gen+ / Core Ultra / Core Series 1&2"
        },
        "processors": cpu_data
    }


def parse_amd_ryzen_cpus(target_amd: dict[str, Any]) -> None:
    url = "https://en.wikipedia.org/wiki/List_of_AMD_Ryzen_processors"
    html = fetch_html(url)
    
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    
    mobile_h2 = soup.find(id='Mobile_processors')
    if not mobile_h2:
        enrich_amd_cpus(target_amd)
        return
        
    curr_series = "Ryzen 7000 Series"
    curr_codename = "Zen Architecture"
    
    node = mobile_h2
    while node:
        node = node.next_element
        if not node:
            break
        tag = getattr(node, 'name', None)
        if tag in ['h2'] and clean_text(node.get_text()) != 'Mobile processors':
            break
        if tag in ['h3']:
            txt = clean_text(node.get_text()).replace('[edit]', '').strip()
            if 'Ryzen' in txt:
                curr_series = normalize_series_name(txt)
        elif tag in ['h4']:
            txt = clean_text(node.get_text()).replace('[edit]', '').strip()
            curr_codename = txt
        elif tag == 'table' and 'wikitable' in node.get('class', []):
            rows = node.find_all('tr')
            parse_amd_table_rows(rows, curr_series, curr_codename, target_amd)

    enrich_amd_cpus(target_amd)


def parse_amd_table_rows(rows: list[Any], series_name: str, codename: str, target_amd: dict[str, Any]) -> None:
    if len(rows) < 2:
        return
    
    for r in rows:
        cols = [clean_text(c.get_text()) for c in r.find_all(['th', 'td'])]
        if not cols:
            continue
        first_col = cols[0]
        
        if any(h in first_col.lower() for h in ['branding and model', 'model', 'cpu', 'cores', 'release date', 'architecture']):
            continue
            
        sku_match = re.search(r'\b(Ryzen\s+(?:AI\s+)?(?:PRO\s+)?\d?\s*(?:HX|HS|U|H)?\s*\d{3,4}[A-Z0-9]*|\d{4}[A-Z]{1,3})\b', " ".join(cols[:2]), re.I)
        if not sku_match:
            continue
            
        raw_sku = sku_match.group(1)
        if not raw_sku.startswith("Ryzen") and not raw_sku.startswith("AMD"):
            if "Ryzen" in first_col:
                raw_sku = f"{first_col} {raw_sku}"
            else:
                raw_sku = f"Ryzen {raw_sku}"
                
        if not raw_sku.startswith("AMD"):
            full_sku = f"AMD {raw_sku}".strip()
        else:
            full_sku = raw_sku.strip()
            
        short_model = full_sku.replace("AMD ", "").strip()
        
        if "AI 3" in full_sku: s_name = "Ryzen AI 300 Series"
        elif "AI 4" in full_sku: s_name = "Ryzen AI 400 Series"
        elif any(k in full_sku for k in ['8040', '8045', '884', '864']): s_name = "Ryzen 8000 Series"
        elif any(k in full_sku for k in ['7020', '7030', '7035', '7040', '7045', '773', '753', '784']): s_name = "Ryzen 7000 Series"
        elif any(k in full_sku for k in ['6800', '6600', '6900']): s_name = "Ryzen 6000 Series"
        elif any(k in full_sku for k in ['5800', '5600', '5700', '5500', '5400', '5900']): s_name = "Ryzen 5000 Series"
        elif any(k in full_sku for k in ['4800', '4700', '4600', '4500']): s_name = "Ryzen 4000 Series"
        elif any(k in full_sku for k in ['3700', '3500', '3300']): s_name = "Ryzen 3000 Series"
        elif any(k in full_sku for k in ['2700', '2500', '2300']): s_name = "Ryzen 2000 Series"
        else: s_name = normalize_series_name(series_name)

        target_amd.setdefault(s_name, {})

        col_str = " ".join(cols)
        cores = parse_int(re.search(r'(\d+)\s*\(\d+\)', col_str).group(1)) if re.search(r'(\d+)\s*\(\d+\)', col_str) else parse_int(re.search(r'\b([48]|10|12|16)\b', col_str))
        threads = parse_int(re.search(r'\(\s*(\d+)\s*\)', col_str).group(1)) if re.search(r'\(\s*(\d+)\s*\)', col_str) else (cores * 2 if cores else 16)
        
        igpu_model = "Integrated AMD Radeon"
        if "780M" in col_str: igpu_model = "Radeon 780M"
        elif "680M" in col_str: igpu_model = "Radeon 680M"
        elif "890M" in col_str: igpu_model = "Radeon 890M"
        elif "880M" in col_str: igpu_model = "Radeon 880M"
        elif "610M" in col_str: igpu_model = "Radeon 610M"
        elif "740M" in col_str: igpu_model = "Radeon 740M"
        elif "Vega" in col_str: igpu_model = "Radeon RX Vega"

        rebrand_ref = None
        if "7530U" in full_sku or "7730U" in full_sku: rebrand_ref = "Barcelo-R refresh of Ryzen 5000 (Cezanne/Barcelo)"
        elif "7735HS" in full_sku or "7535HS" in full_sku or "7335U" in full_sku: rebrand_ref = "Rembrandt-R refresh of Ryzen 6000 (Rembrandt)"
        elif "8840HS" in full_sku or "8845HS" in full_sku or "8640HS" in full_sku: rebrand_ref = "Hawk Point refresh of Phoenix (Ryzen 7040 series) with upgraded NPU"

        target_amd[s_name][short_model] = {
            "sku": full_sku,
            "model": short_model,
            "brand": "AMD",
            "series": s_name,
            "codename": codename,
            "cores": cores or 8,
            "threads": threads or 16,
            "clocks": {
                "base_clock_ghz": parse_float(re.search(r'(\d\.\d+)\s*[-–/]', col_str)),
                "boost_clock_ghz": parse_float(re.search(r'[-–/]\s*(\d\.\d+)', col_str))
            },
            "igpu": {
                "model": igpu_model
            },
            "npu": {
                "model": "Ryzen AI (XDNA)" if "7040" in col_str or "8040" in col_str or "AI" in full_sku else None,
                "tops": 50.0 if "AI 3" in full_sku or "AI 4" in full_sku else (16.0 if "8040" in col_str else (10.0 if "7040" in col_str else None))
            },
            "power": {
                "tdp_w": "15-28W" if "U" in full_sku else ("35-54W" if "HS" in full_sku else "55-75W")
            },
            "rebrand_reference": rebrand_ref
        }


def parse_intel_mobile_cpus(target_intel: dict[str, Any]) -> None:
    url = "https://en.wikipedia.org/wiki/List_of_Intel_Core_mobile_processors"
    html = fetch_html(url)
    
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    
    for h in soup.find_all(['h2', 'h3', 'h4']):
        txt = clean_text(h.get_text())
        if any(k in txt for k in ['Coffee Lake', 'Whiskey Lake', 'Amber Lake', 'Comet Lake', 'Ice Lake', 'Tiger Lake', 'Alder Lake', 'Raptor Lake', 'Meteor Lake', 'Lunar Lake', 'Arrow Lake', 'Series 1', 'Series 2']):
            node = h
            while node:
                node = node.next_element
                if not node:
                    break
                tag = getattr(node, 'name', None)
                if tag in ['h2', 'h3'] and clean_text(node.get_text()) != txt:
                    break
                if tag == 'table' and 'wikitable' in node.get('class', []):
                    rows = node.find_all('tr')
                    parse_intel_table_rows(rows, txt, target_intel)
                    break

    enrich_intel_cpus(target_intel)


def parse_intel_table_rows(rows: list[Any], heading_name: str, target_intel: dict[str, Any]) -> None:
    if len(rows) < 2:
        return
        
    for r in rows:
        cols = [clean_text(c.get_text()) for c in r.find_all(['th', 'td'])]
        if not cols:
            continue
        first_col = cols[0]
        if any(h in first_col.lower() for h in ['model', 'processor', 'cores', 'release date', 'architecture']):
            continue
            
        sku_match = re.search(r'\b((?:Core\s+)?(?:Ultra\s+\d+[A-Z0-9]*|i[3579][-\s]?\d{4,5}[A-Z0-9]*|\d{3}U|\d{3}V))\b', " ".join(cols[:2]), re.I)
        if not sku_match:
            continue
            
        raw_sku = sku_match.group(1)
        if not raw_sku.startswith("Core") and not raw_sku.startswith("Intel"):
            raw_sku = f"Core {raw_sku}"
            
        if not raw_sku.startswith("Intel"):
            full_sku = f"Intel {raw_sku}".strip()
        else:
            full_sku = raw_sku.strip()
            
        short_model = full_sku.replace("Intel ", "").strip()
        
        if "Ultra" in full_sku and any(k in full_sku for k in ['225', '255', '265', '285', '258V', '268V', '288V']):
            s_name = "Core Ultra Series 2"
        elif "Ultra" in full_sku:
            s_name = "Core Ultra Series 1"
        elif any(k in full_sku for k in ['14700', '14650', '14900', '14450']):
            s_name = "Core 14th Gen"
        elif any(k in full_sku for k in ['13700', '13600', '13500', '13420', '13900', '1335']):
            s_name = "Core 13th Gen"
        elif any(k in full_sku for k in ['12700', '12600', '12500', '12450', '12900', '1235', '1215']):
            s_name = "Core 12th Gen"
        elif any(k in full_sku for k in ['11800', '11370', '1165', '1135']):
            s_name = "Core 11th Gen"
        elif any(k in full_sku for k in ['10750', '10510', '10710', '1035']):
            s_name = "Core 10th Gen"
        elif any(k in full_sku for k in ['9750', '9300', '9880']):
            s_name = "Core 9th Gen"
        elif any(k in full_sku for k in ['8750', '8550', '8250', '8300']):
            s_name = "Core 8th Gen"
        elif any(k in full_sku for k in ['100U', '120U', '150U']):
            s_name = "Core Series 1 / Series 2"
        elif any(k in full_sku for k in ['N100', 'N200', 'N305']):
            s_name = "Processor N-series"
        else:
            s_name = "Core Mobile"

        target_intel.setdefault(s_name, {})

        col_str = " ".join(cols)
        cores_match = re.search(r'(\d+)\s*\(\s*(\d+)\s*\+\s*(\d+)\s*\)', col_str)
        if cores_match:
            total_c = int(cores_match.group(1))
            p_c = int(cores_match.group(2))
            e_c = int(cores_match.group(3))
        else:
            total_c = parse_int(re.search(r'\b([48]|10|12|14|16|24)\b', col_str)) or 8
            p_c = total_c // 2 if total_c > 4 else total_c
            e_c = total_c - p_c if total_c > 4 else 0

        igpu = "Integrated Intel UHD"
        if "Arc 140V" in col_str or "258V" in full_sku or "288V" in full_sku: igpu = "Integrated Intel Arc 140V"
        elif "Arc 130V" in col_str or "226V" in full_sku: igpu = "Integrated Intel Arc 130V"
        elif "Arc" in col_str or "Ultra" in full_sku: igpu = "Integrated Intel Arc Graphics"
        elif "Iris Xe" in col_str or "11th" in s_name or "12th" in s_name or "13th" in s_name: igpu = "Integrated Intel Iris Xe"

        rebrand_ref = None
        if "14th Gen" in s_name:
            rebrand_ref = "Raptor Lake Refresh HX of 13th Gen HX"
        elif "Series 1" in s_name and "Ultra" not in s_name:
            rebrand_ref = "Raptor Lake Refresh U"

        target_intel[s_name][short_model] = {
            "sku": full_sku,
            "model": short_model,
            "brand": "Intel",
            "series": s_name,
            "codename": heading_name.replace('[edit]', '').strip(),
            "cores": {
                "total": total_c,
                "p_cores": p_c,
                "e_cores": e_c,
                "lpe_cores": 2 if "Series 2" in s_name or "Lunar" in heading_name else 0
            },
            "threads": total_c + p_c,
            "clocks": {
                "max_turbo_ghz": parse_float(re.search(r'(\d\.\d+)\s*GHz', col_str))
            },
            "igpu": {
                "model": igpu
            },
            "npu": {
                "model": "Intel AI Boost" if "Ultra" in full_sku else None,
                "tops": 47.0 if "Series 2" in s_name else (11.5 if "Ultra" in full_sku else None)
            },
            "power": {
                "base_power_w": 28.0 if "H" in full_sku else (15.0 if "U" in full_sku else 55.0),
                "max_turbo_power_w": 115.0 if "H" in full_sku else (57.0 if "U" in full_sku else 157.0)
            },
            "rebrand_reference": rebrand_ref
        }


def enrich_amd_cpus(data: dict[str, Any]) -> None:
    defaults = {
        "Ryzen AI 300 Series": [
            ("AMD Ryzen AI 9 HX 370", "Strix Point", 12, 24, "Radeon 890M", 50.0, "28-54W"),
            ("AMD Ryzen AI 9 365", "Strix Point", 10, 20, "Radeon 880M", 50.0, "28-54W"),
            ("AMD Ryzen AI 7 PRO 360", "Strix Point", 8, 16, "Radeon 880M", 50.0, "28-54W")
        ],
        "Ryzen 8000 Series": [
            ("AMD Ryzen 7 8845HS", "Hawk Point", 8, 16, "Radeon 780M", 16.0, "35-54W"),
            ("AMD Ryzen 7 8840HS", "Hawk Point", 8, 16, "Radeon 780M", 16.0, "20-30W"),
            ("AMD Ryzen 5 8645HS", "Hawk Point", 6, 12, "Radeon 760M", 16.0, "35-54W"),
            ("AMD Ryzen 5 8640HS", "Hawk Point", 6, 12, "Radeon 760M", 16.0, "20-30W"),
            ("AMD Ryzen 7 8745HX", "Dragon Range Refresh", 8, 16, "Radeon 610M", None, "55-75W")
        ],
        "Ryzen 7000 Series": [
            ("AMD Ryzen 7 7840HS", "Phoenix", 8, 16, "Radeon 780M", 10.0, "35-54W"),
            ("AMD Ryzen 7 7840U", "Phoenix", 8, 16, "Radeon 780M", 10.0, "15-30W"),
            ("AMD Ryzen 5 7640HS", "Phoenix", 6, 12, "Radeon 760M", 10.0, "35-54W"),
            ("AMD Ryzen 7 7735HS", "Rembrandt-R", 8, 16, "Radeon 680M", None, "35-54W"),
            ("AMD Ryzen 5 7535HS", "Rembrandt-R", 6, 12, "Radeon 660M", None, "35-54W"),
            ("AMD Ryzen 7 7730U", "Barcelo-R", 8, 16, "Integrated AMD Radeon", None, "15-28W"),
            ("AMD Ryzen 5 7530U", "Barcelo-R", 6, 12, "Integrated AMD Radeon", None, "15-28W"),
            ("AMD Ryzen 3 7320U", "Mendocino", 4, 8, "Radeon 610M", None, "15W")
        ],
        "Ryzen 6000 Series": [
            ("AMD Ryzen 7 6800H", "Rembrandt", 8, 16, "Radeon 680M", None, "45W"),
            ("AMD Ryzen 7 6800U", "Rembrandt", 8, 16, "Radeon 680M", None, "15-28W"),
            ("AMD Ryzen 5 6600H", "Rembrandt", 6, 12, "Radeon 660M", None, "45W")
        ],
        "Ryzen 5000 Series": [
            ("AMD Ryzen 7 5800H", "Cezanne", 8, 16, "Radeon RX Vega 8", None, "45W"),
            ("AMD Ryzen 7 5825U", "Barcelo", 8, 16, "Radeon RX Vega 8", None, "15W"),
            ("AMD Ryzen 5 5600H", "Cezanne", 6, 12, "Radeon RX Vega 7", None, "45W"),
            ("AMD Ryzen 5 5625U", "Barcelo", 6, 12, "Radeon RX Vega 7", None, "15W"),
            ("AMD Ryzen 7 5700U", "Lucienne", 8, 16, "Radeon RX Vega 8", None, "15W")
        ]
    }

    for series, item_list in defaults.items():
        data.setdefault(series, {})
        for sku, codename, cores, threads, igpu, tops, tgp in item_list:
            short_m = sku.replace("AMD ", "").strip()
            if short_m not in data[series]:
                rebrand_ref = None
                if "7730U" in sku or "7530U" in sku: rebrand_ref = "Barcelo-R rebrand of Cezanne 5000 series"
                elif "7735HS" in sku or "7535HS" in sku: rebrand_ref = "Rembrandt-R refresh of Rembrandt 6000 series"
                elif "8845HS" in sku or "8645HS" in sku: rebrand_ref = "Hawk Point refresh of Phoenix 7040 series"

                data[series][short_m] = {
                    "sku": sku,
                    "model": short_m,
                    "brand": "AMD",
                    "series": series,
                    "codename": codename,
                    "cores": cores,
                    "threads": threads,
                    "clocks": {
                        "base_clock_ghz": 3.2 if "HS" in sku or "H" in sku else 2.0,
                        "boost_clock_ghz": 5.1 if "7" in short_m or "9" in short_m else 4.5
                    },
                    "igpu": {
                        "model": igpu
                    },
                    "npu": {
                        "model": "Ryzen AI" if tops else None,
                        "tops": tops
                    },
                    "power": {
                        "tdp_w": tgp
                    },
                    "rebrand_reference": rebrand_ref
                }


def enrich_intel_cpus(data: dict[str, Any]) -> None:
    defaults = {
        "Core Ultra Series 2": [
            ("Intel Core Ultra 9 288V", "Lunar Lake", 8, 4, 4, 8, "Integrated Intel Arc 140V", 48.0, 30.0, 37.0),
            ("Intel Core Ultra 7 258V", "Lunar Lake", 8, 4, 4, 8, "Integrated Intel Arc 140V", 47.0, 17.0, 37.0),
            ("Intel Core Ultra 7 256V", "Lunar Lake", 8, 4, 4, 8, "Integrated Intel Arc 140V", 47.0, 17.0, 37.0),
            ("Intel Core Ultra 5 226V", "Lunar Lake", 8, 4, 4, 8, "Integrated Intel Arc 130V", 40.0, 17.0, 37.0),
            ("Intel Core Ultra 9 285HX", "Arrow Lake-HX", 24, 8, 16, 24, "Integrated Intel Graphics", 13.0, 55.0, 160.0),
            ("Intel Core Ultra 7 265H", "Arrow Lake-H", 16, 6, 8, 22, "Integrated Intel Arc Graphics", 13.0, 28.0, 115.0),
            ("Intel Core Ultra 5 322", "Arrow Lake-U", 10, 2, 8, 12, "Integrated Intel Graphics (2 Xe-Cores)", 13.0, 15.0, 57.0),
            ("Intel Core Ultra 5 325", "Arrow Lake-U", 10, 2, 8, 12, "Integrated Intel Graphics (2 Xe-Cores)", 13.0, 15.0, 57.0),
            ("Intel Core Ultra 7 355", "Arrow Lake-U", 12, 2, 8, 14, "Integrated Intel Graphics (4 Xe-Cores)", 13.0, 15.0, 57.0),
            ("Intel Core Ultra 7 356H", "Arrow Lake-H", 16, 6, 8, 22, "Integrated Intel Arc Graphics (8 Xe-Cores)", 13.0, 28.0, 115.0),
            ("Intel Core Ultra X7 368H", "Arrow Lake-H", 16, 6, 8, 22, "Integrated Intel Arc Graphics (8 Xe-Cores)", 13.0, 28.0, 115.0),
            ("Intel Core Ultra X9 388H", "Arrow Lake-H", 16, 6, 8, 22, "Integrated Intel Arc Graphics (8 Xe-Cores)", 13.0, 45.0, 115.0)
        ],

        "Core Ultra Series 1": [
            ("Intel Core Ultra 9 185H", "Meteor Lake-H", 16, 6, 8, 22, "Integrated Intel Arc Graphics", 11.5, 45.0, 115.0),
            ("Intel Core Ultra 7 155H", "Meteor Lake-H", 16, 6, 8, 22, "Integrated Intel Arc Graphics", 11.5, 28.0, 115.0),
            ("Intel Core Ultra 5 125H", "Meteor Lake-H", 14, 4, 8, 18, "Integrated Intel Arc Graphics", 11.5, 28.0, 115.0),
            ("Intel Core Ultra 7 155U", "Meteor Lake-U", 12, 2, 8, 14, "Integrated Intel Graphics", 11.5, 15.0, 57.0),
            ("Intel Core Ultra 5 125U", "Meteor Lake-U", 12, 2, 8, 14, "Integrated Intel Graphics", 11.5, 15.0, 57.0)
        ],
        "Core 14th Gen": [
            ("Intel Core i9-14900HX", "Raptor Lake Refresh HX", 24, 8, 16, 32, "Integrated Intel UHD", None, 55.0, 157.0),
            ("Intel Core i7-14700HX", "Raptor Lake Refresh HX", 20, 8, 12, 28, "Integrated Intel UHD", None, 55.0, 157.0),
            ("Intel Core i7-14650HX", "Raptor Lake Refresh HX", 16, 8, 8, 24, "Integrated Intel UHD", None, 55.0, 157.0),
            ("Intel Core i5-14500HX", "Raptor Lake Refresh HX", 14, 6, 8, 20, "Integrated Intel UHD", None, 55.0, 157.0)
        ],
        "Core 13th Gen": [
            ("Intel Core i9-13900HX", "Raptor Lake-HX", 24, 8, 16, 32, "Integrated Intel UHD", None, 55.0, 157.0),
            ("Intel Core i7-13700HX", "Raptor Lake-HX", 16, 8, 8, 24, "Integrated Intel UHD", None, 55.0, 157.0),
            ("Intel Core i7-13620H", "Raptor Lake-H", 10, 6, 4, 16, "Integrated Intel UHD", None, 45.0, 115.0),
            ("Intel Core i5-13420H", "Raptor Lake-H", 8, 4, 4, 12, "Integrated Intel UHD", None, 45.0, 95.0),
            ("Intel Core i7-1355U", "Raptor Lake-U", 10, 2, 8, 12, "Integrated Intel Iris Xe", None, 15.0, 55.0),
            ("Intel Core i5-1335U", "Raptor Lake-U", 10, 2, 8, 12, "Integrated Intel Iris Xe", None, 15.0, 55.0)
        ],
        "Core 12th Gen": [
            ("Intel Core i9-12900H", "Alder Lake-H", 14, 6, 8, 20, "Integrated Intel Iris Xe", None, 45.0, 115.0),
            ("Intel Core i7-12700H", "Alder Lake-H", 14, 6, 8, 20, "Integrated Intel Iris Xe", None, 45.0, 115.0),
            ("Intel Core i5-12450H", "Alder Lake-H", 8, 4, 4, 12, "Integrated Intel UHD", None, 45.0, 95.0),
            ("Intel Core i7-1255U", "Alder Lake-U", 10, 2, 8, 12, "Integrated Intel Iris Xe", None, 15.0, 55.0),
            ("Intel Core i5-1235U", "Alder Lake-U", 10, 2, 8, 12, "Integrated Intel Iris Xe", None, 15.0, 55.0)
        ],
        "Core 11th Gen": [
            ("Intel Core i7-11800H", "Tiger Lake-H", 8, 8, 0, 16, "Integrated Intel UHD", None, 45.0, 109.0),
            ("Intel Core i5-11400H", "Tiger Lake-H", 6, 6, 0, 12, "Integrated Intel UHD", None, 45.0, 95.0),
            ("Intel Core i7-1165G7", "Tiger Lake-UP3", 4, 4, 0, 8, "Integrated Intel Iris Xe", None, 15.0, 28.0),
            ("Intel Core i5-1135G7", "Tiger Lake-UP3", 4, 4, 0, 8, "Integrated Intel Iris Xe", None, 15.0, 28.0)
        ],
        "Core 10th Gen": [
            ("Intel Core i7-10750H", "Comet Lake-H", 6, 6, 0, 12, "Integrated Intel UHD 630", None, 45.0, 90.0),
            ("Intel Core i5-10300H", "Comet Lake-H", 4, 4, 0, 8, "Integrated Intel UHD 630", None, 45.0, 75.0),
            ("Intel Core i7-1065G7", "Ice Lake-U", 4, 4, 0, 8, "Integrated Intel Iris Plus", None, 15.0, 25.0),
            ("Intel Core i5-10210U", "Comet Lake-U", 4, 4, 0, 8, "Integrated Intel UHD 620", None, 15.0, 25.0)
        ],
        "Core 9th Gen": [
            ("Intel Core i7-9750H", "Coffee Lake Refresh H", 6, 6, 0, 12, "Integrated Intel UHD 630", None, 45.0, 75.0),
            ("Intel Core i5-9300H", "Coffee Lake Refresh H", 4, 4, 0, 8, "Integrated Intel UHD 630", None, 45.0, 65.0)
        ],
        "Core 8th Gen": [
            ("Intel Core i7-8750H", "Coffee Lake-H", 6, 6, 0, 12, "Integrated Intel UHD 630", None, 45.0, 78.0),
            ("Intel Core i7-8550U", "Kaby Lake R", 4, 4, 0, 8, "Integrated Intel UHD 620", None, 15.0, 25.0),
            ("Intel Core i5-8250U", "Kaby Lake R", 4, 4, 0, 8, "Integrated Intel UHD 620", None, 15.0, 25.0)
        ],
        "Core Series 1 / Series 2": [
            ("Intel Core 7 150U", "Raptor Lake Refresh U", 10, 2, 8, 12, "Integrated Intel Graphics", None, 15.0, 55.0),
            ("Intel Core 5 120U", "Raptor Lake Refresh U", 10, 2, 8, 12, "Integrated Intel Graphics", None, 15.0, 55.0),
            ("Intel Core 3 100U", "Raptor Lake Refresh U", 6, 2, 4, 8, "Integrated Intel Graphics", None, 15.0, 55.0)
        ]
    }

    for series, item_list in defaults.items():
        data.setdefault(series, {})
        for sku, codename, total_c, p_c, e_c, threads, igpu, tops, base_p, max_p in item_list:
            short_m = sku.replace("Intel ", "").strip()
            if short_m not in data[series]:
                rebrand_ref = None
                if "14th Gen" in series: rebrand_ref = "Raptor Lake Refresh HX of 13th Gen HX"
                elif "Series 1" in series and "Core Ultra" not in series: rebrand_ref = "Raptor Lake Refresh U"

                data[series][short_m] = {
                    "sku": sku,
                    "model": short_m,
                    "brand": "Intel",
                    "series": series,
                    "codename": codename,
                    "cores": {
                        "total": total_c,
                        "p_cores": p_c,
                        "e_cores": e_c,
                        "lpe_cores": 2 if "Lunar" in codename else 0
                    },
                    "threads": threads,
                    "clocks": {
                        "max_turbo_ghz": 5.4 if "i9" in sku or "288V" in sku else (4.8 if "i7" in sku else 4.4)
                    },
                    "igpu": {
                        "model": igpu
                    },
                    "npu": {
                        "model": "Intel AI Boost" if tops else None,
                        "tops": tops
                    },
                    "power": {
                        "base_power_w": base_p,
                        "max_turbo_power_w": max_p
                    },
                    "rebrand_reference": rebrand_ref
                }


def scrape_wikipedia_cpu_igpu_specs() -> dict[str, dict[str, Any]]:

    """
    Dynamically scrape mobile processor iGPU specs directly from Wikipedia tables.
    """
    urls = [
        "https://en.wikipedia.org/wiki/List_of_Intel_Core_mobile_processors",
        "https://en.wikipedia.org/wiki/List_of_Intel_processors",
        "https://en.wikipedia.org/wiki/List_of_AMD_Ryzen_processors",
        "https://en.wikipedia.org/wiki/Lunar_Lake",
        "https://en.wikipedia.org/wiki/Meteor_Lake",
        "https://en.wikipedia.org/wiki/Arrow_Lake"
    ]
    
    from bs4 import BeautifulSoup
    scraped_map: dict[str, dict[str, Any]] = {}

    for u in urls:
        try:
            html = fetch_html(u)
            soup = BeautifulSoup(html, "html.parser")
            for table in soup.find_all("table", class_="wikitable"):
                for r in table.find_all("tr"):
                    cols = [clean_text(td.get_text()) for td in r.find_all(["th", "td"])]
                    row_str = " ".join(cols)
                    models = re.findall(r"\b((?:Ultra\s+[3579]\s+\d{3}[HVU]?|i[3579]-\d{4,5}[HVUQ]?\d?|Ryzen\s+[3579]\s+(?:PRO\s+)?\d{4}[HSU]?|Core\s+[357]\s+\d{3}U|\d{5}[HUX]{1,2}))\b", row_str, re.I)
                    for m in models:
                        gpu_match = re.search(r"(Arc\s*1?\d{2}[VT]?|Arc\s*Graphics|Iris\s*Xe|Iris\s*Plus|UHD\s*Graphics|Radeon\s*\d{3}M|Radeon\s*RX\s*Vega|Radeon\s*Graphics)", row_str, re.I)
                        xe_match = re.search(r"(\d+)\s*(?:Xe-?cores?|Xe)", row_str, re.I)
                        cu_match = re.search(r"(\d+)\s*(?:CU|CUs|Compute Units)", row_str, re.I)
                        clock_match = re.search(r"(\d+(?:\.\d+)?)\s*GHz", row_str, re.I)

                        raw_igpu = gpu_match.group(0) if gpu_match else "Integrated Graphics"
                        xe_cores = int(xe_match.group(1)) if xe_match else None
                        cus = int(cu_match.group(1)) if cu_match else None
                        boost = f"{clock_match.group(1)} GHz" if clock_match else ""

                        # Determine clean iGPU model name
                        igpu_name = raw_igpu
                        igpu_series = "Integrated Graphics"
                        if "Arc" in raw_igpu or "Ultra" in m:
                            if xe_cores:
                                igpu_name = f"Intel Arc Graphics ({xe_cores} Xe-Cores)" if "Arc" in raw_igpu else f"Intel Graphics ({xe_cores} Xe-Cores)"
                            igpu_series = "Intel Arc Graphics Series"
                        elif "Radeon" in raw_igpu:
                            igpu_series = "Radeon 800M Series" if "8" in raw_igpu else ("Radeon 700M Series" if "7" in raw_igpu else "Radeon 600M Series")

                        m_key = clean_text(m).lower()
                        if m_key not in scraped_map or (xe_cores or cus):
                            scraped_map[m_key] = {
                                "igpu": igpu_name,
                                "igpu_series": igpu_series,
                                "igpu_boost_clock": boost,
                                "xe_cores": xe_cores,
                                "cus": cus
                            }
        except Exception as e:
            print(f"    Notice: Wikipedia CPU iGPU table scrape fallback ({e})")

    return scraped_map


def cleanup_cpu_inventory(cpu_data: dict[str, Any]) -> None:
    scraped_specs = scrape_wikipedia_cpu_igpu_specs()

    for brand in list(cpu_data.keys()):
        cleaned_series: dict[str, dict[str, Any]] = {}
        for series_name, models in cpu_data[brand].items():
            norm_name = normalize_series_name(series_name)
            cleaned_series.setdefault(norm_name, {})
            for m_key, m_val in models.items():
                if m_key.lower() in ['model', 'branding and model', 'cpu', 'cores', 'release date']:
                    continue
                full_cpu_name = m_val.get("sku") or m_val.get("model") or m_key
                m_key_clean = clean_text(full_cpu_name).lower()
                
                # Fetch dynamically scraped specs from Wikipedia or fallback
                spec = scraped_specs.get(m_key_clean) or scraped_specs.get(clean_text(m_key).lower()) or {}
                
                # Ensure accurate defaults for SKU families
                t = full_cpu_name.lower()
                igpu_name = spec.get("igpu") or "Integrated Graphics"
                igpu_series = spec.get("igpu_series") or "Integrated Graphics"
                xe_cores = spec.get("xe_cores")
                cus = spec.get("cus")
                boost_clock = spec.get("igpu_boost_clock") or ""

                if "322" in t or "325" in t:
                    igpu_name, igpu_series, xe_cores, boost_clock = "Intel Arc Graphics (2 Xe-Cores)", "Intel Arc Graphics Series", 2, "1.80 GHz"
                elif "225u" in t or "235u" in t or "255u" in t or "265u" in t or "355" in t:
                    igpu_name, igpu_series, xe_cores, boost_clock = "Intel Arc Graphics (4 Xe-Cores)", "Intel Arc Graphics Series", 4, "2.00 GHz"

                elif "185h" in t or "155h" in t or "285h" in t or "265h" in t or "255h" in t or "388h" in t or "368h" in t or "356h" in t or "386h" in t or "366h" in t:
                    igpu_name, igpu_series, xe_cores, boost_clock = "Intel Arc Graphics (8 Xe-Cores)", "Intel Arc Graphics Series", 8, "2.25 GHz"
                elif "125h" in t or "225h" in t or "235h" in t or "336h" in t:
                    igpu_name, igpu_series, xe_cores, boost_clock = "Intel Arc Graphics (7 Xe-Cores)", "Intel Arc Graphics Series", 7, "2.20 GHz"
                elif "258v" in t or "268v" in t or "288v" in t or "256v" in t or "266v" in t:
                    igpu_name, igpu_series, xe_cores, boost_clock = "Intel Arc 140V (8 Xe-Cores)", "Intel Arc V-Series", 8, "2.05 GHz"
                elif "226v" in t or "228v" in t or "236v" in t or "238v" in t:
                    igpu_name, igpu_series, xe_cores, boost_clock = "Intel Arc 130V (7 Xe-Cores)", "Intel Arc V-Series", 7, "1.85 GHz"
                elif "890m" in t or "ai 9 hx" in t or "388h" in t or "370" in t:
                    igpu_name, igpu_series, cus, boost_clock = "Radeon 890M", "Radeon 800M Series", 16, "2.90 GHz"
                elif "880m" in t or "ai 9" in t or "365" in t:
                    igpu_name, igpu_series, cus, boost_clock = "Radeon 880M", "Radeon 800M Series", 12, "2.90 GHz"
                elif "780m" in t or "8845hs" in t or "8840hs" in t or "8745hx" in t or "7840hs" in t or "7840u" in t or "260" in t or "250" in t:
                    igpu_name, igpu_series, cus, boost_clock = "Radeon 780M", "Radeon 700M Series", 12, "2.70 GHz"
                elif "760m" in t or "8645hs" in t or "8640u" in t or "7640hs" in t or "7640u" in t or "220" in t or "215" in t:
                    igpu_name, igpu_series, cus, boost_clock = "Radeon 760M", "Radeon 700M Series", 8, "2.60 GHz"
                elif "740m" in t or "8540u" in t or "7540u" in t or "pro 210" in t:
                    igpu_name, igpu_series, cus, boost_clock = "Radeon 740M", "Radeon 700M Series", 4, "2.50 GHz"

                m_val["igpu_specs"] = {
                    "igpu": igpu_name,
                    "igpu_series": igpu_series,
                    "igpu_boost_clock": boost_clock,
                    "xe_cores": xe_cores,
                    "cus": cus
                }
                cleaned_series[norm_name][m_key] = m_val
        cpu_data[brand] = {k: v for k, v in cleaned_series.items() if v}



def main() -> None:

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # 1. Build iGPU Inventory
    igpu_inventory = build_igpu_inventory()
    igpu_path = DATA_DIR / "igpu_inventory.json"
    with open(igpu_path, "w", encoding="utf-8") as f:
        json.dump(igpu_inventory, f, indent=2, ensure_ascii=False)
    print(f"Saved iGPU Inventory to {igpu_path}")

    # 2. Build dGPU Inventory
    gpu_inventory = build_gpu_inventory()
    gpu_path = DATA_DIR / "gpu_inventory.json"
    with open(gpu_path, "w", encoding="utf-8") as f:
        json.dump(gpu_inventory, f, indent=2, ensure_ascii=False)
    print(f"Saved dGPU Inventory to {gpu_path}")
    
    # 3. Build CPU Inventory
    cpu_inventory = build_cpu_inventory()
    cpu_path = DATA_DIR / "cpu_inventory.json"
    with open(cpu_path, "w", encoding="utf-8") as f:
        json.dump(cpu_inventory, f, indent=2, ensure_ascii=False)
    print(f"Saved CPU Inventory to {cpu_path}")

    print("\nInventory build complete!")


if __name__ == "__main__":
    main()
