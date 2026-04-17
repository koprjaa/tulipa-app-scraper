"""CLI entry point — argparse + main workflow + loop mode."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from tulipa_app_scraper.infrastructure.cache import CacheStore
from tulipa_app_scraper.infrastructure.config import Settings
from tulipa_app_scraper.infrastructure.csv_writer import CSVStore
from tulipa_app_scraper.infrastructure.helios_client import HeliosClient
from tulipa_app_scraper.services.discovery import Discovery
from tulipa_app_scraper.services.scraper import TulipaScraper

_DEFAULT_CSV_NAME = "produkty_komplet.csv"


def _setup_logging(level: str) -> logging.Logger:
    logger = logging.getLogger("tulipa_scraper")
    logger.setLevel(getattr(logging, level.upper()))
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    logger.addHandler(handler)
    return logger


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tulipa-scraper",
        description="Tulipa B2B Helios scraper — export product data to CSV.",
    )
    parser.add_argument("--output", default=_DEFAULT_CSV_NAME, help="Output CSV file name")
    parser.add_argument("--filter-group", help="Filter by main group (e.g. Dekor, Kveto)")
    parser.add_argument("--limit", type=int, help="Cap number of products")
    parser.add_argument("--browse", action="store_true", help="Use fast GetBrowse endpoint")
    parser.add_argument("--loop", action="store_true", help="Rerun every 30 minutes; Ctrl+C stops")
    parser.add_argument("--reset", action="store_true", help="Wipe cached session token and exit")
    parser.add_argument("--discover", action="store_true", help="Probe candidate categories and exit")
    parser.add_argument(
        "--list-browse",
        action="store_true",
        help="List available GetBrowse definitions and exit",
    )
    parser.add_argument(
        "--test-actions",
        action="store_true",
        help="Probe each known ActionID with expected params and exit",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug-level logging")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    parser.add_argument("--safety-reserve", type=int, default=5)
    parser.add_argument("--reserve-threshold", type=int, default=20)
    return parser


def _run_once(
    args: argparse.Namespace,
    client: HeliosClient,
    scraper: TulipaScraper,
    cache: CacheStore,
    csv_store: CSVStore,
    logger: logging.Logger,
) -> int:
    """One scrape → CSV cycle. Returns exit code."""
    try:
        csv_file = Path(args.output) if args.output != _DEFAULT_CSV_NAME else None
        latest = cache.find_latest() if csv_file is None else (csv_file if csv_file.exists() else None)

        if latest is not None and cache.is_fresh(latest, max_age_hours=1):
            logger.info(f"Fresh cache found: {latest} — skipping scrape")
            return 0

        logger.info("Cache stale or missing — starting scrape")
        products = (
            scraper.scrape_via_browse() if args.browse else scraper.scrape_via_actions()
        )
        if not products:
            logger.error("No products scraped; aborting")
            return 1

        output_path = csv_file or cache.new_cache_path()
        if not csv_store.write(products, output_path):
            logger.error("CSV write failed")
            return 1

        cache.cleanup_old()
        logger.info("Workflow finished")
        return 0

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 1
    except Exception as e:  # noqa: BLE001
        logger.error(f"Workflow failed: {e}")
        return 1


def main() -> int:
    args = _build_parser().parse_args()
    logger = _setup_logging(args.log_level)

    settings = Settings.from_env()
    settings.debug = args.debug
    settings.safety_reserve = args.safety_reserve
    settings.reserve_threshold = args.reserve_threshold

    client = HeliosClient(settings, logger=logger)

    # Special modes that don't scrape
    if args.reset:
        logger.info("Reset mode — wiping session")
        client.force_logout()
        return 0

    if args.discover:
        logger.info("Discovery mode")
        results = Discovery(client, settings, logger).discover_categories()
        found = [name for name, info in results.items() if info.get("exists")]
        logger.info(f"Found {len(found)} categories: {found}")
        return 0

    if args.test_actions:
        logger.info("Test-actions mode")
        client.activate_database()
        results = Discovery(client, settings, logger).test_action_ids()
        passed = sum(1 for r in results.values() if r["success"])
        logger.info(f"Test summary: {passed}/{len(results)} passed")
        return 0

    if args.list_browse:
        logger.info("List-browse mode")
        client.activate_database()
        browse_list = Discovery(client, settings, logger).list_browse_definitions()
        logger.info(f"Found {len(browse_list)} browse definitions:")
        for i, info in enumerate(browse_list[:10], 1):
            logger.info(f"  {i}. {info}")
        return 0

    # Normal scrape path
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    scraper = TulipaScraper(client, settings, logger)
    cache = CacheStore(settings)
    csv_store = CSVStore(logger)

    if not args.loop:
        return _run_once(args, client, scraper, cache, csv_store, logger)

    logger.info(f"Loop mode — every {settings.loop_interval_seconds // 60} minutes. Ctrl+C to stop.")
    iteration = 1
    while True:
        try:
            logger.info(
                f"Iteration #{iteration} — {datetime.now():%Y-%m-%d %H:%M:%S}"
            )
            rc = _run_once(args, client, scraper, cache, csv_store, logger)
            log = logger.info if rc == 0 else logger.warning
            log(f"Iteration #{iteration} finished (exit {rc})")
            iteration += 1
            logger.info(
                f"Sleeping {settings.loop_interval_seconds // 60} min before next iteration"
            )
            time.sleep(settings.loop_interval_seconds)
        except KeyboardInterrupt:
            logger.info("Loop stopped by user")
            return 0
        except Exception as e:  # noqa: BLE001
            logger.error(f"Iteration error: {e}")
            logger.info(f"Waiting {settings.loop_error_wait_seconds} s before retrying")
            time.sleep(settings.loop_error_wait_seconds)


if __name__ == "__main__":
    sys.exit(main())
