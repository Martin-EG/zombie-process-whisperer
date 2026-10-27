"""Docker scanner: exited containers and dangling volumes. Read-only (invariant S1), reads only."""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from zombie import proc
from zombie.models import Zombie, make_id

log = logging.getLogger(__name__)

TIMEOUT = 5

PS_ARGS = [
    "docker", "ps", "-a", "--no-trunc", "--filter", "status=exited", "--format", "{{json .}}"
]
VOLUME_ARGS = ["docker", "volume", "ls", "--filter", "dangling=true", "--format", "{{json .}}"]

Runner = Callable[..., proc.subprocess.CompletedProcess]


class DockerUnavailable(RuntimeError):
    """Docker is missing, the daemon is down, or a call timed out."""


def _call(run: Runner, args: list[str]) -> str:
    try:
        result = run(args, timeout=TIMEOUT)
    except FileNotFoundError as exc:
        raise DockerUnavailable("docker is not installed or not on PATH") from exc
    except proc.CommandTimeout as exc:
        raise DockerUnavailable(f"`{' '.join(args[:3])}` timed out after {TIMEOUT} s") from exc
    except OSError as exc:
        raise DockerUnavailable(f"docker could not run: {exc.strerror or exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise DockerUnavailable(detail[-1] if detail else "the Docker daemon is not running")
    return result.stdout


def _json_lines(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            log.warning("Skipping unparseable docker output line.")
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _created(text: str) -> datetime | None:
    """Parse docker's CreatedAt, e.g. `2026-09-01 10:22:33 -0700 PDT`."""
    try:
        return datetime.strptime(" ".join(text.split()[:3]), "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None


def _container(row: dict, now: datetime) -> Zombie | None:
    container_id = str(row.get("ID", ""))
    if not proc.is_container_id(container_id):
        log.warning("Skipping a container with an unexpected id format.")
        return None
    name = str(row.get("Names", "")) or container_id[:12]
    created_text = str(row.get("CreatedAt", ""))
    created = _created(created_text)
    return Zombie(
        kind="docker_container",
        id=make_id("docker_container", container_id, name),
        title=name,
        location=container_id,
        age_days=max(0, (now - created).days) if created else None,
        facts={
            "image": str(row.get("Image", "")),
            "status": str(row.get("Status", "")),
            "created": created_text,
        },
        buryable=True,
    )


def _volume(row: dict) -> Zombie | None:
    name = str(row.get("Name", ""))
    if not name:
        return None
    return Zombie(
        kind="docker_volume",
        id=make_id("docker_volume", name, name),
        title=name,
        location=name,
        age_days=None,
        facts={"driver": str(row.get("Driver", ""))},
        buryable=False,
        reason_not_buryable="volumes are report-only",
    )


def scan_docker(run: Runner = proc.run_read, now: datetime | None = None) -> list[Zombie]:
    """Exited containers (buryable) and dangling volumes (report-only). [] if docker is down."""
    now = now or datetime.now(UTC)
    try:
        containers = _json_lines(_call(run, PS_ARGS))
        volumes = _json_lines(_call(run, VOLUME_ARGS))
    except DockerUnavailable as exc:
        log.warning("Skipping Docker: %s. Start Docker to include containers and volumes.", exc)
        return []
    zombies = [z for row in containers if (z := _container(row, now))]
    zombies += [z for row in volumes if (z := _volume(row))]
    return zombies
