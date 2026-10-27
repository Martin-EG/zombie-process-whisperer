"""Web UI tests with FastAPI TestClient (invariants S5, S6, S8, S9)."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_bury import refs, sha_of, state_files
from tests.test_scan_git import HOSTILE, commit, git, new_repo
from zombie import bury, cli, proc, scan_git, state
from zombie.models import Zombie
from zombie.web import app as web_app

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
ORIGIN = {"Origin": BASE}
STATIC = Path(web_app.__file__).resolve().parent / "static"
GET_ROUTES = ["/", "/static/app.js", "/static/style.css", "/api/zombies", "/api/graveyard"]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """main with one merged branch; HEAD on main."""
    repo = new_repo(tmp_path / "projects" / "app")
    git(repo, "checkout", "-q", "-b", "merged-feature")
    commit(repo, "finish merged work", 150)
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge it", "merged-feature", days_ago=140)
    return repo.resolve()


@pytest.fixture
def scanned(repo: Path) -> dict[str, Zombie]:
    zombies = scan_git.scan_git(repo.parent)
    for z in zombies:
        z.eulogy = f"Here lies {z.title}."
    state.save_last_scan(zombies)
    return {z.title: z for z in zombies}


def make_client(root: Path, token: str = "good") -> TestClient:
    app = web_app.create_app(root, PORT, token=token, use_model=False)
    return TestClient(app, base_url=BASE, follow_redirects=False)


@pytest.fixture
def client(repo: Path) -> TestClient:
    return make_client(repo.parent)


@pytest.fixture
def authed(client: TestClient) -> TestClient:
    response = client.get("/?token=good")
    assert response.status_code == 303
    return client


# ---------------------------------------------------------------- S9: auth


def test_s9_token_exchange(client: TestClient) -> None:
    first = client.get("/?token=good")
    assert first.status_code == 303
    assert first.headers["location"] == "/"
    cookie = first.headers["set-cookie"]
    assert cookie.startswith(f"{web_app.COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie or "SameSite=Strict" in cookie
    assert "Path=/" in cookie
    session = client.cookies.get(web_app.COOKIE)
    assert session and session != "good"  # a separate value, not the token
    assert client.get("/").status_code == 200

    again = client.get("/?token=good")
    assert again.status_code == 401
    assert "set-cookie" not in again.headers
    # A fresh client (no cookie) cannot reuse the token either.
    fresh = TestClient(client.app, base_url=BASE, follow_redirects=False)
    assert fresh.get("/?token=good").status_code == 401
    assert fresh.get("/api/zombies").status_code == 401


def test_s9_wrong_token(client: TestClient) -> None:
    assert client.get("/?token=bad").status_code == 401
    assert client.get("/?token=göod").status_code == 401  # non-ASCII must not crash
    assert web_app.COOKIE not in client.cookies
    assert client.get("/api/zombies").status_code == 401
    # The real token still works after a wrong guess.
    assert client.get("/?token=good").status_code == 303


def test_s9_no_cookie(client: TestClient) -> None:
    assert client.get("/api/zombies").status_code == 401
    assert client.get("/").status_code == 401
    assert client.get("/static/app.js").status_code == 401


def test_s9_forged_cookie(authed: TestClient) -> None:
    authed.cookies.set(web_app.COOKIE, "forged")
    assert authed.get("/api/zombies").status_code == 401


def test_s9_token_post_is_not_exchanged(client: TestClient) -> None:
    assert client.post("/?token=good", headers=ORIGIN).status_code == 401
    assert client.get("/?token=good").status_code == 303


def test_s9_bad_host(authed: TestClient) -> None:
    assert authed.get("/api/zombies").status_code == 200
    for host in ("evil.example:8765", "127.0.0.1:9999", "127.0.0.1", "localhost.evil:8765"):
        assert authed.get("/api/zombies", headers={"Host": host}).status_code == 400
    assert authed.get("/api/zombies", headers={"Host": "localhost:8765"}).status_code == 200


def test_s9_bad_host_before_token(client: TestClient) -> None:
    response = client.get("/?token=good", headers={"Host": "evil.example:8765"})
    assert response.status_code == 400
    assert client.get("/?token=good").status_code == 303  # token not consumed


def test_s9_cross_origin_post(
    authed: TestClient, scanned: dict[str, Zombie], repo: Path, zombie_home: Path
) -> None:
    z = scanned["merged-feature"]
    before_refs, before_state = refs(repo), state_files(zombie_home)
    body = {"id": z.id, "typed_name": z.title, "apply": True}
    for headers in ({"Origin": "http://evil.example"}, {"Origin": "null"}, {}):
        assert authed.post("/api/bury", json=body, headers=headers).status_code == 403
    assert authed.post("/api/rescan", headers={"Origin": "http://evil.example"}).status_code == 403
    assert authed.get("/api/zombies", headers={"Origin": "http://evil.example"}).status_code == 403
    assert refs(repo) == before_refs
    assert state_files(zombie_home) == before_state
    assert sha_of(repo, "merged-feature") == z.facts["last_commit_sha"]


def test_s9_localhost_origin_allowed(authed: TestClient, scanned: dict[str, Zombie]) -> None:
    z = scanned["merged-feature"]
    body = {"id": z.id, "typed_name": z.title, "apply": False}
    headers = {"Origin": f"http://localhost:{PORT}"}
    assert authed.post("/api/bury", json=body, headers=headers).status_code == 200


def test_s9_get_never_mutates(
    authed: TestClient,
    scanned: dict[str, Zombie],
    repo: Path,
    zombie_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("a GET route tried to change something")

    monkeypatch.setattr(bury, "bury", boom)
    monkeypatch.setattr(bury, "resurrect", boom)
    monkeypatch.setattr(proc, "run_mutation", boom)
    monkeypatch.setattr(web_app, "run_all", boom)
    monkeypatch.setattr(web_app, "eulogize_all", boom)
    before_refs, before_state = refs(repo), state_files(zombie_home)
    for path in [*GET_ROUTES, "/favicon.ico"]:
        assert authed.get(path).status_code in (200, 204), path
        assert authed.head(path).status_code in (200, 204, 405), path
    assert refs(repo) == before_refs
    assert state_files(zombie_home) == before_state


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("/", "text/html"),
        ("/static/app.js", "javascript"),
        ("/static/style.css", "text/css"),
        ("/api/zombies", "application/json"),
        ("/api/graveyard", "application/json"),
    ],
)
def test_s9_security_headers(
    authed: TestClient, scanned: dict[str, Zombie], path: str, content_type: str
) -> None:
    response = authed.get(path)
    assert response.status_code == 200
    assert content_type in response.headers["content-type"]
    assert_security_headers(response.headers)


def test_s9_security_headers_on_rejections(client: TestClient) -> None:
    for response in (
        client.get("/api/zombies"),
        client.get("/", headers={"Host": "evil.example:8765"}),
        client.post("/api/bury", headers={"Origin": "http://evil.example"}),
        client.get("/?token=good"),
    ):
        assert_security_headers(response.headers)


def assert_security_headers(headers: object) -> None:
    assert headers["content-security-policy"] == "default-src 'self'"  # type: ignore[index]
    assert headers["x-content-type-options"] == "nosniff"  # type: ignore[index]
    assert headers["referrer-policy"] == "no-referrer"  # type: ignore[index]
    assert headers["cache-control"] == "no-store"  # type: ignore[index]


def test_s9_unknown_body_fields_rejected(authed: TestClient, scanned: dict[str, Zombie]) -> None:
    z = scanned["merged-feature"]
    body = {"id": z.id, "typed_name": z.title, "apply": False, "force": True}
    assert authed.post("/api/bury", json=body, headers=ORIGIN).status_code == 422
    loose = {"id": z.id, "typed_name": z.title, "apply": "yes"}
    assert authed.post("/api/bury", json=loose, headers=ORIGIN).status_code == 422
    extra = {"id": z.id, "sha": "0" * 40}
    assert authed.post("/api/resurrect", json=extra, headers=ORIGIN).status_code == 422


# ---------------------------------------------------------------- S6: bury through the API


def test_s6_bury_via_api(
    authed: TestClient, scanned: dict[str, Zombie], repo: Path, zombie_home: Path
) -> None:
    z = scanned["merged-feature"]
    sha = z.facts["last_commit_sha"]

    listing = authed.get("/api/zombies").json()
    item = next(i for i in listing["zombies"] if i["id"] == z.id)
    assert item["command"] == f"git -C {repo} branch -d merged-feature"
    assert item["reversible"] is True
    assert item["eulogy"] == "Here lies merged-feature."

    before = refs(repo), state_files(zombie_home)
    dry = authed.post(
        "/api/bury", json={"id": z.id, "typed_name": z.title, "apply": False}, headers=ORIGIN
    )
    assert dry.status_code == 200
    assert dry.json()["applied"] is False
    assert dry.json()["command"] == item["command"]
    assert "DRY RUN" in dry.json()["message"]
    assert (refs(repo), state_files(zombie_home)) == before

    wrong = authed.post(
        "/api/bury", json={"id": z.id, "typed_name": "merged-featur", "apply": True}, headers=ORIGIN
    )
    assert wrong.status_code == 409
    assert "does not match" in wrong.json()["detail"]
    assert (refs(repo), state_files(zombie_home)) == before

    missing = authed.post(
        "/api/bury", json={"id": "0000000000", "typed_name": "x", "apply": True}, headers=ORIGIN
    )
    assert missing.status_code == 409

    applied = authed.post(
        "/api/bury", json={"id": z.id, "typed_name": z.title, "apply": True}, headers=ORIGIN
    )
    assert applied.status_code == 200
    assert applied.json()["applied"] is True
    assert applied.json()["entry"]["sha"] == sha
    assert sha_of(repo, "merged-feature") is None

    graves = authed.get("/api/graveyard").json()
    assert [(g["zombie_id"], g["sha"], g["reversible"]) for g in graves] == [(z.id, sha, True)]
    assert z.id not in [i["id"] for i in authed.get("/api/zombies").json()["zombies"]]

    risen = authed.post("/api/resurrect", json={"id": z.id}, headers=ORIGIN)
    assert risen.status_code == 200
    assert sha_of(repo, "merged-feature") == sha
    assert authed.get("/api/graveyard").json()[0]["resurrected_at"]

    twice = authed.post("/api/resurrect", json={"id": z.id}, headers=ORIGIN)
    assert twice.status_code == 409


def test_s6_stale_scan_via_api(
    authed: TestClient, scanned: dict[str, Zombie], repo: Path
) -> None:
    z = scanned["merged-feature"]
    git(repo, "branch", "-D", "merged-feature")
    response = authed.post(
        "/api/bury", json={"id": z.id, "typed_name": z.title, "apply": True}, headers=ORIGIN
    )
    assert response.status_code == 409
    assert "no longer exists" in response.json()["detail"]
    assert state.load_graveyard() == []


def test_rescan_via_api(authed: TestClient, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("zombie.scan.scan_docker", lambda: [])
    monkeypatch.setattr("zombie.scan.scan_cron", lambda: [])
    monkeypatch.setattr("zombie.scan.scan_procs", lambda: [])
    assert authed.get("/api/zombies").json()["zombies"] == []
    response = authed.post("/api/rescan", headers=ORIGIN)
    assert response.status_code == 200
    data = response.json()
    assert data["model"] == "none (template eulogies)"
    titles = {i["title"]: i for i in data["zombies"]}
    assert titles["merged-feature"]["eulogy"]  # template eulogy filled in
    assert [z.title for z in state.load_last_scan()] == ["merged-feature"]
    assert state.load_last_scan()[0].eulogy == titles["merged-feature"]["eulogy"]


def test_s5_hostile_metadata_is_plain_json(authed: TestClient, repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", "hostile")
    commit(repo, HOSTILE, 100)
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge hostile", "hostile", days_ago=99)
    state.save_last_scan(scan_git.scan_git(repo.parent))
    body = authed.get("/api/zombies").json()
    item = next(i for i in body["zombies"] if i["title"] == "hostile")
    assert item["facts"]["last_commit_subject"] == HOSTILE


# ---------------------------------------------------------------- S5 and S8: static files


def test_s5_no_unsafe_dom() -> None:
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    for needle in (
        "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
        "setAttribute(\"style", ".style.", "setTimeout(\"", "setInterval(\"",
    ):
        assert needle not in source, needle


def test_s5_no_inline_scripts_or_styles() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert re.findall(r"<script\b[^>]*>", html) == ['<script src="/static/app.js">']
    assert "<style" not in html
    assert " style=" not in html
    assert not re.search(r"\son[a-z]+=", html)  # no inline event handlers


def test_s8_no_external_urls() -> None:
    for name in ("index.html", "app.js", "style.css"):
        text = (STATIC / name).read_text(encoding="utf-8")
        assert not re.search(r"https?://", text), name
        assert "@import" not in text, name


def test_s8_binds_localhost(tmp_path: Path) -> None:
    app = web_app.create_app(tmp_path, PORT, token="t")
    config = cli.ui_config(app, PORT)
    assert config.host == "127.0.0.1"
    assert config.port == PORT
    assert config.proxy_headers is False
    sock = cli.bind_socket(0)
    try:
        assert sock.getsockname()[0] == "127.0.0.1"
    finally:
        sock.close()


# ---------------------------------------------------------------- CLI


def test_cli_ui_busy_port(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    holder = cli.bind_socket(0)
    try:
        port = holder.getsockname()[1]
        assert cli.main(["ui", str(tmp_path), "--port", str(port), "--no-open"]) == 2
    finally:
        holder.close()
    out = capsys.readouterr().out
    assert f"port {port} on 127.0.0.1 is busy" in out
    assert "--port" in out
    assert not (Path(state.zombie_home()) / state.LAST_SCAN).exists()  # failed before scanning


def test_cli_ui_missing_folder(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["ui", str(tmp_path / "nope"), "--no-open"]) == 2
    assert "is not a folder" in capsys.readouterr().out


def test_cli_ui_starts_server(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    started: dict[str, object] = {}
    opened: list[str] = []

    def fake_run(self: object, sockets: list) -> None:
        started["config"] = self.config  # type: ignore[attr-defined]
        started["sockname"] = sockets[0].getsockname()
        sockets[0].close()

    monkeypatch.setattr(cli.uvicorn.Server, "run", fake_run)
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    monkeypatch.setattr("zombie.scan.scan_docker", lambda: [])
    monkeypatch.setattr("zombie.scan.scan_cron", lambda: [])
    monkeypatch.setattr("zombie.scan.scan_procs", lambda: [])

    probe = cli.bind_socket(0)
    port = probe.getsockname()[1]
    probe.close()

    assert cli.main(["ui", str(repo.parent), "--port", str(port), "--no-model"]) == 0
    config = started["config"]
    assert config.host == "127.0.0.1"  # type: ignore[attr-defined]
    assert started["sockname"] == ("127.0.0.1", port)
    assert len(opened) == 1
    assert re.fullmatch(rf"http://127\.0\.0\.1:{port}/\?token=[\w-]{{43}}", opened[0])
    out = capsys.readouterr().out
    assert opened[0] in out
    assert [z.title for z in state.load_last_scan()] == ["merged-feature"]
    assert state.load_last_scan()[0].eulogy  # scan and eulogies ran before the server


def test_cli_ui_no_open(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(cli.uvicorn.Server, "run", lambda self, sockets: sockets[0].close())
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    monkeypatch.setattr("zombie.scan.scan_docker", lambda: [])
    monkeypatch.setattr("zombie.scan.scan_cron", lambda: [])
    monkeypatch.setattr("zombie.scan.scan_procs", lambda: [])
    probe = cli.bind_socket(0)
    port = probe.getsockname()[1]
    probe.close()
    assert cli.main(["ui", str(repo.parent), "--port", str(port), "--no-model", "--no-open"]) == 0
    assert opened == []
