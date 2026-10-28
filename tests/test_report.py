"""Terminal report, graveyard.md, and the scan/graveyard CLI commands (invariants S1 and S5)."""

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from zombie import cli, report, scan, state
from zombie.models import GraveEntry, Zombie, make_id

HOME = "/Users/ghost"
NOW = datetime(2026, 10, 27, 12, 0)
UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")


def zombie(kind: str, title: str, location: str = "x", **overrides: Any) -> Zombie:
    fields: dict[str, Any] = {
        "kind": kind,
        "id": make_id(kind, location, title),
        "title": title,
        "location": location,
        "age_days": 12,
        "facts": {},
        "buryable": True,
        "eulogy": f"Here lies {title}.",
    }
    fields.update(overrides)
    return Zombie(**fields)


def sample() -> list[Zombie]:
    return [
        zombie("git_branch", "[red]x[/red]", f"{HOME}/projects/app"),
        zombie("git_branch", "a|b\nc", f"{HOME}/projects/app", age_days=None,
               buryable=False, reason_not_buryable="not merged into main"),
        zombie("docker_container", "old-redis", "b" * 64, eulogy="Ran redis from /Users/ghost/x"),
        zombie("cron_job", f"{HOME}/bin/backup.sh", "crontab", age_days=None),
        zombie("cron_job", "/opt/gone.sh", "crontab", age_days=None, eulogy="Line one\nline two"),
    ]


def record(width: int = 100) -> Console:
    return Console(record=True, width=width, color_system=None, force_terminal=False)


def test_s5_report_renders_markup_literally() -> None:
    console = record()
    report.print_report(sample(), console)
    text = console.export_text()
    assert "[red]x[/red]" in text
    assert "a|b\\nc" in text  # newline shown as text, the row is not split
    assert {"not", "merged", "into", "main"} <= set(text.split())  # reason may wrap


def test_s5_report_markup_in_eulogy_and_reason_is_literal() -> None:
    z = zombie("git_branch", "b", "/r", buryable=False, reason_not_buryable="[bold]nope[/]",
               eulogy="[link=http://x]click[/link]")
    console = record()
    report.print_report([z], console)
    text = console.export_text()
    assert "[bold]nope[/]" in text
    assert "[link=http://x]click[/link]" in text


def test_s5_control_characters_are_made_visible() -> None:
    z = zombie("process", "evil\x1b[2Jname", "42")
    console = record()
    report.print_report([z], console)
    text = console.export_text()
    assert "\x1b" not in text
    assert "evil\\x1b[2Jname" in text


def test_report_groups_by_kind_and_shows_buryable() -> None:
    zombies = sample()
    console = record()
    report.print_report(list(reversed(zombies)), console)
    text = console.export_text()
    assert text.index("branch") < text.index("container") < text.index("cron")
    assert "yes" in text
    for header in ("ID", "KIND", "NAME", "AGE", "BURYABLE", "EULOGY"):
        assert header in text


def test_report_shows_where_each_zombie_lives() -> None:
    zombies = [
        *sample(),
        zombie("git_branch", "old", f"{HOME}/elsewhere/lib"),
        zombie("docker_volume", "pgdata", "pgdata"),
        zombie("process", "railway", "4242"),
    ]
    console = record(width=140)
    report.print_report(zombies, console, root=Path(f"{HOME}/projects"), home=HOME)
    text = console.export_text()
    assert "app" in text.split()  # repo relative to the scan root
    assert f"{HOME}/elsewhere/lib" not in text  # outside the root: shown with ~
    assert "~/elsewhere/lib" in text
    assert "bbbbbbbbbbbb" in text and "b" * 13 not in text  # short container id
    assert "4242" in text


def test_where_labels() -> None:
    root = Path(f"{HOME}/projects")
    assert report.where(zombie("git_branch", "b", f"{HOME}/projects/a/api"), root, HOME) == "a/api"
    assert report.where(zombie("git_branch", "b", f"{HOME}/projects"), root, HOME) == "projects"
    assert report.where(zombie("git_branch", "b", "/srv/x"), root, HOME) == "/srv/x"
    assert report.where(zombie("git_branch", "b", f"{HOME}/x"), None, HOME) == "~/x"
    assert report.where(zombie("docker_container", "c", "f" * 64), root, HOME) == "f" * 12
    assert report.where(zombie("docker_volume", "v", "v"), root, HOME) == "docker"
    assert report.where(zombie("cron_job", "/x.sh", "crontab"), root, HOME) == "crontab"
    assert report.where(zombie("process", "p", "42"), root, HOME) == "pid 42"


def test_report_fits_100_columns_with_long_eulogy() -> None:
    long = "Here lies a very long eulogy " * 12
    zombies = [*sample(), zombie("process", "p" * 80, "1", eulogy=long)]
    console = record(width=100)
    report.print_report(zombies, console)
    lines = console.export_text().splitlines()
    assert max(len(line) for line in lines) <= 100
    assert " ".join(lines).split().count("eulogy") == 12  # wrapped, nothing cut off


def test_empty_report_message() -> None:
    console = record()
    report.print_report([], console)
    assert console.export_text().strip() == report.EMPTY_MESSAGE


