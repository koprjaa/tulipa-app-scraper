#
# Project: tulipa-app-scraper
# File:    scraper.py
#
# Description:
# Walks the groups, subgroups, and categories and collects every product row.
#
# Author:
# Jan Alexandr Kopřiva
# jan.alexandr.kopriva@gmail.com
#
# License: MIT
#

"""High-level scrape orchestration — walks groups, subgroups, and categories.

Two entry points:
- `TulipaScraper.scrape_via_browse()` — fast path using `GetBrowse` on browse 82.
- `TulipaScraper.scrape_via_actions()` — exhaustive path using `RunExternalAction`
  across HLAVNI_SKUPINY, extra products, category products, and the full
  categories-workflow (fetch_all_products_from_categories logic).

Both return a list of product dicts ready for CSV serialisation.
"""
from __future__ import annotations

import logging
from typing import Any

from tulipa_app_scraper.infrastructure.config import Settings
from tulipa_app_scraper.infrastructure.helios_client import HeliosClient


class TulipaScraper:
    def __init__(
        self,
        client: HeliosClient,
        settings: Settings,
        logger: logging.Logger | None = None,
    ) -> None:
        self.client = client
        self.settings = settings
        self.logger = logger or logging.getLogger(__name__)

    # ====================================================================
    # Entry points
    # ====================================================================

    def scrape_via_browse(self) -> list[dict]:
        """Fast path — single `GetBrowse` call returns all products at once."""
        self.logger.info("=== Phase 1: scrape via GetBrowse ===")
        self.client.activate_database()
        products = self._get_browse_rows(self.settings.browse_name_products)
        if not products:
            self.logger.warning("GetBrowse returned nothing; falling back to RunExternalAction")
            return self.scrape_via_actions()
        for p in products:
            p.setdefault("HlavniSkupina", "Browse")
            p.setdefault("PodskupinaKod", p.get("SkupZbo", ""))
            p.setdefault("PodskupinaNazev", p.get("SkupZbo", ""))
        self.logger.info(f"Browse path collected {len(products)} products")
        return products

    def scrape_via_actions(self) -> list[dict]:
        """Exhaustive path — several sweeps across main groups + categories."""
        self.logger.info("=== Phase 1: scrape via RunExternalAction ===")

        combined: list[dict] = []
        combined.extend(self._fetch_main_group_products())
        combined.extend(self._fetch_extra_products())
        for series, label in self.settings.category_series.items():
            combined.extend(self._fetch_all_products_from_categories(series, label))

        # Tentýž produkt může přijít z víc průchodů (Extra má jiné ID, ale stejný EAN);
        # nechat první, nejúplnější výskyt.
        unique: dict[str, dict] = {}
        for product in combined:
            unique.setdefault(product.get("_EAN") or product.get("ID") or str(id(product)), product)
        self.logger.info(f"Collected {len(unique)} unique products ({len(combined)} rows before dedup)")
        return list(unique.values())

    # ====================================================================
    # Main groups → subgroups → products
    # ====================================================================

    def _fetch_main_group_products(self) -> list[dict]:
        products: list[dict] = []
        # Hlavní skupiny jsou kategorie řady 200; seznam natvrdo jen jako záloha, když server neodpoví.
        groups = [c["K1"] for c in self._fetch_category_list("200")] or list(self.settings.main_groups)
        for group in groups:
            self.logger.info(f"Main group: {group}")
            subgroups = self._fetch_subgroups(group)
            for subgroup_code, subgroup_name in subgroups.items():
                for product in self._fetch_products(group, subgroup_code, subgroup_name):
                    product["HlavniSkupina"] = group
                    product["PodskupinaKod"] = subgroup_code
                    product["PodskupinaNazev"] = subgroup_name
                    products.append(product)
        return products

    def _fetch_subgroups(self, group_name: str) -> dict[str, str]:
        self.logger.info(f"  Fetching subgroups for '{group_name}'")
        response = self.client.run_external_action(
            self.settings.action_subgroups, parameters=[group_name]
        )
        if not HeliosClient.is_success(response):
            self.logger.warning(f"  Failed to fetch subgroups for '{group_name}'")
            return {}

        try:
            rows = response["result"][0]["fields"]["Result"]["table"]["rows"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            return {}

        result: dict[str, str] = {}
        for row in rows:
            try:
                code, name = row[1]["Value"], row[2]["Value"]
                if code:
                    result[code] = name
            except (IndexError, KeyError):
                continue
        self.logger.info(f"  Found {len(result)} subgroups for '{group_name}'")
        return result

    def _fetch_products(
        self, group: str, subgroup_code: str, subgroup_name: str
    ) -> list[dict]:
        self.logger.info(f"    Fetching products for '{group} / {subgroup_name} ({subgroup_code})'")
        response = self.client.run_external_action(
            self.settings.action_products, parameters=[group, subgroup_code]
        )
        if not HeliosClient.is_success(response):
            return []
        rows = self._extract_rows(response["result"][0]["fields"]["Result"])  # type: ignore[index]
        products = [self._row_to_dict(row) for row in (rows or [])]
        self.logger.info(f"    Got {len(products)} products")
        return products

    # ====================================================================
    # Known extra categories + misc sweeps
    # ====================================================================

    def _fetch_extra_products(self) -> list[dict]:
        self.logger.info("Fetching extra products from browse metadata...")
        response = self.client.run_external_action(
            self.settings.action_browse_metadata, parameters=[]
        )
        if not HeliosClient.is_success(response):
            return []
        try:
            result = response["result"][0]["fields"]["Result"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            return []
        extra_products: list[dict] = []
        for row in self._extract_rows(result) or []:
            try:
                product = self._row_to_dict(row)
            except (IndexError, KeyError, TypeError):
                continue
            product["HlavniSkupina"] = "Extra"
            product["PodskupinaKod"] = "Extra3"
            product["PodskupinaNazev"] = "Extra produkty 3"
            extra_products.append(product)
        self.logger.info(f"  Got {len(extra_products)} extra products")
        return extra_products

    def _fetch_category_list(self, series: str) -> list[dict]:
        """Categories of one series (100 cut flowers, 200 main groups, 300 florist supplies) with stock."""
        response = self.client.run_external_action(
            self.settings.action_kategorie_300, parameters=[series]
        )
        if not HeliosClient.is_success(response):
            self.logger.warning(f"  Failed to fetch category series {series}")
            return []
        rows = self._extract_rows(response["result"][0]["fields"].get("Result")) or []  # type: ignore[index]
        categories = [self._row_to_dict(row) for row in rows]
        return [c for c in categories if c.get("K1") and str(c.get("Pocet", "")).isdigit() and int(c["Pocet"]) > 0]

    def _fetch_all_products_from_categories(self, series: str, label: str) -> list[dict]:
        """Walk every category of a series → every subgroup → every product."""
        self.logger.info(f"Fetching all products via category series {series} ({label})...")
        products: list[dict] = []
        for category in self._fetch_category_list(series):
            cat_code, cat_name, expected = category["K1"], category.get("K1Name", ""), int(category["Pocet"])
            self.logger.info(f"  Category: {cat_code} — {cat_name} ({expected} products)")
            got = self._walk_category_subgroups(cat_code, cat_name, label)
            if len(got) < expected:
                self.logger.warning(f"  Category {cat_code}: server reports {expected}, collected {len(got)}")
            products.extend(got)
        self.logger.info(f"  Total {len(products)} products in series {series}")
        return products

    def _walk_category_subgroups(self, cat_code: str, cat_name: str, label: str) -> list[dict]:
        response = self.client.run_external_action(
            self.settings.action_subgroups, parameters=[cat_code]
        )
        if not HeliosClient.is_success(response):
            return []
        try:
            rows = response["result"][0]["fields"]["Result"]["table"]["rows"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            return []

        results: list[dict] = []
        for row in rows:
            subgroup_code, subgroup_name = None, None
            for field in row:
                if field.get("FieldName") == "K2":
                    subgroup_code = field.get("Value")
                elif field.get("FieldName") == "K2Name":
                    subgroup_name = field.get("Value")
            if not subgroup_code:
                continue
            for p in self._fetch_products_for_subgroup(cat_code, subgroup_code, subgroup_name or ""):
                p["HlavniSkupina"] = label
                p["PodskupinaKod"] = cat_code
                p["PodskupinaNazev"] = subgroup_name or cat_name
                results.append(p)
        return results

    def _fetch_products_for_subgroup(
        self, cat_code: str, subgroup_code: str, subgroup_name: str
    ) -> list[dict]:
        response = self.client.run_external_action(
            self.settings.action_products, parameters=[cat_code, subgroup_code]
        )
        if not HeliosClient.is_success(response):
            return []
        try:
            result = response["result"][0]["fields"]["Result"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            return []
        rows = self._extract_rows(result)
        return [self._row_to_dict(row) for row in (rows or [])]

    # ====================================================================
    # Low-level helpers for response parsing
    # ====================================================================

    def _get_browse_rows(self, browse_name: str) -> list[dict]:
        response = self.client.get_browse(browse_name)
        if not HeliosClient.is_success(response):
            self.logger.warning(f"GetBrowse '{browse_name}' failed")
            return []
        try:
            result = response["result"][0]["fields"]["Result"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            return []
        rows = self._extract_rows(result)
        if not rows:
            return []
        return [self._row_to_dict(row) for row in rows]

    @staticmethod
    def _extract_rows(result: Any) -> list | None:
        """Helios returns rows in several shapes — `{table: {rows}}`, `{fields: {QueryBrowse: {table: {rows}}}}`, bare list."""
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            if "table" in result:
                return result["table"].get("rows", [])
            if (
                "fields" in result
                and isinstance(result["fields"], dict)
                and "QueryBrowse" in result["fields"]
            ):
                qb = result["fields"]["QueryBrowse"]
                if isinstance(qb, dict) and "table" in qb:
                    return qb["table"].get("rows", [])
        return None

    @staticmethod
    def _row_to_dict(row: list[dict]) -> dict:
        product = {field.get("FieldName", ""): field.get("Value", "") for field in row}
        # Helios vrací část cen s desetinnou čárkou ("7,5"), zbytek jako celá čísla; sjednotit na tečku.
        for key, value in product.items():
            if key.startswith("_cena_cu") and isinstance(value, str):
                product[key] = value.replace(",", ".")
        return product
