"""PSREF-based spec enrichment for Amazon-sold Lenovo listings.

Amazon listings for Lenovo machines carry weaker specs than the manufacturer
publishes: ``power.watt`` is absent from every row, ``display.refresh`` is
missing from a quarter of them, and ``battery``/``ports`` are sparse. Lenovo
publishes PSREF datasheets per machine type; those datasheets are the source of
truth we contract against here.

Confidence is deliberately two-tiered. A single machine type spans up to ~136
distinct configurations (CPU x GPU x memory x storage x display), so guessing a
configuration from the machine type alone would invent specs for the other 135:

* **psref_exact** — the listing's own SKU appears verbatim in a datasheet's
  ``models`` dict.  This resolves a *true* configuration via the model's
  ``spec_refs`` into the datasheet's ``spec_pool`` (processor, graphics,
  memory, storage, display) and therefore may overwrite a conflicting retailer
  value (authoritative origin in ``manufacturer.ORIGIN_PRECEDENCE``).
* **psref_platform** — only the listing's machine-type prefix is known. The
  datasheet's ``platform_defaults`` sections (power, battery, ports, network,
  memory_slots, ...) are published identically for every configuration in the
  family, so publishing them is safe, and they only fill gaps — the weaker
  family tier never overwrites a retailer reading. Config-varying fields are
  never emitted from this tier.

All merge/conflict semantics are delegated to
:func:`laptopdeals.manufacturer.apply_manufacturer_specs`; this module never
implements its own merge.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from laptopdeals.manufacturer import (
    ORIGIN_PSREF_EXACT,
    ORIGIN_PSREF_PLATFORM,
    apply_manufacturer_specs,
    resolve_specs,
)
from laptopdeals.psref import hydrate_sku_specs
from laptopdeals.specs import parse_display_psref

# A Lenovo SKU: a 2-digit year prefix followed by a machine-type code and the
# variant/suffix, all alphanumeric. Examples seen in the wild:
# "82C70005UK", "21M3S0QV00", "21M6CTO1WWIN1". Machine types themselves are
# 4 chars that may mix digits and letters ("21M3", "21MV", "82XQ", "83DV").
# We additionally require at least two letters in the whole token so a bare
# number like "20260808" can never match.
_SKU_TOKEN_RE = re.compile(r"(?<![A-Z0-9])(\d{2}[A-Z0-9]{5,12})(?![A-Z0-9])", re.I)

# The 15 platform_defaults sections — identical for every configuration of a
# machine type, therefore safe to publish for a machine-type-only match.
PLATFORM_DEFAULT_SECTIONS = frozenset(
    {
        "audio",
        "battery",
        "build",
        "camera",
        "certifications",
        "dimensions",
        "keyboard",
        "memory_slots",
        "network",
        "ports",
        "power",
        "security",
        "software",
        "storage_slots",
        "warranty",
    }
)

# Fields that vary across configurations. The platform tier must NEVER emit
# these; they are only resolved by an exact match into the spec_pool.
CONFIGURATION_SECTIONS = frozenset({"processor", "graphics", "memory", "storage", "display"})

TIER_EXACT = "psref_exact"
TIER_PLATFORM = "psref_platform"


def extract_lenovo_sku(product: dict[str, Any]) -> str | None:
    """Return an uppercase Lenovo SKU borne by the product, if any.

    The listing's ``internal_model_code`` is the most reliable source. Many
    Amazon listings in the wild only carry the SKU inside the title text, so
    that is used as a lower-priority fallback. Returns ``None`` when the
    product carries no SKU-shaped code at all.
    """
    candidates: list[str] = []
    for field in ("internal_model_code", "model_name", "title", "product_code", "id"):
        value = product.get(field)
        if isinstance(value, str) and value.strip():
            candidates.append(value.upper())
    for text in candidates:
        for match in _SKU_TOKEN_RE.finditer(text):
            token = match.group(1)
            if sum(char.isalpha() for char in token) < 2:
                continue  # a pure-digit string is never a Lenovo SKU
            return token
    return None


def machine_types_for(products: Iterable[dict[str, Any]]) -> list[str]:
    """The PSREF machine types a batch of listings would need, deduplicated.

    Keeps the "SKU's first four characters are the machine type" rule in the one
    module that owns it, so an on-demand fetch asks for exactly the datasheets
    the catalog references instead of walking the whole menu cache.
    """
    seen: set[str] = set()
    for product in products:
        if not isinstance(product, dict):
            continue
        sku = extract_lenovo_sku(product)
        if sku and len(sku) >= 4:
            seen.add(sku[:4].upper())
    return sorted(seen)


def _normalize_psref_specs(specs: dict[str, Any]) -> dict[str, Any]:
    """Map a PSREF detailed-spec bundle onto the canonical tech_specs shape.

    Mirrors ``laptopdeals.providers.lenovo._normalize_psref_specs`` (kept
    self-contained here so this enrichment module does not depend on that
    provider's import side effects). It consumes either a full config row
    (exact tier: spec_pool resolutions + platform_defaults) or the
    platform_defaults alone (platform tier) and emits only the sections the
    canonical contract understands.
    """
    out: dict[str, Any] = {}
    proc = specs.get("processor") or {}
    if proc:
        out["processor"] = {
            "brand": proc.get("brand", ""),
            "model": proc.get("model", ""),
            "cores": proc.get("cores"),
            "threads": proc.get("threads"),
            "base_clock": f"{proc['base_clock_ghz']} GHz" if proc.get("base_clock_ghz") else "",
            "boost_clock": f"{proc['boost_clock_ghz']} GHz" if proc.get("boost_clock_ghz") else "",
        }
    gpu = specs.get("graphics") or {}
    if gpu:
        out["graphics"] = {
            "model": gpu.get("model", ""),
            "vram": f"{gpu['vram_gb']} GB" if gpu.get("vram_gb") else ("Shared" if not gpu.get("dedicated") else ""),
            "dedicated": gpu.get("dedicated", False),
            "boost_clock": f"{gpu['boost_clock_mhz']} MHz" if gpu.get("boost_clock_mhz") else "",
            "tgp": f"{gpu['tgp_w']}W" if gpu.get("tgp_w") else "",
            "ai_tops": gpu.get("ai_tops"),
        }
    mem = specs.get("memory") or {}
    if mem:
        out["memory"] = {
            "amount": mem.get("amount", ""),
            "type": mem.get("type", ""),
            "speed": mem.get("speed", ""),
            "slots_used": mem.get("slots_populated"),
            "soldered": mem.get("soldered", False),
        }
    sto = specs.get("storage") or {}
    if sto:
        out["storage"] = {"capacity": sto.get("capacity", ""), "type": sto.get("type", "")}
    dpy = specs.get("display") or {}
    if dpy:
        color = dpy.get("color", "")
        if not color and dpy.get("raw"):
            color = parse_display_psref(dpy["raw"]).get("color", "")
        out["display"] = {
            "size": dpy.get("size", ""),
            "resolution": dpy.get("resolution_name", "") or dpy.get("resolution", ""),
            "type": dpy.get("type", ""),
            "refresh": dpy.get("refresh", ""),
            "brightness": dpy.get("brightness", ""),
            "color": color,
            "touch": dpy.get("touch", ""),
            "surface": dpy.get("surface", ""),
        }
    net = specs.get("network") or {}
    if net:
        out["network"] = {"wifi": net.get("wifi", ""), "bluetooth": net.get("bluetooth", "")}
    pwr = specs.get("power") or {}
    if pwr:
        out["power"] = {"adapter": pwr.get("adapter", ""), "watt": pwr.get("watt")}
    batt = specs.get("battery") or {}
    if batt:
        out["battery"] = {"capacity": f"{batt['capacity_wh']}Wh" if batt.get("capacity_wh") else batt.get("raw", "")}
    ports = specs.get("ports") or {}
    if ports.get("items"):
        out["ports"] = {"items": ports["items"]}
    cam = specs.get("camera") or {}
    if cam.get("raw"):
        out["camera"] = {"model": cam["raw"]}
    kb = specs.get("keyboard") or {}
    if kb.get("raw"):
        out["keyboard"] = {"type": kb["raw"]}
    dim = specs.get("dimensions") or {}
    if dim:
        out["dimensions"] = {"size": dim.get("raw", ""), "weight": dim.get("weight", "")}
    build = specs.get("build") or {}
    if build:
        out["build"] = {k: v for k, v in build.items() if v}
    sw = specs.get("software") or {}
    if sw:
        out["software"] = {k: v for k, v in sw.items() if v}
    audio = specs.get("audio") or {}
    if audio:
        out["audio"] = {k: v for k, v in audio.items() if v}
    ms = specs.get("memory_slots") or {}
    if ms:
        out["memory_slots"] = {k: v for k, v in ms.items() if v}
    ss = specs.get("storage_slots") or {}
    if ss:
        out["storage_slots"] = {k: v for k, v in ss.items() if v}
    return out


def _platform_bundle(platform_defaults: dict[str, Any]) -> dict[str, Any]:
    """Isolate the safe-to-publish family sections from a datasheet.

    Filters ``platform_defaults`` down to the static family sections and, as a
    hard guarantee, strips anything that varies by configuration — even if a
    future datasheet were to mislabel one, it must never reach a platform row.
    """
    return {
        key: value
        for key, value in (platform_defaults or {}).items()
        if key in PLATFORM_DEFAULT_SECTIONS and key not in CONFIGURATION_SECTIONS and isinstance(value, dict) and value
    }


def _resolve_config_specs(datasheet: dict[str, Any], sku: str) -> dict[str, Any]:
    """Resolve a verbatim SKU to its true configuration's detailed specs.

    Returns a dict of the five config-varying sections (processor, graphics,
    memory, storage, display) in the PSREF detailed shape, or ``{}`` when the
    SKU names no resolvable configuration.
    """
    model = (datasheet.get("models") or {}).get(sku, {})
    spec_refs = model.get("spec_refs") or {}
    if not spec_refs:
        return {}
    detailed = hydrate_sku_specs(
        spec_refs,
        datasheet.get("spec_pool") or {},
        datasheet.get("platform_defaults") or {},
    )
    return {key: value for key, value in detailed.items() if key in CONFIGURATION_SECTIONS}


def resolve_lenovo_tier(
    product: dict[str, Any],
    *,
    psref_index: "PsrefIndex",
) -> tuple[str, str, dict[str, Any]] | None:
    """Classify a Lenovo product into the tier PSREF can serve.

    Returns ``(tier, sku, datasheet)`` when a datasheet covers the machine:
    ``psref_exact`` if the SKU names a specific configuration, ``psref_platform``
    if only the family's machine type can be claimed. ``None`` when there is no
    usable SKU or no datasheet for its machine type (retired hardware).
    """
    sku = extract_lenovo_sku(product)
    if not sku or len(sku) < 4:
        return None
    datasheet = psref_index.datasheet_for(sku[:4])
    if datasheet is None:
        return None
    if sku in (datasheet.get("models") or {}):
        return TIER_EXACT, sku, datasheet
    return TIER_PLATFORM, sku, datasheet


def enrich_lenovo_row(product: dict[str, Any], *, psref_index: "PsrefIndex") -> bool:
    """Enrich ``product`` in place from its PSREF datasheet when available.

    Applies either the ``psref_exact`` or ``psref_platform`` tier through
    ``apply_manufacturer_specs`` (which owns all merge/conflict decisions),
    and records a small provenance note under ``product["psref_enrichment"]``.
    Returns ``True`` if any PSREF section actually changed the product.
    """
    result = resolve_lenovo_tier(product, psref_index=psref_index)
    if result is None:
        return False
    tier, sku, datasheet = result
    platform_sections = _normalize_psref_specs(_platform_bundle(datasheet.get("platform_defaults") or {}))
    if tier == TIER_EXACT:
        config_detailed = _resolve_config_specs(datasheet, sku)
        config_sections = _normalize_psref_specs(config_detailed)
        ladder = resolve_specs(
            exact=config_sections,
            exact_origin=ORIGIN_PSREF_EXACT,
            family=platform_sections,
            family_origin=ORIGIN_PSREF_PLATFORM,
        )
        changed = _apply_ladder(product, ladder)
    else:
        changed = _apply_ladder(product, [(platform_sections, ORIGIN_PSREF_PLATFORM)] if platform_sections else [])
    if not changed:
        return False
    product["psref_enrichment"] = {
        "tier": tier,
        "sku": sku,
        "machine_type": sku[:4],
        "product_name": datasheet.get("product_name", ""),
    }
    return True


def _apply_ladder(product: dict[str, Any], ladder: list[tuple[dict[str, Any], str]]) -> bool:
    """Apply (sections, origin) pairs strongest-first; return True if any changed.

    A config resolution (origin ``psref_exact``) is authoritative and may
    overwrite a conflicting retailer value; the family platform sections
    (origin ``psref_platform``) only fill blanks, never displacing a reading
    taken from the machine's own listing.
    """
    had_origin = "spec_origin" in product
    had_conflicts = list(product.get("spec_conflicts") or [])
    changed = False
    for sections, origin in ladder:
        before = dict(product.get("tech_specs") or {})
        apply_manufacturer_specs(product, sections, origin=origin)
        if product.get("tech_specs") != before:
            changed = True
    # A family-vs-retailer disagreement recorded in spec_conflicts is real
    # information even when the retailer value is kept, so it counts too.
    if len(product.get("spec_conflicts") or []) > len(had_conflicts):
        changed = True
    if not changed:
        # No-op apply still stamps an empty spec_origin; drop it so a False
        # return really means the product was left untouched.
        if not had_origin:
            product.pop("spec_origin", None)
        if not had_conflicts:
            product.pop("spec_conflicts", None)
    return changed


class PsrefIndex:
    """In-memory index of Lenovo PSREF datasheets, keyed by machine type."""

    def __init__(self, datasheets: dict[str, dict[str, Any]], machine_type_map: dict[str, Any] | None = None) -> None:
        self._datasheets: dict[str, dict[str, Any]] = {}
        for machine_type, datasheet in (datasheets or {}).items():
            if isinstance(machine_type, str) and isinstance(datasheet, dict):
                self._datasheets[machine_type.upper()] = datasheet
        self.machine_type_map: dict[str, Any] = dict(machine_type_map or {})

    @classmethod
    def from_directory(cls, datasheets_dir: Path | str, *, machine_type_map: Path | str | None = None) -> "PsrefIndex":
        """Load every ``{machine_type}.json`` datasheet from a directory once.

        ``machine_type_map`` points at the sibling machine-type menu cache
        (``machine_type_map.json``) used only for on-demand fetching; when a
        path is given and the file sits directly next to the datasheets dir,
        it is located automatically.
        """
        directory = Path(datasheets_dir)
        if not directory.is_dir():
            return cls({})
        datasheets: dict[str, dict[str, Any]] = {}
        for path in sorted(directory.glob("*.json")):
            payload = _read_json(path)
            if not isinstance(payload, dict):
                continue
            machine_type = payload.get("machine_type") or path.stem
            datasheets[str(machine_type).upper()] = payload
        entry = cls(datasheets)
        resolved_map: Path | None = None
        if machine_type_map is None:
            sibling = directory.parent / "machine_type_map.json"
            if sibling.exists():
                resolved_map = sibling
        elif isinstance(machine_type_map, (Path, str)):
            resolved_map = Path(machine_type_map)
        if resolved_map is not None:
            raw = _read_json(resolved_map)
            if isinstance(raw, dict):
                entry.machine_type_map = {str(k).upper(): v for k, v in raw.items()}
        return entry

    def datasheet_for(self, machine_type: str) -> dict[str, Any] | None:
        """Return the datasheet for a 4-char machine type, or ``None``.

        Only meaningful for the first four characters of a real SKU; a machine
        type absent from PSREF (retired hardware) returns ``None``.
        """
        if not isinstance(machine_type, str):
            return None
        return self._datasheets.get(machine_type.upper()[:4])

    def __contains__(self, machine_type: object) -> bool:
        return isinstance(machine_type, str) and machine_type.upper()[:4] in self._datasheets

    def missing_machine_types(self) -> list[str]:
        """Machine types catalogued in the menu cache but with no datasheet."""
        return sorted(mt for mt in self.machine_type_map if mt not in self._datasheets)

    def fetch_missing_datasheets(
        self,
        *,
        write_dir: Path | str | None = None,
        limit: int | None = None,
        only: Iterable[str] | None = None,
    ) -> list[str]:
        """Fetch datasheets for catalogued machine types missing on disk.

        Reuses ``psref.fetch_mt_model_data_json`` / ``psref.build_mt_datasheet``
        so a missing machine type can be pulled on demand. Requires network and
        must never be called from tests. Returns the machine types fetched.

        ``only`` restricts the fetch to the machine types a caller actually
        needs. Pass it whenever this is driven from a catalog build: the live
        menu cache lists 1037 machine types of which 915 have no datasheet on
        disk, while an Amazon catalog references only ~14 of them. Without
        ``only``, ``limit`` would truncate that 915 in *alphabetical* order and
        fetch machine types nothing in the catalog asks for.
        """
        from laptopdeals.psref import build_mt_datasheet, fetch_mt_model_data_json, resolve_mt_via_suggest
        from laptopdeals.jsonio import write_json

        wanted = {str(item).upper()[:4] for item in only if str(item).strip()} if only is not None else None
        fetched: list[str] = []
        map_updated = False

        for machine_type in sorted(self.machine_type_map):
            ds = self._datasheets.get(machine_type)
            if ds and (ds.get("platform_defaults") or ds.get("models")):
                continue
            if wanted is not None and machine_type not in wanted:
                continue
            entry = self.machine_type_map[machine_type]
            product_key = entry.get("product_key") if isinstance(entry, dict) else ""
            if not product_key:
                continue
            try:
                rows, filter_options = fetch_mt_model_data_json(product_key)
            except Exception:
                continue
            mt_rows = [row for row in rows if str(row.get("Machine Type") or row.get("machine_type") or "").upper() == machine_type] or rows
            if not mt_rows and not filter_options:
                continue
            datasheet = build_mt_datasheet(machine_type, mt_rows, entry, filter_options=filter_options)
            self._datasheets[machine_type] = datasheet
            if write_dir is not None:
                path = Path(write_dir)
                path.mkdir(parents=True, exist_ok=True)
                write_json(path / f"{machine_type}.json", datasheet)
            fetched.append(machine_type)
            if limit is not None and len(fetched) >= limit:
                break

        # Check extra wanted MTs not in machine_type_map (e.g. withdrawn models)
        if wanted is not None:
            extra_wanted = [mt for mt in sorted(wanted) if mt not in self.machine_type_map and (limit is None or len(fetched) < limit)]
            for machine_type in extra_wanted:
                ds = self._datasheets.get(machine_type)
                if ds and (ds.get("platform_defaults") or ds.get("models")):
                    continue
                suggested = resolve_mt_via_suggest(machine_type)
                if not suggested or not suggested.get("product_key"):
                    continue
                self.machine_type_map[machine_type] = suggested
                map_updated = True
                product_key = suggested.get("product_key")
                try:
                    rows, filter_options = fetch_mt_model_data_json(product_key)
                except Exception:
                    continue
                mt_rows = [row for row in rows if str(row.get("Machine Type") or row.get("machine_type") or "").upper() == machine_type] or rows
                if not mt_rows and not filter_options:
                    continue
                datasheet = build_mt_datasheet(machine_type, mt_rows, suggested, filter_options=filter_options)
                self._datasheets[machine_type] = datasheet
                if write_dir is not None:
                    path = Path(write_dir)
                    path.mkdir(parents=True, exist_ok=True)
                    write_json(path / f"{machine_type}.json", datasheet)
                fetched.append(machine_type)
                if limit is not None and len(fetched) >= limit:
                    break

        if map_updated and write_dir is not None:
            map_path = Path(write_dir).parent / "machine_type_map.json"
            if map_path.parent.exists():
                write_json(map_path, self.machine_type_map)

        return fetched


def _read_json(path: Path) -> Any:
    from laptopdeals.jsonio import read_json

    return read_json(path)