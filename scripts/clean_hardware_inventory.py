#!/usr/bin/env python3
"""
Hardware Inventory Sanitizer & Post-Processor.

Recursively cleans and normalizes all hardware inventory files across
AMD, Intel, and NVIDIA inventories:
  - Replaces Unicode dashes (\u2013 en-dash, \u2014 em-dash, − minus) with standard ASCII '-'
  - Normalizes non-breaking spaces (\xa0) and zero-width spaces
  - Cleans redundant model name artifacts (e.g. 'Laptop GPU/ Laptop')
  - Fixes file naming slugs and updates master inventory JSON files.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "data"
INVENTORY_DIR = DATA_DIR / "inventory"


def sanitize_text(text: str) -> str:
    if not isinstance(text, str):
        return text
    is_url = "://" in text
    # Replace unicode dash variants with standard ASCII hyphen
    s = text.replace("\u2013", "-").replace("\u2014", "-").replace("–", "-").replace("—", "-").replace("−", "-")
    # Normalize non-breaking and zero-width spaces
    s = s.replace("\xa0", " ").replace("\u200b", "")
    # Clean redundant model suffix artifacts. Never applied to URLs: AMD product
    # page URLs carry a mandatory '/laptop/' path segment (e.g.
    # .../products/processors/laptop/ryzen/7000-series/...) and stripping it
    # yields a 404, which breaks spec enrichment on later runs.
    if not is_url:
        s = re.sub(r"Laptop\s*GPU/\s*Laptop", "Laptop GPU", s, flags=re.I)
        s = re.sub(r"Mobile/\s*Laptop", "Laptop GPU", s, flags=re.I)
        s = re.sub(r"/\s*Laptop", "", s, flags=re.I)
    # Strip multiple spaces
    s = re.sub(r"\s+", " ", s).strip()
    return s


def sanitize_data(obj: Any) -> Any:
    if isinstance(obj, str):
        return sanitize_text(obj)
    elif isinstance(obj, list):
        return [sanitize_data(item) for item in obj]
    elif isinstance(obj, dict):
        new_dict: dict[str, Any] = {}
        for k, v in obj.items():
            clean_k = sanitize_text(k)
            new_dict[clean_k] = sanitize_data(v)
        return new_dict
    return obj


def clean_inventory_directory(target_dir: Path) -> tuple[int, int]:
    file_count = 0
    renamed_count = 0

    if not target_dir.exists():
        return 0, 0

    for json_file in target_dir.glob("**/*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            cleaned_data = sanitize_data(data)

            # Check if filename has redundant slug suffix
            new_stem = sanitize_text(json_file.stem)
            new_stem = re.sub(r"_laptop_gpu_laptop$", "_laptop_gpu", new_stem)
            new_file = json_file.parent / f"{new_stem}.json"

            if new_file != json_file:
                if json_file.exists():
                    json_file.unlink()
                renamed_count += 1

            with open(new_file, "w", encoding="utf-8") as f:
                json.dump(cleaned_data, f, indent=2)

            file_count += 1
        except Exception as e:
            print(f"[cleaner-error] Failed to process {json_file}: {e}")

    return file_count, renamed_count


def clean_master_json_files() -> int:
    master_files = [
        DATA_DIR / "amd_cpu_inventory.json",
        DATA_DIR / "amd_igpu_inventory.json",
        DATA_DIR / "intel_cpu_inventory.json",
        DATA_DIR / "intel_igpu_inventory.json",
        DATA_DIR / "cpu_inventory.json",
        DATA_DIR / "igpu_inventory.json",
        DATA_DIR / "nvidia_gpu_inventory.json",
        DATA_DIR / "gpu_inventory.json"
    ]

    cleaned_master_count = 0
    for master_file in master_files:
        if not master_file.exists():
            continue
        try:
            with open(master_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            cleaned_data = sanitize_data(data)

            with open(master_file, "w", encoding="utf-8") as f:
                json.dump(cleaned_data, f, indent=2)

            cleaned_master_count += 1
        except Exception as e:
            print(f"[cleaner-error] Failed to process master file {master_file}: {e}")

    return cleaned_master_count


def clean_all_hardware_inventories() -> dict[str, int]:
    print("=== Sanitizing All Hardware Inventory Files (AMD, Intel, NVIDIA) ===")
    total_files, renamed_files = clean_inventory_directory(INVENTORY_DIR)
    master_count = clean_master_json_files()

    print(f"[sanitizer] Cleaned {total_files} individual inventory JSON files (renamed {renamed_files}).")
    print(f"[sanitizer] Cleaned {master_count} master inventory JSON files.")

    return {
        "individual_files_cleaned": total_files,
        "files_renamed": renamed_files,
        "master_files_cleaned": master_count
    }


if __name__ == "__main__":
    res = clean_all_hardware_inventories()
    print("=== Hardware Inventory Sanitization Complete ===")
    print(res)
