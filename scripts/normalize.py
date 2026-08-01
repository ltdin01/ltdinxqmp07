#!/usr/bin/env python3
"""Normalize hardware specs from local inventories after the PSREF/format steps."""
from __future__ import annotations

import argparse

from laptopdeals.normalize_hardware import normalize_catalog, normalize_cto_configs


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize hardware specs from local inventories")
    parser.add_argument("--no-cto", action="store_true", help="Skip CTO config normalization")
    args = parser.parse_args()

    cto_n = 0 if args.no_cto else normalize_cto_configs()
    catalog_n = normalize_catalog()
    print({"catalog_normalized": catalog_n, "cto_normalized": cto_n})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
