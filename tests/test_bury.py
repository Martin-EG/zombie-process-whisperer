"""bury() and resurrect() with real git in tmp_path and fake docker (invariants S2, S6, S7)."""

import json
import subprocess
from pathlib import Path

import pytest

from tests.test_scan_git import commit, git, new_repo
from zombie import bury, cli, proc, scan_git, state
from zombie.models import GraveEntry, Zombie, make_id

CID = "3f4e8a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789ab"


class Crash(BaseException):
    """Simulates the process dying (power loss, SIGKILL) right after the undo entry is written."""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """main with a merged branch and a stale unmerged one; HEAD on main."""
    repo = new_repo(tmp_path / "projects" / "app")
    git(repo, "checkout", "-q", "-b", "merged-feature")
    commit(repo, "finish merged work", 150)
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge it", "merged-feature", days_ago=140)
    git(repo, "checkout", "-q", "-b", "stale-unmerged")
    commit(repo, "abandoned idea", 120)
    git(repo, "checkout", "-q", "main")
    return repo.resolve()


@pytest.fixture
def scanned(repo: Path) -> dict[str, Zombie]:
    zombies = scan_git.scan_git(repo.parent)
    state.save_last_scan(zombies)
    return {z.title: z for z in zombies}


def sha_of(repo: Path, branch: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def refs(repo: Path) -> str:
    return git(repo, "for-each-ref", "--format=%(refname) %(objectname)")


def state_files(home: Path) -> dict[str, bytes | None]:
    return {
        name: (home / name).read_bytes() if (home / name).exists() else None
        for name in (state.LAST_SCAN, state.GRAVEYARD)
    }


# ---------------------------------------------------------------- S6: plan and confirmation


def test_plan_bury_branch(scanned: dict[str, Zombie], repo: Path) -> None:
    z = scanned["merged-feature"]
    plan = bury.plan_bury(z.id)
    assert plan.args == ["git", "-C", str(repo), "branch", "-d", "merged-feature"]
    assert plan.reversible is True


def test_s6_dry_run_changes_nothing(
    scanned: dict[str, Zombie], repo: Path, zombie_home: Path
) -> None:
    z = scanned["merged-feature"]
    before_refs, before_state = refs(repo), state_files(zombie_home)
    result = bury.bury(z.id, "merged-feature", apply=False)
    assert result.applied is False
    assert result.entry is None
    assert result.plan.args[-2:] == ["-d", "merged-feature"]
    assert refs(repo) == before_refs
    assert state_files(zombie_home) == before_state
    assert before_state[state.GRAVEYARD] is None


@pytest.mark.parametrize(
    "typed",
    ["merged-feature ", " merged-feature", "Merged-feature", "MERGED-FEATURE", "merged-featur",
     "merged-feature\n", "", "merged_feature"],
)
def test_s6_typed_name_mismatch(
    scanned: dict[str, Zombie], repo: Path, zombie_home: Path, typed: str
) -> None:
    z = scanned["merged-feature"]
    before_refs, before_state = refs(repo), state_files(zombie_home)
    with pytest.raises(bury.ConfirmationMismatch):
        bury.bury(z.id, typed, apply=True)
    assert refs(repo) == before_refs
    assert state_files(zombie_home) == before_state


def test_s6_mismatch_checked_before_dry_run(scanned: dict[str, Zombie]) -> None:
    with pytest.raises(bury.ConfirmationMismatch):
        bury.bury(scanned["merged-feature"].id, "nope", apply=False)


def test_s6_unknown_id_refused(scanned: dict[str, Zombie], repo: Path) -> None:
    before = refs(repo)
    with pytest.raises(bury.NotInLastScan):
        bury.bury("0000000000", "merged-feature", apply=True)
    with pytest.raises(bury.NotInLastScan):
        bury.plan_bury("0000000000")
    assert refs(repo) == before


def test_s6_unknown_id_refused_without_a_scan() -> None:
    with pytest.raises(bury.NotInLastScan):
        bury.plan_bury("0000000000")


def report_only(kind: str, title: str, location: str, buryable: bool = False) -> Zombie:
    return Zombie(
        kind=kind, id=make_id(kind, location, title), title=title, location=location,
        age_days=None, facts={}, buryable=buryable,
        reason_not_buryable=None if buryable else "report-only",
    )


def test_s6_not_buryable_refused(scanned: dict[str, Zombie], repo: Path) -> None:
    others = [
        report_only("docker_volume", "old_data", "old_data"),
        report_only("cron_job", "/gone/backup.sh", "crontab"),
        report_only("process", "defunct", "4242"),
    ]
    state.save_last_scan([*scanned.values(), *others])
    before = refs(repo)
    stale = scanned["stale-unmerged"]
    with pytest.raises(bury.NotBuryable, match="not merged"):
        bury.bury(stale.id, stale.title, apply=True)
    for z in others:
        with pytest.raises(bury.NotBuryable):
            bury.bury(z.id, z.title, apply=True)
    assert refs(repo) == before
    assert state.load_graveyard() == []


@pytest.mark.parametrize(
    ("kind", "title", "location"),
    [("docker_volume", "v", "v"), ("cron_job", "/gone.sh", "crontab"), ("process", "p", "42")],
)
def test_s6_tampered_scan_cannot_bury_report_only_kinds(
    kind: str, title: str, location: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    z = report_only(kind, title, location, buryable=True)
    state.save_last_scan([z])
    monkeypatch.setattr(proc, "run_mutation", lambda *a, **k: pytest.fail("must not run"))
    with pytest.raises(bury.NotBuryable, match="report-only"):
        bury.bury(z.id, z.title, apply=True)


def test_s6_tampered_scan_with_hostile_branch_name_refused(repo: Path) -> None:
    z = Zombie(
        kind="git_branch", id="abcdef0123", title="--force", location=str(repo),
        age_days=1, facts={"last_commit_sha": "a" * 40}, buryable=True,
    )
    state.save_last_scan([z])
    with pytest.raises(bury.NotBuryable):
        bury.plan_bury(z.id)


# ---------------------------------------------------------------- S6: re-verify


def test_s6_reverify(scanned: dict[str, Zombie], repo: Path) -> None:
    z = scanned["merged-feature"]
    git(repo, "checkout", "-q", "merged-feature")
    commit(repo, "one more thing", 1)
    new_sha = sha_of(repo, "merged-feature")
    git(repo, "checkout", "-q", "main")
    with pytest.raises(bury.StaleScan, match="moved since the scan"):
        bury.bury(z.id, z.title, apply=True)
    assert sha_of(repo, "merged-feature") == new_sha
    assert state.load_graveyard() == []


def test_s6_reverify_new_sha_even_if_still_merged(scanned: dict[str, Zombie], repo: Path) -> None:
    z = scanned["merged-feature"]
    git(repo, "checkout", "-q", "merged-feature")
    commit(repo, "one more thing", 1)
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge again", "merged-feature")
    with pytest.raises(bury.StaleScan, match="Rescan first"):
        bury.bury(z.id, z.title, apply=True)
    assert sha_of(repo, "merged-feature") is not None


def test_s6_unmerged_after_scan(scanned: dict[str, Zombie], repo: Path) -> None:
    z = scanned["merged-feature"]
    git(repo, "reset", "-q", "--hard", "HEAD~1")  # main back to before the merge
    with pytest.raises(bury.StaleScan, match="no longer merged"):
        bury.bury(z.id, z.title, apply=True)
    assert sha_of(repo, "merged-feature") == z.facts["last_commit_sha"]
    assert state.load_graveyard() == []


def test_s6_checked_out_after_scan(scanned: dict[str, Zombie], repo: Path) -> None:
    z = scanned["merged-feature"]
    git(repo, "checkout", "-q", "merged-feature")
    with pytest.raises(bury.StaleScan, match="checked out"):
        bury.bury(z.id, z.title, apply=True)
    assert sha_of(repo, "merged-feature") is not None


def test_s6_branch_gone_after_scan(scanned: dict[str, Zombie], repo: Path) -> None:
    z = scanned["merged-feature"]
    git(repo, "branch", "-q", "-d", "merged-feature")
    with pytest.raises(bury.StaleScan, match="no longer exists"):
        bury.bury(z.id, z.title, apply=True)
    assert state.load_graveyard() == []


def test_s6_repo_gone_after_scan(scanned: dict[str, Zombie], repo: Path) -> None:
    z = scanned["merged-feature"]
    repo.rename(repo.with_name("moved"))
    with pytest.raises(bury.StaleScan, match="no longer a git repo"):
        bury.bury(z.id, z.title, apply=True)


# ---------------------------------------------------------------- S7: undo first


def test_s7_undo_written_before_delete(
    scanned: dict[str, Zombie], repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    z = scanned["merged-feature"]
    seen: list[list[GraveEntry]] = []

    def crash(args: list[str], **kwargs: object) -> None:
        proc.validate_mutation(args)
        seen.append(state.load_graveyard())  # what is on disk when git would run
        raise Crash

    monkeypatch.setattr(proc, "run_mutation", crash)
    with pytest.raises(Crash):
        bury.bury(z.id, z.title, apply=True)
    assert len(seen) == 1 and len(seen[0]) == 1
    on_disk = state.load_graveyard()
    assert on_disk == seen[0]
    entry = on_disk[0]
    assert entry.sha == z.facts["last_commit_sha"] == sha_of(repo, "merged-feature")
    assert (entry.zombie_id, entry.title, entry.location) == (z.id, z.title, str(repo))
    assert entry.reversible is True
    # The branch was never deleted, so the leftover entry cannot be resurrected over it.
    with pytest.raises(bury.BranchExists):
        bury.resurrect(z.id)


def test_s7_failed_git_removes_undo_entry(
    scanned: dict[str, Zombie], repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    z = scanned["merged-feature"]
    seen: list[int] = []

    def refuse(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        proc.validate_mutation(args)
        seen.append(len(state.load_graveyard()))
        return subprocess.CompletedProcess(args, 1, "", "error: branch is not fully merged\n")

    monkeypatch.setattr(proc, "run_mutation", refuse)
    with pytest.raises(bury.MutationFailed, match="not fully merged"):
        bury.bury(z.id, z.title, apply=True)
    assert seen == [1]
    assert state.load_graveyard() == []
    assert z.id in {x.id for x in state.load_last_scan()}
    assert sha_of(repo, "merged-feature") is not None


def test_s7_exception_with_branch_still_present_removes_entry(
    scanned: dict[str, Zombie], monkeypatch: pytest.MonkeyPatch
) -> None:
    z = scanned["merged-feature"]

    def timeout(args: list[str], **kwargs: object) -> None:
        raise proc.CommandTimeout(args, 15)

    monkeypatch.setattr(proc, "run_mutation", timeout)
    with pytest.raises(bury.MutationFailed, match="could not run"):
        bury.bury(z.id, z.title, apply=True)
    assert state.load_graveyard() == []


def test_s7_exception_after_branch_deleted_keeps_entry(
    scanned: dict[str, Zombie], repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    z = scanned["merged-feature"]
    real = proc.run_mutation

    def delete_then_time_out(args: list[str], **kwargs: object) -> None:
        real(args)
        raise proc.CommandTimeout(args, 15)

    monkeypatch.setattr(proc, "run_mutation", delete_then_time_out)
    with pytest.raises(bury.MutationFailed):
        bury.bury(z.id, z.title, apply=True)
    assert sha_of(repo, "merged-feature") is None
    assert [e.sha for e in state.load_graveyard()] == [z.facts["last_commit_sha"]]


def other_entry() -> GraveEntry:
    return GraveEntry(
        zombie_id="0123456789", kind="git_branch", title="other", location="/elsewhere",
        sha="b" * 40, buried_at="2026-10-27T00:00:00+00:00", reversible=True,
    )


def test_s7_resurrect_keeps_entries_added_meanwhile(
    scanned: dict[str, Zombie], repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Another process (CLI vs web UI) buries something while resurrect is running git.
    z = scanned["merged-feature"]
    bury.bury(z.id, z.title, apply=True)
    real = proc.run_mutation

    def concurrent_bury(args: list[str], **kwargs: object) -> object:
        state.save_graveyard([*state.load_graveyard(), other_entry()])
        return real(args, **kwargs)

    monkeypatch.setattr(proc, "run_mutation", concurrent_bury)
    bury.resurrect(z.id)
    graves = state.load_graveyard()
    assert [e.title for e in graves] == ["merged-feature", "other"]  # undo entry not lost
    assert graves[0].resurrected_at is not None


def lock_is_held() -> bool:
    """True if another open file description cannot take the state lock right now."""
    import fcntl

    path = state.zombie_home() / state.LOCK
    with open(path, "a") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return False


def test_s7_bury_and_resurrect_hold_the_state_lock(
    scanned: dict[str, Zombie], repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CLI and web UI are separate processes; graveyard read-modify-write must be serialized.
    z = scanned["merged-feature"]
    held: list[bool] = []
    real = proc.run_mutation

    def check(args: list[str], **kwargs: object) -> object:
        held.append(lock_is_held())
        return real(args, **kwargs)

    monkeypatch.setattr(proc, "run_mutation", check)
    bury.bury(z.id, z.title, apply=True)
    bury.resurrect(z.id)
    assert held == [True, True]
    assert not lock_is_held()  # released afterwards


# ---------------------------------------------------------------- round trip and resurrect


def test_bury_and_resurrect_round_trip(scanned: dict[str, Zombie], repo: Path) -> None:
    z = scanned["merged-feature"]
    original = sha_of(repo, "merged-feature")
    result = bury.bury(z.id, "merged-feature", apply=True)
    assert result.applied is True
    assert sha_of(repo, "merged-feature") is None
    assert result.entry is not None and result.entry.sha == original
    assert z.id not in {x.id for x in state.load_last_scan()}
    assert scanned["stale-unmerged"].id in {x.id for x in state.load_last_scan()}
    [entry] = state.load_graveyard()
    assert entry.sha == original and entry.resurrected_at is None

    back = bury.resurrect(z.id)
    assert back.args == ["git", "-C", str(repo), "branch", "merged-feature", original]
    assert sha_of(repo, "merged-feature") == original
    [entry] = state.load_graveyard()  # history kept, not deleted
    assert entry.sha == original and entry.resurrected_at is not None
    with pytest.raises(bury.NotInGraveyard, match="already resurrected"):
        bury.resurrect(z.id)


def test_bury_again_after_resurrect(scanned: dict[str, Zombie], repo: Path) -> None:
    z = scanned["merged-feature"]
    bury.bury(z.id, z.title, apply=True)
    bury.resurrect(z.id)
    rescanned = {x.title: x for x in scan_git.scan_git(repo.parent)}
    state.save_last_scan(list(rescanned.values()))
    assert rescanned["merged-feature"].id == z.id
    bury.bury(z.id, z.title, apply=True)
    entries = state.load_graveyard()
    assert [e.resurrected_at is None for e in entries] == [False, True]
    bury.resurrect(z.id)
    assert sha_of(repo, "merged-feature") == z.facts["last_commit_sha"]


def test_resurrect_refuses_existing_branch(scanned: dict[str, Zombie], repo: Path) -> None:
    z = scanned["merged-feature"]
    bury.bury(z.id, z.title, apply=True)
    git(repo, "branch", "merged-feature", "main")
    main_sha = sha_of(repo, "main")
    with pytest.raises(bury.BranchExists):
        bury.resurrect(z.id)
    assert sha_of(repo, "merged-feature") == main_sha
    assert state.load_graveyard()[0].resurrected_at is None


def test_resurrect_refuses_container_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    state.save_graveyard([
        GraveEntry(zombie_id="c0ffee0000", kind="docker_container", title="old", location=CID,
                   sha=None, buried_at="2026-10-27T12:00:00+00:00", reversible=False)
    ])
    monkeypatch.setattr(proc, "run_mutation", lambda *a, **k: pytest.fail("must not run"))
    with pytest.raises(bury.NotReversible):
        bury.resurrect("c0ffee0000")


def test_resurrect_unknown_id() -> None:
    with pytest.raises(bury.NotInGraveyard):
        bury.resurrect("0000000000")


# ---------------------------------------------------------------- containers (fake docker)


def container_zombie() -> Zombie:
    return Zombie(
        kind="docker_container", id=make_id("docker_container", CID, "forgotten_flask"),
        title="forgotten_flask", location=CID, age_days=56,
        facts={"image": "python:3.12-slim", "status": "Exited (0) 7 weeks ago"}, buryable=True,
    )


class FakeDocker:
    def __init__(self, status: str = "exited", rm_code: int = 0, name: str = "forgotten_flask"):
        self.status, self.rm_code, self.name = status, rm_code, name
        self.mutations: list[list[str]] = []
        self.graveyard_at_rm: list[int] = []
        self.down = False

    def read(self, args: list[str], timeout: float = 15) -> subprocess.CompletedProcess:
        proc.validate_read(args)
        assert args == ["docker", "container", "inspect", CID]
        if self.down:
            raise FileNotFoundError("docker")
        body = [{"Id": CID, "Name": f"/{self.name}", "State": {"Status": self.status}}]
        return subprocess.CompletedProcess(args, 0, json.dumps(body), "")

    def mutate(self, args: list[str], timeout: float = 15) -> subprocess.CompletedProcess:
        proc.validate_mutation(args)
        self.mutations.append(args)
        self.graveyard_at_rm.append(len(state.load_graveyard()))
        err = "" if self.rm_code == 0 else "Error response from daemon: conflict\n"
        return subprocess.CompletedProcess(args, self.rm_code, CID + "\n", err)


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    fake = FakeDocker()
    monkeypatch.setattr(proc, "run_read", fake.read)
    monkeypatch.setattr(proc, "run_mutation", fake.mutate)
    state.save_last_scan([container_zombie()])
    return fake


def test_container_plan() -> None:
    state.save_last_scan([container_zombie()])
    plan = bury.plan_bury(container_zombie().id)
    assert plan.args == ["docker", "rm", CID]
    assert plan.reversible is False


def test_container_bury_exited(docker: FakeDocker) -> None:
    z = container_zombie()
    result = bury.bury(z.id, z.title, apply=True)
    assert docker.mutations == [["docker", "rm", CID]]
    assert docker.graveyard_at_rm == [0]  # written only after a successful removal
    [entry] = state.load_graveyard()
    assert (entry.kind, entry.location, entry.sha, entry.reversible) == (
        "docker_container", CID, None, False
    )
    assert result.entry == entry
    assert state.load_last_scan() == []


def test_container_dry_run_never_calls_docker(docker: FakeDocker) -> None:
    z = container_zombie()
    bury.bury(z.id, z.title, apply=False)
    assert docker.mutations == []
    assert state.load_graveyard() == []


@pytest.mark.parametrize("status", ["running", "restarting", "paused", "created"])
def test_container_not_exited_refused(docker: FakeDocker, status: str) -> None:
    docker.status = status
    z = container_zombie()
    with pytest.raises(bury.StaleScan, match=status):
        bury.bury(z.id, z.title, apply=True)
    assert docker.mutations == []
    assert state.load_graveyard() == []


def test_container_renamed_refused(docker: FakeDocker) -> None:
    docker.name = "someone_else"
    z = container_zombie()
    with pytest.raises(bury.StaleScan, match="changed"):
        bury.bury(z.id, z.title, apply=True)
    assert docker.mutations == []


def test_container_docker_down_refused(docker: FakeDocker) -> None:
    docker.down = True
    z = container_zombie()
    with pytest.raises(bury.StaleScan, match="not installed"):
        bury.bury(z.id, z.title, apply=True)
    assert docker.mutations == []


def test_container_rm_failure_reports_stderr(docker: FakeDocker) -> None:
    docker.rm_code = 1
    z = container_zombie()
    with pytest.raises(bury.MutationFailed, match="conflict"):
        bury.bury(z.id, z.title, apply=True)
    assert state.load_graveyard() == []
    assert [x.id for x in state.load_last_scan()] == [z.id]


# ---------------------------------------------------------------- CLI


def answer(monkeypatch: pytest.MonkeyPatch, text: str, prompts: list[str]) -> None:
    def fake_input(prompt: str = "") -> str:
        prompts.append(prompt)
        return text

    monkeypatch.setattr("builtins.input", fake_input)


def test_cli_bury_dry_run(
    scanned: dict[str, Zombie], repo: Path, capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("dry run must not prompt"))
    z = scanned["merged-feature"]
    before = refs(repo)
    assert cli.main(["bury", z.id]) == 0
    out = capsys.readouterr().out
    assert f"git -C {repo} branch -d merged-feature" in out
    assert "DRY RUN" in out and "Add --apply to do it." in out
    assert refs(repo) == before


def test_cli_bury_apply_and_resurrect(
    scanned: dict[str, Zombie], repo: Path, capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    z = scanned["merged-feature"]
    prompts: list[str] = []
    answer(monkeypatch, "merged-feature", prompts)
    assert cli.main(["bury", z.id, "--apply"]) == 0
    assert prompts == ["Type the branch name to confirm: "]
    out = capsys.readouterr().out
    assert "cannot be undone" not in out
    assert f"zombie resurrect {z.id}" in out
    assert sha_of(repo, "merged-feature") is None
    assert cli.main(["resurrect", z.id]) == 0
    assert sha_of(repo, "merged-feature") == z.facts["last_commit_sha"]
    assert cli.main(["resurrect", z.id]) == 2


def test_cli_bury_wrong_name_exit_2(
    scanned: dict[str, Zombie], repo: Path, capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    z = scanned["merged-feature"]
    answer(monkeypatch, "merged-feature ", [])
    assert cli.main(["bury", z.id, "--apply"]) == 2
    assert "does not match" in capsys.readouterr().out
    assert sha_of(repo, "merged-feature") is not None


def test_cli_bury_eof_exit_2(
    scanned: dict[str, Zombie], repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def eof(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert cli.main(["bury", scanned["merged-feature"].id, "--apply"]) == 2
    assert sha_of(repo, "merged-feature") is not None


@pytest.mark.parametrize("zombie_id", ["0000000000", "stale"])
def test_cli_bury_refusals_exit_2(
    scanned: dict[str, Zombie], zombie_id: str, capsys: pytest.CaptureFixture
) -> None:
    zid = scanned["stale-unmerged"].id if zombie_id == "stale" else zombie_id
    assert cli.main(["bury", zid, "--apply"]) == 2
    out = capsys.readouterr().out
    assert "Traceback" not in out
    assert ("not merged" in out) if zombie_id == "stale" else ("not in the last scan" in out)


def test_cli_bury_stale_exit_2(
    scanned: dict[str, Zombie], repo: Path, capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    answer(monkeypatch, "merged-feature", [])
    assert cli.main(["bury", scanned["merged-feature"].id, "--apply"]) == 2
    assert "Rescan first" in capsys.readouterr().out


def test_cli_bury_mutation_failure_exit_1(
    scanned: dict[str, Zombie], capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        proc, "run_mutation",
        lambda args, **k: subprocess.CompletedProcess(args, 1, "", "error: boom\n"),
    )
    answer(monkeypatch, "merged-feature", [])
    assert cli.main(["bury", scanned["merged-feature"].id, "--apply"]) == 1
    assert "boom" in capsys.readouterr().out


def test_cli_container_warns_before_prompt(
    docker: FakeDocker, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    printed_before_prompt: list[str] = []

    def fake_input(prompt: str = "") -> str:
        printed_before_prompt.append(capsys.readouterr().out)
        assert prompt == "Type the container name to confirm: "
        return "forgotten_flask"

    monkeypatch.setattr("builtins.input", fake_input)
    assert cli.main(["bury", container_zombie().id, "--apply"]) == 0
    assert "This cannot be undone." in printed_before_prompt[0]
    assert f"docker rm {CID}" in printed_before_prompt[0]
    assert docker.mutations == [["docker", "rm", CID]]


def test_cli_resurrect_container_exit_2(capsys: pytest.CaptureFixture) -> None:
    state.save_graveyard([
        GraveEntry(zombie_id="c0ffee0000", kind="docker_container", title="old", location=CID,
                   sha=None, buried_at="2026-10-27T12:00:00+00:00", reversible=False)
    ])
    assert cli.main(["resurrect", "c0ffee0000"]) == 2
    assert "cannot be brought back" in capsys.readouterr().out


def test_cli_shows_hostile_names_escaped(
    repo: Path, capsys: pytest.CaptureFixture
) -> None:
    z = Zombie(
        kind="git_branch", id="abcdef0123", title="x\x1b[2Jy", location=str(repo),
        age_days=1, facts={}, buryable=False, reason_not_buryable="not merged",
    )
    state.save_last_scan([z])
    assert cli.main(["bury", z.id]) == 2
    out = capsys.readouterr().out
    assert "\x1b" not in out
