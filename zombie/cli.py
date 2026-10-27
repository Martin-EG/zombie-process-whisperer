"""argparse entry point for the `zombie` command."""

import argparse
import logging
from pathlib import Path

from zombie import state
from zombie.eulogy import eulogize_all
from zombie.report import print_graveyard, print_report, tilde, write_graveyard_md
from zombie.scan import run_all


def non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError(f"{value} is negative; pass 0 or more days.")
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
    ui.add_argument("--port", type=int, default=8765, help="port on 127.0.0.1 (default 8765)")

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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.WARNING)
    if args.command == "scan":
        return _scan(
            args.root, args.out, use_model=not args.no_model, stale_days=args.stale_days
        )
    if args.command == "graveyard":
        return _graveyard()
    print(f"zombie {args.command}: not implemented yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
