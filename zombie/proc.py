"""The only module that imports subprocess.

run_read() accepts only read-only command shapes (see validate_read) and refuses everything else.
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

# Re-exported so callers can catch timeouts without importing subprocess (invariant S3).
CommandTimeout = subprocess.TimeoutExpired


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


class ReadRefused(CommandRefused):
    """A read did not match one of the allowed read-only shapes."""


# Subcommands that only read. `git branch` and `git symbolic-ref` are left out because their
# positional forms write; scanners use `for-each-ref --merged` and `rev-parse` instead.
_GIT_READ_SUBCOMMANDS = frozenset(
    {"for-each-ref", "rev-parse", "merge-base", "log", "rev-list", "show-ref", "check-ref-format"}
)
# Options that write files, run external programs, or point git at another repo.
_GIT_FORBIDDEN_PREFIXES = (
    "--output",
    "--ext-diff",
    "--textconv",
    "--filters",
    "--exec",
    "--upload-pack",
    "--receive-pack",
    "--git-dir",
    "--work-tree",
)
_DOCKER_READ_SHAPES = (
    ("ps",),
    ("inspect",),
    ("info",),
    ("version",),
    ("container", "ls"),
    ("container", "inspect"),
    ("volume", "ls"),
    ("volume", "inspect"),
)


def validate_read(args: list[str]) -> None:
    """Raise ReadRefused unless `args` is a read-only command shape.

    git:     `git --version`, or `git -C <abs repo> <read subcommand> ...` with no -c/--git-dir
             style global options and no option that writes files or runs external programs.
    docker:  `docker <ps|inspect|info|version|container ls|container inspect|volume ls|
             volume inspect> ...` with no global options before the subcommand.
    crontab: exactly `crontab -l`.
    """
    _check_args(args, ReadRefused)

    if args[0] == "crontab":
        if args == ["crontab", "-l"]:
            return
        raise ReadRefused("Only `crontab -l` is allowed for reads.")

    if args[0] == "docker":
        for shape in _DOCKER_READ_SHAPES:
            if tuple(args[1 : 1 + len(shape)]) == shape:
                return
        raise ReadRefused(f"`docker {' '.join(args[1:3])}` is not a read-only docker command.")

    if args[0] == "git":
        if args == ["git", "--version"]:
            return
        if len(args) < 4 or args[1] != "-C" or not _is_repo_arg(args[2]):
            raise ReadRefused("Git reads must look like `git -C <absolute repo> <subcommand> ...`.")
        if args[3] not in _GIT_READ_SUBCOMMANDS:
            raise ReadRefused(f"`git {args[3]}` is not an allowed read-only git subcommand.")
        for arg in args[4:]:
            if arg.startswith(_GIT_FORBIDDEN_PREFIXES):
                raise ReadRefused(f"Git option {arg!r} is not allowed in reads.")
        return

    raise ReadRefused(f"Executable {args[0]!r} may not be used for reads.")


def run_read(args: list[str], timeout: float = 15) -> subprocess.CompletedProcess:
    """Run a command that matches one of the read-only shapes in validate_read()."""
    validate_read(args)
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
