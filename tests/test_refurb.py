from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from laptopdeals import pricing
from laptopdeals.providers import get_provider, refurb
from laptopdeals.sources import lenovo_outlet


class TestRefurb(unittest.TestCase):
    def test_absolute_outlet_url(self):
        self.assertEqual(
            lenovo_outlet.absolute_outlet_url("/p/laptops/ideapad/82x6r000r0"),
            "https://www.lenovo.com/in/outletin/en/p/laptops/ideapad/82x6r000r0",
        )
        self.assertEqual(
            lenovo_outlet.absolute_outlet_url("https://www.lenovo.com/in/outletin/en/p/laptops/ideapad/82x6r000r0"),
            "https://www.lenovo.com/in/outletin/en/p/laptops/ideapad/82x6r000r0",
        )

    def test_build_affiliate_link(self):
        store_link = "https://www.lenovo.com/in/outletin/en/p/laptops/ideapad/82x6r000r0"
        link = lenovo_outlet.build_affiliate_link(store_link, "82X6R000R0")
        self.assertIn("prodsku=82X6R000R0", link)
        self.assertIn("lenovo-in.zlvv.net", link)
        self.assertIn("intsrc=CATF_4639", link)

    def test_classification_to_spec_maps(self):
        classification = [
            {"a": "Processor", "b": "13th Gen Intel Core i3-1315U"},
            {"a": "Memory", "b": "8 GB LPDDR5-4800MHz (Soldered)"},
            {"a": "Storage", "b": "512 GB SSD M.2 2242 PCIe Gen4 TLC"},
            {"a": "Display", "b": "35.56cms (14) FHD (1920 x 1080), IPS, Anti-Glare"},
            {"a": "Graphic Card", "b": "Integrated Intel UHD Graphics"},
        ]
        rows, by_label, by_code = lenovo_outlet.classification_to_spec_maps(classification)
        self.assertEqual(len(rows), 5)
        self.assertEqual(by_label["Processor"], "13th Gen Intel Core i3-1315U")
        self.assertEqual(by_code["LOIS_SCA_CPU"], "13th Gen Intel Core i3-1315U")
        self.assertEqual(by_code["LOIS_SCA_MEM"], "8 GB LPDDR5-4800MHz (Soldered)")
        self.assertEqual(by_code["LOIS_SCA_HDD"], "512 GB SSD M.2 2242 PCIe Gen4 TLC")
        self.assertEqual(by_code["LOIS_SCA_VIDEO"], "Integrated Intel UHD Graphics")

    def test_category_from_product(self):
        self.assertEqual(refurb.category_from_product({"url": "/p/laptops/ideapad/300/82x6"}), "Ideapad")
        self.assertEqual(refurb.category_from_product({"url": "/p/laptops/thinkpad/x1/21kg"}), "ThinkPad")
        self.assertEqual(refurb.category_from_product({"url": "/p/laptops/yoga/9i/83cv"}), "Yoga")
        self.assertEqual(refurb.category_from_product({"url": "/p/laptops/legion/pro7/83de"}), "Legion Laptops")
        self.assertEqual(refurb.category_from_product({"url": "/p/laptops/lenovo-laptops/v-series/v14/83a0"}), "Lenovo V-series")
        self.assertEqual(refurb.category_from_product({"url": "/p/laptops/thinkbook/14/21kg"}), "Thinkbook")

    def test_clean_refurb_title_and_model_name(self):
        title = "IdeaPad Slim 3 14 - Intel i3, 8 GB RAM, 512 SSD | Lenovo India"
        cleaned = refurb.clean_refurb_title(title, "82X6R000R0")
        self.assertEqual(cleaned, "IdeaPad Slim 3 14 - Intel i3, 8 GB RAM, 512 SSD")
        model = refurb.model_name_from_title(cleaned)
        self.assertEqual(model, "IdeaPad Slim 3 14")

    def test_format_catalog_and_archive(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            raw_file = tmp / "raw.json"
            app_file = tmp / "data-refurb.json"
            arch_file = tmp / "archive-refurb.json"
            hist_dir = tmp / "history"
            hist_dir.mkdir()

            raw_payload = {
                "groups": {
                    "Ideapad": [
                        {
                            "id": "82X6R000R0",
                            "product_code": "82X6R000R0",
                            "title": "IdeaPad Slim 3 14",
                            "summary": "14 inch lightweight laptop",
                            "price": 30190,
                            "mrp": 52758,
                            "availability": "out of stock",
                            "store_link": "https://www.lenovo.com/in/outletin/en/p/laptops/ideapad/82x6r000r0",
                            "images": ["https://example.com/hero.jpg"],
                            "specs_by_code": {
                                "LOIS_SCA_CPU": "13th Gen Intel Core i3-1315U",
                                "LOIS_SCA_MEM": "8 GB LPDDR5-4800MHz (Soldered)",
                                "LOIS_SCA_HDD": "512 GB SSD M.2 2242 PCIe Gen4 TLC",
                                "LOIS_SCA_DPY": "35.56cms (14) FHD (1920 x 1080)",
                            },
                        }
                    ]
                }
            }
            raw_file.write_text(json.dumps(raw_payload))

            res = refurb.format_catalog(
                input_path=raw_file,
                output_path=app_file,
                archive_path=arch_file,
                history_dir=hist_dir,
            )
            self.assertEqual(res["formatted"], 1)
            self.assertTrue(app_file.exists())

            formatted_data = json.loads(app_file.read_text())
            self.assertIn("Ideapad", formatted_data)
            prod = formatted_data["Ideapad"][0]
            self.assertEqual(prod["id"], "82X6R000R0")
            self.assertEqual(prod["price"], "30190.00 INR")
            self.assertEqual(prod["mrp"], "52758.00 INR")
            self.assertEqual(prod["availability"], "out of stock")
            self.assertEqual(prod["product_condition"], "CERTIFIED REFURBISHED")

    def test_update_from_refurb(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            hist_dir = tmp / "history"
            hist_dir.mkdir()

            data = {
                "Ideapad": [
                    {
                        "id": "82XQR002R0",
                        "price": "35000.00 INR",
                        "availability": "unknown",
                        "store_link": "https://www.lenovo.com/in/outletin/en/p/laptops/ideapad/82xqr002r0",
                    }
                ]
            }

            with patch.object(
                lenovo_outlet.LenovoOutletClient,
                "fetch_batch_inventory",
                return_value={"82XQR002R0": "in stock"},
            ), patch.object(
                lenovo_outlet.LenovoOutletClient,
                "fetch_batch_prices",
                return_value={"82XQR002R0": (37891, 56541)},
            ):
                result = pricing.update_from_refurb(
                    data,
                    history_dir=hist_dir,
                    delay_min=0,
                    delay_max=0,
                )
                self.assertEqual(result["checked"], 1)
                self.assertEqual(result["changed"], 1)
                self.assertEqual(data["Ideapad"][0]["price"], "37891.00 INR")
                self.assertEqual(data["Ideapad"][0]["availability"], "in stock")


    def test_archive_stale_and_unarchive_returning(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            raw_file = tmp / "raw.json"
            app_file = tmp / "data-refurb.json"
            arch_file = tmp / "archive-refurb.json"
            hist_dir = tmp / "history"
            hist_dir.mkdir()

            # 1. First format with 2 products (A and B)
            raw_payload_1 = {
                "groups": {
                    "Ideapad": [
                        {
                            "id": "82X6R000R0",
                            "product_code": "82X6R000R0",
                            "title": "IdeaPad Slim 3 14",
                            "price": 30000,
                            "mrp": 50000,
                            "availability": "in stock",
                            "store_link": "https://example.com/a",
                        },
                        {
                            "id": "82XQR002R0",
                            "product_code": "82XQR002R0",
                            "title": "IdeaPad Flex 5",
                            "price": 40000,
                            "mrp": 60000,
                            "availability": "in stock",
                            "store_link": "https://example.com/b",
                        },
                    ]
                }
            }
            raw_file.write_text(json.dumps(raw_payload_1))
            res1 = refurb.format_catalog(
                input_path=raw_file,
                output_path=app_file,
                archive_path=arch_file,
                history_dir=hist_dir,
            )
            self.assertEqual(res1["formatted"], 2)

            # 2. Second format: Product B is dropped (stale). It must be retained in data-refurb.json as out of stock & recorded in archive.
            raw_payload_2 = {
                "groups": {
                    "Ideapad": [
                        {
                            "id": "82X6R000R0",
                            "product_code": "82X6R000R0",
                            "title": "IdeaPad Slim 3 14",
                            "price": 30000,
                            "mrp": 50000,
                            "availability": "in stock",
                            "store_link": "https://example.com/a",
                        },
                    ]
                }
            }
            raw_file.write_text(json.dumps(raw_payload_2))
            res2 = refurb.format_catalog(
                input_path=raw_file,
                output_path=app_file,
                archive_path=arch_file,
                history_dir=hist_dir,
                existing_data=app_file,
            )
            self.assertEqual(res2["formatted"], 2)
            data_after = json.loads(app_file.read_text())["Ideapad"]
            b_prod = next(p for p in data_after if p["id"] == "82XQR002R0")
            self.assertTrue(b_prod["archived"])
            self.assertEqual(b_prod["availability"], "out of stock")

            archived = json.loads(arch_file.read_text())["products"]
            self.assertEqual(len(archived), 1)
            self.assertEqual(archived[0]["id"], "82XQR002R0")
            self.assertTrue(archived[0]["archived"])
            self.assertEqual(archived[0]["availability"], "out of stock")

            # 3. Third format: Product B returns back in stock! It must be updated to in stock.
            raw_file.write_text(json.dumps(raw_payload_1))
            res3 = refurb.format_catalog(
                input_path=raw_file,
                output_path=app_file,
                archive_path=arch_file,
                history_dir=hist_dir,
                existing_data=app_file,
            )
            self.assertEqual(res3["formatted"], 2)
            data_after3 = json.loads(app_file.read_text())["Ideapad"]
            b_prod3 = next(p for p in data_after3 if p["id"] == "82XQR002R0")
            self.assertEqual(b_prod3["availability"], "in stock")
            archived_after = json.loads(arch_file.read_text())["products"]
            self.assertEqual(len(archived_after), 0)


if __name__ == "__main__":
    unittest.main()
