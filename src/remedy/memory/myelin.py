"""Myelin — crystallized cognition: frontier reasoning becomes local skill.

Weights are the most expensive place to store intelligence. Remedy's
native growth medium is the one she is made of: software. When a pathway
is worn by repetition — the partner keeps asking for the same shape of
work — the organism myelinates it, the way a brain sheathes a practiced
circuit: the next capable muscle extracts the *method* (not the answer)
as a runnable script with a test; the machine verifies it green; from
then on the competence is hers — permanent, local, free to run, and
still hers when no model is connected at all.

The loop:

  observe   — each turn, a cheap task-signature heuristic counts pathway
              use in a local ledger (no text kept beyond short scrubbed
              examples).
  candidate — a pathway worn ≥3 times with no sheath becomes a
              myelination candidate; one compact line rides the soul
              inject so the next capable muscle knows what to
              crystallize. (The curiosity ledger, v0: offline she
              prepares the question; muscle time is spent at leverage.)
  crystallize — the muscle authors ``run.py`` + ``test.py``; the machine
              runs the test in a subprocess; only green sheaths count as
              verified. Authored intelligence, machine-checked.
  run       — ``myelin_run`` executes a sheath locally (subprocess,
              timeout, output-capped). Works with any muscle or none.
  re-verify — vigil nights re-run stale sheath tests, muscle-free, so
              the library stays trustworthy while she sleeps.

Her intellect grows as an auditable library: you can read what she
knows, test it, and diff it — strength you can inspect (charter §1).

On disk: ``~/.remedy/myelin/ledger.json`` + ``myelin/sheaths/<slug>/``.
Execution safety: sheaths run with Remedy's own privileges — authoring
and running are approval-gated at the tool layer, exactly like the build
engine's host powers; this module never executes anything by itself
except the sheath's own test/run scripts when explicitly asked.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_json_atomic

MYELIN_DIRNAME = "myelin"
LEDGER_FILENAME = "ledger.json"
SHEATHS_DIRNAME = "sheaths"
SCHEMA_VERSION = 1

CANDIDATE_MIN_USES = 3
MAX_PATHWAYS = 64
MAX_EXAMPLES = 3
TEST_TIMEOUT_S = 60
RUN_TIMEOUT_S = 120
OUTPUT_CAP = 8000
REVERIFY_AFTER_S = 7 * 24 * 3600

_VERBS = (
    "reconcile|summari[sz]e|rename|convert|organi[sz]e|clean|extract|"
    "resize|merge|split|format|sort|export|backup|compress|translate|"
    "transcribe|draft|review|check|scan|fix|update|generate|deploy|"
    "install|download|schedule|track|compare|calculate|parse|validate|"
    "archive|dedupe|catalog|label|caption"
)
_SIG_VERB = re.compile(rf"(?i)\b({_VERBS})\b")
_STOP = frozenset(
    ["the", "a", "an", "my", "our", "your", "this", "that", "these", "those", "all", "every", "each", "some", "of", "for", "to", "in", "on", "at", "with", "from", "and", "or", "please", "can", "you", "me", "it", "them", "again"]
)
_SECRETISH = re.compile(
    r"(?i)(api[_-]?key|password|secret|token|sk-[a-z0-9]{8,}|bearer\s+\S+)"
)
# Questions / negations are talk about a task, not a request to do it.
_NOT_A_REQUEST = re.compile(
    r"(?i)^\s*(did|do not|don'?t|never|why|what|when|who|how|where|is|are|"
    r"was|were|does|should i|can i|could)\b"
)


def task_signature(text: str) -> str:
    """Cheap pathway signature: verb + up to two content words, or ''.

    "please reconcile my card receipts again" → "reconcile card receipts".
    Non-tasky text ('how are you', opinions) yields '' and is never counted.
    """
    t = (text or "").strip()
    if len(t) < 8 or _SECRETISH.search(t):
        return ""
    if _NOT_A_REQUEST.match(t):
        return ""
    m = _SIG_VERB.search(t)
    if not m:
        return ""
    # Negated verb right before the match ("don't archive those")
    prefix = t[max(0, m.start() - 12):m.start()].lower()
    if "don't " in prefix or "not " in prefix or "never " in prefix:
        return ""
    verb = m.group(1).lower()
    tail = t[m.end():]
    words = [
        w
        for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", tail)
        if w.lower() not in _STOP
    ][:2]
    if not words:
        return ""
    return " ".join([verb, *[w.lower() for w in words]])[:60]


def _home(home: str | Path | None = None) -> Path:
    import os

    if home:
        return Path(home).expanduser()
    env = (os.environ.get("REMEDY_HOME") or "").strip()
    return Path(env or "~/.remedy").expanduser()


def myelin_dir(home: str | Path | None = None) -> Path:
    d = _home(home) / MYELIN_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def sheaths_dir(home: str | Path | None = None) -> Path:
    d = myelin_dir(home) / SHEATHS_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ledger_path(home: str | Path | None = None) -> Path:
    return myelin_dir(home) / LEDGER_FILENAME


_lock = threading.Lock()


# --- pathway ledger --------------------------------------------------------


def load_ledger(home: str | Path | None = None) -> dict[str, Any]:
    p = _ledger_path(home)
    with suppress(Exception):
        from remedy.memory.statecache import read_json_cached

        raw = read_json_cached(p)
        if isinstance(raw, dict) and isinstance(raw.get("pathways"), dict):
            # Copy: callers mutate the ledger; the cache stays pristine.
            return {
                "schema": raw.get("schema", SCHEMA_VERSION),
                "pathways": {
                    k: {
                        "count": v.get("count", 0),
                        "last_ts": v.get("last_ts", 0.0),
                        "examples": list(v.get("examples") or []),
                    }
                    for k, v in raw["pathways"].items()
                    if isinstance(v, dict)
                },
            }
    return {"schema": SCHEMA_VERSION, "pathways": {}}


def save_ledger(ledger: dict[str, Any], home: str | Path | None = None) -> None:
    paths = ledger.get("pathways") or {}
    if len(paths) > MAX_PATHWAYS:
        keep = sorted(
            paths.items(), key=lambda kv: float(kv[1].get("last_ts") or 0), reverse=True
        )[:MAX_PATHWAYS]
        ledger["pathways"] = dict(keep)
    p = _ledger_path(home)
    write_json_atomic(p, ledger, ensure_ascii=False)


def observe_pathway(user_text: str, home: str | Path | None = None) -> str:
    """Count one pathway use. Returns the signature ('' when not tasky)."""
    sig = task_signature(user_text)
    if not sig:
        return ""
    with _lock:
        ledger = load_ledger(home)
        pw = ledger["pathways"].setdefault(
            sig, {"count": 0, "last_ts": 0.0, "examples": []}
        )
        pw["count"] = int(pw.get("count") or 0) + 1
        pw["last_ts"] = time.time()
        ex = str(user_text or "")[:80]
        examples = [e for e in (pw.get("examples") or []) if e][:MAX_EXAMPLES]
        if ex not in examples and not _SECRETISH.search(ex):
            examples = (examples + [ex])[-MAX_EXAMPLES:]
        pw["examples"] = examples
        save_ledger(ledger, home)
    return sig


# --- sheaths ---------------------------------------------------------------


@dataclass
class Sheath:
    """One crystallized competence: method + test + provenance."""

    name: str = ""
    slug: str = ""
    description: str = ""
    trigger: str = ""  # the pathway signature this myelinates
    created_ts: float = 0.0
    muscle: str = ""  # who authored it (provenance, never identity)
    verified: bool = False
    last_pass_ts: float = 0.0
    uses: int = 0
    last_run_ok: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "sheath").lower()).strip("-")
    return (s[:48] or "sheath").rstrip("-")


def sheath_path(slug: str, home: str | Path | None = None) -> Path:
    return sheaths_dir(home) / _slugify(slug)


def load_sheath(slug: str, home: str | Path | None = None) -> Sheath | None:
    d = sheath_path(slug, home)
    meta = d / "sheath.json"
    with suppress(Exception):
        from remedy.memory.statecache import read_json_cached

        raw = read_json_cached(meta)
        if isinstance(raw, dict):
            return Sheath(
                name=str(raw.get("name") or slug),
                slug=str(raw.get("slug") or slug),
                description=str(raw.get("description") or ""),
                trigger=str(raw.get("trigger") or ""),
                created_ts=float(raw.get("created_ts") or 0.0),
                muscle=str(raw.get("muscle") or ""),
                verified=bool(raw.get("verified")),
                last_pass_ts=float(raw.get("last_pass_ts") or 0.0),
                uses=int(raw.get("uses") or 0),
                last_run_ok=raw.get("last_run_ok"),
            )
    return None


def save_sheath(sheath: Sheath, home: str | Path | None = None) -> Path:
    d = sheath_path(sheath.slug, home)
    d.mkdir(parents=True, exist_ok=True)
    write_json_atomic(d / "sheath.json", sheath.to_dict(), ensure_ascii=False)
    return d


def list_sheaths(home: str | Path | None = None) -> list[Sheath]:
    out: list[Sheath] = []
    root = sheaths_dir(home)
    with suppress(OSError):
        for d in sorted(root.iterdir()):
            if d.is_dir():
                s = load_sheath(d.name, home)
                if s is not None:
                    out.append(s)
    return out


def _run_script(
    script: Path, args: list[str], *, timeout: int
) -> tuple[bool, str]:
    """Run one sheath script in a subprocess. Never shell; cwd = sheath dir."""
    try:
        r = subprocess.run(
            [sys.executable, str(script), *args],
            cwd=str(script.parent),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
        out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
        return r.returncode == 0, out[:OUTPUT_CAP]
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    except OSError as e:
        return False, f"exec failed: {e}"


def crystallize(
    *,
    name: str,
    description: str,
    script: str,
    test: str,
    trigger: str = "",
    muscle: str = "",
    home: str | Path | None = None,
) -> dict[str, Any]:
    """Save a sheath and verify it by running its test. Green = verified.

    The muscle authors; the machine checks. An unverified sheath is kept
    (visible, improvable) but marked — it never counts as competence.
    """
    nm = (name or "").strip()
    if not nm:
        return {"ok": False, "error": "name required"}
    if not (script or "").strip() or not (test or "").strip():
        return {"ok": False, "error": "both script and test are required"}
    slug = _slugify(nm)
    existing = load_sheath(slug, home)
    if existing is not None and existing.name.strip().lower() != nm.lower():
        return {
            "ok": False,
            "error": (
                f"slug {slug!r} already holds a different competence "
                f"({existing.name!r}) — pick a distinct name; overwriting a "
                "verified sheath silently is not allowed"
            ),
        }
    d = sheath_path(slug, home)
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.py").write_text(script, encoding="utf-8")
    (d / "test.py").write_text(test, encoding="utf-8")
    passed, output = _run_script(d / "test.py", [], timeout=TEST_TIMEOUT_S)
    sheath = Sheath(
        name=nm[:80],
        slug=slug,
        description=(description or "")[:300],
        trigger=(trigger or "")[:60],
        created_ts=time.time(),
        muscle=(muscle or "")[:80],
        verified=passed,
        last_pass_ts=time.time() if passed else 0.0,
    )
    save_sheath(sheath, home)
    return {
        "ok": True,
        "slug": slug,
        "verified": passed,
        "test_output": output[:1200],
        "note": (
            "Verified green — this competence is hers now, locally, forever."
            if passed
            else "Test FAILED — sheath saved unverified; fix run.py/test.py and re-verify."
        ),
    }


def verify_sheath(slug: str, home: str | Path | None = None) -> dict[str, Any]:
    """Re-run a sheath's test (muscle-free). Vigil calls this at night."""
    s = load_sheath(slug, home)
    if s is None:
        return {"ok": False, "error": f"no sheath {slug!r}"}
    test = sheath_path(s.slug, home) / "test.py"
    if not test.is_file():
        return {"ok": False, "error": "sheath has no test.py"}
    passed, output = _run_script(test, [], timeout=TEST_TIMEOUT_S)
    with _lock:
        s = load_sheath(slug, home) or s
        s.verified = passed
        if passed:
            s.last_pass_ts = time.time()
        save_sheath(s, home)
    return {"ok": True, "slug": s.slug, "verified": passed, "test_output": output[:1200]}


