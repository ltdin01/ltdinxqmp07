#!/usr/bin/env python3
"""
Hardware Spec Normalization Engine for LaptopDeals.

Normalizes Intel, AMD, and Snapdragon CPUs, integrated GPUs (iGPUs), and discrete GPUs (dGPUs)
against authoritative hardware inventories, preserving manufacturer TGP/TDP and PSREF specs.

Rules:
1. PSREF Layer: For Lenovo products, specs are normalized first from PSREF catalog.
2. Hardware Inventory Layer: Specs are enriched using the pre-built local hardware databases (cpu_inventory.json, gpu_inventory.json, igpu_inventory.json).
3. If new hardware is discovered, check local data/inventory/ files first.
4. If NOT found in local inventories (brand-new unreleased hardware), DO NOT scrape web pages dynamically.
   Leave normalization unhandled and record a reflective action message in data/reports/unmatched_hardware_report.json.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from .paths import REPO_ROOT, APP_DATA, ARCHIVE, TARGET_ROOT, CPU_INVENTORY, GPU_INVENTORY, IGPU_INVENTORY, CTO_CONFIGS

DATA_DIR = TARGET_ROOT / "data"
CATALOG_PATH = APP_DATA
REPORT_DIR = DATA_DIR / "reports"
UNMATCHED_REPORT_PATH = REPORT_DIR / "unmatched_hardware_report.json"
INVENTORY_DIR = DATA_DIR / "inventory"

_CPU_INDEX: dict[str, dict[str, Any]] = {}
_GPU_INDEX: dict[str, dict[str, Any]] = {}
_IGPU_INDEX: dict[str, dict[str, Any]] = {}
_UNMATCHED_ITEMS: list[dict[str, Any]] = []


def _index_igpu_file(json_file: Path) -> None:
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            item = json.load(f)
    except Exception:
        return
    if not isinstance(item, dict):
        return
    full_m = clean_text(item.get("igpu_model") or item.get("full_model") or "").lower()
    short_m = clean_text(item.get("short_model") or "").lower()
    if full_m: _IGPU_INDEX[full_m] = item
    if short_m: _IGPU_INDEX[short_m] = item


def _index_cpu_file(json_file: Path) -> None:
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            item = json.load(f)
    except Exception:
        return
    if not isinstance(item, dict):
        return
    full_m = clean_text(item.get("full_model") or "").lower()
    short_m = clean_text(item.get("short_model") or "").lower()
    if full_m: _CPU_INDEX[full_m] = item
    if short_m: _CPU_INDEX[short_m] = item


def _index_gpu_file(json_file: Path) -> None:
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            item = json.load(f)
    except Exception:
        return
    if not isinstance(item, dict):
        return
    full_m = clean_text(item.get("full_model") or item.get("gpu_model") or "").lower()
    short_m = clean_text(item.get("short_model") or "").lower()
    if full_m: _GPU_INDEX[full_m] = item
    if short_m: _GPU_INDEX[short_m] = item


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


def build_inventory_indices() -> None:
    global _CPU_INDEX, _GPU_INDEX, _IGPU_INDEX
    _CPU_INDEX.clear()
    _GPU_INDEX.clear()
    _IGPU_INDEX.clear()

    # 1. Load consolidated CPU inventory
    for c_path in [CPU_INVENTORY, DATA_DIR / "intel_cpu_inventory.json", DATA_DIR / "amd_cpu_inventory.json"]:
        if c_path.exists():
            try:
                with open(c_path, "r", encoding="utf-8") as f:
                    procs = json.load(f).get("processors", {})
                    for p_key, p_val in procs.items():
                        if isinstance(p_val, dict):
                            full_m = clean_text(p_val.get("full_model") or "").lower()
                            short_m = clean_text(p_val.get("short_model") or "").lower()
                            if full_m: _CPU_INDEX[full_m] = p_val
                            if short_m: _CPU_INDEX[short_m] = p_val
            except Exception:
                pass

    # 2. Load consolidated dGPU inventory
    for g_path in [GPU_INVENTORY, DATA_DIR / "nvidia_gpu_inventory.json"]:
        if g_path.exists():
            try:
                with open(g_path, "r", encoding="utf-8") as f:
                    gpus = json.load(f).get("gpus", {})
                    for g_key, g_val in gpus.items():
                        if isinstance(g_val, dict):
                            full_m = clean_text(g_val.get("full_model") or g_val.get("gpu_model") or "").lower()
                            short_m = clean_text(g_val.get("short_model") or "").lower()
                            if full_m: _GPU_INDEX[full_m] = g_val
                            if short_m: _GPU_INDEX[short_m] = g_val
            except Exception:
                pass

    # 3. Load consolidated iGPU inventory
    for i_path in [IGPU_INVENTORY, DATA_DIR / "intel_igpu_inventory.json", DATA_DIR / "amd_igpu_inventory.json"]:
        if i_path.exists():
            try:
                with open(i_path, "r", encoding="utf-8") as f:
                    igpus = json.load(f).get("igpus", {})
                    for i_key, i_val in igpus.items():
                        if isinstance(i_val, dict):
                            full_m = clean_text(i_val.get("igpu_model") or i_val.get("full_model") or "").lower()
                            short_m = clean_text(i_val.get("short_model") or "").lower()
                            if full_m: _IGPU_INDEX[full_m] = i_val
                            if short_m: _IGPU_INDEX[short_m] = i_val
            except Exception:
                pass

    # 3b. Fallback: per-series iGPU files under data/inventory/{intel,amd}/igpus/
    if not _IGPU_INDEX and INVENTORY_DIR.exists():
        for json_file in INVENTORY_DIR.joinpath("intel", "igpus").rglob("*.json"):
            _index_igpu_file(json_file)
        for json_file in INVENTORY_DIR.joinpath("amd", "igpus").rglob("*.json"):
            _index_igpu_file(json_file)

    # 1b. Fallback: per-series CPU files under data/inventory/{intel,amd}/cpus/
    if not _CPU_INDEX and INVENTORY_DIR.exists():
        for json_file in INVENTORY_DIR.joinpath("intel", "cpus").rglob("*.json"):
            _index_cpu_file(json_file)
        for json_file in INVENTORY_DIR.joinpath("amd", "cpus").rglob("*.json"):
            _index_cpu_file(json_file)

    # 2b. Fallback: per-series dGPU files under data/inventory/nvidia/gpus/
    if not _GPU_INDEX and INVENTORY_DIR.exists():
        for json_file in INVENTORY_DIR.joinpath("nvidia", "gpus").rglob("*.json"):
            _index_gpu_file(json_file)


def extract_cpu_sku(text: str) -> str:
    m = re.search(r"\b(ultra\s+[x\d]?\s+\d{3}[a-z]*|[i3579][-\s]?\d{4,5}[a-z]*|\d{3}[u|h|v|hx]{1,2}|ryzen\s+[3579]\s+(?:pro\s+)?(?:\d{3,4}[a-z]*|40|30|20|10)|\d{4,5}[a-z]*)\b", text, re.I)
    return m.group(0).lower().replace(" ", "").replace("-", "") if m else ""


def extract_gpu_sku(text: str) -> str:
    m = re.search(r"\b(rtx\s*\d{4}(?:\s*ti)?|gtx\s*\d{4}(?:\s*ti)?|rx\s*\d{4}[a-z]*)\b", text, re.I)
    return m.group(0).lower().replace(" ", "") if m else ""


def find_cpu_in_local_data(cpu_name: str) -> dict[str, Any] | None:
    target = clean_text(cpu_name).lower()
    if not target:
        return None
        
    if target in _CPU_INDEX:
        return _CPU_INDEX[target]

    # Match by extracted SKU
    t_sku = extract_cpu_sku(target)
    if t_sku:
        for k, v in _CPU_INDEX.items():
            if extract_cpu_sku(k) == t_sku:
                return v

    # Partial match in active index
    for k, v in _CPU_INDEX.items():
        if k and len(k) > 4 and (k in target or target in k):
            return v

    # Deep scan local disk files under data/inventory/
    if INVENTORY_DIR.exists():
        for json_file in INVENTORY_DIR.rglob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    item = json.load(f)
                    if isinstance(item, dict) and "full_model" in item:
                        f_m = clean_text(item["full_model"]).lower()
                        if t_sku and extract_cpu_sku(f_m) == t_sku:
                            _CPU_INDEX[f_m] = item
                            return item
            except Exception:
                pass

    return None


def find_gpu_in_local_data(gpu_name: str) -> dict[str, Any] | None:
    target = clean_text(gpu_name).lower()
    if not target:
        return None
        
    if target in _GPU_INDEX:
        return _GPU_INDEX[target]

    t_sku = extract_gpu_sku(target)
    if t_sku:
        for k, v in _GPU_INDEX.items():
            if extract_gpu_sku(k) == t_sku:
                return v

    for k, v in _GPU_INDEX.items():
        if k and len(k) > 4 and (k in target or target in k):
            return v

    if INVENTORY_DIR.exists():
        for json_file in INVENTORY_DIR.rglob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    item = json.load(f)
                    if isinstance(item, dict) and ("full_model" in item or "gpu_model" in item):
                        f_m = clean_text(item.get("full_model") or item.get("gpu_model") or "").lower()
                        if t_sku and extract_gpu_sku(f_m) == t_sku:
                            _GPU_INDEX[f_m] = item
                            return item
            except Exception:
                pass

    return None


def normalize_cpu_name(raw_name: Any, brand: str = "") -> tuple[str, str]:
    text = clean_text(raw_name)
    text = re.sub(r"\(.*?\)", "", text).strip()
    text = re.sub(r"\b(Processor|vPro)\b", "", text, flags=re.I).strip()
    text = re.sub(r"^\d+(?:st|nd|rd|th)\s+(?:Generation|Gen)\s+(?:Intel\s+)?", "", text, flags=re.I).strip()
    text = re.sub(r"\s+", " ", text).strip()

    if brand.lower() == "snapdragon" or "snapdragon" in text.lower() or "qualcomm" in text.lower():
        full_m = re.sub(r"^(?:Qualcomm\s+)?", "Qualcomm ", text, flags=re.I).strip()
        return full_m, "Snapdragon X Series"

    if brand == "Intel" or "intel" in text.lower() or "core" in text.lower() or re.search(r"\bi[3579]-", text, re.I):
        text = re.sub(r"^(?:Intel\s+)?", "", text, flags=re.I).strip()
        
        # 1. Core Ultra Series
        ultra_match = re.search(r"\b(Core\s+)?Ultra\s+([X\d]+)\s+([A-Z]?\d{3,4}[A-Z]{0,3})\b", text, re.I)
        if ultra_match:
            u_num = ultra_match.group(2)
            u_sku = ultra_match.group(3).upper()
            full_m = f"Intel Core Ultra {u_num} {u_sku}"
            num_val = int(re.search(r"\d+", u_sku).group(0)) if re.search(r"\d+", u_sku) else 100
            if num_val >= 300:
                series = "Core Ultra Series 3"
            elif u_sku.endswith("V") or num_val >= 200:
                series = "Core Ultra Series 2"
            else:
                series = "Core Ultra Series 1"
            return full_m, series
            
        # 2. Core i3/i5/i7/i9
        core_i_match = re.search(r"\b(Core\s+)?(i[3579])[-_\s]?(\d{4,5}[A-Z]{0,3})\b", text, re.I)
        if core_i_match:
            i_sku = core_i_match.group(3).upper()
            full_m = f"Intel Core {core_i_match.group(2).lower()}-{i_sku}"
            gen_num = int(i_sku[:2]) if len(i_sku) >= 5 else int(i_sku[0])
            series = f"Core {gen_num}th Gen" if gen_num in list(range(2, 15)) else "Core Mobile"
            return full_m, series
            
        # 3. Core Series 1 / Series 2 / Series 3 (Core 7 150U, Core 5 120U, Core 7 250U, Core 5 220U, Core 9 270H, Core 7 360)
        core_series_match = re.search(r"\b(Core\s+)?([3579])\s+(\d{3}[A-Z]{0,2})\b", text, re.I)
        if core_series_match:
            sku_num = core_series_match.group(3).upper()
            full_m = f"Intel Core {core_series_match.group(2)} {sku_num}"
            if sku_num.startswith("3"): series = "Core Series 3"
            elif sku_num.startswith("2"): series = "Core Series 2"
            else: series = "Core Series 1"
            return full_m, series

        # 4. Processor N-series
        n_match = re.search(r"\b(Processor\s+)?(N\d{3})\b", text, re.I)
        if n_match:
            return f"Intel Processor {n_match.group(2).upper()}", "Processor N-series"

        full_name = f"Intel {text}" if not text.startswith("Intel") else text
        return full_name, "Intel Core Series"

    elif brand == "AMD" or "amd" in text.lower() or "ryzen" in text.lower():
        text = re.sub(r"^(?:AMD\s+)?", "", text, flags=re.I).strip()
        
        # 1. Ryzen AI Series
        ai_match = re.search(r"\bRyzen\s+AI\s+([3579])(?:\s+PRO)?(?:\s+(HX))?(?:\s+PRO)?\s+(\d{3})\b", text, re.I)
        if ai_match:
            has_pro = "pro" in text.lower()
            has_hx = bool(ai_match.group(2))
            digit = ai_match.group(3)[0]
            full_m = "AMD Ryzen AI {}{}{} {}".format(
                ai_match.group(1),
                " HX" if has_hx else "",
                " PRO" if has_pro else "",
                ai_match.group(3),
            )
            series = f"Ryzen AI {digit}00 Series"
            return full_m, series
            
        # 2. Standard Ryzen Series & Ryzen 200/100/10 Series
        ryzen_match = re.search(r"\bRyzen\s+([3579])(?:\s+PRO)?\s+([A-Z]*\d{2,4}[A-Z]{0,3})\b", text, re.I)
        if ryzen_match:
            pro_str = " PRO" if "pro" in text.lower() else ""
            sku_code = ryzen_match.group(2).upper()
            full_m = f"AMD Ryzen {ryzen_match.group(1)}{pro_str} {sku_code}"
            
            if re.search(r"\b(40|30|20|10)\b", sku_code): return full_m, "Ryzen 10 Series"
            elif re.search(r"\b2\d{2}\b", sku_code): return full_m, "Ryzen 200 Series"
            elif re.search(r"\b1\d{2}\b", sku_code): return full_m, "Ryzen 100 Series"

            num_part = re.search(r"\b([2-9])\d{3}[A-Z]*\b", sku_code)
            if num_part:
                series = f"Ryzen {num_part.group(1)}000 Series"
            else:
                num_part2 = re.search(r"\d{3,4}", sku_code)
                series = f"Ryzen {num_part2.group(0)[0]}000 Series" if (num_part2 and len(num_part2.group(0)) == 4) else "Ryzen Mobile"
            return full_m, series

        full_name = f"AMD {text}" if not text.startswith("AMD") else text
        return full_name, "Ryzen Mobile"

    return text, "Other Mobile"


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
    clean_m = re.sub(r"\b(Laptop GPU|GPU|Graphics)\b", "", clean_m, flags=re.I)
    clean_m = re.sub(r"\b\d{1,2}\s*GB\s*(GDDR\d[X]?|DDR\d)?\b", "", clean_m, flags=re.I).strip()
    clean_m = re.sub(r"\b(Shared|Integrated)\b", "", clean_m, flags=re.I).strip()
    clean_m = clean_text(clean_m)

    gpu_series = "GeForce RTX/GTX"
    if "5090" in clean_m or "5080" in clean_m or "5070" in clean_m or "5060" in clean_m or "5050" in clean_m or "blackwell" in clean_m.lower():
        gpu_series = "RTX 50 Series"
        if not vram_str:
            if "5090" in clean_m: vram_str = "24 GB GDDR7"
            elif "5080" in clean_m: vram_str = "16 GB GDDR7"
            elif "5070" in clean_m or "5060" in clean_m or "5050" in clean_m: vram_str = "8 GB GDDR7"
    elif "4090" in clean_m or "4080" in clean_m or "4070" in clean_m or "4060" in clean_m or "4050" in clean_m or "ada" in clean_m.lower():
        gpu_series = "RTX 40 Series"
        if not vram_str:
            if "4090" in clean_m: vram_str = "16 GB GDDR6"
            elif "4080" in clean_m: vram_str = "12 GB GDDR6"
            elif "4070" in clean_m or "4060" in clean_m: vram_str = "8 GB GDDR6"
            elif "4050" in clean_m: vram_str = "6 GB GDDR6"
    elif "3080" in clean_m or "3070" in clean_m or "3060" in clean_m or "3050" in clean_m:
        gpu_series = "RTX 30 Series"
        if not vram_str:
            if "3070" in clean_m: vram_str = "8 GB GDDR6"
            elif "3060" in clean_m or "3050" in clean_m: vram_str = "6 GB GDDR6"
    elif "2050" in clean_m:
        gpu_series = "RTX 20 Series"
        if not vram_str: vram_str = "4 GB GDDR6"
    elif "1650" in clean_m or "1660" in clean_m:
        gpu_series = "GTX 16 Series"
        if not vram_str: vram_str = "4 GB GDDR6"

    full_dgpu_name = f"{clean_m} {vram_str}".strip() if vram_str else clean_m
    return full_dgpu_name, vram_str, gpu_series


# --- Shared CPU / GPU normalization helpers ---------------------------------
# Used by both prebuilt product catalog entries and Lenovo CTO config choices so
# that CPU and GPU specs (names, series, clocks, TOPS, Xe-Cores/CUs, VRAM) are
# identical across every configuration type.

AMD_IGPU_CU_MAP = {
    "890m": 16, "880m": 12, "870m": 12, "860m": 8, "850m": 8, "840m": 8,
    "780m": 12, "770m": 12, "760m": 8, "740m": 4,
    "680m": 12, "660m": 6, "650m": 6, "610m": 2,
}

_CANONICAL_GPU_NAME_CASE = {
    "intel arc graphics": "Intel Arc Graphics",
    "intel graphics": "Intel Graphics",
    "intel uhd graphics": "Intel UHD Graphics",
}


def _igpu_cus_from_model(igpu_model: str) -> int | None:
    m = re.search(r"\b(\d{3}m)\b", clean_text(igpu_model), re.I)
    return AMD_IGPU_CU_MAP.get(m.group(1).lower()) if m else None


def is_dedicated_gpu_name(raw_gpu_model: str) -> bool:
    low = str(raw_gpu_model).lower()
    if any(k in low for k in ["integrated", "shared", "onboard"]):
        return False
    if any(k in low for k in ["rtx", "gtx", "geforce", "quadro", "titan", "blackwell", "ada lovelace"]):
        return True
    if re.search(r"\bradeon rx\s+(?!vega)", low):
        return True
    return False


def _clean_gpu_name(name: str) -> str:
    cleaned = clean_text(name).strip()
    return _CANONICAL_GPU_NAME_CASE.get(cleaned.lower(), cleaned)


def _report_unmatched(ctx: dict[str, Any] | None, component: str, raw_value: str, norm_attempt: str, action_message: str) -> None:
    _UNMATCHED_ITEMS.append({
        "product_id": (ctx or {}).get("product_id") or "unknown",
        "title": (ctx or {}).get("title") or "",
        "status": "UNMATCHED_HARDWARE",
        "missing_component": component,
        "raw_value": raw_value,
        "normalized_attempt": norm_attempt,
        "action_message": action_message,
    })


def enrich_cpu_spec(proc: dict[str, Any], raw_cpu_model: str, brand: str = "", ctx: dict[str, Any] | None = None) -> None:
    """Canonicalize a processor name and enrich it from the local CPU inventory."""
    norm_full_cpu, cpu_series = normalize_cpu_name(raw_cpu_model, brand)
    norm_short_cpu = norm_full_cpu.replace("Intel ", "").replace("AMD ", "").replace("Qualcomm ", "").strip()

    proc["brand"] = "Intel" if "Intel" in norm_full_cpu else ("AMD" if "AMD" in norm_full_cpu else ("Qualcomm" if "Qualcomm" in norm_full_cpu else brand))
    proc["model"] = norm_short_cpu
    proc["full_model"] = norm_full_cpu
    proc["series"] = cpu_series

    cpu_spec = find_cpu_in_local_data(norm_full_cpu)
    if cpu_spec:
        cores_obj = cpu_spec.get("cores")
        if isinstance(cores_obj, dict):
            if cores_obj.get("total_cores"): proc["cores"] = cores_obj["total_cores"]
            if cores_obj.get("total_threads"): proc["threads"] = cores_obj["total_threads"]
            if cores_obj.get("threads"): proc["threads"] = cores_obj["threads"]
        elif isinstance(cores_obj, (int, str)):
            proc["cores"] = cores_obj

        clocks_obj = cpu_spec.get("clock_speeds")
        if isinstance(clocks_obj, dict):
            base_clock = (
                clocks_obj.get("p_core_base_ghz")
                or clocks_obj.get("base_clock")
                or clocks_obj.get("base_clock_ghz")
            )
            if base_clock:
                proc["base_clock"] = base_clock
            boost_clock = (
                clocks_obj.get("p_core_max_turbo_ghz")
                or clocks_obj.get("boost_clock")
                or clocks_obj.get("boost_clock_ghz")
            )
            if boost_clock:
                proc["boost_clock"] = boost_clock

        if cpu_spec.get("igpu"):
            igpu_obj = cpu_spec["igpu"]
            if isinstance(igpu_obj, dict):
                if igpu_obj.get("model"): proc["igpu"] = igpu_obj["model"]
                if igpu_obj.get("series"): proc["igpu_series"] = igpu_obj["series"]
                if igpu_obj.get("xe_cores"): proc["xe_cores"] = f"{igpu_obj['xe_cores']} Xe-Cores"
                if igpu_obj.get("cus"): proc["cus"] = f"{igpu_obj['cus']} CUs"
            elif isinstance(igpu_obj, str):
                proc["igpu"] = igpu_obj
    else:
        _report_unmatched(ctx, "CPU", raw_cpu_model, norm_full_cpu, f"UNMATCHED HARDWARE: Processor '{norm_full_cpu}' not found in local hardware inventory. Requires manual addition or wiki refresh.")

    # Purge raw nested dict keys so only flattened primitives render
    for raw_key in ["clock_speeds", "power", "cache", "memory", "intel_official_specs", "intel_specs", "amd_specs", "npu", "code_name", "code_name_slug", "raw", "base_clock_ghz", "boost_clock_ghz"]:
        proc.pop(raw_key, None)


def enrich_dedicated_gpu(gpu: dict[str, Any], raw_gpu_model: str, ctx: dict[str, Any] | None = None) -> None:
    """Normalize a discrete GPU name and enrich it from the local dGPU inventory."""
    clean_gpu, extracted_vram, gpu_series = normalize_dgpu_model(raw_gpu_model, gpu.get("vram") or "")
    gpu["model"] = clean_gpu
    gpu["series"] = gpu_series
    gpu["dedicated"] = True
    if extracted_vram and not gpu.get("vram"): gpu["vram"] = extracted_vram

    gpu_spec = find_gpu_in_local_data(clean_gpu)
    if gpu_spec:
        if gpu_spec.get("tgp_range_w"): gpu["tgp"] = gpu_spec["tgp_range_w"]
        if gpu_spec.get("vram_config"): gpu["vram_config"] = gpu_spec["vram_config"]
        if gpu_spec.get("cuda_cores"): gpu["cuda_cores"] = gpu_spec["cuda_cores"]
    else:
        _report_unmatched(ctx, "dGPU", raw_gpu_model, clean_gpu, f"UNMATCHED HARDWARE: Graphics card '{clean_gpu}' not found in local hardware inventory. Requires manual addition or wiki refresh.")


def enrich_integrated_gpu(gpu: dict[str, Any], cpu_spec: dict[str, Any] | None, ctx: dict[str, Any] | None = None) -> None:
    """Enrich an integrated GPU (iGPU) from the CPU's hardware inventory entry."""
    gpu["dedicated"] = False
    gpu["vram"] = "Shared"
    gpu.pop("tgp", None)
    gpu.pop("cuda_cores", None)
    gpu.pop("full_model", None)
    for stale in ("vram_gb", "boost_clock_mhz", "tgp_w", "raw"):
        gpu.pop(stale, None)

    if not cpu_spec:
        return

    igpu_obj = cpu_spec.get("igpu")
    intel_gpu_specs = (cpu_spec.get("intel_official_specs") or {}).get("GPU Specifications", {})
    xe_cores = (
        intel_gpu_specs.get("Xe-cores") or
        (igpu_obj.get("xe_cores") if isinstance(igpu_obj, dict) else "")
    )
    if xe_cores:
        gpu["xe_cores"] = str(xe_cores)

    # Canonical iGPU name: prefer the official ARK GPU Name; otherwise fall back to
    # the inventory iGPU model. Never synthesize "Intel Arc Graphics (N Xe-Cores)".
    official_gpu_name = (
        intel_gpu_specs.get("GPU Name") or
        (igpu_obj.get("model") if isinstance(igpu_obj, dict) else (igpu_obj if isinstance(igpu_obj, str) else "")) or
        gpu.get("model") or ""
    )
    if official_gpu_name:
        gpu["model"] = _clean_gpu_name(official_gpu_name)
        if isinstance(igpu_obj, dict) and igpu_obj.get("series"):
            gpu["series"] = igpu_obj["series"]

    igpu_boost = ""
    if isinstance(igpu_obj, dict):
        igpu_boost = igpu_obj.get("boost_clock") or igpu_obj.get("max_dynamic_frequency") or ""
    elif isinstance(igpu_obj, str):
        igpu_boost = ""
    boost_clock = (
        intel_gpu_specs.get("Graphics Max Dynamic Frequency") or
        igpu_boost
    )
    if boost_clock:
        gpu["boost_clock"] = str(boost_clock)

    # AMD compute units (the CPU iGPU entry does not carry a CU count)
    if "radeon" in clean_text(gpu.get("model") or "").lower():
        cus = _igpu_cus_from_model(gpu.get("model") or "")
        if cus:
            gpu["cus"] = cus

    ai_tops = (
        intel_gpu_specs.get("GPU Peak TOPS (Int8)") or
        (igpu_obj.get("ai_tops") if isinstance(igpu_obj, dict) else "")
    )
    if ai_tops:
        gpu["ai_tops"] = f"{ai_tops} TOPS" if not str(ai_tops).endswith("TOPS") else str(ai_tops)


