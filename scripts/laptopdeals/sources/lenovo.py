from __future__ import annotations

import html
import json
import random
import re
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

from ..http import curl_requests


BASE_URL = "https://www.lenovo.com"
SITE_BASE = "https://www.lenovo.com/in/en"
OPENAPI_BASE = "https://openapi.lenovo.com/in/en"
RESULTS_URL = SITE_BASE + "/laptops/subseries-results/?visibleDatas=4376:{series}"


def get_results_url(series: str, *, encoded_colon: bool = False) -> str:
    colon = "%3A" if encoded_colon else ":"
    return f"{SITE_BASE}/laptops/subseries-results/?visibleDatas=4376{colon}{quote(series)}"


def result_url_variants(series: str) -> list[str]:
    return [get_results_url(series), get_results_url(series, encoded_colon=True)]


def toggle_query_colon(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url
    query = parsed.query
    if ":" in query:
        new_query = query.replace(":", "%3A")
    elif "%3A" in query:
        new_query = query.replace("%3A", ":")
    elif "%3a" in query:
        new_query = query.replace("%3a", ":")
    else:
        new_query = query
    return urlunparse(parsed._replace(query=new_query))


RESULTS_FACET_ID = "4376"
DEFAULT_SERIES = ["LOQ", "ThinkPad", "Legion", "IdeaPad", "ThinkBook", "Yoga"]
INIT_CONFIG_URL = OPENAPI_BASE + "/cto/init-config"
CVLIST_URL = OPENAPI_BASE + "/cvlist/cto/get"
CONFIG_PRICE_URL = OPENAPI_BASE + "/cto/config/price"
DEFAULT_PLANT = "COMPAL"
DEFAULT_VENDOR_ID = "1000314513"
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}

# Group-level product codes returned by the listing API look like LEN101Q0005.
# Real purchasable SKUs look like 83DV01AFIN or 21MW008YIN (start with digits).
GROUP_CODE_RE = re.compile(r'^LEN\d+[A-Z]\d+$', re.IGNORECASE)

# Mobile/tablet UAs — used for catalog page fetches (landing, results, subseries).
# Lenovo's CDN serves the DLP page config in the HTML for mobile clients.
MOBILE_USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; moto g power 5G) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/126.0.0.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
]

# Desktop UAs — used for product detail pages and XHR/API calls.
DESKTOP_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]

# Legacy alias — keep for any external references
USER_AGENTS = MOBILE_USER_AGENTS + DESKTOP_USER_AGENTS


def require_requests():
    return curl_requests()

