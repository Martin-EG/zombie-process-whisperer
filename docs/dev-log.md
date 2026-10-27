# Dev log

## Build status

- [x] Step 1: scaffold, models, proc, state (2026-10-27)
- [x] Step 2: git scanner (2026-10-27)
- [x] Step 3: docker, cron, process scanners (2026-10-27)
- [x] Step 4: eulogy writer (2026-10-27)
- [ ] Step 5: CLI scan report and graveyard.md
- [ ] Step 6: bury and resurrect
- [ ] Step 7: local web UI
- [ ] Step 8: safety audit
- [ ] Step 9: demo graveyard and recording

## Step 1 (2026-10-27)

Scaffold, `models.py`, `proc.py` (executable allowlist, S2 mutation validator), `state.py` (atomic JSON),
argparse CLI stubs, socket guard and temp `ZOMBIE_HOME` fixtures. 58 tests pass, ruff clean.

## Step 1 follow-up (2026-10-27)

`run_read` now refuses anything outside a read-only shape allowlist (`validate_read`, raises `ReadRefused`).
Closes the gap where `run_read(["crontab", "-r"])` or `git branch -D` would have run. 110 tests pass, ruff clean.

## Step 2 (2026-10-27)

`scan_git.py`: `find_repos`, `default_branch`, `scan_git`. One `for-each-ref` per repo with NUL-separated
fields, merged set from `for-each-ref --merged <default>`, per-repo errors logged and skipped. `zombie scan`
temporarily prints one line per git zombie. 123 tests pass, ruff clean. Scan of 6 repos: 0.26 s.

## Step 3 (2026-10-27)

`scan_docker.py`, `scan_cron.py`, `scan_procs.py` with injectable runners; `scan.run_all` runs all four,
isolates failures, sorts, saves `last_scan.json`; `zombie scan` uses it and prints counts per kind.
Docker fixtures are hand-written in `docker ps/volume ls --format '{{json .}}'` shape (daemon was down,
so not recorded live). 151 tests pass, ruff clean. Local scan with Docker down: one warning, 0.39 s.

## Step 3 follow-up (2026-10-27)

Scanner checked against a live daemon (Docker 29.8.2): one real exited container found with full id,
image, status and age. Fixture rows now carry the `HealthStatus` and nested `Platform` fields real
`docker ps` emits. 151 tests pass, ruff clean.

## Step 4 (2026-10-27)

`eulogy.py`: `eulogy`, `template_eulogy`, `eulogize_all` (cache in `eulogies.json`, rich progress on a
terminal), `clean`/`truncate` post-processing. `zombie scan --no-model` uses templates only. 205 tests
pass, ruff clean. Live with gemma3:1b: cold scan 1.83 s, warm 0.36 s, longest eulogy 28 words. The 1b
model invents numbers now and then ("Five years of existence" for a 5-day-old container) despite the
prompt; text is display-only (S4), so this is a quality issue, not a safety one.
