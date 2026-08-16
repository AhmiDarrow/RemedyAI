"""Owner checkpoints: payment-surface escalation + raw-card detection."""

from __future__ import annotations

from remedy.core.approvals import (
    SENSITIVE_PREFIX,
    looks_like_payment_surface,
    looks_like_raw_card,
    payment_surface_checkpoint,
    raw_secret_checkpoint,
    sensitive_computer_checkpoint,
)

CHECKOUT = "https://shop.example.com/checkout Place order Order total $42 card number"
NORMAL = "https://news.example.com/article Read more Comments Share"


# --- payment surface escalation -------------------------------------------


def test_coordinate_click_on_payment_surface_is_checkpointed():
    r = payment_surface_checkpoint(
        "computer_click", label_resolved=False, page_context=CHECKOUT
    )
    assert r and r.startswith(SENSITIVE_PREFIX)


def test_enter_key_on_payment_surface_is_checkpointed():
    r = payment_surface_checkpoint(
        "computer_key", label_resolved=False, page_context=CHECKOUT, key="enter"
    )
    assert r and r.startswith(SENSITIVE_PREFIX)


def test_non_submit_key_on_payment_surface_is_fine():
    assert (
        payment_surface_checkpoint(
            "computer_key", label_resolved=False, page_context=CHECKOUT, key="tab"
        )
        is None
    )


def test_resolved_label_defers_to_text_classifier():
    # A readable label goes through the text classifier, not this fallback.
    assert (
        payment_surface_checkpoint(
            "computer_click", label_resolved=True, page_context=CHECKOUT
        )
        is None
    )


def test_coordinate_click_on_normal_page_is_not_checkpointed():
    assert (
        payment_surface_checkpoint(
            "computer_click", label_resolved=False, page_context=NORMAL
        )
        is None
    )


def test_non_mutation_tool_not_escalated():
    assert (
        payment_surface_checkpoint(
            "computer_screenshot", label_resolved=False, page_context=CHECKOUT
        )
        is None
    )


def test_payment_surface_detection():
    assert looks_like_payment_surface(CHECKOUT)
    assert looks_like_payment_surface("/cart")
    assert not looks_like_payment_surface(NORMAL)


# --- raw card detection ----------------------------------------------------


def test_luhn_valid_card_is_detected():
    assert looks_like_raw_card("my card is 4242 4242 4242 4242 exp 12/28")
    assert looks_like_raw_card("4111111111111111")


def test_random_digits_are_not_a_card():
    assert not looks_like_raw_card("call me at 5551234 or order 12345")
    assert not looks_like_raw_card("1234 5678 9012 3456")  # fails Luhn


def test_raw_card_typing_is_checkpointed():
    r = raw_secret_checkpoint("computer_type", "4242 4242 4242 4242")
    assert r and r.startswith(SENSITIVE_PREFIX)


def test_vault_handle_typing_is_not_flagged():
    assert raw_secret_checkpoint("computer_type", "{{vault:card-visa}}") is None


def test_non_type_tool_not_card_checked():
    assert raw_secret_checkpoint("computer_click", "4242424242424242") is None


# --- existing text classifier still intact --------------------------------


def test_text_classifier_still_catches_place_order():
    r = sensitive_computer_checkpoint("computer_click", "click text='Place order'")
    assert r and r.startswith(SENSITIVE_PREFIX)


def test_coding_tools_never_sensitive():
    assert sensitive_computer_checkpoint("file_write", "place order pay now") is None
    assert (
        payment_surface_checkpoint("bash_exec", label_resolved=False, page_context=CHECKOUT)
        is None
    )


# --- effortless in auto/full: the standing grant is the countersignature ---


def test_auto_full_mode_skips_payment_surface_heads_up():
    """Owner who granted auto/full uses the PC effortlessly — no per-action
    roadblock on checkout. Only the cautious `ask` default gets the heads-up."""
    from remedy.core.agent_computer_tools import _computer_approval_gate
    from remedy.core.approvals import APPROVALS

    runtime = type("R", (), {})()
    prev = APPROVALS.mode
    try:
        APPROVALS.set_mode("full")
        # Coordinate click on a checkout page: no block in full mode.
        assert (
            _computer_approval_gate(
                runtime, "computer_click", "click x=100 y=200",
                page_context=CHECKOUT, label_resolved=False,
            )
            is None
        )
        # Raw card typed in full mode: effortless, no block.
        assert (
            _computer_approval_gate(
                runtime, "computer_type", "type chars=19",
                typed_text="4242 4242 4242 4242",
            )
            is None
        )
        APPROVALS.set_mode("auto")
        assert (
            _computer_approval_gate(
                runtime, "computer_key", "key='enter'",
                page_context=CHECKOUT, label_resolved=False, key="enter",
            )
            is None
        )
    finally:
        APPROVALS.set_mode(prev)
