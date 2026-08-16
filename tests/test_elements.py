"""Element text matching for click-by-text."""

from __future__ import annotations

from remedy.core.computer.elements import find_best_element, find_best_elements, score_element


def test_exact_name_wins() -> None:
    els = [
        {"ref": "e1", "name": "Log in", "tag": "a", "w": 80, "h": 30},
        {"ref": "e2", "name": "Membership options", "tag": "button", "w": 140, "h": 36},
        {"ref": "e3", "name": "Home", "tag": "a", "w": 60, "h": 24},
    ]
    best = find_best_element(els, "Membership options")
    assert best is not None
    assert best["ref"] == "e2"
    assert score_element(best, "Membership options") >= 100


def test_partial_match() -> None:
    els = [
        {"ref": "e1", "name": "Sign in to continue", "tag": "button", "w": 100, "h": 40},
        {"ref": "e2", "name": "Cancel", "tag": "button", "w": 80, "h": 40},
    ]
    hits = find_best_elements(els, "sign in", top_k=2)
    assert hits
    assert hits[0]["ref"] == "e1"


def test_no_match() -> None:
    els = [{"ref": "e1", "name": "Home", "tag": "a", "w": 40, "h": 20}]
    assert find_best_element(els, "zebra warehouse", min_score=20) is None


def test_som_list_and_email_boost() -> None:
    from remedy.core.computer.elements import (
        extract_typed_credentials,
        format_som_list,
        score_element,
    )

    els = [
        {
            "ref": "e1",
            "name": "Email or phone",
            "tag": "input",
            "type": "email",
            "w": 200,
            "h": 40,
            "x": 10,
            "y": 20,
        },
        {"ref": "e2", "name": "Password", "tag": "input", "type": "password", "w": 200, "h": 40},
    ]
    assert score_element(els[0], "email") > score_element(els[1], "email")
    som = format_som_list(els, query="email")
    assert "[e1]" in som
    assert "Set-of-Mark" in som
    creds = extract_typed_credentials(
        "log me in with username user@example.com please"
    )
    assert creds.get("email") == "user@example.com"
