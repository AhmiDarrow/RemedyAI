"""Self-declared open work: a final that lists what is still open is a hop boundary.

Yesterday's build session: the owner said "proceed with all remaining fixes …
and only once complete report" / "keep working until none remain" / "do all of
those things"; Remedy ran one hop, then ended the turn with "**Partial** …
**Still open:** Upload UI, tests, …" — nine times in a row. After tools had
run, any prose was accepted as the final answer. Under a finish-everything
request **or an active build**, a final that *itself* names required open work
must turn back into a hop. Green tests are a checkpoint, not the finish line.
"""

from __future__ import annotations

import re

from remedy.core.build_oracle import coerce_text_arg

_FINISH_EVERYTHING_RE = re.compile(
    r"(?:\buntil\s+(?:we\s+)?(?:run\s+out|none\s+remain|(?:it'?s\s+|they'?re\s+)?"
    r"(?:all\s+)?(?:done|complete|finished|green))"
    r"|\ball\s+(?:the\s+)?remaining\b"
    r"|\bclose\s+(?:(?:all|every|the)\s+)?(?:remaining\s+)?gaps?\b"
    r"|\bone\s+by\s+one\b"
    r"|\beach\s+(?:lesson|day|item|file|page|gap|route|screen)s?\b"
    r"|\bproceed\s+with\s+all\b"
    r"|\bfinish\s+(?:all|everything|it\s+all|the\s+rest)\b"
    r"|\bkeep\s+(?:working|going)\b"
    r"|\bonly\s+(?:once|when)\s+(?:complete|done|finished)\b"
    r"|\b(?:do\s+not|don'?t)\s+stop\b"
    r"|\bcomplete\s+(?:it\s+)?all\b"
    r"|\btake\s+as\s+long\s+as\s+(?:it\s+)?(?:is\s+)?needed\b"
    r"|\bno\s+gaps?\s+remain"
    r"|\ball\s+(?:of\s+)?(?:them|the\s+(?:days|items|files|pages|lessons))\b"
    r"|\bdo\s+all\s+of\s+(?:those|that|them|these|it)\b"
    r"|\bdo\s+all\s+(?:of\s+)?(?:the\s+)?(?:fixes|items|things|that)\b"
    r"|\ball\s+of\s+those\s+things\b)",
    re.IGNORECASE,
)

_OPEN_WORK_LINE_RE = re.compile(
    r"^\s*(?:[-*•]\s*)?(?:\*\*)?\s*(?:"
    r"still\s+open|still\s+to\s+do|still\s+(?:not\s+)?(?:wired|done|missing)|"
    r"not\s+(?:yet\s+)?(?:wired|done|finished|implemented|complete)(?:\s+yet)?|"
    r"remaining(?:\s+gaps?|\s+work|\s+items)?|left\s+to\s+do|open\s+gaps?|"
    r"next\s+(?:hop|slice|pass|step)|partial(?:\s+[—-].*)?|"
    r"needs?\s+(?:one\s+)?more\s+pass|todo|to\s+do"
    r")[ \t]*(?:\*\*)?[ \t]*(?:\(|:|—|-|$)",
    re.IGNORECASE | re.MULTILINE,
)
_OPEN_WORK_OPTIONAL_RE = re.compile(
    r"still\s+(?:open\s+)?\(?optional\)?|\boptional\b",
    re.IGNORECASE,
)
# Owner-choice leftovers, not product work. Session 765c 03:12: "Still open:
# local git commit if you want this hop saved · no push" re-armed a finished
# hop, then a 94s generation dumped index.css into the chat.
_OPEN_WORK_OWNER_CHOICE_RE = re.compile(
    r"(?i)("
    r"if you want this hop saved"
    r"|local git commit"
    r"|no push"
    r"|say when you want"
    r"|when you want (?:the )?next"
    r")"
)
_UNCHECKED_BOX_RE = re.compile(r"^\s*[-*]\s*\[\s\]\s+\S", re.MULTILINE)

OPEN_WORK_CONTINUE_NUDGE = (
    "You listed open work in that report and the owner asked for the whole job, "
    "not one hop. Do not stop here. Pick the first open item, do it with real "
    "tool calls, verify, then the next — and only report when nothing is left "
    "(or you hit a real blocker you cannot clear; say exactly what it is).\n"
    "todo_write each item completed as you finish it so the Build list on "
    "screen updates. Do not leave a row in_progress after you have moved on.\n"
    "Open items you named:\n{items}"
)


