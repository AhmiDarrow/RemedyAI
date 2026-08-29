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
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from remedy.core.build_oracle import coerce_json_text, coerce_text_arg
from remedy.core.computer.types import ComputerAction

# Owner-moment verbs. "Add to cart" is deliberately omitted.
_CHECKPOINT_RE = re.compile(
    r"(?is)\b("
    r"place order|place your order|pay now|buy now|complete purchase|"
    r"submit (the )?(order|payment|application)|"
    r"send (the )?(email|message|form)|delete (the )?(account|file|forever)|"
    r"confirm (payment|delete|purchase|order)|enter (your )?password|"
    r"captcha|not a robot|"
    r"send|delete|pay|submit"
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
    evidence_hash: str = ""
    screenshot: str = ""

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
            "evidence_hash": self.evidence_hash,
            "screenshot": self.screenshot,
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


def plan_plain_language(goal: str, steps: list[dict[str, Any]]) -> str:
    """Owner-facing plan: what Remedy will do, and where it will stop."""
    lines = [f"Remedy will work toward: {goal}"]
    stops: list[str] = []
    for i, step in enumerate(steps, 1):
        title = coerce_text_arg(step.get("title")) or coerce_text_arg(
            step.get("action")
        ) or f"step {i}"
        if step_is_checkpoint(step):
            stops.append(title)
            lines.append(f"{i}. then stop for you: {title}")
        else:
            lines.append(f"{i}. {title}")
    if stops:
        lines.append(
            "Checkpoints no mode can skip: " + ", ".join(stops) + "."
        )
    else:
        lines.append("No payment/send/password stop in this plan.")
    return "\n".join(lines)


def life_plan_gate(
    goal: str,
    steps: list[dict[str, Any]],
    *,
    session_id: str | None = None,
) -> str | None:
    """One Ask for the whole plan. None when Auto/Full or already approved."""
    from remedy.core.approvals import APPROVALS

    cmd = plan_plain_language(goal, steps)
    ask = APPROVALS.needs_ask(cmd, tool_name="life_drive")
    if not ask:
        return None
    if APPROVALS.is_approved("life_drive", cmd, session_id=session_id):
        return None
    from remedy.core.speakable import speakable_plan

    titles = [
        coerce_text_arg(s.get("title")) or coerce_text_arg(s.get("action")) or "step"
        for s in steps
    ]
    stops = [
        coerce_text_arg(s.get("title")) or "checkpoint"
        for s in steps
        if step_is_checkpoint(s)
    ]
    spoken = speakable_plan(goal, titles, stops=stops)
    item = APPROVALS.create(
        tool_name="life_drive",
        command=cmd,
        reason=ask,
        session_id=session_id,
        summary=spoken,
    )
    from remedy.core.life_task_hub import build_card, publish

    publish(
        build_card(
            goal=goal,
            status="need_you",
            source_steps=steps,
            spoken=spoken,
            approval_id=item.id,
            kind="plan_gate",
            markdown=cmd,
            session_id=session_id,
        ),
        session_id=session_id,
    )
    return (
        f"APPROVAL_REQUIRED id={item.id}\n"
        f"reason={ask}\n"
        f"{cmd}\n"
        f"{spoken}\n"
        "Approve in UI then retry. "
        "Pay/send/password/CAPTCHA still stop after this yes."
    )


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
    from remedy.core.react_loop.loop_util import browse_tool_ok

    ok_true, ok_false = browse_tool_ok(text)
    low = text.lower()
    return {
        "ok": bool(ok_true) and not ok_false,
        "message": text[:800],
        "unverified": "unverified" in low,
        "approval_required": "approval_required" in low,
    }


def _step_blob_ok(blob: dict[str, Any]) -> bool:
    extra = blob.get("extra") if isinstance(blob.get("extra"), dict) else {}
    if not blob.get("ok") or blob.get("unverified") or extra.get("unverified"):
        return False
    if blob.get("pending_load") or extra.get("pending_load"):
        return False
    observed = blob.get("observed")
    if observed is None:
        observed = extra.get("observed")
    return observed is not False


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


def _stamp_evidence(rec: DriveStepResult) -> None:
    """Hash intended vs observed; attach last screenshot path if the rail saved one."""
    import hashlib

    blob = f"{rec.intended}\n{rec.observed}\n{rec.evidence}".encode()
    rec.evidence_hash = hashlib.sha256(blob).hexdigest()[:16]
    try:
        from remedy.core.computer.host_bridge import get_host_bridge

        path = str((get_host_bridge().last_shot() or {}).get("path") or "")
        if path:
            rec.screenshot = path
    except Exception:
        rec.screenshot = rec.screenshot or ""


def _intended(step: dict[str, Any]) -> str:
    title = coerce_text_arg(step.get("title")) or coerce_text_arg(step.get("action")) or "step"
    bits = [title]
    for k in ("url", "text", "click", "label", "expect_text", "expect_url"):
        v = coerce_text_arg(step.get(k))
        if v:
            bits.append(f"{k}={v[:80]}")
    return " · ".join(bits)[:400]


def _spoken_for(
    goal: str,
    results: list[DriveStepResult],
    status: str,
    *,
    total: int | None = None,
) -> str:
    from remedy.core.speakable import (
        speakable_blocked,
        speakable_checkpoint,
        speakable_done,
        speakable_progress,
    )

    last = results[-1] if results else None
    if status == "need_you":
        return speakable_checkpoint(last.title if last else "this step")
    if status == "blocked":
        return speakable_blocked(
            last.title if last else "that step",
            last.block_reason if last else "",
        )
    if status == "done":
        return speakable_done(goal)
    tot = max(int(total or 0), len(results), 1)
    step_n = min(len(results) + 1, tot) if (not last or last.status == "done") else len(results)
    title = last.title if last else "the next step"
    return speakable_progress(max(1, step_n), tot, title)


def _publish_drive(
    *,
    goal: str,
    status: str,
    results: list[DriveStepResult],
    parsed: list[dict[str, Any]],
    session_id: str | None,
    task_id: str | None = None,
    approval_id: str | None = None,
    kind: str = "",
    ok: bool = False,
    markdown: str = "",
) -> dict[str, Any]:
    from remedy.core.life_task_hub import build_card, publish

    spoken = _spoken_for(goal, results, status, total=len(parsed))
    card = build_card(
        goal=goal,
        status=status,
        steps=[r.as_dict() for r in results],
        source_steps=parsed,
        spoken=spoken,
        task_id=task_id,
        approval_id=approval_id,
        kind=kind or status,
        ok=ok,
        markdown=markdown,
        session_id=session_id,
    )
    if status == "need_you":
        from remedy.core.life_task_handoff import handoff_payload

        src = next((s for s in parsed if step_is_checkpoint(s)), None)
        paused = ""
        for r in reversed(results):
            obs = r.observed or ""
            if "://" in obs:
                part = obs.split("@")[-1].strip()
                if part.startswith("http"):
                    paused = part
                    break
        if not paused:
            try:
                from remedy.core.computer.host_bridge import get_host_bridge

                paused = get_host_bridge().last_observed_url()
            except Exception:
                paused = ""
        payload = handoff_payload(src or (results[-1].as_dict() if results else None), paused_url=paused)
        if payload:
            card["handoff"] = payload
    return publish(card, session_id=session_id)


def drive_life_task(
    *,
    goal: str,
    steps: Any = None,
    run_action: RunAction | None = None,
    runtime: Any = None,
    max_retries: int = 1,
    persist: bool = False,
    session_id: str | None = None,
    home: Any = None,
    task_id: str | None = None,
    require_plan_approval: bool | None = None,
    recipe: str = "",
    url: str = "",
    query: str = "",
    vault: Any = None,
) -> dict[str, Any]:
    """Run *steps* on this PC. Never claims done without an observed ok.

    *run_action* is ``executor.run`` (returns a JSON public_result string).
    Tests inject a fake. Checkpoints do not call *run_action*.
    """
    g = coerce_text_arg(goal) or "life task"
    parsed = parse_steps(steps)
    if not parsed and (recipe or url or vault or g):
        from remedy.core.life_task_routines import expand_recipe

        parsed = expand_recipe(
            goal=g,
            recipe=recipe,
            url=url,
            query=query,
            vault=vault,
            home=home,
        )
    results: list[DriveStepResult] = []
    if not parsed:
        return {
            "ok": False,
            "status": "blocked",
            "goal": g,
            "steps": [],
            "markdown": (
                f"**Toward {g}** — no steps to drive. Pass steps= "
                "[{title, action, …}] or recipe=open|search|shop|fill|sign_in "
                "with a url=."
            ),
        }

    gate = require_plan_approval
    if gate is None:
        gate = not bool(os.environ.get("PYTEST_CURRENT_TEST"))
    if gate:
        blocked = life_plan_gate(g, parsed, session_id=session_id)
        if blocked:
            return {
                "ok": False,
                "status": "need_you",
                "goal": g,
                "steps": [],
                "markdown": blocked,
            }

    _publish_drive(
        goal=g,
        status="running",
        results=[],
        parsed=parsed,
        session_id=session_id,
        task_id=task_id,
        kind="running",
    )

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

    def _note(rec: DriveStepResult, st: str | None = None) -> None:
        _stamp_evidence(rec)
        results.append(rec)
        live = st or ("running" if rec.status == "done" else rec.status)
        _publish_drive(
            goal=g,
            status=live,
            results=results,
            parsed=parsed,
            session_id=session_id,
            task_id=task_id,
            kind="checkpoint" if rec.status == "need_you" else live,
        )

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
            _note(rec, "need_you")
            halted = "need_you"
            break

        mapped = _ACTION_MAP.get(action_name)
        if mapped is None:
            rec.status = "blocked"
            rec.block_reason = "tool_failed"
            rec.observed = f"Unknown action {action_name!r}."
            _note(rec, "blocked")
            halted = "tool_failed"
            break

        kw = _kwargs_for_step(step)
        blob = _do(mapped, **kw)
        rec.evidence = str(blob.get("message") or "")[:500]
        rec.observed = _observed_line(blob)
        rec.ok = _step_blob_ok(blob)
        if blob.get("approval_required") or "APPROVAL_REQUIRED" in rec.evidence:
            rec.status = "need_you"
            rec.block_reason = "need_you"
            rec.ok = False
            rec.observed = rec.evidence or rec.observed
            _note(rec, "need_you")
            halted = "need_you"
            break

        if rec.ok:
            rec.status = "done"
            _note(rec, "running")
            continue

        # One re-observe + retry. Same action, fresh snapshot first.
        recovered = False
        for _n in range(tries):
            rec.retries += 1
            _do(ComputerAction.SNAPSHOT)
            blob = _do(mapped, **kw)
            rec.evidence = str(blob.get("message") or "")[:500]
            rec.observed = _observed_line(blob)
            rec.ok = _step_blob_ok(blob)
            if rec.ok:
                rec.status = "done"
                recovered = True
                break
        if recovered:
            _note(rec, "running")
            continue
        rec.status = "blocked"
        rec.block_reason = (
            "couldnt_verify" if blob.get("unverified") else "tool_failed"
        )
        if not rec.observed:
            rec.observed = rec.evidence or "Step did not verify."
        _note(rec, "blocked")
        halted = rec.block_reason
        break

    all_done = bool(results) and all(r.status == "done" for r in results)
    status = "done" if all_done and not halted else (halted or "blocked")
    if status == "done" and len(results) < len(parsed):
        status = "blocked"
        all_done = False
    md = _markdown(g, results, status)
    final_status = status if status in {"done", "need_you", "blocked"} else "blocked"
    out = {
        "ok": bool(all_done),
        "status": final_status,
        "goal": g,
        "steps": [r.as_dict() for r in results],
        "markdown": md,
        "spoken": _spoken_for(g, results, final_status, total=len(parsed)),
    }
    if persist:
        from remedy.core.life_task_store import save_life_task

        sid = session_id
        if not sid and runtime is not None:
            sid = str(getattr(runtime, "_session_id", "") or "") or None
        h = home
        if h is None and runtime is not None:
            h = getattr(getattr(runtime, "config", None), "home_dir", None)
        out = save_life_task(
            out,
            source_steps=parsed,
            session_id=sid,
            home=h,
            task_id=task_id,
        )
        out["markdown"] = (
            str(out.get("markdown") or md)
            + f"\n\nEvidence id=`{out.get('task_id')}` — resume with "
            "life_drive(task_id=…) or review the saved steps."
        )
        sid = sid or session_id
    else:
        sid = session_id
    _publish_drive(
        goal=g,
        status=str(out.get("status") or final_status),
        results=results,
        parsed=parsed,
        session_id=sid,
        task_id=str(out.get("task_id") or task_id or "") or None,
        kind=str(out.get("status") or final_status),
        ok=bool(out.get("ok")),
        markdown=str(out.get("markdown") or md),
    )
    return out


def resume_life_task(
    task_id: str,
    *,
    run_action: RunAction | None = None,
    runtime: Any = None,
    max_retries: int = 1,
    home: Any = None,
) -> dict[str, Any]:
    """Continue a saved drive from the first unfinished step."""
    from remedy.core.life_task_store import load_life_task, remaining_source_steps

    h = home
    if h is None and runtime is not None:
        h = getattr(getattr(runtime, "config", None), "home_dir", None)
    rec = load_life_task(task_id, home=h)
    if rec is None:
        return {
            "ok": False,
            "status": "blocked",
            "goal": "",
            "steps": [],
            "markdown": f"No saved life task `{task_id}`.",
        }
    remaining, halt = remaining_source_steps(rec)
    if halt == "need_you":
        md = str(rec.get("markdown") or "")
        return {
            "ok": False,
            "status": "need_you",
            "goal": rec.get("goal") or "",
            "task_id": rec.get("id"),
            "steps": list(rec.get("steps") or []),
            "markdown": (
                md
                + "\n\nStill an owner moment — password, 2FA, CAPTCHA, pay, "
                "send, or delete. Nothing was re-pressed."
            ),
        }
    if not remaining:
        return {
            "ok": bool(rec.get("ok")),
            "status": str(rec.get("status") or "done"),
            "goal": rec.get("goal") or "",
            "task_id": rec.get("id"),
            "steps": list(rec.get("steps") or []),
            "markdown": str(rec.get("markdown") or "Already finished."),
        }
    prior = [
        s
        for s in (rec.get("steps") or [])
        if isinstance(s, dict) and s.get("status") in ("done", "skipped")
    ]
    nxt = drive_life_task(
        goal=str(rec.get("goal") or ""),
        steps=remaining,
        run_action=run_action,
        runtime=runtime,
        max_retries=max_retries,
        persist=False,
        session_id=rec.get("session_id"),
        home=h,
        task_id=str(rec.get("id") or task_id),
        require_plan_approval=False,
    )
    merged = prior + list(nxt.get("steps") or [])
    nxt["steps"] = merged
    all_done = bool(merged) and all(
        s.get("status") in ("done", "skipped") for s in merged
    )
    nxt["ok"] = all_done
    if all_done:
        nxt["status"] = "done"
    from remedy.core.life_task_store import save_life_task

    nxt = save_life_task(
        nxt,
        source_steps=list(rec.get("source_steps") or remaining),
        session_id=rec.get("session_id"),
        home=h,
        task_id=str(rec.get("id") or task_id),
    )
    return nxt


def resume_after_handoff(
    task_id: str,
    *,
    run_action: RunAction | None = None,
    runtime: Any = None,
    home: Any = None,
    max_retries: int = 1,
) -> dict[str, Any]:
    """Owner handled the wall. Skip the checkpoint — never press it — continue."""
    from remedy.core.life_task_store import (
        load_life_task,
        remaining_source_steps,
        save_life_task,
    )

    h = home
    if h is None and runtime is not None:
        h = getattr(getattr(runtime, "config", None), "home_dir", None)
    rec = load_life_task(task_id, home=h)
    if rec is None:
        return {
            "ok": False,
            "status": "blocked",
            "goal": "",
            "steps": [],
            "markdown": f"No saved life task `{task_id}`.",
        }
    remaining, halt = remaining_source_steps(rec)
    if halt != "need_you" or not remaining or not step_is_checkpoint(remaining[0]):
        return resume_life_task(
            task_id, run_action=run_action, runtime=runtime, home=h
        )
    skipped_src = remaining[0]
    skip_title = coerce_text_arg(skipped_src.get("title")) or coerce_text_arg(
        skipped_src.get("action")
    ) or "checkpoint"
    skip_rec = DriveStepResult(
        title=skip_title,
        action=coerce_text_arg(skipped_src.get("action") or "checkpoint"),
        status="skipped",
        block_reason="owner_handled",
        observed=(
            "You handled this step. Remedy did not press pay, send, "
            "password, or CAPTCHA."
        ),
    )
    _stamp_evidence(skip_rec)
    prior = [
        s
        for s in (rec.get("steps") or [])
        if isinstance(s, dict) and s.get("status") in ("done", "skipped")
    ]
    rest = remaining[1:]
    if not rest:
        merged = prior + [skip_rec.as_dict()]
        out = {
            "ok": True,
            "status": "done",
            "goal": rec.get("goal") or "",
            "steps": merged,
            "markdown": str(rec.get("markdown") or ""),
            "task_id": rec.get("id"),
        }
        out = save_life_task(
            out,
            source_steps=list(rec.get("source_steps") or remaining),
            session_id=rec.get("session_id"),
            home=h,
            task_id=str(rec.get("id") or task_id),
        )
        from remedy.core.life_task_hub import build_card, publish
        from remedy.core.speakable import speakable_done

        spoken = speakable_done(str(out.get("goal") or ""))
        publish(
            build_card(
                goal=str(out.get("goal") or ""),
                status="done",
                steps=merged,
                source_steps=list(rec.get("source_steps") or []),
                spoken=spoken,
                task_id=str(out.get("task_id") or task_id),
                kind="done",
                ok=True,
                markdown=str(out.get("markdown") or ""),
                session_id=rec.get("session_id"),
            ),
            session_id=rec.get("session_id"),
        )
        out["spoken"] = spoken
        return out
    nxt = drive_life_task(
        goal=str(rec.get("goal") or ""),
        steps=rest,
        run_action=run_action,
        runtime=runtime,
        max_retries=max_retries,
        persist=False,
        session_id=rec.get("session_id"),
        home=h,
        task_id=str(rec.get("id") or task_id),
        require_plan_approval=False,
    )
    merged = prior + [skip_rec.as_dict()] + list(nxt.get("steps") or [])
    nxt["steps"] = merged
    all_done = all(s.get("status") in ("done", "skipped") for s in merged)
    nxt["ok"] = all_done
    if all_done:
        nxt["status"] = "done"
    nxt = save_life_task(
        nxt,
        source_steps=list(rec.get("source_steps") or remaining),
        session_id=rec.get("session_id"),
        home=h,
        task_id=str(rec.get("id") or task_id),
    )
    return nxt


def cancel_life_task(
    task_id: str,
    *,
    home: Any = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    from remedy.core.life_task_hub import clear
    from remedy.core.life_task_store import load_life_task, save_life_task

    rec = load_life_task(task_id, home=home) if task_id else None
    if rec is None:
        clear(session_id)
        return {"ok": True, "status": "cancelled", "steps": [], "goal": ""}
    rec["status"] = "cancelled"
    rec["ok"] = False
    out = save_life_task(
        rec,
        source_steps=list(rec.get("source_steps") or []),
        session_id=rec.get("session_id") or session_id,
        home=home,
        task_id=str(rec.get("id") or task_id),
    )
    clear(session_id or rec.get("session_id"))
    return out


def act_life_task(
    action: str,
    *,
    session_id: str | None = None,
    task_id: str | None = None,
    approval_id: str | None = None,
    home: Any = None,
    run_action: RunAction | None = None,
    runtime: Any = None,
) -> dict[str, Any]:
    """Owner Yes / No / Explain on the life-task card."""
    from remedy.core.life_task_hub import current, sse_card
    from remedy.core.speakable import explain_plan

    act = (action or "").strip().lower()
    card = current(session_id)
    tid = (task_id or (card or {}).get("task_id") or "").strip()
    appr = (approval_id or (card or {}).get("approval_id") or "").strip()
    kind = str((card or {}).get("kind") or "")

    if act in ("explain", "why"):
        md = str((card or {}).get("markdown") or "")
        goal = str((card or {}).get("goal") or "")
        if tid:
            from remedy.core.life_task_store import load_life_task

            rec = load_life_task(tid, home=home)
            if rec:
                md = str(rec.get("markdown") or md)
                goal = str(rec.get("goal") or goal)
        spoken = explain_plan(goal, md)
        return {
            "ok": True,
            "action": "explain",
            "spoken": spoken,
            "task": sse_card(card),
        }

    if act in ("no", "deny", "cancel"):
        if appr:
            from remedy.core.approvals import APPROVALS

            APPROVALS.resolve(appr, approve=False, scope="session")
        if tid:
            out = cancel_life_task(tid, home=home, session_id=session_id)
        else:
            from remedy.core.life_task_hub import clear

            clear(session_id)
            out = {"ok": True, "status": "cancelled", "steps": [], "goal": ""}
        return {
            "ok": True,
            "action": "no",
            "spoken": "Stopped. Nothing else was pressed.",
            "task": sse_card({**out, "status": "cancelled", "spoken": "Stopped."}),
        }

    if act in ("yes", "resume", "continue"):
        if appr:
            from remedy.core.approvals import APPROVALS

            APPROVALS.resolve(appr, approve=True, scope="session")
        src = list((card or {}).get("source_steps") or [])
        goal = str((card or {}).get("goal") or "")
        if src and kind == "plan_gate":
            out = drive_life_task(
                goal=goal,
                steps=src,
                run_action=run_action,
                runtime=runtime,
                persist=True,
                session_id=session_id,
                home=home,
                require_plan_approval=False,
            )
            return {
                "ok": bool(out.get("ok")),
                "action": "yes",
                "spoken": str(out.get("spoken") or ""),
                "task": sse_card(current(session_id)),
                "result": {
                    "status": out.get("status"),
                    "task_id": out.get("task_id"),
                },
            }
        # Checkpoint: owner handled the wall — skip it, never press it.
        if tid and (kind == "checkpoint" or (card or {}).get("checkpoint")):
            out = resume_after_handoff(
                tid, run_action=run_action, runtime=runtime, home=home
            )
            return {
                "ok": bool(out.get("ok")),
                "action": "yes",
                "spoken": str(out.get("spoken") or out.get("markdown") or ""),
                "task": sse_card(current(session_id)),
                "result": {
                    "status": out.get("status"),
                    "task_id": out.get("task_id"),
                },
            }
        if tid:
            out = resume_life_task(
                tid, run_action=run_action, runtime=runtime, home=home
            )
            return {
                "ok": bool(out.get("ok")),
                "action": "yes",
                "spoken": str(out.get("spoken") or out.get("markdown") or ""),
                "task": sse_card(current(session_id)),
                "result": {
                    "status": out.get("status"),
                    "task_id": out.get("task_id"),
                },
            }
        return {
            "ok": True,
            "action": "yes",
            "spoken": "Yes. Ask Remedy to continue.",
            "task": sse_card(card),
        }

    return {"ok": False, "action": act, "spoken": "Say Yes, No, or Explain.", "task": sse_card(card)}


def probe_handoff(
    *,
    session_id: str | None = None,
    task_id: str | None = None,
    page_text: str = "",
    url: str = "",
    rail_ready: bool | None = None,
    home: Any = None,
    run_action: RunAction | None = None,
    runtime: Any = None,
) -> dict[str, Any]:
    """If the owner finished a captcha/password/2FA wall, continue the drive."""
    from remedy.core.life_task_handoff import auto_resume_kind, wall_cleared
    from remedy.core.life_task_hub import current, publish, sse_card

    card = current(session_id)
    tid = (task_id or (card or {}).get("task_id") or "").strip()
    if not card or str(card.get("status") or "") != "need_you":
        return {"ok": True, "cleared": False, "task": sse_card(card)}
    hand = card.get("handoff") if isinstance(card.get("handoff"), dict) else {}
    kind = str((hand or {}).get("kind") or "")
    if not auto_resume_kind(kind):
        return {
            "ok": True,
            "cleared": False,
            "reason": "needs_yes",
            "task": sse_card(card),
        }
    ready = rail_ready
    live_url = coerce_text_arg(url)
    if ready is None or not live_url:
        try:
            from remedy.core.computer.host_bridge import get_host_bridge

            br = get_host_bridge()
            if ready is None:
                ready = bool(br.host_connected())
            if not live_url:
                live_url = br.last_observed_url()
        except Exception:
            if ready is None:
                ready = False
    paused = str((hand or {}).get("paused_url") or "")
    if wall_cleared(
        kind,
        page_text=page_text,
        url=live_url,
        paused_url=paused,
        rail_ready=bool(ready),
    ):
        if tid:
            out = resume_after_handoff(
                tid, run_action=run_action, runtime=runtime, home=home
            )
            return {
                "ok": True,
                "cleared": True,
                "resumed": True,
                "spoken": str(out.get("spoken") or out.get("markdown") or ""),
                "task": sse_card(current(session_id)),
            }
        return {"ok": True, "cleared": True, "resumed": False, "task": sse_card(card)}
    spoken = (
        "Open the Browser rail, then finish the sign-in or CAPTCHA. "
        "Remedy will continue after."
        if not ready
        else "The Browser rail is ready. Finish the sign-in or CAPTCHA, "
        "then Remedy will continue."
    )
    card = dict(card)
    card["spoken"] = spoken
    publish(card, session_id=session_id)
    return {
        "ok": True,
        "cleared": False,
        "spoken": spoken,
        "task": sse_card(card),
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
        from remedy.core.speakable import speakable_checkpoint

        stop_title = results[-1].title if results else "this step"
        lines.append(speakable_checkpoint(stop_title))
    if status == "blocked":
        from remedy.core.speakable import speakable_blocked

        blocked = results[-1] if results else None
        lines.append(
            speakable_blocked(
                blocked.title if blocked else "that step",
                blocked.block_reason if blocked else "",
            )
        )
    if status == "done":
        from remedy.core.speakable import speakable_done

        lines.append(speakable_done(goal))
    return "\n".join(lines)


def format_drive_result(result: dict[str, Any]) -> str:
    return str(result.get("markdown") or json.dumps(result)[:2000])
