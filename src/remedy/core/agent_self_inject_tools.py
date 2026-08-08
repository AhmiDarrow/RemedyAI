"""Self-inject tool registration: expose the self-improvement loop to the agent.

Tools here drive ``remedy.core.self_inject`` so the agent (or an idle trigger)
can run a test-gated improvement round: draft -> snapshot -> gate -> apply/
rollback -> record -> continue. See ``docs/SELF_INJECT.md``.
"""

from __future__ import annotations

import json
from typing import Any

from remedy.core.self_inject import (
    SelfInjectRound,
    append_ledger,
    apply_or_rollback,
    git_capture,
    read_ledger,
    run_gate,
    should_run_now,
)


def _repo_root(runtime: Any) -> str:
    """The RemedyAI monorepo root (this codebase).

    Prefers the live source tree (where ``pyproject.toml`` names this product),
    falling back to the package location, then cwd. Never returns a path that is
    not this repo (avoids self-injecting into an unrelated tree).
    """
    from pathlib import Path

    def _is_remedy_pp(text: str) -> bool:
        return "name = \"remedy-ai\"" in text or "remedy-ai" in text

    candidates: list[Path] = []
    try:
        import remedy as _pkg

        candidates.append(Path(_pkg.__file__).resolve().parent.parent.parent)
    except Exception:
        pass
    candidates.append(Path.cwd())

    for start in candidates:
        cur = start
        for _ in range(6):
            pp = cur / "pyproject.toml"
            if pp.exists():
                with __import__("contextlib").suppress(Exception):
                    if _is_remedy_pp(pp.read_text(encoding="utf-8", errors="ignore")):
                        return str(cur)
            if (cur / ".git").exists():
                break
            parent = cur.parent
            if parent == cur:
                break
            cur = parent
    return str(candidates[0])


def register_self_inject_tools(runtime: Any) -> None:
    """Register self-inject loop tools on the runtime tool registry."""

    async def tool_self_inject_status(limit: int = 20) -> str:
        """Return the self-inject audit ledger (recent rounds) + trigger state."""
        home = getattr(runtime, "home_dir", None)
        ledger = read_ledger(home)
        recent = ledger[-limit:] if limit else ledger
        lines = [
            f"self-inject enabled={should_run_now(home) or True} "
            f"(ledger rounds={len(ledger)})",
        ]
        for r in reversed(recent):
            lines.append(
                f"- {r.get('round_id','?')} {r.get('status','?')} "
                f"tree={r.get('tree','')} outcome={r.get('outcome','')} "
                f"started={r.get('started_utc','')}"
            )
        return "\n".join(lines) or "no rounds recorded"

    async def tool_self_inject_round(
        tree: str = "python",
        *,
        apply: bool = True,
        timeout: float = 900.0,
        focus: str = "auto",
    ) -> str:
        """Run ONE self-inject round on the Remedy codebase.

        tree=python | desktop | both. Snapshots the git diff, gates with tests
        only (pytest / npm test), applies on green (restart sidecar / rebuild
        SPA) or rolls back on red, and records the round in the audit ledger.

        focus=auto (default) picks a continuity-gap target (soul/memory paths)
        when continuity score is weak; focus=code leaves free-form agent edits;
        focus=continuity forces continuity-module targeting notes into the round.
        """
        repo = _repo_root(runtime)
        home = getattr(runtime, "home_dir", None) or getattr(
            getattr(runtime, "config", None), "home_dir", None
        )
        round_ = SelfInjectRound(tree=tree)
        focus_n = (focus or "auto").strip().lower()
        # Continuity-targeted self-improvement (organism densifying its own memory)
        with __import__("contextlib").suppress(Exception):
            from remedy.memory.soul.continuity_metrics import primary_self_inject_focus

            cont = primary_self_inject_focus(home)
            if focus_n in ("auto", "continuity") and cont.get("overall", 1.0) < 0.72:
                tgt = cont.get("focus") or {}
                round_.summary = str(cont.get("summary") or "")[:240]
                round_.detail["continuity_focus"] = tgt
                round_.detail["continuity_score"] = cont.get("score")
                # Soft note for ledger / agent: which path to improve
                if tgt.get("path"):
                    round_.detail["suggested_path"] = tgt["path"]
                    round_.detail["suggested_why"] = tgt.get("why") or ""
            elif focus_n == "continuity":
                round_.detail["continuity_focus"] = cont.get("focus")
                round_.summary = str(cont.get("summary") or "continuity focus")[:240]
        try:
            snapshot = await git_capture(repo)
            round_.detail["head"] = snapshot.get("head")
            round_ = await run_gate(round_, repo, timeout=timeout)
            if apply:
                round_ = await apply_or_rollback(
                    round_, repo, snapshot, home=home
                )
            else:
                round_.status = "gated_only"
                round_.outcome = "noop"
            append_ledger(round_, home)
        except Exception as e:  # noqa: BLE001
            round_.status = "error"
            round_.summary = f"round failed: {e}"
            round_.finished_utc = __import__(
                "remedy.core.self_inject", fromlist=["_now_utc"]
            )._now_utc()
            with __import__("contextlib").suppress(Exception):
                append_ledger(round_, home)
            return f"self-inject round error: {e}"

        return json.dumps(round_.to_ledger(), indent=2, ensure_ascii=False)

    runtime.tool_registry.register_builtin_handler(
        "self_inject_status",
        "Show the self-inject improvement loop audit ledger (recent rounds) and "
        "whether auto-triggering is active. Use when asked about self-improvement "
        "history, injected fixes, or loop state.",
        tool_self_inject_status,
        {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max recent rounds to show (default 20).",
                }
            },
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "self_inject_round",
        "Run ONE test-gated self-improvement round on the Remedy codebase: "
        "snapshot -> pytest/npm test gate -> apply (restart sidecar / rebuild SPA) "
        "on green or roll back on red -> record in the audit ledger. "
        "focus=auto targets continuity/soul gaps when personhood score is weak. "
        "Use when asked to improve/self-inject/fix Remedy's own code or memory.",
        tool_self_inject_round,
        {
            "type": "object",
            "properties": {
                "tree": {
                    "type": "string",
                    "enum": ["python", "desktop", "both"],
                    "description": "Which surface to gate (default python).",
                },
                "apply": {
                    "type": "boolean",
                    "description": "Apply on green / roll back on red (default true).",
                },
                "timeout": {
                    "type": "number",
                    "description": "Per-command timeout seconds (default 900).",
                },
                "focus": {
                    "type": "string",
                    "enum": ["auto", "continuity", "code"],
                    "description": "auto=continuity if weak; continuity=force; code=skip.",
                },
            },
        },
    )
