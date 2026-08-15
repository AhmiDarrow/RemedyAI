"""Turn Cost Compiler — L0–L3 spend tiers (heuristics first, never block)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class TurnTier(IntEnum):
    """Spend class for a user turn. Higher = more agency / cost allowed."""

    L0_INSTANT = 0  # no frontier / tool-only local
    L1_LEAN = 1  # frontier, minimal tools
    L2_AGENCY = 2  # full tools, ledger, mid-turn slim
    L3_DEEP = 3  # work-alone / partitionable / long mission


# High-confidence L0: answer without frontier model when tools/local suffice.
_L0_WHOAMI = re.compile(
    r"(?is)^\s*("
    r"/whoami|who am i|what do you know about me|"
    r"what('?s| is) my name|my (name|profile)"
    r")\s*[.?!]?\s*$"
)
_L0_MODEL = re.compile(
    r"(?is)^\s*("
    r"what (model|provider|llm) (am i|are we|are you|is) (using|on|active)|"
    r"which (model|provider)|current (model|provider)|"
    r"what model is this"
    r")\s*[.?!]?\s*$"
)
_L0_SKILLS = re.compile(
    r"(?is)^\s*("
    r"(list |show |what )?(my |your |the )?(skills?|skill list)|"
    r"what skills? (do you|can you) (have|use)|"
    r"/skills?"
    r")\s*[.?!]?\s*$"
)
_L0_STATUS = re.compile(
    r"(?is)^\s*("
    r"(system |server )?status|are you (online|ready|there)|"
    r"health check|/status"
    r")\s*[.?!]?\s*$"
)
_L0_VERSION = re.compile(
    r"(?is)^\s*("
    r"(what('?s| is) )?(your |remedy |the )?version|"
    r"/version"
    r")\s*[.?!]?\s*$"
)
_L0_LIFE_DRIVE = re.compile(
    r"(?is)^\s*("
    r"what should i (do|work on|focus on)|"
    r"what('?s| is) next|"
    r"what now|"
    r"next (step|action|move)|"
    r"work on my goals?|"
    r"handle my goals?|"
    r"keep going on my (life )?goals?|"
    r"make progress on my goals?|"
    r"do the next (life )?step|"
    r"take the next step"
    r")\s*[.?!]?\s*$"
)
_L0_LIFE_PULSE = re.compile(
    r"(?is)^\s*("
    r"how am i doing|"
    r"how('?s| is) (my )?(year|life|week|goals?)|"
    r"weekly review|"
    r"review my goals"
    r")\s*[.?!]?\s*$"
)
_L0_LIFE_NOTICE = re.compile(
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
    r"that's finished|"
    r"i finished (my |the )?(life )?goal|"
    r"(the |my )?(life )?goal is (done|complete|finished)|"
    r"mark (the |my )?(life )?goal (as )?(done|complete)|"
    r"goal (is )?(complete|completed|done)|"
    r"i completed (my |the )?(life )?goal"
    r")\s*[.?!]?\s*$"
)
_L0_ORGANISM = re.compile(
    r"(?is)^\s*("
    r"how are you|"
    r"how('?re| are) you (doing|feeling)|"
    r"are you (alive|ok|okay|well)|"
    r"what('?s| is) your (state|mood)|"
    r"organism status"
    r")\s*[.?!]?\s*$"
)
_L0_LIFE_DIGEST = re.compile(
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

_L3_AUTONOMOUS = re.compile(
    r"(?is)\b("
    r"work alone|on your own|handle this on your own|"
    r"don'?t wait for me|do not wait for me|unattended|"
    r"fully autonomous|finish without me|take it from here|"
    # Departure intent — not "go over/through" prose or "step away from X" rhetoric
    r"i need to go(?!\s+(over|through|into|back|ahead|around|with)\b)|"
    r"step away(?!\s+from\b)|"
    r"be with my kids"
    r")\b"
)
_L3_PARTITION = re.compile(
    r"(?is)\b(?:"
    r"in parallel|fan.?out|spread out|across (?:the )?(?:modules?|codebase|packages?)|"
    r"multiple (?:areas?|modules?|trees?|packages?|urls?|sites?)|"
    # "review all options" is chat — require code/test targets
    r"review all (?:the )?(?:code|files?|modules?|packages?|tests?|src)|"
    r"whole (?:repo|codebase|project)|entire (?:repo|codebase)|"
    r"all tests|full suite|codebase.?wide|repo.?wide|"
    # Conceptual "compare X and Y" stays L1; multi-module compare is L3
    r"compare .+\b(?:and|vs|versus)\b.+\b(?:"
    r"modules?|packages?|services?|trees?|codebases?|dirs?|directories|"
    r"files?|repos?|implementations?"
    r")\b"
    r")\b"
)
_L2_AGENCY = re.compile(
    r"(?is)\b("
    r"implement|refactor|debug|fix (the |this |a )|"
    r"file_edit|file_read|create file|write (a |the )|"
    r"read (the |this |a |my )?(file|path|code|script|module|src)|"
    r"\bread\s+[\w./\\-]+\.(py|ts|tsx|js|md|json|toml|rs|go)\b|"
    r"bash|shell|pytest|npm run|cargo |"
    r"computer_|navigate|screenshot|click |"
    r"edit |open (the )?project|in (the )?repo|"
    r"list_dir|repo_search|mission_|spread_run|job_run|"
    # Common agency phrasing (missed by tool-id / path-only heuristics)
    r"run (the |all |my )?(tests?|pytest|suite|ci|npm|build)|"
    r"(check|show|open|look at|inspect|view|cat|type|print|tail|head) "
    r"(me |us )?(the |my |a |this )?(file|readme|package\.json|logs?|error|code|"
    r"script|config|src|test|output)|"
    r"(what|which|list|show) (files?|dirs?|directories|folders?) "
    r"(are |is )?(in|under|at|inside|here)|"
    r"(dump|list) (the )?(directory|dir|folder)( listing| contents)?|"
    r"\b(ls|dir)\b(\s|$)|"
    r"search (the )?(codebase|repo|project|code|tree)|"
    r"codebase search|grep (for |the )|"
    r"find where .{0,48}?\b(is )?(defined|implemented|declared|handled)\b|"
    r"where is (the |our |my )?[\w./\\-]{2,40}\b|"
    r"(goto|go to|navigate to|browse|open) "
    r"(https?://|www\.|\w+\.(com|org|net|io|dev)\b|gmail|google|"
    r"youtube|github|outlook|slack)|"
    r"open (that |the |a )?(pr|pull request)\b|"
    r"create (a |the )?(new )?(skill|file|module|test|folder|directory)|"
    r"add (a |an |the )?(unit |integration |e2e )?(test|tests)\b|"
    r"help me (fix|debug|implement|write|edit|build|run)|"
    r"look at the error|in the logs|"
    # VCS / package / process / CUA verbs that need tools
    r"\bgit\s+(status|diff|log|add|commit|push|pull|fetch|checkout|branch|stash|"
    r"rebase|merge|clone|reset|restore|show|remote)\b|"
    r"\b(commit|push|pull) (the |these |my )?(latest |recent )?(changes|commits?|branch|pr|prs)\b|"
    r"\b(uv|pip|npm|pnpm|yarn|cargo)\s+(install|sync|add|run|test|build|update)\b|"
    r"install (the )?(deps|dependencies|packages|requirements)\b|"
    r"\b(start|stop|restart|kill) (the )?(server|api|process|service|app|daemon|"
    r"sidecar|serve)\b|"
    r"\b(scroll|double.?click|right.?click)\b|"
    r"type (into|in) (the |this |a )|"
    r"update (the )?(changelog|readme)\b|"
    r"make sure (the )?(build|tests?|ci|suite) (works|passes|succeeds)|"
    r"bump (the )?version\b|"
    # Review / audit — tools required (was L1 → model said "activating skill" with no tools)
    # Target required so "review all options" (chat) stays L1; bare "review" is short-kick.
    r"review (the |this |my |our |a )?(project|codebase|code|repo|pr|pull request|"
    r"module|package|security|architecture|changes|diff|app|desktop)\b|"
    r"\bcode review\b|"
    # Short kicks only (full-string end) — not "review all options" chat
    r"(?:please\s+)?review\s*[.?!]?\s*$|"
    r"audit (the |this |my |our )?(project|code|security|repo|codebase)\b|"
    # Noun-first: "security audit", "code audit" (was L1 strip → prose-only)
    r"(security|code|project|repo|codebase)\s+audit\b|"
    r"walk (me |us )?(through )?(the )?(project|codebase|repo|code)\b|"
    r"give me (a |an )?(overview|tour of|status of) (the )?(project|codebase|repo)\b|"
    # Live 2026-08-13: "full bugsweep" classified L1 → tools=[] → "tool_c"
    r"bugsweep|bug.?sweep|bug.?hunt|bugfix|hotfix|"
    r"full\s+(bug\s*)?sweep|triage|cleanup|dogfood|"
    # Explore / inspect phrasing that message_wants_tools already rescues
    r"(inspect|analyze|analyse|explore|scan)\s+(the |this |my |our )?"
    r"(project|codebase|repo|code|tree|structure)\b|"
    r"look over (the |this |my |our )?(project|codebase|repo|code)\b|"
    r"check (the |this )?(project|repo) (structure|layout|tree)\b|"
    # Bare list/show files (was L1; tools only via message_wants_tools lag)
    r"\b(list|show|display)\s+(me |us )?(the |all |my )?(files?|dirs?|directories|folders?)\b|"
    r"\bwhat files\b|"
    # Skill progressive disclosure — need skill_activate tool (not prose "activating…")
    # "load …" alone is too broad (load balancer chat); require skill suffix.
    r"skill_activate|skill_search|skill_run|skill_reload|"
    r"\b(reload|rescan|refresh)\s+(all\s+)?(my\s+|the\s+)?skills?\b|"
    r"\b(activate|enable)\s+(the |a |this )?[\w.-]{2,48}(\s+skill)?\b|"
    r"\bload\s+(the |a |this )?[\w.-]{2,48}\s+skill\b|"
    r"\b(use|follow|run)\s+(the |a |this )?[\w.-]{2,48}\s+skill\b|"
    r"\b(use|load|enable)\s+(the |a )?skill\b|"
    r"\bactivate\s+skill\b|"
    # Live 2026-08-14: product change without implement/fix verbs → L1 strip
    r"we need (a |an |to )|"
    r"can we (add|resize|change|shrink|tighten|fix)|"
    r"resize|shrink|"
    r"autolock|auto[- ]?lock|"
    r"settings (and |/ )?(about )?(ui|dialog|panel|window)|"
    r"about (ui|dialog|panel|window)"
    r")\b"
)
# Paths + bare filenames that imply workspace tools
_L2_PATH = re.compile(
    r"(?:[A-Za-z]:\\|~/|\.\.?/|src/|desktop/|tests?/)[^\s]{2,}"
    r"|\b[\w.-]+\.(py|ts|tsx|js|jsx|md|json|toml|rs|go|css|html|yml|yaml|txt|log)\b"
)
_COMPLEX = re.compile(
    r"(?is)\b("
    r"and then|after that|step by step|multi.?step|"
    r"plan (this|out)|break (this )?down"
    r")\b"
)
# Pure chat greets / acks — skip L2/L3 regexes entirely (hot path).
_CHAT_SHORT = frozenset(
    {
        "hi",
        "hey",
        "hello",
        "yo",
        "sup",
        "hi!",
        "hey!",
        "hello!",
        "thanks",
        "thank you",
        "thanks!",
        "thx",
        "ty",
        "ok",
        "okay",
        "k",
        "yes",
        "no",
        "yep",
        "nope",
        "sure",
        "cool",
        "nice",
        "bye",
        "goodbye",
        "good morning",
        "good night",
        "gm",
        "gn",
        "lol",
        "lmao",
        "np",
        "got it",
        "sounds good",
        "perfect",
        "great",
        "awesome",
    }
)
# Path-ish chars — only run _L2_PATH when present (avoids regex on pure prose).
_PATH_HINT = re.compile(r"[\\/]|\.\w{1,8}\b|[A-Za-z]:")


@dataclass(frozen=True)
class TierPolicy:
    """What the hot path is allowed to spend for this tier."""

    tier: TurnTier
    label: str
    allow_frontier: bool
    allow_tools: bool
    full_snapshot: bool  # phase-2 pack/scout/spread
    force_spread: bool
    record_ir: bool
    shadow_high_blast: bool
    allow_critical_verify: bool
    max_tool_result_chars: int
    system_note: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "tier": int(self.tier),
            "label": self.label,
            "allow_frontier": self.allow_frontier,
            "allow_tools": self.allow_tools,
            "full_snapshot": self.full_snapshot,
            "force_spread": self.force_spread,
            "record_ir": self.record_ir,
            "shadow_high_blast": self.shadow_high_blast,
            "allow_critical_verify": self.allow_critical_verify,
            "max_tool_result_chars": self.max_tool_result_chars,
        }


# Partner 2026-08-09: 2k/12k tool-result caps were neutering file_read of
# real source (app.py ~25k, pdf_engine ~17k). L2/L3 must ship full files to
# the model; only L0 stays lean. 0 = no tier soft-cap (HARD_SAFETY only).
_POLICIES: dict[TurnTier, TierPolicy] = {
    TurnTier.L0_INSTANT: TierPolicy(
        tier=TurnTier.L0_INSTANT,
        label="L0_instant",
        allow_frontier=False,
        allow_tools=True,  # local tools only (PA / status)
        full_snapshot=False,
        force_spread=False,
        record_ir=False,
        shadow_high_blast=False,
        allow_critical_verify=False,
        max_tool_result_chars=16_000,
        system_note="",
    ),
    TurnTier.L1_LEAN: TierPolicy(
        tier=TurnTier.L1_LEAN,
        label="L1_lean",
        allow_frontier=True,
        allow_tools=False,  # bias off; may still answer
        full_snapshot=False,
        force_spread=False,
        record_ir=False,
        shadow_high_blast=False,
        allow_critical_verify=False,
        # Was 2000 — that truncated every real file_read when mis-tiered
        max_tool_result_chars=64_000,
        system_note="Reply to the person. Be brief.",
    ),
    TurnTier.L2_AGENCY: TierPolicy(
        tier=TurnTier.L2_AGENCY,
        label="L2_agency",
        allow_frontier=True,
        allow_tools=True,
        full_snapshot=True,
        force_spread=False,
        record_ir=True,
        shadow_high_blast=True,
        allow_critical_verify=True,
        # 0 = unlimited soft-cap (HARD_SAFETY_CHARS still applies)
        max_tool_result_chars=0,
        system_note=(
            "[Tier L2] Agency: prefer tools over monologue; "
            "batch independent reads; do not re-read known paths."
        ),
    ),
    TurnTier.L3_DEEP: TierPolicy(
        tier=TurnTier.L3_DEEP,
        label="L3_deep",
        allow_frontier=True,
        allow_tools=True,
        full_snapshot=True,
        force_spread=True,
        record_ir=True,
        shadow_high_blast=True,
        allow_critical_verify=True,
        max_tool_result_chars=0,
        system_note=(
            "[Tier L3] Deep / work-alone: finish end-to-end. "
            "When work partitions, use spread_run. Record progress; verify before done."
        ),
    ),
}


def tier_policy(tier: TurnTier | int | str) -> TierPolicy:
    if isinstance(tier, TurnTier):
        return _POLICIES[tier]
    if isinstance(tier, int):
        return _POLICIES.get(TurnTier(tier), _POLICIES[TurnTier.L1_LEAN])
    label = str(tier or "").strip().upper()
    for t, p in _POLICIES.items():
        if p.label.upper() == label or label == f"L{int(t)}":
            return p
    return _POLICIES[TurnTier.L1_LEAN]


def classify_turn_tier(
    user_text: str = "",
    *,
    intent: str = "chat",
    plan_mode: bool = False,
    has_attachments: bool = False,
    tools_enabled: bool = True,
    pure_action: bool = False,
    browse: bool = False,
) -> TurnTier:
    """Heuristic tier classification — deterministic, no network.

    Cheap flag / length / greeting exits run before multi-regex scans.
    """
    # Flag short-circuits (no text work)
    if has_attachments or pure_action or browse:
        return TurnTier.L2_AGENCY

    ut = (user_text or "").strip()
    if not ut:
        return TurnTier.L1_LEAN

    n = len(ut)
    intent_l = (intent or "chat").strip().lower()
    if intent_l == "autonomous":
        return TurnTier.L3_DEEP

    # Ultra-short pure chat (hi / thanks / ok) — no regexes
    if n <= 24 and "\n" not in ut:
        low_short = ut.lower().rstrip("!.?")
        if low_short in _CHAT_SHORT or ut.lower() in _CHAT_SHORT:
            return TurnTier.L1_LEAN

    # L0 instant (only short, single-clause) before heavier L2/L3 scans
    if n <= 120 and "\n" not in ut:
        if (
            _L0_WHOAMI.match(ut)
            or _L0_MODEL.match(ut)
            or _L0_SKILLS.match(ut)
            or _L0_STATUS.match(ut)
            or _L0_VERSION.match(ut)
            or _L0_LIFE_DRIVE.match(ut)
            or _L0_LIFE_PULSE.match(ut)
            or _L0_LIFE_NOTICE.match(ut)
            or _L0_LIFE_DIGEST.match(ut)
            or _L0_ORGANISM.match(ut)
        ):
            return TurnTier.L0_INSTANT

    # L3 only when text is long enough for keywords (min ~6–8 chars).
    # Lazy-cache partition match so agency path never re-runs the same regex.
    _partition: bool | None = None

    def _partition_hit() -> bool:
        nonlocal _partition
        if _partition is None:
            _partition = bool(n >= 10 and _L3_PARTITION.search(ut))
        return _partition

    if n >= 6 and _L3_AUTONOMOUS.search(ut):
        return TurnTier.L3_DEEP
    if _partition_hit():
        return TurnTier.L3_DEEP

    if plan_mode:
        return TurnTier.L2_AGENCY if tools_enabled else TurnTier.L1_LEAN

    if intent_l in ("tool", "skill"):
        return TurnTier.L2_AGENCY

    # Agency heuristics — skip path regex when no path-ish chars
    agency = bool(_L2_AGENCY.search(ut))
    if not agency and _PATH_HINT.search(ut):
        agency = bool(_L2_PATH.search(ut))
    if agency:
        # Nested L3 only when complex multi-step + partition language
        if n >= 20 and _COMPLEX.search(ut) and _partition_hit():
            return TurnTier.L3_DEEP
        return TurnTier.L2_AGENCY

    if intent_l == "memory":
        return TurnTier.L1_LEAN
    if intent_l == "plan":
        return TurnTier.L2_AGENCY

    if not tools_enabled:
        return TurnTier.L1_LEAN

    # Default chat — complex multi-step with code words elevates
    if n > 400 or (n >= 12 and _COMPLEX.search(ut)):
        low = ut.lower()
        return (
            TurnTier.L2_AGENCY
            if any(w in low for w in ("code", "file", "project", "repo", "bug", "error"))
            else TurnTier.L1_LEAN
        )

    return TurnTier.L1_LEAN


# "hi what is 1 + 1" / "what's 2*2" — never worth a thinking panel.
_TRIVIAL_ARITH = re.compile(
    r"(?is)^\s*(?:(?:hi|hey|hello|yo)\b[\s,!.]*)*"
    r"(?:what(?:'s| is)\s+)?"
    r"\d+\s*[-+*/x×÷]\s*\d+\s*\??\s*$"
)


def is_trivial_chat(user_text: str = "") -> bool:
    """True for greetings and one-line trivia (no thinking dump, no tools)."""
    ut = (user_text or "").strip()
    if not ut or "\n" in ut:
        return not ut
    low = ut.lower().rstrip("!.?")
    if low in _CHAT_SHORT or ut.lower() in _CHAT_SHORT:
        return True
    return bool(len(ut) <= 48 and _TRIVIAL_ARITH.match(ut))


def tier_system_block(tier: TurnTier | int) -> str:
    return tier_policy(tier).system_note
