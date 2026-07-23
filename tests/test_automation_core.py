from __future__ import annotations

import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from laptopdeals import catalog, cto, history, pricing
from laptopdeals import archive as archive_logic
from laptopdeals.ids import ids_from_args, read_ids_file, split_ids
from laptopdeals.jsonio import read_json, write_json
from laptopdeals.sources.bitbns import parse_graph_response
from laptopdeals.sources import lenovo as lenovo_source


class IdTests(unittest.TestCase):
    def test_split_ids_accepts_commas_spaces_and_repeats(self) -> None:
        self.assertEqual(
            split_ids(["abc def,ghi", "ABC"]),
            {"ABC", "DEF", "GHI"},
        )

    def test_ids_file_accepts_list_and_dict_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            list_path = Path(tmp) / "list.json"
            dict_path = Path(tmp) / "dict.json"
            list_path.write_text(json.dumps(["abc", "Def"]), encoding="utf-8")
            dict_path.write_text(json.dumps({"new_ids": ["ghi"]}), encoding="utf-8")
            self.assertEqual(read_ids_file(list_path), {"ABC", "DEF"})
            self.assertEqual(read_ids_file(dict_path), {"GHI"})

    def test_ids_from_args_merges_cli_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ids_path = Path(tmp) / "ids.json"
            ids_path.write_text(json.dumps({"ids": ["fileid"]}), encoding="utf-8")
            args = Namespace(id=["cliid"], ids_file=str(ids_path))
            self.assertEqual(ids_from_args(args), {"CLIID", "FILEID"})


class BitBnsTests(unittest.TestCase):
    def test_parse_graph_response_keeps_valid_points_sorted(self) -> None:
        raw = "2026-01-02~100~x~*~*bad~200~*~*2026-01-01~90"
        self.assertEqual(
            parse_graph_response(raw),
            [{"date": "2026-01-01", "price": 90}, {"date": "2026-01-02", "price": 100}],
        )


