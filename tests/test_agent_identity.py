"""Agent name + gender presentation (female default)."""

from __future__ import annotations

from remedy.core.agent_identity import (
    DEFAULT_GENDER,
    DEFAULT_NAME,
    gender_presentation_line,
    normalize_agent_gender,
    normalize_agent_name,
    pronouns_for,
)
from remedy.core.react_policy import build_system_prompt


def test_defaults():
    assert normalize_agent_name(None) == DEFAULT_NAME
    assert normalize_agent_name("") == "Remedy"
    assert normalize_agent_gender(None) == DEFAULT_GENDER
    assert DEFAULT_GENDER == "female"


def test_normalize_gender_aliases():
    assert normalize_agent_gender("she") == "female"
    assert normalize_agent_gender("he") == "male"
    assert normalize_agent_gender("they") == "neutral"
    assert normalize_agent_gender("AI") == "neutral"
    assert normalize_agent_gender("neither") == "neutral"


def test_normalize_name():
    assert normalize_agent_name("  Aria  ") == "Aria"
    assert len(normalize_agent_name("x" * 100)) == 48


def test_pronouns():
    assert pronouns_for("female")["subject"] == "she"
    assert pronouns_for("male")["subject"] == "he"
    assert pronouns_for("neutral")["subject"] == "they"


def test_system_prompt_name_and_gender():
    p = build_system_prompt("balanced", name="Nova", gender="male")
    assert "You are Nova" in p
    assert "male AI partner" in p.lower() or "he/him" in p.lower()
    p2 = build_system_prompt(None, name="Sage", gender="neutral")
    assert "You are Sage" in p2
    assert "without fixed human gender" in p2 or "they/them" in p2
    p3 = build_system_prompt(None)  # defaults
    assert "You are Remedy" in p3
    assert "female" in p3.lower()


def test_presentation_line():
    assert "female" in gender_presentation_line("female", "Remedy").lower()
    assert "male" in gender_presentation_line("male", "Rex").lower()


import pytest as _pytest

from remedy.core.agent_identity import pronoun_gender, pronoun_vars, render_pronouns


@_pytest.mark.parametrize(
    ("gender", "expect"),
    [
        ("female", ("she", "her", "her", "hers", "herself", "s")),
        ("male", ("he", "him", "his", "his", "himself", "s")),
        ("neutral", ("they", "them", "their", "theirs", "themself", "")),
        ("", ("they", "them", "their", "theirs", "themself", "")),
        (None, ("they", "them", "their", "theirs", "themself", "")),
        ("robot", ("they", "them", "their", "theirs", "themself", "")),
    ],
)
def test_pronoun_table_per_gender(gender, expect) -> None:  # noqa: ANN001
    p = pronouns_for(gender)
    got = (p["subject"], p["object"], p["possessive"], p["possessive_pronoun"], p["reflexive"], p["s"])
    assert got == expect


def test_pronoun_gender_aliases() -> None:
    assert pronoun_gender(" She ") == "female"
    assert pronoun_gender("M") == "male"
    assert pronoun_gender("nonbinary") == "neutral"


def test_render_pronouns_matches_desktop_tokens() -> None:
    tpl = "Steer while {they} work{s} · {agent} keeps {their} notes · {Their} call · {x}"
    assert render_pronouns(tpl, "female") == (
        "Steer while she works · Remedy keeps her notes · Her call · {x}"
    )
    assert render_pronouns(tpl, "male", "Atlas") == (
        "Steer while he works · Atlas keeps his notes · His call · {x}"
    )
    assert render_pronouns(tpl, "neutral") == (
        "Steer while they work · Remedy keeps their notes · Their call · {x}"
    )
    assert set(pronoun_vars("male")) == {
        "they", "them", "their", "theirs", "themself", "They", "Their",
        "s", "is", "has", "was", "agent",
    }


def test_english_catalogs_do_not_hardcode_agent_pronouns() -> None:
    import json
    import re
    from pathlib import Path

    from remedy.i18n import catalog as cat

    gendered = re.compile(r"\b(she|her|hers|herself|he|him|his|himself)\b", re.I)
    en_py = cat.EN
    root = Path(__file__).resolve().parents[1]
    en_go = json.loads((root / "native/go/httpapi/i18n_data.json").read_text(encoding="utf-8"))["en"]
    for source in (en_py, en_go):
        assert not {k: v for k, v in source.items() if gendered.search(str(v))}
