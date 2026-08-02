#
# Project: tulipa-app-scraper
# File:    errors.py
#
# Description:
# The error types the Helios client and the scrape services raise.
#
# Author:
# Jan Alexandr Kopřiva
# jan.alexandr.kopriva@gmail.com
#
# License: MIT
#

"""Typed error hierarchy for Tulipa/Helios operations."""


class TulipaError(Exception):
    """Base error for any Tulipa scraper failure."""


class TulipaSessionExpired(TulipaError):
    """The Helios session token is invalid or has expired."""


class TulipaAPIError(TulipaError):
    """Helios API returned an error response we cannot recover from."""
