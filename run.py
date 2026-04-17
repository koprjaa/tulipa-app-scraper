#!/usr/bin/env python3
"""Thin entry shim for backwards compatibility with `python run.py ...`.

All logic lives under `src/tulipa_app_scraper/`. You can also invoke:
    python -m tulipa_app_scraper ...
    tulipa-scraper ...        # after `pip install -e .`
"""
import sys

from tulipa_app_scraper.cli import main

if __name__ == "__main__":
    sys.exit(main())
