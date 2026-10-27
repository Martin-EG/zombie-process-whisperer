"""argparse entry point for the `zombie` command."""

import argparse
import logging
import secrets
import shlex
import socket
import webbrowser
from pathlib import Path

import uvicorn

from zombie import bury as bury_mod
from zombie import proc, state
from zombie.eulogy import eulogize_all
from zombie.report import print_graveyard, print_report, tilde, visible, write_graveyard_md
from zombie.scan import run_all
from zombie.web import app as web_app


def non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError(f"{value} is negative; pass 0 or more days.")
    return number


def port_number(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError(f"{value} is not a port; pass 1-65535.")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zombie",
        description="Find abandoned branches, containers, cron jobs and zombie processes.",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    scan = sub.add_parser("scan", help="read-only scan and report")
    scan.add_argument("root", nargs="?", default=".", help="folder to search for git repos")
    scan.add_argument(
        "--no-model", action="store_true", help="template eulogies only; do not call Ollama"
    )
    scan.add_argument(
        "--out", default="graveyard.md", help="markdown report path (default graveyard.md)"
    )
    scan.add_argument(
        "--stale-days",
        type=non_negative_int,
        default=90,
        metavar="N",
        help="unmerged branches idle this many days count as stale (default 90)",
    )

    bury = sub.add_parser("bury", help="delete a safe zombie (dry run unless --apply)")
    bury.add_argument("id", help="zombie id from the last scan")
    bury.add_argument("--apply", action="store_true", help="actually delete it")

    resurrect = sub.add_parser("resurrect", help="restore a buried git branch")
    resurrect.add_argument("id", help="zombie id from the graveyard")

    sub.add_parser("graveyard", help="list buried zombies")

    ui = sub.add_parser("ui", help="local web UI on 127.0.0.1")
    ui.add_argument("root", nargs="?", default=".", help="folder to search for git repos")
    ui.add_argument(
        "--port", type=port_number, default=8765, help="port on 127.0.0.1 (default 8765)"
    )
    ui.add_argument(
        "--no-model", action="store_true", help="template eulogies only; do not call Ollama"
    )
    ui.add_argument("--no-open", action="store_true", help="print the URL; do not open a browser")

    return parser


def _scan(root: str, out: str, use_model: bool = True, stale_days: int = 90) -> int:
    path = Path(root).expanduser()
    if not path.is_dir():
        print(f"zombie scan: {root} is not a folder. Pass a folder that contains git repos.")
        return 2
    out_path = Path(out).expanduser()
    if not out_path.parent.is_dir():
        print(f"zombie scan: {out_path.parent} does not exist. Create it or pick another --out.")
        return 2
    zombies = eulogize_all(run_all(path, stale_days=stale_days), use_model=use_model)
    state.save_last_scan(zombies)
    print_report(zombies)
    write_graveyard_md(zombies, out_path, root=path)
    buryable = sum(z.buryable for z in zombies)
    print(
        f"{len(zombies)} zombies found, {buryable} buryable."
        f" Report saved to {tilde(str(out_path))}."
    )
    return 0


def _graveyard() -> int:
    print_graveyard(state.load_graveyard())
    return 0


def _command(args: list[str]) -> str:
    return visible(shlex.join(args))


def _bury(zombie_id: str, apply: bool) -> int:
    try:
        plan = bury_mod.plan_bury(zombie_id)
    except bury_mod.BuryRefused as exc:
        print(f"zombie bury: {visible(str(exc))}")
        return 2
    z = plan.zombie
    kind = bury_mod.KIND_NAMES[z.kind]
    print(f"Plan: bury {kind} '{visible(z.title)}' ({visible(tilde(z.location))})")
    print(f"  {_command(plan.args)}")
    if plan.reversible:
        print(f"  Reversible: yes, with `zombie resurrect {z.id}`")
    else:
        print("  Reversible: no")
    if not apply:
        print("DRY RUN: nothing was changed. Add --apply to do it.")
        return 0
    if not plan.reversible:
        print("This cannot be undone.")
    try:
        typed = input(f"Type the {kind} name to confirm: ")
    except EOFError:
        typed = None
    if typed is None:
        print("zombie bury: no name typed. Nothing was changed.")
        return 2
    try:
        result = bury_mod.bury(zombie_id, typed, apply=True)
    except bury_mod.BuryRefused as exc:
        print(f"zombie bury: {visible(str(exc))}")
        return 2
    except bury_mod.MutationFailed as exc:
        print(f"zombie bury: {visible(str(exc))}. Nothing was deleted.")
        return 1
    entry = result.entry
    if entry is not None and entry.sha:
        print(f"Buried {kind} '{visible(z.title)}' at {entry.sha[:12]}.")
        print(f"Undo with `zombie resurrect {z.id}`.")
    else:
        print(f"Buried {kind} '{visible(z.title)}'. It cannot be brought back.")
    return 0


def _resurrect(zombie_id: str) -> int:
    try:
        result = bury_mod.resurrect(zombie_id)
    except (bury_mod.BuryRefused, proc.CommandRefused) as exc:
        print(f"zombie resurrect: {visible(str(exc))}")
        return 2
    except bury_mod.MutationFailed as exc:
        print(f"zombie resurrect: {visible(str(exc))}. Nothing was changed.")
        return 1
    e = result.entry
    print(f"  {_command(result.args)}")
    print(
        f"Resurrected branch '{visible(e.title)}' at {e.sha[:12] if e.sha else '?'}"
        f" in {visible(tilde(e.location))}."
    )
    return 0


def bind_socket(port: int) -> socket.socket:
    """Listen on 127.0.0.1:<port> before the scan, so a busy port fails fast. Raises OSError."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((web_app.HOST, port))
        sock.listen(128)
    except OSError:
        sock.close()
        raise
    return sock


def ui_config(app: object, port: int) -> uvicorn.Config:
    """uvicorn settings for `zombie ui`: 127.0.0.1 only, no proxy headers, quiet logs (S8)."""
    return uvicorn.Config(
        app,
        host=web_app.HOST,
        port=port,
        proxy_headers=False,
        server_header=False,
        access_log=False,
        log_level="warning",
    )


def _ui(root: str, port: int, use_model: bool = True, open_browser: bool = True) -> int:
    path = Path(root).expanduser()
    if not path.is_dir():
        print(f"zombie ui: {root} is not a folder. Pass a folder that contains git repos.")
        return 2
    try:
        sock = bind_socket(port)
    except OSError as exc:
        print(
            f"zombie ui: port {port} on {web_app.HOST} is busy ({exc.strerror})."
            " Stop the other program or pass --port N."
        )
        return 2
    path = path.resolve()
    zombies = eulogize_all(run_all(path), use_model=use_model)
    state.save_last_scan(zombies)
    token = secrets.token_urlsafe(32)
    app = web_app.create_app(path, port, token=token, use_model=use_model)
    server = uvicorn.Server(ui_config(app, port))
    url = f"http://{web_app.HOST}:{port}/?token={token}"
    print(f"{len(zombies)} zombies found in {tilde(str(path))}.")
    print(f"Zombie Process Whisperer is running at {url}")
    print("The link works once. Press Ctrl-C to stop.", flush=True)
    if open_browser:
        webbrowser.open(url)  # the socket is already listening, so the request just queues
    server.run(sockets=[sock])
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.WARNING)
    if args.command == "scan":
        return _scan(
            args.root, args.out, use_model=not args.no_model, stale_days=args.stale_days
        )
    if args.command == "graveyard":
        return _graveyard()
    if args.command == "bury":
        return _bury(args.id, apply=args.apply)
    if args.command == "resurrect":
        return _resurrect(args.id)
    return _ui(
        args.root, args.port, use_model=not args.no_model, open_browser=not args.no_open
    )


if __name__ == "__main__":
    raise SystemExit(main())
