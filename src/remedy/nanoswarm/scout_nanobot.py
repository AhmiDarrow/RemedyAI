"""Scout nanobot — cheap prep suggestions + background project warm-up.

Heuristics on the hot path; optional parallel local warm-up (list_dir / git)
off the critical path so the *next* turn already has a cache.
"""

from __future__ import annotations

import re
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

_GIT_RE = re.compile(
    r"\b(git|commit|branch|pr\b|pull request|merge|rebase|diff|stage)\b", re.I
)
_BUILD_RE = re.compile(
    r"\b(build|test|lint|ci|pytest|cargo|npm|uv |compile|typecheck)\b", re.I
)
_FIND_RE = re.compile(
    r"\b(find|where is|locate|discover|installed|which |path to)\b", re.I
)
_IMAGE_RE = re.compile(r"\b(image|comfy|flux|generate.*(pic|image|art)|screenshot)\b", re.I)
_DEBUG_RE = re.compile(
    r"\b(error|fail|bug|stack|traceback|exception|broken|not work)\b", re.I
)

_WARM_LOCK = threading.Lock()
_WARM_PENDING: set[str] = set()


class ScoutNanobot:
    """Suggest first-wave tools; cache cheap project/git warm results."""

    def __init__(self) -> None:
        self.scouts_run = 0
        self.warms_run = 0
        self.last: dict[str, Any] | None = None
        # project_path -> warm payload
        self._warm: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def scout(
        self,
        user_text: str = "",
        *,
        intent: str = "chat",
        project_path: str | None = None,
    ) -> dict[str, Any]:
        text = (user_text or "").strip()
        intent = (intent or "chat").lower()
        if intent not in ("tool", "plan", "skill") and len(text) < 12:
            out = {
                "bot": "scout",
                "active": False,
                "suggest_tools": [],
                "system_hint": "",
            }
            self.last = out
            return out

        tools: list[str] = []
        tips: list[str] = []

        if _FIND_RE.search(text) or "comfy" in text.lower():
            tools.extend(["local_discover", "list_dir"])
            tips.append("Use local_discover before hunting installs on disk.")
        if _GIT_RE.search(text):
            tools.append("bash_exec")
            tips.append("Start with git status / branch, not recursive file dumps.")
        if _BUILD_RE.search(text):
            tools.extend(["list_dir", "bash_exec"])
            tips.append("Detect project root + package manager before long builds.")
        if _IMAGE_RE.search(text):
            tools.append("comfyui")
            tips.append("Prefer comfyui tool (status/locate/generate) over manual paths.")
        if _DEBUG_RE.search(text):
            tools.extend(["list_dir", "file_read"])
            tips.append("Read the failing file and recent stderr before rewriting.")
        if intent in ("tool", "plan") and not tools:
            tools.extend(["list_dir", "file_read"])
            tips.append("Orient with list_dir on project root, then targeted reads.")

        seen: set[str] = set()
        uniq: list[str] = []
        for t in tools:
            if t not in seen:
                seen.add(t)
                uniq.append(t)

        warm = self.get_warm(project_path) if project_path else None
        hint = ""
        if uniq or tips or warm:
            parts = []
            if uniq:
                parts.append("First tools to consider: " + ", ".join(uniq[:6]))
            if tips:
                parts.append(tips[0])
            if project_path:
                parts.append(f"Project: {project_path}")
            if warm:
                if warm.get("top_entries"):
                    parts.append("Warm top-level: " + ", ".join(warm["top_entries"][:10]))
                if warm.get("git_branch"):
                    parts.append(f"git branch={warm['git_branch']}")
                if warm.get("git_dirty") is True:
                    parts.append("working tree dirty")
                elif warm.get("git_dirty") is False:
                    parts.append("working tree clean")
                if warm.get("markers"):
                    parts.append("stack: " + ", ".join(warm["markers"][:6]))
            hint = "[Continuity/Scout] " + " · ".join(parts)

        out = {
            "bot": "scout",
            "active": bool(hint),
            "suggest_tools": uniq[:8],
            "tips": tips[:4],
            "system_hint": hint,
            "intent": intent,
            "warm": bool(warm),
        }
        self.scouts_run += 1
        self.last = out
        return out

    def get_warm(self, project_path: str | None) -> dict[str, Any] | None:
        if not project_path:
            return None
        key = str(Path(project_path).expanduser())
        with self._lock:
            w = self._warm.get(key)
            if not w:
                return None
            # Stale after 10 minutes
            if time.time() - float(w.get("ts") or 0) > 600:
                return None
            return dict(w)

    def warm_project(
        self,
        project_path: str | None,
        *,
        user_text: str = "",
        force: bool = False,
    ) -> dict[str, Any]:
        """Synchronous cheap warm (list_dir + git). Call from background thread."""
        if not project_path:
            return {"ok": False, "error": "no_project"}
        root = Path(project_path).expanduser()
        try:
            root = root.resolve()
        except OSError:
            root = Path(project_path).expanduser()
        if not root.is_dir():
            return {"ok": False, "error": "not_a_directory"}
        with suppress(Exception):
            from remedy.core.work_roots import discover_work_root

            wr = discover_work_root(root)
            if wr is not None:
                root = wr

        key = str(root)
        if not force:
            existing = self.get_warm(key)
            if existing:
                return {"ok": True, "cached": True, **existing}

        top: list[str] = []
        markers: list[str] = []
        marker_names = {
            "package.json": "node",
            "pyproject.toml": "python",
            "Cargo.toml": "rust",
            "go.mod": "go",
            "pom.xml": "java",
            "build.gradle": "gradle",
            "CMakeLists.txt": "cmake",
            "requirements.txt": "python",
            "uv.lock": "uv",
            "composer.json": "php",
        }
        try:
            entries = sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            for p in entries:
                if p.name.startswith("."):
                    if p.name == ".git":
                        markers.append("git")
                    continue
                top.append(p.name + ("/" if p.is_dir() else ""))
                if p.name in marker_names:
                    markers.append(marker_names[p.name])
                if len(top) >= 24:
                    break
        except OSError as e:
            return {"ok": False, "error": str(e)}

        git_branch = None
        git_dirty = None
        git_dir = root / ".git"
        if git_dir.exists() or "git" in markers:
            try:
                from remedy.execution.process import run_hidden
                from remedy.execution.sandbox import scrub_subprocess_env

                git_env = scrub_subprocess_env()
                git_env["GIT_TERMINAL_PROMPT"] = "0"
                r = run_hidden(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=str(root),
                    timeout=2.5,
                    capture_output=True,
                    text=True,
                    env=git_env,
                )
                if r.returncode == 0:
                    git_branch = (r.stdout or "").strip() or None
                r2 = run_hidden(
                    ["git", "status", "--porcelain", "-b"],
                    cwd=str(root),
                    timeout=3.0,
                    capture_output=True,
                    text=True,
                    env=git_env,
                )
                if r2.returncode == 0:
                    lines = [ln for ln in (r2.stdout or "").splitlines() if ln.strip()]
                    # first line is branch header; any other line => dirty
                    git_dirty = len(lines) > 1
            except Exception:
                # Fallback: read .git/HEAD only
                try:
                    head = (root / ".git" / "HEAD").read_text(encoding="utf-8", errors="replace")
                    if head.startswith("ref:"):
                        git_branch = head.strip().split("/")[-1]
                except Exception:
                    pass

        payload = {
            "ok": True,
            "project_path": key,
            "top_entries": top[:16],
            "markers": list(dict.fromkeys(markers))[:8],
            "git_branch": git_branch,
            "git_dirty": git_dirty,
            "ts": time.time(),
            "user_hint": (user_text or "")[:120],
        }
        with self._lock:
            self._warm[key] = payload
            self.warms_run += 1
        return payload

    def schedule_warm(
        self,
        project_path: str | None,
        *,
        user_text: str = "",
    ) -> bool:
        """Background warm once per project (debounced)."""
        if not project_path:
            return False
        try:
            key = str(Path(project_path).expanduser().resolve())
        except OSError:
            key = str(Path(project_path).expanduser())
        with _WARM_LOCK:
            if key in _WARM_PENDING:
                return False
            if self.get_warm(key):
                return False
            _WARM_PENDING.add(key)

        def _run() -> None:
            try:
                self.warm_project(project_path, user_text=user_text)
            except Exception:
                pass
            finally:
                with _WARM_LOCK:
                    _WARM_PENDING.discard(key)

        threading.Thread(
            target=_run, name=f"remedy-scout-warm-{key[-12:]}", daemon=True
        ).start()
        return True

    def status(self) -> dict[str, Any]:
        with self._lock:
            warm_n = len(self._warm)
        return {
            "bot": "scout",
            "scouts_run": self.scouts_run,
            "warms_run": self.warms_run,
            "warm_projects": warm_n,
            "last_tools": (self.last or {}).get("suggest_tools") or [],
            "last_active": bool((self.last or {}).get("active")),
            "last_warm": bool((self.last or {}).get("warm")),
        }
