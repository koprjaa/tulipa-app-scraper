# tulipa-app-scraper

Exports product and stock data from the Tulipa B2B florist portal.

[![CI](https://github.com/koprjaa/tulipa-app-scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/koprjaa/tulipa-app-scraper/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## Install

Install a stable Rust toolchain, then run from the repository directory:

```sh
cargo install --path .
```

Set `HELIOS_USERNAME` and `HELIOS_PASSWORD` in your environment or a `.env` file before the first login. Environment values take precedence. The tool connects to the Tulipa portal by default; `HELIOS_URL` can override its base URL.

## Use

Export the full catalog:

```sh
tulipa-scraper
```

The default output is `data/YYYY-MM-DD/produkty_komplet_YYYYMMDD_HHMMSS.csv`, relative to your working directory. CSV files use semicolons and UTF-8 with a BOM. Rows include product fields, prices and stock quantities. Inventory values stay unchanged.

Export up to 100 products from the Dekor main group to a chosen file:

```sh
tulipa-scraper --filter-group Dekor --limit 100 --output dekor.csv
```

Filtering happens after the full scrape and matches the main group exactly, ignoring case.

Repeat the export every 30 minutes until Ctrl+C:

```sh
tulipa-scraper --loop
```

Full exports reuse output less than one hour old, including custom output files. Filtered or limited exports bypass this check. Successful exports remove matching dated cache files older than 24 hours.

Sessions are saved in `data/tulipa_session.json`. Use `--reset` to log out and remove the saved session, or `--help` for all options.

## License

[MIT](LICENSE)
