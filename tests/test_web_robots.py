"""robots.txt gate, per-host pacing, and the self-identifying User-Agent.

The transport layer (``_pinned_fetch``) is covered by test_web_fetch_ssrf; what
is checked here is the policy layer on top of it: a stated rule is obeyed, an
absent one is not invented, and two hits on one host are never back to back.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from remedy.core import agent_web_tools as w


@pytest.fixture(autouse=True)
def _clear_caches():
    """Robots answers and pacing timestamps are module state — never leak."""
    w._robots_cache.clear()
    w._last_fetch_at.clear()
    yield
    w._robots_cache.clear()
    w._last_fetch_at.clear()


def _robots(body: str):
    """Patch the transport so robots.txt returns *body*."""
    return patch.object(
        w, "_pinned_fetch", lambda url, **kw: (url, body.encode("utf-8"), "utf-8")
    )


def test_user_agent_names_itself_and_a_contact():
    assert w.USER_AGENT.startswith("RemedyAI-WebFetch/")
    assert "+https://" in w.USER_AGENT
    # The version must track the package, not a frozen literal.
    assert "/0.13 " not in w.USER_AGENT


def test_disallowed_path_is_refused():
    with _robots("User-agent: *\nDisallow: /private/\n"):
        with pytest.raises(ValueError, match="ROBOTS_BLOCKED"):
            w._robots_gate("https://example.com/private/page")


def test_allowed_path_passes_and_reports_no_delay():
    with _robots("User-agent: *\nDisallow: /private/\n"):
        assert w._robots_gate("https://example.com/public/page") == 0.0


def test_rule_aimed_at_remedy_specifically_is_obeyed():
    body = "User-agent: RemedyAI-WebFetch\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    with _robots(body):
        with pytest.raises(ValueError, match="ROBOTS_BLOCKED"):
            w._robots_gate("https://example.com/anything")


def test_crawl_delay_is_reported():
    with _robots("User-agent: *\nCrawl-delay: 4\nDisallow:\n"):
        assert w._robots_gate("https://example.com/page") == 4.0


def test_unreachable_robots_fails_open():
    """No served rule is not the same as a rule saying no."""

    def _boom(url, **kw):
        raise OSError("connection refused")

    with patch.object(w, "_pinned_fetch", _boom):
        assert w._robots_gate("https://example.com/page") == 0.0


def test_robots_answer_is_cached_per_origin():
    calls: list[str] = []

    def _count(url, **kw):
        calls.append(url)
        return (url, b"User-agent: *\nDisallow:\n", "utf-8")

    with patch.object(w, "_pinned_fetch", _count):
        w._robots_gate("https://example.com/a")
        w._robots_gate("https://example.com/b")
    assert len(calls) == 1
    assert calls[0].endswith("/robots.txt")


def test_pacing_spaces_two_hits_on_one_host():
    slept: list[float] = []
    with patch.object(w.time, "sleep", slept.append):
        w._pace_host("example.com", 0.0)
        w._pace_host("example.com", 0.0)
    # First hit goes straight out; only the second one waits.
    assert len(slept) == 1
    assert 0 < slept[0] <= w._DEFAULT_CRAWL_DELAY


def test_pacing_does_not_delay_a_first_hit():
    slept: list[float] = []
    with patch.object(w.time, "sleep", slept.append):
        w._pace_host("first-seen.example", 0.0)
    assert slept == []


def test_absurd_crawl_delay_is_refused_not_slept_through():
    with patch.object(w.time, "sleep", lambda _s: None):
        w._pace_host("slow.example", 0.0)
        with pytest.raises(ValueError, match="ROBOTS_DELAY"):
            w._pace_host("slow.example", 3600.0)


def test_polite_fetch_skips_the_gate_when_the_owner_turns_it_off():
    """``respect_robots=False`` must not even fetch robots.txt."""
    seen: list[str] = []

    def _fetch(url, **kw):
        seen.append(url)
        return (url, b"body", "utf-8")

    with patch.object(w, "_pinned_fetch", _fetch), patch.object(w.time, "sleep", lambda _s: None):
        w.polite_fetch("https://example.com/page", max_chars=100, respect_robots=False)
    assert seen == ["https://example.com/page"]


def test_polite_fetch_rechecks_robots_after_a_cross_host_redirect():
    """An open redirect must not carry a fetch onto a host that said no."""

    def _fetch(url, **kw):
        if url.endswith("/robots.txt"):
            if "start.example" in url:
                return (url, b"User-agent: *\nDisallow:\n", "utf-8")
            return (url, b"User-agent: *\nDisallow: /\n", "utf-8")
        return ("https://landed.example/final", b"body", "utf-8")

    with patch.object(w, "_pinned_fetch", _fetch), patch.object(w.time, "sleep", lambda _s: None):
        with pytest.raises(ValueError, match="ROBOTS_BLOCKED"):
            w.polite_fetch("https://start.example/go", max_chars=100, respect_robots=True)


# --- search backend selection -------------------------------------------------


def test_search_uses_ddg_when_no_local_backend():
    with patch.object(w, "_searxng_base", lambda runtime=None: ""), patch.object(
        w, "_openserp_rows", side_effect=RuntimeError("down")
    ), patch.object(
        w, "_ddg_rows", lambda q, n, t: [{"title": "t", "url": "https://e.org", "snippet": ""}]
    ):
        rows, backend = w.run_search("q", max_results=3, timeout=5.0)
    assert rows and "DuckDuckGo" in backend


def test_search_prefers_openserp_when_it_answers():
    with patch.object(w, "_searxng_base", lambda runtime=None: ""), patch.object(
        w,
        "_openserp_rows",
        lambda q, n, t: [{"title": "t", "url": "https://e.org", "snippet": ""}],
    ):
        rows, backend = w.run_search("q", max_results=3, timeout=5.0)
    assert rows and backend == "OpenSERP (local)"


def test_owner_instance_wins_and_never_asks_for_consent():
    """A search instance the owner runs needs no acceptance of anything."""
    with patch.object(w, "_searxng_base", lambda runtime=None: "https://searx.example"), patch.object(
        w, "_scraping_acked", lambda runtime=None: False
    ), patch.object(w, "_searxng_rows", lambda b, q, n, t: [{"title": "t", "url": "https://e.org", "snippet": ""}]):
        rows, backend = w.run_search("q", max_results=3, timeout=5.0)
    assert rows and backend == "your search instance"


def test_background_search_uses_ddg_without_a_second_ack():
    with patch.object(w, "_web_enabled", lambda runtime=None: True), patch.object(
        w, "_searxng_base", lambda runtime=None: ""
    ), patch.object(
        w, "_openserp_rows", side_effect=RuntimeError("down")
    ), patch.object(
        w, "_ddg_rows", lambda q, n, t: [{"title": "t", "url": "https://e.org", "snippet": ""}]
    ):
        assert w.search_public_web("anything") == [
            {"title": "t", "url": "https://e.org", "snippet": ""}
        ]


def test_web_tools_are_on_when_the_key_is_missing():
    with patch("remedy.interfaces.config.load_config", return_value={}):
        assert w._web_enabled() is True


def test_web_tools_stay_off_when_the_owner_said_so():
    with patch(
        "remedy.interfaces.config.load_config",
        return_value={"web_tools_enabled": False},
    ):
        assert w._web_enabled() is False


def test_private_search_instance_needs_an_explicit_override():
    """The SSRF guard is not opened by anything that can write config."""
    with patch.object(w, "_cfg", lambda key, runtime=None: None):
        with pytest.raises(ValueError, match="SEARCH_PRIVATE_HOST"):
            w._searxng_rows("http://127.0.0.1:8080", "q", 3, 5.0)


def test_private_search_instance_allowed_when_the_owner_says_so():
    calls: list[str] = []

    def _direct(url, *, timeout):
        calls.append(url)
        return b'{"results": [{"url": "https://e.org", "title": "T", "content": "S"}]}'

    with patch.object(w, "_cfg", lambda key, runtime=None: True), patch.object(
        w, "_direct_fetch", _direct
    ):
        rows = w._searxng_rows("http://127.0.0.1:8080", "q", 3, 5.0)
    assert rows == [{"title": "T", "url": "https://e.org", "snippet": "S"}]
    assert calls and "format=json" in calls[0]
