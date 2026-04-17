"""Unit tests for CSVStore — round-trip, key-column ordering, empty input."""

import pytest

from tulipa_app_scraper.infrastructure.csv_writer import CSVStore


@pytest.fixture
def csv_store() -> CSVStore:
    return CSVStore()


def test_write_empty_returns_false(csv_store, tmp_path):
    assert not csv_store.write([], tmp_path / "out.csv")


def test_roundtrip(csv_store, tmp_path):
    products = [
        {"RegCis": "A001", "Nazev1": "Tulip", "_cena_cu1": "42"},
        {"RegCis": "A002", "Nazev1": "Rose", "_cena_cu1": "55"},
    ]
    out = tmp_path / "out.csv"
    assert csv_store.write(products, out)
    loaded = csv_store.read(out)
    assert len(loaded) == 2
    assert loaded[0]["RegCis"] == "A001"
    assert loaded[1]["Nazev1"] == "Rose"


def test_key_columns_are_promoted_to_front(csv_store, tmp_path):
    products = [
        {
            "Zcolumn": "z",
            "Acolumn": "a",
            "Nazev1": "Tulip",
            "RegCis": "X1",
            "HlavniSkupina": "Dekor",
        }
    ]
    out = tmp_path / "out.csv"
    csv_store.write(products, out)
    first_line = out.read_text(encoding="utf-8-sig").splitlines()[0]
    headers = first_line.split(";")
    # HlavniSkupina, PodskupinaKod, PodskupinaNazev, Nazev1, RegCis, _cena_cu1, Mnozstvi
    # Only those present: HlavniSkupina, Nazev1, RegCis → should appear before Acolumn/Zcolumn
    hs_idx = headers.index("HlavniSkupina")
    nazev_idx = headers.index("Nazev1")
    regcis_idx = headers.index("RegCis")
    a_idx = headers.index("Acolumn")
    assert hs_idx < a_idx
    assert nazev_idx < a_idx
    assert regcis_idx < a_idx


def test_extras_action_ignore_skips_missing_fields(csv_store, tmp_path):
    products = [
        {"RegCis": "A1", "Foo": "x"},
        {"RegCis": "A2", "Bar": "y"},  # no Foo field
    ]
    out = tmp_path / "out.csv"
    csv_store.write(products, out)
    loaded = csv_store.read(out)
    assert loaded[0]["Foo"] == "x"
    assert loaded[0]["Bar"] == ""
    assert loaded[1]["Foo"] == ""
    assert loaded[1]["Bar"] == "y"
