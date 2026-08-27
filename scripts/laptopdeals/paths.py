from __future__ import annotations

from pathlib import Path


def _find_repo_root() -> Path:
    p = Path(__file__).resolve().parent
    top = p
    candidate = None
    while p != p.parent:
        if (p / "pnpm-workspace.yaml").exists() or (p / "pnpm-lock.yaml").exists() or (p / "apps/web/data.json").exists():
            return p
        if (p / "apps/web").exists() and candidate is None:
            candidate = p
        if (p / ".git").exists():
            top = p
        p = p.parent
    return candidate or top


REPO_ROOT = _find_repo_root()
TARGET_ROOT = REPO_ROOT / "website-target" if (REPO_ROOT / "website-target").exists() else REPO_ROOT


def raw_catalog(provider: str) -> Path:
    return REPO_ROOT / f"data/{provider}-catalog.json"


def new_ids(provider: str) -> Path:
    return REPO_ROOT / f"data/{provider}-new-ids.json"


def app_data(provider: str) -> Path:
    return TARGET_ROOT / (f"apps/web/data.json" if provider == "lenovo" else f"apps/web/data-{provider}.json")


def archive(provider: str) -> Path:
    return TARGET_ROOT / (f"apps/web/archive.json" if provider == "lenovo" else f"apps/web/archive-{provider}.json")


def price_history_dir(provider: str) -> Path:
    return TARGET_ROOT / ("apps/web/price_history" if provider == "lenovo" else f"apps/web/price_history_{provider}")


def spec_inventory(provider: str) -> Path:
    return TARGET_ROOT / (f"data/spec_inventory.json" if provider == "lenovo" else f"data/spec_inventory_{provider}.json")


APP_DATA = app_data("lenovo")
ARCHIVE = archive("lenovo")
PRICE_HISTORY = price_history_dir("lenovo")
CTO_CONFIGS = TARGET_ROOT / "apps/web/cto_configs"
RAW_CATALOG = raw_catalog("lenovo")
NEW_IDS = new_ids("lenovo")
SPEC_INVENTORY = spec_inventory("lenovo")
PRICE_CLEANUP_REPORT = REPO_ROOT / "data/price-history-cleanup-report.json"
PSREF_DIR = TARGET_ROOT / "data/lenovo_psref"
PSREF_DATASHEETS_DIR = PSREF_DIR / "datasheets"
PSREF_SKU_DIR = PSREF_DIR / "by_sku"
PSREF_MAP = PSREF_DIR / "final_sku_specs.json"
CPU_INVENTORY = TARGET_ROOT / "data/cpu_inventory.json"
GPU_INVENTORY = TARGET_ROOT / "data/gpu_inventory.json"
IGPU_INVENTORY = TARGET_ROOT / "data/igpu_inventory.json"

APP_DATA_REFURB = app_data("refurb")
ARCHIVE_REFURB = archive("refurb")
PRICE_HISTORY_REFURB = price_history_dir("refurb")
SPEC_INVENTORY_REFURB = spec_inventory("refurb")
RAW_CATALOG_REFURB = raw_catalog("refurb")
NEW_IDS_REFURB = new_ids("refurb")


def resolve(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else REPO_ROOT / value
