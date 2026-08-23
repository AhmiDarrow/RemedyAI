"""Native game tools: Godot run/check/export/import, project info, playtest loop.

Every engine subprocess goes through the same sandbox helpers ``bash_exec``
uses (write-jail roots, approvals, env scrubbing). Windowed launches reuse
``shell._spawn_background`` so the turn can observe the window afterwards.
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from remedy.core import game_engines, godot_scene
from remedy.core.errors import format_tool_error

_TAIL_CHARS = 6_000
_CHECK_FILE_CAP = 200
_CHECK_ENGINE_CAP = 40
_CHECK_CONCURRENCY = 4
_SKIP_DIRS = {".godot", "addons", ".git", ".import"}
_PNG_RE = re.compile(r"[A-Za-z]:\\[^\s\"']+\.png|/[^\s\"']+\.png", re.I)


def _tail(text: str, cap: int = _TAIL_CHARS) -> str:
    text = text or ""
    if len(text) <= cap:
        return text
    return f"…[{len(text) - cap} chars cut]\n" + text[-cap:]


def _clamp_timeout(value: Any, default: float = 120.0) -> float:
    try:
        t = float(value if value is not None else default)
    except (TypeError, ValueError):
        t = default
    return max(5.0, min(600.0, t))


def _resolve_root(runtime: Any, path: str) -> Path:
    raw = (path or "").strip()
    if not raw:
        return Path(runtime.effective_project_path())
    try:
        p = Path(runtime.resolve_tool_path(raw))
    except Exception:
        return Path(runtime.effective_project_path())
    if p.is_file():
        p = p.parent
    return p


def _godot_root(runtime: Any, path: str, tool: str) -> Path | str:
    """Project root holding ``project.godot`` or a formatted error string."""
    base = _resolve_root(runtime, path)
    root = godot_scene.project_root_for(base) if base.exists() else None
    if root is None:
        return format_tool_error(
            f"no project.godot found at or above {base}",
            code="NO_GODOT_PROJECT",
            tool_name=tool,
            suggestion="Pass path= pointing at the folder that holds project.godot.",
        )
    return root


def _no_binary_error(tool: str, root: Path) -> str:
    return format_tool_error(
        "no Godot binary found",
        code="NO_ENGINE",
        tool_name=tool,
        suggestion=(
            "Set the GODOT env var to the executable, drop Godot*.exe next to "
            f"project.godot in {root}, or tell me where Godot is installed."
        ),
    )


def _find_godot(root: Path) -> Path | None:
    return game_engines.find_engine_binary("godot", project_root=root)


def _approval_block(runtime: Any, tool: str, command: str) -> str | None:
    """Same partner-trust gate bash_exec applies (ask mode → APPROVAL_REQUIRED)."""
    try:
        from remedy.core.approvals import APPROVALS
        from remedy.core.turn_context import turn_session_id
    except Exception:
        return None
    reason = APPROVALS.needs_ask(command, tool_name=tool)
    sid = turn_session_id(runtime)
    if not reason or APPROVALS.is_approved(tool, command, session_id=sid):
        return None
    item = APPROVALS.create(tool_name=tool, command=command, reason=reason, session_id=sid)
    return (
        f"APPROVAL_REQUIRED id={item.id}\n"
        f"reason={reason}\n"
        f"command={command[:400]}\n"
        "Do not invent success. Tell the user this needs approval in the UI "
        f"(or /approve {item.id}). After they approve, retry {tool}."
    )


def _write_roots(runtime: Any) -> list[Path]:
    try:
        roots = list(runtime.write_roots() or [])
    except Exception:
        roots = []
    return roots


async def _sandbox_run(runtime: Any, argv: list[str], *, cwd: Path, timeout: float) -> Any:
    """Run argv through SubprocessSandbox exactly like bash_exec does."""
    from remedy.core.project_fingerprint import path_env_with_local_bins
    from remedy.execution.sandbox import SubprocessSandbox, allowed_paths_for_shell

    roots = _write_roots(runtime) or [cwd]
    sandbox = SubprocessSandbox(allowed_paths=allowed_paths_for_shell(roots, cwd))
    env = path_env_with_local_bins(cwd)
    return await sandbox.execute(argv, workdir=cwd, timeout_seconds=timeout, env=env)


def _spawn(argv: list[str], *, cwd: Path, command: str) -> str:
    from remedy.core.project_fingerprint import path_env_with_local_bins
    from remedy.core.workspace_tools.shell import _spawn_background

    return _spawn_background(argv, cwd=cwd, env=path_env_with_local_bins(cwd), command=command)


def _argv_text(argv: list[str]) -> str:
    return " ".join(f'"{a}"' if " " in a else a for a in argv)


def _result_text(argv: list[str], cwd: Path, result: Any, started: float) -> str:
    parts = [
        f"exit_code={result.exit_code}",
        f"duration_s={time.monotonic() - started:.1f}",
        f"cwd={cwd}",
        f"command={_argv_text(argv)}",
    ]
    if result.stdout:
        parts.append(_tail(result.stdout))
    if result.stderr:
        parts.append("stderr:\n" + _tail(result.stderr))
    return "\n".join(parts)


def _iter_project_files(root: Path, suffixes: tuple[str, ...], cap: int) -> list[Path]:
    out: list[Path] = []
    try:
        for p in sorted(root.rglob("*")):
            if len(out) >= cap:
                break
            if any(part in _SKIP_DIRS for part in p.relative_to(root).parts[:-1]):
                continue
            if p.is_file() and p.suffix.lower() in suffixes:
                out.append(p)
    except OSError:
        pass
    return out


def _select_paths(root: Path, spec: str) -> list[Path]:
    spec = (spec or "").strip()
    if not spec:
        return _iter_project_files(root, (".gd", ".tscn", ".tres"), _CHECK_FILE_CAP)
    out: list[Path] = []
    for item in (s.strip() for s in spec.split(",")):
        if not item:
            continue
        if any(ch in item for ch in "*?["):
            hits = sorted(root.glob(item))
        else:
            p = Path(item)
            hits = [p if p.is_absolute() else root / p]
        for h in hits:
            if h.is_file() and h not in out:
                out.append(h)
    return out[:_CHECK_FILE_CAP]


def parse_export_presets(text: str) -> list[dict[str, str]]:
    """``[preset.N]`` blocks → [{name, platform, export_path}] (options blocks skipped)."""
    presets: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"^\[preset\.(\d+)(\.options)?\]$", line)
        if m:
            if m.group(2):
                current = None
            else:
                current = {"index": m.group(1), "name": "", "platform": "", "export_path": ""}
                presets.append(current)
            continue
        if current is None or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key in ("name", "platform", "export_path"):
            current[key] = val.strip().strip('"')
    return presets


def _parse_keys(spec: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for item in (s.strip() for s in (spec or "").split(",")):
        if not item:
            continue
        key, _, hold = item.partition(":")
        try:
            ms = int(hold) if hold else 0
        except ValueError:
            ms = 0
        out.append((key.strip(), max(0, ms)))
    return out


def _log_file_from(command: str) -> Path | None:
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        return None
    for i, part in enumerate(parts):
        if part == "--log-file" and i + 1 < len(parts):
            return Path(parts[i + 1].strip('"'))
        if part.startswith("--log-file="):
            return Path(part.split("=", 1)[1].strip('"'))
    return None


def register_game_tools(runtime: Any) -> None:
    """Register game_project_info, godot_run/check/export/import, game_playtest."""

    async def game_project_info(path: str = "") -> str:
        """Stack/engine fingerprint of a game project as JSON."""
        from remedy.core.project_fingerprint import fingerprint_path

        root = _resolve_root(runtime, path)
        if not root.is_dir():
            return format_tool_error(
                f"not a directory: {root}",
                code="BAD_PATH",
                tool_name="game_project_info",
                suggestion="Pass path= to a project folder.",
            )
        fp = await asyncio.to_thread(fingerprint_path, root)
        engine = dict(fp.engine)
        name = engine.get("name", "")
        if name and not engine.get("binary"):
            found = game_engines.find_engine_binary(name, project_root=root)
            if found is not None:
                engine["binary"] = str(found)
        counts: dict[str, int] = {}
        for suffix in (".gd", ".tscn", ".tres", ".lua", ".cs", ".py", ".js", ".ts"):
            n = len(_iter_project_files(root, (suffix,), 5000))
            if n:
                counts[suffix] = n
        info = {
            "path": str(root),
            "stacks": list(fp.stacks),
            "markers": list(fp.markers),
            "hints": list(fp.hints),
            "engine": engine,
            "skill": game_engines.engine_skill(name) if name else "",
            "suggest_verify": fp.suggest_verify,
            "counts": counts,
        }
        return json.dumps(info, indent=2, default=str)

    async def godot_run(
        path: str = "",
        scene: str = "",
        script: str = "",
        headless: bool = True,
        timeout_seconds: float = 120,
        extra_args: str = "",
        quit_after: int = 0,
    ) -> str:
        """Run a Godot project (headless sync, or windowed in the background)."""
        root = _godot_root(runtime, path, "godot_run")
        if isinstance(root, str):
            return root
        binary = _find_godot(root)
        if binary is None:
            return _no_binary_error("godot_run", root)
        argv = [str(binary), "--path", str(root)]
        if headless:
            argv.append("--headless")
        if script.strip():
            argv += ["-s", script.strip()]
        elif scene.strip():
            argv.append(scene.strip())
        try:
            qa = int(quit_after or 0)
        except (TypeError, ValueError):
            qa = 0
        if qa > 0:
            argv += ["--quit-after", str(qa)]
        if extra_args.strip():
            try:
                extra = shlex.split(extra_args)
            except ValueError as exc:
                return format_tool_error(
                    f"bad extra_args: {exc}",
                    code="BAD_ARGS",
                    tool_name="godot_run",
                    suggestion="Quote extra_args like a shell command line.",
                )
            blocked_flags = ("--path", "--export-release", "--export-debug", "--write-movie")
            if any(a == f or a.startswith(f + "=") for a in extra for f in blocked_flags):
                return format_tool_error(
                    "extra_args cannot retarget --path or export/write-movie",
                    code="BAD_ARGS",
                    tool_name="godot_run",
                    suggestion="Use path= / godot_export for those.",
                )
            argv += extra
        command = _argv_text(argv)
        blocked = _approval_block(runtime, "godot_run", command)
        if blocked:
            return blocked
        if not headless:
            text = _spawn(argv, cwd=root, command=command)
            return (
                text
                + "\nNext: game_playtest pid=<pid> seconds=20 to watch it, or "
                "computer_screenshot target=desktop for a single frame."
            )
        timeout = _clamp_timeout(timeout_seconds)
        started = time.monotonic()
        result = await _sandbox_run(runtime, argv, cwd=root, timeout=timeout)
        return _result_text(argv, root, result, started) + f"\ntimeout_s={timeout:g}"

    async def godot_check(paths: str = "", scenes: bool = True) -> str:
        """Syntax-check .gd (engine --check-only or tokenizer) and .tscn/.tres files."""
        root = _godot_root(runtime, "", "godot_check")
        if isinstance(root, str):
            return root
        files = _select_paths(root, paths)
        gd = [p for p in files if p.suffix.lower() == ".gd"]
        scene_files = [p for p in files if p.suffix.lower() in (".tscn", ".tres")] if scenes else []
        binary = _find_godot(root) if gd else None
        results: list[dict[str, Any]] = []

        def rel(p: Path) -> str:
            try:
                return p.relative_to(root).as_posix()
            except ValueError:
                return str(p)

        engine_gd = gd[:_CHECK_ENGINE_CAP] if binary is not None else []
        if engine_gd:
            sem = asyncio.Semaphore(_CHECK_CONCURRENCY)

            async def one(p: Path) -> dict[str, Any]:
                argv = [str(binary), "--headless", "--path", str(root), "--check-only", "-s", rel(p)]
                async with sem:
                    res = await _sandbox_run(runtime, argv, cwd=root, timeout=60.0)
                ok = res.exit_code == 0
                err = "" if ok else _tail((res.stderr or res.stdout or "").strip(), 800)
                return {"path": rel(p), "ok": ok, "engine": "godot", "error": err}

            results.extend(await asyncio.gather(*(one(p) for p in engine_gd)))
        for p in gd[len(engine_gd):]:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                results.append({"path": rel(p), "ok": False, "engine": "none", "error": str(exc)})
                continue
            ok, err = godot_scene.check_gdscript_text(text)
            results.append({"path": rel(p), "ok": ok, "engine": "tokenizer", "error": err})
        for p in scene_files:
            r = godot_scene.check_scene(p)
            results.append(
                {
                    "path": rel(p),
                    "ok": bool(r.get("ok")),
                    "engine": str(r.get("engine") or "scene-parser"),
                    "error": str(r.get("error") or ""),
                }
            )
        bad = [r for r in results if not r["ok"]]
        summary = f"{len(results)} checked, {len(bad)} failed"
        if gd and binary is None:
            summary += " (no Godot binary: .gd checked by tokenizer only)"
        if len(gd) > len(engine_gd) and binary is not None:
            summary += f" (engine cap {_CHECK_ENGINE_CAP}; rest by tokenizer)"
        return json.dumps({"ok": not bad, "results": results, "summary": summary}, indent=2)

    async def godot_export(
        preset: str = "",
        output: str = "",
        debug: bool = False,
        list: bool = False,  # noqa: A002
    ) -> str:
        """Export a preset (or list presets from export_presets.cfg)."""
        root = _godot_root(runtime, "", "godot_export")
        if isinstance(root, str):
            return root
        cfg = root / "export_presets.cfg"
        if list or not preset.strip():
            if not cfg.is_file():
                return format_tool_error(
                    "no export_presets.cfg in the project",
                    code="NO_PRESETS",
                    tool_name="godot_export",
                    suggestion="Create a preset in Godot: Project → Export, then retry.",
                )
            presets = parse_export_presets(cfg.read_text(encoding="utf-8", errors="replace"))
            return json.dumps({"presets": presets}, indent=2)
        if not output.strip():
            return format_tool_error(
                "output path required",
                code="NO_OUTPUT",
                tool_name="godot_export",
                suggestion="Pass output= (e.g. build/game.exe) under the project folder.",
            )
        out = Path(output.strip())
        if not out.is_absolute():
            out = root / out
        allowed = [root, *_write_roots(runtime)]
        try:
            out_res = out.resolve(strict=False)
            inside = any(
                out_res == r.resolve(strict=False) or out_res.is_relative_to(r.resolve(strict=False))
                for r in allowed
            )
        except OSError:
            inside = False
        if not inside:
            return format_tool_error(
                f"export output {out} is outside the project / allowed roots",
                code="WRITE_JAIL",
                tool_name="godot_export",
                suggestion="Pass an output path under the project folder (e.g. build/).",
            )
        binary = _find_godot(root)
        if binary is None:
            return _no_binary_error("godot_export", root)
        flag = "--export-debug" if debug else "--export-release"
        argv = [str(binary), "--headless", "--path", str(root), flag, preset.strip(), str(out)]
        command = _argv_text(argv)
        blocked = _approval_block(runtime, "godot_export", command)
        if blocked:
            return blocked
        with suppress(OSError):
            out.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        result = await _sandbox_run(runtime, argv, cwd=root, timeout=600.0)
        if result.exit_code == 0:
            from remedy.core.workspace_tools.guards import note_path

            note_path(runtime, out)
        return _result_text(argv, root, result, started) + f"\noutput={out}"

    async def godot_import(path: str = "") -> str:
        """Re-import project assets headlessly (fills .godot/imported)."""
        root = _godot_root(runtime, path, "godot_import")
        if isinstance(root, str):
            return root
        binary = _find_godot(root)
        if binary is None:
            return _no_binary_error("godot_import", root)
        argv = [str(binary), "--headless", "--path", str(root), "--import"]
        command = _argv_text(argv)
        blocked = _approval_block(runtime, "godot_import", command)
        if blocked:
            return blocked
        started = time.monotonic()
        result = await _sandbox_run(runtime, argv, cwd=root, timeout=600.0)
        imported = root / ".godot" / "imported"
        count = 0
        with suppress(OSError):
            count = sum(1 for _ in imported.iterdir()) if imported.is_dir() else 0
        return _result_text(argv, root, result, started) + f"\nimported_entries={count}"

    async def game_playtest(
        command: str = "",
        pid: int = 0,
        seconds: int = 20,
        interval: int = 5,
        keys: str = "",
        question: str = "",
    ) -> str:
        """Launch (optional), then screenshot the desktop every *interval* s for *seconds*."""
        reg = runtime.tool_registry
        lines: list[str] = []
        log_file = None
        root = Path(runtime.effective_project_path())
        if command.strip():
            try:
                argv = shlex.split(command, posix=False)
            except ValueError as exc:
                return format_tool_error(
                    f"bad command: {exc}",
                    code="BAD_ARGS",
                    tool_name="game_playtest",
                    suggestion="Quote the command like a shell command line.",
                )
            argv = [a.strip('"') for a in argv]
            blocked = _approval_block(runtime, "game_playtest", command)
            if blocked:
                return blocked
            spawn_text = _spawn(argv, cwd=root, command=command)
            lines.append(spawn_text.splitlines()[0] if spawn_text else "started")
            if "SPAWN_FAILED" in spawn_text:
                return spawn_text
            log_file = _log_file_from(command)
            await asyncio.sleep(3)
        elif pid:
            lines.append(f"observing pid={pid}")
        else:
            lines.append("observing the running game (no command / pid given)")

        total = max(1, min(300, int(seconds or 20)))
        step = max(1, min(60, int(interval or 5)))
        key_plan = _parse_keys(keys)
        shots: list[str] = []
        last_shot_text = ""
        elapsed = 0
        while True:
            shot = await reg.execute("computer_screenshot", target="desktop")
            shot_text = str(shot)
            if "APPROVAL_REQUIRED" in shot_text:
                return "\n".join([*lines, shot_text])
            last_shot_text = shot_text
            shots.extend(_PNG_RE.findall(shot_text))
            for key, hold_ms in key_plan:
                taps = max(1, min(10, hold_ms // 100)) if hold_ms else 1
                for _ in range(taps):
                    out = str(await reg.execute("computer_key", key=key, target="desktop"))
                    if "APPROVAL_REQUIRED" in out:
                        return "\n".join([*lines, out])
            elapsed += step
            if elapsed >= total:
                break
            await asyncio.sleep(step)
        lines.append(f"screenshots ({len(shots)}): " + (", ".join(shots) or last_shot_text[:300]))
        if log_file is not None:
            try:
                tail = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
                lines.append(f"log {log_file} (last {len(tail)} lines):\n" + "\n".join(tail))
            except OSError:
                lines.append(f"log {log_file}: not readable yet")
        if question.strip() and shots:
            try:
                answer = await reg.execute(
                    "vision_decode", action="decode", path=shots[-1], question=question
                )
                lines.append(f"vision: {answer}")
            except Exception as exc:
                lines.append(f"vision unavailable: {exc}")
        return "\n".join(lines)

    # Quick fingerprint: a short outer budget (table entries must stay >= default).
    game_project_info._remedy_timeout = 30.0  # type: ignore[attr-defined]
    game_playtest._remedy_timeout = None  # type: ignore[attr-defined]
    reg = runtime.tool_registry
    reg.register_builtin_handler(
        "game_project_info",
        "Game project fingerprint (stacks, engine/binary/version, suggested "
        "verify command, file counts) as JSON. Call first in a game project.",
        game_project_info,
        {"type": "object", "properties": {"path": {"type": "string"}}},
    )
    reg.register_builtin_handler(
        "godot_run",
        "Run a Godot 4 project. headless=true (default) runs synchronously and "
        "returns exit code + output; headless=false opens the window in the "
        "background (then use game_playtest / computer_screenshot target=desktop). "
        "script= runs a SceneTree script (-s), scene= a .tscn, quit_after=N frames.",
        godot_run,
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "scene": {"type": "string"},
                "script": {"type": "string"},
                "headless": {"type": "boolean"},
                "timeout_seconds": {"type": "number"},
                "extra_args": {"type": "string"},
                "quit_after": {"type": "integer"},
            },
        },
    )
    reg.register_builtin_handler(
        "godot_check",
        "Check GDScript (.gd via godot --check-only, tokenizer fallback without a "
        "binary) and .tscn/.tres scene files. paths= comma list or glob relative "
        "to the project; default all. Returns {ok, results, summary}.",
        godot_check,
        {
            "type": "object",
            "properties": {
                "paths": {"type": "string"},
                "scenes": {"type": "boolean"},
            },
        },
    )
    reg.register_builtin_handler(
        "godot_export",
        "Export a Godot preset headlessly (list=true shows export_presets.cfg "
        "presets). output must be under the project folder.",
        godot_export,
        {
            "type": "object",
            "properties": {
                "preset": {"type": "string"},
                "output": {"type": "string"},
                "debug": {"type": "boolean"},
                "list": {"type": "boolean"},
            },
        },
    )
    reg.register_builtin_handler(
        "godot_import",
        "Re-import Godot project assets headlessly (godot --import).",
        godot_import,
        {"type": "object", "properties": {"path": {"type": "string"}}},
    )
    reg.register_builtin_handler(
        "game_playtest",
        "Playtest a running/launched game: optionally start command= in the "
        "background, then screenshot the desktop every interval s for seconds, "
        "pressing keys= ('right:1000,space:200' key:hold_ms) between shots. "
        "question= asks the local vision model about the last frame.",
        game_playtest,
        {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "pid": {"type": "integer"},
                "seconds": {"type": "integer"},
                "interval": {"type": "integer"},
                "keys": {"type": "string"},
                "question": {"type": "string"},
            },
        },
    )
