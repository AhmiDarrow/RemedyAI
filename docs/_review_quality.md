# Quality / tests / packaging / repo-hygiene review

Reviewed local tree `C:\Users\Administrator\Old-Remedy` (product **v0.23.2** surfaces; local `master` HEAD `ccfc652f` is **3 commits past** origin `8cd8918` / tag `v0.23.2`). Did not modify product code. Did not re-run the suite.

## Summary

The committed product tree is in better shape than the dirty working copy suggests. Version surfaces (pyproject, desktop `package.json`, Tauri, Cargo, `scripts/latest.json`, README, What's new) agree on **0.23.2**. CI exists and is real (Linux full pytest + ruff + mypy + docs gate; a Windows subset; desktop vitest + production build). Most of the 193 `tests/test_*.py` files are ordinary unit tests with concrete assertions, not empty stubs.

The serious problems are: (1) a few tests still touch the real `~/.remedy` profile; (2) Windows-only computer-use tests silently pass on Linux, and Windows CI does not run the write-jail / computer-use files; (3) CHANGELOG `[Unreleased]` is empty while local master already has user-visible post-0.23.2 work; (4) the working tree is piled with ignored build/log junk plus two unignored root folders (`Users/`, `~/`) that `git add .` would pick up.

## Issues

### Issue 1 -- Severity: bug
- File: tests/test_e2e_simple_c_rmb.py:69
- Description: The only `pytest.mark.live` test binds `home_dir` and `memory_db_path` to the **real** `Path.home() / ".remedy"` and writes `_e2e_simple_c_pytest/` in the repo. `conftest.py` never isolates `REMEDY_HOME`. Default `uv run pytest` collects this test (`addopts` does not exclude `-m live`). On a developer box with RMB + gcc it mutates the owner's memory DB and approval state; CI only stays safe because `skipif` trips when RMB is down.
- Suggestion: Point `home_dir` / `memory_db_path` at `tmp_path`. Add `addopts` `-m "not live"` (or `filterwarnings` + an explicit `pytest -m live` target). Keep the repo scratch dir under `tmp_path` or the already-gitignored `_e2e_simple_c_pytest/`.
- Status: open

### Issue 2 -- Severity: bug
- File: tests/test_computer_use.py:1179
- Description: `test_print_window_foreground` writes `Path.home() / ".remedy" / "computer" / "shots" / "_test_print.png"` — the live product screenshot folder. On Windows it can create dirs / overwrite a shot in the owner's profile. On non-Windows it `return`s (see Issue 3) so Linux CI never fails.
- Suggestion: Write under `tmp_path`. Use `pytest.skip` on non-Windows. Same isolation for `tests/test_vision.py:635` (`BasicRuntime(..., home_dir=str(Path.home() / ".remedy"))`).
- Status: open

### Issue 3 -- Severity: bug
- File: tests/test_computer_use.py:983
- Description: Several Windows computer-use tests **return** instead of `pytest.skip` when `sys.platform != "win32"`: `test_desktop_screenshot_roundtrip` (983), `test_computer_capture_api` (1002), `test_list_monitors_windows` (1109), `test_desktop_snapshot_and_ref_store` (1134), `test_print_window_foreground` (1169). They report **passed** on Linux CI with zero assertions. Sibling `test_open_app_resolves_relative_in_search_dir` (249) already uses `pytest.skip` correctly. Windows CI (`.github/workflows/ci.yml:58`) does **not** run `test_computer_use.py` at all, so these paths are untested in both jobs.
- Suggestion: Replace every bare `return` with `pytest.skip("Windows only")`. Add `tests/test_computer_use.py` (and `test_host_bridge.py`, `test_win_paths.py`) to the Windows job.
- Status: open

### Issue 4 -- Severity: bug
- File: .github/workflows/ci.yml:58
- Description: The Windows job is labeled "agency + security + windows-sensitive" but omits the files that actually encode Windows path/jail/computer behavior: `test_project_write_jail.py`, `test_shell_jail_fuzz.py`, `test_win_paths.py`, `test_uninstall_wipe_paths.py`, `test_computer_use.py`, `test_host_bridge.py`, `test_hidden_process.py` is included; write-jail is not. Linux pytest cannot catch `win32` branches that `return` (Issue 3) or Win32 path/junction cases. A Windows-only jail regression can ship green.
- Suggestion: Expand the Windows list to the jail + computer + uninstall + host-bridge modules, or run the full suite on `windows-latest` (this product is Windows-first).
- Status: open

### Issue 5 -- Severity: suggestion
- File: CHANGELOG.md:5
- Description: `[Unreleased]` is empty. Version surfaces still say **0.23.2**. Local `master` HEAD `ccfc652f` is three commits after origin `8cd8918` (`feat: host bridge, first-home stretch, and vendor-neutral GPU`; `fix: keep Remedy on task…`; `fix: stop local-model tool loops and close files-API jail holes`). `docs/manual/13-whats-new.md` still presents 0.23.2 as current. Shipping or publishing this tree as 0.23.2 would mislabel new behavior.
- Suggestion: Either bump via `python scripts/sync_version.py` and fill `[Unreleased]` / What's new, or keep the commits unpublished until that gate. Do not `uv publish` / tag `v0.23.2` again from this HEAD.
- Status: open

### Issue 6 -- Severity: suggestion
- File: tests/conftest.py:7
- Description: Shared fixtures are one line: `REMEDY_API_AUTH=0`. There is no default `REMEDY_HOME=tmp_path`, no asyncio loop-scope setting (`pytest-asyncio` 1.x is in dev deps; `[tool.pytest.ini_options]` has no `asyncio_mode`). Isolation is per-test and easy to forget (Issues 1–2). Many files do isolate correctly (`test_secret_store.py`, `test_messengers.py`, `test_rmb_mode.py`); the suite is inconsistent, not uniformly sloppy.
- Suggestion: Autouse fixture that sets `REMEDY_HOME` to a tmp dir unless a test opts into live. Set `asyncio_mode = "auto"` (or keep explicit marks and set `asyncio_default_fixture_loop_scope`).
- Status: open

### Issue 7 -- Severity: suggestion
- File: tests/test_shell_jail_fuzz.py:89
- Description: The `python -c "print(1)"` vector is marked `must_block=False` but the body accepts **either** allow or block (`if hit is not None: assert …; return`). The comment admits the contract is undecided. That row does not lock a behavior. `tests/test_agency_battery.py:26` (`test_agency_battery_prompts_file_exists`) only checks that `prompts.md` contains a keyword — not agency behavior (the following tests in that file *are* real).
- Suggestion: Pick fail-closed or allow for opaque `python -c print` and assert one outcome. Drop or replace the prompts-file existence test.
- Status: open

### Issue 8 -- Severity: suggestion
- File: tests/test_computer_use.py:1102
- Description: `test_computer_capture_api` asserts `r.status_code in (200, 404)` after a capture call. 404 is treated as success when the singleton home does not match `tmp_path`. A broken route that always 404s still passes.
- Suggestion: Isolate `REMEDY_HOME` / the host-bridge singleton so the test expects 200, or split "wired" vs "wrong-home" cases.
- Status: open

### Issue 9 -- Severity: suggestion
- File: scripts/live_settings_matrix.py:614
- Description: Soak check is `mark("config has no sk- secrets", "sk-" not in raw or "api_key" not in raw.lower() or True)`. The trailing `or True` makes the assertion always pass. This is not pytest, but it is a committed live gate that claims to scan secrets.
- Suggestion: Delete `or True`. Fail if a real `sk-` / `xai-` value is present in `config.toml`.
- Status: open

### Issue 10 -- Severity: suggestion
- File: pyproject.toml:130
- Description: mypy is scoped to `core` / `execution` / `tools` plus a few interface helpers, then **excludes** the files most likely to hide stream/tool bugs: `agent.py`, `agent_react_loop.py`, `react_loop/loop.py`, `react_loop/tool_batch.py`, `react_turn.py`, `agent_llm.py`, `build_*`, `learning*`. `interfaces/routes/` (sessions, settings, stream) is not in `files` at all. CI `uv run mypy` therefore cannot catch type errors on the ReAct loop or HTTP routes. `ignore_missing_imports = true` further softens the gate. Ruff ignores (`E501`, `SIM102`, `B005`, and a large `scripts/*` list including `B018`/`B023`/`B904`) are mostly style; they do not hide the mypy hole.
- Suggestion: Shrink the exclude list starting with `tool_batch.py` / `react_turn.py`. Add `interfaces/routes` incrementally. Keep Win32 modules excluded on Linux if needed; type-check them on the Windows job.
- Status: open

### Issue 11 -- Severity: suggestion
- File: .github/workflows/ci.yml:71
- Description: Desktop job is `npm test` + `npm run build` on Ubuntu only. No `cargo test` / `cargo clippy` for `desktop/src-tauri` (browser host, PTY, updater). No Windows desktop job. Release workflow builds NSIS but does not run the Python/desktop test jobs first. Local `docs/_full_product_soak_results.json` (gitignored) recorded 4 soak failures on `6640bf44` — not a CI signal, but a reminder that green Linux pytest ≠ product soak.
- Suggestion: Add a `cargo test --manifest-path desktop/src-tauri/Cargo.toml` step (or `cargo clippy -D warnings` on a subset). Optionally `needs: [test, desktop]` on `desktop-release` so a red suite cannot tag.
- Status: open

### Issue 12 -- Severity: suggestion
- File: .gitignore:53
- Description: Logs, `dist/`, `desktop/bin/`, `_e2e_simple_c_pytest/`, `docs/_soak*`, and most `docs/_*` dumps are already ignored — good. Two root folders created by bad `Path("~")` / `Users\...` joins are **not** ignored: `Users/` and `~/`. `git add .` would stage them. Underscore one-shots in `scripts/` are only partially ignored (`_cdp_`, `_inspect_`, `_test_`, `_verify_`); origin already tracks `_split_form_sections.py`, `_desktop_send_once.py`, `_start_serve.py`, etc. Local-only extras (`_abort_all_sessions.py`, `_desktop_break_now.py`, `_desktop_stream_probe.py`, `_break_probe.txt`) sit next to them. `.github/workflows/self-improve-bot.yml` and `scripts/self_improve_pr_guard.py` exist locally but are **404 on origin/master**.
- Suggestion: Add `/Users/` and `/~/` to `.gitignore`. Ignore `scripts/_*.txt` and decide which `_*.py` are product vs scratch. Push or drop the self-improve workflow so CI and the tree match.
- Status: open

### Issue 13 -- Severity: nit
- File: pyproject.toml:9
- Description: `license = { text = "LicenseRef-Proprietary" }` while `LICENSE` + `README.md` describe a source-available free grant (sub-$1M / &lt;20 FTE). Not a legal contradiction — SPDX has no identifier for this license — but PyPI metadata reads "proprietary" with no pointer. `COMMERCIAL.md` is the right human summary.
- Suggestion: Keep `LicenseRef-Proprietary`; add `license-files = ["LICENSE"]` if the uv/hatchling version allows it so the wheel carries LICENSE.
- Status: open

### Issue 14 -- Severity: nit
- File: scripts/latest.json:7
- Description: Committed updater payload has `"signature": ""`. Version URL shape (`Remedy.Desktop_0.23.2_x64-setup.exe`) is correct. The desktop-release workflow regenerates `latest.json` from the real `.sig`. The repo file is a template; it must never be uploaded as the GitHub Release asset.
- Suggestion: Document in `scripts/latest.json` (or DESKTOP.md) that an empty signature is expected in-tree. Release job already fails if `.sig` is missing — keep that.
- Status: open

### Issue 15 -- Severity: nit
- File: tests/test_packaging_policy.py:36
- Description: `test_no_gguf_committed_under_resources_local` `return`s if `desktop/resources/local` is missing, so a deleted README/dir would skip the GGUF guard. The sibling `test_local_resources_readme_documents_first_run_download` would still fail if the README vanished — residual risk is low.
- Suggestion: `assert local.is_dir()` instead of silent return.
- Status: open

### Issue 16 -- Severity: nit
- File: tests/test_autoupdate_hooks.py:18
- Description: Update-pipeline tests are source greps (`hooks.nsh` / `lib.rs` must contain marker strings). That is documented and useful as a contract fence, not a substitute for `scripts/test_autoupdate_pipeline.ps1`. Not a false pass unless someone leaves the strings and breaks the control flow.
- Suggestion: Keep; do not treat green pytest as proof the NSIS updater works.
- Status: open

## What is in good shape

- **Version alignment:** `pyproject.toml`, `desktop/package.json`, `desktop/src-tauri/tauri.conf.json`, `Cargo.toml`, `scripts/latest.json`, `remedy.__version__` (reads pyproject from source), README, and `docs/manual/13-whats-new.md` all say 0.23.2. `scripts/sync_version.py` covers the right surfaces including `package-lock.json` and Cargo.lock `app`.
- **CI is present:** `.github/workflows/ci.yml` (3.12/3.13 Linux: ruff, mypy, import smoke, `check_docs.py`, full pytest; Windows subset; desktop npm test + build). `.github/workflows/desktop-release.yml` stamps version, builds sidecar + NSIS, renames `Remedy Desktop` → `Remedy.Desktop_*`, requires a minisign `.sig`.
- **Tests are mostly real:** ~193 `test_*.py` files, 1600+ `def test_*` plus parametrize (`test_shell_jail_fuzz.py` ~40 vectors, `test_provider_sanitize.py` 7 secrets). README claims `560+ tests; currently ~1991` and `check_docs.py` enforces |claimed − collected| ≤ 25. Assertions in jail, auth, SSRF, secret-store, and build-engine tests check outcomes, not `assert True`.
- **Origin is not polluted:** GitHub `master` at `8cd8918` does not contain `Users/`, `~/`, `*.log`, `dist/*.whl`, `desktop/src-tauri/target`, or `community/remedy-skills/dist/*.zip`. Those are local (mostly gitignored).
- **License/commercial:** `LICENSE` + `COMMERCIAL.md` match; no live API keys found in tracked source (test/docs use placeholders).
- **Packaging policy tests** exist so GGUF models cannot sneak into the installer (`test_packaging_policy.py`, `test_clean_home_smoke.py`).

## Hygiene inventory

Local junk that must not be committed or shipped. Most is already gitignored; listed so a sloppy `git add -f` / copy does not leak it.

### Already gitignored (working-tree dirt only)

| Path | Notes |
|------|--------|
| `*.log` at repo root (`bbt.log`, `bbt2.log`, `both.log`, `bugsweep.log`, `chunk1-3.log`, `ci-fail.log`, `ci-fail2.log`, `direct.log`, `dump.log`, `dump2.log`, `dump3.log`, `harness.log`, `harness2.log`, `one.log`, `pytest_chunk1.log`, `pytest_verbose.log`, `react.log`, `react2.log`, `two.log`) | Dev/CI scrapes |
| `collect.txt`, `grep_loop.out` | Grep leftovers |
| `dist/` old wheels `remedy_ai-0.18.0` … `0.23.2` plus `Remedy Desktop_0.21.1` / `0.22.3` and dotted `Remedy.Desktop_*` installers | Never commit; do not upload the space-named EXE as the release asset |
| `desktop/bin/` (`remedy-desktop.exe`, `.fatbak`, `.prod`, extra triples, staged `webui/`) | Sidecar + fat backups |
| `desktop/dist/`, `desktop/node_modules/` | Vite output / npm (desktop `.gitignore`) |
| `desktop/src-tauri/target/` | Rust debug+release (crate `.gitignore`) |
| `desktop/tauri-local-build.json`, `desktop/tauri-local-test.json` | Local tauri overrides |
| `build/dev_sidecar/`, `build/pyinstaller/` | PyInstaller work dirs |
| `_e2e_simple_c_pytest/` (`hello.c`, `hello.exe`) | Live e2e residue |
| `docs/_bugsweep_review.md`, `docs/_full_product_soak_results.json`, `docs/_open_issues_final.json`, `docs/_redteam_live_results.json`, `docs/_soak_probe_results.json`, `docs/_soak_next/*` | Soak/red-team dumps |
| `community/remedy-skills/dist/` (282 zips) | Regenerated catalog artifacts |
| `tests/__pycache__/`, `src/remedy/**/__pycache__/`, `scripts/__pycache__/`, `examples/__pycache__/` | Bytecode |

### Not gitignored (would stage with `git add .`)

| Path | Notes |
|------|--------|
| `Users/Administrator/` | Empty accidental tree from a `Users\...` path join. **Ignore `/Users/`.** |
| `~/` | Empty literal-tilde directory from `Path("~")` instead of `Path.home()`. **Ignore `/~/`.** |
| `scripts/_abort_all_sessions.py`, `_desktop_break_now.py`, `_desktop_stream_probe.py`, `_break_probe.txt` | Local one-shots not on origin |
| `.github/workflows/self-improve-bot.yml`, `scripts/self_improve_pr_guard.py`, `scripts/self_improve_security_scan.py` | Present locally; **not on origin/master** |

### Tracked on origin and look like one-off debug (product-adjacent)

These are already in GitHub `master`. Consider moving to `scripts/soak/` or deleting if unused:

- `scripts/_split_form_sections.py` — one-shot TS split; still mutates `FormSections.tsx`
- `scripts/_desktop_send_once.py`, `_start_serve.py`, `_spawn_serve_breakaway.py`, `_serve_watchdog.py`
- `scripts/_full_product_soak.py`, `_soak_run.py`, `_soak_drive.py`, `_open_issues_final.py`, `_prove_write_jail.py`, `_redteam_live_probes.py`
- `scripts/live_*.py`, `scripts/stress_*.py` — intentional soak battery; keep, but do not treat as CI

### Do not ship in the wheel / installer

`uv_build` module is `src/remedy` — root `dist/`, `Users/`, logs, desktop `target/`, and community zips are **outside** the Python package. Tauri `bundle.resources` maps `desktop/dist` → `webui` and the sidecar exe only (`desktop/src-tauri/tauri.conf.json:63`). Residual risk is human: copying `desktop/bin/remedy-desktop.exe.fatbak` or a space-named NSIS exe into a release.

### Git status (as far as this review can see)

- **Branch:** `master` @ `ccfc652f`
- **Origin `master`:** `8cd8918` (`release: 0.23.2 ship UI as Remedy Desktop.exe`)
- **Local is 3 commits ahead** (unpublished host-bridge / agency / files-jail work)
- **Working tree:** heavily dirty with ignored artifacts; plus unignored `Users/`, `~/`, and likely uncommitted self-improve + extra `_` scripts
- **No evidence of tracked secrets** in the origin file list

## Tests layout (spot-check)

| Kind | Examples | Notes |
|------|----------|--------|
| Unit (majority) | `test_file_edit.py`, `test_project_write_jail.py`, `test_web_fetch_ssrf.py`, `test_secret_store.py`, `test_build_engine.py` | Isolated `tmp_path` / mocks; real assertions |
| Integration-ish | `test_session_stream.py`, `test_react_stream.py`, `test_api_auth.py` | FastAPI `TestClient`; still in-process |
| Live (1 file) | `test_e2e_simple_c_rmb.py` | Marked `live` + skipif; see Issue 1 |
| Source-contract | `test_autoupdate_hooks.py`, `test_docs_sync.py`, `test_packaging_policy.py` | Grep / docs gate; fine if labeled |
| Phase leftovers | `test_phase1.py` … `test_phase7.py` | Older memory/agent units; still execute real code |

`tests/conftest.py` is 10 lines. Desktop has a separate Vitest suite under `desktop/src/**/*.test.ts` (CI `npm test`). No cargo tests.

## Packaging / license checklist

| Surface | 0.23.2? |
|---------|---------|
| `pyproject.toml` `version` | yes |
| `desktop/package.json` | yes |
| `desktop/src-tauri/tauri.conf.json` | yes |
| `desktop/src-tauri/Cargo.toml` | yes |
| `scripts/latest.json` `version` + `Remedy.Desktop_*` URL | yes (empty signature — Issue 14) |
| `CHANGELOG.md` `## [0.23.2]` | yes (Unreleased empty — Issue 5) |
| `docs/manual/13-whats-new.md` | yes |
| `LICENSE` / `COMMERCIAL.md` | present; source-available free grant |
| PyPI name | `remedy-ai` (not occupied `remedy`) |

Installer naming in AGENTS.md / desktop-release.yml matches `Remedy.Desktop_{ver}_x64-setup.exe`. `mainBinaryName` is `Remedy Desktop` (0.23.2 Defender fix).
