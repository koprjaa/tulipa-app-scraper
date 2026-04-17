"""Discovery / debugging helpers — not part of the core scrape path.

Used by `--discover`, `--list-browse`, `--test-actions` CLI modes to poke at the
Helios API without committing to a full scrape.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from tulipa_app_scraper.infrastructure.config import Settings
from tulipa_app_scraper.infrastructure.helios_client import HeliosClient


class Discovery:
    def __init__(
        self,
        client: HeliosClient,
        settings: Settings,
        logger: logging.Logger | None = None,
    ) -> None:
        self.client = client
        self.settings = settings
        self.logger = logger or logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # --discover
    # ------------------------------------------------------------------

    def discover_categories(self) -> dict[str, dict[str, Any]]:
        """Probe a hardcoded list of candidate category names to see which exist."""
        candidates = (
            "Dnešní nabídka", "Dnes", "Nabídka", "Aktuální", "Speciální",
            "Akce", "Sleva", "Promo", "Týdenní", "Měsíční",
            "Novinky", "Trendy", "Sezónní", "Výprodej", "Doporučené",
        )
        found: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            self.logger.info(f"Probing category: '{candidate}'")
            response = self.client.run_external_action(
                self.settings.action_subgroups, parameters=[candidate]
            )
            if not HeliosClient.is_success(response):
                found[candidate] = {"exists": False}
                continue
            try:
                rows = response["result"][0]["fields"]["Result"]["table"]["rows"]  # type: ignore[index]
            except (KeyError, IndexError, TypeError):
                found[candidate] = {"exists": True, "subgroups_count": "unknown"}
                continue
            sample = [r[2]["Value"] for r in rows[:3] if len(r) > 2] if rows else []
            found[candidate] = {
                "exists": True,
                "subgroups_count": len(rows) if rows else 0,
                "sample_subgroups": sample,
            }
            time.sleep(0.1)
        return found

    # ------------------------------------------------------------------
    # --list-browse
    # ------------------------------------------------------------------

    def list_browse_definitions(self) -> list[dict[str, Any]]:
        response = self.client.get_browse(browse_name=None)
        if not HeliosClient.is_success(response):
            return []
        try:
            rows = response["result"][0]["fields"]["Result"]["table"]["rows"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            return []
        browse_list: list[dict[str, Any]] = []
        for row in rows:
            info: dict[str, Any] = {}
            for field in row:
                info[field.get("FieldName", "")] = field.get("Value", "")
            browse_list.append(info)
        return browse_list

    # ------------------------------------------------------------------
    # --test-actions
    # ------------------------------------------------------------------

    def test_action_ids(self) -> dict[str, dict[str, Any]]:
        """Call each known ActionID with its expected parameter shape and record success/failure."""
        tests: list[tuple[str, list, str]] = [
            (self.settings.action_kontakty, ["12095", "15303"], "Kontakty with params"),
            (self.settings.action_kategorie_100, ["100"], "Kategorie with param 100"),
            (self.settings.action_kategorie_300, ["300"], "Kategorie with param 300"),
            (self.settings.action_browse_metadata, [], "Browse metadata, no params"),
            (self.settings.action_subgroups, ["Aranž"], "Subgroups for Aranž"),
            (self.settings.action_subgroups, ["Sezón"], "Subgroups for Sezón"),
        ]
        results: dict[str, dict[str, Any]] = {}
        for action_id, params, description in tests:
            self.logger.info(f"Testing {description}: {action_id}")
            response = self.client.run_external_action(action_id, parameters=params)
            if HeliosClient.is_success(response):
                data = response["result"][0]["fields"].get("Result", {})  # type: ignore[index]
                row_count = None
                if isinstance(data, dict) and "table" in data:
                    row_count = len(data["table"].get("rows", []))
                results[action_id] = {
                    "success": True,
                    "description": description,
                    "params": params,
                    "row_count": row_count,
                }
                self.logger.info(f"  ✓ {description} — OK ({row_count if row_count is not None else '?'} rows)")
            else:
                err = (
                    response["result"][0]["fields"].get("ErrorMessage", "unknown")  # type: ignore[index]
                    if response
                    else "no response"
                )
                results[action_id] = {
                    "success": False,
                    "description": description,
                    "params": params,
                    "error": err,
                }
                self.logger.info(f"  ✗ {description} — FAILED: {err}")
        return results
