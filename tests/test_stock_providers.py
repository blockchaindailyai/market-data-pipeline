import builtins
import importlib.util
import sys
import time
import types
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "cryptodatav3.5.py"


def load_cryptodata_with_fake_dependencies(monkeypatch):
    original_print = builtins.print

    class FakeDataFrame:
        pass

    fake_ccxt = types.SimpleNamespace(Exchange=type("Exchange", (), {}))
    fake_pandas = types.SimpleNamespace(DataFrame=FakeDataFrame, Timestamp=type("Timestamp", (), {}))
    monkeypatch.setitem(sys.modules, "ccxt", fake_ccxt)
    monkeypatch.setitem(sys.modules, "pandas", fake_pandas)

    spec = importlib.util.spec_from_file_location("cryptodata_stock_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    builtins.print = original_print
    return module


def test_get_output_path_sanitizes_stock_suffix_without_optional_dependencies(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
    monkeypatch.setattr(cryptodata, "OUTPUT_DIR", Path("data"))
    monkeypatch.setattr(cryptodata, "STOCK_OUTPUT_DIR", Path("korean_stock_data"))

    assert cryptodata.get_output_path("005930.KS") == Path("data/005930_ks.csv")
    assert cryptodata.get_output_path("005930.KS", cryptodata.STOCK_OUTPUT_DIR) == Path("korean_stock_data/005930_ks.csv")


def test_create_stock_providers_uses_configured_priority(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
    monkeypatch.setattr(cryptodata, "STOCK_DATA_PROVIDERS", ["naver", "yahoo"])
    monkeypatch.setattr(cryptodata, "stock_tickers", ["005930.KS"])

    providers = cryptodata.create_stock_providers()

    assert [provider.id for provider in providers] == ["naver", "yahoo"]
    assert all(provider.symbols == ["005930.KS"] for provider in providers)


def test_process_stock_symbol_falls_back_after_first_provider_failure(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
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


def test_decode_http_body_uses_xml_declared_euc_kr(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)

    class FakeResponse:
        headers = {}

        def read(self):
            return '<?xml version="1.0" encoding="EUC-KR" ?><name>삼성전자</name>'.encode("euc-kr")

    assert "삼성전자" in cryptodata.decode_http_body(FakeResponse())


def test_naver_provider_adds_today_quote_during_trading_day_without_optional_dependencies(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
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


def test_korea_today_timestamp_uses_eastern_display_date(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
    korea_trading_time = cryptodata.datetime.datetime(
        2026, 7, 7, 10, 30, tzinfo=cryptodata.KOREA_TZ
    )

    timestamp_ms = cryptodata.get_tradestation_today_timestamp_ms(korea_trading_time)
    timestamp_date = cryptodata.datetime.datetime.fromtimestamp(
        timestamp_ms / 1000,
        cryptodata.datetime.UTC,
    ).date()

    assert timestamp_date == cryptodata.datetime.date(2026, 7, 6)


def test_stock_date_scheme_marker_tracks_migration_state(monkeypatch, tmp_path):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
    csv_path = tmp_path / "005930_ks.csv"
    client = types.SimpleNamespace(id="naver")

    assert not cryptodata.stock_date_scheme_is_current(csv_path, client)

    cryptodata.mark_stock_date_scheme_current(csv_path, client)

    assert cryptodata.stock_date_scheme_is_current(csv_path, client)
    assert (
        cryptodata.stock_date_scheme_marker_path(csv_path).read_text(encoding="utf-8").strip()
        == cryptodata.STOCK_DATE_SCHEME_VERSION
    )


def test_incremental_overlap_days_config_default_is_available(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)

    assert cryptodata.INCREMENTAL_OVERLAP_DAYS == 7


def test_process_stock_symbols_uses_configured_worker_pool(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
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
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)

    class FakeBootstrapClient:
        symbols = ["BTC/USDT"]
        urls = {"api": "fake-api"}

    monkeypatch.setattr(cryptodata, "SOURCE_EXCHANGES", ["fake"])
    monkeypatch.setattr(cryptodata, "stock_tickers", ["005930.KS"])
    monkeypatch.setattr(cryptodata, "FAIL_ON_VERIFICATION_ERRORS", True)
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE", "configured")
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


def test_resolve_stock_tickers_loads_top_market_cap_universe(monkeypatch, tmp_path):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE", "krx_top_market_cap")
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_REFRESH", True)
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_TOP_N", 3)
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_MIN_SYMBOLS", 3)
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_CACHE_FILE", tmp_path / "tickers.json")
    monkeypatch.setattr(cryptodata, "stock_tickers", ["005930.KS"])
    monkeypatch.setattr(cryptodata, "fetch_top_krx_stock_tickers", lambda limit: (_ for _ in ()).throw(RuntimeError("KRX unavailable")))
    monkeypatch.setattr(cryptodata, "fetch_top_naver_current_stock_tickers", lambda limit: [])
    monkeypatch.setattr(cryptodata, "fetch_top_naver_mobile_stock_tickers", lambda limit: ["005930.KS", "000660.KS", "035420.KS"][:limit])


    assert cryptodata.resolve_stock_tickers() == ["005930.KS", "000660.KS", "035420.KS"]


def test_fetch_top_korean_stock_tickers_combines_and_sorts_markets(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
    monkeypatch.setattr(cryptodata, "STOCK_MAX_WORKERS", 4)

    def fake_fetch_page(sosok, page):
        pages = {
            (0, 1): [("005930.KS", 100), ("000660.KS", 80)],
            (1, 1): [("247540.KQ", 90), ("086520.KQ", 70)],
        }
        return pages.get((sosok, page), [])

    monkeypatch.setattr(cryptodata, "fetch_naver_market_sum_page", fake_fetch_page)

    assert cryptodata.fetch_top_korean_stock_tickers(3) == ["005930.KS", "247540.KQ", "000660.KS"]


def test_build_naver_market_sum_url_uses_direct_paginated_page(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
    monkeypatch.setattr(cryptodata, "NAVER_MARKET_SUM_URL", "https://finance.naver.com/sise/sise_market_sum.naver")

    url = cryptodata.build_naver_market_sum_url(1, 7)

    assert url == "https://finance.naver.com/sise/sise_market_sum.naver?sosok=1&page=7"
    assert "field_submit" not in url


def test_fetch_top_korean_stock_tickers_keeps_distinct_pages(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
    monkeypatch.setattr(cryptodata, "STOCK_MAX_WORKERS", 4)

    def fake_fetch_page(sosok, page):
        return [(f"{sosok}{page:05d}.{'KS' if sosok == 0 else 'KQ'}", (10 - page) * 100 + sosok)]

    monkeypatch.setattr(cryptodata, "fetch_naver_market_sum_page", fake_fetch_page)

    assert len(cryptodata.fetch_top_korean_stock_tickers(60)) == 4

def test_parse_naver_market_sum_rows_uses_market_cap_column_not_last_cell(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
    row = (
        '<tr>'
        '<td>1</td>'
        '<td><a href="/item/main.naver?code=005930">Samsung</a></td>'
        '<td>70,000</td>'
        '<td>100</td>'
        '<td>0.14%</td>'
        '<td>100</td>'
        '<td>4,178,849</td>'
        '<td>5,969,782</td>'
        '<td><a href="/item/board.naver?code=005930">토론실</a></td>'
        '</tr>'
    )

    assert cryptodata.parse_naver_market_sum_rows(row, 0) == [("005930.KS", 4178849)]


def test_fetch_naver_market_sum_page_falls_back_to_field_submit_post(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
    calls = []

    def fake_fetch_html(sosok, page, use_field_submit=False):
        calls.append(use_field_submit)
        if not use_field_submit:
            return "<html></html>"
        return '<tr><td></td><td><a href="/item/main.naver?code=005930">Samsung</a></td><td>1</td><td>2</td><td>3</td><td>4</td><td>1,000</td><td>ignored</td></tr>'

    monkeypatch.setattr(cryptodata, "fetch_naver_market_sum_html", fake_fetch_html)

    assert cryptodata.fetch_naver_market_sum_page(0, 2) == [("005930.KS", 1000)]
    assert calls == [False, True]


def test_resolve_stock_tickers_uses_fallback_when_universe_is_empty(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE", "krx_top_market_cap")
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_REFRESH", True)
    monkeypatch.setattr(cryptodata, "stock_tickers", ["005930.KS", "000660.KS"])
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_MIN_SYMBOLS", 2)
    monkeypatch.setattr(cryptodata, "fetch_top_krx_stock_tickers", lambda limit: [])
    monkeypatch.setattr(cryptodata, "fetch_top_naver_mobile_stock_tickers", lambda limit: [])
    monkeypatch.setattr(cryptodata, "fetch_top_korean_stock_tickers", lambda limit: [])
    monkeypatch.setattr(cryptodata, "load_stock_universe_cache", lambda: [])

    assert cryptodata.resolve_stock_tickers() == ["005930.KS", "000660.KS"]


def test_resolve_stock_tickers_refuses_tiny_configured_fallback(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE", "krx_top_market_cap")
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_REFRESH", True)
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_MIN_SYMBOLS", 500)
    monkeypatch.setattr(cryptodata, "stock_tickers", ["005930.KS", "000660.KS"])
    monkeypatch.setattr(cryptodata, "fetch_top_krx_stock_tickers", lambda limit: [])
    monkeypatch.setattr(cryptodata, "fetch_top_naver_mobile_stock_tickers", lambda limit: [])
    monkeypatch.setattr(cryptodata, "fetch_top_korean_stock_tickers", lambda limit: [])
    monkeypatch.setattr(cryptodata, "load_stock_universe_cache", lambda: [])

    try:
        cryptodata.resolve_stock_tickers()
    except RuntimeError as exc:
        assert "Refusing to silently scrape a tiny fallback list" in str(exc)
    else:
        raise AssertionError("Expected resolve_stock_tickers to reject tiny fallback list")


def test_resolve_stock_tickers_writes_refreshed_universe_cache(monkeypatch, tmp_path):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
    cache_file = tmp_path / "korean_stock_tickers.json"
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE", "krx_top_market_cap")
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_REFRESH", True)
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_CACHE_FILE", cache_file)
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_MIN_SYMBOLS", 2)
    monkeypatch.setattr(cryptodata, "fetch_top_krx_stock_tickers", lambda limit: [])
    monkeypatch.setattr(cryptodata, "fetch_top_naver_mobile_stock_tickers", lambda limit: ["005930.KS", "000660.KS"])

    assert cryptodata.resolve_stock_tickers() == ["005930.KS", "000660.KS"]
    assert "005930.KS" in cache_file.read_text(encoding="utf-8")

def test_parse_krx_market_cap_symbols_maps_market_suffixes(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
    rows = [
        {"ISU_SRT_CD": "005930", "MKT_NM": "KOSPI", "MKTCAP": "10,000"},
        {"ISU_SRT_CD": "247540", "MKT_NM": "KOSDAQ", "MKTCAP": "9,000"},
        {"ISU_SRT_CD": "123456", "MKT_NM": "KONEX", "MKTCAP": "8,000"},
    ]

    assert cryptodata.parse_krx_market_cap_symbols(rows, 2) == ["005930.KS", "247540.KQ"]


def test_resolve_stock_tickers_prefers_krx_universe(monkeypatch, tmp_path):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE", "krx_top_market_cap")
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_REFRESH", True)
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_MIN_SYMBOLS", 2)
    monkeypatch.setattr(cryptodata, "STOCK_UNIVERSE_CACHE_FILE", tmp_path / "tickers.json")
    monkeypatch.setattr(cryptodata, "fetch_top_krx_stock_tickers", lambda limit: ["005930.KS", "247540.KQ"])
    monkeypatch.setattr(cryptodata, "fetch_top_korean_stock_tickers", lambda limit: (_ for _ in ()).throw(AssertionError("Naver should not be used")))

    assert cryptodata.resolve_stock_tickers() == ["005930.KS", "247540.KQ"]


def test_stock_provider_rate_limit_can_be_disabled_for_parallel_stock_fetches(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
    monkeypatch.setattr(cryptodata, "STOCK_REQUEST_DELAY_SECONDS", 0)

    assert cryptodata.get_api_call_delay(cryptodata.NaverDailyOHLCVProvider(["005930.KS"])) == 0


def test_naver_mobile_market_page_parses_codes_and_caps(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)

    class FakeResponse:
        headers = {"Content-Type": "application/json"}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"stocks":[{"itemCode":"005930","marketValue":"4,178,849"},{"itemCode":"000660","marketValue":"1,000,000"}]}'

    def fake_urlopen(request, timeout):
        assert "sortType=marketValue" in request.full_url
        assert "category=KOSPI" in request.full_url
        return FakeResponse()

    monkeypatch.setattr(cryptodata, "urlopen", fake_urlopen)
    assert cryptodata.fetch_naver_mobile_market_sum_page("KOSPI", 1) == [
        ("005930.KS", 4178849), ("000660.KS", 1000000)
    ]


def test_naver_mobile_universe_merges_both_markets(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
    monkeypatch.setattr(cryptodata, "fetch_naver_mobile_market_sum_page",
                        lambda category, page: [("005930.KS", 100)] if category == "KOSPI"
                        else [("247540.KQ", 90)])
    assert cryptodata.fetch_top_naver_mobile_stock_tickers(2) == [
        "005930.KS", "247540.KQ"
    ]


def test_naver_current_market_page_parses_current_list(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)

    class FakeResponse:
        headers = {"Content-Type": "application/json"}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"content":[{"itemcode":"005930"},{"itemCode":"000660"}]}'

    def fake_urlopen(request, timeout):
        assert "stock.naver.com/api/domestic/market/stock/default" in request.full_url
        assert "orderType=marketSum" in request.full_url
        return FakeResponse()

    monkeypatch.setattr(cryptodata, "urlopen", fake_urlopen)
    assert cryptodata.fetch_naver_current_market_page("KOSPI", 0) == ["005930", "000660"]


def test_naver_current_universe_keeps_global_rank_and_market_suffix(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)

    def fake_page(market, page, page_size=100):
        return {
            ("ALL", 0): ["005930", "247540", "000660"],
            ("KOSPI", 0): ["005930", "000660"],
            ("KOSDAQ", 0): ["247540"],
        }[(market, page)]

    monkeypatch.setattr(cryptodata, "fetch_naver_current_market_page", fake_page)
    assert cryptodata.fetch_top_naver_current_stock_tickers(3) == [
        "005930.KS", "247540.KQ", "000660.KS"
    ]
