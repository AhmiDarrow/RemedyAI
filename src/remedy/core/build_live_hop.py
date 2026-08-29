"""Live-disk LLM hop — reducer unit with real filesystem oracle.

The model is a **stateless** function: f(closure, error_vector) → source.
The machine owns disk write, structural oracle, import dry-run, and re-queue.

Supports:
- single hop (one unit, optional LLM fill)
- multi-unit ``build_project`` with live disk materialize + oracle
"""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from typing import Any

from remedy.core.builds.reducer import (
    BuildResult,
    BuildSpec,
    OracleError,
    Signature,
    SymbolRegistry,
    UnitSpec,
    build_project,
    extract_markdown_fence,
    run_oracle,
)


def _project_root(runtime: Any) -> Path:
    root = Path(runtime.effective_project_path())
    if root.is_file():
        root = root.parent
    return root


def _clean_rel(path: str) -> str:
    rel = (path or "").replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def _resolve_write(runtime: Any, rel: str, root: Path) -> Path:
    """Resolve *rel* through the write jail. Never fall open on refusal."""
    if runtime is not None and hasattr(runtime, "resolve_tool_path"):
        try:
            return Path(runtime.resolve_tool_path(rel, for_write=True))
        except TypeError:
            return Path(runtime.resolve_tool_path(rel))
    dest = (root / rel)
    try:
        dest_res = dest.resolve()
        dest_res.relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise PermissionError(f"hop path outside project: {rel}") from exc
    return dest_res


def disk_oracle(unit: UnitSpec, files: dict[str, str]) -> list[OracleError]:
    """Structural oracle against in-memory *files* dict (reducer contract)."""
    src = files.get(unit.path, "")
    return run_oracle(unit, src)


def materialize_files(runtime: Any, files: dict[str, str], root: Path) -> list[str]:
    """Write files to disk via jail; return list of written rel paths."""
    written: list[str] = []
    for rel, body in (files or {}).items():
        dest = _resolve_write(runtime, _clean_rel(rel), root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body or "", encoding="utf-8")
        written.append(_clean_rel(rel))
    return written


