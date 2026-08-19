"""``remedy auth`` / ``config`` / ``settings`` / ``computer`` — the CLI settings surface.

These four commands are the only way a CLI-only owner changes anything: where the
home directory lives, which provider key is stored, what the agent's settings are,
and whether the computer host is running. If they are wrong the damage is quiet —
a raw API key echoed to a terminal, a ``--home`` typo that mkdirs into a system
directory, a value like ``off`` stored as the string "off" instead of ``False``,
or a malformed ``KEY=VALUE`` accepted and written as garbage.

Nothing here starts a real computer host, opens a real browser, or touches the
owner's ``~/.remedy``: every home is a tmp_path and every host/bridge/browser
entry point is replaced with a fake.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from remedy.interfaces import secret_store, xai_auth
from remedy.interfaces.cli import cmd_settings


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _args(home, **kw) -> SimpleNamespace:
    """Build an argparse-like namespace. ``home`` is mandatory on purpose —
    a default of None resolves to the owner's real ~/.remedy."""
    return SimpleNamespace(home=str(home), **kw)


def _out(capsys) -> str:
    return capsys.readouterr().out


def _squash(text: str) -> str:
    """Drop every space/newline so rich's column wrapping cannot break a match."""
    return "".join(text.split())


@pytest.fixture(autouse=True)
def _wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    # rich re-reads COLUMNS on every print; a wide console keeps tables unwrapped.
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("LINES", "50")


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture()
def isolated_home(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tmp home that REMEDY_HOME also points at, so config reads/writes land there."""
    monkeypatch.setenv("REMEDY_HOME", str(home))
    from remedy.interfaces import api_support

    api_support.invalidate_config_cache()
    return home


@pytest.fixture()
def no_xai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("REMEDY_XAI_API_KEY", raising=False)


# --------------------------------------------------------------------------
# catalog helpers
# --------------------------------------------------------------------------
def test_the_returned_provider_catalog_is_a_copy_callers_cannot_poison() -> None:
    known = cmd_settings._known_providers()
    known.pop("openai", None)
    known["bogus"] = {}
    assert "openai" in cmd_settings._known_providers()
    assert "bogus" not in cmd_settings._known_providers()


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("openai", ["api_key"]),
        ("anthropic", ["api_key"]),
        ("ollama", ["none"]),
        ("demo", ["none"]),
        ("xai", ["oauth", "api_key"]),
        ("does-not-exist", []),
    ],
)
def test_auth_methods_reports_what_each_provider_accepts(provider, expected) -> None:
    assert cmd_settings._auth_methods(provider) == expected


# --------------------------------------------------------------------------
# _print_key_status
# --------------------------------------------------------------------------
def test_an_empty_store_says_so_and_still_names_the_store_path(home, capsys) -> None:
    cmd_settings._print_key_status(home)
    out = _out(capsys)
    assert "No provider API keys stored yet." in out
    assert "Stored in" in out


def test_a_stored_key_is_shown_as_a_fingerprint_never_as_its_value(home, capsys) -> None:
    secret = "sk-live-do-not-print-me-1234567890"
    secret_store.set_provider_secret("openai", secret, home)
    cmd_settings._print_key_status(home, "openai")
    out = _out(capsys)
    assert secret not in out
    assert secret_store.fingerprint_key(secret) in _squash(out)


def test_a_provider_with_no_stored_key_is_listed_as_no(home, capsys) -> None:
    cmd_settings._print_key_status(home, "anthropic")
    out = _squash(_out(capsys))
    assert "anthropic" in out
    assert "no" in out


def test_a_live_env_var_is_reported_as_a_fallback(home, monkeypatch, capsys) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    monkeypatch.delenv("REMEDY_LLM_API_KEY", raising=False)
    secret_store.set_provider_secret("openai", "stored", home)
    cmd_settings._print_key_status(home, "openai")
    assert "OPENAI_API_KEY" in _squash(_out(capsys))


# --------------------------------------------------------------------------
# auth — non-xAI providers
# --------------------------------------------------------------------------
def test_all_only_answers_status_and_logout(home, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cmd_settings._cmd_auth(
            _args(home, provider="all", auth_cmd="apikey", api_key="k")
        )
    assert exc.value.code == 2
    assert "Name a provider" in _out(capsys)


def test_status_for_all_lists_every_stored_provider(home, capsys) -> None:
    secret_store.set_provider_secret("openai", "k1", home)
    secret_store.set_provider_secret("groq", "k2", home)
    cmd_settings._cmd_auth(_args(home, provider="all", auth_cmd="status"))
    out = _squash(_out(capsys))
    assert "openai" in out
    assert "groq" in out


def test_the_provider_name_is_lowercased_and_stripped(home) -> None:
    cmd_settings._cmd_auth(
        _args(home, provider="  OpenAI  ", auth_cmd="apikey", api_key="k1")
    )
    assert secret_store.get_provider_secret("openai", home) == "k1"


def test_a_missing_key_is_prompted_for_instead_of_being_stored_empty(
    home, monkeypatch
) -> None:
    from rich.prompt import Prompt

    asked: list[str] = []

    def fake_ask(prompt, **kw):
        asked.append(prompt)
        assert kw.get("password") is True, "an API key prompt must not echo"
        return "typed-key"

    monkeypatch.setattr(Prompt, "ask", staticmethod(fake_ask))
    cmd_settings._cmd_auth(_args(home, provider="openai", auth_cmd="apikey", api_key=None))
    assert asked and "OpenAI" in asked[0]
    assert secret_store.get_provider_secret("openai", home) == "typed-key"


def test_an_unknown_auth_subcommand_prints_usage_and_exits(home, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cmd_settings._cmd_auth(_args(home, provider="openai", auth_cmd="frobnicate"))
    assert exc.value.code == 2
    assert "Usage: remedy auth" in _out(capsys)


def test_a_rejected_key_is_reported_not_raised(home, monkeypatch, capsys) -> None:
    def boom(*a, **kw):
        raise ValueError("store is sealed")

    monkeypatch.setattr(secret_store, "set_provider_secret", boom)
    with pytest.raises(SystemExit) as exc:
        cmd_settings._cmd_auth(
            _args(home, provider="openai", auth_cmd="apikey", api_key="k")
        )
    assert exc.value.code == 1
    assert "store is sealed" in _out(capsys)


def test_status_for_a_keyless_provider_does_not_touch_the_store(home, capsys) -> None:
    cmd_settings._cmd_auth(_args(home, provider="demo", auth_cmd="status"))
    assert "needs no key" in _out(capsys)


# --------------------------------------------------------------------------
# auth — xAI
# --------------------------------------------------------------------------
def test_xai_status_prints_the_public_fields_only(home, no_xai_env, capsys) -> None:
    xai_auth.save_api_key("xai-secret-value-abc", home=home)
    cmd_settings._cmd_auth(_args(home, provider="xai", auth_cmd="status"))
    out = _out(capsys)
    assert "xAI auth" in out
    assert "has_api_key" in out
    assert "xai-secret-value-abc" not in out


def test_xai_logout_removes_the_credentials_file(home, no_xai_env) -> None:
    xai_auth.save_api_key("xai-key", home=home)
    assert xai_auth.load_credentials(home=home).api_key == "xai-key"
    cmd_settings._cmd_auth(_args(home, provider="xai", auth_cmd="logout"))
    assert xai_auth.load_credentials(home=home).api_key in (None, "")


def test_xai_apikey_saves_and_reports_connected(home, no_xai_env, capsys) -> None:
    cmd_settings._cmd_auth(
        _args(home, provider="xai", auth_cmd="apikey", api_key="xai-key")
    )
    assert "Saved xAI API key" in _out(capsys)
    assert xai_auth.load_credentials(home=home).api_key == "xai-key"


def test_a_blank_xai_key_is_refused(home, no_xai_env, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cmd_settings._cmd_auth(
            _args(home, provider="xai", auth_cmd="apikey", api_key="   ")
        )
    assert exc.value.code == 1
    assert "empty" in _out(capsys).lower()


def test_a_failed_oauth_start_is_reported_not_raised(home, monkeypatch, capsys) -> None:
    def boom(**kw):
        raise RuntimeError("no network")

    monkeypatch.setattr(xai_auth, "start_device_login", boom)
    with pytest.raises(SystemExit) as exc:
        cmd_settings._cmd_auth(_args(home, provider="xai", auth_cmd="login"))
    assert exc.value.code == 1
    assert "Failed to start OAuth" in _out(capsys)


@pytest.fixture()
def fake_device_login(monkeypatch: pytest.MonkeyPatch):
    """Stub the whole device-code flow: no HTTP, no real browser, no real waiting.

    The poll loop is ``while time.time() < deadline: time.sleep(interval)``, so a
    no-op sleep would spin for the full 900s window. Sleeping advances a fake
    clock instead, which makes the deadline arrive immediately.
    """
    import time
    import webbrowser

    opened: list[str] = []
    clock = {"now": 1_700_000_000.0}
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **kw: opened.append(url))
    monkeypatch.setattr(time, "time", lambda: clock["now"])
    monkeypatch.setattr(
        time, "sleep", lambda seconds=1.0: clock.__setitem__("now", clock["now"] + max(seconds, 1))
    )

    def _install(session_status: str, *, expires_in: int = 900, error: str | None = None):
        monkeypatch.setattr(
            xai_auth,
            "start_device_login",
            lambda **kw: {
                "verification_uri_complete": "https://x.ai/device?code=ABCD",
                "user_code": "ABCD",
                "session_id": "sess-1",
                "expires_in": expires_in,
                "interval": 5,
            },
        )
        monkeypatch.setattr(
            xai_auth,
            "login_status",
            lambda **kw: {"session": {"status": session_status, "error": error}},
        )
        return opened

    return _install


def test_a_connected_session_ends_the_login_wait(home, fake_device_login, capsys) -> None:
    opened = fake_device_login("connected")
    cmd_settings._cmd_auth(_args(home, provider="xai", auth_cmd="login"))
    out = _out(capsys)
    assert "Connected via xAI OAuth" in out
    assert opened == ["https://x.ai/device?code=ABCD"]


def test_a_rejected_session_fails_the_login(home, fake_device_login, capsys) -> None:
    fake_device_login("error", error="user denied")
    with pytest.raises(SystemExit) as exc:
        cmd_settings._cmd_auth(_args(home, provider="xai", auth_cmd="login"))
    assert exc.value.code == 1
    assert "user denied" in _out(capsys)


@pytest.mark.parametrize("expires_in", [12, 0])
def test_an_expired_device_code_times_out_instead_of_waiting_forever(
    home, fake_device_login, expires_in, capsys
) -> None:
    # A session that never leaves "pending" must stop at the advertised deadline.
    # expires_in=0 is falsy, so it falls back to the built-in 900s window.
    fake_device_login("pending", expires_in=expires_in)
    with pytest.raises(SystemExit) as exc:
        cmd_settings._cmd_auth(_args(home, provider="xai", auth_cmd="login"))
    assert exc.value.code == 1
    assert "timed out" in _out(capsys)


def test_an_unknown_xai_subcommand_prints_usage_without_exiting(
    home, no_xai_env, capsys
) -> None:
    # Documents today's asymmetry: the non-xAI path exits 2 here, xAI returns 0.
    cmd_settings._cmd_auth(_args(home, provider="xai", auth_cmd="frobnicate"))
    assert "Usage: remedy auth login|logout|status|apikey xai" in _out(capsys)


# --------------------------------------------------------------------------
# --home refusals
# --------------------------------------------------------------------------
def test_auth_refuses_a_home_that_is_a_file(tmp_path, capsys) -> None:
    f = tmp_path / "not-a-dir.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cmd_settings._cmd_auth(_args(f, provider="openai", auth_cmd="status"))
    assert exc.value.code == 2
    assert "--home" in _out(capsys)


def test_settings_refuses_a_home_that_is_a_file(tmp_path, capsys) -> None:
    f = tmp_path / "not-a-dir.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cmd_settings._cmd_settings(_args(f, settings_cmd="show"))
    assert exc.value.code == 2
    assert "--home" in _out(capsys)


def test_computer_refuses_a_home_that_is_a_file(tmp_path, capsys) -> None:
    f = tmp_path / "not-a-dir.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cmd_settings._cmd_computer(_args(f, computer_cmd="status"))
    assert exc.value.code == 2
    assert "--home" in _out(capsys)


def test_config_refuses_a_home_that_is_a_file(tmp_path, capsys) -> None:
    f = tmp_path / "not-a-dir.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        asyncio.run(cmd_settings._cmd_config(_args(f, config_cmd="show")))
    assert exc.value.code == 2
    assert "--home" in _out(capsys)


# --------------------------------------------------------------------------
# _parse_setting_value
# --------------------------------------------------------------------------
@pytest.mark.parametrize("raw", ["true", "TRUE", " yes ", "On"])
def test_truthy_words_become_real_booleans(raw) -> None:
    assert cmd_settings._parse_setting_value(raw) is True


@pytest.mark.parametrize("raw", ["false", "FALSE", " no ", "Off"])
def test_falsy_words_become_real_booleans(raw) -> None:
    assert cmd_settings._parse_setting_value(raw) is False


@pytest.mark.parametrize("raw", ["null", "None", " NONE "])
def test_null_words_become_none(raw) -> None:
    assert cmd_settings._parse_setting_value(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("42", 42),
        ("-7", -7),
        (" 13 ", 13),
        ("0", 0),
        ("3.5", 3.5),
        ("-0.25", -0.25),
    ],
)
def test_numbers_are_parsed_as_numbers(raw, expected) -> None:
    assert cmd_settings._parse_setting_value(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ("[1, 2]", [1, 2]),
        ("{}", {}),
        ("[]", []),
    ],
)
def test_json_shaped_values_are_decoded(raw, expected) -> None:
    assert cmd_settings._parse_setting_value(raw) == expected


@pytest.mark.parametrize("raw", ["{not json}", "[1, 2", "{'a': 1}"])
def test_broken_json_stays_a_plain_string(raw) -> None:
    assert cmd_settings._parse_setting_value(raw) == raw.strip()


@pytest.mark.parametrize("raw", ["high", "C:\\Users\\me", "grok-4.3", ""])
def test_anything_else_is_left_as_a_string(raw) -> None:
    assert cmd_settings._parse_setting_value(raw) == raw


def test_a_leading_zero_number_still_loses_its_zeros_via_the_float_fallback() -> None:
    """Documents current behaviour, not desired behaviour.

    The int branch deliberately refuses "007" so identifier-shaped values keep
    their zeros, but the float branch right below accepts the same string, so
    what gets stored is 7.0 and not the string "007".
    """
    assert cmd_settings._parse_setting_value("007") == 7.0
    assert isinstance(cmd_settings._parse_setting_value("007"), float)


# --------------------------------------------------------------------------
# settings show / keys / get
# --------------------------------------------------------------------------
@pytest.fixture()
def fake_snapshot(monkeypatch: pytest.MonkeyPatch) -> dict:
    from remedy.interfaces import settings_apply

    snap = {"thinking_level": "high", "llm_provider": "openai", "tool_process": "full"}
    monkeypatch.setattr(
        settings_apply, "public_settings_snapshot", lambda *a, **kw: dict(snap)
    )
    return snap


@pytest.mark.parametrize("sub", [None, "show"])
def test_show_prints_the_redacted_snapshot(isolated_home, fake_snapshot, sub, capsys) -> None:
    cmd_settings._cmd_settings(_args(isolated_home, settings_cmd=sub))
    out = _out(capsys)
    assert "Remedy settings" in out
    assert "thinking_level" in out


def test_keys_lists_every_settable_key(isolated_home, capsys) -> None:
    from remedy.interfaces.settings_apply import SETTABLE_KEYS

    cmd_settings._cmd_settings(_args(isolated_home, settings_cmd="keys"))
    out = _squash(_out(capsys))
    for key in ("thinking_level", "approval_mode", "privacy_mode"):
        assert key in SETTABLE_KEYS
        assert key in out


def test_get_prints_a_single_public_key(isolated_home, fake_snapshot, capsys) -> None:
    cmd_settings._cmd_settings(
        _args(isolated_home, settings_cmd="get", key="thinking_level")
    )
    out = _out(capsys)
    assert "thinking_level" in out
    assert "llm_provider" not in out


def test_get_of_a_settable_but_private_key_explains_instead_of_leaking(
    isolated_home, fake_snapshot, capsys
) -> None:
    cmd_settings._cmd_settings(_args(isolated_home, settings_cmd="get", key="llm_api_key"))
    out = _squash(_out(capsys))
    assert "settablebutnotinpublicsnapshot" in out


@pytest.mark.parametrize("key", ["nonsense_key", "", None, "   "])
def test_get_of_an_unknown_key_fails_loudly(isolated_home, fake_snapshot, key, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cmd_settings._cmd_settings(_args(isolated_home, settings_cmd="get", key=key))
    assert exc.value.code == 1
    assert "Unknown key" in _out(capsys)


def test_an_unknown_settings_subcommand_prints_usage(isolated_home, capsys) -> None:
    cmd_settings._cmd_settings(_args(isolated_home, settings_cmd="frobnicate"))
    assert "Usage: remedy settings show|get|set|keys" in _out(capsys)


# --------------------------------------------------------------------------
# settings set
# --------------------------------------------------------------------------
@pytest.fixture()
def captured_apply(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Replace the shared apply path; record the patch it was handed."""
    from remedy.interfaces import settings_apply

    seen: list[dict] = []

    async def fake_apply(patch, **kw):
        seen.append(dict(patch))
        return {
            "status": "saved",
            "message": "Applied settings: thinking_level",
            "changes": list(patch),
            **patch,
        }

    monkeypatch.setattr(settings_apply, "apply_settings_update", fake_apply)
    return seen


def _set(home, pairs=None, json_patch=None) -> SimpleNamespace:
    return _args(home, settings_cmd="set", pairs=pairs, json_patch=json_patch)


def test_a_set_with_nothing_to_apply_is_refused(isolated_home, captured_apply, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cmd_settings._cmd_settings(_set(isolated_home, pairs=[]))
    assert exc.value.code == 1
    assert "No settings to apply" in _out(capsys)
    assert captured_apply == []


def test_invalid_json_is_reported_before_anything_is_applied(
    isolated_home, captured_apply, capsys
) -> None:
    with pytest.raises(SystemExit) as exc:
        cmd_settings._cmd_settings(_set(isolated_home, json_patch="{oops"))
    assert exc.value.code == 1
    assert "Invalid --json" in _out(capsys)
    assert captured_apply == []


@pytest.mark.parametrize("raw", ["[1, 2]", '"a string"', "42", "null"])
def test_a_json_patch_that_is_not_an_object_is_refused(
    isolated_home, captured_apply, raw, capsys
) -> None:
    with pytest.raises(SystemExit) as exc:
        cmd_settings._cmd_settings(_set(isolated_home, json_patch=raw))
    assert exc.value.code == 1
    assert "must be an object" in _out(capsys)
    assert captured_apply == []


@pytest.mark.parametrize("pair", ["thinking_level", "justakey", " "])
def test_a_pair_without_an_equals_sign_is_refused(
    isolated_home, captured_apply, pair, capsys
) -> None:
    with pytest.raises(SystemExit) as exc:
        cmd_settings._cmd_settings(_set(isolated_home, pairs=[pair]))
    assert exc.value.code == 1
    assert "Expected KEY=VALUE" in _out(capsys)
    assert captured_apply == []


@pytest.mark.parametrize("pair", ["=value", "   =value"])
def test_a_pair_with_an_empty_key_is_refused(
    isolated_home, captured_apply, pair, capsys
) -> None:
    with pytest.raises(SystemExit) as exc:
        cmd_settings._cmd_settings(_set(isolated_home, pairs=[pair]))
    assert exc.value.code == 1
    assert "Empty key" in _out(capsys)
    assert captured_apply == []


def test_only_the_first_equals_sign_splits_a_pair(isolated_home, captured_apply) -> None:
    cmd_settings._cmd_settings(
        _set(isolated_home, pairs=["sleev_gateway_url=http://127.0.0.1:17321/x=y"])
    )
    assert captured_apply == [{"sleev_gateway_url": "http://127.0.0.1:17321/x=y"}]


def test_pairs_are_type_parsed_and_override_the_json_patch(
    isolated_home, captured_apply
) -> None:
    cmd_settings._cmd_settings(
        _set(
            isolated_home,
            pairs=["privacy_mode=on", "retention_log_days=30"],
            json_patch=json.dumps({"privacy_mode": False, "rmb_enabled": True}),
        )
    )
    assert captured_apply == [
        {"privacy_mode": True, "rmb_enabled": True, "retention_log_days": 30}
    ]


def test_a_rejected_patch_is_reported_not_raised(isolated_home, monkeypatch, capsys) -> None:
    from remedy.interfaces import settings_apply

    async def reject(patch, **kw):
        raise ValueError("no recognized settings keys in patch")

    monkeypatch.setattr(settings_apply, "apply_settings_update", reject)
    with pytest.raises(SystemExit) as exc:
        cmd_settings._cmd_settings(_set(isolated_home, pairs=["nonsense=1"]))
    assert exc.value.code == 1
    assert "Settings rejected" in _out(capsys)


def test_a_write_failure_is_reported_not_raised(isolated_home, monkeypatch, capsys) -> None:
    from remedy.interfaces import settings_apply

    async def fail(patch, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(settings_apply, "apply_settings_update", fail)
    with pytest.raises(SystemExit) as exc:
        cmd_settings._cmd_settings(_set(isolated_home, pairs=["thinking_level=high"]))
    assert exc.value.code == 1
    assert "Write failed" in _out(capsys)


def test_a_successful_set_echoes_the_message_changes_and_new_values(
    isolated_home, captured_apply, capsys
) -> None:
    cmd_settings._cmd_settings(_set(isolated_home, pairs=["thinking_level=high"]))
    out = _out(capsys)
    assert "Applied settings: thinking_level" in out
    assert "Changed:" in out
    assert "high" in out


def test_a_real_set_lands_in_the_config_file(isolated_home) -> None:
    """End to end through the shared apply path — no fake in the way."""
    cmd_settings._cmd_settings(_set(isolated_home, pairs=["thinking_level=low"]))
    text = (isolated_home / "config.toml").read_text(encoding="utf-8")
    assert 'thinking_level = "low"' in text


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------
@pytest.fixture()
def no_project_seed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """``create_default_config`` otherwise mkdirs ~/Documents/Remedy Projects."""
    from remedy.core import workspace

    seed = tmp_path / "seed-project"
    seed.mkdir()
    monkeypatch.setattr(workspace, "ensure_new_project_seed", lambda: seed)
    return seed


def test_config_init_creates_a_config_file(home, no_project_seed, capsys) -> None:
    asyncio.run(cmd_settings._cmd_config(_args(home, config_cmd="init")))
    assert (home / "config.toml").is_file()
    assert "Config created" in _out(capsys)


def test_config_init_never_overwrites_an_existing_file(home, no_project_seed) -> None:
    cfg = home / "config.toml"
    cfg.write_text('llm_provider = "deepseek"\n', encoding="utf-8")
    asyncio.run(cmd_settings._cmd_config(_args(home, config_cmd="init")))
    assert cfg.read_text(encoding="utf-8") == 'llm_provider = "deepseek"\n'


def test_config_show_redacts_secret_shaped_values(isolated_home, capsys) -> None:
    (isolated_home / "config.toml").write_text(
        'llm_provider = "openai"\nllm_api_key = "sk-do-not-print"\n', encoding="utf-8"
    )
    asyncio.run(cmd_settings._cmd_config(_args(isolated_home, config_cmd="show")))
    out = _out(capsys)
    assert "sk-do-not-print" not in out
    assert "[redacted]" in out


def test_config_path_prints_the_file_when_it_exists(home, capsys) -> None:
    (home / "config.toml").write_text("", encoding="utf-8")
    asyncio.run(cmd_settings._cmd_config(_args(home, config_cmd="path")))
    assert _squash(str(home / "config.toml")) in _squash(_out(capsys))


def test_config_path_says_how_to_create_a_missing_config(home, capsys) -> None:
    asyncio.run(cmd_settings._cmd_config(_args(home, config_cmd="path")))
    out = _out(capsys)
    assert "No config found" in out
    assert "remedy config init" in out


def test_an_unknown_config_subcommand_does_nothing(home, capsys) -> None:
    asyncio.run(cmd_settings._cmd_config(_args(home, config_cmd="frobnicate")))
    assert _out(capsys) == ""
    assert not (home / "config.toml").exists()


# --------------------------------------------------------------------------
# computer
# --------------------------------------------------------------------------
class FakeBridge:
    def __init__(self, *, connected: bool = False, pending: int = 0) -> None:
        self._connected = connected
        self._pending = pending

    def host_connected(self, **_kw) -> bool:
        return self._connected

    def pending_count(self) -> int:
        return self._pending


class FakeHost:
    def __init__(self, **status) -> None:
        self.running = bool(status.pop("running", False))
        self._status = {"jobs_completed": 0, "home": "tmp", **status}

    def status(self) -> dict:
        return dict(self._status)


@pytest.fixture()
def fake_computer(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """No real host, no real bridge — nothing here may reach the job queue."""
    from remedy.core.computer import cli_host, host_bridge

    calls: dict[str, list] = {"start": [], "stop": []}
    state: dict[str, object] = {"bridge": FakeBridge(), "host": FakeHost()}

    monkeypatch.setattr(host_bridge, "get_host_bridge", lambda *a, **kw: state["bridge"])
    monkeypatch.setattr(cli_host, "get_local_computer_host", lambda *a, **kw: state["host"])

    def fake_start(home_dir=None):
        calls["start"].append(home_dir)
        return state["host"]

    def fake_stop(**kw):
        calls["stop"].append(kw)
        return True

    monkeypatch.setattr(cli_host, "start_cli_computer_host", fake_start)
    monkeypatch.setattr(cli_host, "stop_cli_computer_host", fake_stop)
    return SimpleNamespace(calls=calls, state=state)


@pytest.mark.parametrize("sub", ["status", None])
def test_computer_status_reports_the_bridge_and_the_host(
    home, fake_computer, sub, capsys
) -> None:
    fake_computer.state["bridge"] = FakeBridge(connected=True, pending=3)
    fake_computer.state["host"] = FakeHost(running=True, jobs_completed=7, home=str(home))
    cmd_settings._cmd_computer(_args(home, computer_cmd=sub))
    out = _squash(_out(capsys))
    assert "host_connected:True" in out
    assert "pending_jobs:3" in out
    assert "running" in out
    assert "jobs_completed:7" in out


def test_computer_status_hides_last_action_and_error_when_there_are_none(
    home, fake_computer, capsys
) -> None:
    cmd_settings._cmd_computer(_args(home, computer_cmd="status"))
    out = _out(capsys)
    assert "last_action" not in out
    assert "last_error" not in out


def test_computer_status_surfaces_the_last_error(home, fake_computer, capsys) -> None:
    fake_computer.state["host"] = FakeHost(last_action="open_url", last_error="host refused")
    cmd_settings._cmd_computer(_args(home, computer_cmd="status"))
    out = _out(capsys)
    assert "open_url" in out
    assert "host refused" in out
    assert fake_computer.calls["start"] == [], "status must never start the host"


def test_host_start_starts_exactly_one_host_at_the_resolved_home(
    home, fake_computer, capsys
) -> None:
    cmd_settings._cmd_computer(_args(home, computer_cmd="host", action="start", api=False))
    assert [str(h) for h in fake_computer.calls["start"]] == [str(home.resolve())]
    assert fake_computer.calls["stop"] == []
    assert "CLI computer host started" in _out(capsys)


def test_host_stop_stops_without_starting_anything(home, fake_computer, capsys) -> None:
    cmd_settings._cmd_computer(_args(home, computer_cmd="host", action="stop", api=False))
    assert fake_computer.calls["start"] == []
    assert len(fake_computer.calls["stop"]) == 1
    assert "stopped" in _out(capsys)


@pytest.mark.parametrize("action", [None, ""])
def test_a_missing_host_action_defaults_to_start(home, fake_computer, action) -> None:
    cmd_settings._cmd_computer(_args(home, computer_cmd="host", action=action, api=False))
    assert len(fake_computer.calls["start"]) == 1


def test_host_run_always_stops_the_host_on_the_way_out(home, fake_computer) -> None:
    # running=False makes the foreground loop exit at once; the finally must still fire.
    cmd_settings._cmd_computer(_args(home, computer_cmd="host", action="run", api=False))
    assert len(fake_computer.calls["start"]) == 1
    assert len(fake_computer.calls["stop"]) == 1


def test_the_api_poller_is_spawned_from_the_repo_root(
    home, fake_computer, monkeypatch
) -> None:
    """It resolved parents[3], which is <root>/src, so the poller was looked
    for at <root>/src/scripts/ and never found: the flag printed "script
    missing" and silently did nothing, every time."""
    import subprocess

    spawned: list = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: spawned.append(a))
    cmd_settings._cmd_computer(_args(home, computer_cmd="host", action="start", api=True))
    assert spawned, "the poller was still not started"
    assert "computer_host_poller.py" in str(spawned[0])


def test_an_unknown_computer_subcommand_prints_usage(home, fake_computer, capsys) -> None:
    cmd_settings._cmd_computer(_args(home, computer_cmd="frobnicate"))
    assert "Usage: remedy computer status|host" in _out(capsys)
