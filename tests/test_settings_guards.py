"""The approval predicates in front of self-configuration.

Remedy can change her own settings when asked. Two of those changes can hand
something away, so both go through an approval gate, and these are the
functions that decide:

* Repointing ``llm_base_url`` at a host that is not the provider's own sends
  the stored API key there on the next request. A confused-deputy: the owner
  says "use my other endpoint", the model obliges, the key leaves.
* Widening a messenger — enabling a channel, setting ``allow_all``, or
  emptying an allowlist — opens Remedy to people who were not allowed before.

A predicate that answers False too readily is the whole vulnerability, so the
tests lean on the cases where it should say True.
"""

from __future__ import annotations

import pytest

from remedy.core.agent_settings_tools import (
    _allowlist_empty,
    _hosts_equivalent,
    _is_loopback_host_url,
    _llm_base_url_needs_approval,
    _messenger_widen,
    _truthy,
    _url_host,
)

# --- truthiness --------------------------------------------------------------
# Settings arrive as JSON from a model and as strings from a form.


@pytest.mark.parametrize(
    "value", [True, "1", "true", "TRUE", " yes ", "on", "enable", "enabled"]
)
def test_every_way_of_saying_yes(value):
    assert _truthy(value) is True


@pytest.mark.parametrize(
    "value", [False, "0", "false", "no", "off", "", "   ", None, "maybe", "2"]
)
def test_anything_else_is_not_yes(value):
    """An unrecognised word must not read as consent."""
    assert _truthy(value) is False


# --- host parsing ------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://api.openai.com/v1", "api.openai.com"),
        ("api.openai.com", "api.openai.com"),
        ("HTTPS://API.OpenAI.COM/v1", "api.openai.com"),
        ("https://api.openai.com./v1", "api.openai.com"),
        ("http://127.0.0.1:11434", "127.0.0.1"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_the_host_is_read_the_same_however_it_is_written(url, host):
    assert _url_host(url) == host


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:11434", "http://localhost:8080/v1", "http://[::1]:1234"],
)
def test_a_local_model_server_is_recognised_as_local(url):
    assert _is_loopback_host_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "https://evil.example/v1",
        # Looks local, is not: the hostname is what matters, not the text.
        "https://localhost.evil.example/v1",
    ],
)
def test_a_remote_host_is_not_local(url):
    assert _is_loopback_host_url(url) is False


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("https://api.openai.com/v1", "https://api.openai.com/v1"),
        ("https://api.openai.com/v1", "openai.com"),
        ("https://eu.api.openai.com/v1", "https://api.openai.com/v1"),
    ],
)
def test_the_same_host_family_is_equivalent(a, b):
    assert _hosts_equivalent(a, b) is True


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("https://api.openai.com/v1", "https://api.anthropic.com/v1"),
        # The classic near-miss: a suffix that is not a subdomain.
        ("https://notopenai.com/v1", "https://openai.com/v1"),
        ("", "https://api.openai.com/v1"),
        ("https://api.openai.com/v1", ""),
    ],
)
def test_a_different_host_is_not_equivalent(a, b):
    assert _hosts_equivalent(a, b) is False


# --- the base-URL gate -------------------------------------------------------


def test_a_foreign_host_needs_approval():
    """The whole point: this is where the stored key would be sent."""
    assert _llm_base_url_needs_approval(
        "https://evil.example/v1", {}, {"llm_provider": "openai"}
    ) is True


def test_a_lookalike_host_needs_approval():
    assert _llm_base_url_needs_approval(
        "https://api.openai.com.evil.example/v1", {}, {"llm_provider": "openai"}
    ) is True


def test_the_providers_own_endpoint_does_not():
    assert _llm_base_url_needs_approval(
        "https://api.openai.com/v1", {}, {"llm_provider": "openai"}
    ) is False


def test_switching_provider_and_endpoint_together_does_not():
    """`use anthropic` sets both; that is one coherent change, not a redirect."""
    assert _llm_base_url_needs_approval(
        "https://api.anthropic.com/v1",
        {"llm_provider": "anthropic"},
        {"llm_provider": "openai"},
    ) is False


def test_a_local_model_server_does_not():
    """Ollama and friends never hold a cloud key."""
    assert _llm_base_url_needs_approval(
        "http://127.0.0.1:11434/v1", {}, {"llm_provider": "ollama"}
    ) is False


def test_leaving_the_url_alone_does_not():
    assert _llm_base_url_needs_approval("", {}, {"llm_provider": "openai"}) is False


def test_setting_the_url_it_already_has_does_not():
    assert _llm_base_url_needs_approval(
        "https://api.openai.com/v1",
        {},
        {"llm_provider": "openai", "llm_base_url": "https://api.openai.com/v1/"},
    ) is False


