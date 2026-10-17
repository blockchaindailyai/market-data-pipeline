import builtins
import importlib.util
import io
import sys
import types
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "cryptodatav3.5.py"


class MisreportedUtf8Cp1252Stream:
    encoding = "utf-8"
    errors = "strict"

    def __init__(self):
        self.buffer = io.BytesIO()
        self.flushed = False

    def write(self, text):
        self.buffer.write(text.encode("cp1252", errors="strict"))

    def flush(self):
        self.flushed = True

    def value(self):
        return self.buffer.getvalue().decode("cp1252")


def load_cryptodata_with_fake_dependencies(monkeypatch, restore_print=True):
    original_print = builtins.print
    fake_ccxt = types.SimpleNamespace(Exchange=type("Exchange", (), {}))
    fake_pandas = types.SimpleNamespace(DataFrame=type("DataFrame", (), {}), Timestamp=type("Timestamp", (), {}))
    monkeypatch.setitem(sys.modules, "ccxt", fake_ccxt)
    monkeypatch.setitem(sys.modules, "pandas", fake_pandas)

    spec = importlib.util.spec_from_file_location("cryptodata_unicode_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if restore_print:
        builtins.print = original_print
    return module


def test_safe_print_escapes_unicode_for_cp1252_stream(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252", errors="strict", newline="")

    cryptodata.safe_print("币安人生/USDT: using remembered MEXC source.", file=stream)
    stream.flush()

    assert buffer.getvalue().decode("cp1252") == (
        "\\u5e01\\u5b89\\u4eba\\u751f/USDT: using remembered MEXC source.\n"
    )


def test_safe_print_escapes_unicode_separator_and_ending(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252", errors="strict", newline="")

    cryptodata.safe_print("left", "right", sep="币", end="人生", file=stream)
    stream.flush()

    assert buffer.getvalue().decode("cp1252") == "left\\u5e01right\\u4eba\\u751f"


def test_safe_print_falls_back_when_stream_encoding_is_wrong(monkeypatch):
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch)
    stream = MisreportedUtf8Cp1252Stream()

    cryptodata.safe_print("币安人生/USDT", file=stream, flush=True)

    assert stream.value() == "\\u5e01\\u5b89\\u4eba\\u751f/USDT\n"
    assert stream.flushed is True


def test_module_replaces_builtin_print_with_safe_print(monkeypatch):
    original_print = builtins.print
    cryptodata = load_cryptodata_with_fake_dependencies(monkeypatch, restore_print=False)
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252", errors="strict", newline="")

    try:
        builtins.print("币安人生/USDT", file=stream)
        stream.flush()
    finally:
        builtins.print = original_print

    assert builtins.print is original_print
    assert cryptodata.safe_print is not original_print
    assert buffer.getvalue().decode("cp1252") == "\\u5e01\\u5b89\\u4eba\\u751f/USDT\n"
