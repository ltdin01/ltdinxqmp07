from __future__ import annotations

import os
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .history import parse_price, replace_points
from .jsonio import read_json, write_json
from .timeutil import parse_date


def cleanup_rapid_price_pairs(
    *,
    history_dir: Path,
    report_path: Path,
    max_gap_minutes: float,
    min_cluster_size: int,
    start_date: str,
    apply: bool,
) -> dict[str, Any]:
    start = parse_date(start_date) if start_date else None
    candidates = []
    clusters: Counter[str] = Counter()
    for path in sorted(history_dir.glob("*.json")):
        rows = read_json(path, [])
        if not isinstance(rows, list):
            continue
        for index, (current, nxt) in enumerate(zip(rows, rows[1:])):
            current_date = parse_date(current.get("date"))
            next_date = parse_date(nxt.get("date"))
            current_price = parse_price(current.get("price"))
            next_price = parse_price(nxt.get("price"))
            if not current_date or not next_date or current_price is None or next_price is None:
                continue
            if start and current_date < start:
                continue
            gap = (next_date - current_date).total_seconds() / 60
            if 0 <= gap <= max_gap_minutes and current_price != next_price:
                bucket = current_date.strftime("%Y-%m-%d %H:%M")
                item = {"product_id": path.stem.upper(), "index": index, "bucket": bucket, "gap_minutes": round(gap, 3), "remove": current, "keep_next": nxt}
                candidates.append(item)
                clusters[bucket] += 1

    removals = [item for item in candidates if clusters[item["bucket"]] >= min_cluster_size]
    changed: dict[str, int] = {}
    if apply:
        by_product: dict[str, set[int]] = defaultdict(set)
        for item in removals:
            by_product[item["product_id"]].add(item["index"])
        for product_id, indexes in by_product.items():
            path = history_dir / f"{product_id}.json"
            rows = read_json(path, [])
            cleaned = [entry for index, entry in enumerate(rows) if index not in indexes]
            cleaned = replace_points(cleaned)
            if cleaned != rows:
                write_json(path, cleaned, indent=4)
                changed[product_id] = len(rows) - len(cleaned)

    report = {
        "applied": apply,
        "candidate_count": len(candidates),
        "remove_count": len(removals),
        "changed_file_count": len(changed),
        "removed_by_product": dict(sorted(changed.items())),
        "selected_clusters": [{"bucket": bucket, "candidate_count": count} for bucket, count in clusters.most_common() if count >= min_cluster_size],
        "removed": removals,
    }
    write_json(report_path, report, indent=2)
    return report


def cleanup_coupon_dips(
    *,
    history_dir: Path,
    report_path: Path,
    max_revert_hours: float,
    min_ratio: float,
    start_date: str,
    apply: bool,
) -> dict[str, Any]:
    """Remove transient nightly-deal dips from price history.

    A coupon dip is a change-point run that drops below a price by at least
    ``min_ratio`` and fully reverts to that same price within ``max_revert_hours``
    — the nightly DOORBUSTERDEAL signature (e.g. 109491 -> 98542 -> 109491). The
    rule is purely structural and time-agnostic: it never looks at the wall clock.
    A trailing dip (no revert yet) is removed only when its price value has
    previously recurred as a reverted dip, so real price cuts are never dropped.
    """
    start = parse_date(start_date) if start_date else None
    max_revert = timedelta(hours=max_revert_hours)
    removals = []
    removed_dip_values: dict[str, set[int]] = {}

    for path in sorted(history_dir.glob("*.json")):
        rows = read_json(path, [])
        if not isinstance(rows, list):
            continue
        product_id = path.stem.upper()

        points: list[dict[str, Any]] = []
        last_price: int | None = None
        for orig_index, row in enumerate(rows):
            price = parse_price(row.get("price"))
            if price is None:
                continue
            if price == last_price:
                continue
            points.append(
                {
                    "orig_index": orig_index,
                    "date": parse_date(row.get("date")),
                    "price": price,
                    "raw": row,
                }
            )
            last_price = price
        if not points or any(point["date"] is None for point in points):
            continue
        if start:
            points = [point for point in points if point["date"] >= start]

        remove_indexes: set[int] = set()
        dip_values: set[int] = set()
        n = len(points)
        i = 0
        while i < n - 1:
            high = points[i]["price"]
            if points[i + 1]["price"] >= high:
                i += 1
                continue
            j = i + 1
            revert_index: int | None = None
            while j < n:
                current = points[j]["price"]
                if current == high:
                    if points[j]["date"] - points[i + 1]["date"] <= max_revert:
                        revert_index = j
                    break
                if current > high:
                    break
                j += 1
            if revert_index is not None:
                dip_min = min(points[k]["price"] for k in range(i + 1, revert_index))
                if high > 0 and (high - dip_min) / high >= min_ratio:
                    for k in range(i + 1, revert_index):
                        remove_indexes.add(points[k]["orig_index"])
                        dip_values.add(points[k]["price"])
                i = revert_index
            else:
                i += 1

        trailing = len(points) - 1
        if trailing > 0 and points[trailing]["orig_index"] not in remove_indexes:
            last = points[trailing]
            prev = points[trailing - 1]
            if last["price"] < prev["price"] and last["price"] in dip_values:
                remove_indexes.add(last["orig_index"])

        for orig_index in sorted(remove_indexes):
            raw = rows[orig_index]
            removals.append(
                {
                    "product_id": product_id,
                    "index": orig_index,
                    "date": str(raw.get("date") or ""),
                    "price": parse_price(raw.get("price")),
                }
            )
        if remove_indexes:
            removed_dip_values[product_id] = dip_values

    changed: dict[str, int] = {}
    if apply:
        by_product: dict[str, set[int]] = defaultdict(set)
        for item in removals:
            by_product[item["product_id"]].add(item["index"])
        for product_id, indexes in by_product.items():
            path = history_dir / f"{product_id}.json"
            rows = read_json(path, [])
            kept = [entry for index, entry in enumerate(rows) if index not in indexes]
            cleaned = replace_points(kept)
            if cleaned != rows:
                write_json(path, cleaned, indent=4)
                changed[product_id] = len(rows) - len(cleaned)

    report = {
        "applied": apply,
        "remove_count": len(removals),
        "changed_file_count": len(changed),
        "removed_by_product": dict(sorted(changed.items())),
        "products_with_removed_dips": sorted(removed_dip_values),
        "removed": removals,
    }
    write_json(report_path, report, indent=2)
    return report


