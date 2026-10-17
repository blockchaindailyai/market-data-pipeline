import builtins
import os
import csv
import datetime
import json
import sys
import io
import re
import time
from zoneinfo import ZoneInfo
from urllib.parse import quote, urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading

import ccxt
import pandas as pd
import tomllib


_original_print = builtins.print
_print_lock = threading.Lock()


def _replace_standard_stream(name: str, stream) -> bool:
    """Replace stdout/stderr with an UTF-8 wrapper when reconfigure is unavailable."""
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        return False

    try:
        wrapped = io.TextIOWrapper(
            buffer,
            encoding="utf-8",
            errors="backslashreplace",
            line_buffering=getattr(stream, "line_buffering", False),
            write_through=True,
        )
    except (AttributeError, ValueError, OSError):
        return False

    setattr(sys, name, wrapped)
    return True


def configure_unicode_output() -> None:
    """Ensure logs can include non-CP1252 ticker symbols under Task Scheduler."""
    os.environ.setdefault("PYTHONIOENCODING", "utf-8:backslashreplace")
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
                continue
            except (AttributeError, ValueError, OSError):
                pass

        _replace_standard_stream(name, stream)


def _encode_for_stream(value, stream) -> str:
    """Return text that can be written even when the stream uses CP1252."""
    text = str(value)
    encoding = getattr(stream, "encoding", None) or "utf-8"
    errors = getattr(stream, "errors", None) or "backslashreplace"
    try:
        return text.encode(encoding, errors="backslashreplace").decode(encoding, errors=errors)
    except (LookupError, UnicodeError):
        return _ascii_escape(text)


def _ascii_escape(value) -> str:
    return str(value).encode("ascii", errors="backslashreplace").decode("ascii")


def _join_print_text(args: tuple, sep, end) -> str:
    separator = " " if sep is None else str(sep)
    ending = "\n" if end is None else str(end)
    return separator.join(str(arg) for arg in args) + ending


def safe_print(*args, **kwargs) -> None:
    """Print without letting non-ASCII ticker names crash Windows logs."""
    file = kwargs.pop("file", None) or sys.stdout
    sep = kwargs.pop("sep", " ")
    end = kwargs.pop("end", "\n")
    flush = kwargs.pop("flush", False)
    if kwargs:
        _original_print(*args, file=file, sep=sep, end=end, flush=flush, **kwargs)
        return

    text = _join_print_text(args, sep, end)
    safe_text = _encode_for_stream(text, file)
    with _print_lock:
        try:
            file.write(safe_text)
        except UnicodeEncodeError:
            # If a stream misreports its encoding, force ASCII escapes as a final
            # fallback so logging never aborts worker threads.
            file.write(_ascii_escape(text))
        if flush:
            file.flush()


configure_unicode_output()
builtins.print = safe_print

CONFIG_PATH = Path("config.toml")
if not CONFIG_PATH.is_file():
    CONFIG_PATH = Path("config.example.toml")

with CONFIG_PATH.open("rb") as config_file:
    config = tomllib.load(config_file)

trading_pairs = config["tickers"]
stock_tickers = config.get("stock_tickers", [])
TIMEFRAME = config.get("timeframe", "1d")
DATE_FORMAT = config.get("output_date_format", "%Y-%m-%d")
PAGE_LIMIT = int(config.get("page_limit", 1000))
MAX_WORKERS = int(config.get("max_workers", 5))
SOURCE_SELECTION_WORKERS = int(config.get("source_selection_workers", MAX_WORKERS))
STOCK_MAX_WORKERS = int(config.get("stock_max_workers", 25))
STOCK_UNIVERSE = config.get("stock_universe", "configured")
STOCK_UNIVERSE_TOP_N = int(config.get("stock_universe_top_n", 500))
STOCK_UNIVERSE_REFRESH = bool(config.get("stock_universe_refresh", True))
STOCK_REQUEST_DELAY_SECONDS = float(config.get("stock_request_delay_seconds", 0))
STOCK_UNIVERSE_CACHE_FILE = Path(config.get("stock_universe_cache_file", "korean_stock_tickers.json"))
STOCK_UNIVERSE_MIN_SYMBOLS = int(config.get("stock_universe_min_symbols", STOCK_UNIVERSE_TOP_N))
RETRY_COUNT = int(config.get("retry_count", 3))
RETRY_DELAY_SECONDS = float(config.get("retry_delay_seconds", 2))
START_DATE = config.get("start_date", "2017-01-01")
SOURCE_EXCHANGES = config.get("source_exchanges", ["binance", "mexc"])
OUTPUT_DIR = Path(config.get("output_dir", "data"))
STOCK_OUTPUT_DIR = Path(config.get("stock_output_dir", OUTPUT_DIR))
SOURCE_MEMORY_FILE = Path(config.get("source_memory_file", "exchange_memory.json"))
MEXC_LONGER_HISTORY_THRESHOLD_DAYS = int(config.get("mexc_longer_history_threshold_days", 30))
ONE_DAY_MS = 24 * 60 * 60 * 1000
MAX_STALE_DAYS = int(config.get("max_stale_days", 2))
MEXC_FORCE_END_TIME = bool(config.get("mexc_force_end_time", True))
FAIL_ON_VERIFICATION_ERRORS = bool(config.get("fail_on_verification_errors", False))
PRICE_DECIMAL_PLACES = int(config.get("price_decimal_places", 8))
PRICE_SCALE_THRESHOLD = Decimal(str(config.get("price_scale_threshold", "0.0001")))
YAHOO_QUERY_BASE_URL = config.get("yahoo_query_base_url", "https://query1.finance.yahoo.com/v7/finance/download")
YAHOO_USER_AGENT = config.get("yahoo_user_agent", "Mozilla/5.0 (compatible; TradestationCryptoData/1.0)")
NAVER_CHART_URL = config.get("naver_chart_url", "https://fchart.stock.naver.com/sise.nhn")
NAVER_QUOTE_URL = config.get("naver_quote_url", "https://finance.naver.com/item/main.naver")
NAVER_MARKET_SUM_URL = config.get("naver_market_sum_url", "https://finance.naver.com/sise/sise_market_sum.naver")
NAVER_MARKET_FIELD_SUBMIT_URL = config.get("naver_market_field_submit_url", "https://finance.naver.com/sise/field_submit.naver")
KRX_MARKET_CAP_URL = config.get("krx_market_cap_url", "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd")
STOCK_DATA_PROVIDERS = config.get("stock_data_providers", ["naver", "yahoo"])
NAVER_HISTORY_COUNT = int(config.get("naver_history_count", 10000))
INCREMENTAL_OVERLAP_DAYS = int(config.get("incremental_overlap_days", 7))
KOREA_TZ = ZoneInfo("Asia/Seoul")
TRADESTATION_TZ = ZoneInfo("America/New_York")
STOCK_DATE_SCHEME_VERSION = "tradestation_eastern_sparse_v2"
STOCK_PROVIDER_IDS = {"naver", "yahoo"}


# Exchange clients are created per worker thread and per exchange id so concurrent
# symbol fetches do not share mutable ccxt client state.
EXCHANGE_KWARGS = {"enableRateLimit": True}
_thread_local = threading.local()
_rate_limit_state_lock = threading.Lock()
_exchange_call_locks: dict[str, threading.Lock] = {}
_exchange_next_call_at: dict[str, float] = {}
_memory_lock = threading.Lock()


def get_exchange_class(exchange_id: str):
    if not hasattr(ccxt, exchange_id):
        raise ValueError(f"Unsupported exchange configured: {exchange_id}")
    return getattr(ccxt, exchange_id)


def get_exchange_call_lock(exchange_id: str) -> threading.Lock:
    with _rate_limit_state_lock:
        lock = _exchange_call_locks.get(exchange_id)
        if lock is None:
            lock = threading.Lock()
            _exchange_call_locks[exchange_id] = lock
        return lock


def rate_limited_client_call(client: ccxt.Exchange, callback):
    exchange_id = client.id
    lock = get_exchange_call_lock(exchange_id)
    with lock:
        next_call_at = _exchange_next_call_at.get(exchange_id, 0)
        wait_seconds = next_call_at - time.monotonic()
        if wait_seconds > 0:
            time.sleep(wait_seconds)

        try:
            return callback()
        finally:
            _exchange_next_call_at[exchange_id] = time.monotonic() + get_api_call_delay(client)


def create_exchange(exchange_id: str) -> ccxt.Exchange:
    exchange_class = get_exchange_class(exchange_id)
    client = exchange_class(EXCHANGE_KWARGS)
    rate_limited_client_call(client, client.load_markets)
    return client


def bootstrap_exchanges() -> dict[str, ccxt.Exchange]:
    clients = {}
    for exchange_id in SOURCE_EXCHANGES:
        try:
            clients[exchange_id] = create_exchange(exchange_id)
        except ccxt.BaseError as exc:
            print(f"{exchange_id.upper()} is unavailable; continuing with other configured sources: {exc}")
    if trading_pairs and not clients:
        raise SystemExit("No configured crypto exchange is available; no crypto symbols can be processed.")
    return clients


def decode_http_body(response) -> str:
    raw_body = response.read()
    content_type = response.headers.get("Content-Type", "") if hasattr(response, "headers") else ""
    declared_match = re.search(r"charset=([\w-]+)", content_type, flags=re.IGNORECASE)
    encodings = []
    if declared_match:
        encodings.append(declared_match.group(1))

    header_preview = raw_body[:200].decode("ascii", errors="ignore")
    xml_match = re.search(r'encoding=["\']([^"\']+)["\']', header_preview, flags=re.IGNORECASE)
    if xml_match:
        encodings.append(xml_match.group(1))

    encodings.extend(["utf-8-sig", "euc-kr", "cp949"])
    seen = set()
    for encoding in encodings:
        normalized = encoding.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            return raw_body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue

    return raw_body.decode("utf-8", errors="replace")