def normalize_product(product: dict[str, Any]) -> tuple[bool, str | None]:
    specs = product.get("tech_specs")
    if not isinstance(specs, dict):
        return False, "Missing tech_specs"

    raw_proc = specs.get("processor") or {}
    raw_gpu = specs.get("graphics") or {}

    if isinstance(raw_proc, str) or (isinstance(raw_proc, dict) and not raw_proc.get("model") and not raw_proc.get("full_model")):
        proc = {"model": raw_proc} if isinstance(raw_proc, str) else {}
    else:
        proc = raw_proc

    if isinstance(raw_gpu, str):
        gpu = {"model": raw_gpu}
    else:
        gpu = raw_gpu

    brand = proc.get("brand") or ""
    raw_cpu_model = proc.get("model") or proc.get("full_model") or ""
    raw_gpu_model = gpu.get("model") or ""

    if not raw_cpu_model:
        return False, "Missing CPU model"

    ctx = {
        "product_id": product.get("id") or product.get("sku") or "unknown",
        "title": product.get("title") or product.get("name") or "",
    }

    enrich_cpu_spec(proc, raw_cpu_model, brand, ctx)

    if is_dedicated_gpu_name(raw_gpu_model):
        enrich_dedicated_gpu(gpu, raw_gpu_model, ctx)
    else:
        cpu_spec = find_cpu_in_local_data(proc["full_model"])
        enrich_integrated_gpu(gpu, cpu_spec, ctx)

    specs["processor"] = proc
    specs["graphics"] = gpu
    return True, None


