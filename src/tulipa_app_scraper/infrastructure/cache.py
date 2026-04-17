"""File-based cache for scraped product CSVs, keyed by date folder."""
from __future__ import annotations

import glob
import os
from datetime import datetime, timedelta
from pathlib import Path

from tulipa_app_scraper.infrastructure.config import Settings


class CacheStore:
    """Manages dated CSV cache under `<data_dir>/YYYY-MM-DD/<base>_<timestamp>.csv`."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def new_cache_path(self, base_name: str = "produkty_komplet") -> Path:
        """Return a fresh dated path (creates the date folder)."""
        date_str = datetime.now().strftime("%Y-%m-%d")
        date_dir = self.settings.data_dir / date_str
        date_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return date_dir / f"{base_name}_{timestamp}.csv"

    def find_latest(self, base_name: str = "produkty_komplet") -> Path | None:
        """Find the newest cache file across all date folders, or None."""
        pattern = str(self.settings.data_dir / "*" / f"{base_name}_*.csv")
        files = glob.glob(pattern)
        if not files:
            return None
        return Path(max(files, key=os.path.getmtime))

    @staticmethod
    def is_fresh(file_path: Path, max_age_hours: int = 1) -> bool:
        """Return True if the file exists and is newer than `max_age_hours`."""
        if not file_path.exists():
            return False
        age = datetime.now() - datetime.fromtimestamp(file_path.stat().st_mtime)
        return age < timedelta(hours=max_age_hours)

    def cleanup_old(self, base_name: str = "produkty_komplet", keep_hours: int = 24) -> int:
        """Delete cache files older than `keep_hours`. Returns count deleted."""
        if not self.settings.data_dir.exists():
            return 0
        pattern = str(self.settings.data_dir / "*" / f"{base_name}_*.csv")
        cutoff = datetime.now() - timedelta(hours=keep_hours)
        deleted = 0
        for file_str in glob.glob(pattern):
            file_path = Path(file_str)
            if datetime.fromtimestamp(file_path.stat().st_mtime) < cutoff:
                try:
                    file_path.unlink()
                    deleted += 1
                except OSError:
                    pass
        return deleted