def parse_naver_number(text: str) -> float | None:
    cleaned = re.sub(r"[^\d.\-]", "", text or "")
    if not cleaned or cleaned in {"-", "."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_naver_quote_field(body: str, korean_label: str) -> float | None:
    label_match = re.search(
        rf"{re.escape(korean_label)}\s*</span>\s*<span[^>]*>(.*?)</span>",
        body,
        flags=re.DOTALL,
    )
    if label_match:
        return parse_naver_number(re.sub(r"<[^>]+>", "", label_match.group(1)))

    text_body = re.sub(r"<[^>]+>", " ", body)
    text_match = re.search(rf"{re.escape(korean_label)}\s*([\d,]+(?:\.\d+)?)", text_body)
    if text_match:
        return parse_naver_number(text_match.group(1))
    return None


def krx_trading_day_has_started(now: datetime.datetime | None = None) -> bool:
    korea_now = now.astimezone(KOREA_TZ) if now is not None else datetime.datetime.now(KOREA_TZ)
    if korea_now.weekday() >= 5:
        return False
    market_open = korea_now.replace(hour=9, minute=0, second=0, microsecond=0)
    return korea_now >= market_open


def get_tradestation_stock_timestamp_ms(korea_trading_date: datetime.date) -> int:
    """Map a KRX trading date to the US/Eastern date TradeStation displays."""
    krx_open = datetime.datetime.combine(
        korea_trading_date,
        datetime.time(hour=9),
        tzinfo=KOREA_TZ,
    )
    tradestation_date = krx_open.astimezone(TRADESTATION_TZ).date()
    tradestation_midnight = datetime.datetime.combine(
        tradestation_date,
        datetime.time.min,
        tzinfo=datetime.UTC,
    )
    return int(tradestation_midnight.timestamp() * 1000)


def get_tradestation_today_timestamp_ms(now: datetime.datetime | None = None) -> int:
    korea_now = now.astimezone(KOREA_TZ) if now is not None else datetime.datetime.now(KOREA_TZ)
    return get_tradestation_stock_timestamp_ms(korea_now.date())


def build_naver_quote_url(stock_code: str) -> str:
    return f"{NAVER_QUOTE_URL}?code={quote(stock_code)}"


class YahooDailyOHLCVProvider:
    """Small Yahoo Finance CSV-download client for free daily stock OHLCV data.

    Korean listings use Yahoo's exchange suffixes, for example Samsung
    Electronics as ``005930.KS`` (KOSPI) and Kakao Games as ``293490.KQ``
    (KOSDAQ).
    """

    id = "yahoo"
    rateLimit = 1000
    urls = {"api": YAHOO_QUERY_BASE_URL}
    last_http_status = None
    last_http_response = None

    def __init__(self, symbols: list[str] | None = None):
        self.symbols = list(symbols or [])
        self.markets = {symbol: {"id": symbol, "type": "stock"} for symbol in self.symbols}

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1d", since: int | None = None, limit: int | None = None, params: dict | None = None):
        if timeframe != "1d":
            raise ValueError("Yahoo stock provider supports only daily timeframe (1d).")

        period1 = int((since if since is not None else get_start_since()) / 1000)
        period2_ms = None if params is None else params.get("period2")
        period2 = int(period2_ms / 1000) if period2_ms is not None else int(time.time())
        url = (
            f"{YAHOO_QUERY_BASE_URL}/{quote(symbol)}?period1={period1}&period2={period2}"
            "&interval=1d&events=history&includeAdjustedClose=true"
        )
        request = Request(url, headers={"User-Agent": YAHOO_USER_AGENT})
        with urlopen(request, timeout=30) as response:
            self.last_http_status = getattr(response, "status", None)
            body = decode_http_body(response)
            self.last_http_response = body[:400]

        reader = csv.DictReader(io.StringIO(body))
        rows = []
        for csv_row in reader:
            if csv_row.get("Close") in (None, "null") or csv_row.get("Volume") in (None, "null"):
                continue
            date_value = datetime.datetime.strptime(csv_row["Date"], "%Y-%m-%d").date()
            timestamp_ms = get_tradestation_stock_timestamp_ms(date_value)
            rows.append([
                timestamp_ms,
                float(csv_row["Open"]),
                float(csv_row["High"]),
                float(csv_row["Low"]),
                float(csv_row["Close"]),
                float(csv_row["Volume"]),
            ])

        if limit is not None:
            return rows[:limit]
        return rows


class NaverDailyOHLCVProvider:
    """Naver Finance chart fallback for Korean daily stock OHLCV data."""

    id = "naver"
    rateLimit = 1000
    urls = {"api": NAVER_CHART_URL}
    last_http_status = None
    last_http_response = None

    def __init__(self, symbols: list[str] | None = None):
        self.symbols = list(symbols or [])
        self.markets = {symbol: {"id": symbol.split(".", 1)[0], "type": "stock"} for symbol in self.symbols}

    def fetch_today_ohlcv(self, stock_code: str) -> list[float] | None:
        if not krx_trading_day_has_started():
            return None

        url = build_naver_quote_url(stock_code)
        request = Request(url, headers={"User-Agent": YAHOO_USER_AGENT})
        with urlopen(request, timeout=30) as response:
            self.last_http_status = getattr(response, "status", None)
            body = decode_http_body(response)
            self.last_http_response = body[:400]

        current = parse_naver_quote_field(body, "현재가")
        open_price = parse_naver_quote_field(body, "시가")
        high = parse_naver_quote_field(body, "고가")
        low = parse_naver_quote_field(body, "저가")
        volume = parse_naver_quote_field(body, "거래량")
        if current is None:
            return None

        open_price = open_price if open_price is not None else current
        high = high if high is not None else max(open_price, current)
        low = low if low is not None else min(open_price, current)
        volume = volume if volume is not None else 0.0
        return [get_tradestation_today_timestamp_ms(), open_price, high, low, current, volume]

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1d", since: int | None = None, limit: int | None = None, params: dict | None = None):
        if timeframe != "1d":
            raise ValueError("Naver stock provider supports only daily timeframe (1d).")

        stock_code = symbol.split(".", 1)[0]
        period2_ms = None if params is None else params.get("period2")
        url = f"{NAVER_CHART_URL}?symbol={quote(stock_code)}&timeframe=day&count={NAVER_HISTORY_COUNT}&requestType=0"
        request = Request(url, headers={"User-Agent": YAHOO_USER_AGENT})
        with urlopen(request, timeout=30) as response:
            self.last_http_status = getattr(response, "status", None)
            body = decode_http_body(response)
            self.last_http_response = body[:400]

        rows = []
        for match in re.finditer(r'<item data="(\d{8})\|([^"|]+)\|([^"|]+)\|([^"|]+)\|([^"|]+)\|([^"|]+)"', body):
            date_text, open_text, high_text, low_text, close_text, volume_text = match.groups()
            date_value = datetime.datetime.strptime(date_text, "%Y%m%d").date()
            timestamp_ms = get_tradestation_stock_timestamp_ms(date_value)
            if since is not None and timestamp_ms < since:
                continue
            if period2_ms is not None and timestamp_ms >= period2_ms:
                continue
            rows.append([
                timestamp_ms,
                float(open_text),
                float(high_text),
                float(low_text),
                float(close_text),
                float(volume_text),
            ])

        today_row = self.fetch_today_ohlcv(stock_code)
        if today_row is not None:
            today_ts = today_row[0]
            rows = [row for row in rows if row[0] != today_ts]
            if since is None or today_ts >= since:
                if period2_ms is None or today_ts < period2_ms:
                    rows.append(today_row)

        rows.sort(key=lambda row: row[0])
        if limit is not None:
            return rows[:limit]
        return rows



def krx_market_suffix(market_name: str) -> str | None:
    normalized = (market_name or "").upper()
    if "KOSPI" in normalized:
        return ".KS"
    if "KOSDAQ" in normalized:
        return ".KQ"
    return None

def previous_calendar_dates(days: int) -> list[str]:
    today = datetime.datetime.now(datetime.UTC).date()
    return [(today - datetime.timedelta(days=offset)).strftime("%Y%m%d") for offset in range(days)]

def fetch_krx_market_cap_rows(trade_date: str) -> list[dict]:
    payload = urlencode({
        "bld": "dbms/MDC/STAT/standard/MDCSTAT01501",
        "mktId": "ALL",
        "trdDd": trade_date,
        "share": "1",
        "money": "1",
        "csvxls_isNo": "false",
    }).encode("utf-8")
    request = Request(
        KRX_MARKET_CAP_URL,
        data=payload,
        headers={
            "User-Agent": YAHOO_USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader",
        },
    )
    with urlopen(request, timeout=30) as response:
        body = decode_http_body(response)
    data = json.loads(body)
    rows = data.get("OutBlock_1") if isinstance(data, dict) else None
    return rows if isinstance(rows, list) else []

def parse_krx_market_cap_symbols(rows: list[dict], limit: int) -> list[str]:
    ranked = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("ISU_SRT_CD") or row.get("isuSrtCd") or "").zfill(6)
        if not re.fullmatch(r"\d{6}", code):
            continue
        suffix = krx_market_suffix(str(row.get("MKT_NM") or row.get("mktNm") or ""))
        if suffix is None:
            continue
        market_cap = parse_int_text(str(row.get("MKTCAP") or row.get("mktcap") or row.get("marketCap") or ""))
        if market_cap <= 0:
            continue
        ranked.append((f"{code}{suffix}", market_cap))

    ranked.sort(key=lambda item: item[1], reverse=True)
    return [symbol for symbol, _ in ranked[:limit]]

