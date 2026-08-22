"""Bundled skills add ability; they never redefine who Remedy is.

Identity, temperament and creed come from agent_identity / the persona
canon. A skill is a procedure she applies when the task calls for it —
so no pack may open with "You are …", rename her, set a voice, or
declare a standing identity ("from now on you are …").
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from remedy.bundled_skills import iter_bundled_skill_dirs

_IDENTITY_RE = re.compile(
    r"(?im)^\s*(?:#+\s*)?(?:"
    r"you are (?:now )?(?:a|an|the) (?!(?:owner|user)\b)"  # "You are a game studio"
    r"|your name is\b"
    r"|from now on(?:,)? you (?:are|will be)\b"
    r"|act as (?:a|an|the)\b"
    r"|adopt (?:a|the) (?:persona|personality|voice|tone)\b"
    r"|speak (?:in|with) a [\w-]+ (?:voice|tone)\b"
    r"|ignore (?:your|the) (?:previous|existing) (?:identity|persona|instructions)\b"
    r")"
)


def _bodies():
    for d in iter_bundled_skill_dirs():
        for md in d.rglob("*.md"):
            yield md, md.read_text(encoding="utf-8", errors="replace")


@pytest.mark.parametrize("path,text", list(_bodies()), ids=lambda x: str(x) if isinstance(x, Path) else "")
def test_no_bundled_skill_redefines_remedy(path: Path, text: str):
    hits = [m.group(0).strip() for m in _IDENTITY_RE.finditer(text)]
    assert not hits, f"{path}: identity framing is not a skill's job: {hits[:3]}"
