# tulipa-offers-scraper

**Extracts product and inventory data from the Tulipa B2B florist portal (Helios ERP backend) and saves it to CSV — with session management, dual scrape strategies, cache, and a loop mode for continuous refresh.**

![python](https://img.shields.io/badge/python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)
![license](https://img.shields.io/badge/license-MIT-A31F34?style=flat-square)
![status](https://img.shields.io/badge/status-active-22863A?style=flat-square)
![requests](https://img.shields.io/badge/requests-2.32-000?style=flat-square)
![lxml](https://img.shields.io/badge/lxml-5.x-555?style=flat-square)
![bs4](https://img.shields.io/badge/bs4-4.13-777?style=flat-square)

Tulipa is a Czech wholesale florist; their B2B portal is fronted by Helios iNuvio, exposed as a JSON-RPC endpoint (`RunExternalAction`, `GetBrowse`) with session tokens, cookie auth, and opaque `ActionID` routes. The API has no documentation — every endpoint was discovered by reverse-engineering the portal UI.

This scraper handles the whole flow: session acquisition and silent refresh, category and subgroup discovery, product detail + image URL extraction across five main product groups, cache with 1-hour TTL, and a `--loop` mode that re-runs every 30 minutes for keeping a downstream CSV fresh.

## Run

```bash
uv venv
uv pip install -r requirements.txt
python run.py                            # full scrape, auto-cached
python run.py --browse                   # faster GetBrowse endpoint
python run.py --loop                     # rerun every 30 minutes
python run.py --output my.csv            # custom output path
python run.py --filter-group Dekor       # only one main group
python run.py --reset                    # force new Helios session
python run.py --discover                 # list available categories
```

## Flags

| flag | default | effect |
|------|---------|--------|
| `--output` | `produkty_komplet.csv` | CSV output path |
| `--filter-group` | all | filter by main group (Dekor, Kveto, …) |
| `--limit` | — | cap on number of products |
| `--browse` | off | use `GetBrowse` instead of `RunExternalAction` (faster) |
| `--loop` | off | rerun every 30 minutes; Ctrl+C to stop |
| `--reset` | off | wipe the cached Helios session token |
| `--discover` | off | enumerate available categories and exit |
| `--list-browse` | off | list available `Browse` definitions and exit |
| `--test-actions` | off | probe `ActionID`s with test parameters |
| `--debug` | off | verbose debug logs |
| `--log-level` | INFO | DEBUG / INFO / WARNING / ERROR |
| `--safety-reserve` | 5 | items held back from available stock |
| `--reserve-threshold` | 20 | threshold for applying the reserve |

## Config (`.env`)

```ini
HELIOS_USERNAME=your_username
HELIOS_PASSWORD=your_password
# Optional — override endpoints if Tulipa changes them:
# HELIOS_URL=https://...
```

The scraper persists the Helios session token to `data/tulipa_session.json` and auto-refreshes it when it expires. No need to re-login per run.

## What gets scraped

For every product across the main groups (Dekor, Kveto, …):

- EAN, RegCis (Helios product ID), name, main group, subgroup
- Price, currency, VAT rate
- Available stock, reserved stock, incoming stock
- Product description, detail HTML
- Image URLs (main + gallery)
- Last stock update timestamp

Output: UTF-8 CSV with one row per product. Auto-named `produkty_komplet_YYYYMMDD_HHMMSS.csv` plus a stable `produkty_komplet.csv` symlink for downstream pipelines.

## Caching

Every successful run caches results under `data/YYYY-MM-DD/`. A subsequent run within 1 hour loads from cache instead of re-scraping. `--output` with a custom name bypasses cache read.

## Known limits

- **Helios token expires unpredictably** — the scraper handles refresh on 401/403 responses but an invalidated cookie mid-run still aborts the current iteration (the next `--loop` tick will recover).
- **Session-based, not API-key based** — authenticated as a human user, so credential hygiene matters. Use a dedicated service account with read-only permissions.
- **ActionID coupling** — hardcoded `ActionID` constants in `run.py`. If Tulipa restructures its portal, these break and must be re-discovered via `--test-actions` / `--list-browse`.
- **Single-threaded** — one request at a time, by design (Helios doesn't like concurrent sessions from the same user).

## License

[MIT](LICENSE)
