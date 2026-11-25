"""Record prompt.* worker fixtures for the native Go port (native/go/mind/prompt).

The Python worker is the specification. Each case builds a fresh Remedy home
in a temp folder, runs the five ``prompt.*`` handlers in-process exactly as
``rmdy_tool_worker`` dispatches them, and writes one JSON file per case under
``native/go/mind/prompt/testdata/``:

- the exact input mapping and output mapping of every step,
- for ``prompt.assemble``, the system block split into named sections
  (each section is ``sep + text``; their concatenation is the system),
- the text files under the home and project before the first step, and the
  ones each step created or changed,
- what the readers the Go port takes as parameters returned in Python
  (config, ranked memory facts, skill rows) where a case uses them.

Determinism: ``time.time``, ``datetime.now``/``utcnow``/``today``,
``uuid.uuid4`` and ``random`` are frozen before ``remedy`` is imported; the
frozen clock is written into every fixture. Absolute temp paths become
``{HOME}`` (REMEDY_HOME), ``{PROJECT}`` (the case project), ``{USER}`` (the
fake user profile folder) and ``{REPO}`` (this checkout).

Safety: every case runs in its own child process with REMEDY_HOME, HOME and
USERPROFILE pointed at fresh temp folders, REMEDY_DESKTOP_SIDECAR=1, and every
``*_API_KEY`` / ``*TOKEN*`` variable removed. Nothing reaches a model or the
network; the real ``~/.remedy`` is never read.

Usage::

    python scripts/record_prompt_fixtures.py            # all cases
    python scripts/record_prompt_fixtures.py empty_hello # one case
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "native" / "go" / "mind" / "prompt" / "testdata"

# 2026-11-25T12:00:00Z
CLOCK = 1795608000.0


# --------------------------------------------------------------------------
# Child side: freeze, set up a home, run steps.
# --------------------------------------------------------------------------


def _freeze() -> None:
    import datetime as _dt
    import random
    import time
    import uuid

    real_dt = _dt.datetime
    utc = _dt.timezone.utc

    class FrozenDateTime(real_dt):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            base = real_dt.fromtimestamp(CLOCK, tz=utc)
            if tz is None:
                # Naive "now" is UTC so the fixture does not carry this
                # machine's time zone.
                return cls(*base.timetuple()[:6])
            got = base.astimezone(tz)
            return cls(*got.timetuple()[:6], got.microsecond, tzinfo=got.tzinfo)

        @classmethod
        def utcnow(cls):  # type: ignore[override]
            base = real_dt.fromtimestamp(CLOCK, tz=utc)
            return cls(*base.timetuple()[:6])

        @classmethod
        def today(cls):  # type: ignore[override]
            return cls.utcnow()

    class FrozenDate(_dt.date):
        @classmethod
        def today(cls):  # type: ignore[override]
            d = real_dt.fromtimestamp(CLOCK, tz=utc).date()
            return cls(d.year, d.month, d.day)

    _dt.datetime = FrozenDateTime  # type: ignore[misc]
    _dt.date = FrozenDate  # type: ignore[misc]
    time.time = lambda: CLOCK  # type: ignore[assignment]
    time.time_ns = lambda: int(CLOCK * 1_000_000_000)  # type: ignore[assignment]
    time.perf_counter = lambda: 1000.0  # type: ignore[assignment]
    counter = [0]

    def _uuid4() -> uuid.UUID:
        counter[0] += 1
        return uuid.UUID(int=(0x4000 << 64) | counter[0], version=4)

    uuid.uuid4 = _uuid4  # type: ignore[assignment]
    random.seed(20261125)


class Ctx:
    def __init__(self, home: Path, user: Path, project_root: Path) -> None:
        self.home = home
        self.user = user
        self.project_root = project_root
        self.project: Path | None = None
        self.readers: dict[str, Any] = {}
        self.notes: list[str] = []

    def make_project(self, files: dict[str, str]) -> str:
        self.project = self.project_root
        for rel, body in files.items():
            p = self.project_root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        return str(self.project_root)


def _replacements(ctx: Ctx) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for raw, tag in (
        (ctx.project_root, "{PROJECT}"),
        (ctx.home, "{HOME}"),
        (ctx.user, "{USER}"),
        (REPO, "{REPO}"),
    ):
        forms: set[str] = set()
        for p in {str(raw), os.path.realpath(str(raw)), str(Path(raw).resolve())}:
            forms.add(p)
            forms.add(p.replace("\\", "/"))
            forms.add(p.lower())
            forms.add(p.replace("\\", "/").lower())
        for f in forms:
            pairs.append((f, tag))
            if "\\" in f:
                # The same path written into a TOML / JSON string body.
                pairs.append((f.replace("\\", "\\\\"), tag[:-1] + ":esc}"))
    # Longest first so a project inside the user folder keeps its own tag.
    pairs.sort(key=lambda kv: len(kv[0]), reverse=True)
    return pairs


def _norm(obj: Any, pairs: list[tuple[str, str]]) -> Any:
    if isinstance(obj, str):
        s = obj
        for raw, tag in pairs:
            if raw and raw in s:
                s = s.replace(raw, tag)
        return s
    if isinstance(obj, dict):
        return {str(_norm(k, pairs)): _norm(v, pairs) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_norm(v, pairs) for v in obj]
    return obj


TEXT_SUFFIXES = {".json", ".jsonl", ".toml", ".md", ".txt", ".rs", ".js", ".py", ".ts"}


def _snapshot(*roots: tuple[Path, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for root, tag in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in TEXT_SUFFIXES:
                continue
            rel = p.relative_to(root).as_posix()
            if tag == "{HOME}" and rel.startswith("skills/"):
                # Bundled skill packs seeded by discovery; the Go port reads
                # skills through a reader, so the copies are not inputs.
                continue
            try:
                out[f"{tag}/{rel}"] = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
    return out


def _diff(before: dict[str, str], after: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in after.items():
        if before.get(k) != v:
            out[k] = v
    for k in before:
        if k not in after:
            out[k] = None
    return out


# --- section capture for prompt.assemble -----------------------------------


class Capture:
    """Records what the section producers returned during one assemble."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.system_prompt: str | None = None
        self.parts: list[str] | None = None
        self.addenda_before: str | None = None
        self.addenda_after: str | None = None
        self.injects: list[str] | None = None
        self.history: str | None = None
        self.named: dict[str, list[str]] = {}
        self.calls: dict[str, list[Any]] = {}

    def note(self, name: str, text: Any) -> None:
        if isinstance(text, str) and text:
            self.named.setdefault(name, []).append(text)


