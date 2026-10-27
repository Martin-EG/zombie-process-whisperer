import os
import socket
from pathlib import Path

import pytest

from tests.conftest import BlockedNetworkConnection
from zombie import state


def test_socket_guard_blocks_external_host() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        with pytest.raises(BlockedNetworkConnection):
            s.connect(("1.1.1.1", 53))
        with pytest.raises(BlockedNetworkConnection):
            s.connect_ex(("1.1.1.1", 53))


def test_socket_guard_blocks_create_connection() -> None:
    with pytest.raises(BlockedNetworkConnection):
        socket.create_connection(("1.1.1.1", 443), timeout=1)


def test_socket_guard_allows_loopback_listener() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass


def test_socket_guard_allows_refused_loopback() -> None:
    # Grab a free port, close it, then connect: refused is fine, blocked is not.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        with pytest.raises(ConnectionRefusedError):
            s.connect(("127.0.0.1", port))


def test_zombie_home_is_temp(zombie_home: Path, tmp_path: Path) -> None:
    assert os.environ["ZOMBIE_HOME"] == str(zombie_home)
    assert zombie_home.is_relative_to(tmp_path)
    assert state.zombie_home() == zombie_home
