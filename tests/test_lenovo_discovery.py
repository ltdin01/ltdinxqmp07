from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from laptopdeals import catalog
from laptopdeals.jsonio import read_json, write_json
from laptopdeals.providers import lenovo as lenovo_provider
from laptopdeals.sources import lenovo as lenovo_source


def card(code: str, category: str, name: str = "", url: str = "", group: str = "", summary: str = "") -> dict:
    """A listing card shaped like the live all-laptops DLP response (Nov 2026)."""
    item = {
        "productCode": code,
        "productName": name or f"Laptop {code}",
        "summary": summary or f"Per-SKU line {code}",
        "url": url or f"/p/laptops/{category}/some-model/{code.lower()}",
        "categoryPath": ["ROOTPMI", "laptops", category, f"{category}-series"],
        "finalPrice": "100000.00",
        "marketingStatus": "Available",
    }
    if group:
        item["categoryPath"].append(group)
        item["parentSubseriesId"] = group
    return item


class NormalizeSkuCardTests(unittest.TestCase):
    def test_group_code_moves_out_of_category_path_and_sku_is_upper_cased(self) -> None:
        raw = card("83f5cto1wwin2", "legion-laptops", group="LEN101G0039",
                   url="/p/laptops/legion-laptops/legion-pro-series/legion-pro-7i-gen-10-16-inch-intel/83f5cto1wwin2")
        out = lenovo_source.normalize_sku_card(raw)
        self.assertEqual(out["categoryPath"], ["ROOTPMI", "laptops", "legion-laptops", "legion-laptops-series"])
        self.assertEqual(out["_subseries"], "LEN101G0039")
        self.assertEqual(out["url"], "/p/laptops/legion-laptops/legion-pro-series/legion-pro-7i-gen-10-16-inch-intel/83F5CTO1WWIN2")
        self.assertEqual(raw["categoryPath"][-1], "LEN101G0039", "input card must not be mutated")

    def test_group_names_replace_per_sku_lines(self) -> None:
        cards = [
            lenovo_source.normalize_sku_card(card("21UFCTO1WWIN2", "thinkpad", group="LEN101T0001")),
            lenovo_source.normalize_sku_card(card("83UTCTO1WWIN1", "lenovo-laptops")),
        ]
        groups = {"LEN101T0001": {"productCode": "LEN101T0001", "summary": "ThinkPad P1 Gen 9 (16, Intel)", "productName": "ThinkPad P1 Gen 9 16 inch Intel"}}
        self.assertEqual(lenovo_source.apply_group_names(cards, groups), 1)
        self.assertEqual(cards[0]["summary"], "ThinkPad P1 Gen 9 (16, Intel)")
        self.assertEqual(cards[0]["productName"], "ThinkPad P1 Gen 9 16 inch Intel")
        self.assertTrue(cards[0]["_group_named"])
        self.assertNotIn("_group_named", cards[1])


DETAIL = (
    [{"name": "Home"}, {"name": "Laptops"}, {"name": "Legion"}],
    {"name": "Product", "image": ["https://example.test/p.png"], "offers": {"price": "100000", "priceCurrency": "INR"}},
    ([], {}, {}),
)


def run_scrape(fake_client, *, series, existing_files=(), discovery="auto", only_new=False, limit=None):
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "catalog.json"
        with patch.object(catalog.lenovo, "LenovoCatalogClient", fake_client):
            result = catalog.scrape_catalog(
                series=series,
                output=output,
                only_new=only_new,
                existing_files=list(existing_files),
                new_ids_output=None,
                limit_per_series=limit,
                delay=(0, 0),
                workers=1,
                verbose=False,
                discovery=discovery,
            )
        payload = read_json(output)
    rows = [item for items in payload["groups"].values() for item in items]
    return result, {row["id"]: row for row in rows}, payload


class SeriesFromCardTests(unittest.TestCase):
    def test_category_path_decides_series(self) -> None:
        self.assertEqual(lenovo_source.series_from_card(card("83Q7CTO1WWIN2", "legion-laptops")), "Legion")
        self.assertEqual(lenovo_source.series_from_card(card("83JQCTO1WWIN3", "loq-laptops")), "LOQ")
        self.assertEqual(lenovo_source.series_from_card(card("21U3S1FD00", "thinkpad")), "ThinkPad")

    def test_product_name_is_the_fallback(self) -> None:
        item = {"productName": "IdeaPad Slim 5 Gen 10", "categoryPath": ["ROOTPMI", "laptops"]}
        self.assertEqual(lenovo_source.series_from_card(item), "IdeaPad")

    def test_unknown_line_is_kept_under_a_generic_series(self) -> None:
        item = card("83UTCTO1WWIN1", "lenovo-laptops", name="Lenovo V15 Gen 5")
        self.assertEqual(lenovo_source.series_from_card(item), lenovo_source.OTHER_SERIES)


