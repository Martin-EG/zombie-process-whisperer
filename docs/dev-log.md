# Dev log

## Build status

- [x] Step 1: scaffold, models, proc, state (2026-10-27)
- [x] Step 2: git scanner (2026-10-27)
- [ ] Step 3: docker, cron, process scanners
- [ ] Step 4: eulogy writer
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
