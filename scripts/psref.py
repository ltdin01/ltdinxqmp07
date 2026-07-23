#!/usr/bin/env python3
from __future__ import annotations

import argparse

from laptopdeals import ids, paths, psref


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Lenovo PSREF-backed spec sidecars")
    parser.add_argument("--catalog", default=str(paths.RAW_CATALOG), help="Input Lenovo raw catalog JSON")
    parser.add_argument("--cto-dir", default=str(paths.CTO_CONFIGS), help="Directory with Lenovo CTO config sidecars")
    parser.add_argument("--output-dir", default=str(paths.PSREF_DIR), help="Output directory for menu cache, workbooks, sidecars, and inventory")
    parser.add_argument("--sku", action="append", help="Limit build to one or more specific SKUs")
    parser.add_argument("--ids-file", help="JSON file containing target SKUs (e.g. data/lenovo-new-ids.json)")
    parser.add_argument("--refresh", action="store_true", help="Re-fetch PSREF menu and workbooks")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    sku_filter = ids.split_ids(args.sku) | ids.read_ids_file(args.ids_file)
    result = psref.build(
        catalog_path=paths.resolve(args.catalog),
        cto_dir=paths.resolve(args.cto_dir),
        output_dir=paths.resolve(args.output_dir),
        refresh=args.refresh,
        sku_filter=sku_filter or None,
        verbose=args.verbose,
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
