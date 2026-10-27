"""Pydantic models shared by every module: Zombie (a found item) and GraveEntry (undo log)."""

import hashlib
from typing import Literal

from pydantic import BaseModel

ZombieKind = Literal["git_branch", "docker_container", "docker_volume", "cron_job", "process"]


def make_id(kind: str, location: str, title: str) -> str:
    """Stable id: first 10 hex chars of sha256(f"{kind}|{location}|{title}")."""
    return hashlib.sha256(f"{kind}|{location}|{title}".encode()).hexdigest()[:10]


class Zombie(BaseModel):
    kind: ZombieKind
    id: str
    title: str
    location: str
    age_days: int | None
    facts: dict[str, str]
    buryable: bool
    reason_not_buryable: str | None = None
    eulogy: str | None = None


class GraveEntry(BaseModel):
    zombie_id: str
    kind: str
    title: str
    location: str
    sha: str | None
    buried_at: str
    reversible: bool
