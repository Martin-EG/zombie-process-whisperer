"""The only module that imports subprocess.

run_read() runs allowlisted executables with no shape check beyond the executable.
run_mutation() accepts exactly three command shapes (invariant S2) and refuses everything else:

    git -C <repo> branch -d <branch>
    git -C <repo> branch <branch> <sha>      (resurrect)
    docker rm <container_id>
"""

import os
import re
import subprocess

ALLOWED_EXECUTABLES = frozenset({"git", "docker", "crontab"})

_CONTAINER_ID_RE = re.compile(r"[0-9a-f]{12,64}")
_SHA_RE = re.compile(r"[0-9a-f]{40}")


class CommandRefused(ValueError):
    """A command was refused before anything ran."""


class MutationRefused(CommandRefused):
    """A mutation did not match one of the allowed shapes."""


def _check_args(args: list[str], error: type[CommandRefused]) -> None:
    if not isinstance(args, list) or not args:
        raise error("Command must be a non-empty list of arguments.")
    if not all(isinstance(a, str) for a in args):
        raise error("Every command argument must be a string.")
    if any("\x00" in a for a in args):
        raise error("Command arguments may not contain NUL bytes.")
    if args[0] not in ALLOWED_EXECUTABLES:
        allowed = ", ".join(sorted(ALLOWED_EXECUTABLES))
        raise error(f"Executable {args[0]!r} is not allowed. Allowed: {allowed}.")


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        shell=False,
        timeout=timeout,
        capture_output=True,
        text=True,
        check=False,
    )


def run_read(args: list[str], timeout: float = 15) -> subprocess.CompletedProcess:
    """Run a read-only command from the executable allowlist."""
    _check_args(args, CommandRefused)
    return _run(args, timeout)


def is_container_id(value: str) -> bool:
    return isinstance(value, str) and _CONTAINER_ID_RE.fullmatch(value) is not None


def is_sha(value: str) -> bool:
    return isinstance(value, str) and _SHA_RE.fullmatch(value) is not None


def validate_branch_name(repo: str, name: str) -> bool:
    """True if git accepts `name` as a branch name, checked with `git check-ref-format --branch`."""
    if not isinstance(name, str) or not name or name.startswith("-") or "@{" in name:
        return False
    if not _is_repo_arg(repo):
        return False
    result = run_read(["git", "-C", repo, "check-ref-format", "--branch", name])
    # --branch prints the normalized name; anything else means git rewrote or rejected it.
    return result.returncode == 0 and result.stdout.strip() == name


def _is_repo_arg(repo: str) -> bool:
    return isinstance(repo, str) and os.path.isabs(repo) and not repo.startswith("-")


def validate_mutation(args: list[str]) -> None:
    """Raise MutationRefused unless `args` is exactly one of the three allowed shapes."""
    _check_args(args, MutationRefused)

    if args[0] == "docker":
        if len(args) == 3 and args[1] == "rm" and is_container_id(args[2]):
            return
        raise MutationRefused(
            "Only `docker rm <container_id>` is allowed, with a 12-64 char lowercase hex id."
        )

    if args[0] == "git":
        if len(args) != 6 or args[1] != "-C" or args[3] != "branch":
            raise MutationRefused(
                "Only `git -C <repo> branch -d <branch>` and "
                "`git -C <repo> branch <branch> <sha>` are allowed."
            )
        repo = args[2]
        if not _is_repo_arg(repo):
            raise MutationRefused(f"Repo path {repo!r} must be an absolute path.")
        if args[4] == "-d":
            branch = args[5]
        else:
            branch, sha = args[4], args[5]
            if not is_sha(sha):
                raise MutationRefused(f"{sha!r} is not a full 40-char lowercase hex SHA.")
        if not validate_branch_name(repo, branch):
            raise MutationRefused(f"{branch!r} is not a valid branch name.")
        return

    raise MutationRefused(f"Executable {args[0]!r} may not be used for mutations.")


def run_mutation(args: list[str], timeout: float = 15) -> subprocess.CompletedProcess:
    """Run one of the three allowed mutations. Only bury.py may call this."""
    validate_mutation(args)
    return _run(args, timeout)
