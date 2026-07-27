"""Spread planner — decide when to fan out (heuristics first, local optional)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from remedy.core.spread.types import SpreadTask

# Multi-area / fan-out language
_AND_AREAS = re.compile(
    r"\b("
    r"auth(?:entication)?|database|db|api|frontend|backend|ui|cli|"
    r"memory|gateway|skills?|tests?|security|desktop|vision|"
    r"routes?|models?|providers?|sessions?"
    r")\b",
    re.I,
)
_FANOUT_EXPLICIT = re.compile(
    r"\b("
    r"in parallel|fan.?out|spread out|cover more ground|across (the )?modules?|"
    r"across (the )?codebase|multiple (areas?|modules?|trees?|packages?)|"
    r"review all|map the|survey (the )?(whole|entire)|"
    r"compare .+ (and|vs|versus) "
    r")\b",
    re.I,
)
_LARGE_SURFACE = re.compile(
    r"\b("
    r"whole (repo|codebase|project)|entire (repo|codebase|project)|"
    r"all tests|full suite|codebase.?wide|repo.?wide"
    r")\b",
    re.I,
)
_PATH_LIKE = re.compile(
    r"(?:[A-Za-z]:\\|~/|\.\.?/|src/|desktop/|tests?/|packages?/)[^\s,;]+|"
    r"\b[\w.-]+/[\w./-]+\b"
)
_SINGLE_FILE = re.compile(
    r"\b(this file|that file|only (edit|fix|change)|single file)\b",
    re.I,
)
_CHATTY = re.compile(
    r"\b(what is|who are|hello|thanks|thank you|explain briefly)\b",
    re.I,
)
_BUILD_DEBUG = re.compile(
    r"\b(test|pytest|build|lint|debug|traceback|failing)\b",
    re.I,
)
_GIT = re.compile(r"\b(git|commit|diff|pr\b|pull request)\b", re.I)
_FIND = re.compile(r"\b(find|where is|locate|search for|grep)\b", re.I)


@dataclass
class SpreadPlan:
    spread: bool
    reason: str
    method: str = "heuristic"  # heuristic | local_model
    tasks: list[SpreadTask] = field(default_factory=list)
    score: int = 0
    signals: dict[str, Any] = field(default_factory=dict)

    def system_hint(self) -> str:
        if not self.spread:
            return ""
        n = len(self.tasks) or 2
        return (
            f"[Spread] Independent work detected ({self.reason}). "
            f"Prefer spread_run with ~{n} parallel workers (explore/search/diff/verify) "
            "before deep serial reads — cover ground, then synthesize. "
            "Do not spawn workers for pure chat or single-file edits."
        )

    def to_public(self) -> dict[str, Any]:
        return {
            "spread": self.spread,
            "reason": self.reason,
            "method": self.method,
            "score": self.score,
            "task_count": len(self.tasks),
            "tasks": [t.to_public() for t in self.tasks[:12]],
            "signals": dict(self.signals),
        }


def plan_spread(
    user_text: str = "",
    *,
    intent: str = "chat",
    project_path: str | None = None,
    inside_worker: bool = False,
    plan_mode: bool = False,
    use_local: bool = True,
    max_tasks: int = 6,
) -> SpreadPlan:
    """Heuristic plan; optionally refine with local Qwen if server already up."""
    if inside_worker:
        return SpreadPlan(spread=False, reason="inside_worker", score=0)
    if plan_mode:
        # Read-only scouts still useful later; for now skip shell-heavy fan-out.
        pass

    text = (user_text or "").strip()
    intent = (intent or "chat").strip().lower()
    if not text or len(text) < 16:
        return SpreadPlan(spread=False, reason="too_short", score=0)
    if _CHATTY.search(text) and intent in ("chat", "memory", "skill"):
        return SpreadPlan(spread=False, reason="chat_or_memory", score=0)
    if _SINGLE_FILE.search(text) and not _FANOUT_EXPLICIT.search(text):
        return SpreadPlan(spread=False, reason="single_file", score=0)

    score = 0
    signals: dict[str, Any] = {}

    areas = list({m.group(1).lower() for m in _AND_AREAS.finditer(text)})
    if len(areas) >= 2:
        score += 2
        signals["areas"] = areas[:8]
    if _FANOUT_EXPLICIT.search(text):
        score += 3
        signals["explicit_fanout"] = True
    if _LARGE_SURFACE.search(text):
        score += 2
        signals["large_surface"] = True

    paths = list(dict.fromkeys(m.group(0) for m in _PATH_LIKE.finditer(text)))[:8]
    if len(paths) >= 2:
        score += 2
        signals["paths"] = paths

    multi_signal = sum(
        1 for rx in (_BUILD_DEBUG, _GIT, _FIND) if rx.search(text)
    )
    if multi_signal >= 2:
        score += 1
        signals["multi_signal"] = multi_signal

    if intent in ("tool", "autonomous", "plan"):
        score += 1
        signals["intent"] = intent
    if intent == "autonomous":
        score += 1

    # Need at least 2 signal points to spread
    if score < 2:
        return SpreadPlan(
            spread=False,
            reason="score_low",
            score=score,
            signals=signals,
        )

    tasks = _tasks_from_signals(
        text,
        areas=areas,
        paths=paths,
        intent=intent,
        project_path=project_path,
        max_tasks=max_tasks,
        plan_mode=plan_mode,
    )
    if len(tasks) < 2:
        # Force a useful 2-task survey when score high but partition weak
        if score >= 3 and not plan_mode:
            tasks = [
                SpreadTask(
                    id="t1",
                    kind="explore",
                    goal="Survey project structure",
                    path=paths[0] if paths else ".",
                    query="",
                ),
                SpreadTask(
                    id="t2",
                    kind="search" if _FIND.search(text) else "diff",
                    goal=text[:200],
                    path=paths[1] if len(paths) > 1 else (paths[0] if paths else "."),
                    query=_extract_query(text),
                ),
            ]
        else:
            return SpreadPlan(
                spread=False,
                reason="cannot_partition",
                score=score,
                signals=signals,
            )

    plan = SpreadPlan(
        spread=True,
        reason=_reason_from_signals(signals),
        method="heuristic",
        tasks=tasks[:max_tasks],
        score=score,
        signals=signals,
    )

    if use_local:
        refined = _try_local_refine(text, plan, max_tasks=max_tasks)
        if refined is not None:
            return refined
    return plan


def _reason_from_signals(signals: dict[str, Any]) -> str:
    parts: list[str] = []
    if signals.get("explicit_fanout"):
        parts.append("explicit fan-out request")
    if signals.get("areas"):
        parts.append(f"multi-area: {', '.join(signals['areas'][:4])}")
    if signals.get("paths"):
        parts.append(f"{len(signals['paths'])} paths")
    if signals.get("large_surface"):
        parts.append("large surface")
    if signals.get("intent") == "autonomous":
        parts.append("work-alone")
    return "; ".join(parts) if parts else "parallelizable work"


def _extract_query(text: str) -> str:
    # Prefer quoted string or last path-ish token
    m = re.search(r'["\']([^"\']{3,80})["\']', text)
    if m:
        return m.group(1)
    words = [w for w in re.split(r"\s+", text) if len(w) > 3 and "/" not in w][:6]
    return " ".join(words[-4:]) if words else text[:80]


def _tasks_from_signals(
    text: str,
    *,
    areas: list[str],
    paths: list[str],
    intent: str,
    project_path: str | None,
    max_tasks: int,
    plan_mode: bool,
) -> list[SpreadTask]:
    tasks: list[SpreadTask] = []
    # Path-based explores
    for i, p in enumerate(paths[: max_tasks - 1]):
        tasks.append(
            SpreadTask(
                id=f"p{i + 1}",
                kind="explore",
                goal=f"Explore {p}",
                path=p,
                query="",
            )
        )
    # Area-based explores when no paths
    if not paths and len(areas) >= 2:
        area_to_path = {
            "auth": "src",
            "authentication": "src",
            "database": "src",
            "db": "src",
            "api": "src/remedy/interfaces",
            "memory": "src/remedy/memory",
            "gateway": "src/remedy/gateway",
            "skill": "src/remedy/skills",
            "skills": "src/remedy/skills",
            "test": "tests",
            "tests": "tests",
            "desktop": "desktop/src",
            "vision": "src/remedy/vision",
            "security": "src/remedy/core",
            "cli": "src/remedy/interfaces",
            "session": "src/remedy/interfaces/routes",
            "sessions": "src/remedy/interfaces/routes",
            "provider": "src/remedy/core",
            "providers": "src/remedy/core",
            "route": "src/remedy/interfaces/routes",
            "routes": "src/remedy/interfaces/routes",
            "model": "src/remedy",
            "models": "src/remedy",
            "frontend": "desktop/src",
            "backend": "src/remedy",
            "ui": "desktop/src",
        }
        for i, a in enumerate(areas[:max_tasks]):
            tasks.append(
                SpreadTask(
                    id=f"a{i + 1}",
                    kind="explore",
                    goal=f"Survey {a}",
                    path=area_to_path.get(a, project_path or "."),
                    query=a,
                )
            )
    # Extra search / verify / diff when signals match
    if _FIND.search(text) and len(tasks) < max_tasks:
        tasks.append(
            SpreadTask(
                id="search1",
                kind="search",
                goal="Targeted search",
                path=paths[0] if paths else ".",
                query=_extract_query(text),
            )
        )
    if _BUILD_DEBUG.search(text) and not plan_mode and len(tasks) < max_tasks:
        tasks.append(
            SpreadTask(
                id="verify1",
                kind="verify",
                goal="Run verify/tests",
                path=paths[0] if paths else ".",
                command="",
            )
        )
    if _GIT.search(text) and len(tasks) < max_tasks:
        tasks.append(
            SpreadTask(
                id="diff1",
                kind="diff",
                goal="Git status/diff",
                path=paths[0] if paths else ".",
            )
        )
    # Deduplicate by (kind, path, query)
    seen: set[tuple[str, str, str]] = set()
    uniq: list[SpreadTask] = []
    for t in tasks:
        key = (t.kind, t.path, t.query)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(t)
    return uniq[:max_tasks]


def _try_local_refine(
    user_text: str,
    base: SpreadPlan,
    *,
    max_tasks: int,
) -> SpreadPlan | None:
    """Optional local Qwen refine — never starts server; short timeout."""
    try:
        from remedy.vision.runtime import is_running

        if not is_running():
            return None
    except Exception:
        return None
    try:
        from remedy.runtime.jobs import LocalJob, LocalRole, default_queue
        from remedy.runtime.local_infer import ensure_handlers_registered
        from remedy.vision.config import load_vision_config

        ensure_handlers_registered()
        cfg = load_vision_config()
        base_url = str(getattr(cfg, "base_url", None) or "") or "http://127.0.0.1:8742"
        prompt = (
            "Decide if the user request needs parallel silent workers.\n"
            "Reply ONLY JSON: "
            '{"spread":bool,"reason":"short","tasks":[{"id":"t1","kind":"explore|search|verify|diff",'
            '"goal":"...","path":".","query":""}]}\n'
            f"Heuristic said spread={base.spread} reason={base.reason}.\n"
            f"User: {user_text[:600]}\n"
            "Max tasks: "
            f"{max_tasks}. Prefer explore/search/diff/verify. No recursive spawn."
        )
        q = default_queue()
        job = LocalJob(
            role=LocalRole.NANO,
            kind="spread_plan",
            payload={
                "prompt": prompt,
                "base_url": base_url,
                "max_tokens": 220,
                "timeout_s": 10.0,
                "system": (
                    "You plan silent fan-out for a coding agent. "
                    "JSON only. kinds: explore,search,verify,diff."
                ),
            },
            priority=1,  # below vision
        )
        out = q.submit(job, wait=True, timeout=12.0)
        if not out.get("ok"):
            return None
        raw = out.get("result") or {}
        text = (
            str(raw.get("text") or "")
            if isinstance(raw, dict) and "text" in raw
            else str(raw)
        )
        parsed = _parse_local_json(text)
        if not parsed:
            return None
        spread = bool(parsed.get("spread"))
        tasks_raw = parsed.get("tasks") if isinstance(parsed.get("tasks"), list) else []
        tasks = [
            SpreadTask.from_dict(t, index=i)
            for i, t in enumerate(tasks_raw)
            if isinstance(t, dict)
        ][:max_tasks]
        if spread and len(tasks) < 2:
            # Keep heuristic tasks if local forgot partition
            tasks = base.tasks
        if not spread:
            return SpreadPlan(
                spread=False,
                reason=str(parsed.get("reason") or "local_no"),
                method="local_model",
                tasks=[],
                score=base.score,
                signals={**base.signals, "local_raw": text[:120]},
            )
        return SpreadPlan(
            spread=True,
            reason=str(parsed.get("reason") or base.reason),
            method="local_model",
            tasks=tasks or base.tasks,
            score=base.score + 1,
            signals={**base.signals, "local_refined": True},
        )
    except Exception:
        return None


def _parse_local_json(text: str) -> dict[str, Any] | None:
    import json

    s = (text or "").strip()
    if not s:
        return None
    # Extract first {...} block
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