def fetch_top_krx_stock_tickers(limit: int) -> list[str]:
    if limit <= 0:
        return []
    last_error = None
    for trade_date in previous_calendar_dates(10):
        try:
            symbols = parse_krx_market_cap_symbols(fetch_krx_market_cap_rows(trade_date), limit)
        except Exception as exc:
            last_error = exc
            if isinstance(exc, HTTPError) and exc.code in (400, 403, 451):
                break
            continue
        if symbols:
            print(f"Loaded KRX market-cap universe for trade_date={trade_date}.")
            return symbols
    if last_error is not None:
        raise RuntimeError(f"KRX market-cap universe lookup failed. Last error: {type(last_error).__name__}: {last_error}")
    return []

def parse_int_text(value: str) -> int:
    digits = re.sub(r"[^0-9]", "", value or "")
    return int(digits) if digits else 0

def suffix_for_naver_market(sosok: int) -> str:
    return ".KS" if sosok == 0 else ".KQ"

def build_naver_market_sum_url(sosok: int, page: int) -> str:
    query = urlencode({"sosok": sosok, "page": page})
    return f"{NAVER_MARKET_SUM_URL}?{query}"

def build_naver_market_submit_body(sosok: int, page: int) -> bytes:
    return urlencode({
        "menu": "market_sum",
        "fieldIds": ["market_sum"],
        "returnUrl": build_naver_market_sum_url(sosok, page),
    }, doseq=True).encode("utf-8")

def fetch_naver_market_sum_html(sosok: int, page: int, use_field_submit: bool = False) -> str:
    if use_field_submit:
        # Naver only applies selected fields such as market cap reliably through
        # a form POST. A GET to field_submit can ignore the requested page.
        request = Request(
            NAVER_MARKET_FIELD_SUBMIT_URL,
            data=build_naver_market_submit_body(sosok, page),
            headers={"User-Agent": YAHOO_USER_AGENT},
        )
    else:
        request = Request(build_naver_market_sum_url(sosok, page), headers={"User-Agent": YAHOO_USER_AGENT})

    with urlopen(request, timeout=30) as response:
        return decode_http_body(response)

def parse_naver_market_sum_rows(body: str, sosok: int) -> list[tuple[str, int]]:
    rows = []
    for row_match in re.finditer(r"<tr[^>]*>(.*?)</tr>", body, flags=re.IGNORECASE | re.DOTALL):
        row_html = row_match.group(1)
        code_match = re.search(r"code=(\d{6})", row_html)
        if not code_match:
            continue
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.IGNORECASE | re.DOTALL)
        if len(cells) < 7:
            continue
        # Naver market-sum rows keep market cap as the seventh <td>: number,
        # name, price, change, percent, par value, market cap. The final cell is
        # often the discussion link, so using cells[-1] parsed as zero symbols.
        market_cap = parse_int_text(re.sub(r"<[^>]+>", "", cells[6]))
        if market_cap <= 0:
            continue
        rows.append((f"{code_match.group(1)}{suffix_for_naver_market(sosok)}", market_cap))
    return rows

def fetch_naver_market_sum_page(sosok: int, page: int) -> list[tuple[str, int]]:
    """Fetch one Naver market-cap ranking page for KOSPI (0) or KOSDAQ (1)."""
    body = fetch_naver_market_sum_html(sosok, page)
    rows = parse_naver_market_sum_rows(body, sosok)
    if rows:
        return rows

    # Direct pages can omit optional field columns depending on Naver defaults.
    # Fall back to the POST flow that explicitly requests market_sum.
    body = fetch_naver_market_sum_html(sosok, page, use_field_submit=True)
    return parse_naver_market_sum_rows(body, sosok)

def fetch_top_korean_stock_tickers(limit: int) -> list[str]:
    """Return the top Korean listings by market cap using Naver's market-sum pages."""
    if limit <= 0:
        return []

    by_symbol: dict[str, int] = {}
    # Naver shows 50 listings per market-sum page; fetch only enough pages to
    # cover the requested limit from each market, then sort both markets together.
    pages_per_market = max(1, (limit + 49) // 50)
    tasks = [(sosok, page) for sosok in (0, 1) for page in range(1, pages_per_market + 1)]
    with ThreadPoolExecutor(max_workers=min(STOCK_MAX_WORKERS, len(tasks))) as executor:
        for page_rows in executor.map(lambda args: fetch_naver_market_sum_page(*args), tasks):
            for symbol, market_cap in page_rows:
                by_symbol[symbol] = max(market_cap, by_symbol.get(symbol, 0))

    ranked = sorted(by_symbol.items(), key=lambda item: item[1], reverse=True)
    return [symbol for symbol, _ in ranked[:limit]]

def fetch_naver_current_market_page(market_type: str, start_idx: int, page_size: int = 100) -> list[str]:
    """Fetch a KRX stock-list page from Naver's current market interface."""
    query = urlencode({
        "tradeType": "KRX",
        "marketType": market_type,
        "orderType": "marketSum",
        "startIdx": start_idx,
        "pageSize": page_size,
    })
    url = f"https://stock.naver.com/api/domestic/market/stock/default?{query}"
    request = Request(url, headers={
        "User-Agent": YAHOO_USER_AGENT,
        "Accept": "application/json",
        "Referer": "https://stock.naver.com/market/stock/kr/stocklist/capitalization",
    })
    with urlopen(request, timeout=15) as response:
        payload = json.loads(decode_http_body(response))
    rows = payload
    for _ in range(4):
        if isinstance(rows, list):
            break
        if not isinstance(rows, dict):
            break
        rows = next((rows[key] for key in ("content", "items", "stocks", "stockList", "result", "data", "list")
                     if isinstance(rows.get(key), (list, dict))), None)
    if not isinstance(rows, list):
        keys = list(payload)[:12] if isinstance(payload, dict) else type(payload).__name__
        raise ValueError(f"Naver current market list has an unexpected response shape for {market_type} page {start_idx}: {keys}")

    codes = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("itemCode") or row.get("itemcode") or row.get("code")
                   or row.get("stockCode") or "").removeprefix("A")
        if re.fullmatch(r"\d{6}", code):
            codes.append(code)
    if rows and not codes:
        keys = list(rows[0])[:12] if isinstance(rows[0], dict) else type(rows[0]).__name__
        raise ValueError(f"Naver current market list has no usable stock codes for {market_type} page {start_idx}: {keys}")
    return codes


def fetch_top_naver_current_stock_tickers(limit: int) -> list[str]:
    """Get one globally ranked list and map each code to its KOSPI/KOSDAQ suffix."""
    if limit <= 0:
        return []
    page_size = 100
    pages = (limit + page_size - 1) // page_size
    max_pages = pages + 5  # Allow for non-stock rows and duplicate codes in ranked pages.
    ranked_codes = []
    market_by_code = {}
    for page in range(max_pages):
        ranked_codes.extend(fetch_naver_current_market_page("ALL", page, page_size))
        ranked_codes = list(dict.fromkeys(ranked_codes))
        # Some ALL rows are not represented in either filtered stock market.
        # Keep the global rank, and take the first 500 codes with a known suffix.
        with ThreadPoolExecutor(max_workers=2) as executor:
            for market, codes in zip(
                ("KOSPI", "KOSDAQ"),
                executor.map(
                    lambda market: fetch_naver_current_market_page(market, page, page_size),
                    ("KOSPI", "KOSDAQ"),
                ),
            ):
                for code in codes:
                    market_by_code[code] = ".KS" if market == "KOSPI" else ".KQ"
        symbols = [f"{code}{market_by_code[code]}" for code in ranked_codes if code in market_by_code]
        if len(symbols) >= limit:
            return symbols[:limit]
    raise ValueError(
        f"Naver current market list mapped only {len(symbols)} of {limit} ranked stocks "
        f"after {max_pages} pages"
    )


def fetch_naver_mobile_market_sum_page(category: str, page: int, page_size: int = 100) -> list[tuple[str, int]]:
    """Read Naver's JSON market-cap list without parsing the redirected legacy HTML."""
    query = urlencode({
        "sortType": "marketValue",
        "category": category,
        "page": page,
        "pageSize": page_size,
    })
    url = f"https://m.stock.naver.com/front-api/stock/domestic/stockList?{query}"
    request = Request(url, headers={
        "User-Agent": YAHOO_USER_AGENT,
        "Accept": "application/json",
        "Referer": "https://stock.naver.com/market/stock/kr/stocklist/capitalization",
    })
    with urlopen(request, timeout=30) as response:
        payload = json.loads(decode_http_body(response))
    rows = payload
    for _ in range(3):
        if isinstance(rows, list):
            break
        if not isinstance(rows, dict):
            break
        rows = next((rows[key] for key in ("stocks", "stockList", "items", "result", "data", "list")
                     if isinstance(rows.get(key), (list, dict))), None)
    if not isinstance(rows, list):
        raise ValueError(f"Naver JSON market list has an unexpected response shape for {category} page {page}")

    suffix = ".KS" if category == "KOSPI" else ".KQ"
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("itemCode") or row.get("code") or "").removeprefix("A")
        cap = parse_int_text(str(row.get("marketValue") or row.get("marketCap") or ""))
        if re.fullmatch(r"\d{6}", code) and cap > 0:
            result.append((f"{code}{suffix}", cap))
    if rows and not result:
        raise ValueError(f"Naver JSON market list has no usable codes and market caps for {category} page {page}")
    return result


