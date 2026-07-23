from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET_ROOT = REPO_ROOT / "website-target" if (REPO_ROOT / "website-target").exists() else REPO_ROOT

APP_DATA = TARGET_ROOT / "apps/web/data.json"
ARCHIVE = TARGET_ROOT / "apps/web/archive.json"
PRICE_HISTORY = TARGET_ROOT / "apps/web/price_history"
CTO_CONFIGS = TARGET_ROOT / "apps/web/cto_configs"
RAW_CATALOG = REPO_ROOT / "data/lenovo-catalog.json"
NEW_IDS = REPO_ROOT / "data/lenovo-new-ids.json"
SPEC_INVENTORY = TARGET_ROOT / "data/spec_inventory.json"
PRICE_CLEANUP_REPORT = REPO_ROOT / "data/price-history-cleanup-report.json"
PSREF_DIR = REPO_ROOT / "data/lenovo_psref"
PSREF_SKU_DIR = PSREF_DIR / "by_sku"
PSREF_MAP = PSREF_DIR / "final_sku_specs.json"


def resolve(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else REPO_ROOT / value
