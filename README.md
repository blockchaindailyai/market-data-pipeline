# TradeStation Crypto Data

A Python daily OHLCV scraper and CSV preparation tool for TradeStation workflows. It fetches spot cryptocurrency candles through CCXT and includes optional Korean stock adapters for Naver and Yahoo Finance. It does not place orders or connect to a TradeStation brokerage account.

This source-only edition excludes generated market datasets, logs, caches, local configuration and compiled executables. No historical dataset or coverage guarantee is included.

## Quick start

Use Python 3.11 or newer. Python 3.12 is the Windows setup script's first choice. From the repository directory:

```powershell
.\setup.bat
Copy-Item config.example.toml config.toml
# Review config.toml, then fetch data:
.\run_scraper.bat
```

For a manual installation on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe cryptodatav3.5.py
```

On macOS/Linux, use `.venv/bin/python` in place of `.venv\Scripts\python.exe`. The batch launchers are Windows-only. Run commands from the repository directory: configuration and output paths are relative to the current working directory. Without `config.toml`, the program reads `config.example.toml`.

The example requests BTC/USDT and ETH/USDT daily data from 2024 onward, uses two workers and leaves stocks disabled. No API credentials are required. Exchange availability varies by location and policy; unavailable exchanges are skipped. Do not use this tool to bypass provider access restrictions. Unsupported symbols are logged and skipped, so a successful exit does not establish watchlist coverage.

Dependencies are CCXT (exchange adapters), pandas (dataframes), and tzdata (IANA timezones on Windows). `requirements.txt` contains supported version ranges, not a reproducible lockfile. Use a virtual environment and record resolved versions when reproducing results.

## Data flow

1. Load exchange markets and choose an available source for each configured pair. Binance is preferred by the example; MEXC can be selected when its detected history starts more than 30 days earlier.
2. Page through history with bounded MEXC requests, retries and duplicate-page detection. Source choices are cached locally.
3. Normalize and deduplicate rows, update recent history using a seven-day overlap when local data passes checks, and write CSVs.
4. Read saved files back and compare their transformed values with fetched data. These are consistency checks, not independent market-data validation.

Output columns are `date,open,high,low,close,volume`. The example uses `MM/DD/YYYY`. Crypto output defaults to `data/`; stocks use `korean_stock_data/`. A configured start date is a request boundary, not a promise that the provider retains data back to that date.

## Transformations that affect analysis

**Synthetic continuity bars:** for crypto at `1d`, missing dates between the first and last returned candle are inserted. Each inserted bar has open, high, low and close equal to the previous close, and volume zero. These are synthetic placeholders, not observed trades. They are not separately flagged in the six-column CSV. Gap-free output therefore does not prove complete source history. Stock providers retain sparse trading dates and do not fill weekends.

**Price scaling:** if every positive OHLC price in the output frame is below `price_scale_threshold`, the program multiplies OHLC by a power of ten based on the smallest positive price. Volume is unchanged. Prices are rounded to `price_decimal_places` and emitted without scientific notation. The multiplier is not stored in the CSV; output may not be in original quote units. In particular, previously scaled local rows and newly fetched raw rows can be mixed during incremental updates. Keep source data separately and validate scaling before quantitative use; this release does not repair that inherited limitation.

**Dates and current bars:** crypto dates derive from UTC timestamps. Korean trading dates are mapped from the 09:00 Asia/Seoul open to the America/New_York calendar date, which may be the preceding day. Naver may append a current-session quote; it can be partial or delayed. The weekday/open-time check is not a Korean holiday calendar. Yahoo's adjusted-close field is not used as an adjustment model.

## Optional Korean stocks

For a small explicit watchlist, keep `stock_universe = "configured"` and set `stock_tickers = ["005930.KS", "000660.KS"]` in your local config. `.KS` and `.KQ` identify KOSPI and KOSDAQ listings.

For dynamic ranking, set `stock_universe = "krx_top_market_cap"`. With the example's settings it requests 500 mapped symbols and requires 500 before accepting a refreshed or fallback universe. The implementation tries Naver's current list, KRX, Naver mobile JSON, and legacy Naver HTML, then eligible local cache/configured fallback. Despite the setting name, Naver is attempted first. Unmapped Naver ranks can be skipped in favor of later mapped stocks.

These adapters depend on external response formats and access policies. KRX can reject requests; Naver endpoints can redirect, change shape or omit rows; Yahoo CSV downloads can fail due to access or rate limits. Naver history is bounded by `naver_history_count`. Cached rankings can be stale. The current implementation does not guarantee a complete, point-in-time top-500 universe, corporate-action consistency or current-session accuracy. Live provider availability is not certified by the offline tests.

## Running and maintenance

`run_scraper_scheduled.bat` is an optional Task Scheduler entry point. It creates a local lock file and appends to `logs/scheduled_scraper.log`; it does not register a scheduled task. The lock is a best-effort file check, not an atomic cross-process lock. After an interrupted run, remove a stale lock only after verifying no scraper is running. Both launchers may install missing dependencies; run setup explicitly before unattended use.

The example enables `fail_on_verification_errors = true`. Setting it to false allows exit code zero despite per-symbol failures. A stock-universe failure still produces a failure exit after the crypto pipeline finishes. Data writes can replace existing CSVs and are not transactional; back up valuable local data before running. Logs may include provider response snippets and local paths: do not publish them without review.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Tests exercise pagination, synthetic bars, formatting, provider parsing/fallback, stock-date conversion and Unicode output with mocked inputs. Network connections are disabled by the test fixture. They do not establish historical completeness, live availability, financial accuracy or performance. Some inherited concurrency tests use wall-clock bounds and may be sensitive to a heavily loaded machine.

## Rights and contact

No license is granted with this source release; a license choice is pending owner approval. Dependencies retain their own licenses and are installed separately, not vendored here. Access to third-party APIs or data does not grant permission to redistribute their datasets. Review the applicable provider terms before collecting, storing or redistributing data. TradeStation and provider names identify compatibility and data sources; this project does not claim affiliation or endorsement.

Contact: fractuitiontechnologies@gmail.com
