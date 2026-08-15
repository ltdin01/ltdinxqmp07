from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Provider(Protocol):
    """Adapter contract implemented by every catalog provider (lenovo, amazon, ...).

    Each provider owns its source-specific scraping, formatting, category mapping
    and price update logic. Entry points in ``laptopdeals/catalog.py`` and
    ``laptopdeals/pricing.py`` dispatch to the provider named by the caller and
    default to ``"lenovo"`` so existing behavior is unchanged.
    """

    name: str

    def scrape_catalog(
        self,
        *,
        output: Path,
        only_new: bool,
        existing_files: list[Path],
        new_ids_output: Path | None,
        limit_per_series: int | None,
        delay: tuple[float, float],
        workers: int,
        verbose: bool,
        ids: set[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Discover and scrape product listings into a raw catalog JSON."""
        ...

    def format_catalog(
        self,
        *,
        input_path: Path,
        output_path: Path,
        history_dir: Path,
        existing_data: Path | None,
        dry_run: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Convert a raw catalog into the served data file with normalized specs."""
        ...

    def category_from_product(self, product: dict[str, Any]) -> str:
        """Map a raw product to its display category name."""
        ...

    def update_prices(
        self,
        data: Any,
        *,
        history_dir: Path,
        ids: set[str] | None = None,
        workers: int = 4,
        dry_run: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Fetch current prices and append to price history."""
        ...