def fetch_top_naver_mobile_stock_tickers(limit: int) -> list[str]:
    if limit <= 0:
        return []
    page_size = 100
    pages_per_market = (limit + page_size - 1) // page_size
    tasks = [(category, page) for category in ("KOSPI", "KOSDAQ")
             for page in range(1, pages_per_market + 1)]
    by_symbol: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=min(STOCK_MAX_WORKERS, len(tasks))) as executor:
        for page_rows in executor.map(lambda args: fetch_naver_mobile_market_sum_page(*args), tasks):
            for symbol, cap in page_rows:
                by_symbol[symbol] = max(cap, by_symbol.get(symbol, 0))
    return [symbol for symbol, _ in sorted(by_symbol.items(), key=lambda item: item[1], reverse=True)[:limit]]


def save_stock_universe_cache(symbols: list[str]) -> None:
    if not STOCK_UNIVERSE_CACHE_FILE:
        return
    STOCK_UNIVERSE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = STOCK_UNIVERSE_CACHE_FILE.with_suffix(STOCK_UNIVERSE_CACHE_FILE.suffix + ".tmp")
    payload = {
        "updated_at": utc_now_iso(),
        "stock_universe": STOCK_UNIVERSE,
        "stock_universe_top_n": STOCK_UNIVERSE_TOP_N,
        "symbols": symbols,
    }
    with temp_file.open("w", encoding="utf-8") as cache_file:
        json.dump(payload, cache_file, indent=2, ensure_ascii=False)
        cache_file.write("\n")
    temp_file.replace(STOCK_UNIVERSE_CACHE_FILE)

def load_stock_universe_cache() -> list[str]:
    if not STOCK_UNIVERSE_CACHE_FILE.is_file():
        return []
    try:
        with STOCK_UNIVERSE_CACHE_FILE.open("r", encoding="utf-8") as cache_file:
            payload = json.load(cache_file)
    except Exception as exc:
        print(
            f"Could not read stock universe cache from {STOCK_UNIVERSE_CACHE_FILE}; ignoring it "
            f"| error_type={type(exc).__name__} | error={exc}"
        )
        return []

    symbols = payload.get("symbols") if isinstance(payload, dict) else None
    if not isinstance(symbols, list):
        return []
    return [symbol for symbol in symbols if isinstance(symbol, str) and symbol]

def get_fallback_stock_tickers(reason: str) -> list[str]:
    cached = load_stock_universe_cache()
    if len(cached) >= STOCK_UNIVERSE_MIN_SYMBOLS:
        print(
            f"{reason}; using {len(cached)} cached stock tickers from {STOCK_UNIVERSE_CACHE_FILE}."
        )
        return cached
    if cached:
        print(f"Ignoring incomplete stock universe cache ({len(cached)} of {STOCK_UNIVERSE_MIN_SYMBOLS} required).")
    if len(stock_tickers) >= STOCK_UNIVERSE_MIN_SYMBOLS:
        print(f"{reason}; using {len(stock_tickers)} configured fallback stock tickers.")
        return stock_tickers
    raise RuntimeError(
        f"{reason}; no cached universe is available and configured stock_tickers has only "
        f"{len(stock_tickers)} symbols, below required minimum {STOCK_UNIVERSE_MIN_SYMBOLS}. "
        "Refusing to silently scrape a tiny fallback list."
    )

def resolve_stock_tickers() -> list[str]:
    if STOCK_UNIVERSE == "krx_top_market_cap" and STOCK_UNIVERSE_REFRESH:
        source_errors = []
        for universe_source, fetcher in (
            ("Naver current", fetch_top_naver_current_stock_tickers),
            ("KRX", fetch_top_krx_stock_tickers),
            ("Naver mobile JSON", fetch_top_naver_mobile_stock_tickers),
            ("Naver legacy HTML", fetch_top_korean_stock_tickers),
        ):
            try:
                top_symbols = fetcher(STOCK_UNIVERSE_TOP_N)
                if len(top_symbols) < STOCK_UNIVERSE_MIN_SYMBOLS:
                    raise ValueError(f"{universe_source} returned only {len(top_symbols)} symbols")
            except Exception as exc:
                source_errors.append(f"{universe_source}: {type(exc).__name__}: {exc}")
                print(f"{universe_source} stock universe unavailable: {exc}")
                continue
            save_stock_universe_cache(top_symbols)
            print(
                f"Loaded {len(top_symbols)} Korean stock tickers from {universe_source} top market-cap universe "
                f"and wrote the list to {STOCK_UNIVERSE_CACHE_FILE}."
            )
            return top_symbols
        return get_fallback_stock_tickers(
            "Could not refresh top Korean stock universe | " + " | ".join(source_errors)
        )
    return stock_tickers


def create_stock_provider(provider_id: str):
    if provider_id == "yahoo":
        return YahooDailyOHLCVProvider(stock_tickers)
    if provider_id == "naver":
        return NaverDailyOHLCVProvider(stock_tickers)
    raise ValueError(f"Unsupported stock provider configured: {provider_id}")


def create_stock_providers() -> list:
    return [create_stock_provider(provider_id) for provider_id in STOCK_DATA_PROVIDERS]


def get_thread_exchange(exchange_id: str) -> ccxt.Exchange:
    clients = getattr(_thread_local, "exchange_clients", None)
    if clients is None:
        clients = {}
        _thread_local.exchange_clients = clients

    client = clients.get(exchange_id)
    if client is None:
        client = create_exchange(exchange_id)
        clients[exchange_id] = client
    return client


def get_api_call_delay(client: ccxt.Exchange) -> float:
    if getattr(client, "id", None) in {"naver", "yahoo"}:
        return max(0, STOCK_REQUEST_DELAY_SECONDS)
    return max(0.2, getattr(client, "rateLimit", 200) / 1000)


def get_timeframe_ms(timeframe: str) -> int:
    unit = timeframe[-1]
    amount_text = timeframe[:-1] or "1"
    amount = int(amount_text)
    if unit == "m":
        return amount * 60 * 1000
    if unit == "h":
        return amount * 60 * 60 * 1000
    if unit == "d":
        return amount * ONE_DAY_MS
    if unit == "w":
        return amount * 7 * ONE_DAY_MS
    if unit == "M":
        # Calendar months are variable length; 31 days keeps paged requests
        # wide enough while still bounded for exchanges that require endTime.
        return amount * 31 * ONE_DAY_MS
    raise ValueError(f"Unsupported timeframe configured: {timeframe}")


def build_ohlcv_params(client: ccxt.Exchange, since: int | None, limit: int) -> dict:
    if since is not None and getattr(client, "id", None) in {"yahoo", "naver"}:
        return {"period2": since + (max(1, limit) * get_timeframe_ms(TIMEFRAME))}
    if since is None or client.id != "mexc" or not MEXC_FORCE_END_TIME:
        return {}

    # MEXC spot klines accept startTime/endTime and document a max page size.
    # In practice, startTime-only requests can stop around the first capped
    # window for some markets, so every MEXC page is bounded explicitly.
    window_ms = max(1, limit) * get_timeframe_ms(TIMEFRAME)
    return {"endTime": since + window_ms - 1}


def get_start_since() -> int:
    start_date = datetime.datetime.strptime(START_DATE, "%Y-%m-%d")
    start_date = start_date.replace(tzinfo=datetime.UTC)
    return int(start_date.timestamp() * 1000)


def _format_exchange_debug(client: ccxt.Exchange) -> str:
    status = getattr(client, "last_http_status", None)
    endpoint = client.urls.get("api") if hasattr(client, "urls") else None
    body = getattr(client, "last_http_response", None)
    if isinstance(body, str) and len(body) > 400:
        body = body[:400] + "..."
    return (
        f"exchange={client.id}, endpoint={endpoint}, last_http_status={status}, "
        f"last_response_snippet={body}"
    )


def describe_symbol_mapping(client: ccxt.Exchange, symbol: str) -> str:
    market = client.markets.get(symbol) if hasattr(client, "markets") else None
    if not market:
        return f"symbol={symbol} (market mapping unavailable)"
    return (
        f"symbol={symbol}, market_id={market.get('id')}, type={market.get('type')}, "
        f"base={market.get('base')}, quote={market.get('quote')}"
    )


REQUIRED_COLUMNS = ["date", "open", "high", "low", "close", "volume"]
NUMERIC_COLUMNS = ["open", "high", "low", "close", "volume"]
PRICE_COLUMNS = ["open", "high", "low", "close"]


def load_local_data(filename: os.PathLike | str) -> pd.DataFrame:
    if not os.path.isfile(filename):
        return pd.DataFrame(columns=REQUIRED_COLUMNS)

    try:
        return normalize_dataframe(pd.read_csv(filename))
    except Exception as exc:
        print(f"Could not read local data from {filename}; treating it as empty | error_type={type(exc).__name__} | error={exc}")
        return pd.DataFrame(columns=REQUIRED_COLUMNS)


def get_local_first_timestamp(local_df: pd.DataFrame) -> int | None:
    if local_df.empty:
        return None
    dates = pd.to_datetime(local_df["date"], format=DATE_FORMAT, errors="coerce", utc=True).dropna()
    if dates.empty:
        return None
    return int(dates.min().timestamp() * 1000)


