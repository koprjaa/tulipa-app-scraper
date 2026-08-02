# tulipa-app-scraper

Extracts product and stock data from the Tulipa B2B florist portal and writes it to CSV. The portal runs on a Helios ERP backend with no documented API.

![python](https://img.shields.io/badge/python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)
![license](https://img.shields.io/badge/license-MIT-A31F34?style=flat-square)
![status](https://img.shields.io/badge/status-active-22863A?style=flat-square)
[![ci](https://github.com/koprjaa/tulipa-app-scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/koprjaa/tulipa-app-scraper/actions/workflows/ci.yml)

Tulipa is a Czech wholesale florist. A Helios iNuvio backend serves the B2B portal through a JSON-RPC endpoint with session tokens, cookie authentication, and opaque `ActionID` routes. The scraper handles the full flow: it acquires and refreshes the session, discovers the categories and subgroups, extracts the product details and image URLs across five main groups, caches the result for one hour, and can repeat the run every 30 minutes.

## Install

```bash
uv venv
uv pip install -e .
```

Create a `.env` file in the repository root:

```ini
HELIOS_USERNAME=your_username
HELIOS_PASSWORD=your_password
# Optional, if the endpoints change:
# HELIOS_URL=https://...
```

The scraper writes the session token to `data/tulipa_session.json` and refreshes it when it expires. You do not log in again per run.

## Use

```bash
tulipa-scraper                        # full scrape, uses the cache
tulipa-scraper --browse               # faster path, GetBrowse instead of RunExternalAction
tulipa-scraper --loop                 # rerun every 30 minutes
tulipa-scraper --output my.csv        # custom output path
tulipa-scraper --filter-group Dekor   # one main group only
tulipa-scraper --reset                # discard the cached session token
tulipa-scraper --discover             # list the categories and exit
```

`python -m tulipa_app_scraper` and `python run.py` accept the same flags.

Each row of the output holds the EAN, the Helios product ID, the name, the main group and subgroup, the price, the currency, the VAT rate, the available, reserved, and incoming stock, the description, the detail HTML, the image URLs, and the time of the last stock update.

The file is named `produkty_komplet_YYYYMMDD_HHMMSS.csv`, with a stable `produkty_komplet.csv` link for downstream jobs.

Every successful run caches its result under `data/YYYY-MM-DD/`. A run inside the next hour reads the cache instead of the portal. A custom `--output` name skips the cache read.

## Options

| Flag | Default | Effect |
|---|---|---|
| `--output` | `produkty_komplet.csv` | CSV output path. |
| `--filter-group` | all | Filter by main group, such as Dekor or Kveto. |
| `--limit` | none | Maximum number of products. |
| `--browse` | off | Use `GetBrowse` instead of `RunExternalAction`. |
| `--loop` | off | Rerun every 30 minutes. Stop with Ctrl+C. |
| `--reset` | off | Discard the cached Helios session token. |
| `--discover` | off | List the categories and exit. |
| `--list-browse` | off | List the `Browse` definitions and exit. |
| `--test-actions` | off | Probe the `ActionID` values with test parameters. |
| `--debug` | off | Verbose debug logging. |
| `--log-level` | `INFO` | One of DEBUG, INFO, WARNING, ERROR. |
| `--safety-reserve` | 5 | Items held back from the available stock. |
| `--reserve-threshold` | 20 | Stock level at which the reserve applies. |

## How it works

The layout is hexagonal. Domain types sit at the core, all input and output sits at the edges, and the services sit between them.

```
src/tulipa_app_scraper/
  domain/errors.py             TulipaError, TulipaSessionExpired, TulipaAPIError
  domain/models.py             Category and Subgroup dataclasses
  infrastructure/config.py     Settings dataclass with environment overrides
  infrastructure/helios_client.py  HTTP session, RPC calls, token cache
  infrastructure/cache.py      Dated CSV cache with a TTL
  infrastructure/csv_writer.py CSV read and write with column ordering
  services/scraper.py          Walks the groups, categories, and subgroups
  services/discovery.py        Backs --discover, --list-browse, --test-actions
  cli.py                       argparse, main, loop
```

The scrape logic performs no input or output of its own. It takes a `HeliosClient` and a `Settings` object. A test can replace the client with a mock. See `tests/` for the pattern.

## How the API was mapped

The Helios iNuvio backend accepts a JSON-RPC envelope (`THeliosMethods.Execute`). There are no API documents, no OpenAPI schema, and no types in the browser bundle. Every endpoint, every `ActionID`, and every parameter shape in `config.py` came from traffic capture with mitmproxy.

1. Install mitmproxy with `pip install mitmproxy`.
2. Run `mitmproxy` once to generate `~/.mitmproxy/`, then import `mitmproxy-ca-cert.pem` into the trust store of the operating system or the browser.
3. Send the client through the proxy. A browser needs a proxy extension pointed at `localhost:8080`. A desktop application needs `HTTPS_PROXY=http://localhost:8080`.
4. Click through every section of the portal by hand: product list, categories, subgroups, product detail, images. Each click produces RPC calls, and mitmproxy records them.
5. Filter for `RunExternalAction` and `GetBrowse` calls. Read the `ActionID` value in the request body, the shape of the `Parameters` array, and the response envelope, which usually looks like `{"result": [{"fields": {"Result": {...}, "IsError": false}}]}`.
6. Name each call, note its parameter signature, and save a sample response.
7. Copy the `ActionID` constants into `infrastructure/config.py` and wrap the call sites in `HeliosClient.run_external_action()`.
8. After a portal change, repeat steps 4 and 5. The `--test-actions` flag checks whether the known endpoints still answer.

`mitmweb` on port 8081 is faster than step 4. Filter it with `~u /datasnap/` to isolate the RPC traffic.

The `Username`, `Password`, and `PluginSysName` triplet in the login call is a public client credential taken from the same capture. It is not a personal account. Rotating it would break every user of the B2B portal.

## Limits

- The Helios token expires without a pattern. The scraper refreshes on a 401 or 403, but a cookie invalidated mid-run still ends the current iteration. The next `--loop` tick recovers.
- Authentication is session based, not key based, so the scraper acts as a human user. Use a dedicated read-only account.
- The `ActionID` constants are fixed in the source. A portal restructure breaks them, and you rediscover them with `--test-actions` and `--list-browse`.
- One request at a time, by design. Helios rejects concurrent sessions from the same user.

## Development

```bash
uv pip install -e ".[dev]"
pytest -q
ruff check .
```

CI runs ruff and pytest on Python 3.10, 3.11, and 3.12, on Linux and Windows. The suite covers config, cache, and the CSV writer.

## License

[MIT](LICENSE)
