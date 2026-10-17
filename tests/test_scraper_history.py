import importlib.util
import time
import types
import io
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("ccxt")


MODULE_PATH = Path(__file__).resolve().parents[1] / "cryptodatav3.5.py"
spec = importlib.util.spec_from_file_location("cryptodata", MODULE_PATH)
cryptodata = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cryptodata)


def test_safe_print_escapes_unicode_for_cp1252_stream():
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252", errors="strict", newline="")

    cryptodata.safe_print("币安人生/USDT: using remembered MEXC source.", file=stream)
    stream.flush()

    assert buffer.getvalue().decode("cp1252") == (
        "\\u5e01\\u5b89\\u4eba\\u751f/USDT: using remembered MEXC source.\n"
    )


class FakeClient:
    id = "fake"
    urls = {"api": "fake-api"}
    rateLimit = 0
    last_http_status = None
    last_http_response = None


def row(timestamp):
    return [timestamp, 1, 2, 0.5, 1.5, 100]


def test_mexc_fetches_are_bounded_with_end_time(monkeypatch):
    client = FakeClient()
    client.id = "mexc"
    monkeypatch.setattr(cryptodata, "TIMEFRAME", "1d")
    monkeypatch.setattr(cryptodata, "MEXC_FORCE_END_TIME", True)

    params = cryptodata.build_ohlcv_params(client, 0, 3)

    assert params == {"endTime": (3 * cryptodata.ONE_DAY_MS) - 1}


def test_non_mexc_fetches_are_not_bounded(monkeypatch):
    client = FakeClient()
    client.id = "binance"
    monkeypatch.setattr(cryptodata, "MEXC_FORCE_END_TIME", True)

    assert cryptodata.build_ohlcv_params(client, 0, 3) == {}


def test_fetch_all_ohlcv_continues_after_short_exchange_capped_page(monkeypatch):
    day = cryptodata.ONE_DAY_MS
    first_page = [row(i * day) for i in range(365)]
    second_page = [row(i * day) for i in range(365, 730)]
    calls = []

    def fake_fetch(client, symbol, since, limit):
        calls.append(since)
        if len(calls) == 1:
            return first_page
        if len(calls) == 2:
            return second_page
        return []

    monkeypatch.setattr(cryptodata, "fetch_ohlcv_with_retries", fake_fetch)
    monkeypatch.setattr(cryptodata, "get_api_call_delay", lambda client: 0)

    df = cryptodata.fetch_all_ohlcv(
        "TEST/USDT",
        FakeClient(),
        start_since=0,
        discover_first_available=False,
    )

    assert len(df) == 730
    assert len(calls) == 3
    assert calls[1] == first_page[-1][0] + 1


def test_fetch_all_ohlcv_refuses_duplicate_only_page(monkeypatch):
    duplicate_page = [row(0)]

    def fake_fetch(client, symbol, since, limit):
        return duplicate_page

    monkeypatch.setattr(cryptodata, "fetch_ohlcv_with_retries", fake_fetch)
    monkeypatch.setattr(cryptodata, "get_api_call_delay", lambda client: 0)

    df = cryptodata.fetch_all_ohlcv(
        "TEST/USDT",
        FakeClient(),
        start_since=0,
        discover_first_available=False,
    )

    assert df is None


def test_fetch_all_ohlcv_fills_missing_daily_candles(monkeypatch):
    day = cryptodata.ONE_DAY_MS
    calls = []

    def fake_fetch(client, symbol, since, limit):
        calls.append(since)
        if len(calls) == 1:
            return [row(0), row(2 * day)]
        return []

    monkeypatch.setattr(cryptodata, "TIMEFRAME", "1d")
    monkeypatch.setattr(cryptodata, "fetch_ohlcv_with_retries", fake_fetch)
    monkeypatch.setattr(cryptodata, "get_api_call_delay", lambda client: 0)

    df = cryptodata.fetch_all_ohlcv(
        "TEST/USDT",
        FakeClient(),
        start_since=0,
        discover_first_available=False,
    )

    assert df["date"].tolist() == ["01/01/1970", "01/02/1970", "01/03/1970"]
    synthetic_row = df[df["date"] == "01/02/1970"].iloc[0]
    assert synthetic_row["open"] == 1.5
    assert synthetic_row["high"] == 1.5
    assert synthetic_row["low"] == 1.5
    assert synthetic_row["close"] == 1.5
    assert synthetic_row["volume"] == 0