CAP = Capture()


def _install_capture() -> None:
    import remedy.core.agent_context as ac
    import remedy.core.agent_react_preamble as arp
    import remedy.runtime.prompt_assemble as pa

    real_rebuild = pa._rebuild_system_prompt

    def rebuild(runtime: Any) -> str:
        out = real_rebuild(runtime)
        CAP.system_prompt = out
        return out

    pa._rebuild_system_prompt = rebuild

    real_hist = pa.render_history_block

    def hist(history: Any, **kw: Any) -> str:
        out = real_hist(history, **kw)
        CAP.history = out
        return out

    pa.render_history_block = hist

    real_add = arp.append_plan_and_computer_addenda

    def add(context: str, **kw: Any) -> str:
        CAP.addenda_before = context
        out = real_add(context, **kw)
        CAP.addenda_after = out
        return out

    arp.append_plan_and_computer_addenda = add

    real_prime = arp.prime_turn_metabolism

    def prime(*a: Any, **kw: Any) -> list[str]:
        out = real_prime(*a, **kw)
        CAP.injects = list(out)
        return out

    arp.prime_turn_metabolism = prime

    # Named producers: their returns name the context parts.
    def wrap(mod: Any, attr: str, name: str, *, join_lines: bool = False) -> None:
        real = getattr(mod, attr)

        def w(*a: Any, **kw: Any) -> Any:
            out = real(*a, **kw)
            if join_lines and isinstance(out, list):
                CAP.note(name, "\n".join(out))
            elif isinstance(out, list):
                for x in out:
                    CAP.note(name, x)
            else:
                CAP.note(name, out)
            return out

        setattr(mod, attr, w)

    import remedy.core.continuity_steering as cs
    import remedy.core.muscle_profile as mp
    import remedy.core.project_fingerprint as pf
    import remedy.core.project_learning as pl
    import remedy.core.work_roots as wr
    import remedy.core.workspace as ws
    import remedy.memory.harness.brief as hb
    import remedy.memory.partner_memory as pm
    import remedy.memory.partner_state as ps
    import remedy.memory.soul.inject as si
    from remedy.core.metabolism import alive as al
    from remedy.core import session_continuity as sc

    wrap(sc, "session_isolation_system_line", "isolation")
    wrap(si, "build_soul_context_block", "soul")
    wrap(al, "build_alive_inject", "alive")
    wrap(mp, "builder_system_addendum", "builder")
    wrap(ws, "workspace_context_block", "workspace")
    wrap(wr, "work_roots_context_block", "work_roots")
    wrap(pf, "orientation_block", "orientation")
    wrap(pm, "build_partner_memory_block", "partner_memory")
    wrap(pl, "project_chapter_block", "project_chapter")
    wrap(ps, "partner_context_blocks", "partner_state")
    wrap(hb, "brief_to_context_block", "session_brief")
    wrap(cs, "continuity_steering_block", "continuity")
    wrap(ac, "_middleman_context_block", "middleman")

    real_ctx_lines = pf.StackFingerprint.context_lines

    def ctx_lines(self: Any) -> list[str]:
        out = real_ctx_lines(self)
        CAP.note("fingerprint", "\n".join(out))
        return out

    pf.StackFingerprint.context_lines = ctx_lines

    real_rank = pm.rank_injectable_facts

    def rank(profile: Any, **kw: Any) -> Any:
        out = real_rank(profile, **kw)
        CAP.calls.setdefault("rank_injectable_facts", []).append(
            {
                "query": kw.get("query", ""),
                "project_path": kw.get("project_path"),
                "limit": kw.get("limit"),
                "facts": [f.model_dump(mode="json") for f in out],
            }
        )
        return out

    pm.rank_injectable_facts = rank


