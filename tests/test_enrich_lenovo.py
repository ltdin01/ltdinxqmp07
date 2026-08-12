from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from laptopdeals import enrich_lenovo as el
from laptopdeals import psref as el_psref
from laptopdeals.manufacturer import ORIGIN_PSREF_EXACT, ORIGIN_PSREF_PLATFORM

# ---------------------------------------------------------------------------
# Synthetic fixtures. A datasheet is deliberately small but structurally
# faithful to the real PSREF payloads: a spec_pool of the five config-varying
# categories, static platform_defaults, and a models dict of SKU -> spec_refs.
# ---------------------------------------------------------------------------

POOL = {
    "processors": {
        "cpu_1": {
            "id": "cpu_1",
            "raw": "AMD Ryzen 7 7735HS (8C / 16T, 3.2 / 4.75GHz, 4MB L2 / 16MB L3)",
            "normalized": {
                "raw": "AMD Ryzen 7 7735HS (8C / 16T, 3.2 / 4.75GHz, 4MB L2 / 16MB L3)",
                "brand": "AMD",
                "model": "Ryzen 7 7735HS",
                "full_model": "AMD Ryzen 7 7735HS",
                "cores": 8,
                "threads": 16,
                "base_clock_ghz": 3.2,
                "boost_clock_ghz": 4.75,
            },
        }
    },
    "graphics": {
        "gpu_1": {
            "id": "gpu_1",
            "raw": "Integrated AMD Radeon 680M Graphics",
            "normalized": {
                "raw": "Integrated AMD Radeon 680M Graphics",
                "brand": "AMD",
                "model": "Integrated AMD Radeon 680M Graphics",
                "full_model": "AMD Integrated AMD Radeon 680M Graphics",
                "dedicated": False,
            },
        }
    },
    "memory": {
        "mem_1": {
            "id": "mem_1",
            "raw": "1x 16GB SODIMM DDR5-4800",
            "normalized": {
                "raw": "1x 16GB SODIMM DDR5-4800",
                "amount": "16 GB",
                "amount_gb": 16,
                "type": "DDR5",
                "speed": "4800 MHz",
                "speed_mhz": 4800,
                "slots_populated": 1,
                "soldered": False,
            },
        }
    },
    "storage": {
        "sto_1": {
            "id": "sto_1",
            "raw": "512GB SSD M.2 2242 PCIe 4.0x4 NVMe Opal 2.0",
            "normalized": {
                "raw": "512GB SSD M.2 2242 PCIe 4.0x4 NVMe Opal 2.0",
                "capacity": "512 GB",
                "capacity_gb": 512,
                "type": "NVMe SSD",
            },
        }
    },
    "displays": {
        "dpy_1": {
            "id": "dpy_1",
            "raw": '14" WUXGA (1920x1200) IPS 300nits Anti-glare, 45% NTSC, 60 Hz',
            "normalized": {
                "raw": '14" WUXGA (1920x1200) IPS 300nits Anti-glare, 45% NTSC, 60 Hz',
                "size": '14"',
                "size_inches": 14,
                "resolution": "1920x1200",
                "resolution_name": "WUXGA",
                "type": "IPS",
                "brightness": "300 nits",
                "brightness_nits": 300,
                "refresh": "60Hz",
                "refresh_hz": 60,
                "color": "45% NTSC",
                "touch": "No",
                "surface": "Anti-glare",
            },
        }
    },
}

