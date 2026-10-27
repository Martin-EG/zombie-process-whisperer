"""State directory and atomic JSON storage for last_scan.json, graveyard.json, eulogies.json."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from zombie.models import GraveEntry, Zombie

DEFAULT_HOME = Path("~/.local/state/zombie-whisperer")

LAST_SCAN = "last_scan.json"
GRAVEYARD = "graveyard.json"
EULOGIES = "eulogies.json"


def zombie_home() -> Path:
    """State directory: $ZOMBIE_HOME if set, else ~/.local/state/zombie-whisperer/."""
    env = os.environ.get("ZOMBIE_HOME")
    return Path(env).expanduser() if env else DEFAULT_HOME.expanduser()


def _read_json(name: str, default: Any) -> Any:
    path = zombie_home() / name
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def _atomic_write_json(name: str, data: Any) -> Path:
    """Write to a temp file in the same folder, flush, fsync, then os.replace over the target."""
    home = zombie_home()
    home.mkdir(parents=True, exist_ok=True)
    target = home / name
    fd, tmp = tempfile.mkstemp(dir=home, prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def load_last_scan() -> list[Zombie]:
    return [Zombie.model_validate(z) for z in _read_json(LAST_SCAN, [])]


def save_last_scan(zombies: list[Zombie]) -> Path:
    return _atomic_write_json(LAST_SCAN, [z.model_dump() for z in zombies])


def load_graveyard() -> list[GraveEntry]:
    return [GraveEntry.model_validate(e) for e in _read_json(GRAVEYARD, [])]


def save_graveyard(entries: list[GraveEntry]) -> Path:
    return _atomic_write_json(GRAVEYARD, [e.model_dump() for e in entries])


def load_eulogies() -> dict[str, str]:
    return dict(_read_json(EULOGIES, {}))


def save_eulogies(eulogies: dict[str, str]) -> Path:
    return _atomic_write_json(EULOGIES, eulogies)