def message_asks_to_finish_everything(message: str | None) -> bool:
    """Owner asked for the whole job, not one hop ("until none remain", "close all gaps")."""
    m = coerce_text_arg(message)
    if not m:
        return False
    return bool(_FINISH_EVERYTHING_RE.search(m))


def final_declares_open_work(text: str | None) -> list[str]:
    """Open-work lines the model wrote into its own final ("Still open: …").

    Returns the matching lines (empty when the final is genuinely complete).
    A line that only names *optional* leftovers does not count.
    """
    t = coerce_text_arg(text)
    if not t:
        return []
    found: list[str] = []
    for m in _OPEN_WORK_LINE_RE.finditer(t):
        line_start = t.rfind("\n", 0, m.start()) + 1
        line_end = t.find("\n", m.end())
        line = t[line_start : line_end if line_end != -1 else len(t)].strip()
        if _OPEN_WORK_OPTIONAL_RE.search(line):
            continue
        if _OPEN_WORK_OWNER_CHOICE_RE.search(line):
            continue
        found.append(line[:160])
    for m in _UNCHECKED_BOX_RE.finditer(t):
        line_end = t.find("\n", m.end())
        found.append(t[m.start() : line_end if line_end != -1 else len(t)].strip()[:160])
    out: list[str] = []
    for f in found:
        if f and f not in out:
            out.append(f)
    return out


_FINDINGS_HEAD_RE = re.compile(
    r"(?im)^\s{0,3}#{1,3}\s*(?:issues?|findings?|defects?|risks?)\b"
)
_NUMBERED_FINDING_RE = re.compile(
    r"(?m)^\s*(?:[-*]\s*)?(?:\*\*)?(?:issue\s+)?(\d{1,2})[.)]\s+(\S.{10,220})"
)


def extract_review_findings(text: str | None) -> list[str]:
    """Numbered issues from a review report. Empty on a '1-10 fixed' wrap-up."""
    t = coerce_text_arg(text)
    if not t:
        return []
    if re.search(r"(?i)\bissues?\s*1\s*[–-]\s*10\s*fixed\b", t) and len(t) < 2500:
        return []
    if re.match(r"(?i)^\s*(?:\*\*)?all closed", t) and len(t) < 1200:
        return []
    headed = bool(_FINDINGS_HEAD_RE.search(t))
    items: list[str] = []
    for m in _NUMBERED_FINDING_RE.finditer(t):
        line = m.group(2).strip().strip("*").strip()
        if re.match(r"(?i)(fixed|done|closed)\b", line):
            continue
        if line and line not in items:
            items.append(line[:200])
        if len(items) >= 12:
            break
    if not headed and len(items) < 3:
        return []
    return items


def open_work_continue_message(items: list[str]) -> dict[str, str]:
    """User-role nudge that turns a 'Still open: …' final back into a hop."""
    body = "\n".join(f"- {i}" for i in (items or [])[:12]) or "- (see your previous message)"
    return {"role": "user", "content": OPEN_WORK_CONTINUE_NUDGE.format(items=body)}


def seed_open_work_todos(runtime: object, items: list[str]) -> int:
    """Put declared remaining work back on the machine checklist.

    The hop-stop pattern: she todo_writes two items, does them, marks them
    done, tests go green, then reports three more things as prose. Seeding
    those lines as pending todos keeps ``open_todo_count`` honest so the next
    green verify cannot strip tools.
    """
    rows: list[dict[str, str]] = []
    for raw in items or []:
        content = re.sub(r"^[-•]\s+|^\*(?!\*)\s+", "", str(raw or "").strip())
        content = re.sub(r"^\*\*[^*]{1,40}\*\*:?\s*", "", content).strip(" :—-")
        if len(content) < 8:
            continue
        if re.match(r"(?i)^(partial|still\s+open|remaining|todo)s?\.?$", content):
            continue
        tid = "ow-" + re.sub(r"[^a-z0-9]+", "-", content.lower())[:24].strip("-")
        rows.append({"id": tid or "ow", "content": content[:240], "status": "pending"})
    if not rows or runtime is None:
        return 0
    from remedy.core.build_todos import upsert_todos

    upsert_todos(runtime, rows, merge=True)
    return len(rows)