PRICE_UPDATE_RE = re.compile(
    r"^Automated (?:price|data) update (?:\(prices & CTO configs\) )?- (?P<date>\d{4}-\d{2}-\d{2}) (?P<time>\d{2}:\d{2}:\d{2}) IST$"
)


@dataclass
class CommitInfo:
    sha: str
    tree: str
    subject: str
    body: str
    author_name: str
    author_email: str
    author_date: str
    committer_name: str
    committer_email: str
    committer_date: str

    @property
    def message(self) -> str:
        return f"{self.subject}\n\n{self.body}" if self.body else self.subject


def git(args: list[str], *, input_text: str | None = None, env: dict[str, str] | None = None, capture: bool = True) -> str:
    result = subprocess.run(["git", *args], input=input_text, text=True, check=True, capture_output=capture, env=env)
    return result.stdout.strip() if capture else ""


def read_commit(sha: str) -> CommitInfo:
    fmt = "%H%x00%T%x00%s%x00%b%x00%an%x00%ae%x00%aI%x00%cn%x00%ce%x00%cI"
    parts = git(["show", "-s", f"--format={fmt}", sha]).split("\x00")
    return CommitInfo(*parts)


def is_price_update(commit: CommitInfo) -> bool:
    return bool(PRICE_UPDATE_RE.match(commit.subject))


def compact_price_update_commits(*, branch: str, write_ref: str, apply: bool) -> dict[str, str]:
    shas = [line for line in git(["rev-list", "--first-parent", "--reverse", branch]).splitlines() if line]
    commits = [read_commit(sha) for sha in shas]
    groups: list[list[CommitInfo]] = []
    current: list[CommitInfo] = []
    for commit in commits:
        if is_price_update(commit):
            current.append(commit)
            continue
        if current:
            groups.append(current)
            current = []
        groups.append([commit])
    if current:
        groups.append(current)
    compactable = [group for group in groups if len(group) > 1 and all(is_price_update(commit) for commit in group)]
    print(f"Branch: {branch}")
    print(f"First-parent commits scanned: {len(commits)}")
    print(f"Compactable groups: {len(compactable)}")
    if not apply or not compactable:
        emit_output("rewritten", "false")
        emit_output("group_count", str(len(compactable)))
        return {"rewritten": "false", "group_count": str(len(compactable))}

    parent = ""
    for group in groups:
        if len(group) > 1 and all(is_price_update(commit) for commit in group):
            template = group[-1]
            first = PRICE_UPDATE_RE.match(group[0].subject).group("date")  # type: ignore[union-attr]
            last = PRICE_UPDATE_RE.match(group[-1].subject).group("date")  # type: ignore[union-attr]
            subject = f"Automated price updates - {first if first == last else first + ' to ' + last} IST"
            body = "Squashed sequential automated price update commits:\n\n" + "\n".join(f"- {commit.subject}" for commit in group)
            parent = create_commit(template.tree, parent, f"{subject}\n\n{body}", template)
        else:
            template = group[0]
            parent = create_commit(template.tree, parent, template.message, template)
    ref = write_ref or f"refs/heads/{re.sub(r'[^A-Za-z0-9._/-]+', '-', branch).strip('-')}-price-compact"
    git(["update-ref", ref, parent], capture=False)
    emit_output("rewritten", "true")
    emit_output("group_count", str(len(compactable)))
    emit_output("write_ref", ref)
    emit_output("new_head", parent)
    return {"rewritten": "true", "group_count": str(len(compactable)), "write_ref": ref, "new_head": parent}


def create_commit(tree: str, parent: str, message: str, template: CommitInfo) -> str:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": template.author_name,
            "GIT_AUTHOR_EMAIL": template.author_email,
            "GIT_AUTHOR_DATE": template.author_date,
            "GIT_COMMITTER_NAME": template.committer_name,
            "GIT_COMMITTER_EMAIL": template.committer_email,
            "GIT_COMMITTER_DATE": template.committer_date,
        }
    )
    args = ["commit-tree", tree]
    if parent:
        args.extend(["-p", parent])
    return git(args, input_text=message, env=env)


def emit_output(key: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"{key}={value}\n")
