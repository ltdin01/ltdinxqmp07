#!/usr/bin/env python3
"""Deep-merge a JSON file from both sides of a git merge."""

import json
import subprocess
import sys
from pathlib import Path


def deep_merge(base, incoming):
    """Recursively merge incoming into base."""
    if isinstance(base, dict) and isinstance(incoming, dict):
        result = dict(base)
        for key, inc_val in incoming.items():
            if key in result:
                base_val = result[key]
                if isinstance(base_val, dict) and isinstance(inc_val, dict):
                    result[key] = deep_merge(base_val, inc_val)
                elif isinstance(base_val, list) and isinstance(inc_val, list):
                    seen = set()
                    merged = []
                    for item in base_val + inc_val:
                        sig = json.dumps(item, sort_keys=True, ensure_ascii=False)
                        if sig not in seen:
                            seen.add(sig)
                            merged.append(item)
                    result[key] = merged
                else:
                    result[key] = inc_val
            else:
                result[key] = inc_val
        return result
    elif isinstance(base, list) and isinstance(incoming, list):
        seen = set()
        merged = []
        for item in base + incoming:
            sig = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if sig not in seen:
                seen.add(sig)
                merged.append(item)
        return merged
    else:
        return incoming


def get_file_at(ref: str, filepath: str) -> dict | None:
    try:
        raw = subprocess.check_output(
            ["git", "show", f"{ref}:{filepath}"],
            stderr=subprocess.DEVNULL, text=True
        )
        return json.loads(raw)
    except Exception:
        return None


def resolve(filepath: str):
    path = Path(filepath)
    print(f"Merging {filepath}...")

    ours = get_file_at("HEAD", filepath)
    theirs = get_file_at("origin/main", filepath)

    if ours is None and theirs is None:
        print(f"  skipped: not found on either side")
        return
    if ours is None:
        print(f"  using remote (new file)")
        result = theirs
    elif theirs is None:
        print(f"  using local (new file)")
        result = ours
    else:
        result = deep_merge(ours, theirs)
        print(f"  deep-merged both sides")

    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    subprocess.run(["git", "add", filepath], check=True)
    print(f"  staged")


if __name__ == "__main__":
    for fp in sys.argv[1:]:
        resolve(fp)
    print(f"\nDone: {len(sys.argv) - 1} files resolved")