# Constant context parts (exact text lives in agent_context.py).
CONST_PREFIXES = [
    ("Access scope: full (or no focus folder).", "access_reminder"),
    ("Approvals: ", "approvals"),
    ("Self-configuration: ", "self_config"),
    ("Durable memory: ", "durable_memory"),
    ("Owner's manual / F1 Help", "manual"),
    ("Skills catalog (", "skills"),
    ("Skills loaded: ", "skills"),
    ("Recalled for this turn:", "recall"),
    ("Recent memory (optional):", "recent_memory"),
    ("[Messenger surface", "messenger"),
    ("RMB local agent", "rmb"),
    ("[Build engine live]", "build_engine"),
    ("[Auto-resume]", "auto_resume"),
]


def _name_part(text: str) -> str:
    for name, outs in CAP.named.items():
        if text in outs:
            return name
    for prefix, name in CONST_PREFIXES:
        if text.startswith(prefix):
            return name
    return "unknown"


def _split_addenda(tail: str) -> list[dict[str, str]]:
    """Name the blocks append_plan_and_computer_addenda added, in order."""
    from remedy.core.computer.guidance import COMPUTER_USE_SYSTEM_ADDENDUM
    from remedy.core.plan_store import (
        BUILD_MODE_SYSTEM_ADDENDUM,
        FRONTIER_BUILD_MODE_ADDENDUM,
        PLAN_MODE_SYSTEM_ADDENDUM,
    )

    known = [
        ("plan_mode_addendum", PLAN_MODE_SYSTEM_ADDENDUM),
        ("frontier_build_addendum", FRONTIER_BUILD_MODE_ADDENDUM),
        ("build_mode_addendum", BUILD_MODE_SYSTEM_ADDENDUM),
        ("computer_use_addendum", COMPUTER_USE_SYSTEM_ADDENDUM),
    ]
    out: list[dict[str, str]] = []
    rest = tail
    while rest:
        if not rest.startswith("\n\n"):
            raise RuntimeError(f"addenda do not start with a blank line: {rest[:80]!r}")
        body = rest[2:]
        for name, text in known:
            if body.startswith(text) and (len(body) == len(text) or body[len(text):].startswith("\n\n")):
                out.append({"name": name, "sep": "\n\n", "text": text})
                rest = body[len(text):]
                break
        else:
            raise RuntimeError(f"unnamed addendum: {body[:120]!r}")
    return out