def test_validate_complete_history_rejects_missing_daily_candle(monkeypatch):
    monkeypatch.setattr(cryptodata, "MAX_STALE_DAYS", 99999)
    df = pd.DataFrame(
        {
            "date": ["01/01/2024", "01/03/2024"],
            "open": [1, 1],
            "high": [2, 2],
            "low": [0.5, 0.5],
            "close": [1.5, 1.5],
            "volume": [100, 100],
        }
    )

    assert cryptodata.validate_complete_history("TEST/USDT", df, "FAKE") is False


def test_dataframes_match_allows_csv_float_roundtrip_noise():
    left = pd.DataFrame(
        {
            "date": ["01/01/2024"],
            "open": [1.000000000001],
            "high": [2.0],
            "low": [0.5],
            "close": [1.5],
            "volume": [100.0],
        }
    )
    right = pd.DataFrame(
        {
            "date": ["01/01/2024"],
            "open": [1.0],
            "high": [2.0],
            "low": [0.5],
            "close": [1.5],
            "volume": [100.0],
        }
    )

    assert cryptodata.dataframes_match(left, right) is True


def test_dataframes_match_allows_configured_price_scaling():
    left = pd.DataFrame(
        {
            "date": ["01/01/2024"],
            "open": [1.23456789],
            "high": [2.23456789],
            "low": [1.03456789],
            "close": [1.53456789],
            "volume": [100.0],
        }
    )
    right = pd.DataFrame(
        {
            "date": ["01/01/2024"],
            "open": [0.000000123456789],
            "high": [0.000000223456789],
            "low": [0.000000103456789],
            "close": [0.000000153456789],
            "volume": [100.0],
        }
    )

    assert cryptodata.dataframes_match(left, right) is True


def test_save_to_csv_writes_plain_decimals_for_tiny_prices(tmp_path):
    df = pd.DataFrame(
        {
            "date": ["01/01/2024", "01/02/2024"],
            "open": [1e-06, 0.000000123456789],
            "high": [2.6725e-06, 0.0000002],
            "low": [1e-06, 0.0000001],
            "close": [2.5127e-06, 0.00000015],
            "volume": [155981859857.19666, 0.0],
        }
    )
    output_file = tmp_path / "tiny.csv"

    cryptodata.save_to_csv(df, output_file)

    csv_text = output_file.read_text()
    assert "e-" not in csv_text.lower()
    assert "10" in csv_text
    assert "1.23456789" in csv_text
    assert "0.000000123456789" not in csv_text
    assert cryptodata.load_local_data(output_file).shape == (2, 6)


def test_rewrite_decimal_format_csv_fixes_existing_local_file(tmp_path):
    output_file = tmp_path / "tiny.csv"
    output_file.write_text(
        "date,open,high,low,close,volume\n"
        "01/01/2024,1e-06,2.6725e-06,1e-06,2.5127e-06,155981859857.19666\n"
    )
    local_df = cryptodata.load_local_data(output_file)

    assert cryptodata.rewrite_decimal_format_csv(output_file, local_df) is True

    csv_text = output_file.read_text()
    assert "e-" not in csv_text.lower()
    assert "1" in csv_text
    assert "2.6725" in csv_text
    rewritten_df = cryptodata.load_local_data(output_file)
    assert cryptodata.rewrite_decimal_format_csv(output_file, rewritten_df) is False


def test_rewrite_decimal_format_csv_limits_existing_price_precision(tmp_path):
    output_file = tmp_path / "too_precise.csv"
    output_file.write_text(
        "date,open,high,low,close,volume\n"
        "01/01/2024,0.000000123456789,0.000000223456789,0.000000103456789,0.000000153456789,100\n"
    )
    local_df = cryptodata.load_local_data(output_file)

    assert cryptodata.rewrite_decimal_format_csv(output_file, local_df) is True

    csv_text = output_file.read_text()
    assert "1.23456789" in csv_text
    assert "0.000000123456789" not in csv_text


