"""Pure domain types — dataclasses and error hierarchy, no I/O."""
from tulipa_app_scraper.domain.errors import (
    TulipaAPIError,
    TulipaError,
    TulipaSessionExpired,
)
from tulipa_app_scraper.domain.models import Category, Subgroup

__all__ = [
    "Category",
    "Subgroup",
    "TulipaAPIError",
    "TulipaError",
    "TulipaSessionExpired",
]