def test_a_foreign_host_needs_approval_even_with_no_provider_set():
    assert _llm_base_url_needs_approval("https://evil.example/v1", {}, {}) is True


def test_pointing_one_providers_url_at_another_provider_needs_approval():
    """Saying `use anthropic` but supplying OpenAI's URL is not coherent."""
    assert _llm_base_url_needs_approval(
        "https://api.openai.com/v1",
        {"llm_provider": "anthropic"},
        {"llm_provider": "anthropic"},
    ) is True


# --- allowlist emptiness -----------------------------------------------------


@pytest.mark.parametrize("value", [[], (), set(), "", "   ", "[]", "none", "null", [" "]])
def test_these_all_mean_the_allowlist_is_gone(value):
    assert _allowlist_empty(value) is True


@pytest.mark.parametrize("value", [["123"], "123", ["", "456"], None])
def test_an_allowlist_with_anything_in_it_is_not_empty(value):
    """None means 'not being changed' — that is not the same as emptied."""
    assert _allowlist_empty(value) is False


# --- messenger widening ------------------------------------------------------


def test_enabling_a_new_channel_needs_approval():
    out = _messenger_widen({"enabled_channels": ["telegram"]}, {"enabled_channels": []})
    assert out is not None
    assert "telegram" in out[0]


def test_a_comma_separated_channel_list_is_understood():
    out = _messenger_widen(
        {"enabled_channels": "telegram, discord"}, {"enabled_channels": []}
    )
    assert out is not None
    assert "discord" in out[0] and "telegram" in out[0]


@pytest.mark.parametrize("channel", ["cli", "web", "api"])
def test_the_local_surfaces_are_not_a_widening(channel):
    """These are how the owner already talks to her."""
    assert _messenger_widen({"enabled_channels": [channel]}, {"enabled_channels": []}) is None


def test_a_channel_that_is_already_on_is_not_a_widening():
    assert (
        _messenger_widen(
            {"enabled_channels": ["telegram"]}, {"enabled_channels": ["telegram"]}
        )
        is None
    )


def test_turning_a_channel_off_is_not_a_widening():
    assert (
        _messenger_widen({"enabled_channels": []}, {"enabled_channels": ["telegram"]})
        is None
    )


def test_enabling_through_the_messengers_block_needs_approval():
    out = _messenger_widen(
        {"messengers": {"telegram": {"enabled": True}}}, {"enabled_channels": []}
    )
    assert out is not None
    assert "telegram" in out[0]


def test_setting_allow_all_needs_approval():
    """This is the one that lets a stranger talk to her."""
    out = _messenger_widen(
        {"messengers": {"telegram": {"allow_all": True}}},
        {"enabled_channels": ["telegram"], "telegram": {"allow_all": False}},
    )
    assert out is not None
    assert "allow_all" in out[0]


def test_allow_all_that_was_already_on_is_not_a_new_widening():
    assert (
        _messenger_widen(
            {"messengers": {"telegram": {"allow_all": True}}},
            {"enabled_channels": ["telegram"], "telegram": {"allow_all": True}},
        )
        is None
    )


@pytest.mark.parametrize("key", ["allow_chat_ids", "allow_ids", "allow_from"])
def test_emptying_an_allowlist_needs_approval(key):
    """Removing the list is the same as allow_all, spelled differently."""
    out = _messenger_widen(
        {"messengers": {"telegram": {key: []}}},
        {"enabled_channels": ["telegram"], "telegram": {key: ["123"]}},
    )
    assert out is not None
    assert key in out[0]


def test_adding_to_an_allowlist_is_not_a_widening_that_needs_this_gate():
    out = _messenger_widen(
        {"messengers": {"telegram": {"allow_ids": ["123", "456"]}}},
        {"enabled_channels": ["telegram"], "telegram": {"allow_ids": ["123"]}},
    )
    assert out is None


def test_an_allowlist_that_was_already_empty_is_not_newly_emptied():
    assert (
        _messenger_widen(
            {"messengers": {"telegram": {"allow_ids": []}}},
            {"enabled_channels": ["telegram"], "telegram": {"allow_ids": []}},
        )
        is None
    )


def test_an_unrelated_settings_change_is_not_a_widening():
    assert _messenger_widen({"agent_name": "Remedy"}, {}) is None


def test_a_malformed_messengers_block_does_not_crash_the_gate():
    """Garbage in must not become 'no approval needed'."""
    assert _messenger_widen({"messengers": "telegram"}, {}) is None
    assert _messenger_widen({"messengers": {"telegram": "on"}}, {}) is None
    assert _messenger_widen({"enabled_channels": 7}, {}) is None
