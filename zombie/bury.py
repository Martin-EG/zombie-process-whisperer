"""bury() and resurrect(). The only caller of proc.run_mutation (invariants S2, S6, S7).

Every burial needs an id from last_scan.json, the item's exact name typed back, and apply=True.
Before anything is deleted the item is re-checked against the live machine, and for a git branch
the undo entry (with its SHA) is written and fsynced to graveyard.json first.

The only argument lists this module builds for run_mutation:

    git -C <repo> branch -d <branch>     bury a git branch
    docker rm <container_id>             bury a docker container
    git -C <repo> branch <branch> <sha>  resurrect a git branch
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from zombie import proc, scan_git, state
from zombie.models import GraveEntry, Zombie

KIND_NAMES = {"git_branch": "branch", "docker_container": "container"}

DOCKER_TIMEOUT = 15


class BuryRefused(Exception):
    """Nothing was changed: the request was refused before any mutation ran."""


class NotInLastScan(BuryRefused):
    pass


class NotBuryable(BuryRefused):
    pass


class ConfirmationMismatch(BuryRefused):
    pass


class StaleScan(BuryRefused):
    pass


class NotInGraveyard(BuryRefused):
    pass


class NotReversible(BuryRefused):
    pass


class BranchExists(BuryRefused):
    pass


class MutationFailed(RuntimeError):
    """git or docker ran and reported an error."""


@dataclass(frozen=True)
class BuryPlan:
    zombie: Zombie
    args: list[str]
    reversible: bool


@dataclass(frozen=True)
class BuryResult:
    plan: BuryPlan
    applied: bool
    entry: GraveEntry | None = None


@dataclass(frozen=True)
class ResurrectResult:
    entry: GraveEntry
    args: list[str]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _stderr(result: proc.CompletedProcess) -> str:
    lines = (result.stderr or result.stdout or "").strip().splitlines()
    return lines[-1] if lines else f"exit code {result.returncode}"


# ---------------------------------------------------------------- plan


def _find(zombie_id: str) -> Zombie:
    for z in state.load_last_scan():
        if z.id == zombie_id:
            return z
    raise NotInLastScan(
        f"{zombie_id!r} is not in the last scan. Run `zombie scan` and use an id from it."
    )


def plan_bury(zombie_id: str) -> BuryPlan:
    """The exact command bury() would run for this id. Changes nothing."""
    z = _find(zombie_id)
    if not z.buryable:
        reason = z.reason_not_buryable or "it is report-only"
        raise NotBuryable(f"{z.title!r} cannot be buried: {reason}.")
    if z.kind == "git_branch":
        if not Path(z.location).is_dir():
            raise StaleScan(f"{z.location} is no longer a git repo. Rescan first.")
        args = ["git", "-C", z.location, "branch", "-d", z.title]
        reversible = True
    elif z.kind == "docker_container":
        args = ["docker", "rm", z.location]
        reversible = False
    else:
        # A tampered last_scan.json could mark any kind buryable; only two kinds have a command.
        raise NotBuryable(f"{z.title!r} cannot be buried: {z.kind} items are report-only.")
    try:
        proc.validate_mutation(args)
    except (proc.CommandRefused, proc.CommandTimeout, OSError) as exc:
        raise NotBuryable(f"{z.title!r} cannot be buried: {exc}") from exc
    return BuryPlan(zombie=z, args=args, reversible=reversible)


# ---------------------------------------------------------------- re-verify (S6)


def _git(repo: str, *args: str) -> proc.CompletedProcess:
    return proc.run_read(["git", "-C", repo, *args])


def branch_sha(repo: str, branch: str) -> str | None:
    """Current SHA of refs/heads/<branch>, or None if the branch does not exist."""
    result = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}")
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and proc.is_sha(sha) else None


def _verify_branch(z: Zombie) -> str:
    """Re-check a branch against the live repo. Returns its SHA, or raises StaleScan."""
    repo, branch = z.location, z.title
    expected = z.facts.get("last_commit_sha", "")
    if not proc.is_sha(expected):
        raise StaleScan("The last scan has no valid SHA for this branch. Rescan first.")
    if not Path(repo).is_dir() or _git(repo, "rev-parse", "--absolute-git-dir").returncode != 0:
        raise StaleScan(f"{repo} is no longer a git repo. Rescan first.")
    current = branch_sha(repo, branch)
    if current is None:
        raise StaleScan(f"Branch {branch!r} no longer exists. Rescan first.")
    if current != expected:
        raise StaleScan(
            f"Branch {branch!r} moved since the scan ({expected[:12]} -> {current[:12]}). "
            "Rescan first."
        )
    default = scan_git.default_branch(Path(repo))
    default_ref = scan_git.default_ref_for(Path(repo), default) if default else None
    if default_ref is None:
        raise StaleScan(f"{repo} has no default branch to check against. Rescan first.")
    if branch == default:
        raise StaleScan(f"{branch!r} is now the default branch. Rescan first.")
    if _git(repo, "merge-base", "--is-ancestor", current, default_ref).returncode != 0:
        raise StaleScan(f"Branch {branch!r} is no longer merged into {default}. Rescan first.")
    head = _git(repo, "rev-parse", "--symbolic-full-name", "HEAD").stdout.strip()
    if head == f"refs/heads/{branch}":
        raise StaleScan(f"Branch {branch!r} is now checked out. Rescan first.")
    return current


def _verify_container(z: Zombie) -> None:
    """Re-check a container with `docker container inspect`, or raise StaleScan."""
    args = ["docker", "container", "inspect", z.location]
    try:
        result = proc.run_read(args, timeout=DOCKER_TIMEOUT)
    except FileNotFoundError as exc:
        raise StaleScan("docker is not installed or not on PATH; nothing was removed.") from exc
    except (proc.CommandTimeout, OSError) as exc:
        raise StaleScan(f"Could not reach Docker ({exc}). Start Docker and rescan.") from exc
    if result.returncode != 0:
        raise StaleScan(f"Container check failed: {_stderr(result)}. Rescan first.")
    try:
        info = json.loads(result.stdout)[0]
        container_id = info["Id"]
        name = str(info.get("Name", "")).removeprefix("/")
        status = info["State"]["Status"]
    except (ValueError, LookupError, TypeError, AttributeError) as exc:
        raise StaleScan("Could not read `docker container inspect` output. Rescan first.") from exc
    if container_id != z.location or name != z.title:
        raise StaleScan(f"Container {z.title!r} changed since the scan. Rescan first.")
    if status != "exited":
        raise StaleScan(f"Container {z.title!r} is now {status}, not exited. Rescan first.")


# ---------------------------------------------------------------- bury


def _drop_from_last_scan(zombie_id: str) -> None:
    state.save_last_scan([z for z in state.load_last_scan() if z.id != zombie_id])


def _append_grave(entry: GraveEntry) -> None:
    state.save_graveyard([*state.load_graveyard(), entry])  # atomic write + fsync


def _remove_grave(entry: GraveEntry) -> None:
    state.save_graveyard([e for e in state.load_graveyard() if e != entry])


def _bury_branch(plan: BuryPlan) -> GraveEntry:
    z = plan.zombie
    sha = _verify_branch(z)
    entry = GraveEntry(
        zombie_id=z.id, kind=z.kind, title=z.title, location=z.location,
        sha=sha, buried_at=_now(), reversible=True,
    )
    _append_grave(entry)  # S7: undo entry is on disk before git runs
    try:
        result = proc.run_mutation(plan.args)
    except Exception as exc:
        # Roll back only if the branch is provably still there; otherwise keep the undo entry.
        if branch_sha(z.location, z.title) == sha:
            _remove_grave(entry)
        raise MutationFailed(f"git could not run: {exc}") from exc
    if result.returncode != 0:
        if branch_sha(z.location, z.title) == sha:
            _remove_grave(entry)
        raise MutationFailed(f"git refused: {_stderr(result)}")
    return entry


def _bury_container(plan: BuryPlan) -> GraveEntry:
    z = plan.zombie
    _verify_container(z)
    try:
        result = proc.run_mutation(plan.args, timeout=DOCKER_TIMEOUT)
    except Exception as exc:
        raise MutationFailed(f"docker could not run: {exc}") from exc
    if result.returncode != 0:
        raise MutationFailed(f"docker refused: {_stderr(result)}")
    entry = GraveEntry(
        zombie_id=z.id, kind=z.kind, title=z.title, location=z.location,
        sha=None, buried_at=_now(), reversible=False,
    )
    _append_grave(entry)
    return entry


def bury(zombie_id: str, typed_name: str, apply: bool) -> BuryResult:
    """Bury one zombie from the last scan. Dry run (no change) unless apply is True."""
    plan = plan_bury(zombie_id)
    if typed_name != plan.zombie.title:
        kind = KIND_NAMES.get(plan.zombie.kind, "item")
        raise ConfirmationMismatch(
            f"The typed name does not match the {kind} name exactly. Nothing was changed."
        )
    if not apply:
        return BuryResult(plan=plan, applied=False)
    with state.lock():  # the CLI and the web UI may bury at the same time
        if plan.zombie.kind == "git_branch":
            entry = _bury_branch(plan)
        else:
            entry = _bury_container(plan)
        _drop_from_last_scan(zombie_id)
    return BuryResult(plan=plan, applied=True, entry=entry)


# ---------------------------------------------------------------- resurrect


def resurrect(zombie_id: str) -> ResurrectResult:
    """Recreate a buried git branch at its recorded SHA and mark the entry resurrected."""
    with state.lock():
        return _resurrect(zombie_id)


def _resurrect(zombie_id: str) -> ResurrectResult:
    entries = state.load_graveyard()
    matches = [e for e in entries if e.zombie_id == zombie_id]
    if not matches:
        raise NotInGraveyard(
            f"{zombie_id!r} is not in the graveyard. Run `zombie graveyard` to see buried ids."
        )
    live = [e for e in matches if e.resurrected_at is None]
    if not live:
        raise NotInGraveyard(f"{zombie_id!r} was already resurrected.")
    entry = live[-1]
    if not entry.reversible or entry.kind != "git_branch":
        raise NotReversible(f"{entry.title!r} was a {entry.kind} and cannot be brought back.")
    if entry.sha is None or not proc.is_sha(entry.sha):
        raise NotReversible(f"The graveyard entry for {entry.title!r} has no valid SHA.")
    repo, branch = entry.location, entry.title
    if not Path(repo).is_dir() or _git(repo, "rev-parse", "--absolute-git-dir").returncode != 0:
        raise NotReversible(f"{repo} is no longer a git repo, so the branch cannot be restored.")
    if branch_sha(repo, branch) is not None:
        raise BranchExists(
            f"A branch named {branch!r} already exists in {repo}. Rename or delete it first."
        )
    commit = _git(repo, "rev-parse", "--verify", "--quiet", f"{entry.sha}^{{commit}}")
    if commit.returncode != 0:
        raise NotReversible(f"Commit {entry.sha[:12]} is gone from {repo} (garbage collected?).")
    args = ["git", "-C", repo, "branch", branch, entry.sha]
    try:
        result = proc.run_mutation(args)
    except Exception as exc:
        raise MutationFailed(f"git could not run: {exc}") from exc
    if result.returncode != 0:
        raise MutationFailed(f"git refused: {_stderr(result)}")
    done = entry.model_copy(update={"resurrected_at": _now()})
    # Reload: entries is from before git ran, and the file is the undo log of record.
    state.save_graveyard([done if e == entry else e for e in state.load_graveyard()])
    return ResurrectResult(entry=done, args=args)