class LenovoAvailabilityTests(unittest.TestCase):
    def test_availability_ignores_body_out_of_stock_when_meta_is_available(self) -> None:
        html = (
            '<meta name="productstatus" content="Available"/>'
            '<script type="application/ld+json">'
            '{"@type":"Product","offers":{"availability":"https://schema.org/InStock"}}'
            "</script>"
            "<div>Recommended model is out of stock</div>"
        )
        self.assertEqual(lenovo_source.availability_from_html(html), "in stock")

    def test_availability_prefers_meta_status_over_jsonld(self) -> None:
        html = (
            '<meta name="productstatus" content="Temporarily Unavailable"/>'
            '<script type="application/ld+json">'
            '{"@type":"Product","offers":{"availability":"https://schema.org/InStock"}}'
            "</script>"
        )
        self.assertEqual(lenovo_source.availability_from_html(html), "out of stock")

    def test_catalog_client_retries_transient_http_errors(self) -> None:
        class FakeResponse:
            def __init__(self, status_code: int, text: str) -> None:
                self.status_code = status_code
                self.text = text

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    raise RuntimeError(f"HTTP Error {self.status_code}")

        class FakeSession:
            def __init__(self) -> None:
                self.responses = [FakeResponse(500, ""), FakeResponse(200, "ok")]

            def get(self, *args, **kwargs) -> FakeResponse:
                return self.responses.pop(0)

        class FakeRequests:
            def __init__(self) -> None:
                self.session = FakeSession()

            def Session(self, *args, **kwargs) -> FakeSession:
                return self.session

        fake_requests = FakeRequests()
        with (
            patch.object(lenovo_source, "require_requests", return_value=fake_requests),
            patch.object(lenovo_source.time, "sleep", return_value=None),
            patch.object(lenovo_source.random, "uniform", return_value=0),
        ):
            client = lenovo_source.LenovoCatalogClient(delay=(0, 0))
            self.assertEqual(client.get_text("https://example.test"), "ok")

    def test_result_url_candidates_finds_dynamic_series_urls(self) -> None:
        html = """
        <a href="https://www.lenovo.com/in/en/laptops/subseries-results/?visibleDatas=4376:LOQ&ipromoID=promo_loq">LOQ</a>
        <a href="/in/en/laptops/results/?visibleDatas=4376%3AThinkPad">ThinkPad</a>
        """
        self.assertEqual(
            lenovo_source.result_url_candidates(html, "LOQ"),
            ["https://www.lenovo.com/in/en/laptops/subseries-results/?visibleDatas=4376:LOQ&ipromoID=promo_loq"],
        )

    def test_get_results_url_uses_subseries_without_default_promo(self) -> None:
        self.assertEqual(
            lenovo_source.get_results_url("Yoga"),
            "https://www.lenovo.com/in/en/laptops/subseries-results/?visibleDatas=4376:Yoga",
        )

    def test_pick_user_agent_avoids_previous_value(self) -> None:
        with patch.object(lenovo_source.random, "choice", side_effect=lambda seq: seq[0]):
            first = lenovo_source.pick_user_agent()
            second = lenovo_source.pick_user_agent(avoid=first)
        self.assertNotEqual(first, second)

    def test_result_config_uses_live_discovered_link_as_is(self) -> None:
        live_url = "https://www.lenovo.com/in/en/laptops/subseries-results/?visibleDatas=4376:LOQ&ipromoID=promo_loq"
        generic_html = f'<a href="{live_url}">LOQ</a>'
        live_html = 'window["ofp-2c-mobile-new-dlp_test"] = {"data":{"formData":{"facetId":"pf-123","pageSize":"20","defaultSort":"bestSelling"}}};'
        null_config = '{"success":false,"resultCode":"404","resultMsg":"pageNode config is null","version":0}'

        client = object.__new__(lenovo_source.LenovoCatalogClient)
        client.result_urls = {}

        def fake_get_text(url: str, referer: str | None = None, attempts: int = 4, **kwargs) -> str:
            if url == f"{lenovo_source.SITE_BASE}/laptops/":
                return generic_html
            if url == live_url:
                return live_html
            return null_config

        client.get_text = fake_get_text
        url, form_data = client.result_config("LOQ")
        self.assertEqual(url, live_url)
        self.assertEqual(form_data["facetId"], "pf-123")
        self.assertEqual(client.result_urls["LOQ"], live_url)


class HistoryTests(unittest.TestCase):
    def test_write_json_preserves_existing_final_newline_style_and_skips_same_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.json"
            path.write_text('{"a": 1}', encoding="utf-8")
            write_json(path, {"a": 1}, indent=None)
            self.assertEqual(path.read_text(encoding="utf-8"), '{"a": 1}')

            path.write_text('{"a": 1}\n', encoding="utf-8")
            write_json(path, {"a": 1}, indent=None)
            self.assertEqual(path.read_text(encoding="utf-8"), '{"a": 1}\n')

    def test_write_json_treats_integer_floats_as_same_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.json"
            path.write_text('{"price": 300}', encoding="utf-8")
            write_json(path, {"price": 300.0}, indent=None)
            self.assertEqual(path.read_text(encoding="utf-8"), '{"price": 300}')

    def test_replace_points_keeps_only_change_points(self) -> None:
        points = [
            {"date": "2026-01-03", "price": 120},
            {"date": "2026-01-01", "price": 100},
            {"date": "2026-01-02", "price": 100},
            {"date": "2026-01-04", "price": 120},
            {"date": "2026-01-05", "price": 110},
        ]
        self.assertEqual(
            history.replace_points(points),
            [
                {"date": "2026-01-01", "price": 100},
                {"date": "2026-01-03", "price": 120},
                {"date": "2026-01-05", "price": 110},
            ],
        )

    def test_merge_points_combines_existing_and_incoming(self) -> None:
        existing = [{"date": "2026-01-01", "price": 100}]
        incoming = [{"date": "2026-01-02", "price": 100}, {"date": "2026-01-03", "price": 90}]
        self.assertEqual(
            history.merge_points(existing, incoming),
            [{"date": "2026-01-01", "price": 100}, {"date": "2026-01-03", "price": 90}],
        )

    def test_apply_current_price_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            history_dir = Path(tmp)
            path = history_dir / "ABC.json"
            write_json(path, [{"date": "2026-01-01", "price": 100}])
            merged, changed = history.apply_current_price(history_dir, "abc", 90, date="2026-01-02", dry_run=True)
            self.assertTrue(changed)
            self.assertEqual(merged[-1], {"date": "2026-01-02", "price": 90})
            self.assertEqual(read_json(path), [{"date": "2026-01-01", "price": 100}])

    def test_compress_dir_targets_selected_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            history_dir = Path(tmp)
            write_json(
                history_dir / "ABC.json",
                [{"date": "2026-01-01", "price": 100}, {"date": "2026-01-02", "price": 100}],
            )
            write_json(
                history_dir / "DEF.json",
                [{"date": "2026-01-01", "price": 200}, {"date": "2026-01-02", "price": 200}],
            )
            result = history.compress_dir(history_dir, ids={"ABC"}, dry_run=False)
            self.assertEqual(result["files_changed"], 1)
            self.assertEqual(read_json(history_dir / "ABC.json"), [{"date": "2026-01-01", "price": 100}])
            self.assertEqual(len(read_json(history_dir / "DEF.json")), 2)


