#
# Project: tulipa-app-scraper
# File:    __init__.py
#
# Description:
# Exposes the domain types and the error hierarchy.
#
# Author:
# Jan Alexandr Kopřiva
# jan.alexandr.kopriva@gmail.com
#
# License: MIT
#

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