def _assemble_sections(system: str) -> list[dict[str, str]]:
    from remedy.core.react_stream import TURN_CONTEXT_MARKER

    sp = CAP.system_prompt or ""
    secs: list[dict[str, str]] = []
    if not system.startswith(sp + "\n\n") or TURN_CONTEXT_MARKER not in system:
        # Local / RMB binding: slim_system_for_local rewrote the whole block.
        hist = CAP.history or ""
        body = system
        if hist and system.endswith("\n\n" + hist):
            body = system[: -(len(hist) + 2)]
        secs.append({"name": "local_slim_system", "sep": "", "text": body})
        if hist:
            secs.append({"name": "history", "sep": "\n\n", "text": hist})
        return secs
    secs.append({"name": "system_prompt", "sep": "", "text": sp})
    rest = system[len(sp) + 2 :]
    idx = rest.index(TURN_CONTEXT_MARKER)
    secs.append({"name": "runtime_info", "sep": "\n\n", "text": rest[:idx]})
    secs.append({"name": "turn_marker", "sep": "", "text": TURN_CONTEXT_MARKER})
    rest = rest[idx + len(TURN_CONTEXT_MARKER) :]
    parts = list(CAP.parts or [])
    first = True
    for part in parts:
        sep = "" if first else "\n\n"
        first = False
        secs.append({"name": _name_part(part), "sep": sep, "text": part})
    joined = "\n\n".join(parts)
    if not rest.startswith(joined):
        raise RuntimeError("context parts do not prefix the turn context")
    rest = rest[len(joined) :]
    before = CAP.addenda_before or ""
    after = CAP.addenda_after or ""
    if before != joined or not after.startswith(before):
        raise RuntimeError("addenda capture does not line up with the context")
    for sec in _split_addenda(after[len(before) :]):
        secs.append(sec)
    rest = rest[len(after) - len(before) :]
    injects = list(CAP.injects or [])
    if injects:
        chunk = "\n\n".join(injects)
        sep = "\n\n" if after else ""
        for i, inj in enumerate(injects):
            secs.append({"name": _inject_name(inj), "sep": sep if i == 0 else "\n\n", "text": inj})
        if not rest.startswith(sep + chunk):
            raise RuntimeError("metabolism injects do not follow the addenda")
        rest = rest[len(sep + chunk) :]
    hist = CAP.history or ""
    if hist:
        # prompt_assemble rstrips the block before appending the history; a
        # section that ended in whitespace would have failed the checks above.
        if rest != "\n\n" + hist:
            raise RuntimeError(f"history does not close the system block: {rest[:120]!r}")
        secs.append({"name": "history", "sep": "\n\n", "text": hist})
        rest = ""
    if rest:
        raise RuntimeError(f"unsectioned tail: {rest[:200]!r}")
    rebuilt = "".join(s["sep"] + s["text"] for s in secs)
    if rebuilt != system:
        at = next((i for i, (a, b) in enumerate(zip(rebuilt, system)) if a != b), min(len(rebuilt), len(system)))
        raise RuntimeError(
            f"sections do not rebuild the system block at {at}: "
            f"{rebuilt[max(0, at - 60):at + 60]!r} vs {system[max(0, at - 60):at + 60]!r}"
        )
    return secs


def _inject_name(text: str) -> str:
    if text.startswith("[House]"):
        return "metabolism_house"
    if text.startswith("[Organism"):
        return "metabolism_organism"
    return "metabolism_" + "".join(c if c.isalnum() else "_" for c in text[:24].lower()).strip("_")


def _trace_parts() -> Any:
    """Profile hook: keep ``parts`` from build_turn_context's last frame exit."""
    import remedy.core.agent_context as ac

    code = ac.build_turn_context.__code__

    def prof(frame: Any, event: str, arg: Any) -> None:
        if event == "return" and frame.f_code is code:
            parts = frame.f_locals.get("parts")
            if isinstance(parts, list):
                CAP.parts = list(parts)

    return prof


def _run_step(ctx: Ctx, op: str, inp: dict[str, Any]) -> dict[str, Any]:
    from remedy.runtime import rmdy_tool_worker as w

    handlers = {
        "prompt.assemble": w._prompt_assemble,
        "prompt.slim_epoch": w._prompt_slim_epoch,
        "prompt.should_continue": w._prompt_should_continue,
        "prompt.after_tool_batch": w._prompt_after_tool_batch,
        "prompt.turn_end": w._prompt_turn_end,
    }
    CAP.reset()
    roots = [(ctx.home, "{HOME}"), (ctx.project_root, "{PROJECT}")]
    before = _snapshot(*roots)
    if op == "prompt.assemble":
        sys.setprofile(_trace_parts())
        try:
            out = dict(handlers[op](inp))
        finally:
            sys.setprofile(None)
    else:
        out = dict(handlers[op](inp))
    step: dict[str, Any] = {"op": op, "input": inp, "output": out}
    if op == "prompt.assemble":
        step["sections"] = _assemble_sections(str(out.get("system") or ""))
        if CAP.calls:
            step["calls"] = CAP.calls
    if op in ("prompt.turn_end", "prompt.after_tool_batch"):
        # The background-thread work (post-turn prep) must land before the diff.
        import threading

        for t in threading.enumerate():
            if t is not threading.current_thread() and not t.daemon:
                t.join(timeout=10)
    step["files_after"] = _diff(before, _snapshot(*roots))
    return step