def run_sheath(
    slug: str,
    args: list[str] | None = None,
    home: str | Path | None = None,
) -> dict[str, Any]:
    """Execute a sheath locally. Works with any muscle worn, or none."""
    s = load_sheath(slug, home)
    if s is None:
        return {"ok": False, "error": f"no sheath {slug!r}"}
    run = sheath_path(s.slug, home) / "run.py"
    if not run.is_file():
        return {"ok": False, "error": "sheath has no run.py"}
    ok, output = _run_script(
        run, [str(a) for a in (args or [])], timeout=RUN_TIMEOUT_S
    )
    with _lock:
        s = load_sheath(slug, home) or s
        s.uses += 1
        s.last_run_ok = ok
        save_sheath(s, home)
    return {
        "ok": ok,
        "slug": s.slug,
        "verified": s.verified,
        "uses": s.uses,
        "output": output,
    }


# --- candidates + surfaces -------------------------------------------------


_cand_cache: dict[str, tuple[tuple[int, int], list[dict[str, Any]]]] = {}


def _cand_key(home: str | Path | None) -> tuple[int, int]:
    def _mt(p: Path) -> int:
        try:
            return p.stat().st_mtime_ns
        except OSError:
            return 0

    return _mt(_ledger_path(home)), _mt(sheaths_dir(home))


