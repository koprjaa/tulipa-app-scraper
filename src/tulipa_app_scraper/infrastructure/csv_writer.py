"""CSV serialisation and de-serialisation for scraped product rows."""
from __future__ import annotations

import csv
import logging
from pathlib import Path

# Columns promoted to the front of the CSV for readability.
_KEY_COLUMNS = (
    "HlavniSkupina",
    "PodskupinaKod",
    "PodskupinaNazev",
    "Nazev1",
    "RegCis",
    "_cena_cu1",
    "Mnozstvi",
)


class CSVStore:
    """Writes / reads the flat CSV dump of scraped Helios rows."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    def write(self, products: list[dict], output_file: Path) -> bool:
        """Write `products` to `output_file` as UTF-8-sig CSV with `;` separator."""
        if not products:
            self.logger.warning("No products to write")
            return False

        fieldnames = self._compute_fieldnames(products)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            with output_file.open("w", newline="", encoding="utf-8-sig") as csvfile:
                writer = csv.DictWriter(
                    csvfile,
                    fieldnames=fieldnames,
                    delimiter=";",
                    extrasaction="ignore",
                )
                writer.writeheader()
                writer.writerows(products)
            self.logger.info(
                f"Wrote {len(products)} products to {output_file} ({len(fieldnames)} cols)"
            )
            return True
        except OSError as e:
            self.logger.error(f"Failed to write CSV: {e}")
            return False

    def read(self, csv_file: Path) -> list[dict]:
        """Read a previously-written CSV back into a list of dicts."""
        with csv_file.open("r", newline="", encoding="utf-8-sig") as f:
            return [dict(row) for row in csv.DictReader(f, delimiter=";")]

    @staticmethod
    def _compute_fieldnames(products: list[dict]) -> list[str]:
        """Gather union of keys, sort, then promote known key columns to the front."""
        all_keys = sorted({key for product in products for key in product.keys()})
        for col in reversed(_KEY_COLUMNS):
            if col in all_keys:
                all_keys.insert(0, all_keys.pop(all_keys.index(col)))
        return all_keys
