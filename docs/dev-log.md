# Dev log

## Build status

- [x] Step 1: scaffold, models, proc, state (2026-10-27)
- [ ] Step 2: git scanner
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
