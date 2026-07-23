#!/usr/bin/env python3
from __future__ import annotations

import argparse

from laptopdeals import cto, paths
from laptopdeals.ids import ids_from_args
from laptopdeals.jsonio import read_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh Lenovo CTO configuration files")
    parser.add_argument("--data", default=str(paths.APP_DATA))
    parser.add_argument("--output-dir", default=str(paths.CTO_CONFIGS))
    parser.add_argument("--id", action="append", default=[], help="Target CTO product ID(s)")
    parser.add_argument("--ids-file", default="")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reenrich", action="store_true", help="Re-enrich existing sidecar files with parsed specs (no network fetch)")
    parser.add_argument("--force", action="store_true", help="Force re-enrichment even if specs already exist")
    args = parser.parse_args()
    if args.reenrich:
        result = cto.reenrich_all_cto(paths.resolve(args.output_dir), force=args.force)
        print(result)
        return 0
    result = cto.refresh_cto_configs(
        read_json(paths.resolve(args.data), {}),
        output_dir=paths.resolve(args.output_dir),
        ids=ids_from_args(args),
        workers=args.workers,
        dry_run=args.dry_run,
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
