"""Terminal table and graveyard.md. Read-only apart from the report file itself (invariant S1).

All metadata is untrusted (S5): terminal cells go through rich.markup.escape after control
characters are made visible, and markdown cells have pipes, backslashes, angle brackets and
newlines escaped so a name can never break the table or inject markup.
"""

import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import get_args

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from zombie.models import GraveEntry, Zombie, ZombieKind

KINDS: tuple[str, ...] = get_args(ZombieKind)

KIND_LABELS = {
    "git_branch": "Git branches",
    "docker_container": "Docker containers",
    "docker_volume": "Docker volumes",
    "cron_job": "Cron jobs",
    "process": "Zombie processes",
}

KIND_SHORT = {
    "git_branch": "branch",
    "docker_container": "container",
    "docker_volume": "volume",
    "cron_job": "cron",
    "process": "process",
}

EMPTY_MESSAGE = "No zombies found. Your machine is suspiciously healthy."
EMPTY_GRAVEYARD = "The graveyard is empty. Nothing has been buried yet."

_VISIBLE_CONTROLS = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def visible(text: str) -> str:
    """Show newlines and other control characters as escape text so they cannot move the cursor."""
    out = []
    for ch in text:
        if ch in _VISIBLE_CONTROLS:
            out.append(_VISIBLE_CONTROLS[ch])
        elif unicodedata.category(ch) == "Cc":
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(ch)
    return "".join(out)


def cell(text: str) -> str:
    """Terminal-safe rich markup for an untrusted string."""
    return escape(visible(text))


def tilde(text: str, home: str | None = None) -> str:
    """Replace the home folder with ~ wherever it appears as a whole path prefix."""
    home = (home if home is not None else str(Path.home())).rstrip("/")
    if not home:
        return text
    return re.sub(re.escape(home) + r"(?=/|$|[^\w.-])", "~", text)


def format_age(age_days: int | None) -> str:
    return "?" if age_days is None else f"{age_days}d"


def format_buried_at(buried_at: str) -> str:
    """ISO 8601 shortened to minutes for the terminal; anything unparseable is shown as is."""
    try:
        return datetime.fromisoformat(buried_at).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return buried_at


def build_table(zombies: list[Zombie]) -> Table:
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False)
    table.add_column("ID", no_wrap=True, style="cyan", min_width=10)
    table.add_column("KIND", no_wrap=True)
    table.add_column("NAME", overflow="fold", min_width=10, max_width=32)
    table.add_column("AGE", no_wrap=True, justify="right")
    table.add_column("BURYABLE", overflow="fold", min_width=8, max_width=16)
    table.add_column("EULOGY", overflow="fold", min_width=16)

    ordered = sorted(zombies, key=lambda z: KINDS.index(z.kind))
    for i, z in enumerate(ordered):
        last_of_kind = i + 1 == len(ordered) or ordered[i + 1].kind != z.kind
        buryable = "yes" if z.buryable else f"[dim]{cell(z.reason_not_buryable or 'no')}[/dim]"
        table.add_row(
            cell(z.id),
            KIND_SHORT[z.kind],
            cell(z.title),
            format_age(z.age_days),
            buryable,
            cell(z.eulogy or ""),
            end_section=last_of_kind,
        )
    return table


def print_report(zombies: list[Zombie], console: Console | None = None) -> None:
    """Print the zombie table grouped by kind, or a short message when there are none."""
    console = console or Console()
    if not zombies:
        console.print(EMPTY_MESSAGE, highlight=False)
        return
    console.print(build_table(zombies))


def print_graveyard(
    entries: list[GraveEntry], console: Console | None = None, home: str | None = None
) -> None:
    """Print buried items from graveyard.json."""
    console = console or Console()
    if not entries:
        console.print(EMPTY_GRAVEYARD, highlight=False)
        return
    table = Table(box=box.SIMPLE_HEAD, expand=True, pad_edge=False)
    table.add_column("ID", no_wrap=True, style="cyan", min_width=10)
    table.add_column("TITLE", overflow="fold", ratio=2, min_width=8)
    table.add_column("LOCATION", overflow="fold", ratio=3, min_width=10)
    table.add_column("BURIED AT", no_wrap=True)
    table.add_column("REVERSIBLE", no_wrap=True)
    for e in entries:
        table.add_row(
            cell(e.zombie_id),
            cell(e.title),
            cell(tilde(e.location, home)),
            cell(format_buried_at(e.buried_at)),
            "[green]resurrected[/green]" if e.resurrected_at
            else "yes" if e.reversible else "[dim]no[/dim]",
        )
    console.print(table)


def md_cell(text: str, home: str | None = None) -> str:
    """One markdown table cell: home folder as ~, no newlines, pipes and HTML escaped."""
    text = tilde(text, home)
    text = re.sub(r"\r\n|\r|\n", " ", text)
    text = "".join(" " if unicodedata.category(ch) == "Cc" else ch for ch in text)
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    return text.replace("<", "&lt;").replace(">", "&gt;").strip()


def md_name(z: Zombie) -> str:
    if z.kind == "git_branch":
        return f"{z.title} ({z.location})"
    return z.title


def render_graveyard_md(
    zombies: list[Zombie],
    root: Path,
    now: datetime | None = None,
    home: str | None = None,
) -> str:
    now = now or datetime.now()
    counts = Counter(z.kind for z in zombies)
    lines = [
        f"# Graveyard of {md_cell(Path(root).resolve().name or str(root), home)},"
        f" {now.date().isoformat()}",
        "",
    ]
    if not zombies:
        lines += [EMPTY_MESSAGE, ""]
    lines += ["| Kind | Count |", "| --- | ---: |"]
    lines += [f"| {KIND_LABELS[kind]} | {counts.get(kind, 0)} |" for kind in KINDS]
    lines += [f"| **Total** | **{len(zombies)}** |", ""]
    for kind in KINDS:
        group = [z for z in zombies if z.kind == kind]
        if not group:
            continue
        lines += [f"## {KIND_LABELS[kind]}", "", "| Name | Age | Eulogy |", "| --- | ---: | --- |"]
        for z in group:
            age = "unknown" if z.age_days is None else f"{z.age_days} days"
            lines.append(
                f"| {md_cell(md_name(z), home)} | {age} | {md_cell(z.eulogy or '', home)} |"
            )
        lines.append("")
    return "\n".join(lines)


def write_graveyard_md(
    zombies: list[Zombie],
    path: Path,
    root: Path = Path("."),
    now: datetime | None = None,
    home: str | None = None,
) -> Path:
    """Write the markdown report to path and return it."""
    path = Path(path)
    path.write_text(render_graveyard_md(zombies, root, now, home), encoding="utf-8")
    return path
