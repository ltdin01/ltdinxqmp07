#!/usr/bin/env python3
from __future__ import annotations

import argparse
from typing import Any

from laptopdeals import catalog, paths
from laptopdeals.ids import ids_from_args

DEFAULT_SERIES: dict[str, list[str] | None] = {
    "lenovo": ["LOQ", "ThinkPad", "Legion", "IdeaPad", "ThinkBook", "Yoga"],
    "refurb": None,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Laptop catalog scrape and formatting operations")
    parser.add_argument("--provider", default="lenovo", help="Provider to operate on (lenovo, refurb)")
    sub = parser.add_subparsers(dest="command", required=True)

    scrape = sub.add_parser("scrape", help="Scrape catalog")
    scrape.add_argument("--series", nargs="*", default=[], help="Series filter (empty = provider default)")
    scrape.add_argument("--output", default="")
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
    fmt.add_argument("--input", default="")
    fmt.add_argument("--output", default="")
    fmt.add_argument("--history-dir", default="")
    fmt.add_argument("--cto-dir", default=str(paths.CTO_CONFIGS))
    fmt.add_argument("--existing-data", default="")
    fmt.add_argument("--psref-dir", default=str(paths.PSREF_SKU_DIR), help="PSREF per-SKU sidecar directory")
    fmt.add_argument("--psref-map", default=str(paths.PSREF_MAP), help="PSREF final SKU specs map JSON")
    fmt.add_argument("--force-psref", action="store_true", help="Force refresh PSREF datasheets for all products")
    fmt.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    if args.command == "scrape":
        if args.provider not in DEFAULT_SERIES:
            raise SystemExit(f"[catalog] unknown provider: {args.provider!r}")
        series = args.series if args.series else DEFAULT_SERIES[args.provider]
        scrape_kwargs: dict[str, Any] = dict(
            series=series or None,
            output=paths.resolve(args.output) if args.output else paths.raw_catalog(args.provider),
            only_new=args.only_new,
            existing_files=[paths.resolve(item) for item in args.existing_file],
            new_ids_output=paths.resolve(args.new_ids_output) if args.new_ids_output else None,
            limit_per_series=args.limit_per_series,
            delay=(args.delay_min, args.delay_max),
            workers=args.workers,
            verbose=args.verbose,
            ids=ids_from_args(args),
        )
        result = catalog.scrape_catalog(provider=args.provider, **scrape_kwargs)
    else:
        result = catalog.format_catalog(
            provider=args.provider,
            input_path=paths.resolve(args.input) if args.input else paths.raw_catalog(args.provider),
            output_path=paths.resolve(args.output) if args.output else paths.app_data(args.provider),
            history_dir=paths.resolve(args.history_dir) if args.history_dir else paths.price_history_dir(args.provider),
            cto_dir=paths.resolve(args.cto_dir),
            existing_data=paths.resolve(args.existing_data) if args.existing_data else None,
            dry_run=args.dry_run,
            psref_dir=paths.resolve(args.psref_dir),
            psref_map=paths.resolve(args.psref_map),
            force_psref=args.force_psref,
        )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