def get_local_latest_timestamp(local_df: pd.DataFrame) -> int | None:
    if local_df.empty:
        return None
    dates = pd.to_datetime(local_df["date"], format=DATE_FORMAT, errors="coerce", utc=True).dropna()
    if dates.empty:
        return None
    return int(dates.max().timestamp() * 1000)


def get_incremental_fetch_start(local_df: pd.DataFrame) -> int | None:
    latest_ts = get_local_latest_timestamp(local_df)
    if latest_ts is None:
        return None
    overlap_days = max(0, INCREMENTAL_OVERLAP_DAYS)
    return max(get_start_since(), latest_ts - (overlap_days * ONE_DAY_MS))


def merge_local_and_remote_data(local_df: pd.DataFrame, remote_df: pd.DataFrame) -> pd.DataFrame:
    return normalize_dataframe(pd.concat([local_df, remote_df], ignore_index=True))


def timestamp_to_date(timestamp_ms: int | None) -> str | None:
    if timestamp_ms is None:
        return None
    return datetime.datetime.fromtimestamp(timestamp_ms / 1000, datetime.UTC).date().isoformat()


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat()


def empty_source_memory() -> dict:
    return {"version": 1, "symbols": {}}


def load_source_memory() -> dict:
    if not SOURCE_MEMORY_FILE.is_file():
        return empty_source_memory()

    try:
        with SOURCE_MEMORY_FILE.open("r", encoding="utf-8") as memory_file:
            memory = json.load(memory_file)
    except Exception as exc:
        print(
            f"Could not read source memory from {SOURCE_MEMORY_FILE}; rebuilding it as symbols are processed "
            f"| error_type={type(exc).__name__} | error={exc}"
        )
        return empty_source_memory()

    if not isinstance(memory, dict):
        return empty_source_memory()
    memory.setdefault("version", 1)
    if not isinstance(memory.get("symbols"), dict):
        memory["symbols"] = {}
    return memory


def save_source_memory(source_memory: dict) -> None:
    SOURCE_MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    source_memory["updated_at"] = utc_now_iso()
    temp_file = SOURCE_MEMORY_FILE.with_suffix(SOURCE_MEMORY_FILE.suffix + ".tmp")
    with temp_file.open("w", encoding="utf-8") as memory_file:
        json.dump(source_memory, memory_file, indent=2, sort_keys=True)
        memory_file.write("\n")
    temp_file.replace(SOURCE_MEMORY_FILE)


def get_valid_memory_entry(symbol: str, candidates: list[str], source_memory: dict) -> dict | None:
    with _memory_lock:
        entry = source_memory.get("symbols", {}).get(symbol)
        if isinstance(entry, dict):
            entry = entry.copy()
    if not isinstance(entry, dict):
        return None

    selected_exchange = entry.get("selected_exchange")
    if selected_exchange not in candidates:
        return None
    if entry.get("timeframe") != TIMEFRAME:
        return None
    if entry.get("start_date") != START_DATE:
        return None
    if entry.get("source_exchanges") != SOURCE_EXCHANGES:
        return None
    if entry.get("mexc_longer_history_threshold_days") != MEXC_LONGER_HISTORY_THRESHOLD_DAYS:
        return None
    return entry


def remember_source_selection(
    source_memory: dict,
    symbol: str,
    selected_exchange: str,
    reason: str,
    first_available_by_exchange: dict[str, int | None],
    local_first_ts: int | None = None,
) -> None:
    first_available_dates = {
        exchange_id: timestamp_to_date(timestamp_ms)
        for exchange_id, timestamp_ms in first_available_by_exchange.items()
    }

    default_exchange = SOURCE_EXCHANGES[0] if SOURCE_EXCHANGES else selected_exchange
    selected_ts = first_available_by_exchange.get(selected_exchange)
    default_ts = first_available_by_exchange.get(default_exchange)
    selected_older_than_default_days = None
    if selected_ts is not None and default_ts is not None:
        selected_older_than_default_days = (default_ts - selected_ts) // ONE_DAY_MS

    mexc_older_than_binance_days = None
    binance_ts = first_available_by_exchange.get("binance")
    mexc_ts = first_available_by_exchange.get("mexc")
    if binance_ts is not None and mexc_ts is not None:
        mexc_older_than_binance_days = (binance_ts - mexc_ts) // ONE_DAY_MS

    entry = {
        "selected_exchange": selected_exchange,
        "reason": reason,
        "timeframe": TIMEFRAME,
        "start_date": START_DATE,
        "source_exchanges": SOURCE_EXCHANGES,
        "mexc_longer_history_threshold_days": MEXC_LONGER_HISTORY_THRESHOLD_DAYS,
        "first_available_dates": first_available_dates,
        "selected_older_than_default_days": selected_older_than_default_days,
        "mexc_older_than_binance_days": mexc_older_than_binance_days,
        "local_first_date": timestamp_to_date(local_first_ts),
        "updated_at": utc_now_iso(),
    }

    with _memory_lock:
        source_memory.setdefault("symbols", {})[symbol] = entry
        save_source_memory(source_memory)


def describe_memory_entry(symbol: str, entry: dict) -> str:
    selected_exchange = entry["selected_exchange"]
    selected_older_days = entry.get("selected_older_than_default_days")
    default_exchange = SOURCE_EXCHANGES[0] if SOURCE_EXCHANGES else selected_exchange
    if selected_older_days is None:
        age_summary = "history age difference not measured"
    elif selected_older_days > 0:
        age_summary = f"{selected_older_days} days older than {default_exchange.upper()}"
    elif selected_older_days < 0:
        age_summary = f"{-selected_older_days} days newer than {default_exchange.upper()}"
    else:
        age_summary = f"same first date as {default_exchange.upper()}"
    return f"{symbol}: using remembered {selected_exchange.upper()} source ({age_summary})."


def fetch_ohlcv_with_retries(client: ccxt.Exchange, symbol: str, since: int | None, limit: int):
    last_error = None
    params = build_ohlcv_params(client, since, limit)
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            return rate_limited_client_call(
                client,
                lambda: client.fetch_ohlcv(symbol, timeframe=TIMEFRAME, since=since, limit=limit, params=params),
            )
        except Exception as exc:
            last_error = exc
            print(f"{symbol}: fetch attempt {attempt}/{RETRY_COUNT} failed (since={since}, timeframe={TIMEFRAME}, limit={limit}, params={params}) | error_type={type(exc).__name__} | error={exc} | {_format_exchange_debug(client)}")
            time.sleep(RETRY_DELAY_SECONDS)
    raise RuntimeError(f"{symbol}: all fetch attempts failed for since={since}, timeframe={TIMEFRAME}, limit={limit}, params={params}. Last error: {type(last_error).__name__}: {last_error}. {_format_exchange_debug(client)}")


def decimal_quantum(decimal_places: int) -> Decimal:
    return Decimal(1).scaleb(-decimal_places)


def to_decimal(value) -> Decimal | None:
    if pd.isna(value):
        return None

    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None

    if not number.is_finite():
        return None
    return number


def get_price_scale_multiplier(dataframe: pd.DataFrame) -> Decimal:
    positive_prices: list[Decimal] = []
    for col in PRICE_COLUMNS:
        if col not in dataframe.columns:
            continue
        for value in dataframe[col].dropna():
            number = to_decimal(value)
            if number is not None and number > 0:
                positive_prices.append(number)

    if not positive_prices:
        return Decimal(1)

    max_price = max(positive_prices)
    if max_price >= PRICE_SCALE_THRESHOLD:
        return Decimal(1)

    min_price = min(positive_prices)
    return Decimal(10) ** max(0, -min_price.adjusted())


def scale_price_columns_for_tradestation(dataframe: pd.DataFrame) -> pd.DataFrame:
    out = dataframe.copy()
    multiplier = get_price_scale_multiplier(out)
    if multiplier == 1:
        return out

    for col in PRICE_COLUMNS:
        out[col] = out[col].map(lambda value: (to_decimal(value) * multiplier) if to_decimal(value) is not None else value)
    return out


def format_plain_decimal(value, decimal_places: int | None = None) -> str:
    """Return a CSV-safe decimal string without scientific notation."""
    number = to_decimal(value)
    if number is None:
        return "" if pd.isna(value) else str(value)

    if decimal_places is not None:
        quantum = decimal_quantum(decimal_places)
        rounded = number.quantize(quantum, rounding=ROUND_HALF_UP)
        number = quantum.copy_sign(number) if rounded == 0 and number != 0 else rounded

    plain_text = format(number, "f")
    if "." in plain_text:
        plain_text = plain_text.rstrip("0").rstrip(".")
    return plain_text or "0"


def prepare_csv_output(dataframe: pd.DataFrame) -> pd.DataFrame:
    out = scale_price_columns_for_tradestation(dataframe[REQUIRED_COLUMNS].copy())
    for col in PRICE_COLUMNS:
        out[col] = out[col].map(lambda value: format_plain_decimal(value, PRICE_DECIMAL_PLACES))
    out["volume"] = out["volume"].map(format_plain_decimal)
    return out


def save_to_csv(dataframe: pd.DataFrame, filename: os.PathLike | str) -> None:
    Path(filename).parent.mkdir(parents=True, exist_ok=True)
    prepare_csv_output(dataframe).to_csv(filename, index=False)


def value_uses_scientific_notation(value) -> bool:
    if not isinstance(value, str) or "e" not in value.lower():
        return False

    try:
        Decimal(value)
    except (InvalidOperation, ValueError):
        return False
    return True


