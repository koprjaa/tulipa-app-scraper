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
        self.client.activate_database()

        additional_categories = self._fetch_additional_categories()
        extra_products = self._fetch_extra_products()
        category_products = self._fetch_products_for_known_categories()
        all_category_products = self._fetch_all_products_from_categories()

        main_group_products = self._fetch_main_group_products()

        combined: list[dict] = []
        combined.extend(main_group_products)
        combined.extend(extra_products)
        combined.extend(category_products)
        combined.extend(all_category_products)
        combined.extend(self._synthesize_category_rows(additional_categories))
        return combined

    # ====================================================================
    # Main groups → subgroups → products
    # ====================================================================

    def _fetch_main_group_products(self) -> list[dict]:
        products: list[dict] = []
        for group in self.settings.main_groups:
            self.logger.info(f"Main group: {group}")
            self.client.activate_database()
            subgroups = self._fetch_subgroups(group)
            for subgroup_code, subgroup_name in subgroups.items():
                self.client.activate_database()
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

    def _fetch_products_for_known_categories(self) -> list[dict]:
        self.logger.info("Fetching products for known categories...")
        all_products: list[dict] = []
        for category in self.settings.known_categories:
            self.logger.info(f"  Category: {category}")
            response = self.client.run_external_action(
                self.settings.action_products, parameters=[category, category]
            )
            if not HeliosClient.is_success(response):
                continue
            try:
                rows = response["result"][0]["fields"]["Result"]["table"]["rows"]  # type: ignore[index]
            except (KeyError, IndexError, TypeError):
                continue
            for row in rows:
                try:
                    product = self._row_to_dict(row)
                except (IndexError, KeyError, TypeError):
                    continue
                product["HlavniSkupina"] = "Kategorie"
                product["PodskupinaKod"] = category
                product["PodskupinaNazev"] = category
                all_products.append(product)
        self.logger.info(f"  Collected {len(all_products)} products from known categories")
        return all_products

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
        if not (isinstance(result, dict) and "table" in result):
            return []
        extra_products: list[dict] = []
        for row in result["table"]["rows"]:
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

    def _fetch_additional_categories(self) -> dict[str, dict[str, Any]]:
        self.logger.info("Fetching additional categories with counts...")
        response = self.client.run_external_action(
            self.settings.action_kategorie_300, parameters=["300"]
        )
        if not HeliosClient.is_success(response):
            return {}
        try:
            rows = response["result"][0]["fields"]["Result"]["table"]["rows"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            return {}
        categories: dict[str, dict[str, Any]] = {}
        for row in rows:
            try:
                cat_id = row[0].get("Value", "")
                cat_code = row[1].get("Value", "")
                cat_name = row[2].get("Value", "")
                count = row[3].get("Value", "0")
                categories[cat_code] = {
                    "id": cat_id,
                    "name": cat_name,
                    "count": int(count) if count.isdigit() else 0,
                }
            except (IndexError, KeyError, ValueError):
                continue
        self.logger.info(f"  Got {len(categories)} additional categories")
        return categories

    def _fetch_all_products_from_categories(self) -> list[dict]:
        """Walk every additional category → every subgroup → every product."""
        self.logger.info("Fetching all products via category workflow...")
        response = self.client.run_external_action(
            self.settings.action_kategorie_300, parameters=["300"]
        )
        if not HeliosClient.is_success(response):
            return []
        try:
            cat_rows = response["result"][0]["fields"]["Result"]["table"]["rows"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            return []

        products: list[dict] = []
        for row in cat_rows:
            try:
                cat_code = row[1].get("Value", "")
                cat_name = row[2].get("Value", "")
                count = row[3].get("Value", "0")
            except (IndexError, KeyError):
                continue
            if not count or not count.isdigit() or int(count) == 0:
                continue
            self.logger.info(f"  Category: {cat_code} — {cat_name} ({count} products)")
            products.extend(self._walk_category_subgroups(cat_code, cat_name))
        self.logger.info(f"  Total {len(products)} products across categories")
        return products

    def _walk_category_subgroups(self, cat_code: str, cat_name: str) -> list[dict]:
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
                p["HlavniSkupina"] = "Kategorie"
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

    @staticmethod
    def _synthesize_category_rows(
        additional_categories: dict[str, dict[str, Any]],
    ) -> list[dict]:
        """Produce synthetic CSV rows that represent each category as its own entity."""
        rows: list[dict] = []
        for code, info in additional_categories.items():
            rows.append(
                {
                    "HlavniSkupina": "Kategorie",
                    "PodskupinaKod": code,
                    "PodskupinaNazev": info["name"],
                    "Nazev1": f"KATEGORIE: {info['name']}",
                    "RegCis": f"CAT_{code}",
                    "_cena_cu1": "0",
                    "Mnozstvi": str(info["count"]),
                    "ID": info["id"],
                    "JizNaSklade": str(info["count"]),
                    "K1": code,
                    "K2": info["name"],
                    "Nazev": f"KATEGORIE: {info['name']}",
                    "NazevK1": code,
                    "NazevK2": info["name"],
                    "PrepMnozstvi": str(info["count"]),
                    "SkupZbo": code,
                    "_Tulipa_Zkratka": code,
                }
            )
        return rows

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
        return {field.get("FieldName", ""): field.get("Value", "") for field in row}
