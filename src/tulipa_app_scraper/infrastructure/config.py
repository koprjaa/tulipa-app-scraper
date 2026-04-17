"""Centralised configuration — URL, credentials, timeouts, paths, ActionIDs.

Defaults are copied from the reverse-engineered Tulipa B2B client. Any of them
can be overridden via environment variables; `.env` is loaded automatically.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    """All runtime configuration. Instantiate via `Settings.from_env()` for env overrides."""

    # ----- Helios endpoint -----
    base_url: str = "https://eserver.tulipapraha.com:4343"
    endpoint: str = "/datasnap/rest/THeliosMethods/%22Execute%22"
    username: str = "eserver_mat"
    password: str = "Mat100953"  # noqa: S105 — Tulipa's own public client cred
    request_timeout: int = 30

    # ----- Paths -----
    data_dir: Path = field(default_factory=lambda: Path("data"))
    session_file: Path = field(default_factory=lambda: Path("data") / "tulipa_session.json")

    # ----- Main groups to iterate -----
    main_groups: tuple[str, ...] = ("Dekor", "Kveto", "Sezón", "Sukul")

    # ----- ActionIDs (reverse-engineered) -----
    action_subgroups: str = "7C100193-68DF-4C59-8692-33E421EEBCD3"
    action_products: str = "7DCCBAB9-35EA-4310-BC22-B7AC873F9398"
    action_kontakty: str = "E0E9F6FC-D077-49D9-BB05-4C8056F669E8"
    action_kategorie_100: str = "44465692-619A-41AD-A578-ADB755659D0B"
    action_kategorie_300: str = "44465692-619A-41AD-A578-ADB755659D0B"
    action_browse_metadata: str = "BC3642D5-D287-4CFE-A3CB-566DA8A126E0"
    action_product_details: str = "9752DF9E-E95E-46F2-97F8-11F96ABEB71C"
    action_product_images: str = "982F0820-87A5-4F22-A2E9-724E14C208E1"
    browse_name_products: str = "82"

    # ----- Known "extra" categories (for fetch_products_for_categories) -----
    known_categories: tuple[str, ...] = (
        "Aranž", "Deko", "Dráty", "Fólie", "Funkč", "Hnoji", "Lesky", "Nářad",
        "Obaly", "Osiva", "Ostat", "Papír", "Pásky", "Rafie", "Manip", "Sklo",
        "Stuhy", "Subst", "Svíčk", "Špend", "Výživ",
    )

    # ----- Inventory reserve (used by downstream consumers via CLI flags) -----
    safety_reserve: int = 5
    reserve_threshold: int = 20

    # ----- Session -----
    session_timeout_minutes: int = 45

    # ----- Loop -----
    loop_interval_seconds: int = 1800
    loop_error_wait_seconds: int = 300

    # ----- Debug -----
    debug: bool = False

    @property
    def full_url(self) -> str:
        return f"{self.base_url}{self.endpoint}"

    @classmethod
    def from_env(cls) -> Settings:
        """Build a `Settings` using env vars to override defaults where present."""
        defaults = cls()
        return cls(
            base_url=os.getenv("HELIOS_URL") or defaults.base_url,
            username=os.getenv("HELIOS_USERNAME") or defaults.username,
            password=os.getenv("HELIOS_PASSWORD") or defaults.password,
        )