PLATFORM_DEFAULTS = {
    "network": {"raw": "Wi-Fi 6E, 802.11ax 2x2 + BT5.3", "wifi": "Wi-Fi 6E", "bluetooth": "5.3"},
    "power": {"raw": "65W USB-C (3-pin)", "adapter": "65W USB-C (3-pin)", "watt": 65},
    "battery": {"raw": "57Wh", "capacity_wh": 57},
    "ports": {
        "raw": "1x USB-A^|^1x HDMI 2.1^|^1x Ethernet (RJ-45)",
        "items": ["1x USB-A", "1x HDMI 2.1", "1x Ethernet (RJ-45)"],
    },
    "memory_slots": {"raw": "Two DDR5 SODIMM slots", "max_memory": "Up to 64GB DDR5-4800"},
    "storage_slots": {"raw": "Two M.2 slots", "max_storage": "Up to two drives"},
    "camera": {"raw": "FHD 1080p with Privacy Shutter"},
    "audio": {"chip": "HD Audio", "speakers": "Stereo speakers, 2W x2", "microphone": "2x, Array"},
    "keyboard": {"raw": "Backlit, English"},
    "dimensions": {"raw": "313 x 219.3 x 18.59 mm", "weight": "Starting at 1.42 kg"},
    "build": {"color": "Black", "material": "Aluminium (Top), PC-ABS (Bottom)"},
    "software": {"os": "Windows 11 Home, English", "bundled": "None"},
    "security": {"chip": "Discrete TPM 2.0 Enabled", "fingerprint": "Touch Style", "other": "Camera privacy shutter"},
    "warranty": {"base": "1-year, Courier or Carry-in", "upgrade": "1Y Onsite upgrade"},
    "certifications": {"green": ["ENERGY STAR 9.0"], "mil_spec": "MIL-STD-810H", "other": ["TUV Rheinland"]},
}


def make_datasheet(machine_type: str = "21M3") -> dict:
    return {
        "machine_type": machine_type,
        "product_key": "ThinkPad_E14_Gen_6_AMD",
        "product_name": "ThinkPad E14 Gen 6 (AMD)",
        "spec_pool": POOL,
        "platform_defaults": PLATFORM_DEFAULTS,
        "models": {
            "21M3S0QV00": {
                "country_region": "India",
                "match_type": "exact",
                "psref_model": "21M3S0QV00",
                "psref_product": "ThinkPad E14 Gen 6 (AMD)",
                "spec_refs": {
                    "processor": "cpu_1",
                    "graphics": "gpu_1",
                    "memory": "mem_1",
                    "storage": "sto_1",
                    "display": "dpy_1",
                },
            }
        },
    }


def make_product(**overrides) -> dict:
    base = {
        "id": "AMZ-B0TEST",
        "internal_model_code": None,
        "model_name": "TestPad E14",
        "title": "Lenovo ThinkPad E14 Gen 6 Laptop",
        "tech_specs": {},
    }
    base.update(overrides)
    return base


def build_index(*datasheets: dict) -> el.PsrefIndex:
    return el.PsrefIndex({ds["machine_type"]: ds for ds in datasheets})


class TestExtractLenovoSku(unittest.TestCase):
    def test_from_internal_model_code(self):
        product = make_product(internal_model_code="21M3S0QV00")
        self.assertEqual(el.extract_lenovo_sku(product), "21M3S0QV00")

    def test_case_insensitive(self):
        product = make_product(internal_model_code="21m3s0qv00")
        self.assertEqual(el.extract_lenovo_sku(product), "21M3S0QV00")

    def test_falls_back_to_title(self):
        product = make_product(title="Lenovo LOQ 15, 83DV00HBIN 15.6in")
        self.assertEqual(el.extract_lenovo_sku(product), "83DV00HBIN")

    def test_cto_sku(self):
        product = make_product(internal_model_code="21M6CTO1WWIN1")
        self.assertEqual(el.extract_lenovo_sku(product), "21M6CTO1WWIN1")

    def test_rejects_non_sku_values(self):
        product = make_product(internal_model_code="LOQ 15IRX9", title="Lenovo LOQ 15")
        self.assertIsNone(el.extract_lenovo_sku(product))

    def test_no_sku_at_all(self):
        product = make_product(internal_model_code=None, title="Lenovo IdeaPad 3 Ryzen 5 laptop")
        self.assertIsNone(el.extract_lenovo_sku(product))

    def test_non_lenovo_code_does_not_match(self):
        product = make_product(internal_model_code="B0872G2MPV")
        self.assertIsNone(el.extract_lenovo_sku(product))


