"""Detect GUI / interactive launches so the agent does not wait on them.

Auto-verify and bash_exec used to run pygame / compiled games to completion.
That hung the turn (60–300s) and then killed the window — the opposite of
play-to-iterate. Compile/syntax-check stays synchronous; the window is
spawned in the background for computer-use.
"""

from __future__ import annotations

import re
from pathlib import Path

# Source markers that mean "this process will not exit on its own".
_GUI_SRC_RE = re.compile(
    r"(?i)(?:\b(?:"
    r"pygame|tkinter|Tkinter|turtle|"
    r"PyQt[0-9]?|PySide[0-9]?|wx|dearpygui|panda3d|"
    r"arcade|pyglet|moderngl|"
    r"sdl2?|glfw|glut|raylib|SFML|"
    r"WinMain\s*\("
    r")\b|#\s*include\s*<\s*(?:SDL|GLFW|raylib|windows\.h))"
)

_COMPILER_OR_TEST_RE = re.compile(
    r"(?ix)"
    r"\b(?:"
    r"gcc|g\+\+|clang|cl\.exe|rustc|cmake|ninja|msbuild|"
    r"pytest|unittest|vitest|jest|tsc\b|"
    r"cargo\s+(?:test|build|check|clippy)|"
    r"go\s+test|dotnet\s+(?:test|build)|"
    r"npm\s+test|npx\s+"
    r")\b"
)

_LAUNCH_EXE_RE = re.compile(
    r"(?ix)"
    r"(?:^|[\s;&|])(?:\.\\|\./|\"|')?([\w.-]+\.exe)\b"
)

_PY_FILE_RE = re.compile(
    r"(?ix)"
    r"\b(?:python|python3|py)\s+(?:-[uE]\s+)*([^\s|&;]+\.py)\b"
)


def source_looks_like_gui(text: str | None) -> bool:
    return bool(_GUI_SRC_RE.search(text or ""))


def path_looks_like_gui(path: Path | str | None) -> bool:
    if path is None:
        return False
    try:
        p = Path(path)
        if not p.is_file():
            return False
        text = p.read_text(encoding="utf-8", errors="replace")[:12_000]
    except OSError:
        return False
    return source_looks_like_gui(text)


def write_set_looks_like_gui(paths: list[str] | None, *, cwd: Path | None = None) -> bool:
    for raw in paths or []:
        p = Path(raw)
        if not p.is_absolute() and cwd is not None:
            p = cwd / p
        if path_looks_like_gui(p):
            return True
        # Header-only hint from the path name
        name = p.name.lower()
        if name.endswith((".c", ".cpp", ".cc", ".h", ".py")) and any(
            k in name for k in ("game", "pygame", "sdl", "win32")
        ):
            if p.is_file() and path_looks_like_gui(p):
                return True
    return False


def command_looks_like_gui_launch(
    command: str,
    cwd: Path | str | None = None,
) -> bool:
    """True when *command* will likely open a window and not exit."""
    cmd = command or ""
    if not cmd.strip():
        return False
    if source_looks_like_gui(cmd):
        return True
    base = Path(cwd) if cwd else None
    for m in _PY_FILE_RE.finditer(cmd):
        rel = m.group(1).strip().strip("\"'")
        cand = Path(rel)
        if not cand.is_absolute() and base is not None:
            cand = base / rel
        if path_looks_like_gui(cand):
            return True
    # Bare / relative .exe that is not a compiler
    for m in _LAUNCH_EXE_RE.finditer(cmd):
        exe = (m.group(1) or "").lower()
        stem = exe[:-4] if exe.endswith(".exe") else exe
        if stem in {
            "gcc",
            "g++",
            "clang",
            "clang++",
            "cl",
            "rustc",
            "cmake",
            "ninja",
            "msbuild",
            "pytest",
            "python",
            "pythonw",
            "py",
            "node",
            "cmd",
            "pwsh",
            "powershell",
        }:
            continue
        # `gcc … && hello.exe` — only treat as GUI if source is GUI
        if _COMPILER_OR_TEST_RE.search(cmd) and base is not None:
            src = base / f"{stem}.c"
            src_cpp = base / f"{stem}.cpp"
            if src.is_file() or src_cpp.is_file():
                return path_looks_like_gui(src if src.is_file() else src_cpp)
            # compile+run of unknown exe: do not auto-background (hello world)
            continue
        return True
    return False


def compile_only_verify_command(command: str) -> str:
    """Drop a trailing ``&& prog.exe`` run so verify is compile/syntax only."""
    cmd = (command or "").strip()
    if not cmd:
        return cmd
    # gcc/clang … && hello.exe  →  keep the compile half
    m = re.search(r"(?i)^(.+?)\s+&&\s+(?:\.\\|\./)?[\w.-]+\.exe\s*$", cmd)
    if m and _COMPILER_OR_TEST_RE.search(m.group(1)):
        return m.group(1).strip()
    m = re.search(r"(?i)^(.+?)\s+&&\s+\./[\w.-]+\s*$", cmd)
    if m and _COMPILER_OR_TEST_RE.search(m.group(1)):
        return m.group(1).strip()
    # python game.py → py_compile
    pm = _PY_FILE_RE.search(cmd)
    if pm and not _COMPILER_OR_TEST_RE.search(cmd):
        return f"python -m py_compile {pm.group(1)}"
    return cmd
