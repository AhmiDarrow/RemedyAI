"""Take one local, reversible step toward an open life goal.

No handholding: invent a next action if missing, do a safe move on this PC
(draft notes, research brief, calendar note), log evidence, advance.

Never auto: send, pay, publish, delete, call, submit, post.
"""

from __future__ import annotations

import os
import re
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from remedy.core.build_oracle import coerce_text_arg
from remedy.home import default_home
from remedy.memory.life_goals import LifeGoal, LifeGoalStore


_IRREVERSIBLE = re.compile(
    r"(?i)\b("
    r"send|email|mail|submit|apply|post|publish|tweet|pay|buy|purchase|"
    r"delete|cancel|quit|resign|call|text|message|wire|transfer"
    r")\b"
)
_DRAFT = re.compile(
    r"(?i)\b("
    r"draft|outline|write|note|notes|plan|chapter|resume|cv|list|"
    r"brainstorm|sketch|bullet|one.?page|journal"
    r")\b"
)
_RESEARCH = re.compile(
    r"(?i)\b("
    r"research|look ?up|find|search|learn|read about|study|compare"
    r")\b"
)
_CALENDAR = re.compile(
    r"(?i)\b("
    r"schedule|block|calendar|remind|appointment|hold time"
    r")\b"
)
_STEP_DONE = re.compile(
    r"(?is)^\s*("
    r"i did it|"
    r"i('?ve| have) (done|finished) (it|that)|"
    r"that('?s| is) done|"
    r"done with that|"
    r"finished that|"
    r"i finished (it|that)|"
    r"checked (it|that) off|"
    r"mark (it|that|the next (step|action)) done|"
    r"next (step|action) (is )?done|"
    r"did that|"
    r"got it done|"
    r"that's finished"
    r")\s*[.?!]?\s*$"
)
_GOAL_DONE = re.compile(
    r"(?is)^\s*("
    r"i finished (my |the )?(life )?goal|"
    r"(the |my )?(life )?goal is (done|complete|finished)|"
    r"mark (the |my )?(life )?goal (as )?(done|complete)|"
    r"goal (is )?(complete|completed|done)|"
    r"i completed (my |the )?(life )?goal"
    r")\s*[.?!]?\s*$"
)
_RETURN = re.compile(
    r"(?is)^\s*("
    r"i('?m| am) back|"
    r"what did you do|"
    r"what('?s| is) new|"
    r"catch me up|"
    r"what happened|"
    r"what have you (been )?doing|"
    r"show (me )?(the )?digest"
    r")\s*[.?!]?\s*$"
)
_README = """# Remedy Life

Drafts and research briefs Remedy writes toward your goals.

These files stay on this computer. Remedy never sends, pays, publishes,
or deletes for you.

- Say **I did it** when you finish the current move.
- Say **I'm back** to hear what Remedy already did.
- Say **what should I do** to take the next local step.
"""


