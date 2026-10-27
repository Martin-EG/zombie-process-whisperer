import json
from pathlib import Path

import pytest

from zombie import state
from zombie.models import GraveEntry, Zombie, make_id


def _zombie(title: str = "feature/old") -> Zombie:
    return Zombie(
        kind="git_branch",
        id=make_id("git_branch", "/tmp/repo", title),
        title=title,
        location="/tmp/repo",
        age_days=120,
        facts={"last_commit": "fix: typo", "merged_into": "main"},
        buryable=True,
    )


def _grave() -> GraveEntry:
    return GraveEntry(
        zombie_id="abcdef0123",
        kind="git_branch",
        title="feature/old",
        location="/tmp/repo",
        sha="a" * 40,
        buried_at="2026-10-27T10:00:00+00:00",
        reversible=True,
    )


def test_zombie_home_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZOMBIE_HOME")
    assert state.zombie_home() == Path("~/.local/state/zombie-whisperer").expanduser()


def test_make_id_rule() -> None:
    import hashlib

    expected = hashlib.sha256(b"git_branch|/tmp/repo|feature/old").hexdigest()[:10]
    assert make_id("git_branch", "/tmp/repo", "feature/old") == expected
    assert len(expected) == 10


def test_load_missing_files_returns_empty(zombie_home: Path) -> None:
    assert state.load_last_scan() == []
    assert state.load_graveyard() == []
    assert state.load_eulogies() == {}
    assert not zombie_home.exists()


def test_last_scan_round_trip(zombie_home: Path) -> None:
    zombies = [_zombie(), _zombie("spike/x").model_copy(update={"eulogy": "RIP"})]
    path = state.save_last_scan(zombies)
    assert path == zombie_home / "last_scan.json"
    assert state.load_last_scan() == zombies


def test_graveyard_round_trip(zombie_home: Path) -> None:
    entries = [_grave(), _grave().model_copy(update={"sha": None, "reversible": False})]
    state.save_graveyard(entries)
    assert state.load_graveyard() == entries


def test_eulogies_round_trip(zombie_home: Path) -> None:
    eulogies = {"abcdef0123": "Here lies feature/old.", "0123456789": "Gone, not forgotten."}
    state.save_eulogies(eulogies)
    assert state.load_eulogies() == eulogies


def test_atomic_save_leaves_no_temp_file(zombie_home: Path) -> None:
    state.save_last_scan([_zombie()])
    state.save_graveyard([_grave()])
    state.save_eulogies({"a": "b"})
    state.save_eulogies({"a": "c"})  # overwrite an existing file
    assert sorted(p.name for p in zombie_home.iterdir()) == [
        "eulogies.json",
        "graveyard.json",
        "last_scan.json",
    ]
    assert json.loads((zombie_home / "eulogies.json").read_text()) == {"a": "c"}


def test_atomic_save_failure_keeps_old_file_and_cleans_temp(zombie_home: Path) -> None:
    state.save_eulogies({"a": "old"})
    with pytest.raises(TypeError):
        state.save_eulogies({"a": object()})  # type: ignore[dict-item]
    assert state.load_eulogies() == {"a": "old"}
    assert [p.name for p in zombie_home.iterdir()] == ["eulogies.json"]
