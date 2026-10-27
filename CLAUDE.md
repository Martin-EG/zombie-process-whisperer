# CLAUDE.md: Zombie Process Whisperer

Read this whole file before every task. The rules here override anything a step prompt says. If a prompt seems to conflict with a rule, stop and ask.

## What this project is

Zombie Process Whisperer finds abandoned things on a developer's own machine: merged or stale git branches, exited Docker containers, dangling Docker volumes, cron jobs whose script is gone, and zombie processes. A local open-weight model (through Ollama) writes a short, darkly funny eulogy for each one. The user can bury the safe ones (delete them) only after typing the item's exact name, and can resurrect a buried git branch from an undo log.

It has two front ends that share the same core: a terminal CLI (`zombie`) and a local web UI (`zombie ui`) served only on 127.0.0.1.

Built for the Hacktoberfest "Ghost in the Machine" challenge (open-source AI at the core). The point of the project is that it runs fully offline and never sends repo names, commit messages, or paths to any server.

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

Data flows one way: scanners produce `Zombie` objects, everything else consumes them. Only `bury.py` changes anything on the machine, and only through `proc.run_mutation`.

## Module map

| Path | Responsibility | May change the machine? |
| --- | --- | --- |
| `zombie/models.py` | Pydantic models: `Zombie`, `GraveEntry` | No |
| `zombie/proc.py` | The only place that imports `subprocess`. `run_read(args)` and `run_mutation(args)` | Only `run_mutation` |
| `zombie/state.py` | State directory, atomic JSON read/write for `last_scan.json`, `graveyard.json`, `eulogies.json` | Writes state files only |
| `zombie/scan_git.py` | Stale and merged local branches | No |
| `zombie/scan_docker.py` | Exited containers, dangling volumes | No |
| `zombie/scan_cron.py` | Crontab entries whose script path is missing | No |
| `zombie/scan_procs.py` | Processes in zombie state (psutil) | No |
| `zombie/scan.py` | `run_all(root)`: runs every scanner, saves `last_scan.json` | No |
| `zombie/eulogy.py` | Eulogy text from the local model, with cache and template fallback | No |
| `zombie/report.py` | Terminal table and `graveyard.md` | No |
| `zombie/bury.py` | `bury()` and `resurrect()`. The only caller of `run_mutation` | Yes |
| `zombie/cli.py` | argparse entry point: `scan`, `bury`, `resurrect`, `graveyard`, `ui` | Through `bury.py` only |
| `zombie/web/app.py` | FastAPI app, auth and security middleware | Through `bury.py` only |
| `zombie/web/static/` | `index.html`, `app.js`, `style.css` (no inline scripts) | No |
| `scripts/make_demo_graveyard.py` | Builds a reproducible fake graveyard for demos | Only inside its own demo folder |
| `tests/` | pytest suite, fixtures in `tests/fixtures/` | No |

## Data model

```python
class Zombie(BaseModel):
    kind: Literal["git_branch", "docker_container", "docker_volume", "cron_job", "process"]
    id: str               # first 10 hex chars of sha256(f"{kind}|{location}|{title}"), stable across scans
    title: str            # branch name, container name, volume name, cron command, process name
    location: str         # repo path, container id, volume name, "crontab", pid
    age_days: int | None
    facts: dict[str, str] # only real metadata: dates, last commit subject, status, image, parent pid
    buryable: bool
    reason_not_buryable: str | None = None
    eulogy: str | None = None

class GraveEntry(BaseModel):
    zombie_id: str
    kind: str
    title: str
    location: str
    sha: str | None       # required for git branches
    buried_at: str        # ISO 8601
    reversible: bool      # True for git branches, False for docker containers
```

State lives in `$ZOMBIE_HOME` (default `~/.local/state/zombie-whisperer/`). Tests always set `ZOMBIE_HOME` to a temp folder.

## Safety invariants

Every change must keep all of these true. Tests named `test_s1_...` through `test_s9_...` enforce them. Never weaken or delete one of those tests.

