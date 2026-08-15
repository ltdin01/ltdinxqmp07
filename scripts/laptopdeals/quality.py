from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from laptopdeals.jsonio import read_json

CRITICAL_FIELDS: dict[str, tuple[str | tuple[str, ...], ...]] = {
    "processor": ("brand", "model"),
    "graphics": ("model", "dedicated"),
    "memory": ("amount", "type"),
    "storage": ("capacity", "type"),
    "display": (("size",), ("resolution", "res", "resolution_name")),
    "network": ("wifi", "bluetooth"),
}

# Useful enrichment fields are reported separately from the strict core-complete
# calculation.  Amazon pages do not expose every one of these fields for every
# listing, but tracking them makes coverage improvements and regressions visible.
ADDITIONAL_FIELDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "battery.capacity": (("tech_specs", "battery", "capacity"),),
    "ports.items": (("tech_specs", "ports", "items"),),
    # ``parse_amazon_specs`` emits ``camera.webcam`` and ``keyboard.details``;
    # earlier revisions of this table looked for ``model``/``raw`` and
    # ``type``/``raw``, which no parser has ever written.  Both entries were
    # therefore pinned at 0.00 coverage regardless of catalog quality.
    "camera.webcam": (("tech_specs", "camera", "webcam"),),
    "keyboard.details": (("tech_specs", "keyboard", "details"),),
    "dimensions.weight": (("tech_specs", "dimensions", "weight"),),
    "software.os": (("tech_specs", "software", "os"),),
    "metadata.model_number": (("product_metadata", "model_number"),),
    "metadata.manufacturer": (("product_metadata", "manufacturer"),),
    "metadata.series": (("product_metadata", "series"),),
}

UNKNOWN_VALUES = {"", "unknown", "n/a", "na", "null", "none", "nan", "-", "0"}

# Raw label vocabulary used to decide whether a critical fact was *observable*
# on the Amazon PDP at all.  Needles mirror the term lists passed to
# ``specs._attr_contains`` / ``specs._all_attr_values`` so "observable" means
# "the parser had something to work with", not "some vaguely related label
# existed".  Matching uses the same token-boundary rule as the parser
# (``specs._needle_matches``), so "screen size" matches "Standing screen
# display size" but "os" never matches "Battery cell composition".
#
# Deliberately absent: ``processor.cores``.  Amazon publishes "Processor
# Count", which is a package/logical-unit count rather than a physical core
# count, so the parser stopped consuming it.  Treating that label as an
# observable source for ``processor.cores`` would penalise the metric for
# correctly refusing bad data.  See CORRECT_REGRESSIONS below.
SOURCE_LABELS: dict[str, tuple[str, ...]] = {
    "processor.brand": (
        "processor brand",
        "cpu manufacturer",
        "processor type",
        "cpu type",
        "cpu model number",
        "processor model",
        "processor name",
        "processor",
    ),
    "processor.model": (
        "cpu model number",
        "processor model",
        "processor name",
        "processor",
        "processor type",
    ),
    "graphics.model": (
        "graphics description",
        "graphics card description",
        "graphics co processor",
        "graphics coprocessor",
        "video processor",
        "gpu model",
    ),
    "memory.amount": (
        "ram memory installed size",
        "ram memory installed",
        "computer memory size",
        "memory storage capacity",
        "installed ram",
        "system memory",
    ),
    "memory.type": ("ram memory technology", "memory technology", "system ram type", "memory type"),
    "storage.capacity": (
        "hard drive size",
        "hard disk size",
        "memory storage capacity",
        "flash memory size",
        "ssd capacity",
        "storage capacity",
    ),
    "storage.type": ("hard disk description", "hard drive interface", "storage type", "hard drive type"),
    "display.size": ("screen size", "standing screen display size", "display size"),
    "display.resolution|res|resolution_name": (
        "native resolution",
        "screen resolution",
        "maximum display resolution",
        "display resolution",
        "resolution",
    ),
    # ``specs`` feeds wifi/bluetooth through ``_all_attr_values`` with these
    # broad terms rather than a fixed label list.
    "network.wifi": ("wireless", "wifi", "wi fi", "connectivity"),
    "network.bluetooth": ("bluetooth",),
    "battery.capacity": ("lithium battery energy content", "battery power", "battery capacity"),
    "ports.items": (
        "total usb ports",
        "number of ports",
        "hardware interface",
        "human interface types",
        "total hdmi port",
        "number of ethernet ports",
        "total thunderbolt ports",
        "video output",
    ),
    "camera.webcam": ("webcam capability", "webcam", "camera"),
    "keyboard.details": ("keyboard description", "keyboard"),
    "dimensions.weight": ("item weight", "product weight", "laptop weight"),
    "software.os": ("operating system installed", "operating system", "os"),
}

