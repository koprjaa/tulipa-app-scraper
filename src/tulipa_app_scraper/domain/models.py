"""Lightweight domain dataclasses.

Product rows stay as plain dicts because Helios returns heterogeneous fields per
ActionID — trying to normalise them into a single dataclass would lose columns
downstream tooling depends on.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    """A top-level Helios category (e.g. Dekor, Kveto)."""

    id: str
    code: str
    name: str
    count: int


@dataclass(frozen=True)
class Subgroup:
    """A subgroup (K2) belonging to a main group / category."""

    code: str
    name: str
