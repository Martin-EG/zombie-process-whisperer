"""FastAPI app with auth and security middleware, served on 127.0.0.1 only (invariants S8, S9).

The app holds no scan, eulogy, or bury logic of its own; routes call scan.run_all,
eulogy.eulogize_all, and bury.plan_bury / bury.bury / bury.resurrect.

Every request passes these checks in order:

    1. Host must be 127.0.0.1:<port> or localhost:<port>          else 400
    2. Origin, if present, must be http://127.0.0.1|localhost:<port>
       and every POST must carry one                               else 403
    3. GET / ?token=<startup token> is exchanged once for the zw_session cookie;
       every other request needs that cookie                       else 401

and every response, rejections included, carries the security headers below.
"""

import logging
import secrets
import shlex
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from starlette.responses import Response

from zombie import bury as bury_mod
from zombie import proc, state
from zombie.eulogy import eulogize_all, model_name
from zombie.models import Zombie
from zombie.report import tilde, visible, where
from zombie.scan import run_all

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
COOKIE = "zw_session"
STATIC = Path(__file__).resolve().parent / "static"

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
}

NOT_SIGNED_IN = (
    "Not signed in. Open the URL that `zombie ui` printed, or restart it to get a new one."
)


class BuryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    typed_name: str
    apply: bool


class ResurrectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str


def _equal(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode(), b.encode())


def _command(args: list[str]) -> str:
    return visible(shlex.join(args))


def _refused(exc: object, status: int = 409) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=status)


def create_app(
    root: Path, port: int, token: str | None = None, use_model: bool = True
) -> FastAPI:
    """Build the app. Does nothing slow: the CLI runs the first scan before uvicorn starts."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{h}" for h in hosts}
    auth: dict[str, str | None] = {
        "token": token or secrets.token_urlsafe(32),  # None once exchanged
        "session": None,
    }
    lock = threading.Lock()  # one rescan, bury, or resurrect at a time
    model = model_name() if use_model else "none (template eulogies)"
    scan_root = Path(root).expanduser().resolve()

    def gate(request: Request) -> Response | None:
        """Steps 1-3 of the module docstring. Returns a rejection, or None to continue."""
        if request.headers.get("host", "").lower() not in hosts:
            return PlainTextResponse("Bad Host header.", status_code=400)
        origin = request.headers.get("origin")
        if origin is not None and origin not in origins:
            return PlainTextResponse("Cross-origin request refused.", status_code=403)
        if request.method == "POST" and origin is None:
            return PlainTextResponse("POST requests must send an Origin header.", status_code=403)

        if request.url.path == "/" and "token" in request.query_params:
            if request.method not in ("GET", "HEAD"):
                return PlainTextResponse(NOT_SIGNED_IN, status_code=401)
            expected = auth["token"]
            given = request.query_params["token"]
            if expected is None or not _equal(given, expected):
                return PlainTextResponse(
                    "This link was already used or is wrong. Restart `zombie ui` for a new one.",
                    status_code=401,
                )
            auth["token"] = None
            auth["session"] = secrets.token_urlsafe(32)
            redirect = RedirectResponse("/", status_code=303)
            redirect.set_cookie(
                COOKIE, auth["session"], httponly=True, samesite="strict", path="/"
            )
            return redirect

        session = auth["session"]
        cookie = request.cookies.get(COOKIE)
        if session is None or cookie is None or not _equal(cookie, session):
            return PlainTextResponse(NOT_SIGNED_IN, status_code=401)
        return None

    @app.middleware("http")
    async def security(request: Request, call_next: Any) -> Response:
        response = gate(request)
        if response is None:
            try:
                response = await call_next(request)
            except Exception:  # noqa: BLE001 - still send the security headers
                log.exception("Request to %s failed", request.url.path)
                response = PlainTextResponse("Internal error. See the terminal.", status_code=500)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response

    def listing(zombies: list[Zombie]) -> dict[str, Any]:
        items = []
        for z in zombies:
            item: dict[str, Any] = z.model_dump()
            item["where"] = where(z, scan_root)
            item["command"] = None
            item["reversible"] = None
            if z.buryable:
                try:
                    plan = bury_mod.plan_bury(z.id)
                    item["command"] = _command(plan.args)
                    item["reversible"] = plan.reversible
                except bury_mod.BuryRefused as exc:
                    item["buryable"] = False
                    item["reason_not_buryable"] = str(exc)
            items.append(item)
        return {"root": tilde(str(root)), "model": model, "zombies": items}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/api/zombies")
    def zombies() -> dict[str, Any]:
        return listing(state.load_last_scan())

    @app.post("/api/rescan")
    def rescan() -> dict[str, Any]:
        with lock:
            found = eulogize_all(run_all(root), use_model=use_model)
            state.save_last_scan(found)
            return listing(found)

    @app.get("/api/graveyard")
    def graveyard() -> list[dict[str, Any]]:
        return [e.model_dump() for e in state.load_graveyard()]

    @app.post("/api/bury", response_model=None)
    def bury(body: BuryRequest) -> dict[str, Any] | JSONResponse:
        with lock:
            try:
                result = bury_mod.bury(body.id, body.typed_name, apply=body.apply)
            except bury_mod.BuryRefused as exc:
                return _refused(exc)
            except bury_mod.MutationFailed as exc:
                return _refused(f"{exc}. Nothing was deleted.", status=500)
        z = result.plan.zombie
        kind = bury_mod.KIND_NAMES[z.kind]
        out: dict[str, Any] = {
            "applied": result.applied,
            "command": _command(result.plan.args),
            "reversible": result.plan.reversible,
            "entry": result.entry.model_dump() if result.entry else None,
        }
        if not result.applied:
            out["message"] = "DRY RUN: nothing was changed. Uncheck Dry run to do it."
        elif result.entry and result.entry.sha:
            out["message"] = (
                f"Buried {kind} '{z.title}' at {result.entry.sha[:12]}."
                " Resurrect it from the Graveyard panel."
            )
        else:
            out["message"] = f"Buried {kind} '{z.title}'. It cannot be brought back."
        return out

    @app.post("/api/resurrect", response_model=None)
    def resurrect(body: ResurrectRequest) -> dict[str, Any] | JSONResponse:
        with lock:
            try:
                result = bury_mod.resurrect(body.id)
            except (bury_mod.BuryRefused, proc.CommandRefused) as exc:
                return _refused(exc)
            except bury_mod.MutationFailed as exc:
                return _refused(f"{exc}. Nothing was changed.", status=500)
        e = result.entry
        return {
            "command": _command(result.args),
            "entry": e.model_dump(),
            "message": f"Resurrected branch '{e.title}' at {(e.sha or '?')[:12]}.",
        }

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
