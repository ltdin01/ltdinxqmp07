#!/usr/bin/env python3
from __future__ import annotations

import argparse

from laptopdeals import catalog, paths
from laptopdeals.ids import ids_from_args


def main() -> int:
    parser = argparse.ArgumentParser(description="Lenovo catalog scrape and formatting operations")
    sub = parser.add_subparsers(dest="command", required=True)

    scrape = sub.add_parser("scrape", help="Scrape Lenovo catalog")
    scrape.add_argument("--series", nargs="*", default=["LOQ", "ThinkPad", "Legion", "IdeaPad", "ThinkBook", "Yoga"])
    scrape.add_argument("--output", default=str(paths.RAW_CATALOG))
    scrape.add_argument("--only-new", action="store_true")
    scrape.add_argument("--existing-file", action="append", default=[])
    scrape.add_argument("--new-ids-output", default="")
    scrape.add_argument("--limit-per-series", type=int, default=None)
    scrape.add_argument("--delay-min", type=float, default=0.8)
    scrape.add_argument("--delay-max", type=float, default=2.2)
    scrape.add_argument("--workers", type=int, default=4, help="Parallel PDP detail fetch workers.")
    scrape.add_argument("--verbose", action="store_true")
    scrape.add_argument("--id", action="append", default=[], help="Target product ID(s). Repeatable.")
    scrape.add_argument("--ids-file", default="", help="JSON file containing product IDs.")

    fmt = sub.add_parser("format", help="Format raw catalog for web app")
    fmt.add_argument("--input", default=str(paths.RAW_CATALOG))
    fmt.add_argument("--output", default=str(paths.APP_DATA))
    fmt.add_argument("--history-dir", default=str(paths.PRICE_HISTORY))
    fmt.add_argument("--cto-dir", default=str(paths.CTO_CONFIGS))
    fmt.add_argument("--existing-data", default="")
    fmt.add_argument("--psref-dir", default=str(paths.PSREF_SKU_DIR), help="PSREF per-SKU sidecar directory")
    fmt.add_argument("--psref-map", default=str(paths.PSREF_MAP), help="PSREF final SKU specs map JSON")
    fmt.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    if args.command == "scrape":
        result = catalog.scrape_catalog(
            series=args.series,
            output=paths.resolve(args.output),
            only_new=args.only_new,
            existing_files=[paths.resolve(item) for item in args.existing_file],
            new_ids_output=paths.resolve(args.new_ids_output) if args.new_ids_output else None,
            limit_per_series=args.limit_per_series,
            delay=(args.delay_min, args.delay_max),
            workers=args.workers,
            verbose=args.verbose,
            ids=ids_from_args(args),
        )
    else:
        result = catalog.format_catalog(
            input_path=paths.resolve(args.input),
            output_path=paths.resolve(args.output),
            history_dir=paths.resolve(args.history_dir),
            cto_dir=paths.resolve(args.cto_dir),
            existing_data=paths.resolve(args.existing_data) if args.existing_data else None,
            dry_run=args.dry_run,
            psref_dir=paths.resolve(args.psref_dir),
            psref_map=paths.resolve(args.psref_map),
        )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