class PricingTests(unittest.TestCase):
    def test_bitbns_missing_history_only_skips_existing_history(self) -> None:
        data = {"Group": [{"id": "ABC"}, {"id": "DEF"}]}
        with tempfile.TemporaryDirectory() as tmp:
            history_dir = Path(tmp)
            write_json(history_dir / "ABC.json", [{"date": "2026-01-01", "price": 100}])

            def fake_fetch(product_id: str, delay: float = 0.0) -> list[dict[str, int | str]]:
                return [{"date": "2026-01-02", "price": 200}]

            with patch.object(pricing.bitbns, "fetch_history", fake_fetch):
                result = pricing.update_from_bitbns(
                    data,
                    history_dir=history_dir,
                    ids=None,
                    mode="replace",
                    missing_history_only=True,
                    workers=1,
                    delay=0,
                    dry_run=False,
                )

            self.assertEqual(result["checked"], 1)
            self.assertEqual(result["changed"], 1)
            self.assertEqual(read_json(history_dir / "ABC.json"), [{"date": "2026-01-01", "price": 100}])
            self.assertEqual(read_json(history_dir / "DEF.json"), [{"date": "2026-01-02", "price": 200}])


class CatalogFormatTests(unittest.TestCase):
    def test_scrape_catalog_parallel_writes_raw_catalog_once_per_product(self) -> None:
        class FakeClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def listing_products(self, series: str, limit: int | None = None) -> list[dict[str, str]]:
                return [
                    {"productCode": "ABC", "url": "/in/en/p/test/abc", "summary": "Laptop ABC", "productName": "ABC"},
                    {"productCode": "DEF", "url": "/in/en/p/test/def", "summary": "Laptop DEF", "productName": "DEF"},
                ][:limit]

            def detail(self, url: str):
                sku = url.rsplit("/", 1)[-1].upper()
                return (
                    [{"name": "Home"}, {"name": "Laptops"}, {"name": "Lenovo LOQ Laptops"}],
                    {"name": f"Product {sku}", "image": [f"https://example.test/{sku}.png"], "offers": {"price": "100", "priceCurrency": "INR"}},
                    ([], {}, {}),
                )

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "catalog.json"
            with patch.object(catalog.lenovo, "LenovoCatalogClient", FakeClient):
                result = catalog.scrape_catalog(
                    series=["LOQ"],
                    output=output,
                    only_new=False,
                    existing_files=[],
                    new_ids_output=None,
                    limit_per_series=None,
                    delay=(0, 0),
                    workers=2,
                    verbose=False,
                )
            payload = read_json(output)
            products = [item for rows in payload["groups"].values() for item in rows]
            self.assertEqual(result, {"products": 2, "total": 2, "failed_series": []})
            self.assertEqual(payload["total_products"], 2)
            self.assertEqual([item["id"] for item in products], ["ABC", "DEF"])

    def test_scrape_catalog_only_new_skips_failed_series(self) -> None:
        class FakeClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def listing_products(self, series: str, limit: int | None = None) -> list[dict[str, str]]:
                if series == "Bad":
                    raise RuntimeError("HTTP Error 500")
                return [{"productCode": "ABC", "url": "/in/en/p/test/abc", "summary": "Laptop ABC", "productName": "ABC"}]

            def detail(self, url: str):
                return (
                    [{"name": "Home"}, {"name": "Laptops"}, {"name": "Lenovo LOQ Laptops"}],
                    {"name": "Product ABC", "image": ["https://example.test/ABC.png"], "offers": {"price": "100", "priceCurrency": "INR"}},
                    ([], {}, {}),
                )

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "catalog.json"
            with patch.object(catalog.lenovo, "LenovoCatalogClient", FakeClient):
                result = catalog.scrape_catalog(
                    series=["Bad", "LOQ"],
                    output=output,
                    only_new=True,
                    existing_files=[],
                    new_ids_output=None,
                    limit_per_series=None,
                    delay=(0, 0),
                    workers=1,
                    verbose=False,
                )
            self.assertEqual(result, {"products": 1, "total": 1, "failed_series": ["Bad"]})

    def test_scrape_catalog_rejects_lenovo_internal_codes_as_titles(self) -> None:
        class FakeClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def listing_products(self, series: str, limit: int | None = None) -> list[dict[str, str]]:
                return [
                    {
                        "productCode": "83LTCTO1WWIN2",
                        "url": "/in/en/p/test/83ltcto1wwin2",
                        "summary": "LEN101G0041",
                        "productName": "Legion Pro 5 16ADR10_83LT - Legion Pro 5 16ADR10_02_IN_2CD",
                    }
                ]

            def detail(self, url: str):
                return (
                    [
                        {"name": "Home"},
                        {"name": "Laptops"},
                        {"name": "Legion Laptops"},
                        {"name": "Legion Pro Series"},
                        {"name": "LEN101G0041"},
                        {"name": "88GMY502057"},
                    ],
                    {"name": "LEN101G0041", "image": ["https://example.test/83LT.png"], "offers": {"price": "100", "priceCurrency": "INR"}},
                    ([], {}, {}),
                    "Lenovo Legion Pro 5 Gen 10 (16, AMD) | AMD powered 16-inch gaming laptop | 83LTCTO1WWIN2",
                )

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "catalog.json"
            with patch.object(catalog.lenovo, "LenovoCatalogClient", FakeClient):
                catalog.scrape_catalog(
                    series=["Legion"],
                    output=output,
                    only_new=False,
                    existing_files=[],
                    new_ids_output=None,
                    limit_per_series=None,
                    delay=(0, 0),
                    workers=1,
                    verbose=False,
                )
            product = next(item for rows in read_json(output)["groups"].values() for item in rows)
            self.assertEqual(product["title"], "Lenovo Legion Pro 5 Gen 10 (16, AMD)")
            self.assertNotIn("LEN101G0041", product["breadcrumb_path"])
            self.assertNotIn("88GMY502057", product["breadcrumb_path"])

    def test_scrape_catalog_collapses_repeated_lenovo_jsonld_name(self) -> None:
        class FakeClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def listing_products(self, series: str, limit: int | None = None) -> list[dict[str, str]]:
                return [{"productCode": "83F1CTO1WWIN1", "url": "/in/en/p/test/83f1cto1wwin1", "summary": "LEN101G0042"}]

            def detail(self, url: str):
                repeated = "Legion 5 Gen 10 (15, AMD)Legion 5 Gen 10 (15, AMD)"
                return (
                    [
                        {"name": "Home"},
                        {"name": "Laptops"},
                        {"name": "Legion Laptops"},
                        {"name": "Legion 5 series"},
                        {"name": "Legion 5 Gen 10 (15, AMD)"},
                        {"name": "88GMY502008"},
                    ],
                    {"name": repeated, "image": ["https://example.test/83F1.png"], "offers": {"price": "100", "priceCurrency": "INR"}},
                    ([], {}, {}),
                    "Legion 5 Gen 10 (AMD) | 15-Inch Robust Laptop for Gaming | 83F1CTO1WWIN1",
                )

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "catalog.json"
            with patch.object(catalog.lenovo, "LenovoCatalogClient", FakeClient):
                catalog.scrape_catalog(
                    series=["Legion"],
                    output=output,
                    only_new=False,
                    existing_files=[],
                    new_ids_output=None,
                    limit_per_series=None,
                    delay=(0, 0),
                    workers=1,
                    verbose=False,
                )
            product = next(item for rows in read_json(output)["groups"].values() for item in rows)
            self.assertEqual(product["title"], "Legion 5 Gen 10 (15, AMD)")
            self.assertNotIn("Legion 5 Gen 10 (15, AMD)Legion", product["breadcrumb_path"])

    def test_is_display_name_accepts_valid_model_names_and_rejects_taglines(self) -> None:
        self.assertTrue(catalog.is_display_name("Lenovo LOQ 15IRX10", "83JE01KLIN"))
        self.assertTrue(catalog.is_display_name("LOQ 15IRX9", "83DV01K7IN"))
        self.assertTrue(catalog.is_display_name("ThinkPad E14 Gen 7 (14, Intel)", "21SYS1A900"))
        self.assertTrue(catalog.is_display_name("LOQ Essential 15ARP10", "83S000CRIN"))

        self.assertFalse(catalog.is_display_name("15-inch laptop for students and gamers", "83JE01KLIN"))
        self.assertFalse(catalog.is_display_name("15 inch Intel-powered AI-tuned gaming laptop", "83DV01K7IN"))
        self.assertFalse(catalog.is_display_name("NB LOQ 15IRX10 I7 16G 512G 11S", "83JE01KLIN"))
        self.assertFalse(catalog.is_display_name("83JE01KLIN", "83JE01KLIN"))

    def test_format_catalog_preserves_existing_live_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_path = root / "raw.json"
            out_path = root / "data.json"
            history_dir = root / "history"
            cto_dir = root / "cto"
            history_dir.mkdir()
            cto_dir.mkdir()

            write_json(
                raw_path,
                {
                    "groups": {
                        "Home > Laptops > Lenovo LOQ Laptops": [
                            {
                                "id": "abc",
                                "title": "Lenovo LOQ 15",
                                "summary": "Lenovo LOQ",
                                "store_link": "https://www.lenovo.com/in/en/p/test/abc",
                                "breadcrumb_path": "Home > Laptops > Lenovo LOQ Laptops",
                                "images": ["https://example.test/image.png"],
                                "listing_category_path": ["ROOT"],
                                "specs_by_code": {"LOIS_SCA_CPU": "AMD Ryzen 7 250 Processor"},
                            }
                        ]
                    }
                },
            )
            write_json(history_dir / "ABC.json", [{"date": "2026-01-01", "price": 100}])
            write_json(
                out_path,
                {
                    "Lenovo LOQ Laptops": [
                        {
                            "id": "ABC",
                            "price": "999.00 INR",
                            "mrp": "1200.00 INR",
                            "availability": "in stock",
                            "price_mean": 999,
                            "price_median": 999,
                            "price_usual": 999,
                            "has_history": True,
                            "last_checked": "2026-01-02 00:00:00",
                        }
                    ]
                },
            )

            result = catalog.format_catalog(
                input_path=raw_path,
                output_path=out_path,
                history_dir=history_dir,
                cto_dir=cto_dir,
                existing_data=out_path,
                dry_run=False,
            )
            formatted = read_json(out_path)
            product = formatted["Lenovo LOQ Laptops"][0]
            self.assertEqual(result["formatted"], 1)
            self.assertEqual(result["categories"], 1)
            self.assertEqual(product["price"], "999.00 INR")
            self.assertEqual(product["mrp"], "1200.00 INR")
            self.assertEqual(product["price_mean"], 999)
            self.assertEqual(product["tech_specs"]["processor"]["brand"], "AMD")

    def test_format_catalog_rejects_internal_code_title_from_raw_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_path = root / "raw.json"
            out_path = root / "data.json"
            history_dir = root / "history"
            cto_dir = root / "cto"
            history_dir.mkdir()
            cto_dir.mkdir()

            write_json(
                raw_path,
                {
                    "groups": {
                        "Home > Laptops > Legion Laptops > Legion Pro Series > LEN101G0041 > 88GMY502057": [
                            {
                                "id": "83LTCTO1WWIN2",
                                "title": "LEN101G0041",
                                "summary": "Legion Pro 5 Gen 10, 40.64cms - AMD",
                                "product_name": "Legion Pro 5 16ADR10_83LT - Legion Pro 5 16ADR10_02_IN_2CD",
                                "store_link": "https://www.lenovo.com/in/en/p/test/83ltcto1wwin2",
                                "breadcrumb": [
                                    {"name": "Home"},
                                    {"name": "Laptops"},
                                    {"name": "Legion Laptops"},
                                    {"name": "Legion Pro Series"},
                                    {"name": "LEN101G0041"},
                                    {"name": "88GMY502057"},
                                ],
                                "breadcrumb_path": "Home > Laptops > Legion Laptops > Legion Pro Series > LEN101G0041 > 88GMY502057",
                                "images": ["https://example.test/image.png"],
                                "listing_category_path": ["ROOT"],
                            }
                        ]
                    }
                },
            )
            write_json(
                out_path,
                {
                    "Legion Laptops": [
                        {
                            "id": "83LTCTO1WWIN2",
                            "title": "Legion Pro 5 Gen 10 (16, AMD)",
                            "model_name": "Legion Pro 5 Gen 10 (16, AMD)",
                        }
                    ]
                },
            )

            catalog.format_catalog(
                input_path=raw_path,
                output_path=out_path,
                history_dir=history_dir,
                cto_dir=cto_dir,
                existing_data=out_path,
                dry_run=False,
            )
            product = read_json(out_path)["Legion Laptops"][0]
            self.assertEqual(product["title"], "Legion Pro 5 Gen 10 (16, AMD)")
            self.assertEqual(product["model_name"], "Legion Pro 5 Gen 10 (16, AMD)")
            self.assertNotIn("LEN101G0041", product["full_category"])
            self.assertNotIn("88GMY502057", product["full_category"])

    def test_format_catalog_collapses_repeated_title_from_raw_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_path = root / "raw.json"
            out_path = root / "data.json"
            history_dir = root / "history"
            cto_dir = root / "cto"
            history_dir.mkdir()
            cto_dir.mkdir()
            repeated = "Legion 5 Gen 10 (15, AMD)Legion 5 Gen 10 (15, AMD)"

            write_json(
                raw_path,
                {
                    "groups": {
                        f"Home > Laptops > Legion Laptops > Legion 5 series > Legion 5 Gen 10 (15, AMD) > {repeated}": [
                            {
                                "id": "83F1CTO1WWIN1",
                                "title": repeated,
                                "summary": "LEN101G0042",
                                "store_link": "https://www.lenovo.com/in/en/p/test/83f1cto1wwin1",
                                "series_filter": "Legion",
                                "breadcrumb": ["Home", "Laptops", "Legion Laptops", "Legion 5 series", "Legion 5 Gen 10 (15, AMD)", repeated],
                                "images": ["https://example.test/image.png"],
                                "listing_category_path": ["ROOT"],
                            }
                        ]
                    }
                },
            )
            catalog.format_catalog(
                input_path=raw_path,
                output_path=out_path,
                history_dir=history_dir,
                cto_dir=cto_dir,
                existing_data=None,
                dry_run=False,
            )
            product = read_json(out_path)["Legion Laptops"][0]
            self.assertEqual(product["title"], "Legion 5 Gen 10 (15, AMD)")
            self.assertEqual(product["model_name"], "Legion 5 Gen 10 (15, AMD)")
            self.assertNotIn("Legion 5 Gen 10 (15, AMD)Legion", product["full_category"])

    def test_format_catalog_drops_cto_last_fetched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_path = root / "raw.json"
            out_path = root / "data.json"
            history_dir = root / "history"
            cto_dir = root / "cto"
            history_dir.mkdir()
            cto_dir.mkdir()

            write_json(
                raw_path,
                {
                    "groups": {
                        "Home > Laptops > Lenovo LOQ Laptops": [
                            {
                                "id": "ABCCTO1WWIN1",
                                "title": "Lenovo LOQ CTO",
                                "summary": "Lenovo LOQ",
                                "store_link": "https://www.lenovo.com/in/en/p/test/abccto1wwin1",
                                "breadcrumb_path": "Home > Laptops > Lenovo LOQ Laptops",
                                "images": ["https://example.test/image.png"],
                                "listing_category_path": ["ROOT"],
                            }
                        ]
                    }
                },
            )
            write_json(cto_dir / "ABCCTO1WWIN1.json", {"bundleId": "ABCCTO1WWIN1", "lastFetched": "volatile", "options": []})
            catalog.format_catalog(
                input_path=raw_path,
                output_path=out_path,
                history_dir=history_dir,
                cto_dir=cto_dir,
                existing_data=None,
                dry_run=False,
            )
            product = read_json(out_path)["Lenovo LOQ Laptops"][0]
            self.assertEqual(product["cto_options"], {"bundleId": "ABCCTO1WWIN1", "options": []})


