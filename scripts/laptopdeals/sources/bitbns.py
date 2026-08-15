from __future__ import annotations

import json
import re
import time
import urllib.parse
from typing import Any

from ..http import curl_requests

PREDICTED_URL = "https://graph.bitbns.com/getPredictedData.php?pos={pos}&pid={product_id}"
SEARCH_URL = "https://graph.bitbns.com/searchTest5.php?pid={product_id}&pos={pos}"
DROP_DETAILS_URL = "https://graph.bitbns.com/extAPIs/getDropDetails"

HEADERS = {
    "Accept": "*/*",
    "Referer": "https://graph.bitbns.com/",
    "Origin": "https://graph.bitbns.com",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def parse_graph_response(raw_text: str) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    if not raw_text or not raw_text.strip():
        return history

    # 1. Try JSON response format (getDropDetails / API endpoints)
    try:
        data = json.loads(raw_text)
        if isinstance(data, dict):
            pts = data.get("data") or data.get("points") or data.get("price_graph") or data.get("result") or []
            if isinstance(pts, list):
                for p in pts:
                    if isinstance(p, dict) and "date" in p and "price" in p:
                        date_str = str(p["date"])[:10]
                        try:
                            price_num = int(float(p["price"]))
                            if re.match(r"^\d{4}-\d{2}-\d{2}", date_str) and price_num > 0:
                                history.append({"date": date_str, "price": price_num})
                        except (ValueError, TypeError):
                            continue
                    elif isinstance(p, (list, tuple)) and len(p) >= 2:
                        date_str = str(p[0])[:10]
                        try:
                            price_num = int(float(p[1]))
                            if re.match(r"^\d{4}-\d{2}-\d{2}", date_str) and price_num > 0:
                                history.append({"date": date_str, "price": price_num})
                        except (ValueError, TypeError):
                            continue
                if history:
                    history.sort(key=lambda item: item["date"])
                    return history
    except Exception:
        pass

    # 2. Try standard tilde-delimited format: date~price~*~*date~price...
    for chunk in raw_text.split("~*~*"):
        parts = chunk.split("~")
        if len(parts) < 2:
            continue
        date = parts[0].strip()
        price = parts[1].strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}", date) and price.isdigit():
            history.append({"date": date, "price": int(price)})

    history.sort(key=lambda item: item["date"])
    return history


def fetch_history(
    product_id_or_url: str,
    *,
    pos: str = "6046",
    delay: float = 0.0,
    timeout: int = 20,
    retries: int = 2,
) -> list[dict[str, Any]]:
    """Fetch time-series price history from BitBns/Buyhatke backend.

    Supports querying by uppercase SKU/PID or full product store URL, with
    multiple endpoint fallbacks (getPredictedData, searchTest5, getDropDetails)
    and traffic masking via curl-impersonate.
    """
    if not product_id_or_url:
        return []

    target = product_id_or_url.strip()
    is_url = target.startswith("http://") or target.startswith("https://")
    pid = target if not is_url else urllib.parse.quote(target, safe="")

    session = curl_requests().Session(impersonate="chrome120")
    if delay > 0:
        time.sleep(delay)

    urls_to_try = [
        PREDICTED_URL.format(pos=pos, product_id=pid if not is_url else urllib.parse.quote(target, safe="")),
        SEARCH_URL.format(pos=pos, product_id=pid if not is_url else urllib.parse.quote(target, safe="")),
    ]

    last_error: Exception | None = None
    for url in urls_to_try:
        for attempt in range(retries + 1):
            try:
                resp = session.get(url, headers=HEADERS, timeout=timeout)
                if resp.status_code == 200:
                    points = parse_graph_response(resp.text)
                    if points:
                        return points
                elif resp.status_code == 429:
                    time.sleep(1.0 * (attempt + 1))
            except Exception as exc:
                last_error = exc
                time.sleep(0.5 * (attempt + 1))

    # Fallback to POST getDropDetails
    try:
        payload = {"url": target} if is_url else {"pid": target.upper(), "pos": pos}
        post_headers = dict(HEADERS)
        post_headers["Content-Type"] = "application/json"
        resp = session.post(DROP_DETAILS_URL, json=payload, headers=post_headers, timeout=timeout)
        if resp.status_code == 200:
            points = parse_graph_response(resp.text)
            if points:
                return points
    except Exception as exc:
        last_error = exc

    return []
