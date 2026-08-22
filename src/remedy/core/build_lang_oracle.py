"""Multi-language cheap oracles — syntax/compile before the full suite.

Python already has ``py_compile``. This module covers JS/TS, Rust, Go, and
C/C++ with real toolchains when present, and a brace-balance fallback so a
missing compiler never silently accepts broken brackets.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from contextlib import suppress
from pathlib import Path
from typing import Any

LANG_SUFFIXES = frozenset(
    {
        ".py",
        ".json",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".rs",
        ".go",
        ".c",
        ".h",
        ".cpp",
        ".cc",
        ".cxx",
        ".hpp",
        # Game engines (Godot text resources, GDScript, Lua)
        ".gd",
        ".tscn",
        ".tres",
        ".lua",
    }
)

_TOOLCHAIN: dict[str, str | None] = {}


def _which(name: str) -> str | None:
    if name not in _TOOLCHAIN:
        _TOOLCHAIN[name] = shutil.which(name)
    return _TOOLCHAIN[name]


def _run(cmd: list[str], *, cwd: Path | None = None, timeout_s: float = 20.0) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        out = ((proc.stdout or "") + (proc.stderr or ""))[-800:]
        return proc.returncode == 0, out
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)[:300]


def brace_balance(text: str) -> tuple[bool, str]:
    """Cheap structural check (strings/comments are best-effort)."""
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    in_str = ""
    escape = False
    i = 0
    n = len(text or "")
    while i < n:
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_str:
                in_str = ""
            i += 1
            continue
        if ch in ("'", '"', "`"):
            in_str = ch
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] not in "\n\r":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        if ch in "([{":
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack[-1] != pairs[ch]:
                return False, f"unbalanced {ch}"
            stack.pop()
        i += 1
    if in_str:
        return False, "unterminated string"
    if stack:
        return False, f"unclosed {stack[-1]}"
    return True, ""


def _is_tsc_project_noise(err: str) -> bool:
    """tsc on a lone file without a tsconfig errors on imports, not syntax."""
    return "Cannot find module" in err or "TS2307" in err or "TS6059" in err


def _jsx_checker() -> str | None:
    """A parser that actually understands JSX, or None.

    esbuild parses .jsx/.tsx natively and is the cheapest; tsc with
    ``--jsx preserve`` is the fallback. Node is deliberately not here.
    """
    return _which("esbuild") or _which("tsc")


def _jsx_command(checker: str, p: Path) -> list[str]:
    if Path(checker).stem.lower() == "esbuild":
        # Transform to stdout (discarded); a parse failure is a non-zero exit.
        return [checker, "--log-level=error", str(p)]
    return [checker, "--noEmit", "--pretty", "false", "--allowJs", "--jsx", "preserve", str(p)]


_RUST_DEP_NOISE = (
    "E0432",  # unresolved import
    "E0433",  # failed to resolve (use of undeclared crate/module)
    "E0463",  # can't find crate
    "E0583",  # file not found for module
    "can't find crate",
    "unresolved import",
    "failed to resolve",
    "file not found for module",
    "failed to write",
)


def _cargo_root_for(p: Path) -> Path | None:
    """Nearest ancestor holding a Cargo.toml (the crate this file belongs to)."""
    for parent in [p.parent, *p.parent.parents]:
        if (parent / "Cargo.toml").is_file():
            return parent
    return None


def _is_rust_dep_noise(err: str) -> bool:
    """Bare ``rustc`` on one file cannot see the crate graph — those errors are not syntax."""
    e = err or ""
    return any(tok in e for tok in _RUST_DEP_NOISE)


def _check_rust(p: Path, text: str, out: dict[str, Any]) -> dict[str, Any]:
    """Rust syntax gate.

    Order of preference:
    1. ``cargo check`` in the owning crate when Cargo.toml + cargo exist — the only
       check that can resolve ``tauri``/plugins/``crate_lib`` paths.
    2. Bare ``rustc --emit=metadata`` into a temp out-dir (never ``-o NUL`` — that
       "failed to write NUL" on Windows and produced a permanent false red).
       Crate-graph errors (unresolved import / can't find crate) are *noise* for a
       single-file probe and fall back to brace balance.
    3. Brace balance.
    """
    cargo_root = _cargo_root_for(p)
    cargo = _which("cargo")
    # A Tauri/desktop crate's first `cargo check` is a 30–120s compile, not a
    # syntax probe. Session 765c: that hung the turn and false-red'd main.rs.
    # Brace/rustc-dep-noise is enough to catch unmatched braces; full typecheck
    # belongs to verify (`cargo check` / `tauri build`), not the write gate.
    tauri_crate = False
    if cargo_root is not None:
        with suppress(OSError):
            tauri_crate = "tauri" in (cargo_root / "Cargo.toml").read_text(
                encoding="utf-8", errors="replace"
            ).lower()
    if cargo_root is not None and cargo and not tauri_crate:
        ok, err = _run(
            [cargo, "check", "--quiet", "--message-format=short"],
            cwd=cargo_root,
            timeout_s=float(os.environ.get("REMEDY_CARGO_CHECK_TIMEOUT_S", "120")),
        )
        if not ok and ("timed out" in err.lower() or "TimeoutExpired" in err):
            # A slow first compile is not a syntax error — do not fail closed on it.
            bok, berr = brace_balance(text)
            out["ok"] = bok
            out["error"] = berr
            out["engine"] = "brace (cargo check timed out)"
            return out
        out["ok"] = ok
        out["error"] = "" if ok else err
        out["engine"] = "cargo check"
        return out

    rustc = _which("rustc")
    if rustc:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="remedy-rustc-") as td:
            ok, err = _run(
                [
                    rustc,
                    "--edition",
                    "2021",
                    "--crate-type",
                    "lib",
                    "--emit=metadata",
                    "--out-dir",
                    td,
                    str(p),
                ]
            )
        if ok:
            out["ok"] = True
            out["error"] = ""
            out["engine"] = "rustc --emit=metadata"
            return out
        if _is_rust_dep_noise(err):
            bok, berr = brace_balance(text)
            out["ok"] = bok
            out["error"] = berr
            out["engine"] = "brace (rustc dep-noise)"
            return out
        out["ok"] = False
        out["error"] = err
        out["engine"] = "rustc --emit=metadata"
        return out

    ok, err = brace_balance(text)
    out["ok"] = ok
    out["error"] = err
    out["engine"] = "brace"
    return out


def check_lang_syntax(path: str | Path) -> dict[str, Any]:
    """Return {ok, path, error, engine} for one file."""
    p = Path(path)
    out: dict[str, Any] = {"ok": True, "path": str(p), "error": "", "engine": "skip"}
    if not p.is_file():
        return {"ok": False, "path": str(p), "error": "not a file", "engine": "stat"}
    suffix = p.suffix.lower()
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"ok": False, "path": str(p), "error": str(e), "engine": "io"}

    if suffix == ".py":
        from remedy.core.build_syntax import check_path_syntax

        r = check_path_syntax(p)
        r["engine"] = "py_compile"
        return r

    if suffix == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError as e:
            out["ok"] = False
            out["error"] = f"JSONDecodeError: {e.msg} (line {e.lineno})"
        out["engine"] = "json"
        return out

    if suffix in {".jsx", ".tsx"}:
        # NOT node --check: node cannot parse .jsx at all (it rejects the
        # *extension* with ERR_UNKNOWN_FILE_EXTENSION). And NOT brace_balance:
        # JSX text is prose, so the apostrophe in ``<p>Don't click {x}</p>``
        # opened a "string" that never closed, and a regex like ``/[{]/`` was
        # an unbalanced brace — working components came back red with an
        # error that sent the model rewriting them. Only a real JSX-aware
        # parser gets a verdict; without one the file is skipped, the same
        # way an extension without an oracle is skipped.
        checker = _jsx_checker()
        if checker is None:
            out["engine"] = "skip (no jsx parser)"
            return out
        ok, err = _run(_jsx_command(checker, p))
        if not ok and _is_tsc_project_noise(err):
            out["engine"] = "skip (tsc import-noise)"
            return out
        out["ok"] = ok
        out["error"] = "" if ok else err
        out["engine"] = f"{Path(checker).stem} (jsx)"
        return out

    if suffix in {".js", ".mjs", ".cjs"}:
        node = _which("node")
        if node:
            ok, err = _run([node, "--check", str(p)])
            out["ok"] = ok
            out["error"] = "" if ok else err
            out["engine"] = "node --check"
            return out
        ok, err = brace_balance(text)
        out["ok"] = ok
        out["error"] = err
        out["engine"] = "brace"
        return out

    if suffix == ".ts":
        tsc = _which("tsc")
        if tsc:
            ok, err = _run([tsc, "--noEmit", "--pretty", "false", "--allowJs", "false", str(p)])
            # tsc on a single file without tsconfig often errors on imports —
            # fall back to brace if the only issue is project config.
            if not ok and _is_tsc_project_noise(err):
                ok, err = brace_balance(text)
                out["engine"] = "brace (tsc import-noise)"
            else:
                out["engine"] = "tsc --noEmit"
            out["ok"] = ok
            out["error"] = "" if ok else err
            return out
        ok, err = brace_balance(text)
        out["ok"] = ok
        out["error"] = err
        out["engine"] = "brace"
        return out

    if suffix == ".rs":
        return _check_rust(p, text, out)

    if suffix == ".go":
        gofmt = _which("gofmt")
        if gofmt:
            ok, err = _run([gofmt, "-e", str(p)])
            out["ok"] = ok
            out["error"] = "" if ok else err
            out["engine"] = "gofmt -e"
            return out
        ok, err = brace_balance(text)
        out["ok"] = ok
        out["error"] = err
        out["engine"] = "brace"
        return out

    if suffix in {".c", ".h"}:
        gcc = _which("gcc") or _which("clang")
        if gcc:
            ok, err = _run([gcc, "-fsyntax-only", "-std=c11", str(p)])
            out["ok"] = ok
            out["error"] = "" if ok else err
            out["engine"] = f"{Path(gcc).name} -fsyntax-only"
            return out
        ok, err = brace_balance(text)
        out["ok"] = ok
        out["error"] = err
        out["engine"] = "brace"
        return out

    if suffix in {".cpp", ".cc", ".cxx", ".hpp"}:
        gxx = _which("g++") or _which("clang++")
        if gxx:
            ok, err = _run([gxx, "-fsyntax-only", "-std=c++17", str(p)])
            out["ok"] = ok
            out["error"] = "" if ok else err
            out["engine"] = f"{Path(gxx).name} -fsyntax-only"
            return out
        ok, err = brace_balance(text)
        out["ok"] = ok
        out["error"] = err
        out["engine"] = "brace"
        return out

    if suffix == ".gd":
        return _check_gdscript(p, text, out)

    if suffix in {".tscn", ".tres"}:
        from remedy.core.godot_scene import check_scene

        res = check_scene(p, text)
        out["ok"] = bool(res.get("ok"))
        out["error"] = str(res.get("error") or "")
        out["engine"] = str(res.get("engine") or "tscn-parse")
        return out

    if suffix == ".lua":
        luac = _which("luac") or _which("luac5.4") or _which("luac5.1") or _which("luajit")
        if luac:
            args = [luac, "-bl" if Path(luac).stem == "luajit" else "-p", str(p)]
            ok, err = _run(args)
            out["ok"] = ok
            out["error"] = "" if ok else err
            out["engine"] = f"{Path(luac).name} -p"
            return out
        # `love` cannot parse-only and Lua's `end` blocks defeat brace_balance
        # — do not false-red a file nobody can judge here.
        out["engine"] = "skip (no luac)"
        return out

    out["engine"] = "skip"
    return out


def _godot_binary() -> str | None:
    if "godot" not in _TOOLCHAIN:
        found: str | None = None
        with suppress(Exception):
            from remedy.core.game_engines import find_engine_binary

            hit = find_engine_binary("godot")
            found = str(hit) if hit else None
        if found is None:
            found = _which("godot4") or _which("godot")
        _TOOLCHAIN["godot"] = found
    return _TOOLCHAIN["godot"]


def _check_gdscript(p: Path, text: str, out: dict[str, Any]) -> dict[str, Any]:
    """``godot --check-only`` when an engine is around, tokenizer otherwise."""
    from remedy.core.godot_scene import check_gdscript_text, project_root_for

    godot = _godot_binary()
    root = project_root_for(p)
    if godot and root is not None:
        ok, err = _run(
            [godot, "--headless", "--path", str(root), "--check-only", "-s", str(p)],
            cwd=root,
            timeout_s=45.0,
        )
        out["ok"] = ok
        out["error"] = "" if ok else err
        out["engine"] = "godot --check-only"
        return out
    ok, err = check_gdscript_text(text)
    out["ok"] = ok
    out["error"] = err
    out["engine"] = "gd-tokenizer (fallback)" if not godot else "gd-tokenizer (no project.godot)"
    return out


def check_lang_paths(paths: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in paths or []:
        p = str(raw or "").strip()
        if not p or p in seen:
            continue
        if " " in p and not Path(p).exists():
            continue
        suf = Path(p).suffix.lower()
        if suf not in LANG_SUFFIXES:
            continue
        seen.add(p)
        results.append(check_lang_syntax(p))
        if len(results) >= 12:
            break
    return results


def scoped_lang_verify(root: Path, write_set: list[str]) -> str:
    """Best-effort scoped verify command for a non-Python write set."""
    sufs = {Path(p).suffix.lower() for p in write_set if p}
    if sufs & {".rs"} and (root / "Cargo.toml").is_file():
        return "cargo test"
    if sufs & {".go"} and (root / "go.mod").is_file():
        return "go test ./..."
    if sufs & {".ts", ".tsx", ".js", ".jsx"} and (root / "package.json").is_file():
        if (root / "pnpm-lock.yaml").is_file():
            return "pnpm test"
        if (root / "yarn.lock").is_file():
            return "yarn test"
        return "npm test"
    if sufs & {".c", ".h", ".cpp", ".cc", ".cxx"}:
        with suppress(Exception):
            from remedy.core.build_oracle import _discover_c_verify_command

            return _discover_c_verify_command(root) or ""
    return ""
