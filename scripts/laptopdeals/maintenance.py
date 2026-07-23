from __future__ import annotations

import os
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
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
