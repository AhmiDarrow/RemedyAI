"""Record the Python post-turn skill learning into native/go/mind/learn/testdata.

Each case is a list of turns. Every turn runs the production path of
``remedy.core.production_learn._finish_production_turn_inner`` that touches
learned skills (normalise steps, feed the pattern buffer, ``auto_learn_from_turn``)
plus ``record_skill_turn_outcome``, against a temp home, with a fixed clock
and a counting uuid4. The nano-swarm pattern buffer is reset before each turn:
the Go port gates on the steps of the turn alone, so recording must not carry
steps from an earlier turn into the gate.

Run from the repo root:  python scripts/record_learn_fixtures.py
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "native" / "go" / "mind" / "learn" / "testdata"

_clock = {"now": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)}
_counter = {"n": 0}


class FakeDT(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: ARG003
        return _clock["now"]


def _fake_uuid4():
    _counter["n"] += 1
    return uuid.UUID(int=_counter["n"], version=4)


def s(tool, ok=True, result="", error="", ms=10):
    return {"tool": tool, "ok": ok, "result": result, "error": error, "duration_ms": ms}


def good(tool, result="done"):
    return s(tool, True, result)


def bad(tool, error="boom"):
    return s(tool, False, "", error)


EDIT_WORK = [
    good("file_read", "read main.go"),
    good("file_read", "read util.go"),
    good("file_edit", "edited main.go"),
    good("bash_exec", "go build ok"),
    good("file_edit", "edited util.go"),
    good("bash_exec", "go test ok"),
]

CASES = {
    "too_small": [
        {"message": "look at the readme", "steps": [good("file_read"), good("file_edit")]}
    ],
    "single_tool_loop": [
        {"message": "read every file in the repo", "steps": [good("file_read")] * 6}
    ],
    "read_edit_bash": [{"message": "Fix the build in the parser module", "steps": EDIT_WORK}],
    "same_work_again": [
        {"message": "Fix the build in the parser module", "steps": EDIT_WORK, "session": "s1"},
        {"message": "Fix the build in the parser module", "steps": EDIT_WORK, "session": "s2"},
    ],
    "mostly_failed": [
        {
            "message": "deploy the service",
            "steps": [
                good("file_read"),
                bad("bash_exec", "exit 1"),
                bad("bash_exec", "exit 2"),
                bad("host_run", "denied"),
                good("file_edit"),
                bad("bash_exec", "exit 1"),
                good("file_read"),
            ],
        }
    ],
    "hard_won_recovery": [
        {
            "message": "Get the flaky integration suite green again",
            "steps": [
                good("file_read", "read suite"),
                bad("bash_exec", "timeout"),
                good("repo_search", "found helper"),
                bad("file_edit", "patch rejected"),
                good("file_edit", "patched"),
                bad("bash_exec", "exit 3"),
                good("list_dir", "listed"),
                good("bash_exec", "pass"),
                good("job_run", "verified"),
            ],
            "now": "2026-01-02T03:04:05.123456+00:00",
        }
    ],
    # Starts discovered, then is lifted to validated by the second write (skill_path etc.).
    "elevated_to_validated": [
        {
            "message": "Tune the cache layer",
            "steps": [
                good("list_dir"),
                good("file_edit"),
                good("web_fetch"),
                bad("bash_exec"),
                good("bash_exec"),
                bad("bash_exec"),
                good("file_read"),
                good("web_fetch"),
            ],
        }
    ],
    "meta_tools_only": [
        {
            "message": "what skills do I have",
            "steps": [
                good("skill_search"),
                good("skill_activate"),
                good("skill_reload"),
                good("local_discover"),
                good("skill_search"),
            ],
        }
    ],
    "unsafe_title": [
        {
            "message": (
                'Fix C:\\Users\\bob\\app and /etc/passwd <b>& rm -rf; "quoted" caf\u00e9 '
                "r\u00e9sum\u00e9 handler: handle\u2026 " + "very long context " * 12 + "\nsecond line"
            ),
            "steps": EDIT_WORK,
        }
    ],
    "secret_inline": [
        {
            "message": "deploy the app with api_key=sk-ant-api03-fake-abcdefghijklmnopq now",
            "steps": EDIT_WORK,
        }
    ],
    "secret_only": [
        {
            "message": "sk-ant-api03-fake-abcdefghijklmnopq",
            "steps": EDIT_WORK,
        }
    ],
    "verb_title": [{"message": "ok", "steps": EDIT_WORK}],
    "outcome_grading": [
        {"message": "Fix the build in the parser module", "steps": EDIT_WORK, "session": "a"},
        {
            "message": "use the skill",
            "steps": [good("file_read"), good("file_read"), good("file_read")],
            "session": "b",
            "skills": ["file-read-fix-the-build-in-the-parser-module", "file_read"],
        },
        {
            "message": "use the skill again",
            "steps": [good("file_read"), bad("bash_exec"), bad("bash_exec"), bad("bash_exec")],
            "session": "c",
            "skills": ["file-read-fix-the-build-in-the-parser-module"],
        },
        {
            "message": "stopped half way",
            "steps": [good("file_read")],
            "session": "c",
            "aborted": True,
            "skills": ["file-read-fix-the-build-in-the-parser-module"],
        },
    ],
}


def normalise(home: Path, text: str) -> str:
    text = text.replace(str(home.resolve()), "<home>").replace(str(home), "<home>")
    text = text.replace("\\\\", "/").replace("<home>\\", "<home>/")
    return re.sub(r"\\skills\\", "/skills/", text)


def snapshot(home: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for p in sorted(home.rglob("*")):
        if p.is_file():
            files[p.relative_to(home).as_posix()] = normalise(
                home, p.read_text(encoding="utf-8")
            )
    return files


class Registry:
    """Minimal skill registry over the disk, as the runtime registry would answer."""

    def __init__(self, skills_dir: Path) -> None:
        self.dir = skills_dir

    def get_skill(self, name: str):
        from remedy.skills.loader import load_skill_from_dir

        try:
            return load_skill_from_dir(self.dir / name)
        except Exception:
            return None


def run_case(turns: list[dict]) -> dict:
    from remedy.core import agent_learn, production_learn
    from remedy.core.learning_loop import LearningLoop
    from remedy.nanoswarm import coordinator, get_swarm

    recorded = []
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        for t in turns:
            now = t.get("now", "2026-01-02T03:04:05+00:00")
            _clock["now"] = datetime.fromisoformat(now)
            coordinator._swarm = None  # fresh pattern buffer per turn (see module doc)
            session = t.get("session", "sess")
            steps = production_learn._normalize_steps(t["steps"])
            pattern = get_swarm().pattern
            for st in steps:
                pattern.on_tool_step(
                    tool_name=st["tool"],
                    success=bool(st["ok"]),
                    duration_ms=float(st.get("duration_ms") or 0.0),
                    session_id=session,
                )
            loop = LearningLoop(skills_dir=home / "skills")
            aborted = bool(t.get("aborted"))
            skill = None
            title = agent_learn._skill_title_from_steps(t["message"], steps)
            if not aborted:
                skill = agent_learn.auto_learn_from_turn(
                    learning_loop=loop, message=t["message"], session_id=session, steps=steps
                )
            decision = loop.last_lifecycle_decision
            reason = decision.reason if decision else None
            if skill is None and agent_learn.should_auto_learn_from_steps(steps) and not aborted:
                preg = get_swarm().pattern.pregate_trace(overall_success=True, title=title, session_id=session)
                if preg.get("skip_learn"):
                    reason = preg.get("reasoning")
            graded = agent_learn.record_skill_turn_outcome(
                loop,
                skills=t.get("skills", []),
                steps=steps,
                aborted=aborted,
                session_id=session,
                registry=Registry(home / "skills"),
            )
            recorded.append(
                {
                    "learned": skill is not None,
                    "skill": skill.manifest.name if skill is not None else "",
                    "merged": bool(reason and reason.startswith("Merged")),
                    "reason": reason,
                    "graded": graded,
                    "files": snapshot(home),
                }
            )
    return recorded


def main() -> None:
    import remedy.core.learning.refiner as refiner
    import remedy.core.learning_loop as ll

    ll.datetime = FakeDT
    refiner.datetime = FakeDT
    uuid.uuid4 = _fake_uuid4
    OUT.mkdir(parents=True, exist_ok=True)
    for name, turns in CASES.items():
        _counter["n"] = 0
        doc = {"name": name, "turns": turns, "expect": run_case(turns)}
        (OUT / f"{name}.json").write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(name, [(e["learned"], e["skill"], e["merged"]) for e in doc["expect"]])


if __name__ == "__main__":
    main()