class CtoTests(unittest.TestCase):
    def test_refresh_cto_configs_skips_last_fetched_only_change(self) -> None:
        data = {"Group": [{"id": "ABCCTO1WWIN1"}]}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            write_json(out / "ABCCTO1WWIN1.json", {"bundleId": "ABCCTO1WWIN1", "lastFetched": "old", "options": []})

            with patch.object(cto, "fetch_config", return_value={"bundleId": "ABCCTO1WWIN1", "lastFetched": "new", "options": []}):
                result = cto.refresh_cto_configs(data, output_dir=out, workers=1, dry_run=False)

            self.assertEqual(result["checked"], 1)
            self.assertEqual(result["changed"], 0)
            self.assertEqual(read_json(out / "ABCCTO1WWIN1.json"), {"bundleId": "ABCCTO1WWIN1", "lastFetched": "old", "options": []})


class ArchiveTests(unittest.TestCase):
    def test_archive_body_text_fallback_ignored_when_structured_status_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            html_dir = Path(tmp)
            html_dir.joinpath("ABC.json").write_text("", encoding="utf-8")
            html_dir.joinpath("ABC.html").write_text(
                '<meta name="productstatus" content="Available"/>'
                '<script type="application/ld+json">'
                '{"@type":"Product","sku":"ABC","offers":{"availability":"https://schema.org/InStock"}}'
                "</script>"
                "<div>Recommended model is out of stock</div>",
                encoding="utf-8",
            )
            result = archive_logic.check_product({"id": "ABC", "store_link": "https://www.lenovo.com/in/en/p/test/abc"}, html_dir=html_dir)
            self.assertFalse(result["archive"])
            self.assertEqual(result["reasons"], [])


if __name__ == "__main__":
    unittest.main()
