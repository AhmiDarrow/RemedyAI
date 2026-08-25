"""Tools: build_status, build_resume, build_unit_hop — machine construction surface."""

from __future__ import annotations

import contextlib
import json
from typing import Any


def register_build_tools(runtime: Any) -> None:
    """Register build-engine tools on the runtime registry."""

    def _home():
        return getattr(getattr(runtime, "config", None), "home_dir", None)

    def _project() -> str:
        try:
            return str(runtime.effective_project_path() or "")
        except Exception:
            return ""

    async def build_status() -> str:
        """Show live build engine + on-disk ledger (machine construction state)."""
        from remedy.core.build_engine import get_build_state
        from remedy.core.build_ledger import load_ledger, resume_hint
        from remedy.core.build_oracle import discover_verify_command

        st = get_build_state(runtime)
        proj = _project()
        led = load_ledger(proj or None, home=_home())
        lines = ["**Build engine**"]
        if st is not None and st.active:
            pub = st.public()
            lines.append(
                f"live: phase={pub['phase']} explore={pub['explore_steps']} "
                f"write={pub['write_steps']} verify={pub['verify_steps']} "
                f"verify_ok={pub['last_verify_ok']} auto_verify={pub['auto_verify_ran']} "
                f"syntax_ok={pub.get('syntax_ok')}"
            )
            lines.append(f"oracle: {pub.get('verify_command') or '(none)'}")
            if pub.get("write_set"):
                lines.append("write_set: " + ", ".join(pub["write_set"]))
            if pub.get("paths"):
                lines.append("paths: " + ", ".join(pub["paths"]))
        else:
            lines.append("live: (no active build turn)")
        if led is not None:
            lines.append(
                f"ledger: phase={led.phase} writes={led.write_steps} "
                f"verify_ok={led.last_verify_ok} cmd={led.verify_command or '—'}"
            )
            if led.hops:
                h = led.hops[-1]
                lines.append(
                    f"last_hop: {h.get('unit_id') or h.get('path')} ok={h.get('ok')}"
                )
        else:
            lines.append("ledger: (empty)")
        cmd = discover_verify_command(runtime)
        if cmd:
            lines.append(f"discovered_verify: {cmd}")
        hint = resume_hint(proj or None, home=_home())
        if hint:
            lines.append("")
            lines.append(hint)
        return "\n".join(lines)

    async def build_resume() -> str:
        """Inject resume context from the build ledger for the current project."""
        from remedy.core.build_engine import begin_build_turn
        from remedy.core.build_ledger import load_ledger, resume_hint

        proj = _project()
        led = load_ledger(proj or None, home=_home())
        if led is None:
            return "No build ledger for this project."
        # Force a build turn using ledger goal
        begin_build_turn(
            runtime,
            led.goal or "continue build",
            force=True,
        )
        hint = resume_hint(proj or None, home=_home())
        return hint or json.dumps(led.to_dict(), indent=2)[:2000]

    async def build_unit_hop(
        path: str = "",
        behavior: str = "",
        symbol: str = "",
        source: str = "",
        use_llm: bool = False,
        max_repairs: int = 3,
        tests: str = "",
        patch_symbol: str = "",
    ) -> str:
        """Live-disk unit hop: structural + behavioral oracle + import dry-run.

        path= required. source= optional body; use_llm=true = stateless model
        fill/repair. tests= optional pytest source (A). patch_symbol= AST-minimal
        replace (G). Machine owns write + oracle + snapshot (E).
        """
        from remedy.core.build_live_hop import live_unit_hop

        rel = (path or "").strip()
        if not rel:
            return "path= required (unit file to build/check)"
        try:
            repairs = max(1, min(8, int(max_repairs or 3)))
        except (TypeError, ValueError):
            repairs = 3
        res = live_unit_hop(
            runtime,
            path=rel,
            behavior=behavior or "",
            symbol=symbol or "",
            source=source or "",
            use_llm=bool(use_llm),
            max_repairs=repairs,
            tests=tests or "",
            patch_symbol=patch_symbol or "",
        )
        if res.get("phase") == "scout" or (
            not res.get("ok") and res.get("error") and not res.get("written")
        ):
            ctx = res.get("context") or ""
            err = res.get("error") or "no source"
            return (
                "Unit hop SCOUT context (no source yet — implement then re-call "
                f"with source= or use_llm=true):\n{ctx}\npath={rel}\n({err})"
            )
        ok = bool(res.get("ok"))
        sym = res.get("symbol") or symbol or rel
        written = res.get("written")
        errs = res.get("errors") or []
        imp = res.get("import") or {}
        if ok:
            imp_note = ""
            if imp:
                imp_note = f" import_ok={imp.get('ok')}"
            beh = " behavioral" if res.get("behavioral") else ""
            snap = f" snap={res.get('snap_id')}" if res.get("snap_id") else ""
            return (
                f"build_unit_hop OK unit={sym} path={res.get('path', rel)} "
                f"written={written} attempts={res.get('attempts')}{imp_note}{beh}{snap}\n"
                f"structural + import"
                f"{' + behavioral' if res.get('behavioral') else ''} oracle green.\n"
                f"{(res.get('context') or '')[:400]}"
            )
        err_lines = "\n".join(f"- {e}" for e in errs[:12])
        return (
            f"build_unit_hop RED unit={sym} path={res.get('path', rel)} "
            f"written={written} attempts={res.get('attempts')}\n"
            f"Oracle errors:\n{err_lines}\n"
            "file_edit / re-run build_unit_hop with source= or use_llm=true."
        )

    async def build_live_project(
        units_json: str = "",
        use_llm: bool = True,
        max_repairs: int = 4,
    ) -> str:
        """Multi-unit live reducer: materialize + disk oracle + import dry-run."""
        from remedy.core.build_live_hop import live_build_project

        raw = (units_json or "").strip()
        if not raw:
            return "units_json= required (JSON array of {path,symbol,behavior,...})"
        try:
            units = json.loads(raw)
        except json.JSONDecodeError as e:
            return f"units_json parse error: {e}"
        if not isinstance(units, list):
            return "units_json must be a JSON array of unit objects"
        try:
            repairs = max(1, min(10, int(max_repairs or 4)))
        except (TypeError, ValueError):
            repairs = 4
        res = live_build_project(
            runtime,
            units,
            use_llm=bool(use_llm),
            max_repairs=repairs,
        )
        if res.get("error") and not res.get("files"):
            return f"build_live_project failed: {res.get('error')}"
        ok = bool(res.get("ok"))
        lines = [
            f"build_live_project {'OK' if ok else 'RED'}",
            f"files={res.get('files')}",
            f"iterations={res.get('iterations')} repaired={res.get('repaired')}",
            (res.get("summary") or "")[:800],
        ]
        fails = res.get("failures") or []
        if fails:
            lines.append("failures:")
            for f in fails[:8]:
                if isinstance(f, dict):
                    lines.append(
                        f"  · {f.get('path')}: {(f.get('error') or '')[:200]}"
                    )
                else:
                    lines.append(f"  · {f}")
        imps = res.get("import_results") or []
        bad_imp = [r for r in imps if isinstance(r, dict) and not r.get("ok")]
        if bad_imp:
            lines.append("import dry-run red:")
            for r in bad_imp[:6]:
                lines.append(f"  · {r.get('module')}: {(r.get('error') or '')[:160]}")
        return "\n".join(lines)[:4000]

    async def build_mutation_score() -> str:
        """Import-cone mutation score for current write_set (scoped verify feed)."""
        from pathlib import Path

        from remedy.core.build_engine import get_build_state
        from remedy.core.build_import_graph import mutation_score_paths

        proj = _project()
        if not proj:
            return "No project path — set project first."
        root = Path(proj)
        if root.is_file():
            root = root.parent
        write_set: list[str] = []
        st = get_build_state(runtime)
        if st is not None and getattr(st, "write_set", None):
            write_set = list(st.write_set)
        # Fall back to ledger hop paths
        if not write_set:
            with contextlib.suppress(Exception):
                from remedy.core.build_ledger import load_ledger

                led = load_ledger(proj, home=_home())
                if led is not None:
                    for h in (led.hops or [])[-20:]:
                        p = h.get("path") if isinstance(h, dict) else None
                        if p and p not in write_set:
                            write_set.append(str(p))
                    for p in getattr(led, "paths", None) or []:
                        if p and p not in write_set:
                            write_set.append(str(p))
        if not write_set:
            return (
                "No write_set yet. Edit files in a build turn, or run "
                "build_unit_hop / build_live_project first."
            )
        ms = mutation_score_paths(root, write_set)
        with contextlib.suppress(Exception):
            if st is not None:
                st.last_mutation_score = ms
            runtime._last_mutation_score = ms
        lines = [
            "**Mutation score (import cone)**",
            f"seeds: {', '.join(ms.get('seed_mods') or []) or '—'}",
            f"cone_mods ({len(ms.get('cone_mods') or [])}): "
            + ", ".join((ms.get("cone_mods") or [])[:20]),
            f"cone_paths: {', '.join((ms.get('cone_paths') or [])[:12]) or '—'}",
            f"mutation_score={ms.get('mutation_score')} "
            f"(graph_modules={ms.get('graph_modules')})",
            "Scoped verify expands write_set with this cone before pytest selection.",
        ]
        return "\n".join(lines)

    async def build_compile_spec(goal: str = "") -> str:
        """B: Compile goal → locked BuildSpec DAG (machine-owned API surface)."""
        from pathlib import Path

        from remedy.core.build_spec_compiler import compile_goal_to_spec, save_locked_spec

        g = (goal or "").strip()
        if not g:
            st = None
            with contextlib.suppress(Exception):
                from remedy.core.build_engine import get_build_state

                st = get_build_state(runtime)
            g = str(getattr(st, "goal", "") or "") if st else ""
        if not g:
            return "goal= required (or active build turn goal)"
        proj = _project()
        root = Path(proj) if proj else None
        if root and root.is_file():
            root = root.parent
        compiled = compile_goal_to_spec(g, root=root)
        if compiled.get("ok") and root:
            save_locked_spec(root, compiled)
        return json.dumps(compiled, indent=2)[:4000]

    async def build_tdd(goal: str = "", use_llm: bool = False) -> str:
        """H: TDD-as-OS — write failing tests first, optional implement hops."""
        from remedy.core.build_engine import begin_build_turn, get_build_state
        from remedy.core.build_tdd import tdd_bootstrap

        g = (goal or "").strip()
        if not g:
            st = get_build_state(runtime)
            g = str(getattr(st, "goal", "") or "") if st else ""
        if not g:
            return "goal= required"
        begin_build_turn(runtime, g, force=True)
        res = tdd_bootstrap(runtime, g, use_llm_implement=bool(use_llm))
        return (
            f"{res.get('message')}\n"
            f"lock={res.get('lock')}\n"
            f"tests={((res.get('tdd') or {}).get('written'))}\n"
            f"units={len((res.get('compiled') or {}).get('units') or [])}"
        )[:4000]

    async def build_gate_tower() -> str:
        """F: Run gate tower L0→L4 on current write_set."""

        from remedy.core.build_engine import get_build_state
        from remedy.core.build_gate_tower import run_gate_tower

        st = get_build_state(runtime)
        write_set: list[str] = list(getattr(st, "write_set", None) or []) if st else []
        if not write_set:
            proj = _project()
            if proj:
                # fallback: recent py files is wrong — require write_set
                pass
        if not write_set:
            return "No write_set — edit files or hop first."
        base = ""
        if st is not None:
            base = str(getattr(st, "verify_command", "") or "")
        res = run_gate_tower(runtime, write_set, base_verify=base)
        if st is not None:
            st.last_gate_tower = res
            if not res.get("ok"):
                st.syntax_ok = False
        lines = [res.get("message") or ""]
        for r in res.get("results") or []:
            mark = "OK" if r.get("ok") else "RED"
            lines.append(f"  [{mark}] {r.get('level')}: {(r.get('summary') or '')[:160]}")
        return "\n".join(lines)[:4000]

    async def build_repair_queue(run_hops: bool = False, use_llm: bool = False) -> str:
        """C: Schedule repair targets from last error vector; optional auto hops."""
        from remedy.core.build_engine import get_build_state
        from remedy.core.build_repair_queue import (
            format_repair_queue_message,
            queue_from_error_vector,
            run_auto_repair_hops,
        )

        st = get_build_state(runtime)
        vec = getattr(st, "last_error_vector", None) if st else None
        if not isinstance(vec, dict):
            vec = {}
        ws = list(getattr(st, "write_set", None) or []) if st else []
        q = queue_from_error_vector(vec, write_set=ws, root=_project() or None)
        if st is not None:
            st.repair_queue = q.to_public()
        msg = format_repair_queue_message(q)["content"]
        if run_hops and q.targets:
            ran = run_auto_repair_hops(
                runtime, q, use_llm=bool(use_llm), max_targets=3, max_repairs=2
            )
            msg += f"\n\nauto_hops ran={ran.get('ran')} ok={ran.get('ok')}"
        return msg[:4000]

    async def build_mutant_score() -> str:
        """D: True mutant kill score (surface mutants under tests)."""
        from pathlib import Path

        from remedy.core.build_engine import get_build_state
        from remedy.core.build_mutant import mutant_kill_score
        from remedy.core.build_scoped import map_source_to_test_candidates

        proj = _project()
        if not proj:
            return "No project path."
        root = Path(proj)
        if root.is_file():
            root = root.parent
        st = get_build_state(runtime)
        write_set = list(getattr(st, "write_set", None) or []) if st else []
        if not write_set:
            return "No write_set — hop/edit first."
        test_paths: list[str] = []
        for w in write_set:
            for tp in map_source_to_test_candidates(
                str(w).replace("\\", "/"), root
            ):
                try:
                    test_paths.append(tp.relative_to(root).as_posix())
                except Exception:
                    test_paths.append(str(tp))
        # also tests/ dir
        if (root / "tests").is_dir() and not test_paths:
            test_paths = ["tests"]
        res = mutant_kill_score(root, write_set, test_command_paths=test_paths or None)
        if st is not None:
            st.last_mutant_kill = res
        return (
            f"{res.get('message')}\n"
            f"killed={res.get('killed')} survived={res.get('survived')} "
            f"rate={res.get('kill_rate')} strong={res.get('strong')}\n"
            + "\n".join(
                f"  · {d.get('file')} {d.get('mutant')}: {d.get('status')}"
                for d in (res.get("details") or [])[:12]
            )
        )[:4000]

    async def build_snapshot(action: str = "list", snap_id: str = "") -> str:
        """E: Hop snapshots — list / restore / bisect last green."""
        from pathlib import Path

        from remedy.core.build_snapshot import (
            bisect_red_wave,
            last_green_snapshot,
            load_manifest,
            restore_snapshot,
        )

        proj = _project()
        if not proj:
            return "No project."
        root = Path(proj)
        if root.is_file():
            root = root.parent
        act = (action or "list").strip().lower()
        if act == "list":
            snaps = load_manifest(root)
            if not snaps:
                return "No snapshots yet (created on each live hop)."
            lines = ["**Build snapshots**"]
            for s in snaps[-12:]:
                lines.append(
                    f"  · {s.get('snap_id')} ok_after={s.get('ok_after')} "
                    f"paths={s.get('paths')} note={s.get('note')}"
                )
            return "\n".join(lines)
        if act == "restore":
            if not snap_id:
                g = last_green_snapshot(root)
                snap_id = str((g or {}).get("snap_id") or "")
            if not snap_id:
                return "snap_id= required (or no last green)"
            return json.dumps(restore_snapshot(root, snap_id), indent=2)[:2000]
        if act == "bisect":
            return json.dumps(bisect_red_wave(root), indent=2)[:2000]
        return "action= list|restore|bisect"

    async def build_symbol_index_tool() -> str:
        """G: Disk-wide symbol index (linker) for current project."""
        from pathlib import Path

        from remedy.core.build_symbol_index import build_symbol_index

        proj = _project()
        if not proj:
            return "No project."
        root = Path(proj)
        if root.is_file():
            root = root.parent
        idx = build_symbol_index(root)
        pub = idx.to_public()
        lines = [
            f"**Symbol index** files={pub['files']} symbols={pub['symbols']}",
            "sample: " + ", ".join(pub.get("sample") or [])[:200],
        ]
        return "\n".join(lines)

    async def todo_write(todos_json: str = "", merge: bool = True) -> str:
        """Create or update the turn/project build checklist (Claude-class)."""
        from remedy.core.build_todos import format_todos_block, load_todos, upsert_todos

        if isinstance(todos_json, list | dict):
            todos_json = json.dumps(todos_json, ensure_ascii=False, default=str)
        raw = str(todos_json or "").strip()
        if not raw:
            items = load_todos(runtime)
            return format_todos_block(items) or "No todos yet. Pass todos_json=[{id,content,status}]."
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            return f"todos_json parse error: {e}"
        if isinstance(parsed, dict):
            parsed = parsed.get("todos") or parsed.get("items") or [parsed]
        if not isinstance(parsed, list):
            return "todos_json must be a JSON array of {id,content,status}"
        items = upsert_todos(runtime, parsed, merge=bool(merge))
        block = format_todos_block(items)
        return block or "Todos updated (empty)."

    async def todo_read() -> str:
        """Show the active build checklist."""
        from remedy.core.build_todos import format_todos_block, load_todos

        return format_todos_block(load_todos(runtime)) or "No todos."

    async def build_drive(
        goal: str = "",
        use_llm: bool = False,
        max_units: int = 8,
        max_repairs: int = 3,
    ) -> str:
        """Machine-owned implement-verify-fix: spec → TDD → hops → gates → repair."""
        from remedy.core.build_drive import drive_build

        res = drive_build(
            runtime,
            goal=goal or "",
            use_llm=bool(use_llm),
            max_units=max_units,
            max_repairs=max_repairs,
        )
        if res.get("error") and not res.get("tdd"):
            return f"build_drive failed: {res.get('error')}"
        lines = [res.get("message") or json.dumps({k: res.get(k) for k in ('ok', 'phase')})]
        dg = res.get("drive_to_green") or {}
        if dg.get("message"):
            lines.append(f"Drive-to-green: {dg['message']}")
        hops = res.get("hops") or []
        for h in hops[:10]:
            if isinstance(h, dict):
                mark = "OK" if h.get("ok") else "RED"
                lines.append(f"  [{mark}] {h.get('path')} {h.get('error') or ''}".rstrip())
        review = res.get("review") or {}
        if review.get("message"):
            lines.append(str(review["message"]))
        return "\n".join(lines)[:4000]

    async def apply_patch(patch: str = "") -> str:
        """Apply a unified diff or Begin-Patch block through the write jail."""
        from remedy.core.build_apply_patch import apply_patch_text

        raw = (patch or "").strip()
        if not raw:
            return "patch= required (unified diff or *** Begin Patch block)"
        res = apply_patch_text(runtime, raw)
        if not res.get("ok"):
            return f"apply_patch RED: {res.get('error')}"
        files = ", ".join(
            f"{a.get('action')} {a.get('path')}" for a in (res.get("applied") or [])
        )
        return f"apply_patch OK files={res.get('files')} {files}"

    async def build_parallel(units_json: str = "", use_llm: bool = False) -> str:
        """Isolated parallel hops — merge a unit only if its oracle is green."""
        from remedy.core.build_isolated import parallel_isolated_hops

        raw = (units_json or "").strip()
        if not raw:
            return "units_json= required (JSON array of {path,symbol,behavior,...})"
        try:
            units = json.loads(raw)
        except json.JSONDecodeError as e:
            return f"units_json parse error: {e}"
        if not isinstance(units, list):
            return "units_json must be a JSON array"
        hops = parallel_isolated_hops(runtime, units, use_llm=bool(use_llm))
        ok_n = sum(1 for h in hops if h.get("ok"))
        lines = [f"build_parallel hops={ok_n}/{len(hops)}"]
        for h in hops[:12]:
            mark = "OK" if h.get("ok") else "RED"
            lines.append(
                f"  [{mark}] {h.get('path')} merged={h.get('merged')} "
                f"{h.get('error') or ''}".rstrip()
            )
        return "\n".join(lines)[:4000]

    async def build_review_fix(use_llm: bool = False) -> str:
        """Second pass over the write set: findings + isolated hops on errors."""
        from remedy.core.build_engine import get_build_state
        from remedy.core.build_review_fix import review_fix_pass

        st = get_build_state(runtime)
        ws = list(getattr(st, "write_set", None) or []) if st else []
        res = review_fix_pass(runtime, ws, use_llm=bool(use_llm))
        if st is not None:
            st.review_fix_ran = True
        lines = [str(res.get("message") or "review_fix")]
        for f in (res.get("findings") or [])[:12]:
            lines.append(f"  · {f.get('severity')} {f.get('kind')} {f.get('path')}: {f.get('detail')}")
        return "\n".join(lines)[:4000]

    runtime.tool_registry.register_builtin_handler(
        "todo_write",
        "Create or update a short build checklist (pending|in_progress|completed|"
        "cancelled). todos_json=[{id,content,status}]. merge=true updates by id. "
        "Use at the start of multi-step implement work; mark done as you go. "
        "Do not claim finished while todos are still pending.",
        todo_write,
        {
            "type": "object",
            "properties": {
                "todos_json": {
                    "description": 'JSON array e.g. [{"id":"1","content":"add parser","status":"in_progress"}]',
                    "anyOf": [
                        {"type": "string"},
                        {
                            "type": "array",
                            "items": {"type": "object"},
                        },
                    ],
                },
                "merge": {
                    "type": "boolean",
                    "description": "true=update by id (default); false=replace list",
                    "default": True,
                },
            },
            "required": [],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "todo_read",
        "Show the active build checklist (from .remedy-build/todos.json).",
        todo_read,
        {"type": "object", "properties": {}},
    )
    runtime.tool_registry.register_builtin_handler(
        "build_drive",
        "Machine-owned implement-VERIFY-fix loop that drives to actually-green: "
        "compile spec, write failing TDD tests, hop units (use_llm=true fills/"
        "repairs via the turn model), then LOOP gate-tower verify → auto-repair "
        "→ re-verify until the tests pass, a repair budget is hit, or progress "
        "stalls (it reports which). Does not stop at first red. Prefer this over "
        "a long plan monologue when the user asked to implement/build; read "
        "drive_to_green in the result and continue only if it's not yet green.",
        build_drive,
        {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "Override active build-turn goal"},
                "use_llm": {
                    "type": "boolean",
                    "description": "Stateless LLM hops (default false; live loop may auto-enable)",
                },
                "max_units": {"type": "integer"},
                "max_repairs": {"type": "integer"},
            },
            "required": [],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "apply_patch",
        "Apply a unified diff or *** Begin Patch / *** Update File: block "
        "through the write jail. Unique hunks only; refuses partial file applies.",
        apply_patch,
        {
            "type": "object",
            "properties": {"patch": {"type": "string"}},
            "required": ["patch"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "build_parallel",
        "Hop independent units in isolated overlays (parallel). Each unit merges "
        "to the live tree only if its oracle is green — siblings cannot corrupt "
        "each other. units_json=[{path,symbol,behavior,source?}].",
        build_parallel,
        {
            "type": "object",
            "properties": {
                "units_json": {"type": "string"},
                "use_llm": {"type": "boolean"},
            },
            "required": ["units_json"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "build_review_fix",
        "Second pass over the write set: TODO/bare-except/syntax/missing-test "
        "findings, then isolated hops on error-severity items.",
        build_review_fix,
        {
            "type": "object",
            "properties": {"use_llm": {"type": "boolean"}},
            "required": [],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "build_status",
        "Show machine build engine + on-disk ledger (phase, oracle command, "
        "verify results, resume hint). Use during implement/ship work.",
        build_status,
        {"type": "object", "properties": {}},
    )
    runtime.tool_registry.register_builtin_handler(
        "build_resume",
        "Resume a mid-ship build from the project build ledger. Forces build "
        "engine supervision and returns the resume block.",
        build_resume,
        {"type": "object", "properties": {}},
    )
    runtime.tool_registry.register_builtin_handler(
        "build_unit_hop",
        "Live-disk unit hop: structural + behavioral oracle, import dry-run, "
        "snapshot. path= required; source= optional; tests= unit pytest; "
        "use_llm= true for stateless fill/repair; patch_symbol= AST-minimal edit.",
        build_unit_hop,
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "behavior": {"type": "string"},
                "symbol": {"type": "string"},
                "source": {"type": "string"},
                "use_llm": {
                    "type": "boolean",
                    "description": "Stateless LLM hop fill/repair (default false).",
                },
                "max_repairs": {"type": "integer"},
                "tests": {
                    "type": "string",
                    "description": "Optional pytest source for behavioral oracle.",
                },
                "patch_symbol": {
                    "type": "string",
                    "description": "AST-replace only this top-level def/class.",
                },
            },
            "required": ["path"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "build_live_project",
        "Multi-unit live reducer: units_json=[{path,symbol,behavior,...}], "
        "use_llm=true runs stateless hops, materializes to disk, import dry-run. "
        "Machine owns oracle loop; model is pure f(context,errors)->source.",
        build_live_project,
        {
            "type": "object",
            "properties": {
                "units_json": {"type": "string"},
                "use_llm": {"type": "boolean"},
                "max_repairs": {"type": "integer"},
            },
            "required": ["units_json"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "build_mutation_score",
        "Import-cone expansion score for write_set (scoped verify feed). "
        "For true mutant kill rate use build_mutant_score.",
        build_mutation_score,
        {"type": "object", "properties": {}},
    )
    # Advanced frontiers A–H (spec compiler, mutants, gate tower, TDD-as-OS, …)
    # stay behind maturity gate so default agency keeps core build tools only.
    from remedy.core.feature_maturity import build_os_advanced_enabled

    if not build_os_advanced_enabled():
        return

    runtime.tool_registry.register_builtin_handler(
        "build_compile_spec",
        "B: Compile goal into locked BuildSpec DAG (declare/requires/tests). "
        "Machine owns API surface; model only fills bodies.",
        build_compile_spec,
        {
            "type": "object",
            "properties": {"goal": {"type": "string"}},
            "required": [],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "build_tdd",
        "H: TDD-as-OS — compile spec, write failing tests first, optional "
        "use_llm implement hops. Order: red tests → implement → gates → DONE.",
        build_tdd,
        {
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "use_llm": {"type": "boolean"},
            },
            "required": [],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "build_gate_tower",
        "F: Gate tower L0 syntax → L1 static → L2 import → L3 unit → L4 cone. "
        "Cheapest red wins.",
        build_gate_tower,
        {"type": "object", "properties": {}},
    )
    runtime.tool_registry.register_builtin_handler(
        "build_repair_queue",
        "C: Error vector → ranked repair targets. run_hops=true auto live_unit_hop.",
        build_repair_queue,
        {
            "type": "object",
            "properties": {
                "run_hops": {"type": "boolean"},
                "use_llm": {"type": "boolean"},
            },
            "required": [],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "build_mutant_score",
        "D: True mutant kill score — inject surface mutants, re-run tests. "
        "Survivors block strong DONE.",
        build_mutant_score,
        {"type": "object", "properties": {}},
    )
    runtime.tool_registry.register_builtin_handler(
        "build_snapshot",
        "E: Snapshots — action=list|restore|bisect. Localize which hop broke build.",
        build_snapshot,
        {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "snap_id": {"type": "string"},
            },
            "required": [],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "build_symbol_index",
        "G: Disk-wide symbol index (linker defs/refs) for hop closure.",
        build_symbol_index_tool,
        {"type": "object", "properties": {}},
    )
