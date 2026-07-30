#!/usr/bin/env python3
"""
Hardware Spec Normalization Engine for LaptopDeals.

Normalizes Intel and AMD CPUs, integrated GPUs (iGPUs), and discrete GPUs (dGPUs)
against authoritative hardware inventories, preserving existing specs and manufacturer TGP.

Provides CLI support for incremental, full (--all), or targeted (--ids) processing.
Generates an internal debug report for unmatched laptops.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from .paths import REPO_ROOT, APP_DATA, CPU_INVENTORY, GPU_INVENTORY, IGPU_INVENTORY

DATA_DIR = REPO_ROOT / "data"
CATALOG_PATH = APP_DATA
REPORT_DIR = DATA_DIR / "reports"
UNMATCHED_REPORT_PATH = REPORT_DIR / "unmatched_hardware_report.json"


def load_inventories() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    cpu_inv, gpu_inv, igpu_inv = {}, {}, {}
    if CPU_INVENTORY.exists():
        with open(CPU_INVENTORY, "r", encoding="utf-8") as f:
            cpu_inv = json.load(f).get("processors", {})
    if GPU_INVENTORY.exists():
        with open(GPU_INVENTORY, "r", encoding="utf-8") as f:
            gpu_inv = json.load(f).get("gpus", {})
    if IGPU_INVENTORY.exists():
        with open(IGPU_INVENTORY, "r", encoding="utf-8") as f:
            igpu_inv = json.load(f).get("igpus", {})
    return cpu_inv, gpu_inv, igpu_inv


def clean_text(val: Any) -> str:
    if not val:
        return ""
    text = str(val).replace("[™®]", "").replace("™", "").replace("®", "")
    return re.sub(r"\s+", " ", text).strip()


def parse_cpu_dict_string(val: Any) -> Any:
    if isinstance(val, dict):
        return val
    val_str = str(val or "").strip()
    if val_str.startswith("{") and "model" in val_str:
        try:
            fixed = val_str.replace("'", '"').replace("None", "null")
            return json.loads(fixed)
        except Exception:
            pass
    return val_str


def is_valid_cpu(raw_text: Any) -> bool:
    text = str(raw_text or "").strip()
    if not text:
        return False
    if any(k in text.lower() for k in ["wi-fi", "bluetooth", "modem", "cat 6", "cat 16", "5g", "4g", "audio", "adapter", "dongle", "battery"]):
        return False
    return any(k in text.lower() for k in ["intel", "amd", "ryzen", "core", "snapdragon", "qualcomm", "processor"])


def normalize_cpu_name(raw_name: Any, brand: str = "") -> tuple[str, str]:
    """
    Normalize Intel, AMD, and Snapdragon CPU model names consistently.
    Returns (full_normalized_name, cpu_series)
    """
    parsed = parse_cpu_dict_string(raw_name)
    if isinstance(parsed, dict):
        text = str(parsed.get("full_model") or parsed.get("model") or "").strip()
        brand = parsed.get("brand") or brand
    else:
        text = str(parsed or "").strip()

    text = clean_text(text)
    text = re.sub(r"\(.*?\)", "", text).strip()
    text = re.sub(r"\b(Processor|vPro)\b", "", text, flags=re.I).strip()
    text = re.sub(r"^\d+(?:st|nd|rd|th)\s+(?:Generation|Gen)\s+(?:Intel\s+)?", "", text, flags=re.I).strip()
    text = re.sub(r"\s+", " ", text).strip()

    # Known catalog anomalies
    if "ryzen 3th gen" in text.lower():
        return "AMD Ryzen 3000 Series", "Ryzen 3000 Series"
    if "ryzen 14th gen" in text.lower():
        return "Intel Core 14th Gen", "Core 14th Gen"

    if brand.lower() == "snapdragon" or "snapdragon" in text.lower() or "qualcomm" in text.lower():
        text = re.sub(r"^(?:Qualcomm\s+)?", "Qualcomm ", text, flags=re.I).strip()
        return text, "Snapdragon Series"

    if brand == "Intel" or "intel" in text.lower() or "core" in text.lower() or re.search(r"\bi[3579]-", text, re.I):
        text = re.sub(r"^(?:Intel\s+)?", "", text, flags=re.I).strip()
        
        # 1. Core Ultra Series
        ultra_match = re.search(r"\b(Core\s+)?Ultra\s+([X\d])\s+([A-Z]?\d{3,4}[A-Z]{0,3})\b", text, re.I)
        if ultra_match:
            u_num = ultra_match.group(2)
            u_sku = ultra_match.group(3).upper()
            full_m = f"Intel Core Ultra {u_num} {u_sku}"
            num_val = int(re.search(r"\d+", u_sku).group(0))
            if num_val >= 300:
                series = "Core Ultra Series 3"
            elif u_sku.endswith("V") or num_val >= 200:
                series = "Core Ultra Series 2"
            else:
                series = "Core Ultra Series 1"
            return full_m, series
            
        # 2. Core i3/i5/i7/i9 (e.g. Core i9-14900HX, Core i7-13700HX, Core i5-12450H, Core i7-8550U)
        core_i_match = re.search(r"\b(Core\s+)?(i[3579])[-_\s]?(\d{4,5}[A-Z]{0,3})\b", text, re.I)
        if core_i_match:
            i_sku = core_i_match.group(3).upper()
            full_m = f"Intel Core {core_i_match.group(2).lower()}-{i_sku}"
            gen_num = int(i_sku[:2]) if len(i_sku) >= 5 else int(i_sku[0])
            series = f"Core {gen_num}th Gen" if gen_num in [8, 9, 10, 11, 12, 13, 14] else "Core Mobile"
            return full_m, series
            
        # 3. Core Series 1 / Series 2 (e.g. Core 7 150U, Core 5 120U, Core 7 240H, Core 5 210H, Core 5 220U, Core 3 100U)
        core_series_match = re.search(r"\b(Core\s+)?([3579])\s+(\d{3}[A-Z]{1,2})\b", text, re.I)
        if core_series_match:
            full_m = f"Intel Core {core_series_match.group(2)} {core_series_match.group(3).upper()}"
            return full_m, "Core Series 1 / Series 2"

        # 4. Processor N-series (e.g. Processor N100, N200, N305)
        n_match = re.search(r"\b(Processor\s+)?(N\d{3})\b", text, re.I)
        if n_match:
            return f"Intel Processor {n_match.group(2).upper()}", "Processor N-series"

        full_name = f"Intel {text}" if not text.startswith("Intel") else text
        gen_match = re.search(r"\b(\d{1,2})th\s+gen\b", text, re.I)
        series = f"Core {gen_match.group(1)}th Gen" if gen_match else "Core Series 1 / Series 2"
        return full_name, series

    elif brand == "AMD" or "amd" in text.lower() or "ryzen" in text.lower():
        text = re.sub(r"^(?:AMD\s+)?", "", text, flags=re.I).strip()
        
        # 1. Ryzen AI Series (e.g. Ryzen AI 9 HX 370, Ryzen AI 7 450, Ryzen AI 5 435)
        ai_match = re.search(r"\bRyzen\s+AI\s+([3579])(?:\s+PRO)?(?:\s+(HX))?(?:\s+PRO)?\s+(\d{3})\b", text, re.I)
        if ai_match:
            pro_str = " PRO" if "pro" in text.lower() else ""
            hx_str = f" {ai_match.group(2).upper()}" if ai_match.group(2) else ""
            digit = ai_match.group(3)[0]
            full_m = f"AMD Ryzen AI {ai_match.group(1)}{pro_str}{hx_str} {ai_match.group(3)}"
            series = f"Ryzen AI {digit}00 Series"
            return full_m, series
            
        # 2. Standard Ryzen Series & Ryzen 200/100 Series
        ryzen_match = re.search(r"\bRyzen\s+([3579])(?:\s+PRO)?\s+([A-Z]*\d{2,4}[A-Z]{0,3})\b", text, re.I)
        if ryzen_match:
            pro_str = " PRO" if "pro" in text.lower() else ""
            sku_code = ryzen_match.group(2).upper()
            full_m = f"AMD Ryzen {ryzen_match.group(1)}{pro_str} {sku_code}"
            
            if re.search(r"\b2\d{2}\b", sku_code):
                return full_m, "Ryzen 200 Series"
            elif re.search(r"\b1\d{2}\b", sku_code):
                return full_m, "Ryzen 100 Series"


            num_part = re.search(r"\b([2-9])\d{3}[A-Z]*\b", sku_code)
            if num_part:
                series = f"Ryzen {num_part.group(1)}000 Series"
            else:
                num_part2 = re.search(r"\d{3,4}", sku_code)
                if num_part2 and len(num_part2.group(0)) == 4:
                    series = f"Ryzen {num_part2.group(0)[0]}000 Series"
                else:
                    series = "Ryzen Mobile"
            return full_m, series

        full_name = f"AMD {text}" if not text.startswith("AMD") else text
        return full_name, "Ryzen Mobile"

    return text, "Other Mobile"


def is_integrated_graphics(text: str) -> bool:

    t = text.lower()
    if "integrated" in t or "shared" in t or "uhd graphics" in t or "iris xe" in t or "radeon graphics" in t:
        return True
    if any(k in t for k in ["rtx", "gtx", "geforce", "blackwell", "ada", "quadro", "discrete"]):
        return False
    return True


_CPU_CACHE: dict[str, dict[str, Any]] = {}

def get_cpu_from_inventory(cpu_model: str) -> dict[str, Any] | None:
    global _CPU_CACHE
    if not _CPU_CACHE:
        cpu_inv, _, _ = load_inventories()
        for brand, series_map in cpu_inv.items():
            if isinstance(series_map, dict):
                for s_name, m_map in series_map.items():
                    if isinstance(m_map, dict):
                        for m_key, m_val in m_map.items():
                            if isinstance(m_val, dict) and "igpu_specs" in m_val:
                                sku = clean_text(m_val.get("sku") or "").lower()
                                model = clean_text(m_val.get("model") or m_key).lower()
                                if sku: _CPU_CACHE[sku] = m_val["igpu_specs"]
                                if model: _CPU_CACHE[model] = m_val["igpu_specs"]

    clean_target = clean_text(cpu_model).lower()
    if clean_target in _CPU_CACHE:
        return _CPU_CACHE[clean_target]
    for k, v in _CPU_CACHE.items():
        if k and len(k) > 4 and (k in clean_target or clean_target in k):
            return v
    return None


def enrich_cpu_details(cpu_model: str, raw_gpu: str = "") -> dict[str, Any]:
    """
    Enrich CPU model with iGPU name, iGPU series, clocks, cores, threads, and Xe-Cores/CUs.
    """
    inv_specs = get_cpu_from_inventory(cpu_model)
    if inv_specs and inv_specs.get("igpu") and inv_specs["igpu"] != "Integrated Graphics":
        return inv_specs

    text = f"{cpu_model} {raw_gpu}".lower()

    igpu, igpu_series = "Integrated Graphics", "Integrated Graphics"
    xe_cores, cus = None, None
    base_clock, boost_clock = None, None
    igpu_boost_clock = None

    if "amd" in text or "ryzen" in text:
        if "890m" in text or "ai 9 hx" in text or "388h" in text or "370" in text:
            igpu, igpu_series = "Radeon 890M", "Radeon 800M Series"
            igpu_boost_clock = "2.90 GHz"
            cus = 16
        elif "880m" in text or "ai 9" in text or "365" in text:
            igpu, igpu_series = "Radeon 880M", "Radeon 800M Series"
            igpu_boost_clock = "2.90 GHz"
            cus = 12
        elif "860m" in text or "ai 7" in text or "350" in text or "450" in text or "445" in text:
            igpu, igpu_series = "Radeon 860M", "Radeon 800M Series"
            igpu_boost_clock = "2.80 GHz"
            cus = 8
        elif "840m" in text or "ai 5" in text or "340" in text or "330" in text or "430" in text or "435" in text:
            igpu, igpu_series = "Radeon 840M", "Radeon 800M Series"
            igpu_boost_clock = "2.70 GHz"
            cus = 6
        elif "780m" in text or "8845hs" in text or "8840hs" in text or "8745hx" in text or "7840hs" in text or "7840u" in text or "260" in text or "250" in text:
            igpu, igpu_series = "Radeon 780M", "Radeon 700M Series"
            igpu_boost_clock = "2.70 GHz"
            cus = 12
        elif "760m" in text or "8645hs" in text or "8640u" in text or "7640hs" in text or "7640u" in text or "220" in text or "215" in text:
            igpu, igpu_series = "Radeon 760M", "Radeon 700M Series"
            igpu_boost_clock = "2.60 GHz"
            cus = 8
        elif "740m" in text or "8540u" in text or "7540u" in text or "pro 210" in text or "40" in text:
            igpu, igpu_series = "Radeon 740M", "Radeon 700M Series"
            igpu_boost_clock = "2.50 GHz"
            cus = 4
        elif "680m" in text or "6800h" in text or "6800u" in text:
            igpu, igpu_series = "Radeon 680M", "Radeon 600M Series"
            igpu_boost_clock = "2.20 GHz"
            cus = 12
        elif "660m" in text or "6600h" in text or "6600u" in text:
            igpu, igpu_series = "Radeon 660M", "Radeon 600M Series"
            igpu_boost_clock = "1.90 GHz"
            cus = 6
        elif "610m" in text or "7320u" in text or "7520u" in text or "170" in text or "150" in text or "130" in text:
            igpu, igpu_series = "Radeon 610M", "Radeon 700M Series"
            igpu_boost_clock = "1.90 GHz"
            cus = 2
        elif "vega" in text or "5700u" in text or "5825u" in text or "5625u" in text or "7530u" in text or "7730u" in text:
            igpu, igpu_series = "Radeon RX Vega", "Radeon RX Vega Series"
            igpu_boost_clock = "1.80 GHz"
        else:
            igpu, igpu_series = "Radeon Graphics", "Radeon Graphics Series"

    elif "intel" in text or "core" in text:
        if "258v" in text or "268v" in text or "288v" in text or "256v" in text or "266v" in text:
            xe_cores = 8
            igpu, igpu_series = "Intel Arc 140V (8 Xe-Cores)", "Intel Arc V-Series"
            igpu_boost_clock = "2.05 GHz"
        elif "226v" in text or "228v" in text or "236v" in text or "238v" in text:
            xe_cores = 7
            igpu, igpu_series = "Intel Arc 130V (7 Xe-Cores)", "Intel Arc V-Series"
            igpu_boost_clock = "1.85 GHz"
        elif "322" in text or "325" in text:
            xe_cores = 2
            igpu, igpu_series = "Intel Arc Graphics (2 Xe-Cores)", "Intel Arc Graphics Series"
            igpu_boost_clock = "1.80 GHz"
        elif "225u" in text or "235u" in text or "255u" in text or "265u" in text or "355" in text or "hx" in text:
            xe_cores = 4
            igpu, igpu_series = "Intel Arc Graphics (4 Xe-Cores)", "Intel Arc Graphics Series"
            igpu_boost_clock = "2.00 GHz"
        elif "185h" in text or "155h" in text or "285h" in text or "265h" in text or "255h" in text or "388h" in text or "368h" in text or "356h" in text or "386h" in text or "366h" in text:
            xe_cores = 8
            igpu, igpu_series = "Intel Arc Graphics (8 Xe-Cores)", "Intel Arc Graphics Series"
            igpu_boost_clock = "2.25 GHz"
        elif "125h" in text or "225h" in text or "235h" in text or "336h" in text:
            xe_cores = 7
            igpu, igpu_series = "Intel Arc Graphics (7 Xe-Cores)", "Intel Arc Graphics Series"
            igpu_boost_clock = "2.20 GHz"
        elif "ultra" in text or "arc" in text:
            if "u" in text:
                xe_cores = 4
                igpu, igpu_series = "Intel Arc Graphics (4 Xe-Cores)", "Intel Arc Graphics Series"
                igpu_boost_clock = "2.00 GHz"

            else:
                xe_cores = 8
                igpu, igpu_series = "Intel Arc Graphics (8 Xe-Cores)", "Intel Arc Graphics Series"
                igpu_boost_clock = "2.25 GHz"
        elif "iris" in text or "13700" in text or "13650" in text or "13620" in text or "13500" in text or "1335u" in text or "12650" in text or "12450" in text or "1215u" in text or "150u" in text or "120u" in text:
            igpu, igpu_series = "Intel Iris Xe", "Intel Iris Xe Series"
            igpu_boost_clock = "1.50 GHz"
        elif "n100" in text or "n200" in text or "n305" in text or "uhd" in text or "100u" in text or "210h" in text or "240h" in text:
            igpu, igpu_series = "Intel UHD Graphics", "Intel UHD Graphics Series"
            igpu_boost_clock = "1.25 GHz"
        else:
            igpu, igpu_series = "Intel Graphics", "Intel UHD Graphics Series"

    elif "snapdragon" in text or "qualcomm" in text or "x1e" in text or "x1p" in text or "x2p" in text:
        igpu, igpu_series = "Qualcomm Adreno GPU", "Qualcomm Adreno Series"
        igpu_boost_clock = "1.25 GHz"

    cores, threads = None, None
    if "8540u" in text: cores, threads, base_clock, boost_clock = 6, 12, "3.2 GHz", "4.9 GHz"
    elif "8845hs" in text or "8840hs" in text: cores, threads, base_clock, boost_clock = 8, 16, "3.8 GHz", "5.1 GHz"
    elif "7840hs" in text or "8745hx" in text: cores, threads, base_clock, boost_clock = 8, 16, "3.8 GHz", "5.1 GHz"
    elif "260" in text or "250" in text or "170" in text: cores, threads, base_clock, boost_clock = 8, 16, "3.8 GHz", "5.1 GHz"
    elif "7535hs" in text or "7530u" in text or "5625u" in text or "220" in text or "215" in text: cores, threads, base_clock, boost_clock = 6, 12, "3.3 GHz", "4.55 GHz"
    elif "7320u" in text or "7520u" in text or "pro 210" in text or "150" in text: cores, threads, base_clock, boost_clock = 4, 8, "2.8 GHz", "4.3 GHz"
    elif "258v" in text or "268v" in text or "288v" in text or "256v" in text or "226v" in text or "228v" in text or "236v" in text or "238v" in text: cores, threads, base_clock, boost_clock = 8, 8, "3.7 GHz", "4.8 GHz"
    elif "322" in text or "325" in text: cores, threads, base_clock, boost_clock = 10, 12, "3.4 GHz", "4.4 GHz"
    elif "155h" in text or "255h" in text or "265h" in text or "285h" in text or "386h" in text or "368h" in text or "388h" in text or "355" in text or "356h" in text or "366h" in text: cores, threads, base_clock, boost_clock = 16, 22, "3.8 GHz", "4.8 GHz"
    elif "125h" in text or "225h" in text or "235h" in text or "336h" in text: cores, threads, base_clock, boost_clock = 14, 18, "3.6 GHz", "4.5 GHz"

    elif "14700hx" in text: cores, threads, base_clock, boost_clock = 20, 28, "3.9 GHz", "5.5 GHz"
    elif "13700hx" in text or "14650hx" in text or "13650hx" in text: cores, threads, base_clock, boost_clock = 16, 24, "3.6 GHz", "5.2 GHz"
    elif "13620h" in text or "12650hx" in text or "240h" in text: cores, threads, base_clock, boost_clock = 10, 16, "3.6 GHz", "4.9 GHz"
    elif "13450hx" in text or "12450h" in text or "12450hx" in text or "13420h" in text or "210h" in text: cores, threads, base_clock, boost_clock = 8, 12, "3.4 GHz", "4.6 GHz"
    elif "150u" in text or "1335u" in text or "255u" in text or "265u" in text: cores, threads, base_clock, boost_clock = 10, 12, "3.7 GHz", "5.0 GHz"
    elif "100u" in text or "1215u" in text or "1315u" in text or "225u" in text or "235u" in text: cores, threads, base_clock, boost_clock = 6, 8, "3.3 GHz", "4.7 GHz"
    elif "x1e" in text or "x2e" in text: cores, threads, base_clock, boost_clock = 12, 12, "3.4 GHz", "4.2 GHz"
    elif "x1p" in text or "x2p" in text: cores, threads, base_clock, boost_clock = 10, 10, "3.4 GHz", "3.4 GHz"
    elif "x1" in text: cores, threads, base_clock, boost_clock = 8, 8, "3.0 GHz", "3.0 GHz"

    return {
        "igpu": igpu,
        "igpu_series": igpu_series,
        "igpu_boost_clock": igpu_boost_clock,
        "cores": cores,
        "threads": threads,
        "xe_cores": xe_cores,
        "cus": cus,
        "base_clock": base_clock,
        "boost_clock": boost_clock
    }


def detect_igpu_model(cpu_model: str, raw_igpu: str = "") -> tuple[str, str]:
    info = enrich_cpu_details(cpu_model, raw_igpu)
    return info["igpu"], info["igpu_series"]


def normalize_dgpu_model(raw_model: str, raw_vram: str = "") -> tuple[str, str, str]:
    text = clean_text(f"{raw_model} {raw_vram}")
    
    vram_match = re.search(r"\b(\d{1,2})\s*GB\s*(GDDR\d[X]?|DDR\d)?\b", text, re.I)
    vram_str = ""
    if vram_match:
        gb = vram_match.group(1)
        mtype = vram_match.group(2) or "GDDR6"
        vram_str = f"{gb} GB {mtype.upper()}"

    clean_m = text
    clean_m = re.sub(r"\bNVIDIA[®™]?\s*", "", clean_m, flags=re.I)
    clean_m = re.sub(r"\bGeForce[®™]?\s*", "", clean_m, flags=re.I)
    clean_m = re.sub(r"\bPRO\b", "", clean_m, flags=re.I)
    clean_m = re.sub(r"\b(Laptop GPU|GPU)\b", "", clean_m, flags=re.I)
    clean_m = re.sub(r"\b\d{1,2}\s*GB\s*(GDDR\d[X]?|DDR\d)?\b", "", clean_m, flags=re.I).strip()
    clean_m = clean_text(clean_m)

    gpu_series = "GeForce RTX/GTX"
    if "5090" in clean_m or "5080" in clean_m or "5070" in clean_m or "5060" in clean_m or "5050" in clean_m or "blackwell" in clean_m.lower():
        gpu_series = "RTX 50 Series"
        if not vram_str and "5050" in clean_m: vram_str = "8 GB GDDR7"
        elif not vram_str and "5060" in clean_m: vram_str = "8 GB GDDR7"
        elif not vram_str and "5070" in clean_m: vram_str = "8 GB GDDR7"
        elif not vram_str and "5080" in clean_m: vram_str = "16 GB GDDR7"
        elif not vram_str and "5090" in clean_m: vram_str = "24 GB GDDR7"
        elif not vram_str and "5000" in clean_m: vram_str = "24 GB GDDR7"
        elif not vram_str and "4000" in clean_m: vram_str = "16 GB GDDR7"
        elif not vram_str and "3000" in clean_m: vram_str = "12 GB GDDR7"
        elif not vram_str and "2000" in clean_m: vram_str = "8 GB GDDR7"
        elif not vram_str and "1000" in clean_m: vram_str = "8 GB GDDR7"
        elif not vram_str and "500" in clean_m: vram_str = "6 GB GDDR7"
    elif "4090" in clean_m or "4080" in clean_m or "4070" in clean_m or "4060" in clean_m or "4050" in clean_m or "ada" in clean_m.lower():
        gpu_series = "RTX 40 Series"
        if not vram_str and "4050" in clean_m: vram_str = "6 GB GDDR6"
        elif not vram_str and "4060" in clean_m: vram_str = "8 GB GDDR6"
        elif not vram_str and "4070" in clean_m: vram_str = "8 GB GDDR6"
        elif not vram_str and "4080" in clean_m: vram_str = "12 GB GDDR6"
        elif not vram_str and "4090" in clean_m: vram_str = "16 GB GDDR6"
        elif not vram_str and "500" in clean_m: vram_str = "4 GB GDDR6"
    elif "3080" in clean_m or "3070" in clean_m or "3060" in clean_m or "3050" in clean_m:
        gpu_series = "RTX 30 Series"
        if not vram_str and "3050 a" in clean_m.lower(): vram_str = "4 GB GDDR6"
        elif not vram_str and "3050" in clean_m: vram_str = "6 GB GDDR6"
        elif not vram_str and "3060" in clean_m: vram_str = "6 GB GDDR6"
        elif not vram_str and "3070" in clean_m: vram_str = "8 GB GDDR6"
    elif "2050" in clean_m:
        gpu_series = "RTX 20 Series"
        if not vram_str and "2050" in clean_m: vram_str = "4 GB GDDR6"
    elif "1650" in clean_m or "1660" in clean_m:
        gpu_series = "GTX 16 Series"
        if not vram_str: vram_str = "4 GB GDDR6"

    full_dgpu_name = f"{clean_m} {vram_str}".strip() if vram_str else clean_m
    return full_dgpu_name, vram_str, gpu_series


def normalize_product(product: dict[str, Any]) -> tuple[bool, str | None]:
    specs = product.get("tech_specs") or {}
    proc = specs.get("processor") or {}
    gpu = specs.get("graphics") or {}
    
    brand = proc.get("brand") or ""
    raw_cpu_model = proc.get("model") or proc.get("full_model") or ""
    raw_gpu_model = gpu.get("model") or ""
    
    if brand.lower() == "snapdragon" or "snapdragon" in str(raw_cpu_model).lower() or "qualcomm" in str(raw_cpu_model).lower():
        proc["brand"] = "Qualcomm"
        proc["series"] = "Snapdragon Series"
        return True, None

    if not raw_cpu_model:
        return False, "Missing CPU model"

    norm_full_cpu, cpu_series = normalize_cpu_name(raw_cpu_model, brand)
    norm_short_cpu = norm_full_cpu.replace("Intel ", "").replace("AMD ", "").strip()
    
    proc["brand"] = "Intel" if "Intel" in norm_full_cpu else ("AMD" if "AMD" in norm_full_cpu else brand)
    proc["model"] = norm_short_cpu
    proc["full_model"] = norm_full_cpu
    proc["series"] = cpu_series
    
    cpu_details = enrich_cpu_details(norm_full_cpu, raw_gpu_model)
    igpu_name = cpu_details["igpu"]
    igpu_series = cpu_details["igpu_series"]
    
    proc["igpu"] = igpu_name
    proc["igpu_series"] = igpu_series
    if cpu_details.get("cores"):
        proc["cores"] = cpu_details["cores"]
    if cpu_details.get("threads"):
        proc["threads"] = cpu_details["threads"]
    if cpu_details.get("base_clock") and not proc.get("base_clock"):
        proc["base_clock"] = cpu_details["base_clock"]
    if cpu_details.get("boost_clock") and not proc.get("boost_clock"):
        proc["boost_clock"] = cpu_details["boost_clock"]
    if cpu_details.get("xe_cores"):
        proc["xe_cores"] = cpu_details["xe_cores"]

    is_dedicated = not is_integrated_graphics(raw_gpu_model) if gpu.get("dedicated") is None else bool(gpu.get("dedicated"))
    if is_integrated_graphics(raw_gpu_model):
        is_dedicated = False

    if is_dedicated:
        clean_gpu, extracted_vram, gpu_series = normalize_dgpu_model(raw_gpu_model, gpu.get("vram") or "")
        gpu["model"] = clean_gpu
        gpu["series"] = gpu_series
        gpu["dedicated"] = True
        if extracted_vram and not gpu.get("vram"):
            gpu["vram"] = extracted_vram
        elif gpu.get("vram") and gpu["vram"] != "Shared":
            gpu["vram"] = clean_text(gpu["vram"])
        if gpu.get("tgp"):
            gpu["tgp"] = clean_text(gpu["tgp"])
    else:
        gpu["model"] = igpu_name
        gpu["series"] = igpu_series
        gpu["vram"] = "Shared"
        gpu["dedicated"] = False
        gpu["boost_clock"] = cpu_details.get("igpu_boost_clock") or ""
        gpu["tgp"] = ""
        if cpu_details.get("xe_cores"):
            gpu["xe_cores"] = cpu_details["xe_cores"]
        if cpu_details.get("cus"):
            gpu["cus"] = cpu_details["cus"]

    return True, None


def normalize_cto_configs() -> int:
    cto_dir = REPO_ROOT / "apps/web/cto_configs"
    if not cto_dir.exists():
        return 0
    
    count = 0
    for file_path in cto_dir.glob("*.json"):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            modified = False
            
            # Find default CPU model for the CTO product
            default_cpu_model = ""
            for option in data.get("options", []):
                if option.get("label") == "Processor":
                    for choice in option.get("choices", []):
                        if choice.get("isDefault"):
                            default_cpu_model = choice.get("specs", {}).get("processor") or choice.get("label", "")
                            break

            for option in data.get("options", []):
                for choice in option.get("choices", []):
                    specs = choice.get("specs")
                    label = choice.get("label", "")
                    
                    # Normalize CPU choices
                    if specs and "processor" in specs:
                        proc_val = specs["processor"]
                        if is_valid_cpu(proc_val):
                            clean_cpu, cpu_series = normalize_cpu_name(proc_val)
                            specs["processor"] = clean_cpu
                            modified = True
                    elif is_valid_cpu(label):
                        clean_cpu, cpu_series = normalize_cpu_name(label)
                        if not choice.get("specs"):
                            choice["specs"] = {}
                        choice["specs"]["processor"] = clean_cpu
                        modified = True

                    # Normalize GPU choices
                    if specs and "graphics" in specs:
                        gpu = specs["graphics"]
                        raw_model = gpu.get("model") or label
                        raw_vram = gpu.get("vram") or ""
                        if is_integrated_graphics(raw_model) or is_integrated_graphics(label):
                            info = enrich_cpu_details(default_cpu_model or "Intel Core Ultra 7", raw_model)
                            gpu["brand"] = "Intel" if "intel" in info["igpu"].lower() else ("AMD" if "radeon" in info["igpu"].lower() else "Integrated")
                            gpu["model"] = info["igpu"]
                            gpu["full_model"] = info["igpu"]
                            gpu["series"] = info["igpu_series"]
                            gpu["vram"] = "Shared"
                            gpu["dedicated"] = False
                            gpu["vram_gb"] = None
                            gpu["boost_clock_mhz"] = None
                            gpu["tgp_w"] = None
                            gpu["ai_tops"] = None
                            if info.get("igpu_boost_clock"):
                                gpu["boost_clock"] = info["igpu_boost_clock"]
                            if info.get("xe_cores"):
                                gpu["xe_cores"] = info["xe_cores"]
                            if info.get("cus"):
                                gpu["cus"] = info["cus"]
                        else:
                            clean_model, clean_vram, series = normalize_dgpu_model(raw_model, raw_vram)
                            gpu["model"] = clean_model
                            gpu["series"] = series
                            gpu["dedicated"] = True
                            if clean_vram:
                                gpu["vram"] = clean_vram
                        modified = True
                    elif is_integrated_graphics(label):
                        info = enrich_cpu_details(default_cpu_model or "Intel Core Ultra 7", label)
                        if not choice.get("specs"):
                            choice["specs"] = {}
                        choice["specs"]["graphics"] = {
                            "brand": "Intel" if "intel" in info["igpu"].lower() else ("AMD" if "radeon" in info["igpu"].lower() else "Integrated"),
                            "model": info["igpu"],
                            "full_model": info["igpu"],
                            "series": info["igpu_series"],
                            "vram": "Shared",
                            "dedicated": False,
                            "boost_clock": info.get("igpu_boost_clock") or ""
                        }
                        if info.get("xe_cores"):
                            choice["specs"]["graphics"]["xe_cores"] = info["xe_cores"]
                        modified = True
                        
            if modified:

                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                count += 1
        except Exception as e:
            print(f"Error normalizing CTO file {file_path}: {e}")
            
    return count


def run_normalization(catalog_data: dict[str, Any], target_ids: set[str] | None = None, force_all: bool = False) -> dict[str, Any]:
    matched_count = 0
    unmatched_count = 0
    unmatched_items: list[dict[str, Any]] = []

    for category, products in catalog_data.items():
        if not isinstance(products, list):
            continue
        for product in products:
            pid = str(product.get("id", ""))
            
            if target_ids and pid not in target_ids:
                continue
                
            specs = product.get("tech_specs") or {}
            proc = specs.get("processor") or {}
            if not force_all and not target_ids and proc.get("full_model") and proc.get("igpu") and proc.get("series"):
                matched_count += 1
                continue

            matched, reason = normalize_product(product)
            if matched:
                matched_count += 1
            else:
                unmatched_count += 1
                unmatched_items.append({
                    "id": pid,
                    "title": product.get("title") or product.get("model_name"),
                    "raw_cpu": proc.get("model"),
                    "raw_gpu": (product.get("tech_specs") or {}).get("graphics", {}).get("model"),
                    "reason": reason
                })

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with open(UNMATCHED_REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "total_unmatched": unmatched_count,
            "total_matched": matched_count,
            "unmatched_items": unmatched_items
        }, f, indent=2)

    print(f"Normalization Complete: {matched_count} matched/processed, {unmatched_count} unmatched.")
    print(f"Internal debug report saved to: {UNMATCHED_REPORT_PATH}")
    
    return catalog_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize CPU and GPU specifications in laptop catalog.")
    parser.add_argument("--all", action="store_true", help="Process all laptops in catalog.")
    parser.add_argument("--ids", type=str, help="Comma-separated list of laptop IDs to process.")
    args = parser.parse_args()

    if not CATALOG_PATH.exists():
        print(f"Catalog path not found: {CATALOG_PATH}")
        return

    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        catalog_data = json.load(f)

    target_ids = set(args.ids.split(",")) if args.ids else None
    updated_catalog = run_normalization(catalog_data, target_ids=target_ids, force_all=args.all)

    with open(CATALOG_PATH, "w", encoding="utf-8") as f:
        json.dump(updated_catalog, f, indent=2, ensure_ascii=False)

    print(f"Updated {CATALOG_PATH}")

    cto_count = normalize_cto_configs()
    print(f"Normalized {cto_count} CTO config files in apps/web/cto_configs/")


if __name__ == "__main__":
    main()
