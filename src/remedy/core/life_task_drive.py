"""Machine-owned life-task drive — act → verify → retry → escalate.

Coding already has ``build_drive``. Computer-use still depended on the model
to remember that a tool-ok is not a finished grocery order. This scheduler
runs a list of local computer hands and **refuses to mark the owner's goal
done** unless each step was observed. Money / password / send / delete steps
are checkpoints: they stop for the owner and never auto-run.

The model (any provider) supplies the plan. This module is the hands + the
honesty about what was seen.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from remedy.core.build_oracle import coerce_json_text, coerce_text_arg
from remedy.core.computer.types import ComputerAction

# Verbs that must never auto-complete — owner moment, not a retry.
_CHECKPOINT_RE = re.compile(
    r"(?is)\b("
    r"place order|pay now|buy now|complete purchase|submit (the )?(order|payment|application)|"
    r"send (the )?(email|message|form)|delete (the )?(account|file|forever)|"
    r"confirm (payment|delete|purchase)|enter (your )?password|"
    r"captcha|not a robot"
    r")\b"
)

_ACTION_MAP: dict[str, ComputerAction] = {
    "navigate": ComputerAction.NAVIGATE,
    "click": ComputerAction.CLICK,
    "type": ComputerAction.TYPE,
    "fill": ComputerAction.FILL,
    "wait": ComputerAction.WAIT,
    "key": ComputerAction.KEY,
    "snapshot": ComputerAction.SNAPSHOT,
    "page_text": ComputerAction.PAGE_TEXT,
    "hover": ComputerAction.HOVER,
    "select": ComputerAction.SELECT,
    "act": ComputerAction.ACT,
    "scroll": ComputerAction.SCROLL,
}

RunAction = Callable[..., str]


@dataclass
class DriveStepResult:
    title: str
    action: str
    status: str  # done | blocked | need_you | skipped
    intended: str = ""
    observed: str = ""
    evidence: str = ""
    block_reason: str = ""
    retries: int = 0
    ok: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "action": self.action,
            "status": self.status,
            "intended": self.intended,
            "observed": self.observed,
            "evidence": self.evidence,
            "block_reason": self.block_reason,
            "retries": self.retries,
            "ok": self.ok,
        }


def parse_steps(raw: Any) -> list[dict[str, Any]]:
    """Accept a JSON string, list, or single dict of drive steps."""
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if isinstance(raw, str):
        text = coerce_json_text(raw).strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        raw = parsed
    if isinstance(raw, dict):
        raw = raw.get("steps") or [raw]
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, str) and item.strip():
            out.append({"title": item.strip(), "action": "snapshot"})
    return out[:40]


def step_is_checkpoint(step: dict[str, Any]) -> bool:
    """True when this step is an owner moment (pay / send / password / CAPTCHA)."""
    if step.get("checkpoint") is True:
        return True
    kind = str(step.get("kind") or step.get("checkpoint") or "").strip().lower()
    if kind in {"pay", "payment", "submit", "send", "delete", "password", "captcha"}:
        return True
    blob = " ".join(
        str(step.get(k) or "")
        for k in ("title", "text", "click", "label", "detail", "action")
    )
    return bool(_CHECKPOINT_RE.search(blob))


def _parse_run_blob(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {"ok": False, "message": "(empty)", "unverified": True}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    low = text.lower()
    ok = "success" in low and "unverified" not in low and "approval_required" not in low
    return {
        "ok": ok,
        "message": text[:800],
        "unverified": "unverified" in low,
        "approval_required": "approval_required" in low,
    }


def _kwargs_for_step(step: dict[str, Any]) -> dict[str, Any]:
    kw: dict[str, Any] = {}
    for src, dst in (
        ("url", "url"),
        ("text", "text"),
        ("ref", "ref"),
        ("label", "label"),
        ("query", "query"),
        ("value", "value"),
        ("key", "key"),
        ("fields", "fields"),
        ("expect_url", "expect_url"),
        ("expect_text", "expect_text"),
        ("seconds", "seconds"),
        ("hint", "hint"),
        ("target", "target"),
    ):
        if step.get(src) not in (None, ""):
            kw[dst] = step[src]
    click = coerce_text_arg(step.get("click"))
    if click and "text" not in kw:
        kw["text"] = click
    return kw


def _intended(step: dict[str, Any]) -> str:
    title = coerce_text_arg(step.get("title")) or coerce_text_arg(step.get("action")) or "step"
    bits = [title]
    for k in ("url", "text", "click", "label", "expect_text", "expect_url"):
        v = coerce_text_arg(step.get(k))
        if v:
            bits.append(f"{k}={v[:80]}")
    return " · ".join(bits)[:400]


def drive_life_task(
    *,
    goal: str,
    steps: Any,
    run_action: RunAction | None = None,
    runtime: Any = None,
    max_retries: int = 1,
) -> dict[str, Any]:
    """Run *steps* on this PC. Never claims done without an observed ok.

    *run_action* is ``executor.run`` (returns a JSON public_result string).
    Tests inject a fake. Checkpoints do not call *run_action*.
    """
    g = coerce_text_arg(goal) or "life task"
    parsed = parse_steps(steps)
    results: list[DriveStepResult] = []
    if not parsed:
        return {
            "ok": False,
            "status": "blocked",
            "goal": g,
            "steps": [],
            "markdown": (
                f"**Toward {g}** — no steps to drive. Pass steps= "
                "[{title, action, …}]."
            ),
        }

    runner = run_action
    if runner is None:
        from remedy.core.computer.executor import get_computer_executor

        ex = get_computer_executor(
            getattr(getattr(runtime, "config", None), "home_dir", None)
        )

        def runner(*args: Any, **kwargs: Any) -> str:
            return ex.run(*args, runtime=runtime, **kwargs)

    tries = max(0, min(3, int(max_retries)))

    def _do(action: ComputerAction | str, **kwargs: Any) -> dict[str, Any]:
        raw = runner(action, **kwargs)
        if isinstance(raw, dict):
            return raw
        return _parse_run_blob(str(raw))

    halted = ""
    for step in parsed:
        title = coerce_text_arg(step.get("title")) or coerce_text_arg(
            step.get("action")
        ) or "step"
        action_name = coerce_text_arg(step.get("action") or "snapshot").lower()
        rec = DriveStepResult(
            title=title,
            action=action_name,
            status="pending",
            intended=_intended(step),
        )
        if step_is_checkpoint(step):
            rec.status = "need_you"
            rec.block_reason = "need_you"
            rec.observed = (
                "Stopped on purpose — this step is an owner moment "
                "(pay, send, password, or CAPTCHA). Nothing was pressed."
            )
            results.append(rec)
            halted = "need_you"
            break

        mapped = _ACTION_MAP.get(action_name)
        if mapped is None:
            rec.status = "blocked"
            rec.block_reason = "tool_failed"
            rec.observed = f"Unknown action {action_name!r}."
            results.append(rec)
            halted = "tool_failed"
            break

        kw = _kwargs_for_step(step)
        blob = _do(mapped, **kw)
        rec.evidence = str(blob.get("message") or "")[:500]
        rec.observed = _observed_line(blob)
        rec.ok = bool(blob.get("ok")) and not blob.get("unverified")
        if blob.get("approval_required") or "APPROVAL_REQUIRED" in rec.evidence:
            rec.status = "need_you"
            rec.block_reason = "need_you"
            rec.ok = False
            rec.observed = rec.evidence or rec.observed
            results.append(rec)
            halted = "need_you"
            break

        if rec.ok:
            rec.status = "done"
            results.append(rec)
            continue

        # One re-observe + retry. Same action, fresh snapshot first.
        recovered = False
        for _n in range(tries):
            rec.retries += 1
            _do(ComputerAction.SNAPSHOT)
            blob = _do(mapped, **kw)
            rec.evidence = str(blob.get("message") or "")[:500]
            rec.observed = _observed_line(blob)
            rec.ok = bool(blob.get("ok")) and not blob.get("unverified")
            if rec.ok:
                rec.status = "done"
                recovered = True
                break
        if recovered:
            results.append(rec)
            continue
        rec.status = "blocked"
        rec.block_reason = (
            "couldnt_verify" if blob.get("unverified") else "tool_failed"
        )
        if not rec.observed:
            rec.observed = rec.evidence or "Step did not verify."
        results.append(rec)
        halted = rec.block_reason
        break

    all_done = bool(results) and all(r.status == "done" for r in results)
    status = "done" if all_done and not halted else (halted or "blocked")
    if status == "done" and len(results) < len(parsed):
        status = "blocked"
        all_done = False
    md = _markdown(g, results, status)
    return {
        "ok": bool(all_done),
        "status": status if status in {"done", "need_you", "blocked"} else "blocked",
        "goal": g,
        "steps": [r.as_dict() for r in results],
        "markdown": md,
    }


def _observed_line(blob: dict[str, Any]) -> str:
    obs = blob.get("observed")
    if isinstance(obs, dict):
        url = str(obs.get("url") or "")[:120]
        title = str(obs.get("title") or "")[:80]
        if url or title:
            return f"{title} @ {url}".strip(" @")
    msg = str(blob.get("message") or "")[:300]
    if blob.get("unverified"):
        return f"unverified: {msg}" if msg else "unverified"
    return msg


def _markdown(goal: str, results: list[DriveStepResult], status: str) -> str:
    lines = [f"**Toward {goal}** — {status}"]
    for i, r in enumerate(results, 1):
        mark = {
            "done": "done",
            "need_you": "needs you",
            "blocked": "blocked",
            "skipped": "skipped",
        }.get(r.status, r.status)
        lines.append(f"{i}. [{mark}] {r.title}")
        if r.observed:
            lines.append(f"   observed: {r.observed}")
        if r.block_reason:
            lines.append(f"   because: {r.block_reason}")
        if r.retries:
            lines.append(f"   retries: {r.retries}")
    if status == "need_you":
        lines.append(
            "This is an owner moment (password, 2FA, CAPTCHA, pay, send, or delete). "
            "Nothing irreversible was done. Say when to continue."
        )
    if status == "done":
        lines.append("Each step was observed — not just a tool returning ok.")
    return "\n".join(lines)


def format_drive_result(result: dict[str, Any]) -> str:
    return str(result.get("markdown") or json.dumps(result)[:2000])
