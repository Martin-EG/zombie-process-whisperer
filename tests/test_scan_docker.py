"""Docker scanner tests with recorded-format output and a fake runner (invariants S1 and S5)."""

import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from zombie import proc
from zombie.scan_docker import PS_ARGS, TIMEOUT, VOLUME_ARGS, scan_docker

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
HOSTILE_NAME = "<script>alert(1)</script>$(whoami)"


def completed(args: list[str], stdout: str = "", stderr: str = "", rc: int = 0):
    return subprocess.CompletedProcess(args, rc, stdout=stdout, stderr=stderr)


class FakeDocker:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], float | None]] = []

    def __call__(self, args: list[str], timeout: float | None = None):
        proc.validate_read(args)  # whatever we ask for must pass the real read allowlist
        self.calls.append((args, timeout))
        name = "docker_ps_exited.jsonl" if args == PS_ARGS else "docker_volume_dangling.jsonl"
        return completed(args, (FIXTURES / name).read_text(encoding="utf-8"))


def test_exited_containers_and_dangling_volumes() -> None:
    fake = FakeDocker()
    zombies = scan_docker(run=fake, now=NOW)
    assert [args for args, _ in fake.calls] == [PS_ARGS, VOLUME_ARGS]
    assert all(timeout == TIMEOUT for _, timeout in fake.calls)

    containers = [z for z in zombies if z.kind == "docker_container"]
    volumes = [z for z in zombies if z.kind == "docker_volume"]
    assert [c.title for c in containers] == ["forgotten_flask", HOSTILE_NAME]
    assert [v.title for v in volumes] == [
        "0d9c5e4f7a2b1c3d4e5f60718293a4b5c6d7e8f90123456789abcdef01234567",
        "old_pgdata",
    ]

    flask = containers[0]
    assert flask.location == "3f4e8a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789ab"
    assert proc.is_container_id(flask.location)
    assert flask.buryable and flask.reason_not_buryable is None
    assert flask.age_days == 55
    assert flask.facts == {
        "image": "python:3.12-slim",
        "status": "Exited (0) 7 weeks ago",
        "created": "2026-09-01 10:22:33 -0700 PDT",
    }
    for v in volumes:
        assert not v.buryable
        assert v.reason_not_buryable == "volumes are report-only"
        assert v.age_days is None


def test_s5_hostile_container_name_is_plain_data() -> None:
    fake = FakeDocker()
    hostile = next(z for z in scan_docker(run=fake, now=NOW) if z.title == HOSTILE_NAME)
    assert hostile.title == HOSTILE_NAME  # kept verbatim, not executed or rewritten
    assert len(fake.calls) == 2  # the name never reached another command


def test_bad_id_and_bad_lines_are_skipped(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        zombies = scan_docker(run=FakeDocker(), now=NOW)
    assert "bad_id_container" not in {z.title for z in zombies}
    assert "unexpected id format" in caplog.text
    assert "unparseable docker output" in caplog.text


def _one_warning(caplog: pytest.LogCaptureFixture) -> str:
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].exc_info is None
    return warnings[0].getMessage()


def test_docker_missing_returns_empty(caplog: pytest.LogCaptureFixture) -> None:
    def run(args: list[str], timeout: float | None = None):
        raise FileNotFoundError(2, "No such file or directory", "docker")

    with caplog.at_level(logging.WARNING):
        assert scan_docker(run=run, now=NOW) == []
    assert "not installed" in _one_warning(caplog)


def test_daemon_down_returns_empty(caplog: pytest.LogCaptureFixture) -> None:
    calls = []

    def run(args: list[str], timeout: float | None = None):
        calls.append(args)
        return completed(args, stderr="Cannot connect to the Docker daemon at unix:///x.sock.\n",
                         rc=1)

    with caplog.at_level(logging.WARNING):
        assert scan_docker(run=run, now=NOW) == []
    assert calls == [PS_ARGS]  # no second call once the daemon is known to be down
    assert "Cannot connect to the Docker daemon" in _one_warning(caplog)


def test_timeout_returns_empty(caplog: pytest.LogCaptureFixture) -> None:
    def run(args: list[str], timeout: float | None = None):
        raise proc.CommandTimeout(args, timeout)

    with caplog.at_level(logging.WARNING):
        assert scan_docker(run=run, now=NOW) == []
    assert "timed out after 5 s" in _one_warning(caplog)


def test_ids_stable() -> None:
    first = [z.id for z in scan_docker(run=FakeDocker(), now=NOW)]
    second = [z.id for z in scan_docker(run=FakeDocker(), now=NOW)]
    assert first == second and len(set(first)) == len(first)