- **S1 Read-only by default.** Scanners, eulogy, report, and the web UI's GET routes never change anything outside the state directory.
- **S2 Mutation allowlist.** `proc.run_mutation` accepts exactly three command shapes and rejects everything else: `git -C <repo> branch -d <branch>`, `git -C <repo> branch <branch> <sha>` (resurrect), and `docker rm <container_id>`. Never `-D`, `--force`, `kill`, crontab edits, file deletion, or volume removal.
- **S3 No shell.** Only `proc.py` imports `subprocess`. Always a list of args, `shell=False`, a timeout, and an executable allowlist of `git`, `docker`, `crontab`. Branch names are validated with `git check-ref-format --branch`; container ids must match `^[0-9a-f]{12,64}$`; SHAs must match `^[0-9a-f]{40}$`.
- **S4 Model output is display text only.** Eulogy text is never parsed, executed, passed to a subprocess, or used to decide anything.
- **S5 Metadata is untrusted.** Commit messages, branch names, container names, and paths go into prompts only inside a JSON block marked as data. In the web UI they are rendered with `textContent`, never `innerHTML`.
- **S6 Confirmed, re-verified burials.** Bury requires an id from the last scan, the item's exact name typed by the user, and an explicit apply (`--apply` on the CLI, Dry run unchecked in the UI). At bury time, re-check that the item still exists and is unchanged: branch still merged with the same SHA, container still exited.
- **S7 Undo first.** For a git branch, append the `GraveEntry` (with SHA) to `graveyard.json` and flush it before deleting. Docker removals are marked `reversible: false` and the user is warned before confirming.
- **S8 Local network only.** The only outbound connection is to Ollama at `127.0.0.1:11434`. The web server binds to `127.0.0.1` only. No CDN, external fonts, analytics, or telemetry.
- **S9 Web UI auth.** A random startup token in the opening URL is exchanged once for a separate HttpOnly, SameSite=Strict session cookie, then the token is invalid. Every route except that exchange requires the cookie. Requests with a Host other than `127.0.0.1:<port>` or `localhost:<port>` are rejected, as are requests with a non-matching `Origin` and POST requests with no `Origin`. Only POST routes change anything. Every response sends `Content-Security-Policy: default-src 'self'`, `X-Content-Type-Options: nosniff`, and `Referrer-Policy: no-referrer`.

## Coding rules

- Python 3.11+, type hints everywhere, pydantic v2.
- Dependencies are fixed: `psutil`, `rich`, `ollama`, `pydantic`, `fastapi`, `uvicorn`; dev: `pytest`, `httpx`, `ruff`. Ask before adding any other.
- CLI uses `argparse` (no click or typer).
- Keep functions small and pure where possible. Scanners take their inputs as arguments so tests can inject fakes.
- Tests never need real Docker, real Ollama, or the network. Use fakes and recorded output in `tests/fixtures/`. Real `git` in temp folders is fine.
- `tests/conftest.py` has an autouse fixture that fails any socket connection to a host other than 127.0.0.1 or localhost, and sets `ZOMBIE_HOME` to a temp folder.
- Error messages say what happened and what to do next. No stack traces for expected conditions (Docker not running, no crontab, Ollama down).
- Do not edit this file's Safety invariants section. You may update Build status and add to Decisions.

## Commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q                 # must pass before every commit
ruff check .              # must be clean before every commit
zombie scan ~/projects    # read-only report
zombie bury <id>          # dry run; add --apply to act
zombie resurrect <id>
zombie ui ~/projects      # local web UI at 127.0.0.1:8765
```

Model: `ZOMBIE_MODEL` env var, default `gemma3:1b`. Any small instruct model in `ollama list` works.

## Definition of done for every step

1. `pytest -q` passes and `ruff check .` is clean.
2. The step's QA checklist was run, and its results were reported back with the actual command output.
3. Build status below is updated.
4. One commit named `step N: <summary>`.

## Build status

- [x] Step 1: scaffold, models, proc, state
- [ ] Step 2: git scanner
- [ ] Step 3: docker, cron, process scanners
- [ ] Step 4: eulogy writer
- [ ] Step 5: CLI scan report and graveyard.md
- [ ] Step 6: bury and resurrect
- [ ] Step 7: local web UI
- [ ] Step 8: safety audit
- [ ] Step 9: demo graveyard and recording

## Decisions

Record non-obvious choices here as one line each, with the step number.

- Step 1: `run_mutation` requires the `-C <repo>` path to be absolute; relative repo paths are refused.
- Step 1: branch names containing `@{` are refused before git runs, and `git check-ref-format --branch` output must equal the input (no `@{-N}` expansion).
- Step 1: regexes use `re.fullmatch`, so a trailing newline never passes container-id or SHA checks.
- Step 1: `run_read` only checks the executable allowlist, not the command shape; read-only use is enforced by review and the S2 architecture test.
- Step 1: `last_scan.json` and `graveyard.json` store plain JSON lists of model dumps; `eulogies.json` is a `{zombie_id: text}` map.