VISIBLE_CATEGORIES = {
    "NBPROCESSOR": "Processor",
    "NBGRAPHICS": "Graphics Card",
    "NBDIMM_MEMORY": "Memory",
    "NBSTORAGE_SELECTION": "Solid State Drive",
    "NBDISPLAY": "Display",
    "NBPRELOAD_OS": "Operating System",
    "NBMICROSOFT_OFFICE": "Microsoft Office",
    "NBKEYBOARD": "Keyboard",
    "NBCOLOR": "Color",
    "NBPOWER_ADAPTER": "Power Adapter",
    "NBWARRANTY": "Warranty",
    "NBWIRELESS_LAN": "Wireless",
    "NBBATTERY": "Battery",
    "NBCAMERA": "Camera",
}


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_meta_content(text: str, name: str) -> str:
    match = re.search(
        rf"<meta[^>]+name=['\"]{re.escape(name)}['\"][^>]+content=['\"](.*?)['\"]",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return clean_text(match.group(1)) if match else ""


def product_page_title(text: str) -> str:
    patterns = [
        r"<meta[^>]+name=['\"]title['\"][^>]+content=['\"](.*?)['\"]",
        r"<meta[^>]+property=['\"]og:title['\"][^>]+content=['\"](.*?)['\"]",
        r"<meta[^>]+name=['\"]twitter:title['\"][^>]+content=['\"](.*?)['\"]",
        r"<title[^>]*>(.*?)</title>",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return clean_text(match.group(1))
    return ""


def normalize_availability(value: Any) -> str:
    text = clean_text(value).lower()
    if not text:
        return "unknown"
    if "instock" in text or text in {"available", "in stock", "instock"}:
        return "in stock"
    unavailable_terms = (
        "outofstock",
        "out of stock",
        "sold out",
        "unavailable",
        "not available",
        "temporarily unavailable",
        "available soon",
        "coming soon",
        "discontinued",
        "end of life",
        "eol",
    )
    if any(term in text for term in unavailable_terms):
        return "out of stock"
    return "unknown"


def absolute_url(url: str | None) -> str:
    if not url:
        return ""
    if url.startswith(("https://", "http://")):
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/in/en/"):
        return BASE_URL + url
    if url.startswith("/"):
        return SITE_BASE + url
    return url


def pick_user_agent(pool: list[str] = MOBILE_USER_AGENTS, *, avoid: str = "") -> str:
    candidates = [ua for ua in pool if ua != avoid] or pool
    return random.choice(candidates)


def page_request_headers(referer: str = SITE_BASE, *, user_agent: str = "") -> dict[str, str]:
    """Headers for fetching HTML catalog/results pages — uses mobile UAs with a
    browser-style Accept so Lenovo serves the full page rather than a JSON error."""
    return {
        "User-Agent": user_agent or pick_user_agent(MOBILE_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": referer,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
    }


def request_headers(referer: str = SITE_BASE, *, user_agent: str = "") -> dict[str, str]:
    """Headers for XHR/API calls — uses desktop UAs with application/json Accept."""
    return {
        "User-Agent": user_agent or pick_user_agent(DESKTOP_USER_AGENTS),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-IN,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": referer,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "X-Requested-With": "XMLHttpRequest",
    }


def extract_balanced_json_after(text: str, marker: str) -> dict[str, Any] | None:
    idx = text.find(marker)
    if idx < 0:
        return None
    start = text.find("{", idx)
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for pos in range(start, len(text)):
        char = text[pos]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : pos + 1])
                except json.JSONDecodeError:
                    return None
    return None


def extract_dlp_form_data(text: str) -> dict[str, Any]:
    marker = 'window["ofp-2c-mobile-new-dlp_'
    idx = text.find(marker)
    if idx < 0:
        return {}
    form_data = extract_balanced_json_after(text[idx:], '"formData"') or {}
    return form_data if form_data.get("facetId") else {}


def extract_result_form_data(text: str) -> dict[str, Any]:
    payload = extract_balanced_json_after(text, 'window["ofp-2c-mobile-new-dlp_') or {}
    form_data = (payload.get("data") or {}).get("formData") or {}
    if not form_data.get("facetId"):
        form_data = extract_dlp_form_data(text)
    return form_data if form_data.get("facetId") else {}


def result_url_candidates(text: str, series: str) -> list[str]:
    patterns = [
        r'href=["\'](https?://[^"\']*(?:/results/|/subseries-results/)[^"\']*)["\']',
        r'href=["\'](/[^"\']*(?:/results/|/subseries-results/)[^"\']*)["\']',
        r'(?:https?://www\.lenovo\.com)?(/in/en/[^"\'<> ]*(?:/results/|/subseries-results/)[^"\'<> ]*)',
    ]
    seen: set[str] = set()
    candidates: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            url = match if isinstance(match, str) else match[0]
            url = absolute_url(html.unescape(url))
            if "visibleDatas=" not in url or series.lower() not in url.lower() or url in seen:
                continue
            seen.add(url)
            candidates.append(url)
    return candidates


def json_ld_objects(text: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    pattern = re.compile(r"<script[^>]+type=['\"]application/ld\+json['\"][^>]*>(.*?)</script>", re.I | re.S)
    for raw in pattern.findall(text):
        try:
            parsed = json.loads(html.unescape(raw).strip())
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            objects.append(parsed)
        elif isinstance(parsed, list):
            objects.extend(item for item in parsed if isinstance(item, dict))
    return objects


def product_ld(text: str) -> dict[str, Any]:
    for obj in json_ld_objects(text):
        value = obj.get("@type")
        types = value if isinstance(value, list) else [value]
        if "Product" in [str(item) for item in types]:
            return obj
    return {}


def breadcrumb_ld(text: str) -> list[dict[str, str]]:
    for obj in json_ld_objects(text):
        value = obj.get("@type")
        types = value if isinstance(value, list) else [value]
        if "BreadcrumbList" not in [str(item) for item in types]:
            continue
        rows = []
        for item in sorted(obj.get("itemListElement", []), key=lambda entry: entry.get("position", 0)):
            name = clean_text(item.get("name"))
            if name:
                rows.append({"name": name, "url": item.get("item", "")})
        if rows:
            return rows
    return []


def availability_from_html(text: str) -> str:
    # Lenovo can leave generic/duplicated JSON-LD on PDPs; productstatus is the
    # SKU-level sellability signal, with JSON-LD only as a fallback.
    meta_status = normalize_availability(extract_meta_content(text, "productstatus"))
    if meta_status != "unknown":
        return meta_status
    ld = product_ld(text)
    offers = ld.get("offers") if isinstance(ld, dict) else {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    return normalize_availability((offers or {}).get("availability"))


def fetch_current_price(product_id: str) -> tuple[int | None, int | None]:
    req = require_requests()
    url = f"{OPENAPI_BASE}/detail/price/batch/get?preSelect=1&mcode={product_id}&configId=&enteredCode="
    response = req.get(url, headers=request_headers(SITE_BASE), impersonate="chrome120", timeout=30)
    response.raise_for_status()
    payload = response.json()
    product_data = (payload.get("data") or {}).get(product_id)
    if payload.get("msg") != "ok" or not isinstance(product_data, list):
        return None, None
    price = int(product_data[4]) if len(product_data) > 4 and str(product_data[4]).isdigit() else None
    mrp = None
    if len(product_data) > 13 and isinstance(product_data[13], list) and len(product_data[13]) > 3:
        raw_mrp = product_data[13][3]
        mrp = int(raw_mrp) if str(raw_mrp).isdigit() else None
    return price, mrp


def fetch_page_availability(store_link: str) -> str:
    req = require_requests()
    if not store_link:
        return "unknown"
    response = req.get(
        store_link,
        headers={**request_headers(SITE_BASE), "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        impersonate="chrome120",
        timeout=30,
    )
    if response.status_code in {403, 429}:
        raise RuntimeError(f"Lenovo page blocked: HTTP {response.status_code}")
    return availability_from_html(response.text) if response.status_code == 200 else "unknown"


def spec_list_to_maps(specs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    rows: list[dict[str, Any]] = []
    by_label: dict[str, str] = OrderedDict()
    by_code: dict[str, str] = OrderedDict()
    for row in specs or []:
        label = clean_text(row.get("a") or row.get("label") or row.get("name"))
        value = clean_text(row.get("b") or row.get("value"))
        code = clean_text(row.get("code"))
        if not value:
            continue
        rows.append({"label": label, "value": value, "code": code or None})
        if label:
            by_label.setdefault(label, value)
        if code:
            by_code.setdefault(code, value)
    return rows, by_label, by_code


def merge_specs(*sources: tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]):
    rows: list[dict[str, Any]] = []
    labels: dict[str, str] = OrderedDict()
    codes: dict[str, str] = OrderedDict()
    seen: set[tuple[str, str, str]] = set()
    for source_rows, source_labels, source_codes in sources:
        for row in source_rows:
            label = clean_text(row.get("label"))
            value = clean_text(row.get("value"))
            code = clean_text(row.get("code"))
            key = (label.lower(), value, code)
            if value and key not in seen:
                rows.append({"label": label, "value": value, "code": code or None})
                seen.add(key)
        for label, value in source_labels.items():
            labels.setdefault(label, value)
        for code, value in source_codes.items():
            codes.setdefault(code, value)
    return rows, labels, codes


def extract_detail_specs(text: str) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    rows: list[dict[str, Any]] = []
    pattern = re.compile(r'"a":"((?:\\.|[^"\\])*)","b":"((?:\\.|[^"\\])*)","code":"((?:LOIS|MNL)_SCA_[A-Z0-9_]+)"')
    for label, value, code in pattern.findall(text):
        try:
            label = json.loads(f'"{label}"')
            value = json.loads(f'"{value}"')
        except json.JSONDecodeError:
            pass
        rows.append({"label": clean_text(label), "value": clean_text(value), "code": code})
    return spec_list_to_maps(rows)


def listing_date_from_images(images: Any) -> str:
    if isinstance(images, str):
        images = [images]
    dates = []
    for image in images or []:
        match = re.search(r"/(\d{4})/(\d{1,2})/(\d{1,2})/", str(image))
        if match:
            year, month, day = match.groups()
            dates.append(f"{int(year):04d}-{int(month):02d}-{int(day):02d}")
    return min(dates) if dates else ""


def build_tech_specs(by_label: dict[str, str], by_code: dict[str, str]) -> dict[str, str]:
    def first(*needles: str) -> str:
        for code in needles:
            if code in by_code:
                return by_code[code]
        for label, value in by_label.items():
            lower = label.lower()
            if any(needle.lower() in lower for needle in needles):
                return value
        return ""

    return OrderedDict(
        [
            ("processor", first("LOIS_SCA_CPU", "processor", "cpu")),
            ("operating_system", first("LOIS_SCA_OPSYS", "operating system")),
            ("graphics", first("LOIS_SCA_VIDEO", "graphics", "graphic")),
            ("memory", first("LOIS_SCA_MEM", "memory", "ram")),
            ("storage", first("LOIS_SCA_HDD", "storage", "ssd")),
            ("display", first("LOIS_SCA_DPY", "display")),
            ("wireless", first("LOIS_SCA_WIFI", "wireless", "wifi")),
            ("power", first("LOIS_SCA_POWERSUPP", "power", "adapter")),
            ("warranty", first("LOIS_SCA_WARRPERIOD", "warranty")),
            ("keyboard", first("LOIS_SCA_KEYBOARD", "keyboard")),
        ]
    )


class LenovoCatalogClient:
    def __init__(self, *, delay: tuple[float, float] = (0.8, 2.2), verbose: bool = False):
        self.delay = delay
        self.verbose = verbose
        self.session = require_requests().Session(impersonate="chrome120")
        if hasattr(self.session, "cookies"):
            self.session.cookies.set("user_country", "IN", domain=".lenovo.com")
        self.result_urls: dict[str, str] = {}

    def sleep(self) -> None:
        time.sleep(random.uniform(*self.delay))

    def reset_session(self) -> None:
        self.session = require_requests().Session(impersonate="chrome120")
        if hasattr(self.session, "cookies"):
            self.session.cookies.set("user_country", "IN", domain=".lenovo.com")

    def get_text(self, url: str, referer: str | None = None, *, attempts: int = 4, page: bool = False) -> str:
        """Fetch a URL as text.

        page=True  — HTML catalog/results pages: uses a plain curl request (no
                     TLS impersonation) with a mobile UA and browser Accept header.
                     impersonate=chrome120 causes Lenovo's CDN to return the 86-byte
                     JSON error instead of the full HTML page.
        page=False — XHR/API calls: uses the impersonated chrome120 session with a
                     desktop UA and application/json Accept header.
        """
        last_response = None
        user_agent = ""
        pool = MOBILE_USER_AGENTS if page else DESKTOP_USER_AGENTS
        for attempt in range(1, max(1, attempts) + 1):
            user_agent = pick_user_agent(pool, avoid=user_agent)
            if page:
                # Plain request — no impersonate — so Lenovo serves full HTML
                cookies = dict(self.session.cookies) if hasattr(self.session, "cookies") else {}
                cookies.setdefault("user_country", "IN")
                response = require_requests().get(
                    url,
                    headers=page_request_headers(referer or SITE_BASE, user_agent=user_agent),
                    cookies=cookies,
                    timeout=45,
                )
            else:
                response = self.session.get(
                    url,
                    headers=request_headers(referer or SITE_BASE, user_agent=user_agent),
                    timeout=45,
                )
            last_response = response
            if response.status_code not in RETRY_STATUS_CODES:
                response.raise_for_status()
                response_url = getattr(response, "url", url)
                if "/in/en/" not in response_url:
                    print(f"[lenovo-redirect-warn] Request was redirected from {url} to {response_url}")
                return response.text
            if attempt >= attempts:
                break
            wait = min(20.0, 2.0 ** (attempt - 1)) + random.uniform(0.0, 0.75)
            print(f"[lenovo-retry] HTTP {response.status_code} attempt={attempt}/{attempts} wait={wait:.1f}s url={url}")
            time.sleep(wait)
            if not page:
                self.reset_session()
        if last_response is not None:
            last_response.raise_for_status()
        raise RuntimeError(f"Lenovo request failed before response: {url}")

    def result_config(self, series: str) -> tuple[str, dict[str, Any]]:
        for url in result_url_variants(series):
            text = self.get_text(url, url, page=True)
            form_data = extract_result_form_data(text)
            if form_data.get("facetId"):
                self.result_urls[series] = url
                return url, form_data
            print(f"[catalog-debug] URL missing config for {series}: {url}")
            print(f"[catalog-debug] Response length: {len(text)}, Content: {text[:300]}")

        print(f"[catalog] Failed to get config for {series} via default URL variants. Attempting live discovery...")
        discovered = self.discover_result_config(series)
        if discovered:
            url, form_data = discovered
            print(f"[catalog] Dynamically discovered working URL for {series}: {url}")
                    
        if not form_data.get("facetId"):
            raise RuntimeError(f"Could not find Lenovo page filter id for {series}")
        self.result_urls[series] = url
        return url, form_data

    def discover_results_url(self, series: str) -> str | None:
        discovered = self.discover_result_config(series)
        return discovered[0] if discovered else None

    def discover_result_config(self, series: str) -> tuple[str, dict[str, Any]] | None:
        try:
            laptops_url = f"{SITE_BASE}/laptops/"
            print(f"[catalog-discover] Searching Lenovo navigation for {series}: {laptops_url}")
            text = self.get_text(laptops_url, laptops_url, page=True)
            candidates = result_url_candidates(text, series)
            for candidate in candidates:
                print(f"[catalog-discover] Found candidate URL for {series}: {candidate}")
            if not candidates:
                print(f"[catalog-discover] No candidate result URLs found for {series} on {laptops_url}")
                    
            for base_url in candidates:
                to_try = []
                to_try.append(base_url)
                to_try.append(toggle_query_colon(base_url))
                    
                seen_urls = set()
                unique_to_try = []
                for u in to_try:
                    if u not in seen_urls:
                        seen_urls.add(u)
                        unique_to_try.append(u)
                        
                for url in unique_to_try:
                    try:
                        print(f"[catalog-discover] Trying URL for {series}: {url}")
                        res_text = self.get_text(url, url, page=True)
                        form_data = extract_result_form_data(res_text)
                        if form_data.get("facetId"):
                            return url, form_data
                        print(
                            f"[catalog-discover] URL missing config for {series}: "
                            f"length={len(res_text)} content={res_text[:160]}"
                        )
                    except Exception as exc:
                        print(f"[catalog-discover] URL failed for {series}: {url} error={exc}")
                        continue
        except Exception as exc:
            print(f"[catalog-discover] Error discovering URL for {series}: {exc}")
        return None

    def result_page(self, series: str, form_data: dict[str, Any], page: int) -> dict[str, Any]:
        page_filter_id = form_data["facetId"]
        params = {
            "classificationGroupIds": "400001",
            "pageFilterId": page_filter_id,
            "facets": [{"facetId": RESULTS_FACET_ID, "selectedValues": series}],
            "page": str(page),
            "pageSize": str(form_data.get("pageSize") or "20"),
            "sorts": [form_data.get("defaultSort") or "bestSelling"],
            "version": "v2",
            "enablePreselect": form_data.get("enablePreselect"),
        }
        encoded = quote(quote(json.dumps({k: v for k, v in params.items() if v is not None}, separators=(",", ":"))))
        url = f"{OPENAPI_BASE}/ofp/search/dlp/product/query/get/_tsc?pageFilterId={page_filter_id}&subseriesCode=&loyalty=false&params={encoded}"
        payload = json.loads(self.get_text(url, get_results_url(series)))
        if payload.get("status") != 200:
            raise RuntimeError(f"Lenovo DLP query failed for {series} page {page}: {payload.get('msg')}")
        return payload["data"]

    def extract_group_skus(self, group_url: str) -> list[str]:
        """Fetch a group page and extract individual purchasable SKUs from its meta tags,
        JSON scripts, or page content.
        """
        try:
            text = self.get_text(group_url, group_url, page=True)
        except Exception as exc:
            print(f"[catalog-group] Failed to fetch group page {group_url}: {exc}")
            return []

        skus: list[str] = []

        def add_sku(code_str: str):
            for candidate in re.split(r"[,;\s]+", code_str or ""):
                candidate = candidate.strip().upper()
                if candidate and not GROUP_CODE_RE.match(candidate) and candidate not in skus:
                    if candidate.isdigit() or candidate.startswith("3DLEN") or re.match(r"^\d+X\d+$", candidate) or not any(c.isalpha() for c in candidate):
                        continue
                    if re.match(r"^\d[0-9A-Z]{7,13}$", candidate):
                        if "CTO" in candidate and not re.search(r"IN\d*$", candidate):
                            continue
                        skus.append(candidate)

        # 1. Search meta tags with flexible attribute order (name before content & content before name)
        for meta_name in ("productname", "productcodeimpressions", "productcode", "productid", "jsonld_sku", "jsonld_mpn"):
            for match in re.finditer(rf'<meta[^>]*name=["\']{meta_name}["\'][^>]*content=["\']([^"\']+)["\']', text, re.IGNORECASE):
                add_sku(match.group(1))
            for match in re.finditer(rf'<meta[^>]*content=["\']([^"\']+)["\'][^>]*name=["\']{meta_name}["\']', text, re.IGNORECASE):
                add_sku(match.group(1))

        # 2. Search JSON attributes in scripts
        for match in re.finditer(r'"(?:partNumber|productCode|productNumber|sku|jsonld_sku|jsonld_mpn|pdp_product_number)":\s*"([0-9A-Z]{8,12})"', text, re.IGNORECASE):
            add_sku(match.group(1))

        return skus

    def expand_group_card(self, card: dict[str, Any]) -> list[dict[str, Any]]:
        """If a listing card has a group code instead of a real SKU, expand it into
        individual SKU cards by fetching the group page and reading its meta tags.

        Each individual card is a copy of the group card with productCode and url
        replaced so that build_product fetches the correct PDP.
        """
        code = clean_text(card.get("productCode") or card.get("productNumber") or "")
        if not GROUP_CODE_RE.match(code):
            return [card]  # already a real SKU card

        group_url = absolute_url(card.get("url") or "")
        if not group_url:
            return []

        skus = self.extract_group_skus(group_url)
        if not skus:
            print(f"[catalog-group] Discarding group card {code} — no individual SKUs found")
            return []

        print(f"[catalog-group] Expanded {code} -> {skus}")
        expanded: list[dict[str, Any]] = []
        for sku in skus:
            # Individual SKU URL = group URL with the group code replaced by the SKU
            sku_url = re.sub(re.escape(code), sku, group_url, flags=re.IGNORECASE)
            sku_card = dict(card)
            sku_card["productCode"] = sku
            sku_card["url"] = sku_url
            expanded.append(sku_card)
        return expanded

    def listing_products(self, series: str, limit: int | None = None) -> list[dict[str, Any]]:
        _, form_data = self.result_config(series)
        products: list[dict[str, Any]] = []
        page = 1
        page_count = 1
        while page <= page_count:
            if page > 1:
                self.sleep()
            payload = self.result_page(series, form_data, page)
            page_count = int(payload.get("pageCount") or page_count)
            for group in payload.get("data") or []:
                for card in group.get("products") or []:
                    for expanded_card in self.expand_group_card(card):
                        products.append(expanded_card)
                        if limit and len(products) >= limit:
                            return products[:limit]
            page += 1
        return products

    def detail(self, url: str):
        self.sleep()
        response = self.session.get(url, headers=request_headers(url), timeout=45)
        response.raise_for_status()
        text = response.text
        return breadcrumb_ld(text), product_ld(text), extract_detail_specs(text), product_page_title(text)
