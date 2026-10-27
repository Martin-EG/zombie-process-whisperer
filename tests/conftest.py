"""Autouse fixtures: block non-local network connections, real Ollama, and isolate ZOMBIE_HOME."""

import socket
from pathlib import Path

import pytest

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})


class BlockedNetworkConnection(RuntimeError):
    """A test tried to connect to a host other than 127.0.0.1 or localhost."""


def _guard(address: object) -> None:
    # AF_UNIX addresses are paths, not network hosts.
    if isinstance(address, tuple) and address and address[0] not in ALLOWED_HOSTS:
        raise BlockedNetworkConnection(f"Tests may not connect to {address[0]!r}.")


@pytest.fixture(autouse=True)
def socket_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def connect(self: socket.socket, address: object) -> None:
        _guard(address)
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: object) -> int:
        _guard(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


@pytest.fixture(autouse=True)
def zombie_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "zombie_home"
    monkeypatch.setenv("ZOMBIE_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def no_real_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    # Port 9 (discard) has no listener, so a real client fails fast and falls back to templates.
    monkeypatch.setattr("zombie.eulogy.OLLAMA_HOST", "http://127.0.0.1:9")