def _bundle_default_cpu_spec(config: dict[str, Any]) -> dict[str, Any] | None:
    for option in config.get("options") or []:
        if option.get("label") != "Processor":
            continue
        choices = option.get("choices") or []
        if not choices:
            return None
        default_choice = next((c for c in choices if c.get("isDefault")), choices[0])
        proc_val = (default_choice.get("specs") or {}).get("processor")
        label = default_choice.get("label") or ""
        if isinstance(proc_val, str) and proc_val:
            return find_cpu_in_local_data(normalize_cpu_name(proc_val)[0])
        if isinstance(proc_val, dict):
            return find_cpu_in_local_data(proc_val.get("full_model") or proc_val.get("model") or label)
        return find_cpu_in_local_data(normalize_cpu_name(label)[0])
    return None


def normalize_cto_configs() -> int:
    cto_dir = CTO_CONFIGS
    if not cto_dir.exists():
        return 0

    if not _CPU_INDEX:
        build_inventory_indices()

    count = 0
    for file_path in sorted(cto_dir.glob("*.json")):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        default_cpu_spec = _bundle_default_cpu_spec(data)
        ctx = {"product_id": file_path.stem, "title": data.get("bundleId") or file_path.stem}
        modified = False

        for option in data.get("options") or []:
            for choice in option.get("choices") or []:
                specs = choice.get("specs")
                if not isinstance(specs, dict):
                    continue

                # --- Processor (string or dict -> prebuilt dict shape) ---
                p = specs.get("processor")
                if isinstance(p, str) and p:
                    proc: dict[str, Any] = {}
                    enrich_cpu_spec(proc, p, "", ctx)
                    specs["processor"] = proc
                    modified = True
                elif isinstance(p, dict) and (p.get("model") or p.get("full_model")):
                    enrich_cpu_spec(p, p.get("full_model") or p.get("model"), p.get("brand") or "", ctx)
                    modified = True

                # --- Graphics (integrated vs dedicated, same rules as prebuilts) ---
                gpu = specs.get("graphics")
                if isinstance(gpu, dict):
                    raw_gpu_model = gpu.get("model") or ""
                    if is_dedicated_gpu_name(raw_gpu_model):
                        enrich_dedicated_gpu(gpu, raw_gpu_model, ctx)
                    else:
                        cpu_spec = None
                        p = specs.get("processor")
                        if isinstance(p, dict):
                            cpu_spec = find_cpu_in_local_data(p.get("full_model") or p.get("model") or "")
                        elif isinstance(p, str) and p:
                            cpu_spec = find_cpu_in_local_data(normalize_cpu_name(p)[0])
                        enrich_integrated_gpu(gpu, cpu_spec or default_cpu_spec, ctx)
                    modified = True

        if modified:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            count += 1
            print(f"[normalize] Normalized CTO config: {file_path.stem}")

    return count


