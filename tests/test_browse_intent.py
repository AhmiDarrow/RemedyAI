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

    # Aliased sites pre-navigate so the agent starts on the right page —
    # and for a known retailer + product that page is the results page, not
    # the homepage (the retail search kick). The task is still NOT open-only:
    # the loop continues to add-to-cart / checkout after this navigate.
    assert (
        parse_browse_navigate_url("goto amazon and order paper towels")
        == "https://www.amazon.com/s?k=paper+towels"
    )


def test_plain_open_still_short_circuits() -> None:
    """Pure opens keep the fast path — no regression from the verb expansion."""
    from remedy.core.computer.browse_intent import is_open_only_browse

    assert is_open_only_browse("goto gmail") is True
    assert is_open_only_browse("open youtube") is True
    assert is_open_only_browse("bring up google") is True


def test_commerce_words_do_not_break_wiki_and_search_kicks() -> None:
    """Regression: bare nouns book/order/cart/post must not trip interaction
    and kill the open-only wiki/search fast paths (reviewer P1)."""
    from remedy.core.computer.browse_intent import (
        parse_browse_navigate_url,
        wants_page_interaction,
    )

    # Wiki topics containing commerce nouns still resolve to Wikipedia
    for msg in (
        "show me the jungle book wiki",
        "open the order of the phoenix wiki",
        "show me the green book wiki",
    ):
        assert wants_page_interaction(msg) is False, msg
        u = parse_browse_navigate_url(msg)
        assert u and "wikipedia.org" in u, msg

    # "in order to" idiom is not a commerce verb
    assert wants_page_interaction("open github in order to check notifications") is False

    # Nested search with a commerce noun in the query still builds a search URL
    u2 = parse_browse_navigate_url("can you goto google and search for the jungle book")
    assert u2 and "google.com/search" in u2 and "jungle" in u2


def test_retail_search_urls_go_straight_to_results():
    """Retailer search lands on the results URL — never homepage/store-finder."""
    from remedy.core.computer.browse_intent import retail_search_url

    assert retail_search_url("walmart", "milk") == "https://www.walmart.com/search?q=milk"
    assert retail_search_url("target", "paper towels") == (
        "https://www.target.com/s?searchTerm=paper+towels"
    )
    assert retail_search_url("amazon", "usb c cable") == (
        "https://www.amazon.com/s?k=usb+c+cable"
    )
    assert retail_search_url("kroger", "milk") == "https://www.kroger.com/search?query=milk"
    # Unknown site → None (caller falls back to generic handling)
    assert retail_search_url("bobs-corner-store", "milk") is None


def test_retail_site_aliases_resolve():
    from remedy.core.computer.browse_intent import resolve_site_alias

    assert resolve_site_alias("walmart") == "https://www.walmart.com"
    assert resolve_site_alias("best buy") == "https://www.bestbuy.com"
    assert resolve_site_alias("lowe's") == "https://www.lowes.com"


def test_retail_search_kick_lands_on_results():
    """'go to <retailer> and find/buy <product>' → the results URL, never the
    homepage or store-locator (the lost-turn trap)."""
    from remedy.core.computer.browse_intent import parse_browse_navigate_url

    u = parse_browse_navigate_url("go to walmart and find whole milk")
    assert u == "https://www.walmart.com/search?q=whole+milk"
    u = parse_browse_navigate_url("go to target and find paper towels")
    assert u == "https://www.target.com/s?searchTerm=paper+towels"
    u = parse_browse_navigate_url("go to amazon and find a 6ft usb-c cable")
    assert u and "amazon.com/s?k=" in u and "usb-c+cable" in u
    u = parse_browse_navigate_url("go to kroger and find a dozen large eggs")
    assert u == "https://www.kroger.com/search?query=dozen+large+eggs"
    # "buy/order" verbs count too
    u = parse_browse_navigate_url("walmart buy a gallon of milk")
    assert u == "https://www.walmart.com/search?q=gallon+of+milk"
    # Polite prefixes share the same matcher as the generic browse command.
    u = parse_browse_navigate_url("can you go to walmart and find whole milk")
    assert u == "https://www.walmart.com/search?q=whole+milk"
    u = parse_browse_navigate_url("could you go to target and find paper towels")
    assert u == "https://www.target.com/s?searchTerm=paper+towels"


def test_retail_open_only_still_homepage():
    """Bare 'go to walmart' (no product) stays on the homepage."""
    from remedy.core.computer.browse_intent import parse_browse_navigate_url

    assert parse_browse_navigate_url("go to walmart") == "https://www.walmart.com"
    # Non-retail search is unaffected
    assert "google.com/search" in (
        parse_browse_navigate_url("go to google and search elephant") or ""
    )


def test_retail_query_stops_at_first_clause():
    """A multi-step prompt searches the PRODUCT, not the whole paragraph
    jammed into ?q= (the '...milk. Add one gallon to the cart, then stop' bug)."""
    from remedy.core.computer.browse_intent import parse_browse_navigate_url

    u = parse_browse_navigate_url(
        "Go to walmart and find whole milk. Add one gallon of whole milk to the "
        "cart, then stop at the cart and hand it to me"
    )
    assert u == "https://www.walmart.com/search?q=whole+milk"
    u = parse_browse_navigate_url("go to kroger and find a dozen large eggs then add to cart")
    assert u == "https://www.kroger.com/search?query=dozen+large+eggs"
    u = parse_browse_navigate_url("go to walmart, find paper towels please")
    assert u == "https://www.walmart.com/search?q=paper+towels"