def life_notes_enabled(home_dir=None) -> bool:
    """Note FILES are opt-in (owner request: stop writing life notes).

    Goal tracking, drive digests, and Time Crystal entries continue —
    only the .md note files under Documents/Remedy Life stop unless the
    owner turns them back on (config ``life_notes_enabled: true`` or
    ``REMEDY_LIFE_NOTES=1``).
    """
    import os as _os

    env = (_os.environ.get("REMEDY_LIFE_NOTES") or "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    try:
        from remedy.interfaces.config import load_config

        cfg = load_config() or {}
        return bool(cfg.get("life_notes_enabled", False))
    except Exception:
        return False


def classify_action(text: str) -> str:
    t = coerce_text_arg(text)
    if not t:
        return "invent"
    if _IRREVERSIBLE.search(t):
        return "irreversible"
    if _CALENDAR.search(t):
        return "calendar"
    if _RESEARCH.search(t):
        return "research"
    if _DRAFT.search(t):
        return "draft"
    return "draft"


def add_and_step(
    home_dir: str | Path | None,
    title: str,
    *,
    why: str = "",
    horizon: str = "season",
    next_action: str = "",
    done_looks_like: str = "",
    source: str = "api",
    force: bool = False,
) -> LifeGoal:
    """Create (or refresh) a life goal and take one local step.

    HTTP / slash handlers must run this off the event loop. ``force`` opens
    the note and may hit the web — only for an explicit human /goal, never
    the status-bar poll or a bulk API create.
    """
    store = LifeGoalStore(home_dir)
    life = store.add(
        title,
        why=why,
        horizon=horizon or "season",
        next_action=next_action or "",
        done_looks_like=done_looks_like or "",
        source=source,
    )
    if life and not life.next_action:
        store.set_next(life.title, invent_next(life))
    with suppress(Exception):
        take_step(home_dir, force=force)
    return store.find(life.title) or life


def invent_next(goal: LifeGoal) -> str:
    title = (goal.title or "this").strip()
    low = title.lower()
    if re.search(r"(?i)\b(novel|book|memoir|chapter|write|screenplay)\b", low):
        return f"Draft a one-page outline for {title}"
    if re.search(r"(?i)\b(job|resume|cv|interview|career|hire)\b", low):
        return f"Draft resume bullets toward {title}"
    if re.search(r"(?i)\b(learn|study|course|language|spanish|piano)\b", low):
        return f"Write a 20-minute practice plan for {title}"
    if re.search(r"(?i)\b(ship|launch|product|app|startup)\b", low):
        return f"Write this week's three ship moves for {title}"
    return f"Write the next 15-minute move for {title}"


def follow_up(goal: LifeGoal, kind: str) -> str:
    if kind == "draft":
        return f"Expand the Life note for {goal.title}"
    if kind == "research":
        return f"Turn the research brief for {goal.title} into one action"
    if kind == "calendar":
        return f"Do the blocked work for {goal.title}"
    return f"Take the next 15-minute step on {goal.title}"


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (title or "goal").lower()).strip("-")
    return (s[:48] or "goal").rstrip("-")


def _friendly_path(path: Path) -> str:
    try:
        rel = path.resolve().relative_to(Path.home())
        return str(rel).replace("\\", "/")
    except Exception:
        return path.name


def _ensure_readme(folder: Path) -> None:
    readme = folder / "README.md"
    if readme.is_file():
        return
    with suppress(OSError):
        readme.write_text(_README, encoding="utf-8")


def resolve_life_notes_dir(
    home_dir: str | Path | None = None,
    *,
    documents_root: str | Path | None = None,
) -> Path:
    """Human-visible Life folder when Documents exists; else ``{home}/life``.

    Test / custom homes stay under ``{home}/life`` unless *documents_root* is
    passed (so tests never write into the real Documents tree).
    """
    store = LifeGoalStore(home_dir)
    root: Path | None = Path(documents_root) if documents_root else None
    if root is None:
        try:
            default = default_home().resolve()
            if store.home.resolve() == default:
                cand = Path.home() / "Documents"
                if cand.is_dir():
                    root = cand
        except OSError:
            root = None
    if root is not None:
        visible = Path(root) / "Remedy Life"
        try:
            visible.mkdir(parents=True, exist_ok=True)
            _ensure_readme(visible)
            canon = store.home / "life"
            canon.mkdir(parents=True, exist_ok=True)
            pointer = canon / "WHERE.md"
            if not pointer.is_file():
                pointer.write_text(
                    f"Life notes the human can see live in:\n{visible}\n",
                    encoding="utf-8",
                )
            return visible
        except OSError:
            pass
    d = store.home / "life"
    d.mkdir(parents=True, exist_ok=True)
    _ensure_readme(d)
    return d


def life_notes_dir(home_dir: str | Path | None) -> Path:
    return resolve_life_notes_dir(home_dir)


def visible_life_dir(home_dir: str | Path | None = None) -> Path:
    """Same as :func:`resolve_life_notes_dir` — the folder to open in Explorer."""
    return resolve_life_notes_dir(home_dir)


def _write_section(path: Path, heading: str, body: str) -> None:
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    block = f"\n## {heading} — {stamp}\n\n{body.rstrip()}\n"
    if path.is_file():
        path.write_text(path.read_text(encoding="utf-8") + block, encoding="utf-8")
    else:
        title = heading.split("·")[0].strip()
        path.write_text(f"# {title}\n{block}", encoding="utf-8")


