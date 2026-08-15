from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from laptopdeals import manufacturer as m


class TestInvariantFields(unittest.TestCase):
    def test_identical_across_configs(self):
        cfgs = [
            {"Memory": "16GB DDR5 on board", "Battery": "42WHrs, 3S1P, 3-cell Li-ion"},
            {"Memory": "16GB DDR5 on board", "Battery": "42WHrs, 3S1P, 3-cell Li-ion"},
        ]
        self.assertEqual(
            m.invariant_fields(cfgs),
            {"Memory": "16GB DDR5 on board", "Battery": "42WHrs, 3S1P, 3-cell Li-ion"},
        )

    def test_varying_field_is_omitted_not_resolved(self):
        cfgs = [
            {"Processor": "Ultra 5 225H", "Battery": "42WHrs"},
            {"Processor": "Ultra 5 325", "Battery": "42WHrs"},
        ]
        shared = m.invariant_fields(cfgs)
        self.assertNotIn("Processor", shared)
        self.assertEqual(shared["Battery"], "42WHrs")

    def test_empty_values_void_the_claim(self):
        cfgs = [{"Battery": "42WHrs"}, {"Battery": ""}]
        self.assertEqual(m.invariant_fields(cfgs), {})

    def test_empty_input(self):
        self.assertEqual(m.invariant_fields([]), {})
        self.assertEqual(m.invariant_fields([{}, {}]), {})


class TestMergeSection(unittest.TestCase):
    def test_authoritative_overwrites_and_records(self):
        conflicts: list[dict] = []
        merged = m.merge_section(
            "graphics",
            {"vram": "Shared", "model": "AMD Radeon"},
            {"vram": "6 GB", "model": "AMD Radeon"},
            origin=m.ORIGIN_ASUS_EXACT,
            conflicts=conflicts,
        )
        self.assertEqual(merged["vram"], "6 GB")
        self.assertEqual(conflicts[0]["field"], "graphics.vram")
        self.assertEqual(conflicts[0]["chosen"], "manufacturer")

    def test_family_fills_gaps_only(self):
        conflicts: list[dict] = []
        merged = m.merge_section(
            "power",
            {"adapter": "", "watt": ""},
            {"adapter": "65W USB-C", "watt": 65},
            origin=m.ORIGIN_ASUS_FAMILY,
            conflicts=conflicts,
        )
        self.assertEqual(merged["adapter"], "65W USB-C")
        self.assertEqual(merged["watt"], 65)
        self.assertEqual(conflicts, [])

    def test_family_does_not_overwrite_retailer(self):
        conflicts: list[dict] = []
        merged = m.merge_section(
            "power",
            {"watt": 90},
            {"watt": 65},
            origin=m.ORIGIN_ASUS_FAMILY,
            conflicts=conflicts,
        )
        self.assertEqual(merged["watt"], 90)
        self.assertEqual(conflicts[0]["chosen"], "retailer")

    def test_weaker_origin_cannot_override_stronger_applied_earlier(self):
        merged = m.merge_section(
            "power",
            {"watt": 65},
            {"watt": 90},
            origin=m.ORIGIN_ASUS_FAMILY,
            current_origin=m.ORIGIN_ASUS_EXACT,
        )
        self.assertEqual(merged["watt"], 65)


class TestApplyManufacturerSpecs(unittest.TestCase):
    def test_origin_and_conflicts_are_recorded(self):
        product = {
            "id": "AMZ-TEST",
            "tech_specs": {
                "graphics": {"vram": "Shared"},
                "power": {"watt": ""},
            },
        }
        m.apply_manufacturer_specs(
            product,
            {
                "graphics": {"vram": "6 GB", "dedicated": True},
                "power": {"watt": 65},
            },
            origin=m.ORIGIN_ASUS_EXACT,
        )
        self.assertEqual(product["tech_specs"]["graphics"]["vram"], "6 GB")
        self.assertEqual(product["tech_specs"]["power"]["watt"], 65)
        self.assertEqual(product["spec_origin"]["graphics"], m.ORIGIN_ASUS_EXACT)
        self.assertEqual(product["spec_origin"]["power"], m.ORIGIN_ASUS_EXACT)
        self.assertEqual(len(product["spec_conflicts"]), 1)
        self.assertEqual(product["spec_conflicts"][0]["field"], "graphics.vram")

    def test_exact_wins_over_family_via_resolve_specs(self):
        product = {"id": "T", "tech_specs": {"processor": {"model": ""}}}
        for sections, origin in m.resolve_specs(
            exact={"processor": {"model": "Ultra 5 225H"}},
            family={"processor": {"model": "Ultra 5"}},
        ):
            m.apply_manufacturer_specs(product, sections, origin=origin)
        self.assertEqual(product["tech_specs"]["processor"]["model"], "Ultra 5 225H")
        self.assertEqual(product["spec_origin"]["processor"], m.ORIGIN_ASUS_EXACT)


if __name__ == "__main__":
    unittest.main()
