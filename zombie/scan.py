"""Runs every scanner and saves last_scan.json. Read-only outside the state dir (invariant S1)."""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import get_args

from zombie import state
from zombie.models import Zombie, ZombieKind
from zombie.scan_cron import scan_cron
from zombie.scan_docker import scan_docker
from zombie.scan_git import scan_git
from zombie.scan_procs import scan_procs

log = logging.getLogger(__name__)

KIND_ORDER = {kind: i for i, kind in enumerate(get_args(ZombieKind))}


def sort_key(z: Zombie) -> tuple[int, bool, int]:
    """Kind in model order, then age_days descending, unknown ages last."""
    return KIND_ORDER[z.kind], z.age_days is None, -(z.age_days or 0)


def run_all(root: Path) -> list[Zombie]:
    """Run every scanner, sort, save last_scan.json, and return the list.

    A scanner that raises is logged and skipped so the others still report.
    """
    # Looked up at call time so tests can monkeypatch the module-level names.
    scanners: list[tuple[str, Callable[[], list[Zombie]]]] = [
        ("git", lambda: scan_git(Path(root))),
        ("docker", lambda: scan_docker()),
        ("cron", lambda: scan_cron()),
        ("processes", lambda: scan_procs()),
    ]
    zombies: list[Zombie] = []
    for name, scanner in scanners:
        try:
            zombies.extend(scanner())
        except Exception as exc:  # noqa: BLE001 - one broken scanner must not stop the rest
            log.warning("The %s scanner failed (%s). Other results are still shown.", name, exc)
    zombies.sort(key=sort_key)
    state.save_last_scan(zombies)
    return zombies