def tables(md: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in md.splitlines():
        if line.startswith("|"):
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def test_graveyard_md_tables_are_valid(tmp_path: Path) -> None:
    out = report.write_graveyard_md(sample(), tmp_path / "g.md", root=tmp_path / "projects",
                                    now=NOW, home=HOME)
    md = out.read_text()
    blocks = tables(md)
    assert len(blocks) == 4  # counts, git, docker, cron
    for block in blocks:
        widths = {len(UNESCAPED_PIPE.findall(line)) for line in block}
        assert len(widths) == 1, block
        assert re.fullmatch(r"\|( -+:? \|)+", block[1]), block[1]
    assert "a\\|b c" in md
    assert "Line one line two" in md


def test_graveyard_md_title_counts_and_sections(tmp_path: Path) -> None:
    md = report.render_graveyard_md(sample(), tmp_path / "projects", now=NOW, home=HOME)
    assert md.splitlines()[0] == "# Graveyard of projects, 2026-10-27"
    assert "| Git branches | 2 |" in md
    assert "| Docker containers | 1 |" in md
    assert "| Docker volumes | 0 |" in md
    assert "| Cron jobs | 2 |" in md
    assert "| Zombie processes | 0 |" in md
    assert "| **Total** | **5** |" in md
    assert "## Git branches" in md and "## Cron jobs" in md
    assert "## Docker volumes" not in md
    assert "| 12 days |" in md and "| unknown |" in md


def test_graveyard_md_replaces_home_with_tilde(tmp_path: Path) -> None:
    md = report.render_graveyard_md(sample(), tmp_path, now=NOW, home=HOME)
    assert HOME not in md
    assert "(~/projects/app)" in md  # repo folder kept
    assert "~/bin/backup.sh" in md
    assert "Ran redis from ~/x" in md


def test_tilde_only_replaces_whole_path_prefix() -> None:
    assert report.tilde("/Users/ghost/x", HOME) == "~/x"
    assert report.tilde("/Users/ghost", HOME) == "~"
    assert report.tilde("/Users/ghostly/x", HOME) == "/Users/ghostly/x"


def test_s5_graveyard_md_escapes_html_and_backslashes(tmp_path: Path) -> None:
    z = zombie("git_branch", "<script>alert(1)</script>\\", "/r")
    md = report.render_graveyard_md([z], tmp_path, now=NOW, home=HOME)
    assert "<script>" not in md
    assert "&lt;script&gt;alert(1)&lt;/script&gt;\\\\ (/r)" in md


def test_empty_graveyard_md(tmp_path: Path) -> None:
    out = report.write_graveyard_md([], tmp_path / "g.md", root=tmp_path, now=NOW, home=HOME)
    md = out.read_text()
    assert report.EMPTY_MESSAGE in md
    assert "| **Total** | **0** |" in md
    assert "##" not in md


@pytest.fixture
def no_scanners(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("scan_docker", "scan_cron", "scan_procs"):
        monkeypatch.setattr(scan, name, lambda: [])
    monkeypatch.setattr(scan, "scan_git", lambda root, stale_days=90: [])


def test_cli_empty_scan_prints_message_and_writes_file(
    tmp_path: Path, no_scanners: None, capsys: pytest.CaptureFixture
) -> None:
    out = tmp_path / "graveyard.md"
    assert cli.main(["scan", str(tmp_path), "--no-model", "--out", str(out)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == report.EMPTY_MESSAGE
    assert lines[-1] == f"0 zombies found, 0 buryable. Report saved to {report.tilde(str(out))}."
    assert out.read_text().startswith(f"# Graveyard of {tmp_path.name}, ")


def test_cli_scan_passes_stale_days(
    tmp_path: Path, no_scanners: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []
    monkeypatch.setattr(scan, "scan_git", lambda root, stale_days=90: seen.append(stale_days) or [])
    out = str(tmp_path / "g.md")
    assert cli.main(["scan", str(tmp_path), "--no-model", "--out", out, "--stale-days", "7"]) == 0
    assert seen == [7]


def test_cli_scan_rejects_negative_stale_days(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        cli.main(["scan", str(tmp_path), "--stale-days", "-1"])


def test_cli_scan_missing_out_folder(
    tmp_path: Path, no_scanners: None, capsys: pytest.CaptureFixture
) -> None:
    out = tmp_path / "nope" / "g.md"
    assert cli.main(["scan", str(tmp_path), "--no-model", "--out", str(out)]) == 2
    assert "does not exist" in capsys.readouterr().out


def test_cli_graveyard_lists_entries(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLUMNS", "120")
    state.save_graveyard([
        GraveEntry(zombie_id="abcdef0123", kind="git_branch", title="[red]old[/red]",
                   location="/repos/app", sha="a" * 40, buried_at="2026-10-27T12:00:00+00:00",
                   reversible=True),
        GraveEntry(zombie_id="0123456789", kind="docker_container", title="old-redis",
                   location="b" * 12, sha=None, buried_at="2026-10-26T08:00:00+00:00",
                   reversible=False),
    ])
    assert cli.main(["graveyard"]) == 0
    out = capsys.readouterr().out
    for text in ("abcdef0123", "[red]old[/red]", "/repos/app", "2026-10-27 12:00",
                 "0123456789", "old-redis", "yes", "no"):
        assert text in out


def test_cli_graveyard_empty(capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["graveyard"]) == 0
    assert capsys.readouterr().out.strip() == report.EMPTY_GRAVEYARD
