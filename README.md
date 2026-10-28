# Zombie Process Whisperer

Find the abandoned things haunting your machine, hear their eulogies, and bury the safe ones.

Zombie Process Whisperer scans your own computer for:

- **Git branches** that are already merged, or unmerged and untouched for a long time
- **Docker containers** that have exited
- **Docker volumes** that are dangling
- **Cron jobs** whose script no longer exists
- **Zombie processes**

A small open-weight model running locally through [Ollama](https://ollama.com) writes a short, darkly funny eulogy for each one. You can then **bury** (delete) the safe ones after typing the item's exact name, and **resurrect** a buried git branch from an undo log.

Everything runs offline. Repo names, commit messages, and paths never leave your machine: the only network connection is to Ollama on `127.0.0.1:11434`.

Built for the Hacktoberfest "Ghost in the Machine" challenge (open-source AI at the core).

---

## Contents

- [Requirements](#requirements)
- [Install](#install)
- [Quick start](#quick-start)
- [Commands](#commands)
- [The web UI](#the-web-ui)
- [What can be buried](#what-can-be-buried)
- [How burying and resurrecting work](#how-burying-and-resurrecting-work)
- [Configuration](#configuration)
- [Files it writes](#files-it-writes)
- [Safety model](#safety-model)
- [Architecture](#architecture)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Requirements

| Needed | Why | Required? |
| --- | --- | --- |
| Python 3.11+ | Runs the tool | Yes |
| `git` | Scans branches; buries and resurrects them | Yes |
| macOS or Linux | Uses `crontab`, `fcntl` file locks, and process states | Yes |
| [Ollama](https://ollama.com) with a small instruct model | Writes the eulogies | No. Without it you get template eulogies |
| Docker | Scans containers and volumes | No. Skipped with a warning if it isn't running |

## Install

```bash
git clone https://github.com/Martin-EG/zombie-process-whisperer.git
cd zombie-process-whisperer
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

This installs the `zombie` command into the virtual environment.

For model-written eulogies, install Ollama and pull the default model:

```bash
ollama pull gemma3:1b
```

```bash
ollama serve
```

## Quick start

```bash
zombie scan ~/projects
```

This is a read-only report. It prints a table of every zombie with its id, kind, name, age, and eulogy, and saves the same report to `./graveyard.md`.

To delete a merged branch from the report, first do a dry run with its id:

```bash
zombie bury 3fa91c0d2e
```

Then apply it. You will be asked to type the branch name exactly:

```bash
zombie bury 3fa91c0d2e --apply
```

Changed your mind?

```bash
zombie resurrect 3fa91c0d2e
```

Prefer a browser?

```bash
zombie ui ~/projects
```

## Commands

### `zombie scan [root]`

Searches `root` (default: the current folder) for git repos up to 4 folders deep, then checks Docker, your crontab, and running processes. Nothing on the machine is changed.

| Option | Default | Meaning |
| --- | --- | --- |
| `--stale-days N` | `90` | An unmerged branch whose last commit is at least this many days old counts as stale |
| `--out PATH` | `graveyard.md` | Where to write the markdown report |
| `--no-model` | off | Use template eulogies only; never call Ollama |

The repo search skips hidden folders, symlinks, `node_modules`, `.venv`, and `venv`, and does not descend into a repo once it finds one.

### `zombie bury <id> [--apply]`

Deletes one zombie from the **last scan**. Without `--apply` it is a dry run: it shows the exact command it would run and whether it can be undone, and changes nothing. With `--apply` it asks you to type the item's exact name, re-checks the item against the live machine, and only then deletes it.

Exit codes: `0` done (or dry run), `2` refused and nothing was changed, `1` git or docker ran and failed.

### `zombie resurrect <id>`

Recreates a buried git branch at the exact commit it pointed to when it was buried. Refuses if a branch with that name already exists, if the repo is gone, or if the commit has been garbage-collected.

### `zombie graveyard`

Lists everything you have buried, when, and whether it was resurrected.

### `zombie ui [root]`

Starts the local web UI. See [The web UI](#the-web-ui).

| Option | Default | Meaning |
| --- | --- | --- |
| `--port N` | `8765` | Port on `127.0.0.1` |
| `--no-model` | off | Template eulogies only |
| `--no-open` | off | Print the URL instead of opening a browser |

## The web UI

```bash
zombie ui ~/projects
```

The UI runs a scan, then opens `http://127.0.0.1:8765/?token=...` in your browser. That link works **once**: the token is swapped for a session cookie, and the link stops working after that. If you lose the session, restart `zombie ui` to get a new link.

In the UI you can:

- See each zombie as a card with its facts and eulogy
- **Rescan** at any time
- **Bury** an item. The dialog shows the exact command that will run and whether it can be undone. **Dry run** is checked by default; uncheck it and type the exact name to actually delete
- See the **Graveyard** and resurrect buried branches from it

The server listens only on `127.0.0.1` and refuses requests from other sites. See [Safety model](#safety-model).

## What can be buried

| Kind | Found when | Buryable? | Bury runs | Undo |
| --- | --- | --- | --- | --- |
| Git branch | Merged into the default branch | Yes | `git -C <repo> branch -d <branch>` | `zombie resurrect` |
| Git branch | Unmerged and idle for `--stale-days` or more | No (report only, "not merged") | — | — |
| Git branch | Stale, in a repo with no default branch | No (report only) | — | — |
| Docker container | Status is `exited` | Yes | `docker rm <container id>` | **None. Permanent** |
| Docker volume | Dangling | No (report only) | — | — |
| Cron job | Its script path no longer exists | No (report only) | — | — |
| Process | In zombie state | No (report only) | — | — |

The default branch is `origin/HEAD`'s target, else `main`, else `master`. The default branch and the checked-out branch are never listed.

Report-only items are listed so you know about them. Clean those up yourself.

## How burying and resurrecting work

Burying a branch:

1. The id must be in the last scan. Run `zombie scan` (or **Rescan**) first.
2. You type the branch name exactly.
3. The tool re-checks the live repo. The branch must still exist, still point at the same commit as in the scan, still be merged into the default branch, and not be checked out. If anything changed, it refuses and asks you to rescan.
4. It writes an undo entry (repo, branch, full commit SHA) to `graveyard.json` and flushes it to disk.
5. Only then does it run `git branch -d`. Git refuses to delete a branch that isn't merged, so `-D` (force) is never used.

Burying a container works the same way, except there is no undo. The tool re-checks that the container still exists with the same id and name and is still exited, and warns you that the removal is permanent before you confirm. `docker rm` is never run with `-f`.

Resurrecting runs `git -C <repo> branch <branch> <sha>` with the SHA from the undo log, then marks the graveyard entry as resurrected. The history stays, so you can bury and resurrect the same branch again.

The CLI and the web UI share the same state and take the same lock, so you can use both at once.

## Configuration

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `ZOMBIE_MODEL` | `gemma3:1b` | Ollama model for eulogies. Any small instruct model in `ollama list` works |
| `ZOMBIE_HOME` | `~/.local/state/zombie-whisperer/` | Where state files live |

The Ollama address is fixed at `http://127.0.0.1:11434`. Proxy environment variables and `OLLAMA_HOST` are ignored on purpose, so prompts can't be sent anywhere else.

If Ollama is down, slow (over 10 seconds), or missing the model, the tool prints one warning and uses template eulogies for the rest of the run.

## Files it writes

| File | Location | Contents |
| --- | --- | --- |
| `last_scan.json` | `$ZOMBIE_HOME` | The most recent scan. Bury only accepts ids from here |
| `graveyard.json` | `$ZOMBIE_HOME` | Undo log of every burial |
| `eulogies.json` | `$ZOMBIE_HOME` | Cache of model eulogies, so unchanged items don't hit the model again |
| `.lock` | `$ZOMBIE_HOME` | Lock file shared by the CLI and the web UI |
| `graveyard.md` | `./graveyard.md` or `--out` | Markdown report from `zombie scan` |

State files are written atomically: a temp file, then fsync, then rename. A crash can't leave a half-written file.

## Safety model

The tool deletes things on your machine, so its rules are strict. Each rule is enforced in code and by tests named `test_s1_*` to `test_s9_*`.

| # | Rule | In practice |
| --- | --- | --- |
| S1 | Read-only by default | Scanning, eulogies, reports, and the web UI's GET routes never change anything outside the state folder |
| S2 | Mutation allowlist | Only three commands can ever change the machine: `git -C <repo> branch -d <branch>`, `git -C <repo> branch <branch> <sha>`, `docker rm <container id>`. Never `-D`, `--force`, `kill`, crontab edits, file deletion, or volume removal |
| S3 | No shell | Commands run as argument lists with `shell=False`, a timeout, and only `git`, `docker`, or `crontab`. Branch names are checked with `git check-ref-format --branch`; container ids must be 12–64 lowercase hex; SHAs must be 40 lowercase hex |
| S4 | Model output is display text only | Eulogies are never parsed, executed, or used to decide anything |
| S5 | Metadata is untrusted | Branch names, commit messages, and paths reach the model only inside a JSON data block. The web UI renders them as plain text, never HTML. The terminal shows control characters as visible escapes |
| S6 | Confirmed, re-verified burials | An id from the last scan, the exact name typed by you, and an explicit apply, followed by a live re-check |
| S7 | Undo first | A branch's undo entry is on disk before it is deleted. Container removals are marked permanent and you are warned |
| S8 | Local network only | The only outbound connection is to Ollama on `127.0.0.1`. The web server binds to `127.0.0.1`. No CDN, fonts, analytics, or telemetry |
| S9 | Web UI auth | A one-time startup token is exchanged for an HttpOnly, SameSite=Strict cookie. Every other request needs the cookie. Wrong `Host`, wrong `Origin`, and POSTs without `Origin` are rejected. Every response sends `Content-Security-Policy: default-src 'self'`, `X-Content-Type-Options: nosniff`, and `Referrer-Policy: no-referrer` |

**Known limitation:** between the re-check and `git branch -d`, a new commit could land on the branch. `-d` still only deletes a branch that is merged, so no commit is lost, but the undo entry would point at the earlier commit.

The full audit is recorded in [CLAUDE.md](CLAUDE.md) under Decisions (step 8).

## Architecture

```text
                    read-only                        text only
 scan_git.py  ─┐                               ┌─► eulogy.py ──► Ollama @ 127.0.0.1:11434
 scan_docker.py├─► scan.py ─► list[Zombie] ────┤     (template fallback if Ollama is down)
 scan_cron.py  │      │                        ├─► report.py ──► rich table, graveyard.md
 scan_procs.py ┘      ▼                        └─► web/app.py ─► browser @ 127.0.0.1
                state.py: last_scan.json
                                                 cli.py ─┐
                                              web/app.py ─┴─► bury.py ──► proc.run_mutation ──► git / docker
                                                                 │
                                                                 └─► state.py: graveyard.json (undo log)
```

Data flows one way: scanners produce `Zombie` objects and everything else consumes them. Only `bury.py` changes anything, and only through `proc.run_mutation`.

| Module | Responsibility |
| --- | --- |
| `zombie/models.py` | Pydantic models `Zombie` and `GraveEntry` |
| `zombie/proc.py` | The only module that imports `subprocess`. `run_read` (read-only shapes) and `run_mutation` (the three allowed shapes) |
| `zombie/state.py` | State folder, atomic JSON files, cross-process lock |
| `zombie/scan_git.py` | Merged and stale branches |
| `zombie/scan_docker.py` | Exited containers, dangling volumes |
| `zombie/scan_cron.py` | Cron entries whose script is missing |
| `zombie/scan_procs.py` | Zombie processes (psutil) |
| `zombie/scan.py` | `run_all(root)`: runs every scanner |
| `zombie/eulogy.py` | Eulogies from Ollama, with cache and template fallback |
| `zombie/report.py` | Terminal table and `graveyard.md` |
| `zombie/bury.py` | `bury()` and `resurrect()` |
| `zombie/cli.py` | `zombie` command (argparse) |
| `zombie/web/app.py` | FastAPI app and security middleware |
| `zombie/web/static/` | `index.html`, `app.js`, `style.css` (no inline scripts) |

## Development

```bash
pytest -q
```

```bash
ruff check .
```

Both must pass before every commit.

- Tests never need real Docker, real Ollama, or the network. They use fakes and recorded output in `tests/fixtures/`, and real `git` in temp folders.
- `tests/conftest.py` fails any socket connection to a host other than `127.0.0.1`/`localhost`, points Ollama at a dead port, and sets `ZOMBIE_HOME` to a temp folder, so tests never touch your real state.
- Dependencies are fixed: `psutil`, `rich`, `ollama`, `pydantic`, `fastapi`, `uvicorn`; dev: `pytest`, `httpx`, `ruff`. Discuss before adding any other.
- Never weaken or delete a `test_s*` test.
- Project rules, build status, and the record of design decisions are in [CLAUDE.md](CLAUDE.md).

Build status: steps 1–8 are done (scanners, eulogies, report, bury and resurrect, web UI, safety audit). Step 9, the demo graveyard (`scripts/make_demo_graveyard.py`) and recording, is not built yet.

## Troubleshooting

| Message or symptom | What to do |
| --- | --- |
| "Could not get eulogies from Ollama…" | Start Ollama with `ollama serve`, check the model with `ollama list`, or pass `--no-model` |
| No Docker items, with a Docker warning | Start Docker Desktop or the Docker daemon, then rescan |
| "… is not in the last scan" | Run `zombie scan` again and use an id from that output |
| "… moved since the scan" / "no longer merged" / "now checked out" | The branch changed after the scan. Rescan, then try again |
| "The typed name does not match" | Type the exact name, including case and any `/` |
| "A branch named … already exists" on resurrect | Rename or delete the existing branch first |
| Web UI says "Not signed in" | The one-time link was already used. Restart `zombie ui` |
| "port 8765 on 127.0.0.1 is busy" | Pass `--port` with another port |

## License

MIT, as declared in `pyproject.toml`.
