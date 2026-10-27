"""Invariants S2 and S3: the mutation allowlist and argument validation in zombie.proc."""

import subprocess
from pathlib import Path

import pytest

from zombie import proc
from zombie.proc import CommandRefused, MutationRefused

SHA = "a" * 40
CID = "0123456789ab"


@pytest.fixture
def repo(tmp_path: Path) -> str:
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return str(path)


@pytest.fixture
def no_exec(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Replace subprocess.run in proc; record calls, never execute mutations."""
    calls: list[list[str]] = []
    real_run = subprocess.run

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        assert kwargs.get("shell") is False
        assert kwargs.get("timeout") is not None
        calls.append(list(args))
        if len(args) > 3 and args[0] == "git" and args[3] == "check-ref-format":
            return real_run(args, **kwargs)  # read-only validation is safe to run
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(proc.subprocess, "run", fake_run)
    return calls


# Allowed shapes: validated only, never executed.


def test_s2_allows_branch_delete(repo: str, no_exec: list[list[str]]) -> None:
    proc.validate_mutation(["git", "-C", repo, "branch", "-d", "feature/old"])
    assert all(c[3] == "check-ref-format" for c in no_exec)


def test_s2_allows_branch_resurrect(repo: str, no_exec: list[list[str]]) -> None:
    proc.validate_mutation(["git", "-C", repo, "branch", "feature/old", SHA])
    assert all(c[3] == "check-ref-format" for c in no_exec)


def test_s2_allows_docker_rm(no_exec: list[list[str]]) -> None:
    proc.validate_mutation(["docker", "rm", CID])
    proc.validate_mutation(["docker", "rm", "f" * 64])
    assert no_exec == []


def test_s2_run_mutation_uses_shell_false_and_timeout(no_exec: list[list[str]]) -> None:
    proc.run_mutation(["docker", "rm", CID], timeout=3)
    assert no_exec == [["docker", "rm", CID]]


# Refused shapes: run_mutation must raise before any subprocess runs.

REFUSED = [
    pytest.param(["git", "branch", "-D", "x"], id="git-branch-D"),
    pytest.param(["git", "-C", "REPO", "branch", "-D", "x"], id="git-C-branch-D"),
    pytest.param(["git", "-C", "r", "branch", "-d", "x", "--force"], id="git-d-force"),
    pytest.param(["git", "-C", "REPO", "branch", "-d", "x", "--force"], id="git-d-force-abs"),
    pytest.param(["git", "-C", "REPO", "branch", "--force", "x", SHA], id="git-force-as-name"),
    pytest.param(["git", "push"], id="git-push"),
    pytest.param(["git", "-C", "REPO", "push", "origin", "x", SHA], id="git-push-6-args"),
    pytest.param(["git", "-C", "REPO", "branch", "-d"], id="git-d-missing-name"),
    pytest.param(["git", "-C", "relative", "branch", "-d", "x"], id="git-relative-repo"),
    pytest.param(["rm", "-rf", "/"], id="rm-rf"),
    pytest.param(["/usr/bin/git", "-C", "REPO", "branch", "-d", "x"], id="git-full-path"),
    pytest.param(["docker", "rm", "-f", "abc"], id="docker-rm-f"),
    pytest.param(["docker", "rm", "-f", CID], id="docker-rm-f-valid-id"),
    pytest.param(["docker", "volume", "rm", "v"], id="docker-volume-rm"),
    pytest.param(["docker", "rm"], id="docker-rm-no-id"),
    pytest.param(["docker", "rm", CID, CID], id="docker-rm-two-ids"),
    pytest.param(["crontab", "-r"], id="crontab-r"),
    pytest.param(["crontab", "-l"], id="crontab-l"),
    pytest.param(["kill", "1"], id="kill"),
    pytest.param(["git", "-C", "REPO", "branch", "-d", "-x"], id="branch-leading-dash"),
    pytest.param(["git", "-C", "REPO", "branch", "--delete", SHA], id="branch-long-flag"),
    pytest.param(["git", "-C", "REPO", "branch", "-d", "bad..name"], id="branch-invalid-ref"),
    pytest.param(["git", "-C", "REPO", "branch", "-d", "@{-1}"], id="branch-at-brace"),
    pytest.param(["git", "-C", "REPO", "branch", "-d", "a b"], id="branch-space"),
    pytest.param(["docker", "rm", "0123456789AB"], id="cid-uppercase"),
    pytest.param(["docker", "rm", "0123456789ab;rm -rf /"], id="cid-shell-chars"),
    pytest.param(["docker", "rm", "0123456789ab\n"], id="cid-trailing-newline"),
    pytest.param(["docker", "rm", "0123456789a"], id="cid-too-short"),
    pytest.param(["git", "-C", "REPO", "branch", "x", "a" * 39], id="sha-too-short"),
    pytest.param(["git", "-C", "REPO", "branch", "x", "a" * 41], id="sha-too-long"),
    pytest.param(["git", "-C", "REPO", "branch", "x", "A" * 40], id="sha-uppercase"),
    pytest.param([], id="empty"),
    pytest.param(["git", "-C", "REPO", "branch", "-d", "x\x00y"], id="nul-byte"),
]


@pytest.mark.parametrize("args", REFUSED)
def test_s2_refuses(args: list[str], repo: str, no_exec: list[list[str]]) -> None:
    args = [repo if a == "REPO" else a for a in args]
    with pytest.raises(MutationRefused):
        proc.run_mutation(args)
    # Nothing but read-only branch-name validation may have run.
    assert all(c[3:4] == ["check-ref-format"] for c in no_exec)


def test_s2_refuses_non_list(no_exec: list[list[str]]) -> None:
    with pytest.raises(MutationRefused):
        proc.run_mutation("docker rm 0123456789ab")  # type: ignore[arg-type]
    assert no_exec == []


# Validators (S3).


def test_s3_validate_branch_name(repo: str) -> None:
    assert proc.validate_branch_name(repo, "feature/ok-1")
    assert not proc.validate_branch_name(repo, "-D")
    assert not proc.validate_branch_name(repo, "")
    assert not proc.validate_branch_name(repo, "a..b")
    assert not proc.validate_branch_name(repo, "ends.lock")
    assert not proc.validate_branch_name(repo, "@{-1}")
    assert not proc.validate_branch_name("relative", "ok")


def test_s3_container_and_sha_regex() -> None:
    assert proc.is_container_id(CID)
    assert not proc.is_container_id("abc")
    assert not proc.is_container_id(CID + "\n")
    assert proc.is_sha(SHA)
    assert not proc.is_sha(SHA[:-1])
    assert not proc.is_sha(SHA + "\n")


def test_s3_run_read_executable_allowlist(no_exec: list[list[str]]) -> None:
    for bad in (["rm", "-rf", "/"], ["kill", "1"], ["sh", "-c", "echo"], [], ["/bin/git"]):
        with pytest.raises(CommandRefused):
            proc.run_read(bad)
    assert no_exec == []
    proc.run_read(["git", "--version"], timeout=5)
    assert no_exec == [["git", "--version"]]


# Read-only shapes (S1): run_read refuses anything that could change the machine.

READ_ALLOWED = [
    pytest.param(["git", "--version"], id="git-version"),
    pytest.param(
        ["git", "-C", "/r", "for-each-ref", "--merged=main", "--format=%(refname)", "refs/heads"],
        id="git-for-each-ref",
    ),
    pytest.param(["git", "-C", "/r", "rev-parse", "--abbrev-ref", "HEAD"], id="git-rev-parse"),
    pytest.param(["git", "-C", "/r", "merge-base", "--is-ancestor", "a", "b"], id="git-merge-base"),
    pytest.param(["git", "-C", "/r", "log", "-1", "--format=%s", "x"], id="git-log"),
    pytest.param(["git", "-C", "/r", "rev-list", "--count", "a..b"], id="git-rev-list"),
    pytest.param(["git", "-C", "/r", "show-ref", "--verify", "refs/heads/x"], id="git-show-ref"),
    pytest.param(["git", "-C", "/r", "check-ref-format", "--branch", "x"], id="git-check-ref"),
    pytest.param(["docker", "ps", "-a", "--filter", "status=exited"], id="docker-ps"),
    pytest.param(["docker", "inspect", CID], id="docker-inspect"),
    pytest.param(["docker", "info"], id="docker-info"),
    pytest.param(["docker", "version"], id="docker-version"),
    pytest.param(["docker", "container", "ls", "-a"], id="docker-container-ls"),
    pytest.param(["docker", "container", "inspect", CID], id="docker-container-inspect"),
    pytest.param(["docker", "volume", "ls", "-f", "dangling=true"], id="docker-volume-ls"),
    pytest.param(["docker", "volume", "inspect", "v"], id="docker-volume-inspect"),
    pytest.param(["crontab", "-l"], id="crontab-l"),
]

READ_REFUSED = [
    pytest.param(["crontab", "-r"], id="crontab-r"),
    pytest.param(["crontab", "-e"], id="crontab-e"),
    pytest.param(["crontab", "/tmp/new_crontab"], id="crontab-install-file"),
    pytest.param(["crontab", "-l", "-u", "root"], id="crontab-l-extra"),
    pytest.param(["crontab"], id="crontab-bare"),
    pytest.param(["git", "-C", "/r", "branch", "-d", "x"], id="git-branch-d"),
    pytest.param(["git", "-C", "/r", "branch", "-D", "x"], id="git-branch-D"),
    pytest.param(["git", "-C", "/r", "branch", "new", SHA], id="git-branch-create"),
    pytest.param(["git", "-C", "/r", "branch", "--list"], id="git-branch-list"),
    pytest.param(["git", "branch", "-D", "x"], id="git-no-C"),
    pytest.param(["git", "-C", "relative", "log"], id="git-relative-repo"),
    pytest.param(["git", "-C", "/r"], id="git-no-subcommand"),
    pytest.param(["git", "-c", "core.pager=sh", "-C", "/r", "log"], id="git-c-config"),
    pytest.param(["git", "-C", "/r", "-c", "x=y", "log"], id="git-c-after-C"),
    pytest.param(["git", "--git-dir=/other", "log"], id="git-git-dir"),
    pytest.param(["git", "-C", "/r", "push"], id="git-push"),
    pytest.param(["git", "-C", "/r", "update-ref", "-d", "refs/heads/x"], id="git-update-ref"),
    pytest.param(["git", "-C", "/r", "symbolic-ref", "HEAD", "refs/heads/x"], id="git-symref"),
    pytest.param(["git", "-C", "/r", "gc"], id="git-gc"),
    pytest.param(["git", "-C", "/r", "log", "--output=/tmp/x"], id="git-log-output"),
    pytest.param(["git", "-C", "/r", "log", "-p", "--ext-diff"], id="git-log-ext-diff"),
    pytest.param(["git", "-C", "/r", "log", "--textconv"], id="git-log-textconv"),
    pytest.param(["docker", "rm", CID], id="docker-rm"),
    pytest.param(["docker", "rm", "-f", CID], id="docker-rm-f"),
    pytest.param(["docker", "container", "rm", CID], id="docker-container-rm"),
    pytest.param(["docker", "container", "prune"], id="docker-container-prune"),
    pytest.param(["docker", "volume", "rm", "v"], id="docker-volume-rm"),
    pytest.param(["docker", "volume", "prune"], id="docker-volume-prune"),
    pytest.param(["docker", "system", "prune", "-af"], id="docker-system-prune"),
    pytest.param(["docker", "kill", CID], id="docker-kill"),
    pytest.param(["docker", "-H", "tcp://10.0.0.1:2375", "ps"], id="docker-remote-host"),
    pytest.param(["docker", "--context", "remote", "ps"], id="docker-context"),
    pytest.param(["docker"], id="docker-bare"),
    pytest.param(["kill", "1"], id="kill"),
]


@pytest.mark.parametrize("args", READ_ALLOWED)
def test_s1_run_read_allows_read_shapes(args: list[str], no_exec: list[list[str]]) -> None:
    proc.run_read(args)
    assert no_exec == [args]


@pytest.mark.parametrize("args", READ_REFUSED)
def test_s1_run_read_refuses_writes(args: list[str], no_exec: list[list[str]]) -> None:
    with pytest.raises(proc.ReadRefused):
        proc.run_read(args)
    assert no_exec == []


def test_s1_read_refused_is_command_refused() -> None:
    assert issubclass(proc.ReadRefused, CommandRefused)
    assert not issubclass(proc.ReadRefused, MutationRefused)