# --- case setup helpers ------------------------------------------------------


def _run(coro: Any) -> Any:
    from remedy.runtime.prompt_assemble import _run_coro

    return _run_coro(coro)


def _house_rules(ctx: Ctx, answers: dict[str, Any]) -> None:
    from remedy.core.house_rules import confirm_onboarding, record_answers

    r = record_answers(answers, home=str(ctx.home))
    if not r.get("ok"):
        raise RuntimeError(f"house rules record failed: {r}")
    r = confirm_onboarding(home=str(ctx.home))
    if not r.get("ok"):
        raise RuntimeError(f"house rules confirm failed: {r}")


def _remember(ctx: Ctx, content: str, user_text: str, *, title: str, category: str) -> None:
    from remedy.runtime.prompt_assemble import save_memory

    out = save_memory(
        {
            "content": content,
            "title": title,
            "category": category,
            "user_text": user_text,
            "session_id": SID,
            "home_dir": str(ctx.home),
        }
    )
    if not out.get("saved"):
        raise RuntimeError(f"memory save failed: {out}")


def _soul(ctx: Ctx, fn: Any) -> None:
    from remedy.memory.soul.field import load_soul_field, save_soul_field

    sf = load_soul_field(str(ctx.home))
    fn(sf)
    save_soul_field(sf, str(ctx.home))


SID = "sess-fixture-1"

NODE_REPO = {
    "package.json": json.dumps(
        {
            "name": "parser-demo",
            "version": "0.1.0",
            "scripts": {"test": "node --test", "build": "tsc -p ."},
            "devDependencies": {"typescript": "^5.4.0"},
        },
        indent=2,
    )
    + "\n",
    "src/parser.js": "export function parse(s) {\n  return s.split(',');\n}\n",
    "README.md": "# parser-demo\n\nA tiny CSV splitter.\n",
}

RUST_REPO = {
    "Cargo.toml": '[package]\nname = "tally"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n',
    "src/main.rs": 'fn main() {\n    println!("{}", 2 + 2);\n}\n',
}


def _assemble(message: str, **kw: Any) -> dict[str, Any]:
    inp: dict[str, Any] = {"message": message, "session_id": SID, "plan_mode": False, "chat_mode": False}
    inp.update(kw)
    return inp


def case_empty_hello(ctx: Ctx) -> list[tuple[str, dict[str, Any]]]:
    return [("prompt.assemble", _assemble("hello"))]


def case_named_house_rules(ctx: Ctx) -> list[tuple[str, dict[str, Any]]]:
    _house_rules(ctx, {"user_name": "Sam", "name": "Nova", "llm_provider": "openai", "llm_model": "gpt-5"})
    return [
        ("prompt.assemble", _assemble("hi Nova, what should we work on today?", provider="openai", model="gpt-5")),
    ]


def case_project_node(ctx: Ctx) -> list[tuple[str, dict[str, Any]]]:
    proj = ctx.make_project(NODE_REPO)
    return [
        (
            "prompt.assemble",
            _assemble("add a unit test for the parser", provider="openai", model="gpt-5", project_path=proj),
        )
    ]


def case_project_rust(ctx: Ctx) -> list[tuple[str, dict[str, Any]]]:
    proj = ctx.make_project(RUST_REPO)
    return [
        (
            "prompt.assemble",
            _assemble("why does cargo build print 4?", provider="anthropic", model="claude-sonnet-4-5", project_path=proj),
        )
    ]


def case_history(ctx: Ctx) -> list[tuple[str, dict[str, Any]]]:
    history = [
        {"role": "user", "content": "what are the three primary colours?"},
        {"role": "assistant", "content": "Red, yellow and blue in paint; red, green and blue in light."},
        {"role": "system", "content": "ignored row"},
        {"role": "user", "content": "which set does a monitor use?"},
        {"role": "assistant", "content": "A monitor mixes light, so red, green and blue."},
        {"role": "user", "content": ""},
    ]
    return [("prompt.assemble", _assemble("and what about a printer?", provider="openai", model="gpt-5", history=history))]


def case_plan_mode(ctx: Ctx) -> list[tuple[str, dict[str, Any]]]:
    proj = ctx.make_project(NODE_REPO)
    return [
        (
            "prompt.assemble",
            _assemble("plan a refactor of the parser module", provider="openai", model="gpt-5", project_path=proj, plan_mode=True),
        )
    ]


