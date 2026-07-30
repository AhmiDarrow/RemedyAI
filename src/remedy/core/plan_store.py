"""Structured task plans for Plan mode (personal partner Phase B).

Not a multi-agent DAG runtime — a durable checklist the user can approve
before Build mode runs tools. Stored under ``~/.remedy/plans/``.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

# Lifecycle sets used by store + desktop Plan banner.
PLAN_TERMINAL_STATUSES = frozenset({"done", "cancelled"})
PLAN_ACTIONABLE_STATUSES = frozenset({"draft", "approved", "active"})


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _slug(text: str, *, max_len: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:max_len] or "plan").rstrip("-")


@dataclass
class PlanStep:
    """One micro-step in a task plan."""

    id: str
    title: str
    detail: str = ""
    status: str = "pending"  # pending | active | done | skipped
    risks: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PlanStep:
        return cls(
            id=str(raw.get("id") or uuid4().hex[:8]),
            title=str(raw.get("title") or "step"),
            detail=str(raw.get("detail") or ""),
            status=str(raw.get("status") or "pending"),
            risks=[str(x) for x in (raw.get("risks") or [])],
            tools=[str(x) for x in (raw.get("tools") or [])],
        )


@dataclass
class TaskPlan:
    """A user-facing plan artifact (goal → steps → risks)."""

    id: str
    title: str
    goal: str = ""
    steps: list[PlanStep] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    session_id: str | None = None
    status: str = "draft"  # draft | approved | active | done | cancelled
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "goal": self.goal,
            "steps": [s.to_dict() for s in self.steps],
            "risks": list(self.risks),
            "session_id": self.session_id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TaskPlan:
        steps_raw = raw.get("steps") or []
        steps = [
            PlanStep.from_dict(s) if isinstance(s, dict) else PlanStep(id=uuid4().hex[:8], title=str(s))
            for s in steps_raw
        ]
        return cls(
            id=str(raw.get("id") or uuid4().hex[:12]),
            title=str(raw.get("title") or "Untitled plan"),
            goal=str(raw.get("goal") or ""),
            steps=steps,
            risks=[str(x) for x in (raw.get("risks") or [])],
            session_id=raw.get("session_id"),
            status=str(raw.get("status") or "draft"),
            created_at=str(raw.get("created_at") or _now()),
            updated_at=str(raw.get("updated_at") or _now()),
            metadata=dict(raw.get("metadata") or {}),
        )

    def summary_markdown(self) -> str:
        lines = [
            f"# Plan: {self.title}",
            f"**Status:** {self.status}",
        ]
        if self.goal:
            lines.append(f"**Goal:** {self.goal}")
        lines.append("")
        lines.append("## Steps")
        for i, step in enumerate(self.steps, 1):
            mark = {
                "done": "[x]",
                "active": "[>]",
                "skipped": "[-]",
            }.get(step.status, "[ ]")
            lines.append(f"{i}. {mark} **{step.title}**")
            if step.detail:
                lines.append(f"   - {step.detail}")
            if step.tools:
                lines.append(f"   - tools: {', '.join(step.tools)}")
            if step.risks:
                lines.append(f"   - risks: {', '.join(step.risks)}")
        if self.risks:
            lines.append("")
            lines.append("## Overall risks")
            for r in self.risks:
                lines.append(f"- {r}")
        lines.append("")
        lines.append("_Switch to **Build** mode to execute. Approve the plan first if it is still draft._")
        return "\n".join(lines)


class PlanStore:
    """Filesystem-backed plan store under ``{home}/plans``."""

    def __init__(self, home_dir: Path | str | None = None) -> None:
        home = Path.home() / ".remedy" if home_dir is None else Path(home_dir).expanduser()
        self.home = home
        self.root = home / "plans"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, plan_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "", plan_id) or "plan"
        return self.root / f"{safe}.json"

    def save(self, plan: TaskPlan) -> TaskPlan:
        plan.updated_at = _now()
        path = self._path(plan.id)
        path.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
        return plan

    def get(self, plan_id: str) -> TaskPlan | None:
        path = self._path(plan_id)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        return TaskPlan.from_dict(raw)

    def list_plans(
        self,
        *,
        session_id: str | None = None,
        limit: int = 50,
    ) -> list[TaskPlan]:
        """List plans newest-first.

        When *session_id* is set, only plans tagged with that exact session
        are returned (untagged / other-session plans are excluded).
        """
        items: list[TaskPlan] = []
        for path in sorted(self.root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict):
                continue
            plan = TaskPlan.from_dict(raw)
            if session_id is not None:
                # Strict: never leak another session's plan into this chat.
                if str(plan.session_id or "") != str(session_id):
                    continue
            items.append(plan)
            if len(items) >= limit:
                break
        return items

    def latest_for_session(
        self,
        session_id: str | None,
        *,
        actionable_only: bool = False,
    ) -> TaskPlan | None:
        """Latest plan for *session_id*, or global latest when session is None.

        Never returns another session's plan when a session id is provided.

        When *actionable_only* is True, skip terminal statuses (done / cancelled)
        so the Plan banner and Build kickoff do not stick on finished/quit plans.
        """
        # Pull a window so we can skip terminal rows without another full scan.
        limit = 50 if actionable_only else 1
        if not session_id:
            plans = self.list_plans(limit=limit)
        else:
            plans = self.list_plans(session_id=str(session_id), limit=limit)
        if actionable_only:
            plans = [p for p in plans if p.status not in PLAN_TERMINAL_STATUSES]
        return plans[0] if plans else None

    def create(
        self,
        title: str,
        *,
        goal: str = "",
        steps: list[dict[str, Any] | str] | None = None,
        risks: list[str] | None = None,
        session_id: str | None = None,
        status: str = "draft",
        supersede_previous: bool = True,
    ) -> TaskPlan:
        step_objs: list[PlanStep] = []
        for i, s in enumerate(steps or []):
            if isinstance(s, str):
                step_objs.append(
                    PlanStep(id=f"s{i+1}", title=s.strip() or f"Step {i+1}")
                )
            elif isinstance(s, dict):
                if not s.get("id"):
                    s = {**s, "id": f"s{i+1}"}
                step_objs.append(PlanStep.from_dict(s))
        st = status if status in ("draft", "approved", "active", "done", "cancelled") else "draft"
        # Fresh saves with all-pending steps must not claim done/cancelled — that
        # left the Plan banner stuck on "Plan ready · done" after chat finished.
        if st in PLAN_TERMINAL_STATUSES and (
            not step_objs or all(s.status == "pending" for s in step_objs)
        ):
            st = "draft"
        # Supersede older actionable plans for this session *before* writing the
        # new file so mtime order still ranks the new plan first.
        if supersede_previous and session_id:
            for old in self.list_plans(session_id=str(session_id), limit=50):
                if old.status in PLAN_ACTIONABLE_STATUSES:
                    old.status = "cancelled"
                    self.save(old)
        pid = uuid4().hex[:12]
        plan = TaskPlan(
            id=pid,
            title=(title or "Untitled plan").strip()[:200],
            goal=(goal or title or "").strip()[:2000],
            steps=step_objs,
            risks=[str(r) for r in (risks or [])],
            session_id=session_id,
            status=st,
        )
        return self.save(plan)

    def set_status(self, plan_id: str, status: str) -> TaskPlan | None:
        plan = self.get(plan_id)
        if plan is None:
            return None
        if status not in ("draft", "approved", "active", "done", "cancelled"):
            return None
        plan.status = status
        return self.save(plan)

    def update_step_status(
        self,
        plan_id: str,
        step_id: str,
        status: str,
        *,
        auto_plan_status: bool = True,
    ) -> TaskPlan | None:
        """Flip one step's status. *step_id* may be id, 1-based index, or title.

        When *auto_plan_status* is True:
          - any non-pending step on an approved/draft plan → plan becomes ``active``
          - all steps done/skipped → plan becomes ``done``
        """
        plan = self.get(plan_id)
        if plan is None:
            return None
        st = (status or "").strip().lower()
        if st not in ("pending", "active", "done", "skipped"):
            return None
        needle = (step_id or "").strip()
        if not needle:
            return None
        target = self._find_step(plan, needle)
        if target is None:
            return None
        target.status = st
        # Drop cosmetic "[done]" title hacks once real status is set.
        target.title = _strip_step_title_status_prefix(target.title)
        if auto_plan_status:
            if st in ("active", "done", "skipped") and plan.status in (
                "draft",
                "approved",
            ):
                plan.status = "active"
            if plan.steps and all(s.status in ("done", "skipped") for s in plan.steps):
                plan.status = "done"
            elif st == "active" and plan.status == "done":
                plan.status = "active"
        return self.save(plan)

    @staticmethod
    def _find_step(plan: TaskPlan, needle: str) -> PlanStep | None:
        """Match step by id, 1-based index (``1`` / ``s1``), or title (fuzzy)."""
        n = (needle or "").strip()
        if not n:
            return None
        n_lower = n.lower()
        # Exact id
        for step in plan.steps:
            if step.id == n or step.id.lower() == n_lower:
                return step
        # 1-based index: "1", "s1", "step 1"
        m = re.match(r"^(?:s(?:tep)?\s*)?(\d+)$", n_lower)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(plan.steps):
                return plan.steps[idx]
        # Exact title
        for step in plan.steps:
            if step.title.lower() == n_lower:
                return step
        # Title without cosmetic [done]/[x] prefix
        bare_needle = _strip_step_title_status_prefix(n).lower()
        for step in plan.steps:
            bare = _strip_step_title_status_prefix(step.title).lower()
            if bare == bare_needle or bare_needle in bare or bare in bare_needle:
                return step
        return None


def _strip_step_title_status_prefix(title: str) -> str:
    """Remove agent hacks like ``[done]`` / ``[x]`` from step titles."""
    t = (title or "").strip()
    t = re.sub(
        r"^\s*\[(?:done|x|complete|completed|ok|skip(?:ped)?|active|>)\]\s*",
        "",
        t,
        flags=re.IGNORECASE,
    )
    return t.strip() or (title or "").strip()


def parse_steps_from_text(text: str) -> list[str]:
    """Best-effort extract numbered / bulleted steps from free text."""
    steps: list[str] = []
    for line in (text or "").splitlines():
        m = re.match(r"^\s*(?:\d+[\.\)]\s+|[-*•]\s+)(.+)$", line)
        if m:
            title = m.group(1).strip()
            # Strip markdown bold
            title = re.sub(r"\*\*(.+?)\*\*", r"\1", title)
            if title:
                steps.append(title[:200])
    return steps[:30]


# Tools allowed when the UI is in Plan mode (explore, no shell/file mutation).
# Read/research tools only — no shell/file writes.
PLAN_MODE_TOOL_NAMES = frozenset(
    {
        "plan_save",
        "plan_show",
        "plan_list",
        "plan_step_status",
        "goal_add",
        "goal_list",
        "memory_search",
        "skill_search",
        "skill_activate",
        "local_discover",
        "file_read",
        "list_dir",
        "repo_search",
        "web_fetch",
        "web_search",
        "media_read",
        "vision_describe",
        # Computer use (research): see rail / screen; no click/type in Plan
        "computer_screenshot",
        "computer_snapshot",
        "computer_navigate",
        "computer_windows",
        "computer_monitors",
        "computer_page_text",
        "computer_find",
        "computer_wait",
        "computer_act",
    }
)

PLAN_MODE_SYSTEM_ADDENDUM = """
## Plan mode (active)

