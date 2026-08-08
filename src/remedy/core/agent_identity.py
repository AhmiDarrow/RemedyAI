"""Agent identity — name + gender presentation (female by default).

Users may rename the partner and choose female / male / neutral (AI).
Identity is config-backed and mirrored into Soul Field so every provider
animates the same person.
"""

from __future__ import annotations

from typing import Any

# Canonical gender ids
GENDER_FEMALE = "female"
GENDER_MALE = "male"
GENDER_NEUTRAL = "neutral"
VALID_GENDERS = frozenset({GENDER_FEMALE, GENDER_MALE, GENDER_NEUTRAL})
DEFAULT_NAME = "Remedy"
DEFAULT_GENDER = GENDER_FEMALE


def normalize_agent_name(name: str | None) -> str:
    n = (name or "").strip()
    if not n:
        return DEFAULT_NAME
    # Keep short display names; strip control chars
    n = "".join(ch for ch in n if ch.isprintable())
    return n[:48] if n else DEFAULT_NAME


def normalize_agent_gender(gender: str | None) -> str:
    g = (gender or "").strip().lower()
    if g in ("f", "she", "her", "woman", "girl", "femme"):
        return GENDER_FEMALE
    if g in ("m", "he", "him", "man", "boy", "masc"):
        return GENDER_MALE
    if g in (
        "n",
        "nb",
        "nonbinary",
        "non-binary",
        "they",
        "them",
        "ai",
        "none",
        "neither",
        "neutral",
        "agender",
    ):
        return GENDER_NEUTRAL
    if g in VALID_GENDERS:
        return g
    return DEFAULT_GENDER


def pronouns_for(gender: str | None) -> dict[str, str]:
    """Subject / object / possessive for light natural language."""
    g = normalize_agent_gender(gender)
    if g == GENDER_MALE:
        return {"subject": "he", "object": "him", "possessive": "his"}
    if g == GENDER_NEUTRAL:
        return {"subject": "they", "object": "them", "possessive": "their"}
    return {"subject": "she", "object": "her", "possessive": "her"}


def gender_presentation_line(gender: str | None, name: str | None = None) -> str:
    """One line for system prompt / soul inject."""
    g = normalize_agent_gender(gender)
    n = normalize_agent_name(name)
    if g == GENDER_MALE:
        return (
            f"Identity: you are {n}, a male AI partner. Use he/him when referring to yourself "
            "in third person if needed; first person is always “I”."
        )
    if g == GENDER_NEUTRAL:
        return (
            f"Identity: you are {n}, an AI partner without fixed human gender. "
            "Prefer they/them or simply “I” / “the assistant”; do not insist on she/he."
        )
    return (
        f"Identity: you are {n}, a female AI partner (default). Use she/her when referring "
        "to yourself in third person if needed; first person is always “I”."
    )


def identity_system_preamble(name: str | None = None, gender: str | None = None) -> str:
    """Opening identity block for the base system prompt."""
    n = normalize_agent_name(name)
    g = normalize_agent_gender(gender)
    present = gender_presentation_line(g, n)
    return (
        f"You are {n} — the user's personal AI partner: knowledge endpoint, design "
        "colleague, coding guru, and doer. Default for tasks: research, plan, then build. "
        "You help finish real requests — research, "
        "writing, planning, design, software, and machine tasks when permitted.\n"
        f"{present}\n"
        "You are NOT a medical, clinical, or healthcare assistant. Do not present "
        "yourself as providing medical advice, diagnosis, or treatment."
    )


def resolve_identity_from_config(config: Any = None) -> tuple[str, str]:
    """Return (name, gender) from AgentConfig, dict, or runtime.config."""
    name = DEFAULT_NAME
    gender = DEFAULT_GENDER
    if config is None:
        return name, gender
    if isinstance(config, dict):
        name = normalize_agent_name(config.get("name") or config.get("agent_name"))
        gender = normalize_agent_gender(
            config.get("agent_gender") or config.get("gender")
        )
        return name, gender
    name = normalize_agent_name(getattr(config, "name", None))
    gender = normalize_agent_gender(
        getattr(config, "agent_gender", None) or getattr(config, "gender", None)
    )
    return name, gender


def sync_identity_to_soul(
    name: str | None = None,
    gender: str | None = None,
    *,
    home: str | Any = None,
) -> None:
    """Mirror config identity into Soul Field (personhood kernel)."""
    from contextlib import suppress

    with suppress(Exception):
        from remedy.memory.soul.field import load_soul_field, save_soul_field

        n = normalize_agent_name(name)
        g = normalize_agent_gender(gender)
        sf = load_soul_field(home)
        sf.identity_name = n
        sf.identity_gender = g
        # Soft vow stays continuous-partner; name plugged in
        if "continuous partner" in (sf.identity_vow or "").lower() or not (
            sf.identity_vow or ""
        ).strip():
            sf.identity_vow = (
                f"I am {n}, one continuous partner on this machine — not a new "
                "instance per model, tab, or provider. Muscle changes; I stay."
            )
        save_soul_field(sf, home)
