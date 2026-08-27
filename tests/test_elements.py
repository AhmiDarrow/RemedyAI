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


def test_card_context_disambiguates_identical_controls():
    """Five identical 'Set as store' buttons — the query picks the one whose
    CARD context matches the store name/address. This is the store-selection
    failure: found the store but no way to click the right control."""
    from remedy.core.computer.elements import find_best_element, score_element

    els = [
        {
            "ref": f"e{i+1}",
            "tag": "button",
            "name": "Set as store",
            "context": ctx,
            "w": 120,
            "h": 36,
            "x": 300,
            "y": 100 + i * 80,
        }
        for i, ctx in enumerate(
            [
                "Springfield Sunshine 1500 E Sunshine St, Springfield MO 65804",
                "Hueytown Supercenter 1420 Ave, Hueytown AL 35023",
                "Republic Rd 3520 W Republic Rd, Springfield MO 65807",
            ]
        )
    ]
    best = find_best_element(els, "set as store Hueytown 35023")
    assert best is not None and best["ref"] == "e2"
    # The right card outscores an identical-label sibling.
    assert score_element(els[1], "set as store Hueytown 35023") > score_element(
        els[0], "set as store Hueytown 35023"
    )


def test_som_shows_context_and_selected_state():
    """SoM list surfaces the card context (to disambiguate) and aria state (so
    the model does not re-toggle an already-selected store/tab)."""
    from remedy.core.computer.elements import format_som_list

    els = [
        {
            "ref": "e1",
            "tag": "button",
            "name": "Set as store",
            "context": "Hueytown Supercenter 1420 Ave AL 35023",
            "x": 1,
            "y": 1,
        },
        {
            "ref": "e2",
            "tag": "button",
            "name": "Your store",
            "context": "Springfield Sunshine",
            "state": "true",
            "x": 1,
            "y": 2,
        },
    ]
    som = format_som_list(els)
    assert 'in: "Hueytown Supercenter' in som
    assert "[selected]" in som  # e2 already-chosen store


def test_nonsense_query_does_not_fire_on_stopword():
    """A query with no meaningful overlap must not match a control via a
    trivial token — 'click XYZZY QUUX' should find nothing, not a random link."""
    from remedy.core.computer.elements import find_best_element

    els = [
        {"ref": "e1", "name": "Request a substitution", "tag": "a", "w": 120, "h": 30},
        {"ref": "e2", "name": "View your orders", "tag": "a", "w": 120, "h": 30},
    ]
    # No shared meaningful token → below the 20 min_score → None
    assert find_best_element(els, "the a to of quuxzzy") is None
    # But a real meaningful token still matches
    assert (find_best_element(els, "substitution") or {}).get("ref") == "e1"


def test_composer_placeholder_does_not_click_gif_toolbar():
    """Live miss: click-by-text 'What's happening?' landed on Add a GIF
    because the 1-char name token 'a' is a substring of 'what's' / 'happening'
    and the GIF button's card wrapped the composer placeholder."""
    from remedy.core.computer.elements import find_best_element, score_element

    els = [
        {
            "ref": "e4",
            "tag": "textarea",
            "role": "textbox",
            "name": "Post text What's happening?",
            "placeholder": "What's happening?",
            "w": 400,
            "h": 80,
            "context": "Post Everyone can reply Add a GIF",
        },
        {
            "ref": "e7",
            "tag": "button",
            "role": "button",
            "name": "Add a GIF",
            "w": 32,
            "h": 32,
            "context": "What's happening? Post Everyone can reply Add a GIF",
        },
        {
            "ref": "e11",
            "tag": "a",
            "role": "link",
            "name": "Post",
            "w": 48,
            "h": 28,
            "context": "Home Explore",
        },
        {
            "ref": "e3",
            "tag": "button",
            "role": "button",
            "name": "Post",
            "type": "submit",
            "w": 72,
            "h": 32,
            "context": "What's happening? Everyone can reply",
        },
    ]
    composer = find_best_element(els, "What's happening?")
    assert composer is not None
    assert composer["ref"] == "e4"
    assert score_element(els[1], "What's happening?") < 20

    post = find_best_element(els, "Post")
    assert post is not None
    assert post["ref"] == "e3"
    assert score_element(els[3], "Post") > score_element(els[2], "Post")


def test_app_banner_continue_is_downranked_vs_form_continue():
    """Reddit 'Continue' in 'View in Reddit App' is not the form Continue."""
    from remedy.core.computer.elements import find_best_element, score_element

    els = [
        {
            "ref": "e9",
            "tag": "button",
            "name": "Continue",
            "w": 80,
            "h": 36,
            "context": "View in Reddit App",
        },
        {
            "ref": "e20",
            "tag": "button",
            "name": "Continue",
            "w": 80,
            "h": 36,
            "context": "Add flair and tags Title Body",
        },
    ]
    best = find_best_element(els, "Continue")
    assert best is not None
    assert best["ref"] == "e20"
    assert score_element(els[1], "Continue") > score_element(els[0], "Continue")


def test_label_matches_query_and_url_path_divergence():
    from remedy.core.computer.elements import (
        label_matches_query,
        looks_like_field_prompt,
        parse_click_landed,
        urls_path_diverged,
    )

    landed = parse_click_landed(
        "Clicked text=What's happening? (ok:27:button:button:Add a GIF)"
    )
    assert landed.get("name") == "Add a GIF"
    assert landed.get("tag") == "button"
    assert not label_matches_query(landed["name"], "What's happening?")
    assert label_matches_query("Post text What's happening?", "What's happening?")
    assert label_matches_query("Post", "Post")
    assert looks_like_field_prompt("What's happening?")
    assert looks_like_field_prompt("Title")
    assert not looks_like_field_prompt("Post")
    assert urls_path_diverged(
        "https://x.com/compose/post",
        "https://x.com/i/foundmedia/search",
    )
    assert not urls_path_diverged(
        "https://x.com/compose/post",
        "https://x.com/compose/post?foo=1",
    )


def test_modal_and_compose_oracle_family() -> None:
    from remedy.core.computer.elements import (
        detect_modal_obstacle,
        draft_still_on_page,
        is_compose_url,
        looks_like_publish_verb,
    )

    gif = detect_modal_obstacle(
        url="https://x.com/i/foundmedia/search",
        title="Categories — GIF Search / X",
    )
    assert gif is not None
    banner = detect_modal_obstacle(
        elements=[
            {
                "name": "Continue",
                "context": "View in Reddit App",
                "tag": "button",
            }
        ]
    )
    assert banner is not None
    assert looks_like_publish_verb("Post")
    assert looks_like_publish_verb("Tweet")
    assert not looks_like_publish_verb("What's happening?")
    assert is_compose_url("https://x.com/compose/post")
    assert is_compose_url("https://www.reddit.com/r/LocalLLaMA/submit/")
    assert not is_compose_url("https://x.com/Ahmi_Darrow/status/123")
    body = "Remedy 0.41.5 is multilingual now — Windows and Linux."
    assert draft_still_on_page(body, "compose\n" + body + "\nPost")
    assert not draft_still_on_page(body, "Your post is now live on the timeline")
