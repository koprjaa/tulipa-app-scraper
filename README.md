# tulipa-app-scraper

![rust](https://img.shields.io/badge/rust-2021-orange?style=flat-square&logo=rust)
![license](https://img.shields.io/badge/license-MIT-A31F34?style=flat-square)

Exports product and inventory data from the Tulipa B2B florist portal through its Helios JSON-RPC endpoint. Requests run sequentially with cookie persistence, verified TLS against the supplied RapidSSL/DigiCert chain, a 30-second request timeout and a 200 ms pause before ordinary RPC calls.


## Installation

Install a current stable Rust toolchain, then run:

```sh
cargo install --path .
```

Set `HELIOS_USERNAME` and `HELIOS_PASSWORD` in the environment or a `.env` file. `HELIOS_URL` optionally replaces the base URL (default `https://eserver.tulipapraha.com:4343`); the RPC path is appended. Empty overrides fall back to defaults. Environment values take precedence over `.env`, searched from the current directory through its parents. Credentials are required only when a new login is necessary.

## Usage

```sh
tulipa-scraper
tulipa-scraper --browse
tulipa-scraper --loop
tulipa-scraper --output my.csv
tulipa-scraper --filter-group Dekor --limit 100
tulipa-scraper --reset
tulipa-scraper --discover
```

| Option | Default | Behavior |
|---|---|---|
| `--output` | `produkty_komplet.csv` | Default writes a dated cache file; another path writes directly there. |
| `--filter-group` | all | Unicode case-insensitive exact main group match after the complete scrape. |
| `--limit` | unlimited | Applied after filtering; zero is unlimited, a negative number omits that many final rows. |
| `--browse` | off | Fetch browse 82, falling back to the complete action traversal if empty. |
| `--loop` | off | Repeat every 1800 seconds. Ctrl+C stops the loop. |
| `--reset` | off | Best-effort logout, remove saved session, exit. |
| `--discover` | off | Probe the original fifteen category candidates and exit. |
| `--list-browse` | off | Activate the database and log the first ten browse definitions. |
| `--test-actions` | off | Run six original probes and summarize results keyed by action GUID. |
| `--debug` | off | Enable HTTP diagnostic messages; visibility still requires DEBUG log level. |
| `--log-level` | INFO | DEBUG, INFO, WARNING or ERROR. Logs go to stdout; banner to stderr. |
| `--safety-reserve` | 5 | Retained consumer setting; does not change exported inventory. |
| `--reserve-threshold` | 20 | Retained consumer setting; does not change exported inventory. |

Special modes take precedence in this order: reset, discover, test-actions, list-browse. Successful commands return 0, failed exports return 1, invalid arguments return 2.

## Export and cache

CSV uses UTF-8 with a BOM, semicolons, CRLF record endings and minimal quoting. Columns are the sorted union of product fields, with present columns promoted in this order: `HlavniSkupina`, `PodskupinaKod`, `PodskupinaNazev`, `Nazev1`, `RegCis`, `_cena_cu1`, `Mnozstvi`. Missing and null values are empty. Price strings under `_cena_cu*` replace commas with decimal points. Stock values are unchanged.

The action traversal reads main groups from category series 200, falling back to Dekor, Kveto, Sezón and Sukul. It then reads extra metadata and category series 100 and 300. The first occurrence of each EAN, otherwise ID, wins; rows without either remain distinct. The browse path preserves duplicates.

Default exports live under `data/YYYY-MM-DD/produkty_komplet_YYYYMMDD_HHMMSS.csv`. Full exports skip scraping when the newest cache is younger than an hour. Custom output paths are also checked for freshness. Filtered or nonzero limited runs bypass freshness checks and use `produkty_vyber` for default dated output. Successful exports remove matching cache files older than 24 hours. There is no stable symlink or copy at the default filename, matching the original implementation.

Sessions use `data/tulipa_session.json`, local ISO timestamps and a 45-minute estimated expiry. Unix token files are restricted to mode 0600. Every action first changes database to Helios001. Session error text triggers one retry; HTTP failures return no response and are not retried. Discovery does not generate JSON, XML or XLSX exports; the original application only exported CSV and its session JSON.

## Development

```sh
cargo build --release
cargo test
cargo clippy --all-targets -- -D warnings
cargo fmt --check
```

CI runs formatting, linting and tests on Ubuntu and Windows. Tests cover every original test case, CLI execution, byte-level CSV output, response shapes, complete category traversal, deduplication and token renewal. HTTP tests use local mocks only. No live portal request is needed.

Dependencies have specific roles: reqwest handles blocking HTTP, cookies, compression and rustls; chrono handles local dates; serde/serde_json preserve heterogeneous RPC rows; csv handles escaping; clap parses arguments; dotenvy loads configuration; unicode-casefold preserves Unicode filtering; ctrlc stops repeating runs. Test-only httpmock provides HTTP mocks and tempfile isolates files.

## Changes from the Python version

- Python entry points and import APIs are replaced by the Rust binary and library; CLI option names and defaults are retained. Clap help and invalid-argument wording/layout differ from argparse, with the same exit codes. Integer CLI values use signed 64-bit bounds rather than arbitrary precision.
- Embedded public login credentials are removed. Configure both credentials in the environment or `.env`; missing credentials produce a clear workflow error when login is needed. The corresponding original defaults test now verifies this intentional change.
- Token-error retries delete the invalid disk token before logging in. Previously a retry could load the same rejected token again. A regression test covers renewal.
- Debug output omits RPC payloads to avoid exposing credentials or session tokens. HTTP errors use Rust transport diagnostics; Python tracebacks are replaced by concise workflow errors. Invalid token files are discarded without Python exception-specific warning text.
- Malformed row/response shapes are treated as empty or missing fields rather than raising Python indexing/type exceptions. Category counts accept ASCII decimal digits fitting an unsigned integer. Nested field values retain Python-style string representations.
- Session JSON remains compatible but duration precision may differ.
- Ctrl+C uses a portable signal handler: it immediately exits with 1 during a single export and 0 in loop mode. Unlike Python, loop interruption during an export stops immediately instead of completing the iteration and waiting for another interrupt.
- Rust HTTP uses rustls rather than OpenSSL and a whole-request timeout rather than separate connect/read timeout phases. Compression is explicitly supported for all advertised encodings. Requests are UTF-8 JSON rather than Python's ASCII-escaped JSON; parsed RPC values are identical.
- The original README incorrectly described custom output cache behavior, a stable link, and HTTP-status-based renewal. This README follows the actual implementation. No stable link, status-based retry, inventory reserve calculation or additional export format has been added.

## License

[MIT](LICENSE)
