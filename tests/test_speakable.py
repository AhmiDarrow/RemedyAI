"""Speakable owner sentences never name tools."""

from __future__ import annotations

from remedy.core.speakable import (
    explain_plan,
    speakable_blocked,
    speakable_checkpoint,
    speakable_done,
    speakable_plan,
    speakable_progress,
    strip_tool_names,
)


def test_strip_tool_names():
    assert "computer_click" not in strip_tool_names("then computer_click the button")
    assert "life_drive" not in strip_tool_names("call life_drive next")


def test_plan_is_one_sentence_with_choices():
    s = speakable_plan(
        "buy milk",
        ["Open the store", "Add milk", "Place order"],
        stops=["Place order"],
    )
    assert "buy milk" in s.lower()
    assert "Place order" in s
    assert "Yes, No, or Explain?" in s
    assert "computer_" not in s


def test_checkpoint_and_blocked_and_done():
    c = speakable_checkpoint("Place order")
    assert "Place order" in c
    assert "Yes, No, or Explain?" in c
    b = speakable_blocked("Add milk", "couldnt_verify")
    assert "Add milk" in b
    d = speakable_done("buy milk")
    assert "Done" in d
    assert "computer_" not in c + b + d


def test_progress_and_explain():
    p = speakable_progress(3, 5, "adding items")
    assert "Step 3 of 5" in p
    e = explain_plan("buy milk", "1. [done] Open")
    assert "Explain" in e
    assert "buy milk" in e
