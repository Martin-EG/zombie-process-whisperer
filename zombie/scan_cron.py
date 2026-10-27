"""Cron scanner: crontab entries whose script path is missing. Read-only (invariant S1)."""

import logging
import os
import re
import shlex
from collections.abc import Callable

from zombie import proc
from zombie.models import Zombie, make_id

log = logging.getLogger(__name__)

TITLE_MAX = 80

_ENV_LINE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\s*=")

Runner = Callable[..., proc.subprocess.CompletedProcess]


def parse_line(line: str) -> tuple[str, str] | None:
    """(schedule, command) for a job line, or None for blanks, comments and VAR=value lines."""
    line = line.strip()
    if not line or line.startswith("#") or _ENV_LINE_RE.match(line):
        return None
    if line.startswith("@"):
        parts = line.split(None, 1)
        return (parts[0], parts[1].strip()) if len(parts) == 2 else None
    parts = line.split(None, 5)
    if len(parts) != 6:
        return None
    return " ".join(parts[:5]), parts[5].strip()


def first_path(command: str) -> str | None:
    """First token that is an absolute path or starts with ~/, with ~ expanded."""
    try:
        tokens = shlex.split(command, comments=False)
    except ValueError:
        tokens = command.split()
    for token in tokens:
        if token.startswith("/") or token.startswith("~/"):
            return os.path.expanduser(token)
    return None


def _read_crontab(run: Runner) -> str | None:
    try:
        result = run(["crontab", "-l"])
    except FileNotFoundError:
        log.warning("Skipping cron: `crontab` is not installed.")
        return None
    except (proc.CommandTimeout, OSError) as exc:
        log.warning("Skipping cron: `crontab -l` failed (%s). Try running it yourself.", exc)
        return None
    if result.returncode != 0:
        if "no crontab" in (result.stderr or "").lower():
            return None
        detail = (result.stderr or "").strip().splitlines()
        reason = detail[-1] if detail else f"exit code {result.returncode}"
        log.warning("Skipping cron: `crontab -l` failed (%s). Try running it yourself.", reason)
        return None
    return result.stdout


def scan_cron(
    run: Runner = proc.run_read, exists: Callable[[str], bool] = os.path.exists
) -> list[Zombie]:
    """Crontab entries whose first path no longer exists. Report-only."""
    text = _read_crontab(run)
    if text is None:
        return []
    zombies: list[Zombie] = []
    for line in text.splitlines():
        parsed = parse_line(line)
        if parsed is None:
            continue
        schedule, command = parsed
        path = first_path(command)
        if path is None or exists(path):
            continue
        title = command[:TITLE_MAX]
        zombies.append(
            Zombie(
                kind="cron_job",
                id=make_id("cron_job", "crontab", title),
                title=title,
                location="crontab",
                age_days=None,
                facts={"schedule": schedule, "missing_path": path},
                buryable=False,
                reason_not_buryable="cron edits are report-only",
            )
        )
    return zombies
