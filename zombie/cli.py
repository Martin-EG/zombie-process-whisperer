"""argparse entry point for the `zombie` command."""

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zombie",
        description="Find abandoned branches, containers, cron jobs and zombie processes.",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    scan = sub.add_parser("scan", help="read-only scan and report")
    scan.add_argument("root", nargs="?", default=".", help="folder to search for git repos")

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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"zombie {args.command}: not implemented yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