def case_chat_mode(ctx: Ctx) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("prompt.assemble", _assemble("tell me something interesting about octopuses", provider="openai", model="gpt-5", chat_mode=True)),
        (
            "prompt.should_continue",
            {"goal": "tell me something interesting", "text": "Octopuses have three hearts.", "session_id": SID,
             "tool_count": 0, "last_results": [], "verify_seen": False, "chat_mode": True, "plan_mode": False,
             "home_dir": str(ctx.home), "project_path": ""},
        ),
    ]


def case_memory_facts(ctx: Ctx) -> list[tuple[str, dict[str, Any]]]:
    _remember(ctx, "My dog is called Biscuit", "remember that my dog is called Biscuit", title="Dog", category="life")
    _remember(ctx, "I prefer tabs over spaces", "please remember I prefer tabs over spaces", title="Indent", category="preference")
    _remember(ctx, "The owner is training for a 10k run in March", "ok", title="Running", category="goal")
    return [("prompt.assemble", _assemble("what do you remember about my dog?", provider="openai", model="gpt-5"))]


def case_soul_vigil(ctx: Ctx) -> list[tuple[str, dict[str, Any]]]:
    def shape(sf: Any) -> None:
        from remedy.memory.soul.field import EpisodeResidue

        rel = sf.relational
        rel.turns_together = 20
        rel.rapport = 0.71
        rel.trust = 0.66
        rel.speech_register = "casual-short"
        rel.voice_markers = ["no worries", "sounds good"]
        rel.help_mode = "pair"
        rel.correction_style = "gentle"
        rel.open_threads = ["check in about the garden plan"]
        rel.last_user_ts = CLOCK - 3 * 86400
        sf.pledges = ["help them finish the garden plan before spring"]
        sf.self_habits = ["I keep answers short when they are busy"]
        sf.episodes = [
            EpisodeResidue(id="ep1", ts=CLOCK - 86400, arc="planned the vegetable beds", user_stance="playful",
                           open_thread="pick tomato varieties", valence=0.4, session_id=SID, strength=0.6),
        ]

    _soul(ctx, shape)
    hearth = ctx.home / "soul" / "hearth.json"
    hearth.write_text(
        json.dumps(
            {
                "schema": 1, "locus": "desk", "surface": "desktop", "last_touch_ts": CLOCK - 60,
                "away_since_ts": CLOCK - 3 * 86400, "greeting": "While you were away I tidied my notes on the garden plan.",
                "greeting_through_ts": CLOCK - 3600, "acked_through_ts": 0, "watches_screen": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return [
        # A greeting is a lean turn: no contract, no threads.
        ("prompt.assemble", _assemble("good morning", provider="openai", model="gpt-5")),
        # A work turn takes the one-time vigil offer and shows the residue.
        ("prompt.assemble", _assemble("let's pick the tomato varieties for the garden plan", provider="openai", model="gpt-5")),
        # The offer was taken above; it must not come back.
        ("prompt.assemble", _assemble("which ones grow best in pots?", provider="openai", model="gpt-5")),
    ]


def case_skills(ctx: Ctx) -> list[tuple[str, dict[str, Any]]]:
    proj = ctx.make_project(NODE_REPO)
    return [
        ("prompt.assemble", _assemble("review this project for security issues before I publish it", provider="openai", model="gpt-5", project_path=proj)),
    ]


def case_skills_registry(ctx: Ctx) -> list[tuple[str, dict[str, Any]]]:
    """Spec for the catalog branch: a runtime with the bundled skills discovered.

    The production worker builds its runtime without a skill registry, so
    ``prompt.assemble`` there always says "Skills loaded: (none yet …)". This
    case attaches the registry the way BasicRuntime(register_tools=True)
    would, to record the catalog text the branch produces.
    """
    proj = ctx.make_project(NODE_REPO)
    from remedy.runtime import prompt_assemble as pa
    from remedy.skills.registry import SkillRegistry

    rt = _run(pa.get_cached_runtime(project_path=proj, provider="openai", model="gpt-5"))
    reg = SkillRegistry()
    reg.discover_defaults(home_dir=str(ctx.home))
    rt.skills = reg
    msg = "review this project for security issues before I publish it"
    ranked = reg.match_skills(msg[:240], limit=24, workspace_hint=str(Path(proj).resolve()))
    ctx.readers["skills_ranked"] = [
        {
            "name": s.manifest.name,
            "status": s.manifest.status.value if hasattr(s.manifest.status, "value") else str(s.manifest.status),
            "description": s.manifest.description or "",
            "score": float(sc),
        }
        for s, sc in ranked
    ]
    ctx.readers["skills_count"] = int(reg.count)
    ctx.notes.append("registry attached by the recorder; the production worker has none")
    return [("prompt.assemble", _assemble(msg, provider="openai", model="gpt-5", project_path=proj))]


def case_local_binding(ctx: Ctx) -> list[tuple[str, dict[str, Any]]]:
    return [("prompt.assemble", _assemble("hello", provider="ollama", model="qwen2.5-coder:7b"))]


LONG_TEXT = "\n".join(
    f"Step {i}: read src/module_{i}.py and noted that function f{i} needs a guard for empty input." for i in range(1, 60)
)


def case_slim_epoch(ctx: Ctx) -> list[tuple[str, dict[str, Any]]]:
    proj = ctx.make_project(NODE_REPO)
    ledger = [
        "- read [ok] src/parser.js",
        "- edit [ok] src/parser.js +3 -1",
        "- bash [fail] exit_code=1 npm test: 1 failing",
        "- bash [ok] exit_code=0 npm test: 4 passing",
    ]
    base_system = "You are Remedy.\n\n[Turn context]\nsession_id=" + SID
    first = {
        "system": base_system, "goal": "fix the failing parser test", "text": LONG_TEXT, "ledger": ledger,
        "session_id": SID, "epoch": 1, "total_steps": 12, "home_dir": str(ctx.home), "project_path": proj,
        "provider": "openai", "model": "gpt-5",
    }
    second = dict(first)
    second.update(
        {
            "system": base_system + "\n\n[Session Brief · epoch working memory]\nold tail that must be replaced",
            "epoch": 2, "total_steps": 20, "ledger": [], "checkpoint": "- bash [ok] exit_code=0 npm test",
            "text": "All four tests pass now.",
        }
    )
    return [("prompt.slim_epoch", first), ("prompt.slim_epoch", second)]


def _sc(ctx: Ctx, **kw: Any) -> dict[str, Any]:
    inp: dict[str, Any] = {
        "goal": "", "text": "", "session_id": SID, "tool_count": 0, "last_results": [], "verify_seen": False,
        "chat_mode": False, "plan_mode": False, "home_dir": str(ctx.home), "project_path": "",
    }
    inp.update(kw)
    return inp


def case_should_continue(ctx: Ctx) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("prompt.should_continue", _sc(ctx, goal="what is the capital of France?", text="Paris.")),
        (
            "prompt.should_continue",
            _sc(ctx, goal="fix the failing test in parser.py", text="Done! All tests pass now.", tool_count=2,
                last_results=[
                    {"name": "edit", "ok": True, "tail": "edited parser.py"},
                    {"name": "bash", "ok": False, "tail": "exit_code=1 FAILED tests/test_parser.py::test_split", "exit_code": 1},
                ]),
        ),
        (
            "prompt.should_continue",
            _sc(ctx, goal="create a file hello.py that prints hi", text="Sure, I'll create hello.py for you now."),
        ),
        (
            "prompt.should_continue",
            _sc(ctx, goal="fix the bug in the build script", text="Fixed it.", tool_count=1, verify_seen=False,
                last_results=[{"name": "edit", "ok": True, "tail": "edited build.py"}]),
        ),
        (
            "prompt.should_continue",
            _sc(ctx, goal="fix the bug in the build script", text="Fixed it, tests are green.", tool_count=2, verify_seen=True,
                last_results=[{"name": "bash", "ok": True, "tail": "exit_code=0 4 passed", "exit_code": 0}]),
        ),
    ]


def case_tool_batch_turn_end(ctx: Ctx) -> list[tuple[str, dict[str, Any]]]:
    proj = ctx.make_project(NODE_REPO)
    results = [
        {"tool": "read", "name": "read", "ok": True, "success": True, "result": "export function parse(s) {...}",
         "tool_call_id": "call_1", "arguments": {"path": "src/parser.js"}},
        {"tool": "bash", "name": "bash", "ok": False, "success": False, "result": "exit_code=1\nnpm ERR! test failed",
         "tool_call_id": "call_2", "arguments": {"command": "npm test"}, "error": "exit_code=1"},
    ]
    steps = [
        {"tool": "read", "name": "read", "ok": True, "success": True, "result": "export function parse(s) {...}"},
        {"tool": "bash", "name": "bash", "ok": False, "success": False, "result": "exit_code=1\nnpm ERR! test failed",
         "error": "exit_code=1"},
    ]
    return [
        ("prompt.after_tool_batch", {"session_id": SID, "results": results, "home_dir": str(ctx.home), "_go_bound": True, "project_path": proj}),
        (
            "prompt.turn_end",
            {"message": "run the tests", "goal": "run the tests", "user_text": "run the tests",
             "assistant_text": "One test fails: the parser drops empty fields.", "session_id": SID, "plan_mode": False,
             "chat_mode": False, "tool_steps": steps, "soul_native": True, "project_path": proj},
        ),
    ]


CASES: dict[str, Any] = {
    "empty_hello": case_empty_hello,
    "named_house_rules": case_named_house_rules,
    "project_node": case_project_node,
    "project_rust": case_project_rust,
    "history": case_history,
    "plan_mode": case_plan_mode,
    "chat_mode": case_chat_mode,
    "memory_facts": case_memory_facts,
    "soul_vigil": case_soul_vigil,
    "skills": case_skills,
    "skills_registry": case_skills_registry,
    "local_binding": case_local_binding,
    "slim_epoch": case_slim_epoch,
    "should_continue": case_should_continue,
    "tool_batch_turn_end": case_tool_batch_turn_end,
}


def child(name: str, home: Path, user: Path, project_root: Path) -> None:
    _freeze()
    sys.path.insert(0, str(REPO / "src"))
    import logging

    logging.disable(logging.CRITICAL)
    _install_capture()
    from remedy.interfaces.config import load_config

    ctx = Ctx(home, user, project_root)
    steps_spec = CASES[name](ctx)
    pairs = _replacements(ctx)
    files_before = _snapshot((home, "{HOME}"), (project_root, "{PROJECT}"))
    config = load_config()
    steps = [_run_step(ctx, op, inp) for op, inp in steps_spec]
    fixture = {
        "case": name,
        "doc": (CASES[name].__doc__ or "").strip(),
        "clock": CLOCK,
        "session_id": SID,
        "notes": ctx.notes,
        "config": config,
        "files": files_before,
        "readers": ctx.readers,
        "steps": steps,
    }
    fixture = _norm(fixture, pairs)
    text = json.dumps(fixture, ensure_ascii=False, indent=2, sort_keys=False, default=str)
    for raw, _tag in pairs:
        if len(raw) > 8 and raw in text:
            raise RuntimeError(f"unnormalised path left in fixture: {raw}")
    for marker in ("remedy-prompt-fixture-", os.environ.get("RECORD_REAL_USER", ""), REPO.name):
        if marker and len(marker) > 3 and marker in text:
            raise RuntimeError(f"machine-specific text left in fixture: {marker}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{name}.json").write_text(text + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Parent side: one clean child process per case.
# --------------------------------------------------------------------------


def _clean_env(home: Path, user: Path) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.upper().endswith("_API_KEY") and "TOKEN" not in k.upper() and not k.upper().startswith("REMEDY_")
    }
    env["REMEDY_HOME"] = str(home)
    env["REMEDY_DESKTOP_SIDECAR"] = "1"
    env["HOME"] = str(user)
    env["USERPROFILE"] = str(user)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONHASHSEED"] = "0"
    # The real profile folder name, only so the child can prove it left no trace.
    env["RECORD_REAL_USER"] = Path.home().name
    return env


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "--child":
        child(argv[2], Path(argv[3]), Path(argv[4]), Path(argv[5]))
        return 0
    names = argv[1:] or list(CASES)
    failed = []
    for name in names:
        if name not in CASES:
            print(f"unknown case {name}", file=sys.stderr)
            return 2
        base = Path(os.path.realpath(tempfile.mkdtemp(prefix="remedy-prompt-fixture-")))
        # REMEDY_HOME is ~/.remedy, as on a default install: prompt.assemble gets
        # no home_dir from Go and its runtime falls back to ~/.remedy.
        user, proj = base / "user", base / "work" / "project"
        home = user / ".remedy"
        for p in (home, proj):
            p.mkdir(parents=True)
        try:
            r = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--child", name, str(home), str(user), str(proj)],
                env=_clean_env(home, user),
                cwd=str(base),
                timeout=300,
            )
            print(f"{name}: {'ok' if r.returncode == 0 else 'FAILED'}")
            if r.returncode != 0:
                failed.append(name)
        finally:
            shutil.rmtree(base, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
