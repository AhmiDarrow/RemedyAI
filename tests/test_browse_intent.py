"""Browse intent → rail URL for short open/goto kicks."""

from __future__ import annotations

from remedy.core.computer.browse_intent import (
    parse_browse_navigate_url,
    resolve_site_alias,
    short_site_label,
)
from remedy.core.computer.router import normalize_url


def test_site_aliases() -> None:
    assert resolve_site_alias("gmail") == "https://mail.google.com"
    assert resolve_site_alias("Google") == "https://www.google.com"
    assert resolve_site_alias("youtube") == "https://www.youtube.com"
    assert resolve_site_alias("unknownsite") is None


def test_normalize_url_aliases() -> None:
    assert normalize_url("gmail") == "https://mail.google.com"
    assert normalize_url("https://mail.google.com") == "https://mail.google.com"


def test_parse_goto_gmail() -> None:
    assert parse_browse_navigate_url("goto gmail") == "https://mail.google.com"
    assert parse_browse_navigate_url("go to gmail") == "https://mail.google.com"
    assert parse_browse_navigate_url("open gmail") == "https://mail.google.com"
    assert parse_browse_navigate_url("bring up google") == "https://www.google.com"
    assert parse_browse_navigate_url("pull up youtube") == "https://www.youtube.com"


def test_parse_google_search() -> None:
    from urllib.parse import unquote_plus

    u = parse_browse_navigate_url("goto google and search elephant")
    assert u is not None
    assert "google.com/search" in u
    assert "elephant" in unquote_plus(u)
    u2 = parse_browse_navigate_url("go to google and search for blue whale")
    assert u2 and "blue+whale" in u2 or (u2 and "blue" in u2)
    u3 = parse_browse_navigate_url("search elephant on google")
    assert u3 and "elephant" in u3
    assert parse_browse_navigate_url("bring up google") == "https://www.google.com"


def test_clear_goals_intent() -> None:
    from remedy.core.computer.browse_intent import is_clear_goals_intent, is_pure_action_kick

    assert is_clear_goals_intent("clear goals") is True
    assert is_clear_goals_intent("just clear goals, we have none") is True
    assert is_clear_goals_intent("goto gmail") is False
    assert is_pure_action_kick("goto google and search elephant") is True
    assert is_pure_action_kick("clear goals") is True


def test_gmail_login_is_interaction_not_open_only() -> None:
    from remedy.core.computer.browse_intent import (
        is_open_only_browse,
        is_pure_action_kick,
        parse_browse_navigate_url,
        wants_page_interaction,
    )

    msg = (
        "goto gmail sign in, once there I want you to log me in "
        "the login input my username user@example.com"
    )
    assert wants_page_interaction(msg) is True
    assert is_open_only_browse(msg) is False
    assert is_pure_action_kick(msg) is False
    assert parse_browse_navigate_url(msg) == "https://mail.google.com"
    assert is_open_only_browse("goto gmail") is True
    assert parse_browse_navigate_url("goto gmail") == "https://mail.google.com"


def test_parse_full_url() -> None:
    assert (
        parse_browse_navigate_url("https://mail.google.com")
        == "https://mail.google.com"
    )
    assert (
        parse_browse_navigate_url("open https://en.wikipedia.org/wiki/Test")
        == "https://en.wikipedia.org/wiki/Test"
    )


def test_parse_wiki_topic() -> None:
    url = parse_browse_navigate_url("gta 5 wiki show me it")
    assert url is not None
    assert "wikipedia.org" in url
    assert "GTA" in url.upper() or "gta" in url.lower() or "5" in url


def test_non_browse_returns_none() -> None:
    assert parse_browse_navigate_url("hi") is None
    assert parse_browse_navigate_url("fix the login bug in src/") is None
    assert parse_browse_navigate_url("what is gmail") is None


def test_short_site_label() -> None:
    assert short_site_label("https://mail.google.com") == "Gmail"
    assert short_site_label("https://www.google.com") == "Google"
    assert "elephant" in short_site_label(
        "https://www.google.com/search?q=elephant"
    ).lower()


def test_life_task_verbs_are_interaction_not_open_only() -> None:
    """Commerce / life-task phrasing must never short-circuit as open-only.

    Regression for the headline life-task gap: "goto amazon and order X"
    previously matched open-only browse and the loop stopped after navigate
    (docs/LIFE_TASK_PARTNER.md).
    """
    from remedy.core.computer.browse_intent import (
        is_open_only_browse,
        is_pure_action_kick,
        parse_browse_navigate_url,
        wants_page_interaction,
    )

    tasks = [
        "goto amazon and order paper towels",
        "open amazon and buy a phone charger",
        "go to amazon, add paper towels to the cart and checkout",
        "open walmart.com and order my usual groceries",
        "goto youtube and subscribe to the channel",
        "open the pharmacy site and renew my prescription",
        "go to the dmv site and schedule an appointment",
        "open irs.gov and apply for an extension",
        "goto patreon and donate to the creator",
        "open gmail and send a reply to mom",
    ]
    for msg in tasks:
        assert wants_page_interaction(msg) is True, msg
        assert is_open_only_browse(msg) is False, msg
        assert is_pure_action_kick(msg) is False, msg

    # Aliased sites still pre-navigate so the agent starts on the right page.
    assert (
        parse_browse_navigate_url("goto amazon and order paper towels")
        == "https://www.amazon.com"
    )


def test_plain_open_still_short_circuits() -> None:
    """Pure opens keep the fast path — no regression from the verb expansion."""
    from remedy.core.computer.browse_intent import is_open_only_browse

    assert is_open_only_browse("goto gmail") is True
    assert is_open_only_browse("open youtube") is True
    assert is_open_only_browse("bring up google") is True
