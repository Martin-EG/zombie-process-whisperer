"""Process scanner: processes in zombie state, via psutil. Read-only (invariant S1)."""

import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

import psutil

from zombie.models import Zombie, make_id

log = logging.getLogger(__name__)

UNKNOWN = "unknown"


def _safe(getter: Callable[[], Any], default: Any) -> Any:
    # Zombies often refuse name()/create_time() with ZombieProcess; that is not a reason to skip.
    try:
        return getter()
    except psutil.ZombieProcess:
        return default


def _parent_name(p: Any) -> str:
    try:
        parent = p.parent()
        return parent.name() if parent is not None else UNKNOWN
    except psutil.Error:
        return UNKNOWN


def _zombie(p: Any, now: datetime) -> Zombie | None:
    if p.status() != psutil.STATUS_ZOMBIE:
        return None
    pid = str(p.pid)
    name = _safe(p.name, "") or UNKNOWN
    created = _safe(p.create_time, None)
    age = None
    if created is not None:
        age = max(0, (now - datetime.fromtimestamp(created, tz=UTC)).days)
    return Zombie(
        kind="process",
        id=make_id("process", pid, name),
        title=name,
        location=pid,
        age_days=age,
        facts={
            "pid": pid,
            "parent_pid": str(_safe(p.ppid, UNKNOWN)),
            "parent_name": _parent_name(p),
        },
        buryable=False,
        reason_not_buryable="process actions are report-only",
    )


def scan_procs(
    iter_procs: Callable[[], Iterable[Any]] = psutil.process_iter, now: datetime | None = None
) -> list[Zombie]:
    """Processes whose status is psutil.STATUS_ZOMBIE. Report-only."""
    now = now or datetime.now(UTC)
    zombies: list[Zombie] = []
    for p in iter_procs():
        try:
            z = _zombie(p, now)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Gone, or not ours to read. ZombieProcess here means status() itself refused.
            continue
        if z is not None:
            zombies.append(z)
    return zombies