def candidates(home: str | Path | None = None) -> list[dict[str, Any]]:
    """Worn pathways with no sheath — what the next muscle should crystallize."""
    hk = str(_home(home).resolve())
    key = _cand_key(home)
    with _lock:
        hit = _cand_cache.get(hk)
        if hit is not None and hit[0] == key:
            return [dict(c) for c in hit[1]]
    ledger = load_ledger(home)
    covered = {s.trigger for s in list_sheaths(home) if s.trigger}
    covered |= {s.slug for s in list_sheaths(home)}
    out = []
    for sig, pw in (ledger.get("pathways") or {}).items():
        if int(pw.get("count") or 0) < CANDIDATE_MIN_USES:
            continue
        if sig in covered or _slugify(sig) in covered:
            continue
        out.append(
            {
                "signature": sig,
                "count": int(pw.get("count") or 0),
                "examples": list(pw.get("examples") or [])[:2],
            }
        )
    out.sort(key=lambda c: -c["count"])
    with _lock:
        _cand_cache[hk] = (key, [dict(c) for c in out])
        while len(_cand_cache) > 8:
            _cand_cache.pop(next(iter(_cand_cache)))
    return out


def candidates_line(home: str | Path | None = None, *, max_chars: int = 220) -> str:
    """≤1 line for the soul inject: what repetition has earned crystallizing."""
    cands = candidates(home)
    if not cands:
        return ""
    bits = ", ".join(f"“{c['signature']}” ×{c['count']}" for c in cands[:3])
    line = (
        f"Myelin candidates (worn pathways — crystallize into a tested local "
        f"skill with myelin_crystallize when convenient): {bits}"
    )
    return line[:max_chars]


def stale_sheath(home: str | Path | None = None) -> Sheath | None:
    """Oldest sheath needing re-verification — vigil's night-work pick."""
    best: Sheath | None = None
    now = time.time()
    for s in list_sheaths(home):
        test = sheath_path(s.slug, home) / "test.py"
        if not test.is_file():
            continue
        due = (not s.verified) or (now - s.last_pass_ts >= REVERIFY_AFTER_S)
        if due and (best is None or s.last_pass_ts < best.last_pass_ts):
            best = s
    return best


def myelin_status(home: str | Path | None = None) -> dict[str, Any]:
    sheaths = list_sheaths(home)
    return {
        "sheaths": [
            {
                "name": s.name,
                "slug": s.slug,
                "trigger": s.trigger,
                "verified": s.verified,
                "uses": s.uses,
                "description": s.description,
            }
            for s in sheaths
        ],
        "verified": sum(1 for s in sheaths if s.verified),
        "candidates": candidates(home),
        "note": (
            "Sheaths are her crystallized competence: authored by muscle, "
            "verified by machine, run locally — inspectable in "
            "~/.remedy/myelin/sheaths/."
        ),
    }