class AllLaptopProductsTests(unittest.TestCase):
    def make_client(self, pages):
        client = object.__new__(lenovo_source.LenovoCatalogClient)
        client.delay = (0, 0)
        client.all_laptops_config = lambda: {"facetId": "laptops-facet", "pageSize": "20"}
        client.dlp_query = lambda form_data, page, **kwargs: pages[page - 1]
        client.expand_group_card = lambda item: [item]
        client.sleep = lambda: None
        return client

    def test_pages_are_walked_tagged_and_deduplicated(self) -> None:
        pages = [
            {"pageCount": 2, "data": [{"products": [card("83Q7CTO1WWIN2", "legion-laptops"), card("21U3S1FD00", "thinkpad")]}]},
            {"pageCount": 2, "data": [{"products": [card("83Q7CTO1WWIN2", "legion-laptops"), card("83SJCTO1WWIN1", "ideapad")]}]},
        ]
        products = self.make_client(pages).all_laptop_products()
        self.assertEqual([p["productCode"] for p in products], ["83Q7CTO1WWIN2", "21U3S1FD00", "83SJCTO1WWIN1"])
        self.assertEqual([p["_series"] for p in products], ["Legion", "ThinkPad", "IdeaPad"])

    def test_group_codes_are_expanded(self) -> None:
        group = card("LEN101G0051", "legion-laptops")
        client = self.make_client([{"pageCount": 1, "data": [{"products": [group]}]}])
        client.expand_group_card = lambda item: [dict(item, productCode=code) for code in ("83Q7CTO1WWIN2", "83Q7009JIN")]
        products = client.all_laptop_products()
        self.assertEqual([p["productCode"] for p in products], ["83Q7CTO1WWIN2", "83Q7009JIN"])
        self.assertEqual({p["_series"] for p in products}, {"Legion"})


