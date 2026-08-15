"""Manufacturer-driven spec resolution.

Retailer listings (Amazon) carry weaker specs than the manufacturer publishes:
``power.watt`` is absent from every Amazon row, ``display.refresh`` from three
quarters of them, and ``graphics.vram`` is sometimes outright wrong. Where we
also hold manufacturer data for the same machine — ASUS techspec tables, Lenovo
PSREF — that data should win.

The resolution rule, in order:

1. **Exact model match** — the manufacturer's own row for this exact model code.
   Authoritative: it overwrites a conflicting retailer value.
2. **Family specs** — fields the manufacturer publishes identically for *every*
   configuration in the family. Safe, but weaker evidence than a reading taken
   from the specific machine's own listing, so it only fills gaps.
3. **Constructed family specs** — same as (2), except the manufacturer split the
   family across several pages (ASUS publishes ``X1607`` and ``X1607Q``
   separately) and we merged them before computing invariance.
4. **Retailer value** — kept when there is no manufacturer evidence at all.

A field that varies across configurations and has no exact match is left empty
rather than guessed. One Lenovo machine type spans up to 136 configurations, so
picking a representative value would mean inventing specs for the other 135.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# Where a spec section came from. Ordered strongest-evidence first; callers rely
# on the index for precedence comparisons, so keep exact tiers above family ones.
ORIGIN_ASUS_EXACT = "asus_exact"
ORIGIN_PSREF_EXACT = "psref_exact"
ORIGIN_ASUS_FAMILY = "asus_family"
ORIGIN_ASUS_FAMILY_CONSTRUCTED = "asus_family_constructed"
ORIGIN_PSREF_PLATFORM = "psref_platform"
ORIGIN_AMAZON_PDP = "amazon_pdp"
ORIGIN_LENOVO_INDIA = "lenovo_india"

ORIGIN_PRECEDENCE: tuple[str, ...] = (
    ORIGIN_ASUS_EXACT,
    ORIGIN_PSREF_EXACT,
    ORIGIN_ASUS_FAMILY,
    ORIGIN_ASUS_FAMILY_CONSTRUCTED,
    ORIGIN_PSREF_PLATFORM,
    ORIGIN_LENOVO_INDIA,
    ORIGIN_AMAZON_PDP,
)

# Only these may overwrite a populated retailer value. Family tiers are real
# manufacturer data but describe the family, not this machine, so they must not
# displace a reading taken from the machine's own listing.
AUTHORITATIVE_ORIGINS: frozenset[str] = frozenset({ORIGIN_ASUS_EXACT, ORIGIN_PSREF_EXACT})

MANUFACTURER_ORIGINS: frozenset[str] = frozenset(
    {
        ORIGIN_ASUS_EXACT,
        ORIGIN_PSREF_EXACT,
        ORIGIN_ASUS_FAMILY,
        ORIGIN_ASUS_FAMILY_CONSTRUCTED,
        ORIGIN_PSREF_PLATFORM,
    }
)


def is_empty(value: Any) -> bool:
    """Treat the pipeline's several "no data" spellings as one thing."""
    if value is None or value is False:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"", "unknown", "n/a", "na", "none", "-"}
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    if isinstance(value, (int, float)):
        return value == 0
    return False


