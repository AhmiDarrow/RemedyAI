"""Settings that arrive as human words, and the normalisers that make them safe.

Two shared entry points feed this: the API PUT, and the agent's own
``update_settings`` tool. The tool is driven by a model reading what the owner
said, so the values are whatever a sentence turned into — "on", "full+", a bare
domain, `javascript:` — and each has to land on something the rest of the app
can trust.

The browser home URL is the sharp one. It is loaded into the in-app rail with
no further checking, so a `javascript:` or `file:` value there is a settings
field that executes.
"""

from __future__ import annotations

import pytest

from remedy.interfaces.settings_apply import (
    DEFAULT_BROWSER_HOME_URL,
    SETUP_ALIASES,
    _as_bool,
    normalize_browser_home_url,
    normalize_tool_process,
    resolve_setup_phrase,
)

# --- how much of her working is shown ----------------------------------------


@pytest.mark.parametrize("raw", ["off", "OFF", "", None, "nonsense", 0, False])
def test_showing_nothing_is_the_default(raw):
    assert normalize_tool_process(raw=raw) == "off"


@pytest.mark.parametrize("raw", ["medium", "MEDIUM", " med "])
def test_the_middle_setting(raw):
    assert normalize_tool_process(raw=raw) == "medium"


@pytest.mark.parametrize(
    "raw",
    ["full", "full+", "fullplus", "full_plus", "debug", "on", "true", "1", "yes", True],
)
def test_everything_that_means_show_me_everything(raw):
    assert normalize_tool_process(raw=raw) == "full"


def test_the_setting_is_read_from_config_when_not_passed():
    assert normalize_tool_process({"tool_process": "medium"}) == "medium"


def test_the_legacy_flag_still_works():
    """Configs written before the three-way setting existed say show_tool_calls."""
    assert normalize_tool_process({"show_tool_calls": True}) == "full"


def test_the_new_setting_wins_over_the_legacy_flag():
    cfg = {"tool_process": "off", "show_tool_calls": True}
    assert normalize_tool_process(cfg) == "off"


def test_a_legacy_flag_that_is_off_does_not_turn_it_on():
    assert normalize_tool_process({"show_tool_calls": False}) == "off"


# --- the browser home page ----------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html,<script>x</script>",
        "vbscript:msgbox(1)",
        "file:///C:/Users/me/.remedy/auth",
        "about:config",
    ],
)
def test_a_scheme_that_executes_or_reads_disk_is_refused(url):
    """This value is loaded into the rail unchecked; it must never be a scheme."""
    assert normalize_browser_home_url(url) == DEFAULT_BROWSER_HOME_URL


@pytest.mark.parametrize("url", ["", "   ", None])
def test_nothing_set_falls_back_to_the_default(url):
    assert normalize_browser_home_url(url) == DEFAULT_BROWSER_HOME_URL


def test_a_real_url_is_kept():
    assert normalize_browser_home_url("https://example.com/start") == (
        "https://example.com/start"
    )


def test_plain_http_is_allowed():
    """A local dashboard on http is a legitimate home page."""
    assert normalize_browser_home_url("http://192.168.0.10:8080") == (
        "http://192.168.0.10:8080"
    )


def test_a_bare_domain_gets_https():
    """People type `example.com`, not `https://example.com`."""
    assert normalize_browser_home_url("example.com") == "https://example.com"


def test_a_bare_domain_with_a_path_gets_https():
    assert normalize_browser_home_url("example.com/dash") == "https://example.com/dash"


def test_surrounding_whitespace_is_ignored():
    assert normalize_browser_home_url("  example.com  ") == "https://example.com"


def test_the_default_is_itself_a_safe_url():
    assert DEFAULT_BROWSER_HOME_URL.startswith("https://")


# --- booleans from a sentence -------------------------------------------------


@pytest.mark.parametrize(
    "value", [True, "1", "true", "TRUE", " yes ", "on", "enable", "enabled"]
)
def test_every_way_of_saying_yes(value):
    assert _as_bool(value) is True


@pytest.mark.parametrize(
    "value", [False, "0", "false", "no", "off", "disable", "disabled", ""]
)
def test_every_way_of_saying_no(value):
    assert _as_bool(value) is False


def test_an_unrecognised_word_is_not_silently_false():
    """Falling through to truthiness is deliberate — a value was given."""
    assert _as_bool("maybe") is True


# --- "set up X" ---------------------------------------------------------------


def test_a_known_phrase_becomes_a_patch():
    assert resolve_setup_phrase("web tools") == {"web_tools_enabled": True}


def test_a_phrase_is_matched_regardless_of_spacing_and_case():
    assert resolve_setup_phrase("  WEB   TOOLS  ") == {"web_tools_enabled": True}


def test_a_longer_sentence_containing_the_phrase_matches():
    """The model passes through what the owner said, not a tidy key."""
    assert resolve_setup_phrase("please turn on web tools for me") == (
        {"web_tools_enabled": True}
    )


def test_turning_something_off_is_its_own_phrase():
    assert resolve_setup_phrase("disable web") == {"web_tools_enabled": False}


def test_an_unknown_phrase_resolves_to_nothing():
    """Guessing a settings change from an unrecognised sentence is worse."""
    assert resolve_setup_phrase("make it faster somehow") is None


@pytest.mark.parametrize("phrase", ["", "   ", None])
def test_an_empty_phrase_resolves_to_nothing(phrase):
    assert resolve_setup_phrase(phrase) is None


def test_the_returned_patch_is_a_copy():
    """A caller mutating the result must not edit the alias table itself."""
    patch = resolve_setup_phrase("web tools")
    patch["web_tools_enabled"] = "tampered"
    assert SETUP_ALIASES["web tools"] == {"web_tools_enabled": True}


@pytest.mark.parametrize("phrase", sorted(SETUP_ALIASES))
def test_every_advertised_phrase_resolves(phrase):
    assert resolve_setup_phrase(phrase) is not None


@pytest.mark.parametrize("phrase", sorted(SETUP_ALIASES))
def test_no_advertised_phrase_yields_an_empty_patch(phrase):
    """An alias that maps to nothing silently does nothing when used."""
    assert resolve_setup_phrase(phrase)
