#!/usr/bin/env python3
"""
Master Hardware Inventory Consolidation Engine for LaptopDeals.

Builds consolidated master hardware inventory databases directly from ALREADY FETCHED local disk inventories:
  - data/intel_cpu_inventory.json
  - data/amd_cpu_inventory.json
  - data/nvidia_gpu_inventory.json
  - data/intel_igpu_inventory.json
  - data/amd_igpu_inventory.json
  - data/inventory/ (all individual CPU, GPU, iGPU JSON files)

Consolidates local data into:
  - data/cpu_inventory.json (Master combined CPU inventory)
  - data/gpu_inventory.json (Master combined dGPU inventory)
  - data/igpu_inventory.json (Master combined iGPU inventory)
  - data/spec_inventory.json (Master combined specification database)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.scripts.clean_hardware_inventory import clean_all_hardware_inventories

DATA_DIR = REPO_ROOT / "data"
INVENTORY_DIR = DATA_DIR / "inventory"


def clean_text(text: Any) -> str:
    if not text:
        return ""
    res = str(text).replace("[™®]", "").replace("™", "").replace("®", "").replace("\xa0", " ")
    res = re.sub(r"\[[0-9a-zA-Z]+\]", "", res)
    return re.sub(r"\s+", " ", res).strip()


def slugify(text: str) -> str:
    s = clean_text(text).lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def load_local_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[warning] Could not load {path.name}: {e}")
        return {}


def consolidate_master_inventories() -> dict[str, int]:
    print("=== Consolidating Master Hardware Inventories From Local Disk Data ===")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    cpu_master: dict[str, Any] = {"processors": {}}
    gpu_master: dict[str, Any] = {"gpus": {}}
    igpu_master: dict[str, Any] = {"igpus": {}}
    spec_master: dict[str, Any] = {"cpus": {}, "gpus": {}, "igpus": {}}

    # 1. Load Intel & AMD CPUs from master files
    for file_name in ["intel_cpu_inventory.json", "amd_cpu_inventory.json"]:
        p_data = load_local_json(DATA_DIR / file_name)
        procs = p_data.get("processors", {})
        cpu_master["processors"].update(procs)
        spec_master["cpus"].update(procs)

    # 2. Load NVIDIA & dGPUs from master file
    gpu_data = load_local_json(DATA_DIR / "nvidia_gpu_inventory.json")
    gpus = gpu_data.get("gpus", {})
    gpu_master["gpus"].update(gpus)
    spec_master["gpus"].update(gpus)

    # 3. Load Intel & AMD iGPUs from master files
    for file_name in ["intel_igpu_inventory.json", "amd_igpu_inventory.json"]:
        i_data = load_local_json(DATA_DIR / file_name)
        igpus = i_data.get("igpus", {})
        igpu_master["igpus"].update(igpus)
        spec_master["igpus"].update(igpus)

    # 4. Deep scan individual JSON files under data/inventory/ for any missing items
    if INVENTORY_DIR.exists():
        for json_file in INVENTORY_DIR.rglob("*.json"):
            try:
                item = load_local_json(json_file)
                if not isinstance(item, dict):
                    continue

                full_m = item.get("full_model") or item.get("model") or item.get("gpu_model") or item.get("igpu_model")
                if not full_m:
                    continue

                # Determine type: CPU, dGPU, or iGPU
                if "cores" in item or "cpu_series" in item or "intel_ark_url" in item or "amd_product_url" in item:
                    s_slug = item.get("series_slug") or slugify(item.get("series") or "cpu")
                    c_slug = item.get("code_name_slug") or slugify(item.get("code_name") or "mobile")
                    key = f"{s_slug}_{c_slug}_{slugify(full_m)}"
                    if key not in cpu_master["processors"]:
                        cpu_master["processors"][key] = item
                    spec_master["cpus"][key] = item

                elif "tgp_range_w" in item or "cuda_cores" in item or "gpu_architecture" in item:
                    g_slug = item.get("series_slug") or slugify(item.get("series") or "gpu")
                    key = f"{g_slug}_{slugify(full_m)}"
                    if key not in gpu_master["gpus"]:
                        gpu_master["gpus"][key] = item
                    spec_master["gpus"][key] = item

                elif "igpu_model" in item or "max_dynamic_frequency_mhz" in item:
                    i_slug = item.get("series_slug") or slugify(item.get("series") or "igpu")
                    key = f"{i_slug}_{slugify(full_m)}"
                    if key not in igpu_master["igpus"]:
                        igpu_master["igpus"][key] = item
                    spec_master["igpus"][key] = item

            except Exception:
                pass

    # Save consolidated master inventory files
    cpu_path = DATA_DIR / "cpu_inventory.json"
    with open(cpu_path, "w", encoding="utf-8") as f:
        json.dump(cpu_master, f, indent=2, ensure_ascii=False)

    gpu_path = DATA_DIR / "gpu_inventory.json"
    with open(gpu_path, "w", encoding="utf-8") as f:
        json.dump(gpu_master, f, indent=2, ensure_ascii=False)

    igpu_path = DATA_DIR / "igpu_inventory.json"
    with open(igpu_path, "w", encoding="utf-8") as f:
        json.dump(igpu_master, f, indent=2, ensure_ascii=False)

    spec_path = DATA_DIR / "spec_inventory.json"
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(spec_master, f, indent=2, ensure_ascii=False)

    print(f"[master] Consolidated CPU Inventory: {len(cpu_master['processors'])} entries -> {cpu_path}")
    print(f"[master] Consolidated dGPU Inventory: {len(gpu_master['gpus'])} entries -> {gpu_path}")
    print(f"[master] Consolidated iGPU Inventory: {len(igpu_master['igpus'])} entries -> {igpu_path}")
    print(f"[master] Consolidated Master Spec Database: {spec_path}")

    # Sanitize all hardware inventory files locally
    clean_all_hardware_inventories()

    return {
        "cpu_count": len(cpu_master["processors"]),
        "gpu_count": len(gpu_master["gpus"]),
        "igpu_count": len(igpu_master["igpus"])
    }


def run_all_scrapers() -> None:
    print("--- Invoking Dynamic Intel Hardware Inventory Pipeline ---")
    try:
        from pipeline.scripts.build_intel_inventory import build_intel_inventory
        build_intel_inventory(parallel_workers=16)
    except Exception as e:
        print(f"[error] Intel pipeline error: {e}")

    print("--- Invoking Dynamic AMD Hardware Inventory Pipeline ---")
    try:
        from pipeline.scripts.build_amd_inventory import build_amd_inventory
        build_amd_inventory(parallel_workers=16)
    except Exception as e:
        print(f"[error] AMD pipeline error: {e}")

    print("--- Invoking Dynamic NVIDIA Hardware Inventory Pipeline ---")
    try:
        from pipeline.scripts.build_nvidia_inventory import build_nvidia_inventory
        build_nvidia_inventory()
    except Exception as e:
        print(f"[error] NVIDIA pipeline error: {e}")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Build and consolidate hardware inventories.")
    parser.add_argument("--scrape", action="store_true", help="Run full dynamic scrapers for Intel, AMD, NVIDIA first")
    args = parser.parse_args()

    if args.scrape:
        run_all_scrapers()

    res = consolidate_master_inventories()
    print("=== Master Hardware Inventory Consolidation Complete ===")
    print(res)


if __name__ == "__main__":
    main()
