"""Unit tests for CacheStore — path generation, freshness, cleanup."""
from datetime import datetime, timedelta

import pytest

from tulipa_app_scraper.infrastructure.cache import CacheStore
from tulipa_app_scraper.infrastructure.config import Settings


@pytest.fixture
def cache(tmp_path) -> CacheStore:
    settings = Settings(data_dir=tmp_path, session_file=tmp_path / "session.json")
    return CacheStore(settings)


def test_new_cache_path_creates_date_folder(cache, tmp_path):
    path = cache.new_cache_path()
    assert path.parent.exists()
    assert path.parent.parent == tmp_path
    assert path.name.startswith("produkty_komplet_")
    assert path.suffix == ".csv"


def test_find_latest_returns_none_when_empty(cache):
    assert cache.find_latest() is None


def test_find_latest_picks_newest(cache, tmp_path):
    day1 = tmp_path / "2026-04-15"
    day2 = tmp_path / "2026-04-17"
    day1.mkdir()
    day2.mkdir()

    old_file = day1 / "produkty_komplet_20260415_120000.csv"
    new_file = day2 / "produkty_komplet_20260417_120000.csv"
    old_file.touch()
    new_file.touch()

    # Force old_file to be older than new_file
    old_ts = (datetime.now() - timedelta(days=2)).timestamp()
    import os
    os.utime(old_file, (old_ts, old_ts))

    assert cache.find_latest() == new_file


def test_is_fresh_true_for_new_file(cache, tmp_path):
    f = tmp_path / "fresh.csv"
    f.touch()
    assert cache.is_fresh(f, max_age_hours=1)


def test_is_fresh_false_for_old_file(cache, tmp_path):
    f = tmp_path / "old.csv"
    f.touch()
    import os
    old_ts = (datetime.now() - timedelta(hours=3)).timestamp()
    os.utime(f, (old_ts, old_ts))
    assert not cache.is_fresh(f, max_age_hours=1)


def test_is_fresh_false_for_missing_file(cache, tmp_path):
    assert not cache.is_fresh(tmp_path / "does-not-exist.csv")


def test_cleanup_old_removes_stale(cache, tmp_path):
    day = tmp_path / "2026-04-15"
    day.mkdir()
    old_file = day / "produkty_komplet_20260415_010000.csv"
    new_file = day / "produkty_komplet_20260417_120000.csv"
    old_file.touch()
    new_file.touch()

    import os
    old_ts = (datetime.now() - timedelta(hours=48)).timestamp()
    os.utime(old_file, (old_ts, old_ts))

    deleted = cache.cleanup_old(keep_hours=24)
    assert deleted == 1
    assert not old_file.exists()
    assert new_file.exists()
