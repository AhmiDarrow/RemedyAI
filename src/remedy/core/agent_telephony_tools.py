"""Conversational phone setup — probe, explain, ask, never a wizard."""

from __future__ import annotations

from typing import Any

from remedy.core.errors import format_tool_error


def register_telephony_tools(runtime: Any) -> None:
    """Register phone-line tools. Real PSTN is not on this PC yet."""

    def _home() -> str | None:
        cfg = getattr(runtime, "config", None)
        if isinstance(cfg, dict) and cfg.get("home_dir"):
            return str(cfg["home_dir"])
        if cfg is not None:
            h = getattr(cfg, "home_dir", None)
            if h:
                return str(h)
        h2 = getattr(runtime, "home", None)
        return str(h2) if h2 else None

    async def phone_status() -> str:
        """What this computer can do about phone calls, in plain sentences."""
        try:
            from remedy.telephony import consent
            from remedy.telephony.options import chosen
            from remedy.telephony.registry import offer_lines
        except Exception as e:
            return format_tool_error(str(e), code="PHONE_STATUS", tool_name="phone_status")
        home = _home()
        c = consent.read(home)
        pick = chosen(home)
        offer = offer_lines(home)
        terms = "Phone terms are agreed." if c.current else consent.ask(home)
        line = f"Chosen line: {pick}." if pick else "No line chosen yet."
        return (
            f"{terms}\n{line}\n{offer}\n"
            "Calling a real number is not on this computer yet. "
            "A loopback line can exercise the voice without dialling anyone."
        )

    async def phone_agree_terms() -> str:
        """Record that the owner agreed to the phone terms after they were said."""
        try:
            from remedy.telephony import consent
        except Exception as e:
            return format_tool_error(str(e), code="PHONE_TERMS", tool_name="phone_agree_terms")
        consent.accept(_home())
        return "Phone terms are recorded. I still will not call 911 or read secrets aloud."

    async def phone_choose_line(name: str = "") -> str:
        """Remember which way to get a line: sip, vm_voip, phone_wired, bluetooth_hfp."""
        try:
            from remedy.telephony.options import choose
            from remedy.telephony.registry import line_options
        except Exception as e:
            return format_tool_error(str(e), code="PHONE_CHOOSE", tool_name="phone_choose_line")
        wanted = (name or "").strip().lower()
        known = {o.name for o in line_options(_home()) if o.achievable}
        if wanted not in known:
            names = ", ".join(sorted(known)) or "none on this PC"
            return f"I do not have a line called {wanted!r}. On this computer: {names}."
        choose(wanted, home=_home())
        return (
            f"I will use {wanted} when a real line exists. "
            "Right now I can only loop back on this PC — nobody is called."
        )

    async def phone_set_policy(
        contact: str = "*",
        disclose: bool | None = None,
        record_notice: bool | None = None,
    ) -> str:
        """Per-contact disclosure / recording notice. Cannot waive 'never claim to be human'."""
        try:
            from remedy.telephony.policy import set_contact
        except Exception as e:
            return format_tool_error(str(e), code="PHONE_POLICY", tool_name="phone_set_policy")
        pol = set_contact(
            contact or "*",
            _home(),
            disclose=disclose,
            record_notice=record_notice,
        )
        extra = pol.opening_line()
        return f"Policy for {pol.contact}: disclose={pol.disclose}, record notice={pol.record_notice}. {extra}"

    runtime.tool_registry.register_builtin_handler(
        "phone_status",
        "What this computer can do about phone calls. Use when the owner asks "
        "to make calls / have a phone number / set up telephony. Speaks the "
        "options and what is missing. Does not dial.",
        phone_status,
        {"type": "object", "properties": {}},
    )
    runtime.tool_registry.register_builtin_handler(
        "phone_agree_terms",
        "Record agreement to the phone terms AFTER they were said aloud. "
        "Never skip saying: not an emergency service, can be wrong, "
        "recording/disclosure is the owner's duty, will not claim to be human.",
        phone_agree_terms,
        {"type": "object", "properties": {}},
    )
    runtime.tool_registry.register_builtin_handler(
        "phone_choose_line",
        "Remember the owner's pick: sip (own number), vm_voip (app in a VM), "
        "phone_wired (their SIM on a cable), bluetooth_hfp (same, wireless — last).",
        phone_choose_line,
        {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "phone_set_policy",
        "Per-contact call policy: whether to disclose she is an assistant, "
        "and whether to mention a written note. Cannot make her claim to be human.",
        phone_set_policy,
        {
            "type": "object",
            "properties": {
                "contact": {"type": "string"},
                "disclose": {"type": "boolean"},
                "record_notice": {"type": "boolean"},
            },
        },
    )
