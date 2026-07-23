from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .jsonio import read_json, write_json
from .timeutil import ist_now, parse_date


@dataclass(frozen=True)
class PricePoint:
    date: str
    price: int

    def as_dict(self) -> dict[str, Any]:
        return {"date": self.date, "price": self.price}


def parse_price(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(str(value).replace(",", "")))
        except (TypeError, ValueError):
            return None


def normalize_points(points: Iterable[dict[str, Any]]) -> list[PricePoint]:
    normalized: list[PricePoint] = []
    for item in points or []:
        if not isinstance(item, dict):
            continue
        date = str(item.get("date") or item.get("Date") or "").strip()
        price = parse_price(item.get("price", item.get("Price")))
        if date and price is not None and price > 0:
            normalized.append(PricePoint(date=date, price=price))
    normalized.sort(key=lambda point: (parse_date(point.date) or point.date, point.date))
    return normalized


def change_points(points: Iterable[PricePoint]) -> list[PricePoint]:
    result: list[PricePoint] = []
    last_price: int | None = None
    seen: set[tuple[str, int]] = set()
    for point in points:
        key = (point.date, point.price)
        if key in seen:
            continue
        seen.add(key)
        if last_price is None or point.price != last_price:
            result.append(point)
            last_price = point.price
    return result


def merge_points(existing: Iterable[dict[str, Any]], incoming: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    points = normalize_points([*(existing or []), *(incoming or [])])
    return [point.as_dict() for point in change_points(points)]


def replace_points(incoming: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [point.as_dict() for point in change_points(normalize_points(incoming))]


def load_history(history_dir: Path, product_id: str) -> list[dict[str, Any]]:
    path = history_dir / f"{product_id.upper()}.json"
    payload = read_json(path, [])
    return payload if isinstance(payload, list) else []


def write_history(history_dir: Path, product_id: str, history: list[dict[str, Any]]) -> None:
    write_json(history_dir / f"{product_id.upper()}.json", history, indent=4)


def apply_current_price(
    history_dir: Path,
    product_id: str,
    price: int,
    *,
    date: str,
    dry_run: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    current = load_history(history_dir, product_id)
    merged = merge_points(current, [{"date": date, "price": price}])
    changed = merged != replace_points(current)
    if changed and not dry_run:
        write_history(history_dir, product_id, merged)
    return merged, changed


def stats(history: list[dict[str, Any]]) -> dict[str, Any]:
    points = replace_points(history)
    if not points:
        return {"price_mean": 0, "price_median": 0, "price_usual": 0, "has_history": False}
    prices = [int(point["price"]) for point in points]
    return {
        "price_mean": int(statistics.mean(prices)),
        "price_median": int(statistics.median(prices)),
        "price_usual": time_weighted_mean(points),
        "has_history": True,
    }


def time_weighted_mean(history: list[dict[str, Any]]) -> int:
    points = normalize_points(history)
    if not points:
        return 0
    if len(points) == 1:
        return points[0].price
    total_days = 0
    weighted_sum = 0
    today = ist_now().replace(tzinfo=None)
    for index, point in enumerate(points):
        current_date = parse_date(point.date)
        if not current_date:
            continue
        if index + 1 < len(points):
            next_date = parse_date(points[index + 1].date) or current_date
            duration = (next_date - current_date).days
        else:
            duration = (today - current_date).days
        duration = max(1, duration)
        weighted_sum += point.price * duration
        total_days += duration
    return int(weighted_sum / total_days) if total_days else points[-1].price


def latest_price(history: list[dict[str, Any]]) -> int:
    points = replace_points(history)
    return int(points[-1]["price"]) if points else 0


def sync_product_stats(product: dict[str, Any], history: list[dict[str, Any]]) -> None:
    product.update(stats(history))
    current = latest_price(history)
    if current:
        product["price"] = f"{current}.00 INR"


def compress_dir(history_dir: Path, *, ids: set[str] | None = None, dry_run: bool = False) -> dict[str, Any]:
    changed: dict[str, dict[str, int]] = {}
    files = sorted(history_dir.glob("*.json"))
    for path in files:
        product_id = path.stem.upper()
        if ids and product_id not in ids:
            continue
        original = read_json(path, [])
        compressed = replace_points(original if isinstance(original, list) else [])
        if compressed != original:
            changed[product_id] = {"before": len(original), "after": len(compressed)}
            if not dry_run:
                write_json(path, compressed, indent=4)
    return {"files_changed": len(changed), "changed": changed}