def _write_unmatched_report() -> None:
    if not _UNMATCHED_ITEMS:
        return
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in _UNMATCHED_ITEMS:
        key = (str(entry.get("product_id")), str(entry.get("missing_component")), str(entry.get("raw_value")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with open(UNMATCHED_REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(deduped, f, indent=2, ensure_ascii=False)
    print(f"[report] Logged {len(deduped)} unmatched hardware action messages to: {UNMATCHED_REPORT_PATH}")


def normalize_catalog() -> int:
    build_inventory_indices()
    print(f"[normalize] Loaded {len(_CPU_INDEX)} CPUs, {len(_GPU_INDEX)} dGPUs, {len(_IGPU_INDEX)} iGPUs from local inventory database.")

    target_files = [
        REPO_ROOT / "data/lenovo-catalog.json",
        APP_DATA,
        ARCHIVE
    ]

    total_normalized = 0
    for target in target_files:
        if not target.exists():
            continue
        try:
            with open(target, "r", encoding="utf-8") as f:
                raw_data = json.load(f)

            products: list[dict[str, Any]] = []
            if isinstance(raw_data, list):
                products = [p for p in raw_data if isinstance(p, dict)]
            elif isinstance(raw_data, dict):
                if "products" in raw_data:
                    products = raw_data["products"]
                elif "groups" in raw_data:
                    for v in raw_data["groups"].values():
                        if isinstance(v, list):
                            products.extend(item for item in v if isinstance(item, dict))
                else:
                    for v in raw_data.values():
                        if isinstance(v, list):
                            products.extend(item for item in v if isinstance(item, dict))

            if not products:
                continue

            count = 0
            for prod in products:
                if isinstance(prod, dict):
                    ok, _ = normalize_product(prod)
                    if ok: count += 1

            if target == APP_DATA:
                cto_configs_dir = CTO_CONFIGS
                for prod in products:
                    sku = str(prod.get("id") or "")
                    if "CTO" not in sku.upper():
                        continue
                    sidecar = cto_configs_dir / f"{sku}.json"
                    if sidecar.exists():
                        payload = json.loads(sidecar.read_text(encoding="utf-8"))
                        prod["cto_options"] = {k: v for k, v in payload.items() if k != "lastFetched"}

            with open(target, "w", encoding="utf-8") as f:
                json.dump(raw_data, f, indent=2, ensure_ascii=False)

            print(f"[normalize] Normalized {count}/{len(products)} products in {target.name}.")
            total_normalized += count
        except Exception as e:
            print(f"[warning] Failed normalizing {target.name}: {e}")

    if _UNMATCHED_ITEMS:
        _write_unmatched_report()

    return total_normalized


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize LaptopDeals hardware specs using local inventories.")
    parser.add_argument("--all", action="store_true", help="Process full catalog (prebuilts + CTO configs)")
    parser.add_argument("--no-cto", action="store_true", help="Skip CTO config normalization")
    args = parser.parse_args()

    normalize_catalog()
    if not args.no_cto:
        cto_count = normalize_cto_configs()
        print(f"[normalize] Normalized {cto_count} CTO config files in apps/web/cto_configs/.")


if __name__ == "__main__":
    main()