class TestExactTier(unittest.TestCase):
    def setUp(self):
        self.index = build_index(make_datasheet())

    def test_resolves_true_config_and_family_platform(self):
        product = make_product(internal_model_code="21M3S0QV00")
        self.assertTrue(el.enrich_lenovo_row(product, psref_index=self.index))

        ts = product["tech_specs"]
        # Configuration sections resolved from the spec_pool, authoritative.
        self.assertEqual(ts["processor"]["model"], "Ryzen 7 7735HS")
        self.assertEqual(ts["processor"]["cores"], 8)
        self.assertEqual(ts["memory"]["amount"], "16 GB")
        self.assertEqual(ts["display"]["refresh"], "60Hz")
        self.assertEqual(ts["display"]["type"], "IPS")
        self.assertEqual(product["spec_origin"]["processor"], ORIGIN_PSREF_EXACT)
        # Family platform sections filled, but only as the weaker origin.
        self.assertEqual(ts["power"]["watt"], 65)
        self.assertEqual(ts["battery"]["capacity"], "57Wh")
        self.assertEqual(ts["ports"]["items"], ["1x USB-A", "1x HDMI 2.1", "1x Ethernet (RJ-45)"])
        self.assertEqual(product["spec_origin"]["power"], ORIGIN_PSREF_PLATFORM)
        self.assertEqual(product["spec_origin"]["battery"], ORIGIN_PSREF_PLATFORM)
        self.assertEqual(product["psref_enrichment"]["tier"], "psref_exact")

    def test_authoritative_config_overwrites_retailer(self):
        product = make_product(
            internal_model_code="21M3S0QV00",
            tech_specs={"processor": {"brand": "Intel", "model": "Core i5-1135G"}},
        )
        el.enrich_lenovo_row(product, psref_index=self.index)
        self.assertEqual(product["tech_specs"]["processor"]["model"], "Ryzen 7 7735HS")
        self.assertEqual(product["spec_origin"]["processor"], ORIGIN_PSREF_EXACT)

    def test_platform_family_never_displaces_a_retailer_power(self):
        product = make_product(
            internal_model_code="21M3S0QV00",
            tech_specs={"power": {"adapter": "90W", "watt": 90}},
        )
        el.enrich_lenovo_row(product, psref_index=self.index)
        # A family adapter is weaker evidence than the machine's own listing.
        self.assertEqual(product["tech_specs"]["power"]["watt"], 90)
        self.assertEqual(product["tech_specs"]["power"]["adapter"], "90W")
        conflicts = product.get("spec_conflicts", [])
        self.assertTrue(any(c["field"] == "power.watt" and c["chosen"] == "retailer" for c in conflicts))

    def test_exact_uses_spec_refs_only_when_present(self):
        datasheet = make_datasheet()
        datasheet["models"]["21M3S0QV00"]["spec_refs"] = {}  # no config resolvable
        product = make_product(internal_model_code="21M3S0QV00")
        ok = el.enrich_lenovo_row(product, psref_index=build_index(datasheet))
        self.assertTrue(ok)
        ts = product["tech_specs"]
        self.assertNotIn("processor", ts)
        self.assertEqual(ts["power"]["watt"], 65)

    def test_lookup_returns_exact_tier(self):
        product = make_product(internal_model_code="21M3S0QV00")
        tier, sku, _ = el.resolve_lenovo_tier(product, psref_index=self.index)
        self.assertEqual(tier, "psref_exact")
        self.assertEqual(sku, "21M3S0QV00")