def _write_life_section(
    home_dir: str | Path | None,
    slug: str,
    heading: str,
    body: str,
    *,
    documents_root: str | Path | None = None,
) -> Path:
    primary = resolve_life_notes_dir(home_dir, documents_root=documents_root)
    path = primary / f"{slug}.md"
    _write_section(path, heading, body)
    store = LifeGoalStore(home_dir)
    canon = store.home / "life" / f"{slug}.md"
    try:
        if canon.resolve() != path.resolve():
            canon.parent.mkdir(parents=True, exist_ok=True)
            canon.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        pass
    return path


def reveal_artifact(path: str | Path) -> bool:
    """Open a Life note in the OS default app. Best-effort; never raises."""
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("REMEDY_NO_REVEAL") == "1":
        return False
    p = Path(path)
    if not p.exists():
        return False
    try:
        if sys.platform == "win32":
            os.startfile(os.path.normpath(str(p)))  # noqa: S606
            return True
        import subprocess

        cmd = ["open", str(p)] if sys.platform == "darwin" else ["xdg-open", str(p)]
        subprocess.Popen(  # noqa: S603
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def looks_like_step_done(message: str) -> bool:
    return bool(_STEP_DONE.match(coerce_text_arg(message)))


def looks_like_goal_done(message: str, goal_title: str = "") -> bool:
    msg = coerce_text_arg(message)
    if _GOAL_DONE.match(msg):
        return True
    title = coerce_text_arg(goal_title)
    if len(title) >= 8:
        try:
            if re.search(
                rf"(?is)\b(i )?(finished|completed|done with)\b.{{0,40}}{re.escape(title)}",
                msg,
            ):
                return True
        except re.error:
            pass
    return False


def looks_like_return(message: str) -> bool:
    return bool(_RETURN.match(coerce_text_arg(message)))


def _search_web(query: str, max_results: int = 3) -> list[dict[str, str]]:
    """Thin wrapper so tests can patch without importing web tools."""
    try:
        from remedy.core.agent_web_tools import search_public_web

        return list(search_public_web(query, max_results=max_results) or [])
    except Exception:
        return []


def _research_body(goal: LifeGoal, action: str, *, allow_web: bool) -> tuple[str, str]:
    """Return (markdown_body, evidence_label)."""
    hits: list[dict[str, str]] = []
    if allow_web:
        q = f"{action} {goal.title}".strip()[:200]
        hits = [h for h in _search_web(q, 3) if isinstance(h, dict)]
    if hits:
        lines = [
            f"**Goal:** {goal.title}",
            f"**Ask:** {action}",
            "",
            "Looked up (public web; I did not apply, send, or sign up):",
        ]
        for h in hits[:3]:
            title = str(h.get("title") or "source").strip()[:160]
            url = str(h.get("url") or "").strip()[:300]
            snip = str(h.get("snippet") or "").strip()[:240]
            bit = f"- **{title}**"
            if snip:
                bit += f" — {snip}"
            if url:
                bit += f"  \n  {url}"
            lines.append(bit)
        lines.extend(
            [
                "",
                "Pick one source. Next move is to turn it into a 15-minute action, not a tab pile.",
            ]
        )
        return "\n".join(lines), f"web research ({len(hits)} sources)"
    body = (
        f"**Goal:** {goal.title}\n"
        f"**Ask:** {action}\n\n"
        "Questions to answer next:\n"
        "- What does done look like in one sentence?\n"
        "- Who already did this well?\n"
        "- What is the smallest proof this week?\n"
    )
    return body, "research brief"


def notice_progress(
    home_dir: str | Path | None = None,
    message: str = "",
    *,
    documents_root: str | Path | None = None,
) -> dict[str, Any]:
    """Advance when the human says they finished the current move or the goal."""
    store = LifeGoalStore(home_dir)
    g = store.active()
    if g is None:
        return {"ok": False, "skipped": "no_open_goal"}
    msg = coerce_text_arg(message)
    if looks_like_goal_done(msg, g.title):
        ev = f"you said the goal is done: {msg[:120]}"
        store.complete(g.title, evidence=ev)
        path = _write_life_section(
            home_dir,
            _slug(g.title),
            f"{g.title} · done",
            f"You marked this complete.\n\n_{msg[:280]}_\n",
            documents_root=documents_root,
        )
        # A different thing from the ``nxt`` string below: this is the goal
        # still open after this one closed.
        still_open = store.active()
        extra = ""
        if still_open is not None:
            extra = f"\nStill holding **{still_open.title}** — next: {still_open.next_action or 'name one move'}."
        return {
            "ok": True,
            "kind": "goal_done",
            "goal": g.title,
            "path": str(path),
            "markdown": (f"**Done:** {g.title}\nLogged in `{_friendly_path(path)}`.{extra}"),
        }
    if not looks_like_step_done(msg):
        return {"ok": False, "skipped": "not_a_completion"}
    did = (g.next_action or "the last move").strip()
    ev = f"you did: {did}"
    path = _write_life_section(
        home_dir,
        _slug(g.title),
        f"{g.title} · you did it",
        f"You finished: **{did}**\n\n_{msg[:280]}_\n",
        documents_root=documents_root,
    )
    nxt = invent_next(g) if not g.next_action else follow_up(g, classify_action(did))
    if nxt.strip().lower() == did.lower():
        nxt = f"Take the next 15-minute step on {g.title}"
    store.patch(g.id, next_action=nxt, evidence=ev)
    store.record_drive(
        {
            "goal": g.title,
            "did": f"you finished: {did}",
            "next": nxt,
            "path": str(path),
            "kind": "noticed",
        }
    )
    return {
        "ok": True,
        "kind": "step_done",
        "goal": g.title,
        "did": did,
        "next": nxt,
        "path": str(path),
        "markdown": (
            f"**Got it — you did:** {did}\n"
            f"Logged in `{_friendly_path(path)}`.\n"
            f"**Next I'll take:** {nxt}"
        ),
    }


def drive_digest(
    home_dir: str | Path | None = None,
    *,
    limit: int = 6,
    mark_seen: bool = False,
) -> dict[str, Any]:
    """What Remedy already did — for 'I'm back' / partner status."""
    store = LifeGoalStore(home_dir)
    store._load()
    since = float(store.last_digest_at or 0)
    unseen = [s for s in store.last_steps if float(s.get("ts") or 0) > since + 0.01]
    steps = unseen[-limit:] if unseen else []
    active = store.active()
    nxt = (active.next_action if active else "") or ""
    title = active.title if active else ""
    if not steps:
        if active is None:
            md = (
                "Nothing new on life goals. Tell me what you want to finish "
                "this season, or `/goal <title>`."
            )
        else:
            md = (
                f"No new Life steps since we last talked.\n"
                f"**Toward {title}** — next: {nxt or 'name one concrete move'}."
            )
        return {"markdown": md, "steps": [], "unseen": 0, "goal": title, "next": nxt}
    lines = ["**While you were away**" if mark_seen else "**What I already did**"]
    for s in steps:
        did = str(s.get("did") or "").strip()
        gtitle = str(s.get("goal") or "").strip()
        p = str(s.get("path") or "").strip()
        bit = f"- {did}" if did else f"- step on {gtitle}"
        if p:
            bit += f" → `{Path(p).name}`"
        lines.append(bit)
    if title:
        lines.append(f"**Toward {title}** — next: {nxt or 'name one concrete move'}")
    if mark_seen:
        store.record_digest()
    return {
        "markdown": "\n".join(lines),
        "steps": steps,
        "unseen": len(unseen),
        "goal": title,
        "next": nxt,
    }


def take_step(
    home_dir: str | Path | None = None,
    *,
    force: bool = False,
    reveal: bool | None = None,
    allow_web: bool | None = None,
    documents_root: str | Path | None = None,
) -> dict[str, Any]:
    """Do one local step. Safe to call from idle or L0.

    *force* (or explicit *allow_web*) is for a human who asked — do the step
    and, if web tools are on, look something up. Idle ticks stay quiet and local.

    Auto-opening the note in the OS app is OFF by default (it surprised users
    with a stray Notepad window); the reply tells them where the note is saved.
    A caller can still pass ``reveal=True`` to open it explicitly.
    """
    do_reveal = False if reveal is None else bool(reveal)
    do_web = force if allow_web is None else bool(allow_web)
    store = LifeGoalStore(home_dir)
    g = store.active()
    if g is None:
        return {"ok": False, "skipped": "no_open_goal"}
    action = (g.next_action or "").strip()
    if not action:
        action = invent_next(g)
        store.set_next(g.title, action)
        g = store.find(g.title) or g
    kind = classify_action(action)
    if kind == "irreversible":
        # Record the attempt so drive_due() goes quiet — otherwise an
        # irreversible next-action burns every idle/vigil wake forever.
        with suppress(Exception):
            store.record_drive(
                {
                    "goal": g.title,
                    "did": f"waiting on you: {action}",
                    "next": action,
                    "path": "",
                    "kind": "needs_you",
                }
            )
        return {
            "ok": False,
            "skipped": "needs_you",
            "goal": g.title,
            "action": action,
            "markdown": (
                f"**Toward {g.title}** needs you for: {action}\n"
                "I will not send, pay, or publish on my own."
            ),
        }
    if kind == "research":
        body, ev_label = _research_body(g, action, allow_web=do_web)
        heading = f"{g.title} · research brief"
    elif kind == "calendar":
        body = (
            f"**Hold time for:** {g.title}\n"
            f"**When:** {g.next_by or 'pick a slot this week'}\n"
            f"**Do in that block:** {action}\n"
        )
        ev_label = "time-block note"
        heading = f"{g.title} · time block"
    else:
        body = (
            f"**Toward:** {g.title}\n"
            f"**This move:** {action}\n\n"
            "Start here:\n"
            f"1. {action.rstrip('.')}.\n"
            "2. Capture what 'done' looks like in one line.\n"
            "3. Name the following 15-minute step at the bottom.\n"
        )
        if g.why:
            body = f"**Why:** {g.why}\n\n" + body
        ev_label = "draft note"
        heading = f"{g.title} · draft"
    path = _write_life_section(
        home_dir,
        _slug(g.title),
        heading,
        body,
        documents_root=documents_root,
    ) if life_notes_enabled(home_dir) else None
    ev = f"{ev_label} → {path.name}" if path is not None else ev_label
    nxt = follow_up(g, kind)
    store.patch(g.id, next_action=nxt, evidence=ev)
    store.record_drive(
        {
            "goal": g.title,
            "did": action,
            "next": nxt,
            "path": str(path) if path is not None else "",
            "kind": kind,
        }
    )
    with suppress(Exception):
        from remedy.memory.middleman import content_key, get_session_middleman

        life_body = f"Toward {g.title}: did {action}. Next: {nxt}"
        mm = get_session_middleman("life")
        fresh = mm.item(content_key(life_body)) is None
        key = mm.put(
            life_body,
            kind="life",
            path=str(path) if path is not None else "",
            session_id="life",
            body_cap=400,
        )
        if key and fresh:
            from remedy.core.metabolism.organism import note_cas_write

            note_cas_write(home_dir, kind="life")
    with suppress(Exception):
        from remedy.core.metabolism.time_crystal import get_time_crystal

        crystal = get_time_crystal("life")
        crystal.admit(
            f"Toward {g.title}: did {action}. Next: {nxt}",
            horizon="life",
            source="life_drive",
        )
        if home_dir:
            crystal.persist(home_dir)
    opened = False
    if do_reveal and path is not None:
        opened = reveal_artifact(path)
    if path is not None:
        where = _friendly_path(path)
        opened_line = (
            f"Opened `{where}` on this PC."
            if opened
            else f"Saved `{where}` — open **Documents/Remedy Life** (or Life notes) to read it."
        )
    else:
        opened_line = "Progress logged (life note files are off)."
    return {
        "ok": True,
        "kind": kind,
        "goal": g.title,
        "did": action,
        "next": nxt,
        "path": str(path) if path is not None else "",
        "evidence": ev,
        "opened": opened,
        "markdown": (f"**Did:** {action}\n{opened_line}\n**Next I'll take:** {nxt}"),
    }


def drive_due(home_dir: str | Path | None = None, *, hours: float = 4.0) -> bool:
    import time as _time

    store = LifeGoalStore(home_dir)
    store._load()
    if store.open_count() == 0:
        return False
    if store.last_drive_at <= 0:
        return True
    return (_time.time() - store.last_drive_at) >= hours * 3600.0


def looks_like_life_work_request(message: str) -> bool:
    return bool(
        re.search(
            r"(?is)^\s*("
            r"work on my goals?|"
            r"handle my goals?|"
            r"keep going on my (life )?goals?|"
            r"make progress on my goals?|"
            r"do the next (life )?step|"
            r"take the next step"
            r")\s*[.?!]?\s*$",
            message or "",
        )
    )
