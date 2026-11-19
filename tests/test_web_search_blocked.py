"""DuckDuckGo's human check reads as "search is blocked", never as "no results".

Before: the challenge page parsed to zero rows, so the model retried search
after search and then fetched Google / Yelp directly (refused by robots) —
a dozen wasted tool calls for one question.
"""

from __future__ import annotations

import pytest

from remedy.core import web_helpers as wh

CHALLENGE = (
    '<html><body><div class="anomaly-modal__title">Unfortunately, bots use DuckDuckGo too.</div>'
    "<p>Please complete the following challenge to confirm this search was made by a human.</p></body></html>"
)
SERP = (
    '<div class="result"><a class="result__a" href="https://example.com/a">Example A</a>'
    '<a class="result__snippet">snippet</a></div>'
)


@pytest.fixture(autouse=True)
def _reset():
    wh._ddg_reset_block()
    yield
    wh._ddg_reset_block()


@pytest.mark.parametrize(
    "html",
    [
        CHALLENGE,
        '<div class="anomaly-modal">x</div>',  # marker only
        "<p>Unfortunately, BOTS USE DUCKDUCKGO TOO.</p>",  # text only, any case
        "<p>confirm this search was made by a human</p>",
    ],
)
def test_challenge_family_is_detected(html: str) -> None:
    assert wh.ddg_challenge_page(html)


@pytest.mark.parametrize(
    "html",
    [
        SERP,
        "",
        "<html><body>No results found for your query.</body></html>",
        # A real result page that merely mentions the word "anomaly".
        SERP + "<p>anomaly detection in time series</p>",
    ],
)
def test_ordinary_pages_are_not_challenges(html: str) -> None:
    assert not wh.ddg_challenge_page(html)


def test_challenge_raises_and_cools_down_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_fetch(url, **_kw):
        calls.append(url)
        return url, CHALLENGE.encode(), "utf-8"

    monkeypatch.setattr(wh, "polite_fetch", fake_fetch)
    with pytest.raises(wh.SearchBlockedError) as first:
        wh._ddg_rows("breakfast denver", 5, 5.0)
    assert "never solves" in str(first.value)
    with pytest.raises(wh.SearchBlockedError):
        wh._ddg_rows("another query", 5, 5.0)
    assert len(calls) == 1, "inside the cooldown no request may leave the PC"


def test_cooldown_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    wh._ddg_mark_blocked(now=1000.0)
    assert wh._ddg_blocked_now(now=1000.0 + wh.DDG_BLOCK_COOLDOWN_S - 1)
    assert not wh._ddg_blocked_now(now=1000.0 + wh.DDG_BLOCK_COOLDOWN_S + 1)


def test_results_still_parse_when_not_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wh, "polite_fetch", lambda url, **_kw: (url, SERP.encode(), "utf-8"))
    rows = wh._ddg_rows("q", 5, 5.0)
    assert rows and rows[0]["url"] == "https://example.com/a"


def test_worker_web_search_reports_blocked_in_words(monkeypatch: pytest.MonkeyPatch) -> None:
    from remedy.runtime import rmdy_tool_worker as worker

    monkeypatch.setattr(wh, "web_tools_enabled", lambda _rt=None: True)

    def blocked(*_a, **_kw):
        raise wh.SearchBlockedError(wh.SEARCH_BLOCKED_NOTE)

    monkeypatch.setattr(wh, "run_search", blocked)
    out = worker._web_search({"query": "breakfast"})
    assert out["blocked"] is True and out["results"] == []
    assert "never solves" in out["note"] and "Browser" in out["note"]


def test_search_public_web_stays_quiet_when_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wh, "_web_enabled", lambda _rt=None: True)

    def blocked(*_a, **_kw):
        raise wh.SearchBlockedError("x")

    monkeypatch.setattr(wh, "run_search", blocked)
    assert wh.search_public_web("anything") == []


def _serp_json(rows):
    import json

    return json.dumps({"results": rows}).encode()


