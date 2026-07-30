from __future__ import annotations

from pathlib import Path


def _find_repo_root() -> Path:
    p = Path(__file__).resolve().parent
    top = p
    while p != p.parent:
        if (p / "apps/web").exists() or (p / "pnpm-workspace.yaml").exists():
            return p
        if (p / ".git").exists():
            top = p
        p = p.parent
    return top


REPO_ROOT = _find_repo_root()
TARGET_ROOT = REPO_ROOT / "website-target" if (REPO_ROOT / "website-target").exists() else REPO_ROOT


APP_DATA = TARGET_ROOT / "apps/web/data.json"
ARCHIVE = TARGET_ROOT / "apps/web/archive.json"
PRICE_HISTORY = TARGET_ROOT / "apps/web/price_history"
CTO_CONFIGS = TARGET_ROOT / "apps/web/cto_configs"
RAW_CATALOG = REPO_ROOT / "data/lenovo-catalog.json"
NEW_IDS = REPO_ROOT / "data/lenovo-new-ids.json"
SPEC_INVENTORY = TARGET_ROOT / "data/spec_inventory.json"
PRICE_CLEANUP_REPORT = REPO_ROOT / "data/price-history-cleanup-report.json"
PSREF_DIR = TARGET_ROOT / "data/lenovo_psref"
PSREF_SKU_DIR = PSREF_DIR / "by_sku"
PSREF_MAP = PSREF_DIR / "final_sku_specs.json"
CPU_INVENTORY = REPO_ROOT / "data/cpu_inventory.json"
GPU_INVENTORY = REPO_ROOT / "data/gpu_inventory.json"
IGPU_INVENTORY = REPO_ROOT / "data/igpu_inventory.json"





def resolve(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else REPO_ROOT / value
