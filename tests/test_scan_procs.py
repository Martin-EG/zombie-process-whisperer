"""Process scanner tests with fake psutil process objects."""

from datetime import UTC, datetime, timedelta

import psutil

from zombie.scan_procs import scan_procs

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


class FakeProc:
    def __init__(
        self,
        pid: int,
        name: str,
        status: str = psutil.STATUS_SLEEPING,
        ppid: int = 1,
        parent: "FakeProc | None" = None,
        days_old: int = 3,
        raises: Exception | None = None,
        name_raises: Exception | None = None,
    ) -> None:
        self.pid = pid
        self._name = name
        self._status = status
        self._ppid = ppid
        self._parent = parent
        self._created = (NOW - timedelta(days=days_old)).timestamp()
        self._raises = raises
        self._name_raises = name_raises

    def status(self) -> str:
        if self._raises:
            raise self._raises
        return self._status

    def name(self) -> str:
        if self._name_raises:
            raise self._name_raises
        return self._name

    def ppid(self) -> int:
        return self._ppid

    def parent(self) -> "FakeProc | None":
        return self._parent

    def create_time(self) -> float:
        return self._created


def test_finds_only_zombies_and_skips_errors() -> None:
    shell = FakeProc(100, "zsh")
    procs = [
        shell,
        FakeProc(200, "defunct-worker", psutil.STATUS_ZOMBIE, ppid=100, parent=shell, days_old=4),
        FakeProc(300, "gone", raises=psutil.NoSuchProcess(300)),
        FakeProc(400, "secret", raises=psutil.AccessDenied(400)),
        FakeProc(500, "orphan", psutil.STATUS_ZOMBIE, ppid=999, parent=None),
    ]
    zombies = scan_procs(iter_procs=lambda: iter(procs), now=NOW)
    assert [z.title for z in zombies] == ["defunct-worker", "orphan"]
    worker = zombies[0]
    assert worker.kind == "process"
    assert worker.location == "200"
    assert worker.age_days == 4
    assert worker.facts == {"pid": "200", "parent_pid": "100", "parent_name": "zsh"}
    assert not worker.buryable
    assert worker.reason_not_buryable == "process actions are report-only"
    assert zombies[1].facts["parent_name"] == "unknown"


def test_zombie_that_refuses_name_is_still_reported() -> None:
    p = FakeProc(600, "x", psutil.STATUS_ZOMBIE, name_raises=psutil.ZombieProcess(600))
    [z] = scan_procs(iter_procs=lambda: [p], now=NOW)
    assert z.title == "unknown"
    assert z.location == "600"


def test_real_process_table_does_not_crash() -> None:
    assert isinstance(scan_procs(), list)