class FakeBootstrapClient:
    symbols = ["BAD/USDT"]
    urls = {"api": "fake-api"}


def configure_fake_main(monkeypatch, fail_on_errors):
    monkeypatch.setattr(cryptodata, "SOURCE_EXCHANGES", ["fake"])
    monkeypatch.setattr(cryptodata, "trading_pairs", ["BAD/USDT"])
    monkeypatch.setattr(cryptodata, "stock_tickers", [])
    monkeypatch.setattr(cryptodata, "SOURCE_SELECTION_WORKERS", 1)
    monkeypatch.setattr(cryptodata, "MAX_WORKERS", 1)
    monkeypatch.setattr(cryptodata, "FAIL_ON_VERIFICATION_ERRORS", fail_on_errors)
    monkeypatch.setattr(cryptodata, "create_exchange", lambda exchange_id: FakeBootstrapClient())
    monkeypatch.setattr(
        cryptodata,
        "select_pair_source",
        lambda pair, available_by_exchange, bootstrap_clients, source_memory: (pair, "fake"),
    )
    monkeypatch.setattr(cryptodata, "load_source_memory", lambda: cryptodata.empty_source_memory())
    monkeypatch.setattr(cryptodata, "process_symbol", lambda symbol_and_exchange: (symbol_and_exchange[0], False, "fake failure"))



def test_process_stock_symbols_uses_configured_worker_pool(monkeypatch):
    monkeypatch.setattr(cryptodata, "stock_tickers", ["005930.KS", "000660.KS", "035420.KS"])
    monkeypatch.setattr(cryptodata, "STOCK_MAX_WORKERS", 3)

    def fake_process_stock_symbol(symbol, stock_providers):
        time.sleep(0.05)
        return symbol, True, None

    monkeypatch.setattr(cryptodata, "process_stock_symbol", fake_process_stock_symbol)

    started_at = time.monotonic()
    results = cryptodata.process_stock_symbols([types.SimpleNamespace(id="naver")])
    elapsed = time.monotonic() - started_at

    assert [symbol for symbol, ok, reason in results] == ["005930.KS", "000660.KS", "035420.KS"]
    assert all(ok and reason is None for _, ok, reason in results)
    assert elapsed < 0.13


def test_main_runs_crypto_and_stock_pipelines_concurrently(monkeypatch):
    monkeypatch.setattr(cryptodata, "SOURCE_EXCHANGES", ["fake"])
    monkeypatch.setattr(cryptodata, "stock_tickers", ["005930.KS"])
    monkeypatch.setattr(cryptodata, "FAIL_ON_VERIFICATION_ERRORS", True)
    monkeypatch.setattr(cryptodata, "create_exchange", lambda exchange_id: FakeBootstrapClient())
    monkeypatch.setattr(cryptodata, "create_stock_providers", lambda: [types.SimpleNamespace(id="naver")])
    monkeypatch.setattr(cryptodata, "load_source_memory", lambda: cryptodata.empty_source_memory())

    def fake_crypto_pipeline(available_by_exchange, bootstrap_clients, source_memory):
        time.sleep(0.08)
        return [("BTC/USDT", True, None)]

    def fake_stock_pipeline(stock_providers):
        time.sleep(0.08)
        return [("005930.KS", True, None)]

    monkeypatch.setattr(cryptodata, "process_crypto_symbols", fake_crypto_pipeline)
    monkeypatch.setattr(cryptodata, "process_stock_symbols", fake_stock_pipeline)

    started_at = time.monotonic()
    cryptodata.main()
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.14

def test_main_keeps_zero_exit_for_nonfatal_verification_errors(monkeypatch, capsys):
    configure_fake_main(monkeypatch, fail_on_errors=False)

    cryptodata.main()

    captured = capsys.readouterr()
    assert "Verification failed for 1 symbols: BAD/USDT" in captured.out
    assert "fail_on_verification_errors=false" in captured.out


