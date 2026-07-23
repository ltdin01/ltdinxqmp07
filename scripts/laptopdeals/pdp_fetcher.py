from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .sources import lenovo


@dataclass
class PDPResult:
    url: str
    product_id: str = ""
    status_code: int = 200
    price: int | None = None
    mrp: int | None = None
    availability: str = "unknown"
    title: str = ""
    specs_rows: list[dict[str, Any]] = field(default_factory=list)
    specs_by_label: dict[str, str] = field(default_factory=dict)
    specs_by_code: dict[str, str] = field(default_factory=dict)
    json_ld: dict[str, Any] = field(default_factory=dict)
    breadcrumbs: list[dict[str, str]] = field(default_factory=list)
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PDPFetcher:
    """Unified single-pass fetcher for Lenovo PDP pages with response caching."""

    _instance: PDPFetcher | None = None
    _lock = threading.Lock()

    def __init__(self, cache_dir: Path | None = None):
        self._cache: dict[str, PDPResult] = {}
        self.cache_dir = cache_dir

    @classmethod
    def get_instance(cls) -> PDPFetcher:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def fetch(self, url: str, product_id: str = "", *, force_refresh: bool = False) -> PDPResult:
        cache_key = url.strip() or product_id.strip()
        if not cache_key:
            return PDPResult(url=url, product_id=product_id, status_code=400, availability="unknown")

        with self._lock:
            if not force_refresh and cache_key in self._cache:
                return self._cache[cache_key]

        req = lenovo.require_requests()
        try:
            resp = req.get(
                url,
                headers={
                    **lenovo.request_headers(lenovo.SITE_BASE),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
                impersonate="chrome120",
                timeout=30,
            )
            status_code = resp.status_code
            text = resp.text if status_code == 200 else ""
        except Exception:
            status_code = 500
            text = ""

        if status_code != 200:
            result = PDPResult(url=url, product_id=product_id, status_code=status_code, availability="unknown")
        else:
            availability = lenovo.availability_from_html(text)
            title = lenovo.product_page_title(text)
            specs_rows, specs_label, specs_code = lenovo.extract_detail_specs(text)
            json_ld = lenovo.product_ld(text)
            breadcrumbs = lenovo.breadcrumb_ld(text)

            price, mrp = None, None
            if product_id:
                try:
                    price, mrp = lenovo.fetch_current_price(product_id)
                except Exception:
                    pass

            result = PDPResult(
                url=url,
                product_id=product_id,
                status_code=200,
                price=price,
                mrp=mrp,
                availability=availability,
                title=title,
                specs_rows=specs_rows,
                specs_by_label=specs_label,
                specs_by_code=specs_code,
                json_ld=json_ld,
                breadcrumbs=breadcrumbs,
            )

        with self._lock:
            self._cache[cache_key] = result
        return result

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