class TestPlatformTier(unittest.TestCase):
    def setUp(self):
        self.index = build_index(make_datasheet())

    def test_platform_fills_family_fields_only(self):
        # SKU belongs to the 21M3 family but is not among the model rows.
        product = make_product(internal_model_code="21M3X0Z999", tech_specs={})
        ok = el.enrich_lenovo_row(product, psref_index=self.index)
        self.assertTrue(ok)
        ts = product["tech_specs"]
        self.assertEqual(ts["power"]["watt"], 65)
        self.assertEqual(ts["power"]["adapter"], "65W USB-C (3-pin)")
        self.assertEqual(ts["battery"]["capacity"], "57Wh")
        self.assertEqual(ts["ports"]["items"], ["1x USB-A", "1x HDMI 2.1", "1x Ethernet (RJ-45)"])
        self.assertEqual(product["spec_origin"]["power"], ORIGIN_PSREF_PLATFORM)
        self.assertEqual(product["psref_enrichment"]["tier"], "psref_platform")

    def test_never_writes_config_sections(self):
        product = make_product(internal_model_code="21M3X0Z999", tech_specs={})
        el.enrich_lenovo_row(product, psref_index=self.index)
        for section in ("processor", "graphics", "memory", "storage", "display"):
            self.assertNotIn(section, product["tech_specs"])

    def test_family_never_overwrites_retailer_config(self):
        product = make_product(
            internal_model_code="21M3X0Z999",
            tech_specs={"graphics": {"model": "RTX 3050 6GB", "dedicated": True}},
        )
        el.enrich_lenovo_row(product, psref_index=self.index)
        self.assertEqual(product["tech_specs"]["graphics"]["model"], "RTX 3050 6GB")
        self.assertNotIn("graphics", product["spec_origin"])

    def test_lookup_returns_platform_tier(self):
        product = make_product(internal_model_code="21M3X0Z999")
        tier, sku, _ = el.resolve_lenovo_tier(product, psref_index=self.index)
        self.assertEqual(tier, "psref_platform")
        self.assertEqual(sku, "21M3X0Z999")

    def test_conflicting_retailer_value_recorded_but_kept(self):
        # Family evidence disagrees with the machine's own listing: the value
        # is kept but the disagreement is surfaced, and counts as a change.
        product = make_product(
            internal_model_code="21M3X0Z999",
            tech_specs={"power": {"adapter": "90W 3-Pin", "watt": 90}},
        )
        self.assertTrue(el.enrich_lenovo_row(product, psref_index=self.index))
        self.assertEqual(product["tech_specs"]["power"]["watt"], 90)
        self.assertEqual(product["tech_specs"]["power"]["adapter"], "90W 3-Pin")
        conflicts = product.get("spec_conflicts", [])
        self.assertTrue(any(c["field"] == "power.watt" and c["chosen"] == "retailer" for c in conflicts))
        self.assertEqual(product["psref_enrichment"]["tier"], "psref_platform")


class TestPlatformNeverEmitsConfig(unittest.TestCase):
    """The hard guarantee: machine-type-only matches never write config fields.

    We deliberately corrupt the fixture so the platform_defaults block DOES
    contain processor/graphics/memory/storage/display entries — as if a future
    datasheet mislabeled them — and assert they are still never emitted.
    """

    def test_config_keys_inside_platform_defaults_are_stripped(self):
        ds = make_datasheet()
        ds["platform_defaults"]["processor"] = {"brand": "AMD", "model": "Ryzen 7 9999", "cores": 99}
        ds["platform_defaults"]["display"] = {"type": "IPS", "refresh": "240Hz"}
        product = make_product(internal_model_code="21M3X0Z999")
        el.enrich_lenovo_row(product, psref_index=build_index(ds))
        self.assertNotIn("processor", product["tech_specs"])
        self.assertNotIn("display", product["tech_specs"])
        # The legitimate platform sections still flow.
        self.assertEqual(product["tech_specs"]["power"]["watt"], 65)


