from __future__ import annotations

import re
import time
from typing import Any

from ..http import curl_requests


GRAPH_URL = "https://graph.bitbns.com/getPredictedData.php?pos=6046&pid={product_id}"
HEADERS = {
    "Accept": "*/*",
    "Referer": "https://graph.bitbns.com/",
    "Accept-Language": "en-US,en;q=0.9",
}


def parse_graph_response(raw_text: str) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for chunk in (raw_text or "").split("~*~*"):
        parts = chunk.split("~")
        if len(parts) < 2:
            continue
        date = parts[0].strip()
        price = parts[1].strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}", date) and price.isdigit():
            history.append({"date": date, "price": int(price)})
    history.sort(key=lambda item: item["date"])
    return history


def fetch_history(product_id: str, *, delay: float = 0.0, timeout: int = 20) -> list[dict[str, Any]]:
    requests = curl_requests()
    if delay > 0:
        time.sleep(delay)
    response = requests.get(
        GRAPH_URL.format(product_id=product_id.upper()),
        headers=HEADERS,
        impersonate="chrome120",
        timeout=timeout,
    )
    if response.status_code == 429:
        raise RuntimeError("BitBns rate limited request")
    response.raise_for_status()
    return parse_graph_response(response.text)
