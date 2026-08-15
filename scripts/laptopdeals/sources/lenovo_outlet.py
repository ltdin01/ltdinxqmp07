from __future__ import annotations

import json
import random
import re
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import quote

from ..http import curl_requests
from . import lenovo as base_lenovo


OUTLET_BASE_URL = "https://www.lenovo.com"
OUTLET_SITE_BASE = "https://www.lenovo.com/in/outletin/en"
OUTLET_OPENAPI_BASE = "https://openapi.lenovo.com/in/outletin/en"
OUTLET_DLP_PAGE_FILTER_ID = "afdcd3f7-d8e6-4e9e-a76a-d6060dc75ae9"
OUTLET_DLP_GROUP_ID = "400001"
AFFILIATE_BASE = "https://lenovo-in.zlvv.net/c/5890822/608695/9634"


def absolute_outlet_url(url: str | None) -> str:
    if not url:
        return ""
    if url.startswith(("https://", "http://")):
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/in/outletin/en/"):
        return OUTLET_BASE_URL + url
    if url.startswith("/"):
        return OUTLET_SITE_BASE + url
    return url


def build_affiliate_link(store_link: str, sku: str) -> str:
    if not store_link or not sku:
        return ""
    full_url = absolute_outlet_url(store_link)
    return f"{AFFILIATE_BASE}?prodsku={sku}&u={quote(full_url, safe='')}&intsrc=CATF_4639"