class TestNoMatch(unittest.TestCase):
    def setUp(self):
        self.index = build_index(make_datasheet())

    def test_no_sku(self):
        product = make_product(internal_model_code=None, title="Lenovo IdeaPad 3 generic")
        original = copy.deepcopy(product)
        self.assertFalse(el.enrich_lenovo_row(product, psref_index=self.index))
        self.assertEqual(product, original)

    def test_machine_type_not_in_psref(self):
        product = make_product(internal_model_code="AB1QET4TZ")
        self.assertFalse(el.enrich_lenovo_row(product, psref_index=self.index))

    def test_returns_false_when_everything_already_populated(self):
        # Re-running enrichment over an already-enriched row is a no-op.
        fully = el._normalize_psref_specs(el._platform_bundle(dict(PLATFORM_DEFAULTS)))
        product = make_product(internal_model_code="21M3X0Z999", tech_specs=fully)
        original = copy.deepcopy(product)
        self.assertFalse(el.enrich_lenovo_row(product, psref_index=self.index))
        self.assertEqual(product, original)


class TestPsrefIndex(unittest.TestCase):
    def test_from_directory_machines_load_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "datasheets").mkdir()
            (tmp / "datasheets" / "21M3.json").write_text(json.dumps(make_datasheet()), encoding="utf-8")
            (tmp / "datasheets" / "21XZ.json").write_text(json.dumps(make_datasheet("21XZ")), encoding="utf-8")
            (tmp / "machine_type_map.json").write_text(
                json.dumps({"21M3": {"product_key": "ThinkPad_E14_Gen_6_AMD"}, "99AB": {"product_key": "Unknown"}}),
                encoding="utf-8",
            )
            index = el.PsrefIndex.from_directory(tmp / "datasheets")
            self.assertEqual(len(index._datasheets), 2)
            self.assertIsNotNone(index.datasheet_for("21m3"))  # case-insensitive
            self.assertIsNotNone(index.datasheet_for("21XZ"))
            self.assertIsNone(index.datasheet_for("ZZZZ"))
            self.assertEqual(index.missing_machine_types(), ["99AB"])

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = el.PsrefIndex.from_directory(tmp)
            self.assertEqual(len(index._datasheets), 0)
            self.assertIsNone(index.datasheet_for("ZZZZ"))

    def test_constructor_dictionary(self):
        index = build_index(make_datasheet())
        self.assertIsNotNone(index.datasheet_for("21M3"))
        self.assertIn("21M3", index)


