"""Cron scanner tests with a recorded crontab and fake runner and exists()."""

import os
import subprocess
from pathlib import Path

import pytest

from zombie import proc
from zombie.scan_cron import first_path, parse_line, scan_cron

FIXTURES = Path(__file__).parent / "fixtures"
EXISTING = {"/usr/bin/env", "/var/empty"}


def runner(stdout: str = "", stderr: str = "", rc: int = 0):
    def run(args: list[str], timeout: float | None = None):
        proc.validate_read(args)
        assert args == ["crontab", "-l"]
        return subprocess.CompletedProcess(args, rc, stdout=stdout, stderr=stderr)

    return run


def sample_run():
    return runner((FIXTURES / "crontab_sample.txt").read_text(encoding="utf-8"))


def test_flags_only_entries_with_missing_paths() -> None:
    zombies = scan_cron(run=sample_run(), exists=EXISTING.__contains__)
    by_path = {z.facts["missing_path"]: z for z in zombies}
    assert set(by_path) == {
        "/opt/ghost/backup.sh",
        os.path.expanduser("~/bin/start-tunnel.sh"),
    }
    backup = by_path["/opt/ghost/backup.sh"]
    assert backup.kind == "cron_job"
    assert backup.location == "crontab"
    assert backup.title == "/opt/ghost/backup.sh --quiet >> /tmp/backup.log 2>&1"
    assert backup.facts["schedule"] == "*/15 * * * *"
    assert not backup.buryable
    assert backup.reason_not_buryable == "cron edits are report-only"
    assert by_path[os.path.expanduser("~/bin/start-tunnel.sh")].facts["schedule"] == "@reboot"


def test_no_crontab_returns_empty() -> None:
    run = runner(stderr="crontab: no crontab for someone\n", rc=1)
    assert scan_cron(run=run, exists=lambda p: False) == []


def test_crontab_missing_or_failing_returns_empty() -> None:
    def missing(args: list[str], timeout: float | None = None):
        raise FileNotFoundError(2, "No such file or directory", "crontab")

    assert scan_cron(run=missing, exists=lambda p: False) == []
    assert scan_cron(run=runner(stderr="crontab: permission denied\n", rc=1)) == []


def test_title_trimmed_to_80_chars() -> None:
    line = "0 * * * * /gone/script.sh " + "x" * 200 + "\n"
    [z] = scan_cron(run=runner(line), exists=lambda p: False)
    assert len(z.title) == 80
    assert z.facts["missing_path"] == "/gone/script.sh"


@pytest.mark.parametrize(
    "line",
    ["", "   ", "# comment", "  # indented", "SHELL=/bin/bash", "PATH = /usr/bin", "@hourly",
     "* * * *"],
)
def test_parse_line_skips_non_jobs(line: str) -> None:
    assert parse_line(line) is None


def test_parse_line_schedules() -> None:
    assert parse_line("@daily /x.sh") == ("@daily", "/x.sh")
    assert parse_line("5 4 * * sun  /x.sh  arg") == ("5 4 * * sun", "/x.sh  arg")


def test_first_path() -> None:
    assert first_path("echo hi") is None
    assert first_path("cd /a && ./b") == "/a"
    assert first_path('python3 "/srv/old reports/x.py"') == "/srv/old reports/x.py"
    assert first_path("~/bin/x") == os.path.expanduser("~/bin/x")
    assert first_path("echo 'unterminated /p") == "/p"