def test_main_can_fail_on_verification_errors_when_configured(monkeypatch):
    configure_fake_main(monkeypatch, fail_on_errors=True)

    with pytest.raises(SystemExit):
        cryptodata.main()


def test_process_symbol_tries_fallback_exchange_after_selected_failure(monkeypatch):
    calls = []
    clients = {
        "mexc": FakeClient(),
        "binance": FakeClient(),
    }
    clients["mexc"].id = "mexc"
    clients["mexc"].symbols = ["DOGE/USDT"]
    clients["binance"].id = "binance"
    clients["binance"].symbols = ["DOGE/USDT"]

    def fake_verify(symbol, filename, client):
        calls.append(client.id)
        if client.id == "mexc":
            return False, "MEXC stale"
        return True, None

    monkeypatch.setattr(cryptodata, "SOURCE_EXCHANGES", ["binance", "mexc"])
    monkeypatch.setattr(cryptodata, "get_thread_exchange", lambda exchange_id: clients[exchange_id])
    monkeypatch.setattr(cryptodata, "verify_file_matches_exchange", fake_verify)

    assert cryptodata.process_symbol(("DOGE/USDT", "mexc")) == ("DOGE/USDT", True, None)
    assert calls == ["mexc", "binance"]


def test_get_output_path_sanitizes_stock_suffix(monkeypatch):
    monkeypatch.setattr(cryptodata, "OUTPUT_DIR", Path("data"))
    monkeypatch.setattr(cryptodata, "STOCK_OUTPUT_DIR", Path("korean_stock_data"))

    assert cryptodata.get_output_path("005930.KS") == Path("data/005930_ks.csv")
    assert cryptodata.get_output_path("005930.KS", cryptodata.STOCK_OUTPUT_DIR) == Path("korean_stock_data/005930_ks.csv")


def test_yahoo_provider_parses_daily_csv(monkeypatch):
    csv_body = "Date,Open,High,Low,Close,Adj Close,Volume\n2024-01-02,100,110,90,105,105,12345\n"

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return csv_body.encode("utf-8")

    def fake_urlopen(request, timeout):
        assert "005930.KS" in request.full_url
        assert "interval=1d" in request.full_url
        assert timeout == 30
        return FakeResponse()

    monkeypatch.setattr(cryptodata, "urlopen", fake_urlopen)
    provider = cryptodata.YahooDailyOHLCVProvider(["005930.KS"])

    rows = provider.fetch_ohlcv("005930.KS", since=0, limit=10)

    assert rows == [[1704067200000, 100.0, 110.0, 90.0, 105.0, 12345.0]]


def test_create_stock_providers_uses_configured_priority(monkeypatch):
    monkeypatch.setattr(cryptodata, "STOCK_DATA_PROVIDERS", ["naver", "yahoo"])
    monkeypatch.setattr(cryptodata, "stock_tickers", ["005930.KS"])

    providers = cryptodata.create_stock_providers()

    assert [provider.id for provider in providers] == ["naver", "yahoo"]
    assert all(provider.symbols == ["005930.KS"] for provider in providers)


def test_naver_provider_parses_and_filters_daily_xml(monkeypatch):
    xml_body = (
        '<item data="20240102|100|110|90|105|12345" />'
        '<item data="20240103|106|111|101|108|23456" />'
    )

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return ('<?xml version="1.0" encoding="EUC-KR" ?>' + xml_body).encode("euc-kr")

    def fake_urlopen(request, timeout):
        assert "symbol=005930" in request.full_url
        assert "timeframe=day" in request.full_url
        assert timeout == 30
        return FakeResponse()

    monkeypatch.setattr(cryptodata, "urlopen", fake_urlopen)
    monkeypatch.setattr(cryptodata, "krx_trading_day_has_started", lambda: False)
    provider = cryptodata.NaverDailyOHLCVProvider(["005930.KS"])

    rows = provider.fetch_ohlcv("005930.KS", since=1704153600000, limit=10)

    assert rows == [[1704153600000, 106.0, 111.0, 101.0, 108.0, 23456.0]]


