#!/usr/bin/env python3
from __future__ import annotations

import argparse

from laptopdeals import inventory, maintenance, paths
from laptopdeals.jsonio import read_json, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Data and repository maintenance")
    sub = parser.add_subparsers(dest="command", required=True)

    inv = sub.add_parser("inventory", help="Export spec inventory")
    inv.add_argument("--input", default=str(paths.APP_DATA))
    inv.add_argument("--output", default=str(paths.SPEC_INVENTORY))

    cleanup = sub.add_parser("cleanup-history", help="Remove stale rapid catalog price points")
    cleanup.add_argument("--history-dir", default=str(paths.PRICE_HISTORY))
    cleanup.add_argument("--report", default=str(paths.PRICE_CLEANUP_REPORT))
    cleanup.add_argument("--max-gap-minutes", type=float, default=30.0)
    cleanup.add_argument("--min-cluster-size", type=int, default=5)
    cleanup.add_argument("--start-date", default="2026-04-26")
    cleanup.add_argument("--apply", action="store_true")

    compact = sub.add_parser("compact-commits", help="Compact contiguous automated price update commits")
    compact.add_argument("--branch", required=True)
    compact.add_argument("--write-ref", default="")
    compact.add_argument("--apply", action="store_true")

    args = parser.parse_args()
    if args.command == "inventory":
        result = inventory.build_spec_inventory(read_json(paths.resolve(args.input), {}))
        write_json(paths.resolve(args.output), result, indent=2)
    elif args.command == "cleanup-history":
        result = maintenance.cleanup_rapid_price_pairs(
            history_dir=paths.resolve(args.history_dir),
            report_path=paths.resolve(args.report),
            max_gap_minutes=args.max_gap_minutes,
            min_cluster_size=args.min_cluster_size,
            start_date=args.start_date,
            apply=args.apply,
        )
    else:
        result = maintenance.compact_price_update_commits(branch=args.branch, write_ref=args.write_ref, apply=args.apply)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

