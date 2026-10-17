"""Keep tests offline: individual provider tests supply fake responses."""
import socket
import pytest


@pytest.fixture(autouse=True)
def deny_live_network(monkeypatch):
    def deny(*args, **kwargs):
        raise OSError("Live network disabled in unit tests; mock the provider")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
