"""argparse entry point for the `zombie` command."""

import argparse
import logging
from collections import Counter
from pathlib import Path

from zombie import state
from zombie.eulogy import eulogize_all
from zombie.scan import KIND_ORDER, run_all


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

    bury = sub.add_parser("bury", help="delete a safe zombie (dry run unless --apply)")
    bury.add_argument("id", help="zombie id from the last scan")
    bury.add_argument("--apply", action="store_true", help="actually delete it")

    resurrect = sub.add_parser("resurrect", help="restore a buried git branch")
    resurrect.add_argument("id", help="zombie id from the graveyard")

    sub.add_parser("graveyard", help="list buried zombies")

    ui = sub.add_parser("ui", help="local web UI on 127.0.0.1")
    ui.add_argument("root", nargs="?", default=".", help="folder to search for git repos")
    ui.add_argument("--port", type=int, default=8765, help="port on 127.0.0.1 (default 8765)")

    return parser


def _scan(root: str, use_model: bool = True) -> int:
    # Temporary plain output; step 5 replaces it with the real report.
    path = Path(root).expanduser()
    if not path.is_dir():
        print(f"zombie scan: {root} is not a folder. Pass a folder that contains git repos.")
        return 2
    zombies = eulogize_all(run_all(path), use_model=use_model)
    state.save_last_scan(zombies)
    for z in zombies:
        age = "?" if z.age_days is None else f"{z.age_days}d"
        print(f"{z.id}  {z.kind}  {z.title}  {age}  buryable={'yes' if z.buryable else 'no'}")
        print(f"    {z.eulogy}")
    counts = Counter(z.kind for z in zombies)
    summary = ", ".join(f"{kind}={counts.get(kind, 0)}" for kind in KIND_ORDER)
    print(f"{len(zombies)} zombies: {summary}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.WARNING)
    if args.command == "scan":
        return _scan(args.root, use_model=not args.no_model)
    print(f"zombie {args.command}: not implemented yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