def decimal_places_in_text(value: str) -> int:
    if "." not in value:
        return 0
    return len(value.split(".", 1)[1].rstrip("0"))


def price_uses_too_many_decimal_places(value) -> bool:
    if not isinstance(value, str):
        return False
    return decimal_places_in_text(value) > PRICE_DECIMAL_PLACES


def value_needs_decimal_rewrite(value, is_price: bool) -> bool:
    return value_uses_scientific_notation(value) or (is_price and price_uses_too_many_decimal_places(value))


def csv_needs_decimal_rewrite(filename: os.PathLike | str) -> bool:
    if not os.path.isfile(filename):
        return False

    with open(filename, newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            for col in NUMERIC_COLUMNS:
                if value_needs_decimal_rewrite(row.get(col, ""), col in PRICE_COLUMNS):
                    return True
    return False


def dataframe_needs_price_scaling(dataframe: pd.DataFrame) -> bool:
    return get_price_scale_multiplier(dataframe) != 1


def rewrite_decimal_format_csv(filename: os.PathLike | str, dataframe: pd.DataFrame) -> bool:
    if not csv_needs_decimal_rewrite(filename) and not dataframe_needs_price_scaling(dataframe):
        return False

    save_to_csv(dataframe, filename)
    return True


def prepare_dataframe_for_comparison(df: pd.DataFrame) -> pd.DataFrame:
    return normalize_dataframe(prepare_csv_output(df)).set_index("date").sort_index()


def dataframes_match(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    left_cmp = prepare_dataframe_for_comparison(left)
    right_cmp = prepare_dataframe_for_comparison(right)
    try:
        pd.testing.assert_frame_equal(
            left_cmp,
            right_cmp,
            check_dtype=False,
            check_exact=False,
            rtol=1e-10,
            atol=1e-12,
        )
    except AssertionError:
        return False
    return True


def normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    for col in NUMERIC_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=REQUIRED_COLUMNS)
    out = out.sort_values("date").drop_duplicates(subset="date", keep="last")
    out["date"] = out["date"].dt.strftime(DATE_FORMAT)
    return out[REQUIRED_COLUMNS]


def find_first_available_batch(
    symbol: str, client: ccxt.Exchange, start_since: int
) -> tuple[int | None, list | None]:
    """Find the earliest usable batch at or after start_since.

    Some exchanges, notably MEXC for newer listings, return an empty result when
    the requested since timestamp is far earlier than the symbol listing date. In
    that case, probe the available range instead of treating the symbol as
    unavailable.
    """
    batch = fetch_ohlcv_with_retries(client, symbol, start_since, PAGE_LIMIT)
    if batch:
        return start_since, batch

    recent_batch = fetch_ohlcv_with_retries(client, symbol, None, PAGE_LIMIT)
    if not recent_batch:
        return None, None

    first_recent_ts = recent_batch[0][0]
    if first_recent_ts <= start_since:
        return first_recent_ts, recent_batch

    print(
        f"{symbol}: {client.id.upper()} returned no candles at start_date={START_DATE}; "
        "probing for first available candle."
    )

    low = start_since
    high = first_recent_ts
    earliest_batch = recent_batch

    while high - low > ONE_DAY_MS:
        probe_since = low + ((high - low) // 2)
        probe_batch = fetch_ohlcv_with_retries(client, symbol, probe_since, PAGE_LIMIT)
        if probe_batch:
            high = probe_since
            earliest_batch = probe_batch
        else:
            low = probe_since + ONE_DAY_MS

    final_batch = fetch_ohlcv_with_retries(client, symbol, high, PAGE_LIMIT)
    if final_batch:
        return high, final_batch
    return first_recent_ts, earliest_batch


def fetch_all_ohlcv(
    symbol: str,
    client: ccxt.Exchange,
    start_since: int | None = None,
    discover_first_available: bool = True,
) -> pd.DataFrame | None:
    """Fetch daily history from start_since to present.

    The loop keeps requesting the next page until the exchange returns no more
    data. It intentionally does not stop when a page is smaller than PAGE_LIMIT
    because some ccxt exchanges silently cap the requested limit. Stopping on a
    short, capped page was truncating several symbols to roughly the first year
    of history.
    """
    all_rows = []
    seen_timestamps = set()
    since = start_since if start_since is not None else get_start_since()

    try:
        if discover_first_available:
            since, batch = find_first_available_batch(symbol, client, since)
        else:
            batch = fetch_ohlcv_with_retries(client, symbol, since, PAGE_LIMIT)

        while batch:
            new_rows = [row for row in batch if row[0] not in seen_timestamps]
            if not new_rows:
                raise RuntimeError(
                    f"exchange returned only duplicate candles after since={since}; "
                    "aborting to avoid saving partial data"
                )

            for row in new_rows:
                seen_timestamps.add(row[0])
            all_rows.extend(new_rows)

            last_ts = max(row[0] for row in new_rows)
            next_since = last_ts + 1
            if since is not None and next_since <= since:
                raise RuntimeError(
                    f"exchange did not advance history pagination (since={since}, next_since={next_since})"
                )

            since = next_since
            batch = fetch_ohlcv_with_retries(client, symbol, since, PAGE_LIMIT)
    except Exception as exc:
        print(f"{symbol}: failed fetching complete history from {client.id} using start_date={START_DATE} | error_type={type(exc).__name__} | error={exc} | {_format_exchange_debug(client)}")
        return None

    if not all_rows:
        return None

    df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert(None)
    df = df.drop(columns=["timestamp"])
    df["date"] = df["date"].dt.strftime(DATE_FORMAT)
    df = normalize_dataframe(df)
    return fill_missing_daily_candles(symbol, df, client.id.upper())


def find_date_gaps(df: pd.DataFrame) -> list[str]:
    dates = pd.to_datetime(df["date"], format=DATE_FORMAT, errors="coerce").dropna().sort_values()
    if dates.empty:
        return ["all-data-missing"]

    expected = pd.date_range(start=dates.min(), end=dates.max(), freq="D")
    missing = expected.difference(dates)
    return [d.strftime(DATE_FORMAT) for d in missing]


def is_stock_source_name(source_name: str) -> bool:
    normalized = (source_name or "").upper()
    return any(provider_id.upper() in normalized for provider_id in STOCK_PROVIDER_IDS)


def is_stock_data_client(client: ccxt.Exchange) -> bool:
    return getattr(client, "id", None) in STOCK_PROVIDER_IDS


def fill_missing_daily_candles(symbol: str, df: pd.DataFrame, source_name: str) -> pd.DataFrame:
    if TIMEFRAME != "1d" or df.empty:
        return df

    normalized = normalize_dataframe(df)
    if is_stock_source_name(source_name):
        return normalized

    dates = pd.to_datetime(normalized["date"], format=DATE_FORMAT, errors="coerce").dropna()
    if dates.empty:
        return normalized

    expected = pd.date_range(start=dates.min(), end=dates.max(), freq="D")
    missing = expected.difference(dates.sort_values())
    if missing.empty:
        return normalized

    print(
        f"{symbol}: {source_name} returned {len(missing)} missing daily candles; "
        "filling gaps with zero-volume candles using the previous close."
    )

    reindexed = normalized.copy()
    reindexed["date"] = pd.to_datetime(reindexed["date"], format=DATE_FORMAT, errors="coerce")
    reindexed = reindexed.dropna(subset=["date"]).set_index("date").sort_index()
    reindexed = reindexed.reindex(expected)

    missing_mask = reindexed["close"].isna()
    reindexed["close"] = reindexed["close"].ffill()
    for col in ["open", "high", "low"]:
        reindexed[col] = reindexed[col].where(~missing_mask, reindexed["close"])
    reindexed["volume"] = reindexed["volume"].where(~missing_mask, 0)

    reindexed = reindexed.reset_index().rename(columns={"index": "date"})
    return normalize_dataframe(reindexed)


def get_latest_expected_closed_date() -> pd.Timestamp:
    today_utc = datetime.datetime.now(datetime.UTC).date()
    latest_date = today_utc - datetime.timedelta(days=1) if TIMEFRAME == "1d" else today_utc
    return pd.Timestamp(latest_date)


def get_history_completeness_error(df: pd.DataFrame, source_name: str, allow_date_gaps: bool = False) -> str | None:
    if df is None or df.empty:
        return f"{source_name} completeness check failed because no rows were fetched."

    if not allow_date_gaps:
        gaps = find_date_gaps(df)
        if gaps:
            preview = ", ".join(gaps[:5])
            suffix = "..." if len(gaps) > 5 else ""
            return (
                f"{source_name} completeness check failed with {len(gaps)} "
                f"missing daily candles ({preview}{suffix})."
            )

    dates = pd.to_datetime(df["date"], format=DATE_FORMAT, errors="coerce").dropna()
    if dates.empty:
        return f"{source_name} completeness check failed because all dates were invalid."

    latest_expected = get_latest_expected_closed_date()
    latest_seen = dates.max()
    stale_days = (latest_expected - latest_seen).days
    if stale_days > MAX_STALE_DAYS:
        return (
            f"{source_name} completeness check failed because latest candle is "
            f"{latest_seen.date()}, expected within {MAX_STALE_DAYS} days of {latest_expected.date()}."
        )

    return None


def validate_complete_history(symbol: str, df: pd.DataFrame, source_name: str) -> bool:
    error = get_history_completeness_error(df, source_name)
    if error is not None:
        print(f"{symbol}: {error}")
        return False
    return True


def verify_saved_file(
    symbol: str,
    filename: os.PathLike | str,
    expected_df: pd.DataFrame,
    source_name: str,
    allow_date_gaps: bool = False,
) -> tuple[bool, str | None]:
    saved_df = load_local_data(filename)
    saved_source = f"saved {source_name}"
    error = get_history_completeness_error(saved_df, saved_source, allow_date_gaps=allow_date_gaps)
    if error is not None:
        print(f"{symbol}: {error}")
        return False, error

    if not dataframes_match(saved_df, expected_df):
        error = f"saved CSV does not match fetched {source_name} data after write."
        print(f"{symbol}: {error}")
        return False, error

    return True, None


def stock_date_scheme_marker_path(filename: os.PathLike | str) -> Path:
    return Path(f"{filename}.date_scheme")


def stock_date_scheme_is_current(filename: os.PathLike | str, client: ccxt.Exchange) -> bool:
    if not is_stock_data_client(client):
        return True
    marker_path = stock_date_scheme_marker_path(filename)
    try:
        return marker_path.read_text(encoding="utf-8").strip() == STOCK_DATE_SCHEME_VERSION
    except OSError:
        return False


def mark_stock_date_scheme_current(filename: os.PathLike | str, client: ccxt.Exchange) -> None:
    if not is_stock_data_client(client):
        return
    marker_path = stock_date_scheme_marker_path(filename)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(f"{STOCK_DATE_SCHEME_VERSION}\n", encoding="utf-8")


def verify_file_matches_exchange(symbol: str, filename: str, client: ccxt.Exchange) -> tuple[bool, str | None]:
    local_df = load_local_data(filename)
    if not local_df.empty and rewrite_decimal_format_csv(filename, local_df):
        print(f"{symbol}: rewrote local CSV decimal format for TradeStation compatibility.")

    stock_sparse_dates = is_stock_data_client(client)
    stock_date_scheme_current = stock_date_scheme_is_current(filename, client)
    if not local_df.empty and not stock_date_scheme_current:
        print(
            f"{symbol}: local CSV predates stock date scheme {STOCK_DATE_SCHEME_VERSION}; "
            f"fetching full {client.id.upper()} replacement."
        )

    local_first_ts = get_local_first_timestamp(local_df)
    local_gaps = [] if stock_sparse_dates or local_df.empty else find_date_gaps(local_df)
    local_error = (
        get_history_completeness_error(local_df, "local CSV", allow_date_gaps=stock_sparse_dates)
        if not local_df.empty
        else None
    )
    can_incremental_update = not local_df.empty and stock_date_scheme_current and not local_gaps and local_error is None

    if can_incremental_update:
        fetch_start = get_incremental_fetch_start(local_df)
        fetch_start_date = datetime.datetime.fromtimestamp(fetch_start / 1000, datetime.UTC).date()
        print(
            f"{symbol}: local CSV is complete; fetching only recent {client.id.upper()} "
            f"candles from {fetch_start_date} with {INCREMENTAL_OVERLAP_DAYS} overlap days."
        )
        remote_df = fetch_all_ohlcv(
            symbol,
            client,
            start_since=fetch_start,
            discover_first_available=False,
        )
        if remote_df is None or remote_df.empty:
            error = f"could not fetch recent exchange data from {client.id.upper()}"
            print(f"Verification failed: {error} for {symbol}. {describe_symbol_mapping(client, symbol)} | {_format_exchange_debug(client)}")
            return False, error

        merged_df = merge_local_and_remote_data(local_df, remote_df)
        source_name = client.id.upper()
        merged_error = get_history_completeness_error(merged_df, source_name, allow_date_gaps=stock_sparse_dates)
        if merged_error is not None:
            print(f"{symbol}: {merged_error}")
            print(f"{symbol}: refusing to save incomplete merged {source_name} history.")
            return False, merged_error

        if not dataframes_match(local_df, merged_df):
            save_to_csv(merged_df, filename)
            saved_ok, saved_error = verify_saved_file(
                symbol,
                filename,
                merged_df,
                source_name,
                allow_date_gaps=stock_sparse_dates,
            )
            if saved_ok:
                mark_stock_date_scheme_current(filename, client)
            return saved_ok, saved_error

        mark_stock_date_scheme_current(filename, client)
        return True, None

    force_stock_date_scheme_rewrite = not local_df.empty and not stock_date_scheme_current
    discover_first_available = local_first_ts is None or force_stock_date_scheme_rewrite
    fetch_start = get_start_since() if force_stock_date_scheme_rewrite else (local_first_ts if local_first_ts is not None else get_start_since())

    if force_stock_date_scheme_rewrite:
        print(
            f"{symbol}: rebuilding full {client.id.upper()} history from start_date={START_DATE} "
            f"for stock date scheme {STOCK_DATE_SCHEME_VERSION}."
        )
    elif discover_first_available:
        print(f"{symbol}: no local data found; discovering first available {client.id.upper()} candle.")
    else:
        local_first_date = datetime.datetime.fromtimestamp(fetch_start / 1000, datetime.UTC).date()
        print(
            f"{symbol}: local data starts on {local_first_date}; "
            f"fetching full {client.id.upper()} replacement because local CSV needs repair."
        )
        if local_gaps:
            print(f"{symbol}: found {len(local_gaps)} local gaps. Repairing from {client.id.upper()}.")
        if local_error is not None:
            print(f"{symbol}: {local_error}")

    remote_df = fetch_all_ohlcv(
        symbol,
        client,
        start_since=fetch_start,
        discover_first_available=discover_first_available,
    )
    if remote_df is None or remote_df.empty:
        error = f"could not fetch exchange data from {client.id.upper()}"
        print(f"Verification failed: {error} for {symbol}. {describe_symbol_mapping(client, symbol)} | {_format_exchange_debug(client)}")
        return False, error

    source_name = client.id.upper()
    remote_error = get_history_completeness_error(remote_df, source_name, allow_date_gaps=stock_sparse_dates)
    if remote_error is not None:
        print(f"{symbol}: {remote_error}")
        print(f"{symbol}: refusing to save incomplete {source_name} history.")
        return False, remote_error

    if local_df.empty:
        save_to_csv(remote_df, filename)
        saved_ok, saved_error = verify_saved_file(
            symbol,
            filename,
            remote_df,
            source_name,
            allow_date_gaps=stock_sparse_dates,
        )
        if not saved_ok:
            return False, saved_error
        mark_stock_date_scheme_current(filename, client)
        print(f"{symbol}: local file created from {source_name} full history.")
        return True, None

    if not dataframes_match(local_df, remote_df):
        print(f"{symbol}: local data differs from complete {source_name} history. Replacing with authoritative data.")
        save_to_csv(remote_df, filename)
        saved_ok, saved_error = verify_saved_file(
            symbol,
            filename,
            remote_df,
            source_name,
            allow_date_gaps=stock_sparse_dates,
        )
        if saved_ok:
            mark_stock_date_scheme_current(filename, client)
        return saved_ok, saved_error

    mark_stock_date_scheme_current(filename, client)
    return True, None


def get_first_available_timestamp(symbol: str, client: ccxt.Exchange) -> int | None:
    try:
        _, batch = find_first_available_batch(symbol, client, get_start_since())
    except Exception as exc:
        print(
            f"{symbol}: could not inspect first candle on {client.id.upper()} "
            f"for source selection | error_type={type(exc).__name__} | error={exc}"
        )
        return None

    if not batch:
        return None
    return batch[0][0]


def exchange_covers_local_start(
    symbol: str,
    exchange_id: str,
    client: ccxt.Exchange,
    local_first_ts: int,
) -> bool:
    try:
        batch = fetch_ohlcv_with_retries(client, symbol, local_first_ts, 1)
    except Exception as exc:
        print(
            f"{symbol}: could not check whether {exchange_id.upper()} covers existing "
            f"local start | error_type={type(exc).__name__} | error={exc}"
        )
        return False

    if not batch:
        return False
    return batch[0][0] <= local_first_ts


def select_source_exchange(
    symbol: str,
    available_by_exchange: dict[str, set[str]],
    bootstrap_clients: dict[str, ccxt.Exchange],
    source_memory: dict,
) -> str | None:
    candidates = [
        exchange_id
        for exchange_id in SOURCE_EXCHANGES
        if symbol in available_by_exchange.get(exchange_id, set())
    ]
    if not candidates:
        return None

    default_exchange = candidates[0]
    memory_entry = get_valid_memory_entry(symbol, candidates, source_memory)
    if memory_entry is not None:
        print(describe_memory_entry(symbol, memory_entry))
        return memory_entry["selected_exchange"]

    local_df = load_local_data(get_output_path(symbol))
    local_first_ts = get_local_first_timestamp(local_df)
    first_available_by_exchange: dict[str, int | None] = {}

    if "binance" in candidates and "mexc" in candidates:
        binance_ts = get_first_available_timestamp(symbol, bootstrap_clients["binance"])
        mexc_ts = get_first_available_timestamp(symbol, bootstrap_clients["mexc"])
        first_available_by_exchange = {"binance": binance_ts, "mexc": mexc_ts}

        if binance_ts is not None and mexc_ts is not None:
            threshold_ms = MEXC_LONGER_HISTORY_THRESHOLD_DAYS * ONE_DAY_MS
            if mexc_ts + threshold_ms < binance_ts:
                binance_start = datetime.datetime.fromtimestamp(binance_ts / 1000, datetime.UTC).date()
                mexc_start = datetime.datetime.fromtimestamp(mexc_ts / 1000, datetime.UTC).date()
                extra_days = (binance_ts - mexc_ts) // ONE_DAY_MS
                print(
                    f"{symbol}: using MEXC because its history starts {extra_days} days "
                    f"before Binance ({mexc_start} vs {binance_start})."
                )
                remember_source_selection(
                    source_memory,
                    symbol,
                    "mexc",
                    "mexc_has_longer_history",
                    first_available_by_exchange,
                    local_first_ts,
                )
                return "mexc"

            remember_source_selection(
                source_memory,
                symbol,
                default_exchange,
                "default_exchange_has_sufficient_history",
                first_available_by_exchange,
                local_first_ts,
            )
            return default_exchange

    if local_first_ts is not None:
        local_first_date = datetime.datetime.fromtimestamp(local_first_ts / 1000, datetime.UTC).date()
        for exchange_id in candidates:
            client = bootstrap_clients[exchange_id]
            if exchange_covers_local_start(symbol, exchange_id, client, local_first_ts):
                print(
                    f"{symbol}: existing local data starts on {local_first_date}; "
                    f"using {exchange_id.upper()} without first-candle discovery."
                )
                remember_source_selection(
                    source_memory,
                    symbol,
                    exchange_id,
                    "exchange_covers_existing_local_start",
                    {exchange_id: local_first_ts},
                    local_first_ts,
                )
                return exchange_id

        print(
            f"{symbol}: no configured exchange confirmed coverage for existing "
            f"local start {local_first_date}; falling back to {default_exchange.upper()}."
        )

    remember_source_selection(
        source_memory,
        symbol,
        default_exchange,
        "default_exchange",
        first_available_by_exchange,
        local_first_ts,
    )
    return default_exchange


def get_output_path(symbol: str, output_dir: Path | None = None) -> Path:
    safe_symbol = symbol.replace("/", "").replace(".", "_").lower()
    filename = f"{safe_symbol}.csv"
    return (output_dir or OUTPUT_DIR) / filename


def select_pair_source(
    pair: str,
    available_by_exchange: dict[str, set[str]],
    bootstrap_clients: dict[str, ccxt.Exchange],
    source_memory: dict,
) -> tuple[str, str | None]:
    return pair, select_source_exchange(pair, available_by_exchange, bootstrap_clients, source_memory)


def get_symbol_exchange_candidates(
    symbol: str, selected_exchange_id: str, active_exchanges: set[str] | None = None
) -> list[str]:
    candidates = [selected_exchange_id]
    candidates.extend(
        exchange_id
        for exchange_id in SOURCE_EXCHANGES
        if exchange_id != selected_exchange_id
    )
    if active_exchanges is not None:
        candidates = [exchange_id for exchange_id in candidates if exchange_id in active_exchanges]
    return candidates


def process_symbol(symbol_and_exchange: tuple) -> tuple[str, bool, str | None]:
    symbol, selected_exchange_id = symbol_and_exchange[:2]
    active_exchanges = symbol_and_exchange[2] if len(symbol_and_exchange) > 2 else None
    filename = get_output_path(symbol)
    failure_reasons = []

    for exchange_id in get_symbol_exchange_candidates(symbol, selected_exchange_id, active_exchanges):
        try:
            client = get_thread_exchange(exchange_id)
        except ccxt.BaseError as exc:
            reason = f"{exchange_id.upper()} unavailable: {exc}"
            print(f"{symbol}: {reason}")
            failure_reasons.append(reason)
            continue
        if symbol not in getattr(client, "symbols", []):
            reason = f"{exchange_id.upper()} does not list {symbol}"
            print(f"{symbol}: skipping {exchange_id.upper()} fallback because {reason}.")
            failure_reasons.append(reason)
            continue

        fallback_note = "" if exchange_id == selected_exchange_id else " fallback"
        print(f"Processing {symbol} using {exchange_id.upper()}{fallback_note}...")
        ok, failure_reason = verify_file_matches_exchange(symbol, filename, client)
        if ok:
            print(f"{symbol}: verification complete using {exchange_id.upper()}{fallback_note}.")
            return symbol, True, None

        reason = failure_reason or "unknown verification error"
        print(f"{symbol}: verification failed using {exchange_id.upper()}: {reason}")
        failure_reasons.append(f"{exchange_id.upper()}: {reason}")

    return symbol, False, "; ".join(failure_reasons)


def process_stock_symbol(symbol: str, stock_providers: list) -> tuple[str, bool, str | None]:
    filename = get_output_path(symbol, STOCK_OUTPUT_DIR)
    failure_reasons = []

    for provider in stock_providers:
        print(f"Processing {symbol} using {provider.id.upper()} stock data...")
        ok, failure_reason = verify_file_matches_exchange(symbol, filename, provider)
        if ok:
            print(f"{symbol}: verification complete using {provider.id.upper()} stock data.")
            return symbol, True, None

        reason = failure_reason or "unknown verification error"
        print(f"{symbol}: stock verification failed using {provider.id.upper()}: {reason}")
        failure_reasons.append(f"{provider.id.upper()}: {reason}")

    return symbol, False, "; ".join(failure_reasons)


def process_crypto_symbols(
    available_by_exchange: dict[str, set[str]],
    bootstrap_clients: dict[str, ccxt.Exchange],
    source_memory: dict,
) -> list[tuple[str, bool, str | None]]:
    with ThreadPoolExecutor(max_workers=SOURCE_SELECTION_WORKERS) as executor:
        source_results = list(
            executor.map(
                lambda pair: select_pair_source(pair, available_by_exchange, bootstrap_clients, source_memory),
                trading_pairs,
            )
        )

    selected_pairs = []
    skipped = []
    for pair, exchange_id in source_results:
        if exchange_id is None:
            skipped.append(pair)
        else:
            selected_pairs.append((pair, exchange_id, set(bootstrap_clients)))

    if skipped:
        print(f"Skipped symbols absent from available exchanges ({', '.join(bootstrap_clients)}): {', '.join(skipped)}")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        return list(executor.map(process_symbol, selected_pairs))


def process_stock_symbols(stock_providers: list) -> list[tuple[str, bool, str | None]]:
    if not stock_providers:
        return []

    provider_names = ", ".join(provider.id.upper() for provider in stock_providers)
    print(
        f"Processing {len(stock_tickers)} stock symbols using stock provider priority: "
        f"{provider_names} with {STOCK_MAX_WORKERS} stock workers..."
    )
    with ThreadPoolExecutor(max_workers=STOCK_MAX_WORKERS) as executor:
        return list(executor.map(lambda symbol: process_stock_symbol(symbol, stock_providers), stock_tickers))


def main() -> None:
    bootstrap_clients = bootstrap_exchanges()
    stock_universe_error = None
    try:
        resolved_stock_tickers = resolve_stock_tickers()
    except RuntimeError as exc:
        stock_universe_error = exc
        resolved_stock_tickers = []
        print(f"Stock universe unavailable; continuing the crypto pipeline: {exc}")
    globals()["stock_tickers"] = resolved_stock_tickers
    stock_providers = create_stock_providers() if resolved_stock_tickers else []
    available_by_exchange = {
        exchange_id: set(client.symbols)
        for exchange_id, client in bootstrap_clients.items()
    }

    exchange_summary = ", ".join(
        f"{exchange_id}={client.urls.get('api')}"
        for exchange_id, client in bootstrap_clients.items()
    )
    print(
        f"Using source priority={SOURCE_EXCHANGES}, endpoints=({exchange_summary}), "
        f"timeframe={TIMEFRAME}, page_limit={PAGE_LIMIT}, start_date={START_DATE}, "
        f"output_dir={OUTPUT_DIR}, stock_output_dir={STOCK_OUTPUT_DIR}, source_memory_file={SOURCE_MEMORY_FILE}, "
        f"max_workers={MAX_WORKERS}, source_selection_workers={SOURCE_SELECTION_WORKERS}, "
        f"stock_max_workers={STOCK_MAX_WORKERS}, "
        f"mexc_longer_history_threshold_days={MEXC_LONGER_HISTORY_THRESHOLD_DAYS}, "
        f"mexc_force_end_time={MEXC_FORCE_END_TIME}, max_stale_days={MAX_STALE_DAYS}, "
        f"fail_on_verification_errors={FAIL_ON_VERIFICATION_ERRORS}, "
        f"stock_tickers={len(stock_tickers)}, stock_providers={[provider.id for provider in stock_providers]}"
    )

    source_memory = load_source_memory()

    with ThreadPoolExecutor(max_workers=2) as executor:
        crypto_future = executor.submit(process_crypto_symbols, available_by_exchange, bootstrap_clients, source_memory)
        stock_future = executor.submit(process_stock_symbols, stock_providers)
        process_results = crypto_future.result() + stock_future.result()

    failed = [(symbol, reason) for symbol, ok, reason in process_results if not ok]
    if failed:
        failed_symbols = ", ".join(symbol for symbol, _ in failed)
        failure_summary = f"Verification failed for {len(failed)} symbols: {failed_symbols}"
        print(failure_summary)
        for symbol, reason in failed:
            print(f"  - {symbol}: {reason or 'unknown verification error'}")
        if FAIL_ON_VERIFICATION_ERRORS:
            raise SystemExit(failure_summary)
        print(
            "Continuing with exit code 0 because fail_on_verification_errors=false; "
            "failed symbols were not saved as complete data."
        )
    if stock_universe_error is not None:
        raise SystemExit(f"Crypto pipeline completed, but Korean stock universe failed: {stock_universe_error}")


if __name__ == "__main__":
    main()