class ScrapeDiscoveryTests(unittest.TestCase):
    FULL = ["LOQ", "ThinkPad", "Legion", "IdeaPad", "ThinkBook", "Yoga"]

    def fake_client(self, all_cards=None, all_error=None, series_cards=None, groups=None, breadcrumb=None):
        calls = {"series": [], "group_lookups": []}

        class FakeClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def all_laptop_products(self, limit=None):
                if all_error:
                    raise all_error
                return [
                    dict(lenovo_source.normalize_sku_card(item), _series=lenovo_source.series_from_card(item))
                    for item in all_cards or []
                ]

            def subseries_cards(self, series_names):
                calls["group_lookups"].append(list(series_names))
                return dict(groups or {})

            def listing_products(self, series, limit=None):
                calls["series"].append(series)
                return list((series_cards or {}).get(series, []))

            def detail(self, url):
                if breadcrumb is None:
                    return DETAIL
                return ([{"name": name} for name in breadcrumb], DETAIL[1], DETAIL[2])

        return FakeClient, calls

    def test_titles_and_paths_match_the_per_series_catalog(self) -> None:
        """Group-named SKUs keep the model name; others fall back to the PDP breadcrumb."""
        cards = [
            card("21UFCTO1WWIN2", "thinkpad", group="LEN101T0001", name="P1 G9_21UF - P1 G9_02_IN"),
            card("83UTCTO1WWIN1", "lenovo-laptops", summary="Lenovo V14 Gen 6 - 35.56cms"),
        ]
        groups = {"LEN101T0001": {"summary": "ThinkPad P1 Gen 9 (16, Intel)", "productName": "ThinkPad P1 Gen 9 16 inch Intel"}}
        client, calls = self.fake_client(
            all_cards=cards,
            groups=groups,
            breadcrumb=["Home", "Laptops", "ThinkPad", "ThinkPad P series", "ThinkPad P1 Gen 9 (16, Intel)"],
        )
        _, rows, _ = run_scrape(client, series=self.FULL)
        p1 = rows["21UFCTO1WWIN2"]
        self.assertEqual(p1["title"], "ThinkPad P1 Gen 9 (16, Intel)")
        self.assertEqual(p1["summary"], "ThinkPad P1 Gen 9 (16, Intel)")
        self.assertEqual(p1["product_name"], "ThinkPad P1 Gen 9 16 inch Intel")
        self.assertEqual(p1["breadcrumb_path"], "Home > Laptops > ThinkPad > ThinkPad P series > ThinkPad P1 Gen 9 (16, Intel)")
        self.assertEqual(p1["listing_category_path"], ["ROOTPMI", "laptops", "thinkpad", "thinkpad-series"])
        self.assertTrue(p1["store_link"].endswith("/21UFCTO1WWIN2"))
        self.assertEqual(rows["83UTCTO1WWIN1"]["title"], "ThinkPad P1 Gen 9 (16, Intel)", "no group: breadcrumb model name wins over the per-SKU line")
        self.assertEqual(calls["group_lookups"], [self.FULL])

    def test_auto_uses_all_laptops_listing_and_keeps_new_lines(self) -> None:
        cards = [
            card("83Q7CTO1WWIN2", "legion-laptops"),
            card("21U3S1FD00", "thinkpad"),
            card("83UTCTO1WWIN1", "lenovo-laptops", name="Lenovo V15 Gen 5"),
        ]
        client, calls = self.fake_client(all_cards=cards)
        result, rows, payload = run_scrape(client, series=self.FULL)
        self.assertEqual(set(rows), {"83Q7CTO1WWIN2", "21U3S1FD00", "83UTCTO1WWIN1"})
        self.assertEqual(rows["83Q7CTO1WWIN2"]["series_filter"], "Legion")
        self.assertEqual(rows["83UTCTO1WWIN1"]["series_filter"], lenovo_source.OTHER_SERIES)
        self.assertEqual(rows["83Q7CTO1WWIN2"]["store_link"], "https://www.lenovo.com/in/en/p/laptops/legion-laptops/some-model/83Q7CTO1WWIN2")
        self.assertEqual(calls["series"], [], "per-series listings should not be needed")
        self.assertEqual(payload["source_urls"]["Legion"], lenovo_source.LAPTOPS_URL)
        self.assertEqual(result["failed_series"], [])

    def test_explicit_series_subset_is_respected(self) -> None:
        cards = [card("83Q7CTO1WWIN2", "legion-laptops"), card("21U3S1FD00", "thinkpad"), card("83UTCTO1WWIN1", "lenovo-laptops")]
        client, _ = self.fake_client(all_cards=cards)
        _, rows, _ = run_scrape(client, series=["Legion"])
        self.assertEqual(set(rows), {"83Q7CTO1WWIN2"})

    def test_limit_per_series_applies_to_all_laptops_listing(self) -> None:
        cards = [card(code, "legion-laptops") for code in ("83Q7CTO1WWIN2", "83Q7009JIN", "83Q7009KIN")]
        client, _ = self.fake_client(all_cards=cards)
        _, rows, _ = run_scrape(client, series=["Legion"], limit=2)
        self.assertEqual(len(rows), 2)

    def test_auto_falls_back_to_series_listings_when_all_laptops_fails(self) -> None:
        client, calls = self.fake_client(
            all_error=RuntimeError("HTTP 503"),
            series_cards={"Legion": [card("83Q7CTO1WWIN2", "legion-laptops")]},
        )
        _, rows, _ = run_scrape(client, series=["Legion"])
        self.assertEqual(set(rows), {"83Q7CTO1WWIN2"})
        self.assertEqual(calls["series"], ["Legion"])

    def test_auto_falls_back_when_all_laptops_listing_is_implausibly_small(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "data.json"
            write_json(existing, {"Legion": [{"id": f"SKU{i:04d}", "availability": "in stock"} for i in range(10)]})
            client, calls = self.fake_client(
                all_cards=[card("83Q7CTO1WWIN2", "legion-laptops")],
                series_cards={"Legion": [card("83Q7CTO1WWIN2", "legion-laptops"), card("83Q7009JIN", "legion-laptops")]},
            )
            _, rows, _ = run_scrape(client, series=["Legion"], existing_files=[existing])
        self.assertEqual(calls["series"], ["Legion"])
        self.assertEqual(set(rows), {"83Q7CTO1WWIN2", "83Q7009JIN"})

    def test_all_mode_fails_loudly_instead_of_falling_back(self) -> None:
        client, calls = self.fake_client(all_error=RuntimeError("HTTP 503"))
        with self.assertRaises(RuntimeError):
            run_scrape(client, series=["Legion"], discovery="all")
        self.assertEqual(calls["series"], [])

    def test_series_mode_keeps_the_original_behaviour(self) -> None:
        client, calls = self.fake_client(
            all_cards=[card("21U3S1FD00", "thinkpad")],
            series_cards={"Legion": [card("83Q7CTO1WWIN2", "legion-laptops")]},
        )
        _, rows, _ = run_scrape(client, series=["Legion"], discovery="series")
        self.assertEqual(set(rows), {"83Q7CTO1WWIN2"})
        self.assertEqual(calls["series"], ["Legion"])

    def test_unknown_discovery_mode_is_rejected(self) -> None:
        client, _ = self.fake_client()
        with self.assertRaises(ValueError):
            run_scrape(client, series=["Legion"], discovery="everything")


class DiscoveryFloorTests(unittest.TestCase):
    def test_floor_counts_only_active_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.json"
            rows = [{"id": f"A{i}"} for i in range(6)] + [{"id": f"B{i}", "archived": True} for i in range(4)]
            write_json(path, {"Laptops": rows})
            self.assertEqual(lenovo_provider.discovery_floor([path]), 3)
            self.assertEqual(lenovo_provider.discovery_floor([]), 0)


if __name__ == "__main__":
    unittest.main()