def test_local_engine_order_and_fallthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    """DuckDuckGo first, Bing when it has nothing; Ecosia is gone."""
    from remedy.runtime import web_search_host as host

    assert wh.OPENSERP_ENGINES == ("duckduckgo", "bing")
    asked: list[str] = []

    def fake_direct(url, **_kw):
        engine = url.split("/")[3]
        asked.append(engine)
        if engine == "duckduckgo":
            return _serp_json([])
        return _serp_json([{"type": "organic", "url": "https://example.com/b", "title": "B", "snippet": "s"}])

    monkeypatch.setattr(host, "is_healthy", lambda **_kw: True)
    monkeypatch.setattr(wh, "_direct_fetch", fake_direct)
    rows = wh._openserp_rows("q", 3, 5.0)
    assert asked == ["duckduckgo", "bing"]
    assert rows[0]["url"] == "https://example.com/b"


def test_local_engine_is_revived_before_falling_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Installed but stopped: start it and use it, not the challenged fallback."""
    from remedy.runtime import web_search_host as host

    state = {"up": False, "started": 0}
    monkeypatch.setattr(host, "_skip_network", lambda: False)
    monkeypatch.setattr(host, "is_healthy", lambda **_kw: state["up"])
    monkeypatch.setattr(host, "find_binary", lambda _h=None: "openserp.exe")

    def fake_start(_home=None, wait_s=0.0):
        state["started"] += 1
        state["up"] = True
        return {"ok": True}

    monkeypatch.setattr(host, "start", fake_start)
    monkeypatch.setattr(
        wh,
        "_direct_fetch",
        lambda url, **_kw: _serp_json([{"url": "https://example.com/x", "title": "X"}]),
    )
    rows = wh._openserp_rows("q", 3, 5.0)
    assert state["started"] == 1 and rows[0]["url"] == "https://example.com/x"


def test_missing_local_engine_downloads_in_background_and_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from remedy.runtime import web_search_host as host

    scheduled: list[object] = []
    monkeypatch.setattr(host, "_skip_network", lambda: False)
    monkeypatch.setattr(host, "is_healthy", lambda **_kw: False)
    monkeypatch.setattr(host, "find_binary", lambda _h=None: None)
    monkeypatch.setattr(host, "schedule_ensure", lambda home=None, **_kw: scheduled.append(home))
    with pytest.raises(RuntimeError):
        wh._openserp_rows("q", 3, 5.0)
    assert len(scheduled) == 1


def test_schedule_ensure_retries_after_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    from remedy.runtime import web_search_host as host

    started: list[int] = []
    monkeypatch.setattr(host, "_skip_network", lambda: False)
    monkeypatch.setattr(host, "ensure_web_search_host", lambda *_a, **_kw: started.append(1) or {"ok": False})
    monkeypatch.setattr(host, "_ensure_started", False)
    monkeypatch.setattr(host, "_ensure_last", 0.0)

    class NowThread:
        def __init__(self, target, **_kw):
            self._t = target

        def start(self):
            self._t()

    monkeypatch.setattr(host.threading, "Thread", NowThread)
    clock = {"t": 1000.0}
    monkeypatch.setattr(host.time, "monotonic", lambda: clock["t"])
    host.schedule_ensure("h")
    host.schedule_ensure("h")  # inside the backoff: no second attempt
    clock["t"] += host.ENSURE_RETRY_S + 1
    host.schedule_ensure("h")  # a dead or failed host is tried again
    assert len(started) == 2


def test_tests_never_start_a_real_search_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under pytest the revive path must not spawn or download anything."""
    from remedy.runtime import web_search_host as host

    touched: list[str] = []
    monkeypatch.setattr(host, "is_healthy", lambda **_kw: False)
    monkeypatch.setattr(host, "find_binary", lambda _h=None: "openserp.exe")
    monkeypatch.setattr(host, "start", lambda *_a, **_kw: touched.append("start") or {"ok": True})
    monkeypatch.setattr(host, "schedule_ensure", lambda *_a, **_kw: touched.append("ensure"))
    with pytest.raises(RuntimeError):
        wh._openserp_rows("q", 3, 5.0)
    assert touched == []
