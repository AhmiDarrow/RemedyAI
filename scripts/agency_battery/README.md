# Agency battery (Phase 0)

Offline fixtures and checklists for measuring Remedy coding agency vs baseline.

## Tasks

| ID | Task | Measures |
|----|------|----------|
| B1 | Multi-file feature + tests | file_edit / file_write / mission |
| B2 | Work alone end-to-end | mission_* + no re-ask |
| B3 | Fix suite until green | mission_verify + retries |
| B4 | Grep-driven refactor | repo_search + file_edit |
| B5 | Optional research (web) | web_fetch when enabled |

## Offline unit checks (no model)

```bash
pytest tests/test_file_edit.py tests/test_repo_search.py tests/test_mission.py -q
```

## Live model battery (manual / operator)

1. Open Remedy Desktop, select Grok 4.5 (or other frontier model).
2. Open a scratch project folder.
3. Run each prompt in `prompts.md` with work-alone language where noted.
4. Record steps, re-asks, pass/fail in a log under `~/.remedy/agency_battery/`.
