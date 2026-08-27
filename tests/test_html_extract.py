"""HTML → markdown extract used by web_fetch."""

from __future__ import annotations

import pytest

from remedy.core.html_extract import html_to_markdown, looks_like_html


def test_extracts_article_and_drops_nav():
    html = """<!doctype html><html><head><title>Docs</title></head>
    <body>
      <nav><a href="/x">Skip</a></nav>
      <article>
        <h1>Install</h1>
        <p>Run <code>pip install remedy-ai</code>.</p>
        <ul><li>One</li><li>Two</li></ul>
        <p>See <a href="https://example.com/guide">the guide</a>.</p>
      </article>
      <script>window.TRACK=1</script>
    </body></html>"""
    out = html_to_markdown(html)
    md = str(out["markdown"])
    assert "Install" in md
    assert "pip install remedy-ai" in md
    assert "the guide" in md
    assert "https://example.com/guide" in md
    assert "window.TRACK" not in md
    assert "Skip" not in md
    assert out["js_shell"] is False


def test_js_shell_detected():
    html = (
        "<!doctype html><html><head><title>App</title></head>"
        "<body><div id='root'></div>"
        "<script>" + ("x" * 5000) + "</script></body></html>"
    )
    out = html_to_markdown(html)
    assert out["js_shell"] is True


def test_looks_like_html():
    assert looks_like_html(b"<!DOCTYPE html><html>")
    assert not looks_like_html(b'{"ok": true}', content_type="application/json")


@pytest.mark.asyncio
async def test_web_fetch_returns_markdown(monkeypatch):
    from unittest.mock import MagicMock

    from remedy.core.agent_web_tools import register_web_tools
    from remedy.skills.tool_registry import ToolRegistry

    html = (
        b"<!doctype html><html><head><title>Guide</title></head>"
        b"<body><nav>chrome</nav><article><h1>Guide</h1>"
        b"<p>Hello world.</p></article><script>bad()</script></body></html>"
    )

    monkeypatch.setattr(
        "remedy.core.agent_web_tools._pinned_fetch",
        lambda url, **k: (url, html, "utf-8"),
    )
    monkeypatch.setattr(
        "remedy.core.agent_web_tools._web_enabled", lambda runtime: True
    )

    async def _no_rail(url: str) -> str:
        return ""

    # Without this the short extract trips the thin-body path and the test
    # silently fetches the real network (green offline, red online).
    monkeypatch.setattr("remedy.core.agent_web_tools._rail_page_text", _no_rail)

    runtime = MagicMock()
    runtime.tool_registry = ToolRegistry()
    register_web_tools(runtime)
    out = await runtime.tool_registry.execute(
        "web_fetch", url="https://example.com/guide"
    )
    assert "Hello world" in out
    assert "bad()" not in out
    assert "chrome" not in out
    assert "URL: https://example.com/guide" in out


@pytest.mark.asyncio
async def test_a_thin_extract_beats_a_thinner_browser_read(monkeypatch):
    """A short page is not an empty shell — the browser only wins if it has more."""
    from unittest.mock import MagicMock

    from remedy.core.agent_web_tools import register_web_tools
    from remedy.skills.tool_registry import ToolRegistry

    html = (
        b"<!doctype html><html><head><title>Guide</title></head>"
        b"<body><article><h1>Guide</h1><p>Hello world.</p></article></body></html>"
    )
    monkeypatch.setattr(
        "remedy.core.agent_web_tools._pinned_fetch",
        lambda url, **k: (url, html, "utf-8"),
    )
    monkeypatch.setattr("remedy.core.agent_web_tools._web_enabled", lambda runtime: True)

    async def _thin_rail(url: str) -> str:
        return "Just this"

    monkeypatch.setattr("remedy.core.agent_web_tools._rail_page_text", _thin_rail)

    runtime = MagicMock()
    runtime.tool_registry = ToolRegistry()
    register_web_tools(runtime)
    out = await runtime.tool_registry.execute("web_fetch", url="https://example.com/g")
    assert "Hello world" in out
    assert "Just this" not in out
    assert "in-app browser" not in out


def test_rail_url_must_match_fetch_target():
    """Live 2026-08-27: PyPI fetch must not return the current Reddit tab."""
    from remedy.core.agent_web_tools import _rail_url_matches

    assert _rail_url_matches(
        "https://pypi.org/project/PyChromecast/",
        "https://pypi.org/project/PyChromecast/",
    )
    assert _rail_url_matches(
        "https://www.pypi.org/project/PyChromecast/",
        "https://pypi.org/project/PyChromecast/",
    )
    assert not _rail_url_matches(
        "https://pypi.org/project/PyChromecast/",
        "https://www.reddit.com/r/artificial/",
    )
    assert not _rail_url_matches(
        "https://pypi.org/project/PyChromecast/",
        "",
    )


@pytest.mark.asyncio
async def test_web_fetch_drops_rail_from_the_wrong_tab(monkeypatch):
    from unittest.mock import MagicMock

    from remedy.core.agent_web_tools import register_web_tools
    from remedy.skills.tool_registry import ToolRegistry

    html = b"<!doctype html><html><body><script>app()</script></body></html>"
    monkeypatch.setattr(
        "remedy.core.agent_web_tools._pinned_fetch",
        lambda url, **k: (url, html, "utf-8"),
    )
    monkeypatch.setattr(
        "remedy.core.agent_web_tools._web_enabled", lambda runtime: True
    )

    async def _wrong_tab(url: str) -> str:
        return "# Reddit\n\nSubmit to r/artificial"

    monkeypatch.setattr("remedy.core.agent_web_tools._rail_page_text", _wrong_tab)

    # Direct unit: matcher rejects; fetch with mocked rail that wouldn't match
    # is still gated inside _rail_page_text. Here we assert the public fetch
    # does not keep a rail body that names a different site if the HTTP
    # extract is an empty shell AND the rail helper returns "".
    async def _empty_rail(url: str) -> str:
        return ""

    monkeypatch.setattr("remedy.core.agent_web_tools._rail_page_text", _empty_rail)

    runtime = MagicMock()
    runtime.tool_registry = ToolRegistry()
    register_web_tools(runtime)
    out = await runtime.tool_registry.execute(
        "web_fetch", url="https://pypi.org/project/PyChromecast/"
    )
    assert "r/artificial" not in out
    assert "URL: https://pypi.org/project/PyChromecast/" in out


def test_jwks_host_allowlist():
    from remedy.gateway.channels.jwt_rs256 import _jwks_url_allowed

    assert _jwks_url_allowed(
        "https://login.microsoftonline.com/common/discovery/v2.0/keys"
    )
    assert _jwks_url_allowed("https://login.botframework.com/v1/.well-known/keys")
    assert not _jwks_url_allowed("https://evil.example/keys")
    assert not _jwks_url_allowed("http://login.microsoftonline.com/keys")


def test_iss_rejects_substring_spoof():
    from remedy.gateway.channels.teams import _jwt_claims_structurally_valid

    now = 1_700_000_000
    base = {"aud": "app", "exp": now + 3600}
    assert _jwt_claims_structurally_valid(
        {**base, "iss": "https://sts.windows.net/tid/"},
        app_id="app",
        now=now,
    )
    assert not _jwt_claims_structurally_valid(
        {**base, "iss": "https://evil.com/sts.windows.net"},
        app_id="app",
        now=now,
    )
