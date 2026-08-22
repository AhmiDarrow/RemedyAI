"""Game-engine binary discovery (Godot, LÖVE, Unity, Unreal).

Resolution order for :func:`find_engine_binary`:

1. env override (``GODOT``, ``GODOT4_BIN``, ``LOVE``, ``UNITY_EDITOR``, ``UE_ROOT``)
2. PATH (``shutil.which`` over candidate names)
3. repo root (``Godot*.exe`` next to ``project.godot``, ``*_console.exe`` first)
4. common install globs built from env vars / ``Path.home()`` — never literal
   user paths.

Nothing here runs an engine; it only locates files.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from remedy.core.project_fingerprint import StackFingerprint


@dataclass(frozen=True)
class EngineSpec:
    """Where to look for one engine's runnable binary."""

    name: str
    env: tuple[str, ...] = ()
    path_names: tuple[str, ...] = ()
    repo_glob: str = ""  # glob under the project root
    prefer: str = ""  # substring preferred among repo-root matches
    skill: str = ""


ENGINES: dict[str, EngineSpec] = {
    "godot": EngineSpec(
        name="godot",
        env=("GODOT", "GODOT4_BIN"),
        path_names=("godot4", "godot", "godot4-console", "Godot_v4"),
        repo_glob="Godot*.exe",
        prefer="console",
        skill="godot-4",
    ),
    "love2d": EngineSpec(
        name="love2d",
        env=("LOVE",),
        path_names=("love", "lovec"),
        skill="love2d",
    ),
    "unity": EngineSpec(name="unity", env=("UNITY_EDITOR",), skill="unity"),
    "unreal": EngineSpec(name="unreal", env=("UE_ROOT",), skill="unreal"),
    "phaser": EngineSpec(name="phaser", skill="web-games"),
    "pixi": EngineSpec(name="pixi", skill="web-games"),
    "bevy": EngineSpec(name="bevy", skill="bevy"),
    "pygame": EngineSpec(name="pygame", skill="pygame-arcade"),
    "arcade": EngineSpec(name="arcade", skill="pygame-arcade"),
}

_GODOT_SMOKE_PATTERNS = ("smoke_*.gd", "diag_*.gd", "boot_*.gd", "validate_*.gd")


# ---------------------------------------------------------------------------
# Path builders (env / home only)
# ---------------------------------------------------------------------------


def _env_dir(*keys: str) -> Path | None:
    for k in keys:
        v = os.environ.get(k, "").strip()
        if v:
            return Path(v)
    return None


def _program_files() -> list[Path]:
    out: list[Path] = []
    for k in ("PROGRAMFILES", "ProgramFiles", "PROGRAMFILES(X86)", "ProgramW6432"):
        d = _env_dir(k)
        if d is not None and d not in out:
            out.append(d)
    return out


def _home() -> Path:
    try:
        return Path(os.environ.get("HOME") or Path.home())
    except Exception:
        return Path(".")


def _safe_glob(base: Path, pattern: str) -> list[Path]:
    try:
        if not base.is_dir():
            return []
        return sorted(p for p in base.glob(pattern) if p.is_file())
    except OSError:
        return []


def _version_key(p: Path) -> tuple[int, ...]:
    nums = re.findall(r"\d+", p.as_posix())
    return tuple(int(n) for n in nums[-4:]) if nums else ()


@dataclass
class _Glob:
    base: Path
    pattern: str
    prefer: str = ""
    newest: bool = False


def glob_candidates(name: str, *, version: str | None = None) -> list[_Glob]:
    """Common install locations for *name* (all env/home-relative)."""
    out: list[_Glob] = []
    home = _home()
    if name == "godot":
        local = _env_dir("LOCALAPPDATA")
        if local is not None:
            out.append(_Glob(local / "Programs" / "Godot", "*.exe", "console"))
        for pf in _program_files():
            out.append(_Glob(pf / "Godot", "*.exe", "console"))
        out.append(_Glob(home, "Godot*/Godot*.exe", "console", newest=True))
        out.append(_Glob(Path("/Applications/Godot.app/Contents/MacOS"), "Godot"))
        out.append(_Glob(Path("/usr/bin"), "godot4"))
        out.append(_Glob(Path("/usr/local/bin"), "godot4"))
    elif name == "love2d":
        for pf in _program_files():
            out.append(_Glob(pf / "LOVE", "lovec.exe"))
            out.append(_Glob(pf / "LOVE", "love.exe"))
        out.append(_Glob(Path("/Applications/love.app/Contents/MacOS"), "love"))
    elif name == "unity":
        ver = (version or "").strip() or "*"
        for pf in _program_files():
            out.append(
                _Glob(pf / "Unity" / "Hub" / "Editor", f"{ver}/Editor/Unity.exe", newest=ver == "*")
            )
        out.append(_Glob(Path("/Applications/Unity/Hub/Editor"), f"{ver}/Unity.app/Contents/MacOS/Unity", newest=True))
        out.append(_Glob(home / "Unity" / "Hub" / "Editor", f"{ver}/Editor/Unity", newest=True))
    elif name == "unreal":
        assoc = (version or "").strip() or "*"
        for pf in _program_files():
            out.append(
                _Glob(pf / "Epic Games", f"UE_{assoc}/Engine/Build/BatchFiles/RunUAT.bat", newest=True)
            )
        if os.name != "nt":
            out.append(_Glob(Path("/Users/Shared/Epic Games"), f"UE_{assoc}/Engine/Build/BatchFiles/RunUAT.sh", newest=True))
            out.append(_Glob(home / "UnrealEngine", "Engine/Build/BatchFiles/RunUAT.sh"))
    return out