def test_naver_provider_adds_today_quote_during_trading_day(monkeypatch):
    xml_body = '<item data="20240102|100|110|90|105|12345" />'
    quote_body = """
        <span>현재가</span><span>120,500</span>
        <span>시가</span><span>119,000</span>
        <span>고가</span><span>121,000</span>
        <span>저가</span><span>118,500</span>
        <span>거래량</span><span>1,234,567</span>
    """

    class FakeResponse:
        status = 200
        headers = {}

        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self.body.encode("euc-kr")

    def fake_urlopen(request, timeout):
        if "sise.nhn" in request.full_url:
            return FakeResponse('<?xml version="1.0" encoding="EUC-KR" ?>' + xml_body)
        assert "item/main.naver?code=005930" in request.full_url
        return FakeResponse(quote_body)

    monkeypatch.setattr(cryptodata, "urlopen", fake_urlopen)
    monkeypatch.setattr(cryptodata, "krx_trading_day_has_started", lambda: True)
    monkeypatch.setattr(cryptodata, "get_tradestation_today_timestamp_ms", lambda: 1704326400000)
    provider = cryptodata.NaverDailyOHLCVProvider(["005930.KS"])

    rows = provider.fetch_ohlcv("005930.KS", since=1704067200000, limit=10)

    assert rows == [
        [1704067200000, 100.0, 110.0, 90.0, 105.0, 12345.0],
        [1704326400000, 119000.0, 121000.0, 118500.0, 120500.0, 1234567.0],
    ]


def test_stock_provider_does_not_fill_weekend_placeholders():
    df = pd.DataFrame(
        [
            {"date": "06/28/2026", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 100},
            {"date": "06/29/2026", "open": 2, "high": 3, "low": 2, "close": 3, "volume": 200},
            {"date": "07/02/2026", "open": 3, "high": 4, "low": 3, "close": 4, "volume": 300},
        ]
    )

    out = cryptodata.fill_missing_daily_candles("005930.KS", df, "NAVER")

    assert out["date"].tolist() == ["06/28/2026", "06/29/2026", "07/02/2026"]
    assert out["volume"].tolist() == [100, 200, 300]


def test_process_stock_symbol_falls_back_after_first_provider_failure(monkeypatch):
    providers = [cryptodata.YahooDailyOHLCVProvider(["005930.KS"]), cryptodata.NaverDailyOHLCVProvider(["005930.KS"])]
    calls = []

    def fake_verify(symbol, filename, provider):
        calls.append(provider.id)
        if provider.id == "yahoo":
            return False, "rate limited"
        return True, None

    monkeypatch.setattr(cryptodata, "verify_file_matches_exchange", fake_verify)
    monkeypatch.setattr(cryptodata, "STOCK_OUTPUT_DIR", Path("korean_stock_data"))

    assert cryptodata.process_stock_symbol("005930.KS", providers) == ("005930.KS", True, None)
    assert calls == ["yahoo", "naver"]


def test_bootstrap_skips_restricted_binance_and_keeps_mexc(monkeypatch, capsys):
    mexc = FakeClient()
    mexc.id = "mexc"
    monkeypatch.setattr(cryptodata, "SOURCE_EXCHANGES", ["binance", "mexc"])
    monkeypatch.setattr(cryptodata, "trading_pairs", ["BTC/USDT"])

    def create(exchange_id):
        if exchange_id == "binance":
            raise cryptodata.ccxt.ExchangeNotAvailable("Binance returned HTTP 451")
        return mexc

    monkeypatch.setattr(cryptodata, "create_exchange", create)
    assert cryptodata.bootstrap_exchanges() == {"mexc": mexc}
    assert "BINANCE is unavailable" in capsys.readouterr().out


