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
- [x] Step 2: git scanner
- [x] Step 3: docker, cron, process scanners
- [x] Step 4: eulogy writer
- [x] Step 5: CLI scan report and graveyard.md
- [x] Step 6: bury and resurrect
- [x] Step 7: local web UI
- [x] Step 8: safety audit
- [ ] Step 9: demo graveyard and recording

## Decisions

Record non-obvious choices here as one line each, with the step number.

- Step 1: `run_mutation` requires the `-C <repo>` path to be absolute; relative repo paths are refused.
- Step 1: branch names containing `@{` are refused before git runs, and `git check-ref-format --branch` output must equal the input (no `@{-N}` expansion).
- Step 1: regexes use `re.fullmatch`, so a trailing newline never passes container-id or SHA checks.
- Step 1: `run_read` accepts only read-only shapes (`validate_read`): `crontab -l`; `git --version` or `git -C <abs repo>` + for-each-ref/rev-parse/merge-base/log/rev-list/show-ref/check-ref-format with no `-c`, `--git-dir`, `--output`, `--ext-diff`, `--textconv`; docker ps/inspect/info/version/container ls|inspect/volume ls|inspect with no global flags. `git branch` and `symbolic-ref` are excluded (positional forms write); use `for-each-ref --merged`.
- Step 1: `last_scan.json` and `graveyard.json` store plain JSON lists of model dumps; `eulogies.json` is a `{zombie_id: text}` map.
- Step 2: the step prompt's `git symbolic-ref` and `git branch --merged` are replaced by `for-each-ref --format=%(symref) refs/remotes/origin/HEAD` and `for-each-ref --merged`, per the step 1 read allowlist.
- Step 2: a stale branch is one whose last committer date is at least `stale_days` old; current branch is detected with `%(HEAD)`.
- Step 2: in a repo with no default branch, `facts["merged"]` is `"unknown"` (not `"no"`), since merge status cannot be checked.
- Step 2: `proc.CommandTimeout` re-exports `subprocess.TimeoutExpired` so scanners can catch timeouts without importing subprocess.
- Step 3: `docker ps` uses `--no-trunc` for full container ids; rows whose id fails `is_container_id` are skipped. If `docker ps` fails, the volume call is skipped too (one warning).
- Step 3: docker container `age_days` comes from `CreatedAt`; volumes and cron jobs have `age_days=None` (no reliable date without extra calls).
- Step 3: cron flags an entry by the first absolute or `~/` token only (`/usr/bin/env python3 /gone.py` is not flagged); `shlex` split, falling back to whitespace on bad quoting.
- Step 3: `psutil.ZombieProcess` from `name()`/`create_time()`/`ppid()` falls back to "unknown"/None instead of skipping, since real zombies often refuse those calls.
- Step 3: `run_all` sorts kinds in `ZombieKind` order (git_branch, docker_container, docker_volume, cron_job, process), unknown ages last within a kind.
- Step 4: `eulogies.json` keys are `"<zombie id>:<sha256 of facts>"`; a new eulogy for an id replaces its old key. Only model text is cached, never templates, so real eulogies appear once Ollama is back.
- Step 4: the cache key drops the trailing relative time from a container's `status` ("Exited (0) 6 minutes ago"), or every scan would miss. `age_days` is not in the key.
- Step 4: `ollama.Client` is built with `trust_env=False` and `follow_redirects=False` so proxy env vars or a redirect cannot send the prompt off 127.0.0.1 (S8).
- Step 4: after the first model failure in `eulogize_all` (Ollama down, timeout, missing model) the rest of the run uses templates with one warning; an empty reply falls back for that item only.
- Step 4: the user message is `Kind: <kind>` then `DATA:` and one JSON line; `clean()` also drops control characters and a leading "Here's a ... eulogy:" preamble that gemma3:1b sometimes adds.
- Step 4: conftest's autouse `no_real_ollama` points `eulogy.OLLAMA_HOST` at 127.0.0.1:9 so no test reaches a real local Ollama.
- Step 4: `zombie scan` saves `last_scan.json` again after eulogies are filled, so the saved scan carries them.
- Step 5: `graveyard.md` (default `./graveyard.md`, `--out` to move it) is the one file the scan writes outside the state dir, as the architecture diagram shows; it is gitignored in this repo.
- Step 5: terminal cells show newlines and other control characters as visible escapes (`\n`, `\x1b`) before `rich.markup.escape`, so a name cannot split a row or send terminal escape codes.
- Step 5: markdown cells replace newlines with spaces and escape `\`, `|`, `<`, `>`; home becomes `~` only as a whole path prefix (`/Users/ghostly` is left alone when home is `/Users/ghost`).
- Step 5: the KIND column uses short labels (branch, container, volume, cron, process) and the table is auto-sized, not expanded, so it fits 80 columns; graveyard.md uses long labels.
- Step 5: `run_all` takes `stale_days` (default 90) for `--stale-days`; `zombie graveyard` shows `buried_at` to the minute.
- Step 6: `GraveEntry` gains optional `resurrected_at` (default None); resurrect sets it and keeps the entry. Resurrect picks the newest entry for the id that is not yet resurrected.
- Step 6: refusals are `BuryRefused` subclasses (NotInLastScan, NotBuryable, ConfirmationMismatch, StaleScan, NotInGraveyard, NotReversible, BranchExists), exit 2; `MutationFailed` (git/docker ran and failed) exits 1.
- Step 6: `plan_bury` refuses any kind other than git_branch and docker_container even if last_scan.json says buryable, and runs `proc.validate_mutation` on the planned args.
- Step 6: re-verify uses read-allowlisted git only: `rev-parse --absolute-git-dir` (repo), `rev-parse --verify --quiet refs/heads/<b>^{commit}` (SHA), `merge-base --is-ancestor <sha> <default ref>` (merged), `rev-parse --symbolic-full-name HEAD` (current). Containers use `docker container inspect`: same full id, same name, State.Status `exited`.
- Step 6: if run_mutation raises or git exits non-zero, the undo entry is removed only when the branch still exists at the recorded SHA; otherwise it is kept. A BaseException (Ctrl-C, crash) leaves it in place; resurrect then refuses because the branch exists.
- Step 6: `state._atomic_write_json` also fsyncs the state folder after `os.replace`, so the undo entry's rename is durable before git runs (S7).
- Step 6: `scan_git._default_ref` is now public `default_ref_for`, shared with bury's re-verify.
- Step 6: `zombie bury --apply` with EOF at the prompt is a refusal (exit 2). Known gap: between re-verify and `git branch -d` a new commit could land; `-d` still refuses if it is unmerged into HEAD, and S2 has no compare-and-delete shape.
- Step 7: `create_app(root, port, token=None, use_model=True)`; the token and session live in the app closure, the token is set to None once exchanged, and HEAD as well as GET on `/?token=` performs the exchange (so `curl -sI` works). Any later `?token=` on `/` is a 401 even with a valid cookie.
- Step 7: the CLI binds the 127.0.0.1 socket before scanning (busy port fails fast, exit 2) and hands it to `uvicorn.Server.run(sockets=...)`; `cli.ui_config` sets host 127.0.0.1, `proxy_headers=False`, no access log (the token never lands in a log line).
- Step 7: responses also send `X-Frame-Options: DENY` (CSP stays exactly `default-src 'self'`); rejections and 500s carry the same headers. Docs and OpenAPI routes are off; `/favicon.ico` is a 204 so browsers log no 404.
- Step 7: `GET /api/zombies` adds `command` (shlex-joined `plan_bury` args) and `reversible` per buryable item so the dialog shows the exact command before any POST; a `plan_bury` refusal turns the item report-only with its reason. POST bodies are `extra="forbid", strict=True` (`"apply": "yes"` is 422).
- Step 7: bury refusals (any `BuryRefused`) are 409 with `{"detail": msg}`; resurrect refusals and `CommandRefused` are 409 too; `MutationFailed` is 500. Rescan, bury and resurrect share one lock.
- Step 7: `zombie ui` prints the URL with `flush=True`; without it a redirected stdout held the URL until exit.
- Step 8: S3 was weak: bury, scan_git, scan_cron, scan_docker reached `proc.subprocess` for type hints, so `proc.subprocess.run(...)` would bypass both allowlists unnoticed. proc now exports `CompletedProcess`; `test_s3_no_backdoor_to_subprocess` refuses `.subprocess`, `._run`, os.system/popen/exec*/spawn*/fork and pty.spawn outside proc.py.
- Step 8: S7 was weak across processes: the CLI and web UI could interleave graveyard read-modify-writes and drop a flushed undo entry; resurrect also saved a snapshot taken before git ran. bury (apply) and resurrect now hold `state.lock()` (fcntl.flock on `$ZOMBIE_HOME/.lock`), and resurrect reloads graveyard.json before saving.
- Step 8: eulogy.py's docstring says "command runner" instead of "subprocess" so the S3 grep shows only proc.py.
- Step 8: S2 probes (leading dashes, spaces, `..`, `@{`, `@{u}`, HEAD, trailing newline, shell characters, `$(id)` container ids, `--force`) are all refused and are now REFUSED cases in test_proc. Unicode names and a bare `@` are accepted: git treats them as literal branch names and no shell is involved.
- Step 8: abbreviated git options (`--outp=` for `--output`) were checked against git 2.54 `log`; git refuses them, so the read prefix list holds.
- Step 8: known residual gap, unchanged: between re-verify and `git branch -d` a branch can move to a newer commit; `-d` still requires it merged into HEAD or upstream, so no commit is lost, but the undo entry records the older SHA.
- Step 8: S8 network-off check runs pytest and `zombie scan` under `sandbox-exec` with outbound denied except localhost; the step 9 demo script is still a stub, so a hand-built scratch repo stood in for the demo folder.