class LenovoOutletClient:
    def __init__(self, *, delay: tuple[float, float] = (0.5, 1.5), verbose: bool = False):
        self.delay = delay
        self.verbose = verbose
        self.session = curl_requests().Session(impersonate="chrome120")
        if hasattr(self.session, "cookies"):
            self.session.cookies.set("user_country", "IN", domain=".lenovo.com")

    def sleep(self) -> None:
        time.sleep(random.uniform(*self.delay))

    def reset_session(self) -> None:
        self.session = curl_requests().Session(impersonate="chrome120")
        if hasattr(self.session, "cookies"):
            self.session.cookies.set("user_country", "IN", domain=".lenovo.com")

    def fetch_all_laptops(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Fetch all laptop items from the Lenovo Outlet DLP search API."""
        page = 1
        page_count = 1
        laptops: list[dict[str, Any]] = []

        while page <= page_count:
            if page > 1:
                self.sleep()
            params = {
                "classificationGroupIds": OUTLET_DLP_GROUP_ID,
                "pageFilterId": OUTLET_DLP_PAGE_FILTER_ID,
                "facets": [],
                "page": str(page),
                "pageSize": 30,
                "groupCode": "",
                "init": True if page == 1 else False,
                "sorts": ["priceUp"],
                "version": "v2",
                "enablePreselect": True,
                "subseriesCode": "",
            }
            encoded = quote(quote(json.dumps(params, separators=(",", ":"))))
            url = f"{OUTLET_OPENAPI_BASE}/ofp/search/dlp/product/query/get/_tsc?pageFilterId={OUTLET_DLP_PAGE_FILTER_ID}&subSeriesCode=&loyalty=false&params={encoded}"
            
            headers = base_lenovo.request_headers(OUTLET_SITE_BASE)
            response = self.session.get(url, headers=headers, timeout=45)
            response.raise_for_status()
            res_json = response.json()

            data_block = res_json.get("data", {})
            page_count = int(data_block.get("pageCount") or page_count)
            groups = data_block.get("data", [])

            for group in groups:
                for product in group.get("products", []):
                    product_url = str(product.get("url") or "")
                    fl_code = str(product.get("flCode") or "")
                    # Strictly filter for laptops
                    if "/p/laptops/" in product_url or fl_code == "laptops":
                        laptops.append(product)
                        if limit and len(laptops) >= limit:
                            return laptops[:limit]
            page += 1

        if self.verbose:
            print(f"[lenovo-outlet] Fetched {len(laptops)} laptops across {page_count} pages")
        return laptops

    def fetch_batch_inventory(self, skus: list[str], *, chunk_size: int = 25) -> dict[str, str]:
        """Fetch stock availability in bulk using the UPS Delivery/Inventory Proxy API.
        
        inventoryStatus == 1 -> 'in stock'
        inventoryStatus == 2 -> 'out of stock'
        """
        results: dict[str, str] = {}
        unique_skus = [s for s in dict.fromkeys(skus) if s]

        for i in range(0, len(unique_skus), chunk_size):
            chunk = unique_skus[i : i + chunk_size]
            if i > 0:
                self.sleep()
            
            product_list = [
                {
                    "guid": sku,
                    "qty": 1,
                    "productCode": sku,
                    "productTypeCode": "0",
                    "outlet": True,
                }
                for sku in chunk
            ]
            batch_param = [
                {
                    "uri": "/api/ups/getDeliveryDate",
                    "key": "batchapiups",
                    "msg": "",
                    "body": "body",
                    "param": {
                        "inventoryCheck": True,
                        "productList": product_list,
                        "zipCode": "",
                        "source": "dlp",
                        "city": "",
                        "timezone": "Asia/Kolkata",
                    },
                }
            ]
            encoded = quote(json.dumps(batch_param, separators=(",", ":")))
            url = f"{OUTLET_OPENAPI_BASE}/proxy/batch/get_cdn?params={encoded}"
            headers = base_lenovo.request_headers(OUTLET_SITE_BASE)

            try:
                resp = self.session.get(url, headers=headers, timeout=30)
                if resp.status_code == 200:
                    payload = resp.json()
                    data_entries = payload.get("data", [])
                    if data_entries:
                        items = (data_entries[0].get("data") or {}).get("deliveryDateItemList", [])
                        for it in items:
                            guid = it.get("guid")
                            if guid:
                                inv_status = it.get("inventoryStatus")
                                results[guid] = "in stock" if inv_status == 1 else "out of stock"
            except Exception as exc:
                if self.verbose:
                    print(f"[lenovo-outlet-batch-inv-err] Chunk {chunk}: {exc}")

        return results

    def fetch_batch_prices(self, skus: list[str], *, chunk_size: int = 25) -> dict[str, tuple[int | None, int | None]]:
        """Fetch current prices & MRPs in bulk using the price batch API."""
        results: dict[str, tuple[int | None, int | None]] = {}
        unique_skus = [s for s in dict.fromkeys(skus) if s]

        for i in range(0, len(unique_skus), chunk_size):
            chunk = unique_skus[i : i + chunk_size]
            if i > 0:
                self.sleep()
            
            mcode_str = ",".join(chunk)
            url = f"{OUTLET_OPENAPI_BASE}/detail/price/batch/get?preSelect=1&mcode={mcode_str}&configId=&enteredCode="
            headers = base_lenovo.request_headers(OUTLET_SITE_BASE)

            try:
                resp = self.session.get(url, headers=headers, timeout=30)
                if resp.status_code == 200:
                    payload = resp.json()
                    data_dict = payload.get("data", {})
                    for sku in chunk:
                        product_data = data_dict.get(sku)
                        if isinstance(product_data, list):
                            price = int(product_data[4]) if len(product_data) > 4 and str(product_data[4]).isdigit() else None
                            mrp = None
                            if len(product_data) > 6 and str(product_data[6]).isdigit():
                                mrp = int(product_data[6])
                            results[sku] = (price, mrp)
            except Exception as exc:
                if self.verbose:
                    print(f"[lenovo-outlet-batch-price-err] Chunk {chunk}: {exc}")

        return results

    def fetch_pdp_availability(self, url: str) -> str:
        """Secondary fallback: fetch PDP and inspect meta tags."""
        if not url:
            return "unknown"
        headers = base_lenovo.request_headers(OUTLET_SITE_BASE)
        try:
            resp = self.session.get(absolute_outlet_url(url), headers=headers, timeout=30)
            if resp.status_code == 200:
                m_status = re.search(r'<meta[^>]+name=[\"\']productstatus[\"\'][^>]+content=[\"\']([^\"\']+)[\"\']', resp.text, re.I)
                m_inv = re.search(r'<meta[^>]+name=[\"\']inventory[\"\'][^>]+content=[\"\']([^\"\']+)[\"\']', resp.text, re.I)
                status_text = m_status.group(1).lower() if m_status else ""
                inv_text = m_inv.group(1).strip() if m_inv else ""
                if status_text in ("available", "in stock", "instock") and inv_text != "0":
                    return "in stock"
                if "unavailable" in status_text or "out of stock" in status_text or inv_text == "0":
                    return "out of stock"
        except Exception:
            pass
        return "unknown"

    def fetch_cart_availability(self, sku: str) -> str:
        """Tertiary fallback: probe the cart item endpoint."""
        if not sku:
            return "unknown"
        url = f"{OUTLET_OPENAPI_BASE}/api/cart/item?productSource=dealspage&qty=1&productCode={sku}"
        headers = base_lenovo.request_headers(OUTLET_SITE_BASE)
        try:
            resp = self.session.get(url, headers=headers, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == 200 and data.get("data"):
                    return "in stock"
                if data.get("status") == 500:
                    return "out of stock"
        except Exception:
            pass
        return "unknown"

    def get_availability(self, sku: str, store_link: str, preloaded_batch: dict[str, str] | None = None) -> str:
        """Evaluate stock availability using the multi-tier hierarchy:
        1. Preloaded batch UPS inventory status
        2. Live batch UPS inventory query
        3. Secondary fallback: PDP HTML meta tags
        4. Tertiary fallback: Cart item API
        """
        if preloaded_batch and sku in preloaded_batch:
            return preloaded_batch[sku]

        # 1. Live batch UPS inventory query
        batch_res = self.fetch_batch_inventory([sku])
        if sku in batch_res:
            return batch_res[sku]

        # 2. Secondary PDP fallback
        pdp_avail = self.fetch_pdp_availability(store_link)
        if pdp_avail != "unknown":
            return pdp_avail

        # 3. Tertiary Cart item API probe
        cart_avail = self.fetch_cart_availability(sku)
        if cart_avail != "unknown":
            return cart_avail

        return "unknown"


def classification_to_spec_maps(classification: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    """Convert Lenovo Outlet classification array into standard specs_by_label and specs_by_code."""
    rows: list[dict[str, Any]] = []
    by_label: dict[str, str] = OrderedDict()
    by_code: dict[str, str] = OrderedDict()

    # Map classification label to LOIS code where applicable
    label_to_code = {
        "Processor": "LOIS_SCA_CPU",
        "Operating System": "LOIS_SCA_OPSYS",
        "Graphic Card": "LOIS_SCA_VIDEO",
        "Graphics": "LOIS_SCA_VIDEO",
        "Memory": "LOIS_SCA_MEM",
        "Storage": "LOIS_SCA_HDD",
        "Solid State Drive": "LOIS_SCA_HDD",
        "Display": "LOIS_SCA_DPY",
        "Warranty": "LOIS_SCA_WARRPERIOD",
        "Keyboard": "LOIS_SCA_KEYBOARD",
        "Wireless": "LOIS_SCA_WIFI",
        "Power Adapter": "LOIS_SCA_POWERSUPP",
        "Battery": "LOIS_SCA_BATTERY",
        "Camera": "LOIS_SCA_CAMERA",
    }

    for item in classification or []:
        label = base_lenovo.clean_text(item.get("a") or item.get("label") or item.get("name"))
        value = base_lenovo.clean_text(item.get("b") or item.get("value"))
        code = base_lenovo.clean_text(item.get("code")) or label_to_code.get(label)
        if not value:
            continue
        rows.append({"label": label, "value": value, "code": code or None})
        if label:
            by_label.setdefault(label, value)
        if code:
            by_code.setdefault(code, value)

    return rows, by_label, by_code