# ``product_metadata`` is not built by ``parse_amazon_specs``; it comes from
# ``sources.amazon.extract_amazon_product_metadata``, which matches labels
# *exactly* rather than on token boundaries.  Mirroring that distinction
# matters: the token rule would let "CPU Model Number" (present on ~95% of
# PDPs) count as evidence for the product's model number, understating the
# metric by treating a processor label as a missed identity field.
SOURCE_EXACT_LABELS: dict[str, tuple[str, ...]] = {
    "metadata.model_number": ("model number", "item model number", "model no"),
    "metadata.manufacturer": ("manufacturer", "manufacturer name", "manufacturer information"),
    # Amazon publishes no "Series" label (only "Series Number", which the
    # extractor rejects), so this stays at zero observations: the served
    # ``series`` value is derived from the title, not observed.
    "metadata.series": ("series", "product series", "model series", "series name"),
}

ALL_SOURCE_FIELDS: tuple[str, ...] = (*SOURCE_LABELS, *SOURCE_EXACT_LABELS)

# Normalized destination for each source-available fact.  Critical fields live
# under ``tech_specs`` with several accepted leaf names; enrichment fields
# reuse the ADDITIONAL_FIELDS paths.
SOURCE_DESTINATIONS: dict[str, tuple[tuple[str, ...], ...]] = {
    "processor.brand": (("tech_specs", "processor", "brand"),),
    "processor.model": (("tech_specs", "processor", "model"),),
    "graphics.model": (("tech_specs", "graphics", "model"),),
    "memory.amount": (("tech_specs", "memory", "amount"),),
    "memory.type": (("tech_specs", "memory", "type"),),
    "storage.capacity": (("tech_specs", "storage", "capacity"),),
    "storage.type": (("tech_specs", "storage", "type"),),
    "display.size": (("tech_specs", "display", "size"),),
    "display.resolution|res|resolution_name": (
        ("tech_specs", "display", "resolution"),
        ("tech_specs", "display", "res"),
        ("tech_specs", "display", "resolution_name"),
    ),
    "network.wifi": (("tech_specs", "network", "wifi"),),
    "network.bluetooth": (("tech_specs", "network", "bluetooth"),),
    **ADDITIONAL_FIELDS,
}

# Bounds on the ``spec_conflicts`` section.  The catalog workflows commit the
# report JSON, so an unbounded dump of all ~400 disagreements would bury the
# diff.  The ranked list is the part that finds bugs; a handful of example value
# pairs per field is enough for a human to spot a systematic parse error.
CONFLICT_TOP_FIELDS = 12
CONFLICT_EXAMPLES_PER_FIELD = 3
CONFLICT_VALUE_CHARS = 120
# Cap on distinct (manufacturer, retailer) pairs tracked per field.  Bounds
# memory on a pathological catalog where every row disagrees differently; the
# examples only ever show the most frequent few anyway.
CONFLICT_PAIRS_TRACKED = 200

# Regressions that are known-correct data-quality fixes rather than lost
# coverage.  Excluded from both the tracked field tables and the
# source-available metric so the gate cannot fail on them.
CORRECT_REGRESSIONS: dict[str, str] = {
    "processor.cores": (
        "Amazon's 'Processor Count' is a package count, not a physical core count. "
        "It agreed with the stored value on 146/146 products purely by coincidence of "
        "both being wrong; the parser no longer consumes it. 92% -> 0% is intended."
    ),
}