def test_symbol_fallback_does_not_retry_unavailable_bootstrap_exchange(monkeypatch):
    calls = []
    mexc = FakeClient()
    mexc.id = "mexc"
    mexc.symbols = ["BTC/USDT"]
    monkeypatch.setattr(cryptodata, "SOURCE_EXCHANGES", ["binance", "mexc"])
    monkeypatch.setattr(cryptodata, "get_thread_exchange", lambda exchange_id: calls.append(exchange_id) or mexc)
    monkeypatch.setattr(cryptodata, "verify_file_matches_exchange", lambda *args: (True, None))

    assert cryptodata.process_symbol(("BTC/USDT", "mexc", {"mexc"})) == ("BTC/USDT", True, None)
    assert calls == ["mexc"]


def test_naver_current_fetches_extra_page_to_reach_500_distinct_stocks(monkeypatch):
    # Five full API pages can contain fewer than 500 usable stock codes.
    ranked = [f"{index:06d}" for index in range(500)]
    calls = []

    def fake_page(market, page, page_size):
        calls.append((market, page))
        if market == "ALL":
            if page < 5:
                return ranked[page * 99:(page + 1) * 99]
            if page == 5:
                return ranked[495:]
            return []
        market_codes = ranked[::2] if market == "KOSPI" else ranked[1::2]
        return market_codes[page * page_size:(page + 1) * page_size]

    monkeypatch.setattr(cryptodata, "fetch_naver_current_market_page", fake_page)

    result = cryptodata.fetch_top_naver_current_stock_tickers(500)

    assert len(result) == 500
    assert result[0] == "000000.KS"
    assert result[1] == "000001.KQ"
    assert result[-1] == "000499.KQ"
    assert ("ALL", 5) in calls


def test_naver_current_still_rejects_short_ranked_universe(monkeypatch):
    def fake_page(market, page, page_size):
        return [f"{index:06d}" for index in range(page * 99, (page + 1) * 99)] if market == "ALL" and page < 5 else []

    monkeypatch.setattr(cryptodata, "fetch_naver_current_market_page", fake_page)

    with pytest.raises(ValueError, match="mapped only 0 of 500"):
        cryptodata.fetch_top_naver_current_stock_tickers(500)


def test_stock_universe_uses_naver_before_blocked_krx(monkeypatch, tmp_path):
    tickers = [f"{index:06d}.KS" for index in range(500)]
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE", "krx_top_market_cap")
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_REFRESH", True)
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_TOP_N", 500)
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_MIN_SYMBOLS", 500)
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_CACHE_FILE", tmp_path / "universe.json")
    monkeypatch.setattr(cryptodata, "fetch_top_naver_current_stock_tickers", lambda limit: tickers)
    monkeypatch.setattr(cryptodata, "fetch_top_krx_stock_tickers", lambda limit: pytest.fail("KRX should not be called"))

    assert cryptodata.resolve_stock_tickers() == tickers
    assert cryptodata.load_stock_universe_cache() == tickers


def test_stock_universe_tries_krx_if_naver_is_unavailable(monkeypatch, tmp_path):
    tickers = [f"{index:06d}.KQ" for index in range(500)]
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE", "krx_top_market_cap")
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_REFRESH", True)
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_TOP_N", 500)
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_MIN_SYMBOLS", 500)
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_CACHE_FILE", tmp_path / "universe.json")
    monkeypatch.setattr(cryptodata, "fetch_top_naver_current_stock_tickers", lambda limit: [])
    monkeypatch.setattr(cryptodata, "fetch_top_krx_stock_tickers", lambda limit: tickers)

    assert cryptodata.resolve_stock_tickers() == tickers


def test_naver_current_skips_unmapped_rank_and_uses_next_ranked_stock(monkeypatch):
    ranked = [f"{index:06d}" for index in range(501)]
    missing_code = ranked[250]

    def fake_page(market, page, page_size):
        if market == "ALL":
            return ranked[page * page_size:(page + 1) * page_size]
        market_codes = [code for code in ranked if code != missing_code
                        and (int(code) % 2 == 0) == (market == "KOSPI")]
        return market_codes[page * page_size:(page + 1) * page_size]

    monkeypatch.setattr(cryptodata, "fetch_naver_current_market_page", fake_page)

    result = cryptodata.fetch_top_naver_current_stock_tickers(500)

    assert len(result) == 500
    assert "000250.KS" not in result
    assert result[-1] == "000500.KS"
