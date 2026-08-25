from __future__ import annotations

from remedy.core.trust_profile import (
    TrustProfile,
    checkpoint_still_required,
    profile_skips_high_impact_ask,
)


def test_autonomous_does_not_waive_mail_or_pay():
    assert profile_skips_high_impact_ask(TrustProfile.AUTONOMOUS) is True
    assert checkpoint_still_required("mail_send", "hello") is not None
    assert checkpoint_still_required("mail_reply", "re: invoice") is not None
    pay = checkpoint_still_required("computer_click", "Place your order")
    assert pay is not None
    assert "Owner checkpoint" in pay


def test_conservative_and_balanced_still_ask():
    assert profile_skips_high_impact_ask(TrustProfile.CONSERVATIVE) is False
    assert profile_skips_high_impact_ask(TrustProfile.BALANCED) is False
    assert checkpoint_still_required("bash_exec", "echo hi") is None