def _label_key(value: Any) -> str:
    """Canonical form of an Amazon spec label (mirrors specs._label_key)."""
    text = str(value or "").replace("×", "x").strip().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _iter_raw_labels(row: dict[str, Any]):
    """Yield (normalized_label, value) for every raw label/value pair on a product.

    Covers the ``specs_attr`` flat map, the ``spec_sections`` nested map and the
    richer ``amazon_observations`` list.  Formatted web-catalog rows carry none
    of these, in which case nothing is yielded and the product simply does not
    contribute to the source-available denominator.
    """
    attrs = row.get("specs_attr")
    if isinstance(attrs, dict):
        for label, value in attrs.items():
            yield _label_key(label), value
    sections = row.get("spec_sections")
    if isinstance(sections, dict):
        for section in sections.values():
            if isinstance(section, dict):
                for label, value in section.items():
                    yield _label_key(label), value
    observations = row.get("amazon_observations")
    if isinstance(observations, list):
        for item in observations:
            if isinstance(item, dict):
                yield _label_key(item.get("label")), item.get("value")


def _has_raw_specs(row: dict[str, Any]) -> bool:
    return any(
        isinstance(row.get(key), (dict, list)) and row.get(key)
        for key in ("specs_attr", "spec_sections", "amazon_observations")
    )


def _observed_labels(row: dict[str, Any]) -> set[str]:
    """Normalized labels on this product that carry a usable value."""
    return {label for label, value in _iter_raw_labels(row) if label and not _is_bad(value)}


def _needle_matches(needle: str, label: str) -> bool:
    """Token-boundary match, mirroring ``specs._needle_matches``.

    Comparing on token boundaries rather than raw substrings is what keeps
    short needles honest: ``os`` must not match ``composition``, and ``ports``
    must not match ``supports``.
    """
    if needle == label:
        return True
    needle_tokens = needle.split()
    label_tokens = label.split()
    if not needle_tokens or len(needle_tokens) > len(label_tokens):
        return False
    return any(
        label_tokens[start : start + len(needle_tokens)] == needle_tokens
        for start in range(len(label_tokens) - len(needle_tokens) + 1)
    )


def _is_observable(labels: set[str], field: str) -> bool:
    """Did the PDP carry a label this field's parser knows how to read?"""
    exact = SOURCE_EXACT_LABELS.get(field)
    if exact is not None:
        return any(_label_key(needle) in labels for needle in exact)
    return any(
        _needle_matches(_label_key(needle), label)
        for needle in SOURCE_LABELS.get(field, ())
        for label in labels
    )


def _as_categories(data: Any) -> dict[str, list[dict[str, Any]]]:
    """Accept either the formatted web catalog or the raw scrape catalog.

    The formatted catalog is ``{category: [product, ...]}``; the raw catalog is
    ``{"generated_at": ..., "products": [product, ...]}``.  Both collapse to a
    category -> rows mapping here so the gate can run on either.
    """
    if not isinstance(data, dict):
        return {}
    products = data.get("products")
    if isinstance(products, list) and not any(
        isinstance(value, list) for key, value in data.items() if key != "products"
    ):
        return {"products": products}
    return {key: value for key, value in data.items() if isinstance(value, list)}