class TestOnDemandFetch(unittest.TestCase):
    """The fetch is network-bound, so only its selection logic is tested here.

    ``fetch_mt_model_data_json`` is patched out in every case: a test that
    reached psref.lenovo.com would be neither hermetic nor honest.
    """

    def _index(self):
        return el.PsrefIndex(
            {"21M3": make_datasheet()},
            machine_type_map={
                "21M3": {"product_key": "Have_It"},
                "21QB": {"product_key": "Want_It"},
                "82X7": {"product_key": "Want_It_Too"},
                "99AB": {"product_key": "Do_Not_Want"},
            },
        )

    def _patched_fetch(self, index, **kwargs):
        """Run ``fetch_missing_datasheets`` with the network stubbed out."""
        asked: list[str] = []

        def fake_fetch(product_key):
            asked.append(product_key)
            return [{"model": "TEST", "machine_type": "TEST"}], {}

        def fake_build(prefix, prefix_rows, mt_entry, filter_options=None):
            return make_datasheet(prefix)

        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.object(el_psref, "fetch_mt_model_data_json", fake_fetch), \
                 unittest.mock.patch.object(el_psref, "build_mt_datasheet", fake_build):
                fetched = index.fetch_missing_datasheets(write_dir=Path(tmp), **kwargs)
        return fetched, asked

    def test_missing_machine_types_reported(self):
        index = el.PsrefIndex(
            {"21M3": make_datasheet()},
            machine_type_map={"21M3": {"product_key": "X"}, "99AB": {"product_key": "Y"}},
        )
        self.assertEqual(index.missing_machine_types(), ["99AB"])

    def test_only_restricts_the_fetch_to_the_machine_types_asked_for(self):
        """A catalog needs ~14 datasheets, not the ~915 that are missing.

        Without ``only``, ``limit`` truncates the missing set alphabetically and
        fetches machine types nothing in the catalog references.
        """
        fetched, _ = self._patched_fetch(self._index(), only=["82X7", "21QB"])
        self.assertEqual(fetched, ["21QB", "82X7"])

    def test_only_accepts_full_skus_not_just_machine_types(self):
        """Callers hold SKUs; the machine-type rule lives in this module."""
        fetched, _ = self._patched_fetch(self._index(), only=["82X700G5IN", "21qbcto1ww"])
        self.assertEqual(fetched, ["21QB", "82X7"])

    def test_only_never_refetches_a_datasheet_already_on_disk(self):
        fetched, _ = self._patched_fetch(self._index(), only=["21M3", "82X7"])
        self.assertEqual(fetched, ["82X7"])

    def test_only_ignores_machine_types_psref_does_not_catalog(self):
        fetched, asked = self._patched_fetch(self._index(), only=["ZZZZ"])
        self.assertEqual(fetched, [])
        self.assertEqual(asked, [], "an uncatalogued machine type must not cost a request")

    def test_limit_still_applies_within_the_requested_set(self):
        fetched, _ = self._patched_fetch(self._index(), only=["82X7", "21QB", "99AB"], limit=2)
        self.assertEqual(len(fetched), 2)

    def test_without_only_every_missing_machine_type_is_fetched(self):
        fetched, _ = self._patched_fetch(self._index())
        self.assertEqual(fetched, ["21QB", "82X7", "99AB"])


class TestMachineTypesFor(unittest.TestCase):
    """The helper an on-demand fetch uses to ask for exactly what it needs."""

    def test_collects_deduplicated_machine_types_from_listings(self):
        products = [
            {"internal_model_code": "82X700G5IN"},
            {"model_name": "Lenovo IdeaPad Slim 3 82X700H1IN"},
            {"title": "Lenovo IdeaPad Slim 3 21QBCTO1WW Laptop"},
        ]
        self.assertEqual(el.machine_types_for(products), ["21QB", "82X7"])

    def test_skips_listings_with_no_sku(self):
        self.assertEqual(el.machine_types_for([{"title": "Lenovo IdeaPad"}, {}, None]), [])


class TestPsrefFetchResilience(unittest.TestCase):
    """Resilience tests for psref fetch_mt_model_data_json and _process_prefix."""

    @unittest.mock.patch.object(el_psref, "_request_bytes")
    def test_fetch_mt_model_data_json_handles_invalid_json(self, mock_request):
        # Simulates PSREF returning empty / invalid JSON (e.g. JSONDecodeError line 1 col 1)
        mock_request.return_value = b""
        rows, filters = el_psref.fetch_mt_model_data_json("test_key")
        self.assertEqual(rows, [])
        self.assertEqual(filters, {})

    @unittest.mock.patch.object(el_psref, "_request_bytes")
    def test_fetch_mt_model_data_json_handles_html_error(self, mock_request):
        mock_request.return_value = b"<html><body>502 Bad Gateway</body></html>"
        rows, filters = el_psref.fetch_mt_model_data_json("test_key")
        self.assertEqual(rows, [])
        self.assertEqual(filters, {})

    @unittest.mock.patch.object(el_psref, "fetch_mt_model_data_json")
    def test_process_prefix_falls_back_on_fetch_failure(self, mock_fetch):
        mock_fetch.side_effect = RuntimeError("network down")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pfx, ds = el_psref._process_prefix("82X7", {"product_key": "PK"}, tmp_path, refresh=True)
            self.assertEqual(pfx, "82X7")
            self.assertIn("models", ds)


if __name__ == "__main__":
    unittest.main()
