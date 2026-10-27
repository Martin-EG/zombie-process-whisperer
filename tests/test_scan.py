"""run_all tests: ordering, last_scan.json, scanner isolation, and S1."""

import functools
import json
import logging
import os
import subprocess
from pathlib import Path

import psutil
import pytest

from tests.test_scan_cron import sample_run
from tests.test_scan_docker import NOW, FakeDocker
from tests.test_scan_procs import FakeProc
from zombie import proc, scan, scan_cron, scan_docker, scan_procs, state
from zombie.models import Zombie, make_id


def z(kind: str, title: str, age: int | None) -> Zombie:
    return Zombie(kind=kind, id=make_id(kind, "x", title), title=title, location="x",
                  age_days=age, facts={}, buryable=False)


def git_repo(path: Path) -> Path:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    path.mkdir(parents=True)
    for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "one"],
                 ["branch", "done-feature"]):
        subprocess.run(["git", "-C", str(path), *args], env=env, check=True, capture_output=True)
    return path


@pytest.fixture
def fixture_scanners(monkeypatch: pytest.MonkeyPatch) -> None:
    procs = [FakeProc(200, "defunct", psutil.STATUS_ZOMBIE, ppid=1, days_old=2)]
    monkeypatch.setattr(scan, "scan_docker",
                        functools.partial(scan_docker.scan_docker, run=FakeDocker(), now=NOW))
    monkeypatch.setattr(scan, "scan_cron",
                        functools.partial(scan_cron.scan_cron, run=sample_run(),
                                          exists={"/usr/bin/env", "/var/empty"}.__contains__))
    monkeypatch.setattr(scan, "scan_procs",
                        functools.partial(scan_procs.scan_procs, iter_procs=lambda: procs,
                                          now=NOW))


def test_sort_key_orders_kind_then_age_desc() -> None:
    items = [z("process", "p", 1), z("git_branch", "a", 5), z("git_branch", "b", None),
             z("git_branch", "c", 50), z("docker_volume", "v", None), z("cron_job", "j", None),
             z("docker_container", "d", 3)]
    ordered = [i.title for i in sorted(items, key=scan.sort_key)]
    assert ordered == ["c", "a", "b", "d", "v", "j", "p"]


def test_run_all_saves_last_scan(tmp_path: Path, fixture_scanners: None,
                                 zombie_home: Path) -> None:
    git_repo(tmp_path / "projects" / "repo")
    zombies = scan.run_all(tmp_path / "projects")
    kinds = [x.kind for x in zombies]
    assert kinds == sorted(kinds, key=scan.KIND_ORDER.__getitem__)
    assert set(kinds) == set(scan.KIND_ORDER)
    saved = json.loads((zombie_home / state.LAST_SCAN).read_text(encoding="utf-8"))
    assert saved == [x.model_dump() for x in zombies]
    assert state.load_last_scan() == zombies


def test_run_all_survives_a_scanner_that_raises(
    tmp_path: Path, fixture_scanners: None, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def boom() -> list[Zombie]:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(scan, "scan_docker", boom)
    with caplog.at_level(logging.WARNING):
        zombies = scan.run_all(tmp_path)
    assert {x.kind for x in zombies} == {"cron_job", "process"}
    assert "docker scanner failed (kaboom)" in caplog.text
    assert state.load_last_scan() == zombies


def test_s1_scanners_never_call_run_mutation(
    tmp_path: Path, fixture_scanners: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail(f"run_mutation called during a scan: {args!r}")

    monkeypatch.setattr(proc, "run_mutation", forbidden)
    repo = git_repo(tmp_path / "projects" / "repo")
    before = subprocess.run(["git", "-C", str(repo), "for-each-ref"], capture_output=True,
                            text=True, check=True).stdout
    zombies = scan.run_all(tmp_path / "projects")
    after = subprocess.run(["git", "-C", str(repo), "for-each-ref"], capture_output=True,
                           text=True, check=True).stdout
    assert {x.kind for x in zombies} == set(scan.KIND_ORDER)
    assert before == after