def make_runtime_llm_model(runtime: Any, *, max_tokens: int = 4096) -> Any:
    """Stateless ModelFn using the live turn's LLM binding (sync)."""

    def model(unit: UnitSpec, closure: str, errs: list[OracleError]) -> str:
        err_txt = "\n".join(f"- {e.message}" for e in (errs or [])[:12])
        prompt = (
            f"Write the complete source for file `{unit.path}`.\n"
            f"Unit id: {unit.id}\n"
            f"Behavior: {unit.behavior or '(none)'}\n"
            f"Must define: {[s.symbol for s in unit.declare]}\n"
            f"Requires symbols: {unit.requires}\n"
            f"Imports: {unit.imports}\n"
            f"Machine context:\n{closure}\n"
        )
        if err_txt:
            prompt += f"\nPrevious oracle errors to fix:\n{err_txt}\n"
        prompt += "\nOutput ONLY the raw file source. No markdown fences."

        # Prefer local loopback if RMB/local
        text = ""
        with suppress(Exception):
            from remedy.core.llm_binding import get_llm_binding
            from remedy.runtime.rmb.mode import is_rmb_provider

            bind = get_llm_binding(runtime)
            if is_rmb_provider(bind.provider, bind.base_url):
                from remedy.core.builds.reducer import local_llama_model

                fn = local_llama_model(base_url=bind.base_url or "http://127.0.0.1:8080/v1")
                return extract_markdown_fence(fn(unit, closure, errs) or "")

        # Cloud / OpenAI-compatible via adapter (sync request)
        with suppress(Exception):
            from remedy.core.llm_binding import get_llm_binding

            bind = get_llm_binding(runtime)
            adapter = bind.adapter()
            headers = adapter.auth_headers(bind.api_key)
            endpoint = adapter.chat_endpoint(bind.base_url)
            with suppress(Exception):
                from remedy.core.sleev import prepare_llm_http

                endpoint, headers = prepare_llm_http(
                    provider=bind.provider,
                    base_url=bind.base_url,
                    api_key=bind.api_key,
                    adapter=adapter,
                    runtime=runtime,
                )
            body = adapter.build_body(
                model=bind.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a stateless code generator for one file. "
                            "Output only raw source code, no fences."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                tools=None,
                stream=False,
                thinking_level="low",
            )
            if isinstance(body, dict) and max_tokens:
                # Prefer not to lower provider default if already set high
                body["max_tokens"] = min(
                    int(body.get("max_tokens") or max_tokens),
                    max(max_tokens, int(body.get("max_tokens") or max_tokens)),
                )
            import urllib.request

            from remedy.core.provider_sanitize import sanitize_chat_body

            body = sanitize_chat_body(
                body if isinstance(body, dict) else {}, local_agent=False
            )
            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(
                endpoint,
                data=data,
                headers={**headers, "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
                payload = json.loads(resp.read().decode("utf-8"))
            choices = payload.get("choices") or []
            if choices:
                msg = choices[0].get("message") or {}
                text = str(msg.get("content") or "")
        if not text:
            # Fallback: keep existing file on disk if present
            return ""
        return extract_markdown_fence(text)

    return model


def _behavioral_oracle_errors(
    unit: UnitSpec,
    body: str,
    root: Path,
    *,
    files: dict[str, str] | None = None,
) -> list[OracleError]:
    """Structural first; if unit.tests set, run PytestOracle on a temp tree."""
    errs = run_oracle(unit, body)
    if errs or not (unit.tests or "").strip():
        return errs
    try:
        import tempfile

        from remedy.core.builds.reducer import PytestOracle

        state = dict(files or {})
        state[unit.path] = body
        with tempfile.TemporaryDirectory(prefix="remedy-hop-") as td:
            oracle = PytestOracle(td, timeout_s=30.0)
            return oracle(unit, state)
    except Exception as e:
        return [OracleError(unit.id, f"behavioral oracle error: {e}")]


def live_unit_hop(
    runtime: Any,
    *,
    path: str,
    behavior: str = "",
    symbol: str = "",
    source: str = "",
    use_llm: bool = False,
    max_repairs: int = 3,
    tests: str = "",
    patch_symbol: str = "",
    require_behavior: bool = True,
) -> dict[str, Any]:
    """One unit: optional LLM fill → snapshot → write → structural + behavioral oracle.

    A: behavioral gate via unit.tests / PytestOracle when tests provided.
    E: snapshot before write.
    G: disk symbol index closure + optional AST-minimal patch via patch_symbol.
    """
    root = _project_root(runtime)
    rel = _clean_rel(path)
    sym = (symbol or Path(rel).stem).strip()
    sig = Signature(symbol=sym, defines_path=rel)

    # Load tests from locked spec if not passed
    test_src = (tests or "").strip()
    if not test_src:
        with suppress(Exception):
            from remedy.core.build_spec_compiler import load_locked_spec

            locked = load_locked_spec(root)
            for u in (locked or {}).get("units") or []:
                if str(u.get("path") or "").replace("\\", "/") == rel or u.get("symbol") == sym:
                    test_src = str(u.get("tests") or "")
                    if not behavior:
                        behavior = str(u.get("behavior") or "")
                    break

    unit = UnitSpec(
        id=sym,
        path=rel,
        declare=[sig],
        behavior=(behavior or "")[:800],
        tests=test_src,
    )

    # G: disk-wide linker closure
    reg = SymbolRegistry()
    reg.declare(sig)
    closure = reg.closure_text(unit, budget=2000)
    with suppress(Exception):
        from remedy.core.build_symbol_index import build_symbol_index, closure_from_index

        idx = build_symbol_index(root)
        closure = (
            closure_from_index(idx, path=rel, symbols=[sym], budget=2500)
            + "\n"
            + closure
        )[:4000]

    try:
        dest = _resolve_write(runtime, rel, root)
    except Exception as exc:
        return {
            "ok": False,
            "path": rel,
            "written": False,
            "error": f"write jail refused ({exc})",
        }
    base_body = ""
    if dest.is_file():
        with suppress(Exception):
            base_body = dest.read_text(encoding="utf-8", errors="replace")

    body = (source or "").strip()
    memo_hit = False
    memo_root = Path(getattr(runtime, "_project", None) or root)
    memo_k = ""
    with suppress(Exception):
        from remedy.core.build_hop_memo import memo_key, try_reuse

        memo_k = memo_key(
            path=rel,
            symbol=sym,
            behavior=unit.behavior or "",
            tests=test_src,
            closure=closure,
            errors=[],
        )
        if not body and use_llm:
            def _behavioral(u: Any, src: str) -> list[Any]:
                if not str(getattr(u, "tests", "") or "").strip():
                    return []
                from remedy.core.builds.reducer import PytestOracle

                path = str(getattr(u, "path", "") or rel)
                return PytestOracle(root)(u, {path: src})

            cached = try_reuse(
                memo_root,
                memo_k,
                oracle_fn=run_oracle,
                behavioral_fn=_behavioral,
                unit=unit,
            )
            if cached:
                body = cached
                memo_hit = True
    # G: AST-minimal patch when patch_symbol + source is a def fragment
    if body and (patch_symbol or "").strip() and base_body:
        with suppress(Exception):
            from remedy.core.build_ast_patch import apply_minimal_patch

            pr = apply_minimal_patch(
                base_body, symbol=(patch_symbol or sym).strip(), patch_source=body
            )
            if pr.ok:
                body = pr.source

    if not body and not use_llm:
        body = base_body
    if not body and use_llm:
        model = make_runtime_llm_model(runtime)
        body = model(unit, closure, [])
    if not body:
        return {
            "ok": False,
            "path": rel,
            "phase": "scout",
            "context": closure,
            "error": "no source; pass source= or use_llm=true",
        }

    # E: snapshot before overwrite
    snap_meta: dict[str, Any] = {}
    with suppress(Exception):
        from remedy.core.build_snapshot import auto_snapshot_before_write

        if dest.is_file():
            snap_meta = auto_snapshot_before_write(root, [rel], note=f"pre-hop:{sym}")

    # Repair loop: structural (+ behavioral after materialize)
    errors: list[OracleError] = []
    attempts = 0
    while attempts < max(1, max_repairs):
        attempts += 1
        errors = run_oracle(unit, body)
        if not errors:
            break
        if use_llm and attempts < max_repairs:
            model = make_runtime_llm_model(runtime)
            nxt = model(unit, closure, errors)
            if nxt.strip():
                if (patch_symbol or "").strip() and base_body:
                    with suppress(Exception):
                        from remedy.core.build_ast_patch import apply_minimal_patch

                        pr = apply_minimal_patch(
                            body if body else base_body,
                            symbol=(patch_symbol or sym).strip(),
                            patch_source=nxt,
                        )
                        if pr.ok:
                            body = pr.source
                            continue
                body = nxt
                continue
        break

    # Materialize atomically (crash mid-write must not leave a half file)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".remedy-tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(dest)
    written = True

    # A: behavioral oracle after disk write (uses tests on unit)
    behavior_errs: list[OracleError] = []
    if require_behavior and (unit.tests or "").strip():
        behavior_errs = _behavioral_oracle_errors(unit, body, root)
        # If only structural was checked in loop, fold behavioral
        for e in behavior_errs:
            if e.message not in {x.message for x in errors}:
                errors.append(e)

    # L2 import dry-run — skip inside isolated overlays (siblings are not
    # copied; a missing neighbor must not false-red a good unit).
    import_result: dict[str, Any] = {}
    if getattr(runtime, "_overlay", None) is None:
        with suppress(Exception):
            from remedy.core.build_import_graph import dry_run_imports_for_paths

            results = dry_run_imports_for_paths([str(dest)], root)
            if results:
                import_result = results[0]
                # Interpreter/spawn failures are machine config, not unit red.
                _cls = str(import_result.get("error_class") or "")
                if not import_result.get("ok") and _cls not in {"interpreter", "spawn"}:
                    errors.append(
                        OracleError(unit.id, f"import dry-run: {import_result.get('error')}")
                    )

    # Behavioral LLM repair once if tests red
    if (
        use_llm
        and errors
        and any("test failed" in e.message for e in errors)
        and attempts < max_repairs + 1
    ):
        model = make_runtime_llm_model(runtime)
        nxt = model(unit, closure, errors)
        if nxt.strip():
            body = nxt
            tmp = dest.with_name(dest.name + ".remedy-tmp")
            tmp.write_text(body, encoding="utf-8")
            tmp.replace(dest)
            attempts += 1
            errors = run_oracle(unit, body)
            if not errors and (unit.tests or "").strip():
                errors = _behavioral_oracle_errors(unit, body, root)

    ok = not errors
    # Overlay hops must not cache until the live merge succeeds.
    if ok and memo_k and (body or "").strip() and getattr(runtime, "_overlay", None) is None:
        with suppress(Exception):
            from remedy.core.build_hop_memo import store_hop

            store_hop(memo_root, memo_k, body, ok=True, path=rel)
    with suppress(Exception):
        from remedy.core.build_snapshot import mark_snapshot_ok

        if snap_meta.get("snap_id"):
            mark_snapshot_ok(root, str(snap_meta["snap_id"]), ok)

    with suppress(Exception):
        from remedy.core.build_ledger import append_hop

        home = getattr(getattr(runtime, "config", None), "home_dir", None)
        hop_goal = ""
        with suppress(Exception):
            from remedy.core.build_engine import get_build_state

            st = get_build_state(runtime)
            hop_goal = str(getattr(st, "goal", "") or "") if st is not None else ""
        append_hop(
            str(root),
            {
                "unit_id": sym,
                "path": rel,
                "ok": ok,
                "errors": [e.message for e in errors][:12],
                "written": written,
                "use_llm": use_llm,
                "attempts": attempts,
                "import_ok": import_result.get("ok"),
                "kind": "live_hop",
                "behavioral": bool(unit.tests),
                "snap_id": snap_meta.get("snap_id"),
            },
            home=home,
            goal=hop_goal or None,
        )
    with suppress(Exception):
        from remedy.core.build_engine import get_build_state

        st = get_build_state(runtime)
        if st is not None:
            st.mark_write(rel)
            st.write_steps += 1
            st.phase = "implement" if ok else "repair"

    return {
        "ok": ok,
        "path": rel,
        "symbol": sym,
        "written": written,
        "attempts": attempts,
        "errors": [e.message for e in errors],
        "import": import_result,
        "context": closure[:600],
        "phase": "done" if ok else "repair",
        "behavioral": bool(unit.tests),
        "snap_id": snap_meta.get("snap_id"),
        "memo_hit": memo_hit,
    }


def live_build_project(
    runtime: Any,
    units: list[dict[str, Any]],
    *,
    use_llm: bool = True,
    max_repairs: int = 4,
    max_iterations: int = 40,
) -> dict[str, Any]:
    """Run reducer build_project with live LLM model + materialize to disk."""
    root = _project_root(runtime)
    specs: list[UnitSpec] = []
    for u in units or []:
        if not isinstance(u, dict):
            continue
        path = str(u.get("path") or "").replace("\\", "/").strip()
        if not path:
            continue
        sym = str(u.get("symbol") or Path(path).stem)
        req = u.get("requires") or []
        if isinstance(req, str):
            req = [req]
        imps = u.get("imports") or []
        if isinstance(imps, str):
            imps = [imps]
        # Prefer explicit tests; else locked spec for this path
        tsrc = str(u.get("tests") or "")
        if not tsrc:
            with suppress(Exception):
                from remedy.core.build_spec_compiler import load_locked_spec

                locked = load_locked_spec(root)
                for lu in (locked or {}).get("units") or []:
                    if str(lu.get("path") or "").replace("\\", "/") == path:
                        tsrc = str(lu.get("tests") or "")
                        break
        specs.append(
            UnitSpec(
                id=sym,
                path=path,
                declare=[Signature(symbol=sym, defines_path=path)],
                requires=list(req),
                imports=list(imps),
                behavior=str(u.get("behavior") or "")[:800],
                tests=tsrc,
            )
        )
    if not specs:
        return {"ok": False, "error": "units[] required with path keys"}

    if use_llm:
        model_fn = make_runtime_llm_model(runtime)
    else:

        def model_fn(unit: UnitSpec, closure: str, errs: list[OracleError]) -> str:  # noqa: ARG001
            p = _resolve_write(runtime, unit.path, root)
            if p.is_file():
                return p.read_text(encoding="utf-8", errors="replace")
            return ""

    result: BuildResult = build_project(
        BuildSpec(units=specs),
        model_fn,
        oracle=disk_oracle,
        max_repairs=max_repairs,
        max_iterations=max_iterations,
    )
    written = materialize_files(runtime, result.files, root)

    # Import dry-run on all written
    import_results: list[dict[str, Any]] = []
    with suppress(Exception):
        from remedy.core.build_import_graph import dry_run_imports_for_paths

        import_results = dry_run_imports_for_paths(
            [str(root / w) for w in written], root
        )

    with suppress(Exception):
        from remedy.core.build_ledger import append_hop

        home = getattr(getattr(runtime, "config", None), "home_dir", None)
        hop_goal = ""
        with suppress(Exception):
            from remedy.core.build_engine import get_build_state

            st = get_build_state(runtime)
            hop_goal = str(getattr(st, "goal", "") or "") if st is not None else ""
        append_hop(
            str(root),
            {
                "kind": "live_build_project",
                "ok": result.ok,
                "files": written,
                "iterations": result.iterations,
                "failures": [f.path for f in result.failures],
            },
            home=home,
            goal=hop_goal or None,
        )

    return {
        "ok": result.ok and all(r.get("ok", True) for r in import_results),
        "summary": result.summary(),
        "files": written,
        "iterations": result.iterations,
        "repaired": result.repaired,
        "failures": [
            {"path": f.path, "attempts": f.attempts, "error": f.last_error[:300]}
            for f in result.failures
        ],
        "import_results": import_results,
        "public": result.to_public(),
    }
