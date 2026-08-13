"""Isolated unit hops — siblings cannot corrupt each other.

Each hop writes into a private overlay directory. The live tree is updated
only when the structural (+ optional behavioral) oracle is green. Independent
units can hop in parallel.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from pathlib import Path
from typing import Any

_MERGE_LOCK = threading.Lock()


class OverlayRuntime:
    """Delegates to the live runtime except path jail → overlay root."""

    def __init__(self, inner: Any, overlay: Path, project: Path) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_overlay", overlay)
        object.__setattr__(self, "_project", project)
        object.__setattr__(self, "_build_turn", None)

    def effective_project_path(self) -> Path:
        return self._overlay

    def resolve_tool_path(self, rel: str, for_write: bool = False) -> Path:  # noqa: ARG002
        p = Path(rel)
        if p.is_absolute():
            with suppress(Exception):
                p = p.relative_to(self._project)
            if p.is_absolute():
                p = Path(p.name)
        if ".." in p.parts:
            raise PermissionError(f"isolated hop path escapes overlay: {rel}")
        dest = (self._overlay / p).resolve()
        try:
            dest.relative_to(self._overlay.resolve())
        except ValueError as exc:
            raise PermissionError(f"isolated hop path escapes overlay: {rel}") from exc
        dest.parent.mkdir(parents=True, exist_ok=True)
        return dest

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        # Never write through to the live runtime — parallel hops would race.
        object.__setattr__(self, name, value)


def _project_root(runtime: Any) -> Path:
    raw = runtime.effective_project_path()
    p = Path(raw)
    return p.parent if p.is_file() else p


def _seed_overlay(project: Path, overlay: Path, rel: str) -> None:
    src = project / rel
    if src.is_file():
        dest = overlay / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    # package inits so import oracles can resolve
    parent = Path(rel).parent
    while str(parent) not in {".", ""}:
        init = project / parent / "__init__.py"
        if init.is_file():
            d = overlay / parent / "__init__.py"
            d.parent.mkdir(parents=True, exist_ok=True)
            if not d.exists():
                shutil.copy2(init, d)
        parent = parent.parent


def isolated_unit_hop(
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
) -> dict[str, Any]:
    """Hop *path* in an overlay; merge to the live tree only if the oracle is green."""
    from remedy.core.build_live_hop import live_unit_hop

    rel = (path or "").replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    if not rel:
        return {"ok": False, "path": path, "error": "path required", "isolated": True}
    project = _project_root(runtime)
    td = tempfile.mkdtemp(prefix="remedy-iso-")
    overlay = Path(td)
    try:
        _seed_overlay(project, overlay, rel)
        shadow = OverlayRuntime(runtime, overlay, project)
        # copy config for home_dir / ledger isolation — ledger stays on real project
        res = live_unit_hop(
            shadow,
            path=rel,
            behavior=behavior,
            symbol=symbol,
            source=source,
            use_llm=use_llm,
            max_repairs=max_repairs,
            tests=tests,
            patch_symbol=patch_symbol,
        )
        overlay_file = overlay / rel
        if res.get("ok") and overlay_file.is_file():
            dest = Path(runtime.resolve_tool_path(rel, for_write=True)) if hasattr(
                runtime, "resolve_tool_path"
            ) else (project / rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            prev = dest.read_text(encoding="utf-8", errors="replace") if dest.is_file() else None
            with _MERGE_LOCK:
                tmp = dest.with_name(dest.name + ".remedy-tmp")
                shutil.copy2(overlay_file, tmp)
                tmp.replace(dest)
                live_ok = True
                if dest.suffix.lower() == ".py":
                    with suppress(Exception):
                        from remedy.core.build_import_graph import dry_run_imports_for_paths

                        imps = dry_run_imports_for_paths([str(dest)], project)
                        if imps and not imps[0].get("ok"):
                            live_ok = False
                            err = str(imps[0].get("error") or "import failed on live tree")
                            errs = list(res.get("errors") or [])
                            errs.append(f"live import: {err}")
                            res["errors"] = errs
                            res["ok"] = False
                if not live_ok:
                    if prev is not None:
                        dest.write_text(prev, encoding="utf-8")
                    else:
                        dest.unlink(missing_ok=True)
                    res["merged"] = False
                else:
                    with suppress(Exception):
                        from remedy.core.build_engine import get_build_state

                        st = get_build_state(runtime)
                        if st is not None:
                            st.mark_write(rel)
                            st.write_steps += 1
                    res["merged"] = True
        else:
            res["merged"] = False
        res["isolated"] = True
        return res
    finally:
        shutil.rmtree(td, ignore_errors=True)


def parallel_isolated_hops(
    runtime: Any,
    units: list[dict[str, Any]],
    *,
    use_llm: bool = False,
    max_repairs: int = 3,
    max_workers: int = 4,
) -> list[dict[str, Any]]:
    """Hop independent units (distinct paths) in parallel overlays."""
    jobs: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for u in units or []:
        if not isinstance(u, dict):
            continue
        path = str(u.get("path") or "").replace("\\", "/").strip()
        if not path or path in seen:
            if path in seen:
                results.append(
                    {"ok": False, "path": path, "error": "duplicate path skipped", "isolated": True}
                )
            continue
        seen.add(path)
        dest = _project_root(runtime) / path
        with suppress(Exception):
            dest = Path(runtime.resolve_tool_path(path, for_write=True))
        if not dest.is_file() and not use_llm and not (u.get("source") or "").strip():
            results.append(
                {
                    "ok": False,
                    "path": path,
                    "phase": "scout",
                    "error": "no source on disk; model must file_write or re-call with use_llm",
                    "isolated": True,
                    "merged": False,
                }
            )
            continue
        jobs.append(u)

    workers = max(1, min(int(max_workers or 4), 8, len(jobs) or 1))
    if not jobs:
        return results

    def _one(u: dict[str, Any]) -> dict[str, Any]:
        return isolated_unit_hop(
            runtime,
            path=str(u.get("path") or ""),
            symbol=str(u.get("symbol") or Path(str(u.get("path") or "")).stem),
            behavior=str(u.get("behavior") or "")[:400],
            source=str(u.get("source") or ""),
            use_llm=use_llm,
            max_repairs=max_repairs,
            tests=str(u.get("tests") or ""),
            patch_symbol=str(u.get("patch_symbol") or ""),
        )

    if workers == 1 or len(jobs) == 1:
        for u in jobs:
            results.append(_one(u))
        return results

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_one, u): u for u in jobs}
        for fut in as_completed(futs):
            try:
                results.append(fut.result())
            except Exception as e:
                u = futs[fut]
                results.append(
                    {
                        "ok": False,
                        "path": u.get("path"),
                        "error": str(e),
                        "isolated": True,
                        "merged": False,
                    }
                )
    # stable order by path
    results.sort(key=lambda r: str(r.get("path") or ""))
    return results
