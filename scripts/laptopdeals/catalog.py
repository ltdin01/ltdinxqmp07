from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .datafile import iter_products
from .ids import normalize_id
from .jsonio import read_json
from .providers import DEFAULT_PROVIDER, get_provider
from .sources import lenovo
from .specs import clean_text


LIVE_FIELDS = ("price", "mrp", "availability", "price_mean", "price_median", "price_usual", "has_history", "last_checked")
TECHNICAL_NAME_RE = re.compile(r"^(?:LEN[0-9A-Z]+|[0-9A-Z]{8,})$")
MODEL_CODE_RE = re.compile(r"\b\d{1,3}[A-Z]{2,}\d{0,4}\b")
DESCRIPTIVE_WORDS_RE = re.compile(r"\b(?:Gen|Intel|AMD|Ryzen|Core|Ultra|Snapdragon|cm|inch|display|backlit|fingerprint|webcam|privacy)\b", re.IGNORECASE)
KNOWN_BRAND_RE = re.compile(r"\b(?:Lenovo|LOQ|Legion|ThinkPad|IdeaPad|Yoga|ThinkBook|Flex|Slim|Pro)\b", re.IGNORECASE)
GENERIC_TAGLINE_RE = re.compile(
    r"\b(?:laptop|pc|computer)\s+for\b|\bfor\s+(?:students|gamers|enthusiasts|professionals)\b|\bAI-tuned\s+gaming\b|^\d{1,2}(?:\.\d)?\s*[- ]*(?:inch|\")?\s*laptop\b",
    re.IGNORECASE,
)
RAW_INTERNAL_PREFIX_RE = re.compile(r"^(?:NB|LOIS|MNL)\s+", re.IGNORECASE)


def existing_ids_from(paths: list[Path]) -> set[str]:
    ids: set[str] = set()
    for path in paths:
        payload = read_json(path, {})
        for _, product in iter_products(payload):
            pid = normalize_id(product.get("id") or product.get("product_code"))
            if pid:
                is_arch = bool(product.get("archived") or product.get("archived_at"))
                if not is_arch:
                    ids.add(pid)
    return ids


def is_display_name(value: Any, sku: str = "") -> bool:
    text = lenovo.clean_text(value)
    if not text:
        return False
    upper = text.upper()
    if sku and upper == sku.upper():
        return False
    if TECHNICAL_NAME_RE.fullmatch(upper) and any(char.isdigit() for char in upper):
        return False
    if "_" in text:
        return False
    if text.lower() in {"home", "laptops"}:
        return False
    if re.search(r"\b(series|laptops?)\b", text, flags=re.IGNORECASE) and not re.search(r"\bgen\b|\d", text, flags=re.IGNORECASE):
        return False
    if GENERIC_TAGLINE_RE.search(text):
        return False
    if RAW_INTERNAL_PREFIX_RE.search(text):
        return False
    if MODEL_CODE_RE.search(upper) and not DESCRIPTIVE_WORDS_RE.search(text) and not KNOWN_BRAND_RE.search(text):
        return False
    if _is_doubled_text(text):
        return False
    return any(char.isalpha() for char in text)


def _is_doubled_text(text: str) -> bool:
    t = text.strip()
    mid = len(t) // 2
    if mid < 4:
        return False
    if len(t) % 2 == 0 and t[:mid] == t[mid:]:
        return True
    if mid + 1 < len(t) and t[:mid] == t[mid + 1:]:
        return True
    return False


def model_name(title: str) -> str:
    parts = [part.strip() for part in str(title or "").split(",")]
    return ", ".join(parts[:2]).split(" - ")[0].strip() or title or "Unknown Model"


def scrape_catalog(
    *,
    series: list[str],
    output: Path,
    only_new: bool,
    existing_files: list[Path],
    new_ids_output: Path | None,
    limit_per_series: int | None,
    delay: tuple[float, float],
    workers: int,
    verbose: bool,
    ids: set[str] | None = None,
    provider: str = DEFAULT_PROVIDER,
    **kwargs: Any,
) -> dict[str, Any]:
    """Discover and scrape product listings, dispatching to the named provider."""
    return get_provider(provider).scrape_catalog(
        series=series,
        output=output,
        only_new=only_new,
        existing_files=existing_files,
        new_ids_output=new_ids_output,
        limit_per_series=limit_per_series,
        delay=delay,
        workers=workers,
        verbose=verbose,
        ids=ids,
        **kwargs,
    )


def format_catalog(
    *,
    input_path: Path,
    output_path: Path,
    history_dir: Path,
    cto_dir: Path,
    existing_data: Path | None,
    dry_run: bool = False,
    psref_dir: Path | None = None,
    psref_map: Path | None = None,
    provider: str = DEFAULT_PROVIDER,
    **kwargs: Any,
) -> dict[str, Any]:
    """Convert a raw catalog into the served data file, dispatching to the named provider."""
    return get_provider(provider).format_catalog(
        input_path=input_path,
        output_path=output_path,
        history_dir=history_dir,
        cto_dir=cto_dir,
        existing_data=existing_data,
        dry_run=dry_run,
        psref_dir=psref_dir,
        psref_map=psref_map,
        **kwargs,
    )