You are in **Plan mode** — research and design only.

**Allowed:** read files, list directories, search the repo, fetch web docs, memory/skills lookup, save plans.
**Blocked:** shell, file write/edit, installs, git mutations, and other side-effect tools.

Process:
1. Research the codebase / docs as needed.
2. Ask clarifying questions when requirements are ambiguous.
3. Produce a clear structured plan (goal, numbered steps, risks, files/tools).
4. Call `plan_save` with that structure.
5. Do **not** claim work is implemented — wait for the user to **Approve → Build**.

Prefer an ASCII outline in the chat reply, e.g.:

```
Plan: <title>
Goal: …
Steps:
  1. …
  2. …
Risks: …
```
""".strip()


BUILD_MODE_SYSTEM_ADDENDUM = """
## Build mode — code efficiently

You are implementing (not only planning). Work like a senior IDE agent:

### Edits
1. **Prefer `file_edit` / `file_edit_batch`** for any change to an existing file. Use multi-hunk `edits=` when several spots in one file change.
2. **`file_write` is for new files** (or rare full rewrites with `force_full_write=true`). Do **not** dump whole large files for +few-line fixes.
3. **Never** leave scaffold junk in the project: no `_ref_*`, `_ex_*`, `_write_*.py`, `_patch_*.py` helper dumps. Read reference code from its real path (e.g. sibling repo) with `file_read` / `repo_search`.
4. Do **not** work around size limits by writing Python that writes the target file. Use surgical edits or one honest `file_write` of the real path.

### Plan progress
5. After finishing a step (and a quick verify when possible), call **`plan_step_status`** with `status=done` (or `active` when starting). Do **not** fake progress by prefixing titles with `[done]`.
6. Follow the active plan order; skip only with `status=skipped` and a reason in chat.

### Verify
7. Batch related edits, then run a focused check (`tsc`, tests, or `cargo check`) — not a full release build unless asked.
8. Prefer `repo_search` over shell `Select-String`/`findstr` for code search.

### Stop conditions
9. When all plan steps are done or the user goal is met, summarize what changed and stop — do not thrash rewrites.

### Process safety (host is alive)
10. **Never** `Get-Process app | Stop-Process`, `taskkill /IM app.exe`, or kill port **7400** / `remedy serve`. Remedy Desktop and many Tauri apps share the binary name `app.exe` — a bare kill suicides the agent. To restart a project app, filter by **Path/CommandLine** containing that project folder only (e.g. `SecretFolder`).

### Large files
11. History may show `<<NOT_SOURCE_CODE history_stub…>>` for prior writes — that is **not** the file. Always `file_read` the path before editing. Never `file_write` that stub text. Prefer `file_edit` for existing files; use `force_full_write=true` only for intentional full rewrites of real source.
""".strip()
