"""``remedy auth`` for every provider, not just xAI.

The storage layer has always handled all of them — ``secret_store`` is
DPAPI-sealed and keyed by provider — but the CLI refused anything that was not
xAI with "not implemented yet". A CLI-only owner (Linux, headless, no desktop
app) therefore had no way to store a provider key short of editing files.

xAI keeps its own credential file and device-code OAuth; everything else goes
through the shared secret store.
"""

from __future__ import annotations

import pytest

from remedy.interfaces import secret_store
from remedy.interfaces.cli import cmd_settings


class _Args:
    def __init__(self, **kw):
        self.provider = kw.pop("provider", "openai")
        self.auth_cmd = kw.pop("auth_cmd", "status")
        self.api_key = kw.pop("api_key", None)
        self.home = kw.pop("home", None)
        for k, v in kw.items():
            setattr(self, k, v)


def _run(tmp_path, **kw):
    args = _Args(home=str(tmp_path), **kw)
    cmd_settings._cmd_auth(args)


def test_a_key_can_be_stored_and_read_back(tmp_path):
    _run(tmp_path, provider="anthropic", auth_cmd="apikey", api_key="sk-ant-abc123")
    assert secret_store.get_provider_secret("anthropic", tmp_path) == "sk-ant-abc123"


@pytest.mark.parametrize(
    "provider", ["openai", "anthropic", "google", "deepseek", "groq", "mistral", "openrouter"]
)
def test_every_api_key_provider_is_reachable(tmp_path, provider):
    _run(tmp_path, provider=provider, auth_cmd="apikey", api_key=f"key-for-{provider}")
    assert secret_store.get_provider_secret(provider, tmp_path) == f"key-for-{provider}"


def test_logout_clears_just_that_provider(tmp_path):
    _run(tmp_path, provider="openai", auth_cmd="apikey", api_key="k1")
    _run(tmp_path, provider="groq", auth_cmd="apikey", api_key="k2")
    _run(tmp_path, provider="openai", auth_cmd="logout")

    assert secret_store.get_provider_secret("openai", tmp_path) is None
    assert secret_store.get_provider_secret("groq", tmp_path) == "k2"


def test_logout_all_clears_everything(tmp_path):
    _run(tmp_path, provider="openai", auth_cmd="apikey", api_key="k1")
    _run(tmp_path, provider="groq", auth_cmd="apikey", api_key="k2")
    _run(tmp_path, provider="all", auth_cmd="logout")

    assert secret_store.public_secret_status(tmp_path)["providers_with_keys"] == []


def test_logging_out_twice_is_harmless(tmp_path):
    _run(tmp_path, provider="openai", auth_cmd="logout")
    _run(tmp_path, provider="openai", auth_cmd="logout")


def test_status_never_prints_the_key(tmp_path, capsys):
    secret = "sk-super-secret-value-9876543210"
    _run(tmp_path, provider="openai", auth_cmd="apikey", api_key=secret)
    capsys.readouterr()
    _run(tmp_path, provider="all", auth_cmd="status")
    out = capsys.readouterr().out
    assert secret not in out, "the CLI printed the raw API key"
    assert secret_store.fingerprint_key(secret)[:8] in out.replace("\n", "")


def test_an_empty_key_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        _run(tmp_path, provider="openai", auth_cmd="apikey", api_key="   ")
    assert secret_store.get_provider_secret("openai", tmp_path) is None


def test_an_unknown_provider_says_what_is_known(tmp_path, capsys):
    with pytest.raises(SystemExit):
        _run(tmp_path, provider="nope", auth_cmd="status")
    out = capsys.readouterr().out
    assert "Unknown provider" in out
    assert "anthropic" in out


def test_a_local_provider_is_told_it_needs_no_key(tmp_path, capsys):
    _run(tmp_path, provider="ollama", auth_cmd="status")
    assert "needs no key" in capsys.readouterr().out


def test_a_local_provider_refuses_to_store_a_key(tmp_path):
    with pytest.raises(SystemExit):
        _run(tmp_path, provider="ollama", auth_cmd="apikey", api_key="pointless")
    assert secret_store.get_provider_secret("ollama", tmp_path) is None


def test_login_points_key_only_providers_at_apikey(tmp_path, capsys):
    with pytest.raises(SystemExit):
        _run(tmp_path, provider="anthropic", auth_cmd="login")
    out = capsys.readouterr().out
    assert "remedy auth apikey anthropic" in out.replace("\n", "")


def test_xai_still_goes_down_its_own_path(tmp_path, capsys):
    """xAI has device-code OAuth and its own credentials file; the new branch
    must not swallow it."""
    _run(tmp_path, provider="xai", auth_cmd="status")
    out = capsys.readouterr().out
    assert "xAI auth" in out
    assert "auth_method" in out