def _pick(matches: list[Path], prefer: str, newest: bool) -> Path | None:
    if not matches:
        return None
    if newest:
        matches = sorted(matches, key=_version_key, reverse=True)
    if prefer:
        for m in matches:
            if prefer in m.name.lower():
                return m
    return matches[0]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_repo_godot(root: Path) -> Path | None:
    """``Godot*.exe`` in the repo root, ``*_console.exe`` first."""
    matches = _safe_glob(root, "Godot*.exe")
    return _pick(matches, "console", newest=False)


def find_engine_binary(
    name: str, *, project_root: Path | None = None, version: str | None = None
) -> Path | None:
    """Locate a runnable binary for engine *name* (see module doc for order)."""
    spec = ENGINES.get(name)
    if spec is None:
        return None
    # 1. env override
    for key in spec.env:
        v = os.environ.get(key, "").strip()
        if not v:
            continue
        p = Path(v).expanduser()
        try:
            if p.is_file():
                return p
            if p.is_dir() and name == "unreal":
                for rel in ("Engine/Build/BatchFiles/RunUAT.bat", "Engine/Build/BatchFiles/RunUAT.sh"):
                    if (p / rel).is_file():
                        return p / rel
        except OSError:
            continue
    # 2. PATH
    for n in spec.path_names:
        found = shutil.which(n)
        if found:
            return Path(found)
    # 3. repo root
    if project_root is not None and spec.repo_glob:
        local = _pick(_safe_glob(Path(project_root), spec.repo_glob), spec.prefer, newest=False)
        if local is not None:
            return local
    # 4. install globs
    for g in glob_candidates(name, version=version):
        hit = _pick(_safe_glob(g.base, g.pattern), g.prefer, g.newest)
        if hit is not None:
            return hit
    return None


def find_godot_smoke_script(root: Path) -> Path | None:
    """Prefer tools/smoke_*.gd > boot_* > diag_* > validate_* (relative path)."""
    tools = root / "tools"
    if not tools.is_dir():
        return None
    found: list[Path] = []
    try:
        for pat in _GODOT_SMOKE_PATTERNS:
            found.extend(tools.glob(pat))
    except OSError:
        return None

    def rank(p: Path) -> tuple[int, str]:
        n = p.name.lower()
        if n.startswith("smoke"):
            return (0, n)
        if n.startswith("boot"):
            return (1, n)
        if n.startswith("diag"):
            return (2, n)
        return (3, n)

    found = [p for p in found if p.is_file()]
    if not found:
        return None
    best = sorted(found, key=rank)[0]
    try:
        return best.relative_to(root)
    except ValueError:
        return Path("tools") / best.name


def _binary_invocation(root: Path, binary: Path) -> str:
    try:
        rel = binary.resolve().relative_to(root.resolve())
        if len(rel.parts) == 1:
            return f".\\{rel.name}" if os.name == "nt" else f"./{rel.name}"
    except (ValueError, OSError):
        pass
    return f'"{binary}"'


def godot_verify_command(project_root: Path | str | None, binary: Path | str | None) -> str | None:
    """Headless smoke (tools/smoke_*.gd) or ``--quit-after 1`` for a Godot project."""
    if binary is None or project_root is None:
        return None
    root = Path(project_root)
    exe = _binary_invocation(root, Path(binary))
    smoke = find_godot_smoke_script(root)
    if smoke is not None:
        return f"{exe} --headless --path . -s {smoke.as_posix()}"
    return f"{exe} --headless --path . --quit-after 1"


def engine_skill(name: str) -> str:
    spec = ENGINES.get(name)
    return spec.skill if spec else ""


def engine_summary(fp: StackFingerprint) -> str:
    """One-line ``godot 4.3 (gdscript) — <binary>`` summary for context."""
    eng = dict(getattr(fp, "engine", None) or {})
    name = eng.get("name", "")
    if not name:
        return ""
    head = name
    if eng.get("version"):
        head += f" {eng['version']}"
    if eng.get("lang"):
        head += f" ({eng['lang']})"
    binary = eng.get("binary", "")
    if binary:
        return f"{head} — {binary}"
    spec = ENGINES.get(name)
    if spec is not None and spec.env:
        return f"{head} — binary not found: set {spec.env[0]} or tell me where it is"
    return head
