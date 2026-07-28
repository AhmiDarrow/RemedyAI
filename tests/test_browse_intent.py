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
