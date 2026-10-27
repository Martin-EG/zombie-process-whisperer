"""Git scanner: stale and merged local branches. Read-only (invariant S1), proc.run_read only."""

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from zombie import proc
from zombie.models import Zombie, make_id

log = logging.getLogger(__name__)

SKIP_DIRS = frozenset({"node_modules", ".venv", "venv"})

# NUL cannot appear in ref names or commit subjects, so it is a safe field separator.
_FIELDS = ("refname", "objectname", "committerdate:unix", "HEAD", "subject")
_FORMAT = "%00".join(f"%({f})" for f in _FIELDS)


class GitError(RuntimeError):
    """A git read failed in one repo."""


def find_repos(root: Path, max_depth: int = 4) -> list[Path]:
    """Folders under `root` (depth 0) up to `max_depth` that contain .git.

    Skips node_modules, .venv, venv, hidden folders and symlinks. Does not descend into a repo.
    """
    found: list[Path] = []

    def walk(folder: Path, depth: int) -> None:
        if (folder / ".git").exists():
            found.append(folder)
            return
        if depth >= max_depth:
            return
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError as exc:
            log.warning("Skipping %s: %s", folder, exc.strerror or exc)
            return
        for entry in entries:
            if entry.name.startswith(".") or entry.name in SKIP_DIRS:
                continue
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                continue
            walk(Path(entry.path), depth + 1)

    walk(Path(root).resolve(), 0)
    return found


def _git(repo: Path, *args: str) -> proc.subprocess.CompletedProcess:
    return proc.run_read(["git", "-C", str(repo), *args])


def _git_ok(repo: Path, *args: str) -> str:
    result = _git(repo, *args)
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise GitError(f"`git {args[0]}` failed: {detail[-1] if detail else 'no output'}")
    return result.stdout


def _ref_exists(repo: Path, ref: str) -> bool:
    return _git(repo, "show-ref", "--verify", "--quiet", ref).returncode == 0


def default_branch(repo: Path) -> str | None:
    """origin/HEAD's target, else `main` if it exists, else `master`, else None."""
    # for-each-ref reads the symref without `git symbolic-ref`, which is not in the read allowlist.
    result = _git(repo, "for-each-ref", "--format=%(symref)", "refs/remotes/origin/HEAD")
    target = result.stdout.strip() if result.returncode == 0 else ""
    prefix = "refs/remotes/origin/"
    if target.startswith(prefix) and len(target) > len(prefix):
        return target[len(prefix) :]
    for name in ("main", "master"):
        if _ref_exists(repo, f"refs/heads/{name}"):
            return name
    return None


def default_ref_for(repo: Path, name: str) -> str | None:
    """Full ref of the default branch: the local branch, else origin's copy, else None."""
    for ref in (f"refs/heads/{name}", f"refs/remotes/origin/{name}"):
        if _ref_exists(repo, ref):
            return ref
    return None


def _age_days(timestamp: int, now: datetime) -> int:
    then = datetime.fromtimestamp(timestamp, tz=UTC)
    return max(0, (now - then).days)


def scan_repo(repo: Path, stale_days: int, now: datetime) -> list[Zombie]:
    repo = repo.resolve()
    default = default_branch(repo)
    default_ref = default_ref_for(repo, default) if default else None

    merged: set[str] = set()
    if default_ref:
        out = _git_ok(repo, "for-each-ref", "--merged", default_ref, "--format=%(refname)",
                      "refs/heads")
        merged = set(out.splitlines())

    zombies: list[Zombie] = []
    for line in _git_ok(repo, "for-each-ref", f"--format={_FORMAT}", "refs/heads").splitlines():
        parts = line.split("\x00")
        if len(parts) != len(_FIELDS):
            log.warning("Skipping unparseable ref line in %s", repo)
            continue
        refname, sha, unix, head, subject = parts
        branch = refname.removeprefix("refs/heads/")
        if head == "*" or branch == default:
            continue
        age = _age_days(int(unix), now)
        is_merged = refname in merged
        stale = age >= stale_days

        if default_ref is None:
            if not stale:
                continue
            buryable, reason, merged_fact = False, "no default branch", "unknown"
        elif is_merged:
            buryable, reason, merged_fact = True, None, "yes"
        elif stale:
            buryable, reason, merged_fact = False, "not merged", "no"
        else:
            continue

        location = str(repo)
        zombies.append(
            Zombie(
                kind="git_branch",
                id=make_id("git_branch", location, branch),
                title=branch,
                location=location,
                age_days=age,
                facts={
                    "repo": location,
                    "branch": branch,
                    "last_commit_date": datetime.fromtimestamp(int(unix), tz=UTC)
                    .date()
                    .isoformat(),
                    "last_commit_subject": subject,
                    "last_commit_sha": sha,
                    "merged": merged_fact,
                },
                buryable=buryable,
                reason_not_buryable=reason,
            )
        )
    return zombies


def scan_git(root: Path, stale_days: int = 90, now: datetime | None = None) -> list[Zombie]:
    """Merged branches (buryable) and stale unmerged branches (not buryable) in every repo."""
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    zombies: list[Zombie] = []
    for repo in find_repos(root):
        try:
            zombies.extend(scan_repo(repo, stale_days, now))
        except (GitError, proc.CommandRefused, proc.CommandTimeout, OSError, ValueError) as exc:
            log.warning("Skipping repo %s: %s. Check it with `git -C %s status`.", repo, exc, repo)
    return zombies