def invariant_fields(configs: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Fields whose value is identical across every configuration given.

    This is what makes a family spec safe to publish: the value is not a
    representative pick, it is the only value the family has. A field that
    differs anywhere is omitted entirely rather than resolved.
    """
    rows = [config for config in configs if isinstance(config, dict)]
    if not rows:
        return {}

    shared: dict[str, Any] = {}
    for label in rows[0]:
        values = [row.get(label) for row in rows]
        if any(is_empty(value) for value in values):
            continue
        first = values[0]
        try:
            if all(value == first for value in values[1:]):
                shared[label] = first
        except Exception:
            # Unhashable/uncomparable payloads are not worth a family claim.
            continue
    return shared


def _origin_rank(origin: str) -> int:
    try:
        return ORIGIN_PRECEDENCE.index(origin)
    except ValueError:
        return len(ORIGIN_PRECEDENCE)


CONFLICT_TRACKED_FIELDS = frozenset(
    {
        "processor.brand",
        "processor.model",
        "processor.base_clock",
        "processor.boost_clock",
        "processor.cores",
        "processor.threads",
        "graphics.brand",
        "graphics.model",
        "graphics.vram",
        "graphics.tgp",
        "memory.amount",
        "memory.type",
        "memory.speed",
        "memory.slots",
        "storage.capacity",
        "storage.type",
        "storage.interface",
        "display.size",
        "display.resolution",
        "display.refresh",
        "display.type",
        "display.touch",
        "power.watt",
        "power.adapter",
        "battery.capacity",
        "dimensions.weight",
        "dimensions.size",
    }
)


def _norm_dim_triple(s: str) -> list[float] | None:
    s = re.sub(r"\([^)]*\)", "", s)
    def _repl_range(m: re.Match[str]) -> str:
        v1, v2 = m.group(1), m.group(2)
        return v1 if v1 == v2 else f"{v1}-{v2}"
    s = re.sub(r"(\d+(?:\.\d+)?)\s*~\s*(\d+(?:\.\d+)?)", _repl_range, s)
    nums = [float(x) for x in re.findall(r"\b\d+(?:\.\d+)?\b", s)]
    if len(nums) >= 3:
        return sorted(nums[:3])
    return None


def are_specs_equivalent(field: str, val1: Any, val2: Any) -> bool:
    if val1 == val2:
        return True
    if is_empty(val1) or is_empty(val2):
        return True
    s1, s2 = str(val1).strip(), str(val2).strip()
    if s1.lower() == s2.lower():
        return True

    # processor.model: ignore brand prefix ("AMD", "Intel"), generation ("3rd Gen,"), "Mobile", "Processor"
    if field == "processor.model":
        def norm_cpu(s: str) -> str:
            s = re.sub(r"^(?:amd|intel|core)\s+", "", s, flags=re.I)
            s = re.sub(r"^(?:\d+(?:st|nd|rd|th)\s*gen(?:eration)?,?\s*)+", "", s, flags=re.I)
            s = re.sub(r"\b(mobile|processor|cpu)\b", "", s, flags=re.I)
            return re.sub(r"\s+", " ", s).strip().lower()
        return norm_cpu(s1) == norm_cpu(s2)

    # clock speeds: "3.5 GHz" vs "Up to 3.5 GHz"
    if "clock" in field:
        def norm_clock(s: str) -> str:
            s = re.sub(r"^(?:up\s*to|approx\.?|about|max\s*boost)\s*", "", s, flags=re.I)
            return re.sub(r"\s+", " ", s).strip().lower()
        return norm_clock(s1) == norm_clock(s2)

    # graphics.model:
    # 1) Brand prefix / graphics suffix stripping: "NVIDIA GeForce RTX 4060" vs "RTX 4060"
    # 2) Generic iGPU vs specific iGPU: "AMD Radeon Graphics" vs "AMD Radeon 610M", "Intel Graphics" vs "Intel Iris Xe Graphics"
    if field == "graphics.model":
        def norm_gpu(s: str) -> str:
            s = re.sub(r"\b(nvidia|geforce|amd|radeon|intel|arc)\b", "", s, flags=re.I)
            s = re.sub(r"\b(laptop\s*gpu|graphics)\b", "", s, flags=re.I)
            return re.sub(r"\s+", " ", s).strip().lower()
        if norm_gpu(s1) == norm_gpu(s2):
            return True
        def is_generic_igpu(s: str) -> bool:
            return re.search(r"\b(integrated|radeon graphics|intel graphics|intel uhd|intel hd)\b", s, re.I) is not None
        if is_generic_igpu(s1) and any(tok in s2.lower() for tok in ("radeon", "iris", "uhd", "610m", "780m", "680m", "arc")):
            return True
        if is_generic_igpu(s2) and any(tok in s1.lower() for tok in ("radeon", "iris", "uhd", "610m", "780m", "680m", "arc")):
            return True

    # dimensions.size
    if "dimensions" in field or "size" in field:
        t1 = _norm_dim_triple(s1)
        t2 = _norm_dim_triple(s2)
        if t1 and t2 and len(t1) == len(t2):
            return all(abs(a - b) < 0.06 for a, b in zip(t1, t2))

    # dimensions.weight: "1.8 kg" vs "1.80 kg"
    if "weight" in field:
        w1 = re.search(r"(\d+(?:\.\d+)?)\s*kg", s1, re.I)
        w2 = re.search(r"(\d+(?:\.\d+)?)\s*kg", s2, re.I)
        if w1 and w2:
            return abs(float(w1.group(1)) - float(w2.group(1))) < 0.01

    # display.resolution: "1920x1080" vs "1920 x 1080" vs "FHD (1920 x 1080)"
    if "resolution" in field:
        r1 = re.search(r"(\d{3,4})\s*[x×]\s*(\d{3,4})", s1)
        r2 = re.search(r"(\d{3,4})\s*[x×]\s*(\d{3,4})", s2)
        if r1 and r2:
            return r1.group(1) == r2.group(1) and r1.group(2) == r2.group(2)

    return False


def merge_section(
    section: str,
    current: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
    *,
    origin: str,
    current_origin: str = "",
    conflicts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge one manufacturer spec section over the existing one.

    An authoritative origin replaces conflicting values; anything weaker only
    fills blanks. Disagreements on core hardware specs are recorded under
    ``spec_conflicts``. Weaker manufacturer tiers never log conflicts against
    earlier manufacturer tiers.
    """
    merged = dict(current or {})
    if not incoming:
        return merged

    authoritative = origin in AUTHORITATIVE_ORIGINS
    # Never let a weaker tier overwrite a stronger one applied earlier.
    if current_origin and _origin_rank(origin) > _origin_rank(current_origin):
        authoritative = False

    for key, value in incoming.items():
        if is_empty(value):
            continue
        existing = merged.get(key)
        if is_empty(existing):
            merged[key] = value
            continue
        if are_specs_equivalent(f"{section}.{key}", existing, value):
            continue

        # If incoming tier is not authoritative, weaker tiers only fill blanks.
        # Disagreements between an incoming manufacturer tier and a retailer value
        # are logged as conflicts. But disagreements between two manufacturer tiers
        # (e.g. weaker family tier vs stronger exact tier) are never retailer conflicts.
        if not authoritative:
            if current_origin in MANUFACTURER_ORIGINS:
                continue
            field_name = f"{section}.{key}"
            if conflicts is not None and field_name in CONFLICT_TRACKED_FIELDS:
                if not any(c.get("field") == field_name for c in conflicts):
                    conflicts.append(
                        {
                            "field": field_name,
                            "manufacturer": value,
                            "retailer": existing,
                            "manufacturer_origin": origin,
                            "chosen": "retailer",
                        }
                    )
            continue

        # Authoritative tier (e.g. exact) overwrites retailer / weaker family value.
        # Record conflict only if overwriting a non-manufacturer retailer value.
        if current_origin not in MANUFACTURER_ORIGINS:
            field_name = f"{section}.{key}"
            if conflicts is not None and field_name in CONFLICT_TRACKED_FIELDS:
                if not any(c.get("field") == field_name for c in conflicts):
                    conflicts.append(
                        {
                            "field": field_name,
                            "manufacturer": value,
                            "retailer": existing,
                            "manufacturer_origin": origin,
                            "chosen": "manufacturer",
                        }
                    )
        merged[key] = value
    return merged


def apply_manufacturer_specs(
    product: dict[str, Any],
    sections: dict[str, dict[str, Any]],
    *,
    origin: str,
) -> dict[str, Any]:
    """Apply a manufacturer spec bundle to a product, in place.

    Records ``spec_origin`` per section and appends any disagreement to
    ``spec_conflicts`` so a wrong retailer parse stays visible instead of being
    silently overwritten.
    """
    if not sections:
        return product

    tech_specs = product.get("tech_specs")
    if not isinstance(tech_specs, dict):
        tech_specs = {}
        product["tech_specs"] = tech_specs

    origins = product.get("spec_origin")
    if not isinstance(origins, dict):
        origins = {}
        product["spec_origin"] = origins

    conflicts = product.get("spec_conflicts")
    if not isinstance(conflicts, list):
        conflicts = []

    for section, incoming in sections.items():
        if not isinstance(incoming, dict) or not incoming:
            continue
        before = tech_specs.get(section)
        current_origin = str(origins.get(section) or "")
        merged = merge_section(
            section,
            before if isinstance(before, dict) else {},
            incoming,
            origin=origin,
            current_origin=current_origin,
            conflicts=conflicts,
        )
        if merged == before:
            continue
        tech_specs[section] = merged
        # Only claim the stronger origin when it actually contributed.
        if not current_origin or _origin_rank(origin) < _origin_rank(current_origin):
            origins[section] = origin

    if conflicts:
        product["spec_conflicts"] = conflicts
    return product


def resolve_specs(
    *,
    exact: dict[str, dict[str, Any]] | None = None,
    family: dict[str, dict[str, Any]] | None = None,
    family_constructed: dict[str, dict[str, Any]] | None = None,
    exact_origin: str = ORIGIN_ASUS_EXACT,
    family_origin: str = ORIGIN_ASUS_FAMILY,
) -> list[tuple[dict[str, Any], str]]:
    """Order the available manufacturer evidence strongest-first.

    Returned as (sections, origin) pairs for the caller to feed through
    :func:`apply_manufacturer_specs`; applying in order means an exact match
    lands before any family fallback can claim the same section.
    """
    ladder: list[tuple[dict[str, Any], str]] = []
    if exact:
        ladder.append((exact, exact_origin))
    if family:
        ladder.append((family, family_origin))
    if family_constructed:
        ladder.append((family_constructed, ORIGIN_ASUS_FAMILY_CONSTRUCTED))
    return ladder
