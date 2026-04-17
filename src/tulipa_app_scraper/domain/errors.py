"""Typed error hierarchy for Tulipa/Helios operations."""


class TulipaError(Exception):
    """Base error for any Tulipa scraper failure."""


class TulipaSessionExpired(TulipaError):
    """The Helios session token is invalid or has expired."""


class TulipaAPIError(TulipaError):
    """Helios API returned an error response we cannot recover from."""
