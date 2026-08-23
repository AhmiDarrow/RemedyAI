"""Which messengers come up, and — more importantly — which quietly do not.

Every adapter here is gated on a credential. When one is missing the channel is
skipped with a log line and the gateway starts anyway, so a typo in the config
does not crash Remedy; it just means the owner's Telegram never answers and
nothing says why. These tests pin both halves: the channel registers when its
secrets are present, and it is *absent* rather than half-alive when they are not.
"""

from __future__ import annotations

import pytest

from remedy.gateway.channel_registry import _home, register_messenger_channels


class FakeGateway:
    """Collects registrations; the real Gateway would start pollers."""

    def __init__(self) -> None:
        self.channels: list[object] = []

    def register_channel(self, channel) -> None:
        self.channels.append(channel)


@pytest.fixture()
def gw():
    return FakeGateway()


#: (config key, the secrets that make it come up)
FULLY_CONFIGURED = {
    "telegram": {"bot_token": "t-tok"},
    "discord": {"bot_token": "d-tok", "channel_id": "c1"},
    "slack": {"bot_token": "s-tok", "app_token": "s-app", "channel_id": "c1"},
    "mattermost": {"bot_token": "m-tok", "base_url": "https://mm.example"},
    "matrix": {"access_token": "mx-tok", "homeserver": "https://mx.example"},
    "whatsapp": {"access_token": "wa-tok", "phone_number_id": "123"},
    "teams": {"app_id": "app", "app_password": "pw"},
    "google_chat": {"access_token": "gc-tok"},
    "signal": {"account": "+15550100"},
}


@pytest.mark.parametrize("name", sorted(FULLY_CONFIGURED))
def test_each_channel_registers_when_configured(gw, tmp_path, name):
    cfg = {"home_dir": str(tmp_path), "enabled_channels": [name], name: FULLY_CONFIGURED[name]}
    assert register_messenger_channels(gw, cfg) == [name]
    assert len(gw.channels) == 1


#: Drop one required secret each and the channel must not come up at all.
@pytest.mark.parametrize(
    ("name", "drop"),
    [
        ("telegram", "bot_token"),
        ("discord", "bot_token"),
        ("slack", "bot_token"),
        ("mattermost", "bot_token"),
        ("mattermost", "base_url"),
        ("matrix", "access_token"),
        ("matrix", "homeserver"),
        ("whatsapp", "access_token"),
        ("whatsapp", "phone_number_id"),
        ("teams", "app_id"),
        ("teams", "app_password"),
        ("google_chat", "access_token"),
    ],
)
def test_a_channel_missing_a_secret_is_skipped_not_half_registered(
    gw, tmp_path, caplog, name, drop
):
    secrets = dict(FULLY_CONFIGURED[name])
    secrets.pop(drop)
    cfg = {"home_dir": str(tmp_path), "enabled_channels": [name], name: secrets}
    with caplog.at_level("WARNING"):
        assert register_messenger_channels(gw, cfg) == []
    assert gw.channels == []
    # Silence here is the failure mode we are guarding against.
    assert any(name in r.message for r in caplog.records), (
        f"{name} was skipped without saying so"
    )


def test_signal_needs_no_credential(gw, tmp_path):
    """signal-cli authenticates out of band, so there is nothing to gate on."""
    cfg = {"home_dir": str(tmp_path), "enabled_channels": ["signal"]}
    assert register_messenger_channels(gw, cfg) == ["signal"]


def test_nothing_enabled_registers_nothing(gw, tmp_path):
    assert register_messenger_channels(gw, {"home_dir": str(tmp_path)}) == []
    assert gw.channels == []


def test_a_configured_but_unenabled_channel_stays_down(gw, tmp_path):
    """Credentials on disk are not consent to run the channel."""
    cfg = {
        "home_dir": str(tmp_path),
        "enabled_channels": [],
        "telegram": {"bot_token": "t-tok"},
    }
    assert register_messenger_channels(gw, cfg) == []


def test_channel_names_are_matched_case_and_space_insensitively(gw, tmp_path):
    cfg = {
        "home_dir": str(tmp_path),
        "enabled_channels": ["  Telegram "],
        "telegram": {"bot_token": "t-tok"},
    }
    assert register_messenger_channels(gw, cfg) == ["telegram"]


def test_a_bare_string_instead_of_a_list_still_works(gw, tmp_path):
    """Hand-edited config often has `enabled_channels: telegram`."""
    cfg = {
        "home_dir": str(tmp_path),
        "enabled_channels": "telegram",
        "telegram": {"bot_token": "t-tok"},
    }
    assert register_messenger_channels(gw, cfg) == ["telegram"]


@pytest.mark.parametrize(
    ("kwarg", "name"),
    [
        ("token_telegram", "telegram"),
        ("token_discord", "discord"),
        ("token_slack", "slack"),
    ],
)
def test_an_explicit_token_turns_the_channel_on_by_itself(gw, tmp_path, kwarg, name):
    """`remedy serve --telegram-token …` should not also require config edits."""
    cfg = {"home_dir": str(tmp_path), "enabled_channels": []}
    assert register_messenger_channels(gw, cfg, **{kwarg: "cli-tok"}) == [name]


def test_several_channels_come_up_together(gw, tmp_path):
    cfg = {"home_dir": str(tmp_path), "enabled_channels": ["telegram", "slack", "signal"]}
    cfg.update({k: FULLY_CONFIGURED[k] for k in ("telegram", "slack")})
    assert sorted(register_messenger_channels(gw, cfg)) == ["signal", "slack", "telegram"]


def test_a_non_dict_channel_section_does_not_crash(gw, tmp_path):
    """Config written by hand; `telegram: true` must not take the gateway down."""
    cfg = {"home_dir": str(tmp_path), "enabled_channels": ["telegram"], "telegram": True}
    assert register_messenger_channels(gw, cfg, token_telegram="t") == ["telegram"]


# --- home resolution --------------------------------------------------------
# The poll lock and Telegram offset live under this path. Two Remedy processes
# that disagree about it will both poll the same bot and double-answer.


def test_home_prefers_explicit_config(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "env"))
    assert _home({"home_dir": str(tmp_path / "cfg")}) == tmp_path / "cfg"


def test_home_falls_back_to_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "env"))
    assert _home({}) == tmp_path / "env"


def test_home_last_resort_matches_the_cli_default(monkeypatch):
    from remedy.home import default_home

    monkeypatch.delenv("REMEDY_HOME", raising=False)
    assert _home({}) == default_home()