def _is_bad(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return value is None
    if isinstance(value, str):
        return value.strip().lower() in UNKNOWN_VALUES
    if isinstance(value, (list, dict)):
        return len(value) == 0
    if isinstance(value, (int, float)):
        return value == 0
    return False


def _value_at(specs: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = specs
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _first_value_at(root: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> Any:
    for path in paths:
        value = _value_at(root, path)
        if not _is_bad(value):
            return value
    return None


def _field_value(specs: dict[str, Any], key: str, paths: str | tuple[str, ...]) -> Any:
    if isinstance(paths, str):
        paths = (paths,)
    for path in paths:
        value = _value_at(specs, (key, path))
        if not _is_bad(value):
            return value
    return None


def _conflict_text(value: Any, *, depth: int = 0) -> str:
    """One-line, length-bounded rendering of a conflicting spec value.

    Conflicts are recorded verbatim from ``tech_specs``, so a value may be a
    list (``ports.items``), a bool (``memory.soldered``) or a number
    (``processor.cores``).  The report compares them by eye, so everything
    collapses to a short single line.
    """
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (list, tuple)):
        text = (
            ", ".join(_conflict_text(item, depth=depth + 1) for item in list(value)[:8])
            if depth < 2
            else f"[{len(value)} items]"
        )
    elif isinstance(value, dict):
        text = f"{{{len(value)} keys}}"
    else:
        text = "" if value is None else str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= CONFLICT_VALUE_CHARS else text[: CONFLICT_VALUE_CHARS - 1] + "…"


def _iter_conflicts(row: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Yield the well-formed conflict records on one product.

    ``spec_conflicts`` is absent on most rows and may be stale/hand-edited, so
    a non-list or a list of non-dicts must degrade to "no conflicts" rather
    than crash the gate.
    """
    conflicts = row.get("spec_conflicts")
    if not isinstance(conflicts, list):
        return
    for entry in conflicts:
        if isinstance(entry, dict):
            yield entry


def _ranked(counter: Counter) -> dict[str, int]:
    """Counter as a plain dict, highest first, ties broken by name."""
    return {key: count for key, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))}


def conflict_report(data: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Summarize every retailer/manufacturer disagreement in a catalog.

    ``manufacturer.merge_section`` records a conflict whenever manufacturer data
    contradicts a populated retailer value, including the ones it declines to
    apply: ``chosen == "retailer"`` means the family tier saw a disagreement but
    is fill-only, ``chosen == "manufacturer"`` means an exact tier overwrote the
    retailer.  Both are signal.  A field where many products disagree with only
    one or two *distinct* value pairs is the tell-tale of a systematic parser
    bug on one side rather than genuine per-SKU variation, so ``distinct_pairs``
    is reported next to the count.
    """
    total = 0
    products = 0
    products_with_conflicts = 0
    malformed = 0
    by_chosen: Counter = Counter()
    by_origin: Counter = Counter()
    per_field: dict[str, dict[str, Any]] = {}

    for rows in _as_categories(data).values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            products += 1
            raw = row.get("spec_conflicts")
            if isinstance(raw, list):
                malformed += sum(1 for entry in raw if not isinstance(entry, dict))
            elif raw is not None and raw != {}:
                malformed += 1
            seen_fields: set[str] = set()
            for entry in _iter_conflicts(row):
                total += 1
                field = str(entry.get("field") or "").strip() or "(unspecified)"
                chosen = str(entry.get("chosen") or "").strip() or "(unspecified)"
                origin = str(entry.get("manufacturer_origin") or "").strip() or "(unspecified)"
                by_chosen[chosen] += 1
                by_origin[origin] += 1
                bucket = per_field.setdefault(
                    field,
                    {
                        "count": 0,
                        "products": 0,
                        "chosen": Counter(),
                        "origin": Counter(),
                        "pairs": {},
                        "pairs_dropped": 0,
                    },
                )
                bucket["count"] += 1
                bucket["chosen"][chosen] += 1
                bucket["origin"][origin] += 1
                # Keyed on the value pair alone. Folding chosen/origin into the
                # key would inflate distinct_pairs -- the same parse mismatch
                # reached through an exact and a family tier is still one bug.
                pair = (
                    _conflict_text(entry.get("manufacturer")),
                    _conflict_text(entry.get("retailer")),
                )
                pairs = bucket["pairs"]
                if pair in pairs or len(pairs) < CONFLICT_PAIRS_TRACKED:
                    seen_pair = pairs.setdefault(
                        pair, {"count": 0, "chosen": Counter(), "origin": Counter()}
                    )
                    seen_pair["count"] += 1
                    seen_pair["chosen"][chosen] += 1
                    seen_pair["origin"][origin] += 1
                else:
                    bucket["pairs_dropped"] += 1
                if field not in seen_fields:
                    seen_fields.add(field)
                    bucket["products"] += 1
            if seen_fields:
                products_with_conflicts += 1

    ranked_fields = sorted(per_field.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
    top_fields: list[dict[str, Any]] = []
    for field, bucket in ranked_fields[:CONFLICT_TOP_FIELDS]:
        examples = [
            {
                "manufacturer": manufacturer,
                "retailer": retailer,
                "count": stats["count"],
                "chosen": _ranked(stats["chosen"]),
                "manufacturer_origin": _ranked(stats["origin"]),
            }
            for (manufacturer, retailer), stats in sorted(
                bucket["pairs"].items(), key=lambda kv: (-kv[1]["count"], kv[0])
            )[:CONFLICT_EXAMPLES_PER_FIELD]
        ]
        top_fields.append(
            {
                "field": field,
                "count": bucket["count"],
                "products": bucket["products"],
                "by_chosen": _ranked(bucket["chosen"]),
                "by_origin": _ranked(bucket["origin"]),
                # Few distinct pairs across many products => one side normalizes
                # the value differently every time, i.e. a parser bug, not a
                # genuine per-configuration difference.
                "distinct_pairs": len(bucket["pairs"]) + bucket["pairs_dropped"],
                "examples": examples,
            }
        )

    return {
        "total": total,
        "products_with_conflicts": products_with_conflicts,
        "products_share": round(products_with_conflicts / products, 4) if products else 0.0,
        "by_chosen": _ranked(by_chosen),
        "by_origin": _ranked(by_origin),
        "fields_with_conflicts": len(per_field),
        "top_fields": top_fields,
        "fields_omitted": max(0, len(ranked_fields) - CONFLICT_TOP_FIELDS),
        # Entries that were not usable conflict records.  Non-zero means the
        # catalog carries a stale or hand-edited spec_conflicts shape.
        "malformed_entries": malformed,
    }


def conflict_summary_line(conflicts: dict[str, Any], total_products: int) -> str:
    """One CLI line: how many disagreements, who won, and the worst field."""
    total = conflicts.get("total", 0)
    if not total:
        return "conflicts: none recorded"
    chosen = conflicts.get("by_chosen") or {}
    split = " / ".join(f"{name} {count}" for name, count in chosen.items())
    top_fields = conflicts.get("top_fields") or []
    top = (
        f" | top: {top_fields[0]['field']} {top_fields[0]['count']}"
        f" ({top_fields[0]['distinct_pairs']} distinct pairs)"
        if top_fields
        else ""
    )
    return (
        f"conflicts: {total} on {conflicts.get('products_with_conflicts', 0)}/{total_products} "
        f"products ({split}){top}"
    )


def coverage_report(data: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    categories: dict[str, int] = {}
    field_stats: dict[str, dict[str, int]] = {}
    for key, paths in CRITICAL_FIELDS.items():
        for path in paths:
            label = f"{key}.{path}" if isinstance(path, str) else f"{key}." + "|".join(path)
            field_stats[label] = {"checked": 0, "missing": 0, "unknown": 0, "ok": 0}
    for label in ADDITIONAL_FIELDS:
        field_stats[label] = {"checked": 0, "missing": 0, "unknown": 0, "ok": 0}
    source_stats: dict[str, dict[str, int]] = {
        label: {"observable": 0, "landed": 0} for label in ALL_SOURCE_FIELDS
    }
    raw_specs_products = 0
    core_ok = 0
    total = 0
    for category, rows in _as_categories(data).items():
        if not isinstance(rows, list):
            continue
        categories[category] = len(rows)
        for row in rows:
            if not isinstance(row, dict):
                continue
            total += 1
            specs = row.get("tech_specs") if isinstance(row.get("tech_specs"), dict) else {}
            per_product_ok = True
            for key, paths in CRITICAL_FIELDS.items():
                for path in paths:
                    label = f"{key}.{path}" if isinstance(path, str) else f"{key}." + "|".join(path)
                    bucket = field_stats[label]
                    bucket["checked"] += 1
                    value = _field_value(specs, key, path)
                    if value is not None:
                        bucket["ok"] += 1
                    elif not isinstance(specs.get(key), dict):
                        bucket["missing"] += 1
                        per_product_ok = False
                    elif _value_at(specs, (key, path)) is None:
                        bucket["missing"] += 1
                        per_product_ok = False
                    else:
                        bucket["unknown"] += 1
                        per_product_ok = False
            if per_product_ok:
                core_ok += 1
            for label, paths in ADDITIONAL_FIELDS.items():
                bucket = field_stats[label]
                bucket["checked"] += 1
                if _first_value_at(row, paths) is not None:
                    bucket["ok"] += 1
                elif any(_value_at(row, path) is not None for path in paths):
                    bucket["unknown"] += 1
                else:
                    bucket["missing"] += 1
            if not _has_raw_specs(row):
                continue
            raw_specs_products += 1
            labels = _observed_labels(row)
            for label in ALL_SOURCE_FIELDS:
                if not _is_observable(labels, label):
                    continue
                source_stats[label]["observable"] += 1
                if _first_value_at(row, SOURCE_DESTINATIONS[label]) is not None:
                    source_stats[label]["landed"] += 1
    fields: dict[str, Any] = {}
    for label, bucket in field_stats.items():
        fields[label] = {
            **bucket,
            "coverage": round(bucket["ok"] / bucket["checked"], 4) if bucket["checked"] else 1.0,
        }
    source_fields: dict[str, Any] = {}
    observable_total = 0
    landed_total = 0
    for label, bucket in source_stats.items():
        observable_total += bucket["observable"]
        landed_total += bucket["landed"]
        source_fields[label] = {
            **bucket,
            "coverage": round(bucket["landed"] / bucket["observable"], 4) if bucket["observable"] else 1.0,
        }
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_products": total,
        "categories": categories,
        "fields": fields,
        "core_specs": {
            "complete": core_ok,
            "coverage": round(core_ok / total, 4) if total else 1.0,
        },
        # Absolute coverage above conflates "Amazon never published this fact"
        # with "our parser dropped it".  source_available isolates the second
        # case: of the facts actually visible in the raw label/value maps, how
        # many reached their normalized tech_specs destination.
        "source_available": {
            "observable": observable_total,
            "landed": landed_total,
            "coverage": round(landed_total / observable_total, 4) if observable_total else 1.0,
            # False means the input carried no raw label maps -- the formatted
            # web catalog drops specs_attr/spec_sections, so the metric is not
            # computable there and the 1.0 above is a vacuous default, not a
            # measurement.  Run the gate on data/amazon-catalog.json to get a
            # real number.
            "computable": bool(raw_specs_products),
            "raw_specs_products": raw_specs_products,
            "fields": source_fields,
        },
        # Field-level disagreements between manufacturer and retailer specs,
        # recorded by manufacturer.merge_section.  Reported, never gated by
        # default: a conflict is evidence, not a failure.
        "spec_conflicts": conflict_report(data),
        "correct_regressions": CORRECT_REGRESSIONS,
    }


def quality_gate(
    input_path: Path,
    *,
    baseline_path: Path | None = None,
    report_path: Path | None = None,
    min_core_coverage: float = 0.0,
    min_field_coverage: float = 0.0,
    min_source_available: float = 0.0,
    max_conflict_share: float = 0.0,
    regression_delta: float = 0.05,
) -> dict[str, Any]:
    data = read_json(input_path, {})
    report = coverage_report(data)
    failures: list[str] = []
    if report["core_specs"]["coverage"] < min_core_coverage:
        failures.append(
            f"core spec coverage {report['core_specs']['coverage']:.2%} < {min_core_coverage:.0%}"
        )
    source = report["source_available"]
    # Only enforceable on the raw scrape catalog: the formatted web catalog
    # drops ``specs_attr``/``spec_sections``, so there is nothing to compare
    # the normalized values against and the threshold would fail vacuously.
    if min_source_available > 0 and source["computable"]:
        if source["coverage"] < min_source_available:
            failures.append(
                f"source-available coverage {source['coverage']:.2%} < {min_source_available:.0%} "
                f"({source['landed']}/{source['observable']} observed facts normalized)"
            )
    for field, stats in report["fields"].items():
        if stats["coverage"] < min_field_coverage:
            failures.append(
                f"field {field} coverage {stats['coverage']:.2%} < {min_field_coverage:.0%}"
            )
    # Off by default: a manufacturer/retailer disagreement is information, and
    # the family tier records plenty it deliberately does not apply.  The
    # threshold exists for a provider that has decided its conflict rate is
    # under control and wants to be told when it spikes.
    conflicts = report["spec_conflicts"]
    if max_conflict_share > 0 and conflicts["products_share"] > max_conflict_share:
        top = conflicts["top_fields"][0]["field"] if conflicts["top_fields"] else "n/a"
        failures.append(
            f"spec conflicts on {conflicts['products_share']:.2%} of products "
            f"> {max_conflict_share:.0%} ({conflicts['total']} total, top field {top})"
        )
    if baseline_path and baseline_path.exists():
        baseline = read_json(baseline_path, {})
        for field, stats in report["fields"].items():
            # Fields listed in CORRECT_REGRESSIONS lost coverage because the
            # parser stopped consuming bad data.  A stale baseline that still
            # records their old (wrong) coverage must not fail the gate.
            if field in CORRECT_REGRESSIONS:
                continue
            base_coverage = baseline.get("fields", {}).get(field, {}).get("coverage")
            if base_coverage is not None and stats["coverage"] < base_coverage - regression_delta:
                failures.append(
                    f"field {field} regression: {stats['coverage']:.2%} vs baseline {base_coverage:.2%}"
                )
    report["exit"] = "fail" if failures else "pass"
    report["failures"] = failures
    if report_path:
        from laptopdeals.jsonio import write_json

        write_json(report_path, report, indent=2)
    return report


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Data quality gate for laptop catalogs")
    parser.add_argument("--input", required=True, help="Catalog JSON (data.json / data-amazon.json)")
    parser.add_argument("--baseline", default="", help="Prior quality report JSON for regression comparison")
    parser.add_argument("--report", default="", help="Where to write the quality report JSON")
    parser.add_argument("--min-core-coverage", type=float, default=0.0)
    parser.add_argument("--min-field-coverage", type=float, default=0.0)
    parser.add_argument(
        "--min-source-available",
        type=float,
        default=0.0,
        help=(
            "Minimum share of facts that were observable in the raw Amazon "
            "spec_sections/specs_attr and reached their normalized tech_specs "
            "destination. Requires the raw scrape catalog; ignored on the "
            "formatted web catalog, which drops the raw label maps."
        ),
    )
    parser.add_argument(
        "--max-conflict-share",
        type=float,
        default=0.0,
        help=(
            "Fail when more than this share of products carry at least one "
            "manufacturer/retailer spec conflict. 0 (the default) disables the "
            "check: conflicts are reported, not gated."
        ),
    )
    parser.add_argument("--regression-delta", type=float, default=0.05)
    args = parser.parse_args()

    report = quality_gate(
        Path(args.input),
        baseline_path=Path(args.baseline) if args.baseline else None,
        report_path=Path(args.report) if args.report else None,
        min_core_coverage=args.min_core_coverage,
        min_field_coverage=args.min_field_coverage,
        min_source_available=args.min_source_available,
        max_conflict_share=args.max_conflict_share,
        regression_delta=args.regression_delta,
    )
    source = report["source_available"]
    source_text = (
        f"source-available={source['coverage']:.2%} ({source['landed']}/{source['observable']})"
        if source["computable"]
        else "source-available=n/a (no raw spec labels in input)"
    )
    print(
        f"quality: {report['exit']} | products={report['total_products']} "
        f"core={report['core_specs']['coverage']:.2%} | {source_text}"
    )
    print(conflict_summary_line(report["spec_conflicts"], report["total_products"]))
    for failure in report["failures"]:
        print(f"  FAIL: {failure}")
    return 0 if report["exit"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
