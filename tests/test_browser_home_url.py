"""In-app Browser homepage setting (default Remedy GitHub)."""

from remedy.interfaces.routes.settings import (
    DEFAULT_BROWSER_HOME_URL,
    _normalize_browser_home_url,
)


def test_default_is_remedy_github():
    assert DEFAULT_BROWSER_HOME_URL == "https://github.com/AhmiDarrow/RemedyAI"
    assert _normalize_browser_home_url(None) == DEFAULT_BROWSER_HOME_URL
    assert _normalize_browser_home_url("") == DEFAULT_BROWSER_HOME_URL
    assert _normalize_browser_home_url("   ") == DEFAULT_BROWSER_HOME_URL


def test_normalizes_bare_host():
    assert _normalize_browser_home_url("example.com/docs") == "https://example.com/docs"


def test_preserves_https():
    assert (
        _normalize_browser_home_url("https://github.com/AhmiDarrow/RemedyAI")
        == "https://github.com/AhmiDarrow/RemedyAI"
    )


def test_blocks_dangerous_schemes():
    assert _normalize_browser_home_url("javascript:alert(1)") == DEFAULT_BROWSER_HOME_URL
    assert _normalize_browser_home_url("file:///C:/Windows") == DEFAULT_BROWSER_HOME_URL
    assert _normalize_browser_home_url("data:text/html,hi") == DEFAULT_BROWSER_HOME_URL
