#!/usr/bin/env python3
from __future__ import annotations

import argparse

from laptopdeals import archive, paths
from laptopdeals.ids import ids_from_args


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive or restore products")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Archive unavailable Lenovo products")
    check.add_argument("--data", default=str(paths.APP_DATA))
    check.add_argument("--raw-catalog", default=str(paths.RAW_CATALOG))
    check.add_argument("--archive", default=str(paths.ARCHIVE))
    check.add_argument("--id", action="append", default=[])
    check.add_argument("--ids-file", default="")
    check.add_argument("--limit", type=int, default=None)
    check.add_argument("--max-archive", type=int, default=25)
    check.add_argument("--html-dir", default="")
    check.add_argument("--apply", action="store_true")

    restore = sub.add_parser("restore", help="Remove selected products from archive")
    restore.add_argument("--data", default=str(paths.APP_DATA))
    restore.add_argument("--archive", default=str(paths.ARCHIVE))
    restore.add_argument("--id", action="append", default=[])
    restore.add_argument("--ids-file", default="")
    restore.add_argument("--apply", action="store_true")

    args = parser.parse_args()
    if args.command == "check":
        result = archive.archive_unavailable(
            data_path=paths.resolve(args.data),
            raw_catalog_path=paths.resolve(args.raw_catalog),
            archive_path=paths.resolve(args.archive),
            ids=ids_from_args(args),
            limit=args.limit,
            max_archive=args.max_archive,
            html_dir=paths.resolve(args.html_dir) if args.html_dir else None,
            apply=args.apply,
        )
    else:
        result = archive.restore_ids(data_path=paths.resolve(args.data), archive_path=paths.resolve(args.archive), ids=ids_from_args(args), apply=args.apply)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

