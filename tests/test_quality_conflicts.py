"""The ``spec_conflicts`` section of the quality report.

``manufacturer.merge_section`` has been recording every manufacturer/retailer
disagreement since the ASUS provider landed, and nothing read them back. These
tests cover the summary that does.

The section exists to answer one operational question: *is a field disagreeing
because the two sources genuinely describe different machines, or because one of
them parses that field wrongly every single time?* ``distinct_pairs`` is the
discriminator, so most of what follows pins its semantics down.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT / "scripts"))

from laptopdeals import quality


def _conflict(field, manufacturer, retailer, *, chosen="retailer", origin="asus_family"):
    return {
        "field": field,
        "manufacturer": manufacturer,
        "retailer": retailer,
        "manufacturer_origin": origin,
        "chosen": chosen,
    }


def _row(row_id, conflicts=None, **extra):
    row = {"id": row_id, "tech_specs": {}}
    if conflicts is not None:
        row["spec_conflicts"] = conflicts
    row.update(extra)
    return row


def _catalog(*rows):
    return {"ASUS: Vivobook": list(rows)}


class TestConflictTotals(unittest.TestCase):
    def test_empty_catalog_reports_zeroes_not_none(self):
        report = quality.conflict_report({})
        self.assertEqual(report["total"], 0)
        self.assertEqual(report["products_with_conflicts"], 0)
        self.assertEqual(report["products_share"], 0.0)
        self.assertEqual(report["top_fields"], [])
        self.assertEqual(report["by_chosen"], {})

    def test_counts_conflicts_products_and_splits(self):
        catalog = _catalog(
            _row(
                "A",
                [
                    _conflict("display.refresh", "144 Hz", "120 Hz"),
                    _conflict("storage.type", "NVMe SSD", "SSD"),
                ],
            ),
            _row(
                "B",
                [
                    _conflict(
                        "storage.type",
                        "NVMe SSD",
                        "SSD",
                        chosen="manufacturer",
                        origin="asus_exact",
                    )
                ],
            ),
            _row("C"),
        )
        report = quality.conflict_report(catalog)
        self.assertEqual(report["total"], 3)
        self.assertEqual(report["products_with_conflicts"], 2)
        self.assertEqual(report["products_share"], round(2 / 3, 4))
        self.assertEqual(report["by_chosen"], {"retailer": 2, "manufacturer": 1})
        self.assertEqual(report["by_origin"], {"asus_family": 2, "asus_exact": 1})
        self.assertEqual(report["fields_with_conflicts"], 2)

    def test_a_product_counts_once_however_many_conflicts_it_carries(self):
        """Ten disagreements on one machine is one unhappy product, not ten."""
        catalog = _catalog(
            _row("A", [_conflict(f"display.f{i}", "x", "y") for i in range(10)]),
            _row("B"),
        )
        report = quality.conflict_report(catalog)
        self.assertEqual(report["total"], 10)
        self.assertEqual(report["products_with_conflicts"], 1)
        self.assertEqual(report["products_share"], 0.5)

    def test_per_field_product_count_is_distinct_products(self):
        """Two conflicts on the same field on one row is one affected product."""
        catalog = _catalog(
            _row(
                "A",
                [
                    _conflict("memory.amount", "8 GB", "16 GB"),
                    _conflict("memory.amount", "32 GB", "16 GB"),
                ],
            )
        )
        field = quality.conflict_report(catalog)["top_fields"][0]
        self.assertEqual(field["count"], 2)
        self.assertEqual(field["products"], 1)


class TestRanking(unittest.TestCase):
    def test_fields_rank_by_count_then_name(self):
        catalog = _catalog(
            _row(
                "A",
                [_conflict("b.rare", "x", "y")]
                + [_conflict("a.common", "x", "y") for _ in range(3)]
                + [_conflict("a.tied", "x", "y")],
            )
        )
        ranked = [f["field"] for f in quality.conflict_report(catalog)["top_fields"]]
        # a.common wins on count; the two 1-count fields tie and sort by name.
        self.assertEqual(ranked, ["a.common", "a.tied", "b.rare"])

    def test_ranked_list_is_capped_and_reports_the_remainder(self):
        """The catalog workflows commit this report; it must stay readable."""
        many = [
            _conflict(f"section.f{i:02d}", "x", "y")
            for i in range(quality.CONFLICT_TOP_FIELDS + 5)
        ]
        report = quality.conflict_report(_catalog(_row("A", many)))
        self.assertEqual(len(report["top_fields"]), quality.CONFLICT_TOP_FIELDS)
        self.assertEqual(report["fields_with_conflicts"], quality.CONFLICT_TOP_FIELDS + 5)
        self.assertEqual(report["fields_omitted"], 5)

    def test_examples_are_capped_and_most_frequent_first(self):
        """The systematic pair must be the one a human sees first.

        A field can disagree in 85 different ways; the pair that repeats is the
        one that points at a parser rule, so it leads.
        """
        conflicts = [_conflict("storage.type", "NVMe SSD", "SSD") for _ in range(5)]
        conflicts += [_conflict("storage.type", f"Odd {i}", "SSD") for i in range(4)]
        report = quality.conflict_report(_catalog(_row("A", conflicts)))
        field = report["top_fields"][0]
        self.assertEqual(len(field["examples"]), quality.CONFLICT_EXAMPLES_PER_FIELD)
        top = field["examples"][0]
        self.assertEqual((top["manufacturer"], top["retailer"], top["count"]), ("NVMe SSD", "SSD", 5))

    def test_examples_carry_the_chosen_split_for_that_pair(self):
        conflicts = [
            _conflict("storage.type", "NVMe SSD", "SSD"),
            _conflict("storage.type", "NVMe SSD", "SSD", chosen="manufacturer", origin="asus_exact"),
        ]
        example = quality.conflict_report(_catalog(_row("A", conflicts)))["top_fields"][0]["examples"][0]
        self.assertEqual(example["count"], 2)
        self.assertEqual(example["chosen"], {"manufacturer": 1, "retailer": 1})
        self.assertEqual(example["manufacturer_origin"], {"asus_exact": 1, "asus_family": 1})


class TestDistinctPairs(unittest.TestCase):
    """``distinct_pairs`` is the bug signal and must count value pairs only."""

    def test_one_repeated_pair_is_one_distinct_pair(self):
        conflicts = [_conflict("processor.brand", "Snapdragon", "Qualcomm") for _ in range(11)]
        field = quality.conflict_report(_catalog(_row("A", conflicts)))["top_fields"][0]
        self.assertEqual(field["count"], 11)
        # 11 products, one disagreement: a vocabulary mismatch, not 11 machines.
        self.assertEqual(field["distinct_pairs"], 1)

    def test_resolution_tier_does_not_split_a_pair(self):
        """The same mismatch reached through two tiers is still one mismatch.

        Folding ``chosen``/``manufacturer_origin`` into the pair key would have
        reported this as two distinct pairs and hidden the systematic case.
        """
        conflicts = [
            _conflict("storage.type", "NVMe SSD", "SSD", chosen="retailer", origin="asus_family"),
            _conflict("storage.type", "NVMe SSD", "SSD", chosen="manufacturer", origin="asus_exact"),
        ]
        field = quality.conflict_report(_catalog(_row("A", conflicts)))["top_fields"][0]
        self.assertEqual(field["distinct_pairs"], 1)

    def test_genuinely_varying_values_stay_distinct(self):
        conflicts = [_conflict("memory.amount", f"{n} GB", "16 GB") for n in (8, 32, 64)]
        field = quality.conflict_report(_catalog(_row("A", conflicts)))["top_fields"][0]
        self.assertEqual(field["distinct_pairs"], 3)


class TestMalformedInput(unittest.TestCase):
    """Stale or hand-edited catalogs must degrade, never crash the gate."""

    def test_non_list_spec_conflicts_is_counted_not_fatal(self):
        report = quality.conflict_report(_catalog(_row("A", conflicts="oops")))
        self.assertEqual(report["total"], 0)
        self.assertEqual(report["malformed_entries"], 1)
        self.assertEqual(report["products_with_conflicts"], 0)

    def test_non_dict_entries_are_skipped_and_counted(self):
        catalog = _catalog(_row("A", ["nope", None, _conflict("display.size", "16.0\"", "16\"")]))
        report = quality.conflict_report(catalog)
        self.assertEqual(report["total"], 1)
        self.assertEqual(report["malformed_entries"], 2)

    def test_missing_keys_fall_back_to_a_named_bucket(self):
        report = quality.conflict_report(_catalog(_row("A", [{}])))
        self.assertEqual(report["total"], 1)
        self.assertEqual(report["top_fields"][0]["field"], "(unspecified)")
        self.assertEqual(report["by_chosen"], {"(unspecified)": 1})

    def test_empty_list_is_not_malformed(self):
        report = quality.conflict_report(_catalog(_row("A", [])))
        self.assertEqual(report["malformed_entries"], 0)
        self.assertEqual(report["products_with_conflicts"], 0)

    def test_non_dict_rows_are_ignored(self):
        report = quality.conflict_report({"ASUS: Vivobook": ["not a row", 7]})
        self.assertEqual(report["total"], 0)
        self.assertEqual(report["products_share"], 0.0)


class TestValueRendering(unittest.TestCase):
    """Conflicts hold whatever ``tech_specs`` held: lists, bools, numbers."""

    def test_list_values_render_as_one_line(self):
        conflict = _conflict("ports.items", ["HDMI 1.4", "USB-C"], ["HDMI 4", "USB"])
        example = quality.conflict_report(_catalog(_row("A", [conflict])))["top_fields"][0]["examples"][0]
        self.assertEqual(example["manufacturer"], "HDMI 1.4, USB-C")
        self.assertEqual(example["retailer"], "HDMI 4, USB")

    def test_numbers_and_bools_render_without_python_syntax(self):
        conflicts = [
            _conflict("processor.cores", 10, 14),
            _conflict("memory.soldered", True, False),
        ]
        report = quality.conflict_report(_catalog(_row("A", conflicts)))
        rendered = {f["field"]: f["examples"][0] for f in report["top_fields"]}
        self.assertEqual(
            (rendered["processor.cores"]["manufacturer"], rendered["processor.cores"]["retailer"]),
            ("10", "14"),
        )
        self.assertEqual(
            (rendered["memory.soldered"]["manufacturer"], rendered["memory.soldered"]["retailer"]),
            ("true", "false"),
        )

    def test_long_values_are_truncated_and_whitespace_collapsed(self):
        blob = "ROG Nebula HDR   Display\n" + "spec detail " * 60
        example = quality.conflict_report(
            _catalog(_row("A", [_conflict("display.resolution", blob, "2560x1600")]))
        )["top_fields"][0]["examples"][0]
        self.assertEqual(len(example["manufacturer"]), quality.CONFLICT_VALUE_CHARS)
        self.assertTrue(example["manufacturer"].endswith("…"))
        self.assertNotIn("\n", example["manufacturer"])
        self.assertNotIn("   ", example["manufacturer"])

    def test_nested_payloads_do_not_dump_their_contents(self):
        """storage.devices is a list of dicts; the report must not inline them."""
        devices = [{"capacity": "512 GB", "type": "SSD", "interface": "NVMe"}]
        example = quality.conflict_report(
            _catalog(_row("A", [_conflict("storage.devices", devices, devices)]))
        )["top_fields"][0]["examples"][0]
        self.assertEqual(example["manufacturer"], "{3 keys}")


class TestSummaryLine(unittest.TestCase):
    def test_quiet_when_there_is_nothing_to_say(self):
        line = quality.conflict_summary_line(quality.conflict_report({}), 0)
        self.assertEqual(line, "conflicts: none recorded")

    def test_reports_total_split_and_worst_field(self):
        catalog = _catalog(
            _row("A", [_conflict("storage.type", "NVMe SSD", "SSD") for _ in range(3)]),
            _row("B", [_conflict("display.size", '16.0"', '16"', chosen="manufacturer")]),
        )
        report = quality.conflict_report(catalog)
        line = quality.conflict_summary_line(report, 2)
        self.assertIn("conflicts: 4 on 2/2 products", line)
        self.assertIn("retailer 3", line)
        self.assertIn("manufacturer 1", line)
        self.assertIn("storage.type 3", line)
        self.assertIn("1 distinct pairs", line)


class TestReportIntegration(unittest.TestCase):
    def test_coverage_report_carries_the_section(self):
        report = quality.coverage_report(_catalog(_row("A", [_conflict("storage.type", "NVMe SSD", "SSD")])))
        self.assertEqual(report["spec_conflicts"]["total"], 1)

    def test_existing_report_shape_is_untouched(self):
        """The golden baselines compare report['fields']; that must not move."""
        with_conflicts = quality.coverage_report(
            _catalog(_row("A", [_conflict("storage.type", "NVMe SSD", "SSD")]))
        )
        without = quality.coverage_report(_catalog(_row("A")))
        for key in ("fields", "core_specs", "source_available", "total_products"):
            self.assertEqual(with_conflicts[key], without[key], key)

    def test_golden_baselines_still_match_the_field_table(self):
        """Adding a top-level key must not invalidate the checked-in baselines."""
        expected = set(quality.coverage_report({"products": []})["fields"])
        for name in ("quality-amazon", "quality-asus"):
            baseline_file = ROOT / "tests" / "golden" / f"{name}.json"
            if not baseline_file.exists():
                continue
            with self.subTest(baseline=name):
                golden = json.loads(baseline_file.read_text(encoding="utf-8"))
                self.assertEqual(set(golden["fields"]), expected)


class TestConflictGate(unittest.TestCase):
    """Conflicts are evidence, not failure. The threshold must be opt-in."""

    @staticmethod
    def _write(tmp, catalog):
        path = Path(tmp) / "catalog.json"
        path.write_text(json.dumps(catalog), encoding="utf-8")
        return path

    def test_default_never_fails_on_conflicts(self):
        catalog = _catalog(
            *[_row(f"P{i}", [_conflict("storage.type", "NVMe SSD", "SSD")]) for i in range(5)]
        )
        with tempfile.TemporaryDirectory() as tmp:
            report = quality.quality_gate(self._write(tmp, catalog))
            self.assertEqual(report["spec_conflicts"]["products_share"], 1.0)
            self.assertEqual(report["failures"], [])
            self.assertEqual(report["exit"], "pass")

    def test_threshold_fails_once_raised(self):
        catalog = _catalog(
            *[_row(f"P{i}", [_conflict("storage.type", "NVMe SSD", "SSD")]) for i in range(5)]
        )
        with tempfile.TemporaryDirectory() as tmp:
            report = quality.quality_gate(self._write(tmp, catalog), max_conflict_share=0.5)
            self.assertEqual(report["exit"], "fail")
            self.assertTrue(
                any("spec conflicts" in message for message in report["failures"]),
                report["failures"],
            )
            self.assertTrue(
                any("storage.type" in message for message in report["failures"]),
                report["failures"],
            )

    def test_threshold_passes_a_clean_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, _catalog(_row("A"), _row("B")))
            self.assertEqual(quality.quality_gate(path, max_conflict_share=0.01)["exit"], "pass")


class TestAgainstGeneratedCatalog(unittest.TestCase):
    """Measured against the real generated catalog, not a hand-written fixture.

    AGENTS.md §4 records a defect that survived because its test fixture was
    hand-written into a shape the pipeline never actually produces. The
    assertions here are invariants recomputed from whatever the catalog holds,
    so they cannot drift into agreeing with a fiction.
    """

    CATALOG = REPO / "apps" / "web" / "data-asus.json"

    def setUp(self):
        if not self.CATALOG.exists():
            self.skipTest(f"generated catalog absent: {self.CATALOG}")
        self.data = json.loads(self.CATALOG.read_text(encoding="utf-8"))
        self.rows = [
            row
            for value in self.data.values()
            if isinstance(value, list)
            for row in value
            if isinstance(row, dict)
        ]

    def test_totals_match_a_direct_recount(self):
        expected_total = sum(
            len(row["spec_conflicts"])
            for row in self.rows
            if isinstance(row.get("spec_conflicts"), list)
        )
        expected_products = sum(
            1 for row in self.rows if isinstance(row.get("spec_conflicts"), list) and row["spec_conflicts"]
        )
        report = quality.conflict_report(self.data)
        self.assertEqual(report["total"], expected_total)
        self.assertEqual(report["products_with_conflicts"], expected_products)
        self.assertEqual(report["malformed_entries"], 0)

    def test_splits_account_for_every_conflict(self):
        report = quality.conflict_report(self.data)
        self.assertEqual(sum(report["by_chosen"].values()), report["total"])
        self.assertEqual(sum(report["by_origin"].values()), report["total"])
        ranked_total = sum(field["count"] for field in report["top_fields"])
        if report["fields_omitted"]:
            self.assertLess(ranked_total, report["total"])
        else:
            self.assertEqual(ranked_total, report["total"])

    def test_recorded_vocabulary_is_the_one_manufacturer_py_writes(self):
        """Catches a rename of the contract in manufacturer.merge_section."""
        report = quality.conflict_report(self.data)
        if not report["total"]:
            self.skipTest("catalog carries no conflicts")
        self.assertTrue(set(report["by_chosen"]) <= {"manufacturer", "retailer"}, report["by_chosen"])
        from laptopdeals import manufacturer

        self.assertTrue(
            set(report["by_origin"]) <= set(manufacturer.ORIGIN_PRECEDENCE),
            report["by_origin"],
        )

    def test_section_stays_bounded(self):
        """A committed report must not balloon with the catalog."""
        report = quality.conflict_report(self.data)
        self.assertLessEqual(len(report["top_fields"]), quality.CONFLICT_TOP_FIELDS)
        for field in report["top_fields"]:
            self.assertLessEqual(len(field["examples"]), quality.CONFLICT_EXAMPLES_PER_FIELD)
            for example in field["examples"]:
                self.assertLessEqual(len(example["manufacturer"]), quality.CONFLICT_VALUE_CHARS)
                self.assertLessEqual(len(example["retailer"]), quality.CONFLICT_VALUE_CHARS)
        self.assertLess(len(json.dumps(report)), 20_000)


if __name__ == "__main__":
    unittest.main()
