#!/usr/bin/env python3
from __future__ import annotations

import argparse

from laptopdeals import history, paths, pricing
from laptopdeals.ids import ids_from_args
from laptopdeals.jsonio import read_json, write_json


def add_targets(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--id", action="append", default=[], help="Target product ID(s). Comma/space separated; repeatable.")
    parser.add_argument("--ids-file", default="", help="JSON list or {'ids': [...]} file.")


def main() -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data", default=str(paths.APP_DATA), help="App data JSON")
    common.add_argument("--history-dir", default=str(paths.PRICE_HISTORY), help="Price history directory")

    parser = argparse.ArgumentParser(description="Price history and live price operations", parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)

    lenovo_parser = sub.add_parser("lenovo-current", parents=[common], help="Fetch current Lenovo price and append only changed price points")
    add_targets(lenovo_parser)
    lenovo_parser.add_argument("--workers", type=int, default=4)
    lenovo_parser.add_argument("--delay-min", type=float, default=1.0)
    lenovo_parser.add_argument("--delay-max", type=float, default=4.0)
    lenovo_parser.add_argument("--dry-run", action="store_true")

    bitbns_parser = sub.add_parser("bitbns-history", parents=[common], help="Fetch BitBns history for new listings or existing products")
    add_targets(bitbns_parser)
    mode = bitbns_parser.add_mutually_exclusive_group()
    mode.add_argument("--replace-history", action="store_true", help="Replace local history with BitBns change-points")
    mode.add_argument("--extend-history", action="store_true", help="Merge BitBns points into local history")
    bitbns_parser.add_argument("--workers", type=int, default=2)
    bitbns_parser.add_argument("--delay", type=float, default=2.0)
    bitbns_parser.add_argument("--missing-history-only", action="store_true", help="Only fetch products without a local history file.")
    bitbns_parser.add_argument("--failed-ids-output", default="", help="Write failed BitBns IDs to this JSON file.")
    bitbns_parser.add_argument("--dry-run", action="store_true")

    stats_parser = sub.add_parser("stats", parents=[common], help="Recalculate stats from local history")
    add_targets(stats_parser)
    stats_parser.add_argument("--dry-run", action="store_true")

    compress_parser = sub.add_parser("compress", parents=[common], help="Normalize history files to change-points")
    add_targets(compress_parser)
    compress_parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    data_path = paths.resolve(args.data)
    history_dir = paths.resolve(args.history_dir)
    ids = ids_from_args(args)

    if args.command == "compress":
        result = history.compress_dir(history_dir, ids=ids, dry_run=args.dry_run)
        print(result)
        return 0

    data = read_json(data_path, {})
    if args.command == "lenovo-current":
        result = pricing.update_from_lenovo(
            data,
            history_dir=history_dir,
            ids=ids,
            workers=args.workers,
            delay_min=args.delay_min,
            delay_max=args.delay_max,
            dry_run=args.dry_run,
        )
    elif args.command == "bitbns-history":
        mode = "replace" if args.replace_history else "extend"
        result = pricing.update_from_bitbns(
            data,
            history_dir=history_dir,
            ids=ids,
            mode=mode,
            missing_history_only=args.missing_history_only,
            workers=args.workers,
            delay=args.delay,
            dry_run=args.dry_run,
        )
        if args.failed_ids_output:
            failed_ids = [item["id"] for item in result["products"] if item.get("status") == "failed" and item.get("id")]
            write_json(paths.resolve(args.failed_ids_output), failed_ids, indent=2)
    else:
        result = pricing.recalculate_stats(data, history_dir=history_dir, ids=ids)

    if not getattr(args, "dry_run", False):
        write_json(data_path, data, indent=4)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
