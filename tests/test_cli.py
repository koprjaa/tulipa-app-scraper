#
# Project: tulipa-app-scraper
# File:    test_cli.py
#
# Description:
# Tests for the --filter-group and --limit product selection.
#
# Author:
# Jan Alexandr Kopřiva
# jan.alexandr.kopriva@gmail.com
#
# License: MIT
#

from tulipa_app_scraper.cli import select_products
from tulipa_app_scraper.services.scraper import TulipaScraper

PRODUCTS = [
    {"HlavniSkupina": "Dekor", "ID": "1"},
    {"HlavniSkupina": "Kveto", "ID": "2"},
    {"HlavniSkupina": "Dekor", "ID": "3"},
]


def test_no_filter_returns_all():
    assert select_products(PRODUCTS, None, None) == PRODUCTS


def test_filter_group_is_case_insensitive():
    assert [p["ID"] for p in select_products(PRODUCTS, "dekor", None)] == ["1", "3"]


def test_limit_applies_after_filter():
    assert [p["ID"] for p in select_products(PRODUCTS, "Dekor", 1)] == ["1"]


def test_prices_use_decimal_point():
    row = [{"FieldName": "_cena_cu1", "Value": "7,5"}, {"FieldName": "Nazev1", "Value": "A, B"}]
    assert TulipaScraper._row_to_dict(row) == {"_cena_cu1": "7.5", "Nazev1": "A, B"}
