"""Agent name + gender presentation (female default)."""

from __future__ import annotations

from remedy.core.agent_identity import (
    DEFAULT_GENDER,
    DEFAULT_NAME,
    gender_presentation_line,
    identity_system_preamble,
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
