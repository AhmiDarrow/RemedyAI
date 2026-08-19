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

    if suffix == ".jsx":
        # NOT node --check: node cannot parse .jsx at all. It rejects the
        # *extension* with ERR_UNKNOWN_FILE_EXTENSION before it looks at a
        # single character, so every .jsx file came back red no matter what was
        # in it — and the "error" handed to the model was a Node internals
        # traceback about file formats, which reads as "your code is broken"
        # and sends it rewriting a working component. Structural check instead,
        # the same fallback .tsx already uses.
        ok, err = brace_balance(text)
        out["ok"] = ok
        out["error"] = err
        out["engine"] = "brace (jsx)"
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

    if suffix in {".ts", ".tsx"}:
        tsc = _which("tsc")
        if tsc:
            ok, err = _run([tsc, "--noEmit", "--pretty", "false", "--allowJs", "false", str(p)])
            # tsc on a single file without tsconfig often errors on imports —
            # fall back to brace if the only issue is project config.
            if not ok and ("Cannot find module" in err or "TS2307" in err or "TS6059" in err):
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
        rustc = _which("rustc")
        if rustc:
            sink = "NUL" if os.name == "nt" else "/dev/null"
            ok, err = _run(
                [rustc, "--edition", "2021", "--crate-type", "lib", "--emit=metadata", "-o", sink, str(p)]
            )
            out["ok"] = ok
            out["error"] = "" if ok else err
            out["engine"] = "rustc --emit=metadata"
            return out
        ok, err = brace_balance(text)
        out["ok"] = ok
        out["error"] = err
        out["engine"] = "brace"
        return out

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

    out["engine"] = "skip"
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
