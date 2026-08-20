"""The individual sections of the setup wizard — the questions it asks and,
more importantly, the ones it must not.

If this code is wrong the owner's very first minute with Remedy goes wrong: a
skipped section quietly invents a provider they never chose, an API key gets
echoed to the screen or written into a plaintext config, a mistyped channel
number silently enables a bot, or "quick" mode stops to ask a question nobody
is there to answer. Each section is tested on its own so a failure names the
step that broke.

Nothing here touches the real ``~/.remedy``: ``run_wizard`` hardcodes it as the
destination, so every test that runs the whole wizard replaces ``_write_config``
and inspects the dict that *would* have been written.
"""

from __future__ import annotations

import sys

import pytest

from remedy.interfaces import wizard as W


class Answers:
    """A scripted stand-in for a rich prompt.

    Records every question asked so a test can assert on the offered defaults,
    and refuses to invent an answer — an unscripted question is a test failure,
    not a silent default.
    """

    def __init__(self, *answers):
        self.pending = list(answers)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, prompt="", *args, **kwargs):
        self.calls.append((str(prompt), kwargs))
        if not self.pending:
            raise AssertionError(f"unscripted question: {prompt!r}")
        return self.pending.pop(0)

    @property
    def prompts(self) -> list[str]:
        return [p for p, _ in self.calls]


def _refuse(*args, **kwargs):
    raise AssertionError("the wizard prompted when it must not have")


def patch_prompts(
    monkeypatch,
    *,
    text=_refuse,
    confirm=_refuse,
    integer=_refuse,
    number=_refuse,
    secret=_refuse,
):
    """Replace every input channel the wizard can reach."""
    monkeypatch.setattr(W.Prompt, "ask", staticmethod(text))
    monkeypatch.setattr(W.Confirm, "ask", staticmethod(confirm))
    monkeypatch.setattr(W.IntPrompt, "ask", staticmethod(integer))
    monkeypatch.setattr(W.FloatPrompt, "ask", staticmethod(number))
    monkeypatch.setattr(W.getpass, "getpass", secret)


# --- persona picker ---------------------------------------------------------


@pytest.mark.parametrize(
    "answer,expected",
    [(1, "default"), (2, "concise"), (3, "verbose"), (4, "sarcastic"), (5, "minimal")],
)
def test_each_persona_number_selects_the_persona_shown_on_that_row(
    monkeypatch, answer, expected
):
    monkeypatch.setattr(W.IntPrompt, "ask", staticmethod(lambda *a, **kw: answer))
    assert W._pick_persona() == expected


@pytest.mark.parametrize("answer,expected", [(0, "default"), (-7, "default"), (99, "minimal")])
def test_a_persona_number_out_of_range_is_clamped_not_rejected(
    monkeypatch, answer, expected
):
    """A fat-fingered number must not raise IndexError mid-wizard."""
    monkeypatch.setattr(W.IntPrompt, "ask", staticmethod(lambda *a, **kw: answer))
    assert W._pick_persona() == expected


# --- channel picker ---------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1", ["cli"]),
        ("3", ["telegram"]),
        ("1,3,4", ["cli", "telegram", "discord"]),
        ("3,1", ["telegram", "cli"]),  # order follows what the user typed
        ("3,3,3", ["telegram"]),  # duplicates collapse
        (" 1 , 3 ", ["cli", "telegram"]),  # spaces are tolerated
        ("2,x,5", ["web", "slack"]),  # junk in the middle is dropped
    ],
)
def test_channel_numbers_map_to_the_rows_they_were_printed_against(
    monkeypatch, raw, expected
):
    monkeypatch.setattr(W.Prompt, "ask", staticmethod(lambda *a, **kw: raw))
    assert W._pick_channels() == expected


@pytest.mark.parametrize("raw", ["", "0", "9", "-1", "abc", ",,,", "1.5"])
def test_an_unusable_channel_answer_enables_cli_only_never_nothing(monkeypatch, raw):
    """A bad answer must not leave the agent with zero channels to talk on."""
    monkeypatch.setattr(W.Prompt, "ask", staticmethod(lambda *a, **kw: raw))
    assert W._pick_channels() == ["cli"]


# --- LLM provider section ---------------------------------------------------


def test_quick_mode_picks_openai_without_asking_anything(monkeypatch, capsys):
    patch_prompts(monkeypatch)
    cfg: dict = {}
    W._configure_llm_provider(cfg, quick=True)
    assert cfg["llm_provider"] == "openai"
    assert cfg["llm_model"] == "gpt-4o-mini"
    assert cfg["llm_base_url"] == "https://api.openai.com/v1"
    assert cfg["llm_api_key"] == ""  # quick mode must never invent a key
    capsys.readouterr()


def test_skipping_the_provider_section_leaves_the_config_untouched(monkeypatch):
    """--skip-providers must not silently select a provider on the user's behalf."""
    patch_prompts(monkeypatch)
    cfg: dict = {"name": "Remedy"}
    W._configure_llm_provider(cfg, skip=True)
    assert cfg == {"name": "Remedy"}


def test_quick_beats_skip_when_both_are_given(monkeypatch, capsys):
    patch_prompts(monkeypatch)
    cfg: dict = {}
    W._configure_llm_provider(cfg, quick=True, skip=True)
    assert cfg["llm_provider"] == "openai"
    out = capsys.readouterr().out
    assert "Skipping provider setup" in out


def test_declining_the_provider_question_writes_no_provider_keys(monkeypatch):
    patch_prompts(monkeypatch, confirm=lambda *a, **kw: False)
    cfg: dict = {}
    W._configure_llm_provider(cfg)
    assert cfg == {}


def test_an_accepted_provider_is_recorded_with_model_and_base_url(monkeypatch, capsys):
    patch_prompts(
        monkeypatch,
        confirm=lambda *a, **kw: True,
        integer=lambda *a, **kw: 2,  # anthropic
        text=Answers("claude-sonnet-5", "https://api.anthropic.com/v1"),
        secret=lambda *a, **kw: "sk-ant-test",
    )
    cfg: dict = {}
    W._configure_llm_provider(cfg)
    assert cfg["llm_provider"] == "anthropic"
    assert cfg["llm_model"] == "claude-sonnet-5"
    assert cfg["llm_base_url"] == "https://api.anthropic.com/v1"
    assert cfg["llm_api_key"] == "sk-ant-test"
    capsys.readouterr()


def test_a_trailing_slash_on_the_base_url_is_stripped(monkeypatch, capsys):
    patch_prompts(
        monkeypatch,
        confirm=lambda *a, **kw: True,
        integer=lambda *a, **kw: 1,
        text=Answers("gpt-4o-mini", "https://proxy.internal/v1///"),
        secret=lambda *a, **kw: "",
    )
    cfg: dict = {}
    W._configure_llm_provider(cfg)
    assert cfg["llm_base_url"] == "https://proxy.internal/v1"
    capsys.readouterr()


def test_whitespace_around_a_pasted_api_key_is_trimmed(monkeypatch, capsys):
    """Keys are usually pasted; a stray newline would break every request."""
    patch_prompts(
        monkeypatch,
        confirm=lambda *a, **kw: True,
        integer=lambda *a, **kw: 1,
        text=Answers("gpt-4o-mini", "https://api.openai.com/v1"),
        secret=lambda *a, **kw: "  sk-live-123 \n",
    )
    cfg: dict = {}
    W._configure_llm_provider(cfg)
    assert cfg["llm_api_key"] == "sk-live-123"
    capsys.readouterr()


def test_the_api_key_is_read_hidden_and_never_echoed_back(monkeypatch, capsys):
    """It must go through getpass, and must not be printed to the terminal."""
    asked = Answers("gpt-4o-mini", "https://api.openai.com/v1")
    hidden = []

    def secret(prompt=""):
        hidden.append(prompt)
        return "sk-super-secret"

    patch_prompts(
        monkeypatch,
        confirm=lambda *a, **kw: True,
        integer=lambda *a, **kw: 1,
        text=asked,
        secret=secret,
    )
    cfg: dict = {}
    W._configure_llm_provider(cfg)
    assert hidden, "the key was not read through getpass"
    assert "sk-super-secret" not in capsys.readouterr().out
    assert not any("sk-super-secret" in p for p in asked.prompts)


@pytest.mark.parametrize("answer,expected", [(0, "openai"), (-4, "openai"), (99, "custom")])
def test_a_provider_number_out_of_range_is_clamped(monkeypatch, capsys, answer, expected):
    patch_prompts(
        monkeypatch,
        confirm=lambda *a, **kw: True,
        integer=lambda *a, **kw: answer,
        text=Answers("some-model", "https://example.test/v1"),
        secret=lambda *a, **kw: "",
    )
    cfg: dict = {}
    W._configure_llm_provider(cfg)
    assert cfg["llm_provider"] == expected
    capsys.readouterr()


def test_the_custom_provider_offers_working_defaults_not_blanks(monkeypatch, capsys):
    """The 'custom' row has no model or URL of its own — pressing Enter through
    it must still leave a usable config rather than empty strings."""
    asked = Answers("gpt-4o-mini", "https://api.openai.com/v1")
    patch_prompts(
        monkeypatch,
        confirm=lambda *a, **kw: True,
        integer=lambda *a, **kw: len(W.LLM_PROVIDERS),  # last row = custom
        text=asked,
        secret=lambda *a, **kw: "",
    )
    cfg: dict = {}
    W._configure_llm_provider(cfg)
    assert cfg["llm_provider"] == "custom"
    offered = [kw.get("default") for _, kw in asked.calls]
    assert offered == ["gpt-4o-mini", "https://api.openai.com/v1"]
    capsys.readouterr()


def test_a_provider_with_no_key_says_so_instead_of_pretending(monkeypatch, capsys):
    patch_prompts(
        monkeypatch,
        confirm=lambda *a, **kw: True,
        integer=lambda *a, **kw: 1,
        text=Answers("gpt-4o-mini", "https://api.openai.com/v1"),
        secret=lambda *a, **kw: "   ",
    )
    cfg: dict = {}
    W._configure_llm_provider(cfg)
    assert cfg["llm_api_key"] == ""
    assert "No API key set" in capsys.readouterr().out


# --- messaging section ------------------------------------------------------


@pytest.mark.parametrize("flags", [{"quick": True}, {"skip": True}, {"quick": True, "skip": True}])
def test_quick_or_skipped_messaging_enables_cli_only_and_asks_nothing(
    monkeypatch, capsys, flags
):
    patch_prompts(monkeypatch)
    cfg: dict = {}
    W._configure_messaging(cfg, **flags)
    assert cfg == {"enabled_channels": ["cli"]}
    capsys.readouterr()


def test_declining_messaging_still_leaves_the_cli_channel_on(monkeypatch, capsys):
    patch_prompts(monkeypatch, confirm=lambda *a, **kw: False)
    cfg: dict = {}
    W._configure_messaging(cfg)
    assert cfg["enabled_channels"] == ["cli"]
    capsys.readouterr()


def test_a_selected_bot_channel_is_asked_for_its_token(monkeypatch, capsys):
    confirm = Answers(True, True)  # configure messaging? / have a telegram token?
    patch_prompts(
        monkeypatch,
        confirm=confirm,
        text=lambda *a, **kw: "3",  # telegram
        secret=lambda *a, **kw: "  123:ABC  ",
    )
    cfg: dict = {}
    W._configure_messaging(cfg)
    assert cfg["enabled_channels"] == ["telegram"]
    assert cfg["telegram"] == {"bot_token": "123:ABC"}
    capsys.readouterr()


def test_declining_a_token_records_an_empty_one_rather_than_omitting_it(
    monkeypatch, capsys
):
    """The empty table is what tells the user where to paste the token later."""
    patch_prompts(
        monkeypatch,
        confirm=Answers(True, False),
        text=lambda *a, **kw: "4",  # discord
    )
    cfg: dict = {}
    W._configure_messaging(cfg)
    assert cfg["discord"] == {"bot_token": ""}
    capsys.readouterr()


def test_channels_that_need_no_token_are_never_asked_for_one(monkeypatch, capsys):
    """cli and web have no credential; prompting for one would be nonsense."""
    patch_prompts(monkeypatch, confirm=Answers(True), text=lambda *a, **kw: "1,2")
    cfg: dict = {}
    W._configure_messaging(cfg)
    assert cfg["enabled_channels"] == ["cli", "web"]
    assert "cli" not in cfg and "web" not in cfg
    capsys.readouterr()


def test_only_the_selected_bot_channels_get_token_tables(monkeypatch, capsys):
    patch_prompts(
        monkeypatch,
        confirm=Answers(True, False, False),  # messaging? / telegram? / slack?
        text=lambda *a, **kw: "3,5",
    )
    cfg: dict = {}
    W._configure_messaging(cfg)
    assert set(cfg) == {"enabled_channels", "telegram", "slack"}
    assert "discord" not in cfg
    capsys.readouterr()


def test_a_bot_token_never_reaches_the_config_file(tmp_path, capsys):
    """Tokens belong in the secret store, not in a file people paste into issues."""
    home = tmp_path / "home"
    path = W._write_config(
        {"home_dir": str(home), "telegram": {"bot_token": "123:SUPERSECRET"}}
    )
    assert "SUPERSECRET" not in path.read_text(encoding="utf-8")
    capsys.readouterr()


# --- skills section ---------------------------------------------------------


@pytest.mark.parametrize("flags", [{"quick": True}, {"skip": True}])
def test_quick_or_skipped_skill_discovery_scans_nothing(monkeypatch, capsys, flags):
    patch_prompts(monkeypatch)
    cfg: dict = {}
    W._configure_skills(cfg, **flags)
    assert cfg == {"skills_dir": []}
    capsys.readouterr()


def test_declining_skill_discovery_scans_nothing(monkeypatch, capsys):
    patch_prompts(monkeypatch, confirm=lambda *a, **kw: False)
    cfg: dict = {}
    W._configure_skills(cfg)
    assert cfg["skills_dir"] == []
    capsys.readouterr()


def test_skill_directories_are_collected_until_an_empty_answer(monkeypatch, capsys):
    patch_prompts(
        monkeypatch,
        confirm=lambda *a, **kw: True,
        text=Answers("skills", "extra/skills", ""),
    )
    cfg: dict = {}
    W._configure_skills(cfg)
    assert cfg["skills_dir"] == ["skills", "extra/skills"]
    capsys.readouterr()


def test_an_immediately_empty_answer_ends_the_loop(monkeypatch, capsys):
    patch_prompts(monkeypatch, confirm=lambda *a, **kw: True, text=Answers(""))
    cfg: dict = {}
    W._configure_skills(cfg)
    assert cfg["skills_dir"] == []
    capsys.readouterr()


def test_the_first_directory_is_suggested_but_later_ones_are_not(monkeypatch, capsys):
    asked = Answers("skills", "")
    patch_prompts(monkeypatch, confirm=lambda *a, **kw: True, text=asked)
    W._configure_skills({})
    assert [kw.get("default") for _, kw in asked.calls] == ["skills", ""]
    capsys.readouterr()


# --- the whole wizard, with the write intercepted ---------------------------


@pytest.fixture()
def written(monkeypatch, tmp_path):
    """Capture the config run_wizard would have written, without writing it."""
    captured: dict = {}

    def fake_write(config):
        captured.clear()
        captured.update(config)
        return tmp_path / "config.toml"

    monkeypatch.setattr(W, "_write_config", fake_write)
    return captured


def test_quick_mode_produces_a_complete_config_without_a_single_question(
    monkeypatch, capsys, written
):
    patch_prompts(monkeypatch)
    path = W.run_wizard(quick=True)
    assert path.name == "config.toml"
    assert written["name"] == "Remedy"
    assert written["persona"] == "default"
    assert written["log_level"] == "INFO"
    assert written["auto_approve_threshold"] == 0.8
    assert written["allow_skill_creation"] is True
    assert written["enabled_channels"] == ["cli"]
    assert written["skills_dir"] == []
    capsys.readouterr()


def test_a_completed_wizard_marks_setup_so_it_never_reruns(monkeypatch, capsys, written):
    patch_prompts(monkeypatch)
    W.run_wizard(quick=True)
    assert written["setup_completed"] is True
    capsys.readouterr()


def test_gateway_and_execution_defaults_are_always_present(monkeypatch, capsys, written):
    patch_prompts(monkeypatch)
    W.run_wizard(quick=True)
    assert written["gateway"] == {"heartbeat_interval": 60, "rate_limit": 120}
    assert written["execution"] == {
        "default_timeout": 30,
        "max_retries": 3,
        "retry_backoff": 1.0,
    }
    capsys.readouterr()


def test_quick_mode_ignores_the_skip_flags_it_already_covers(
    monkeypatch, capsys, written
):
    patch_prompts(monkeypatch)
    W.run_wizard(quick=True, skip_providers=True, skip_messaging=True, skip_skills=True)
    assert written["llm_provider"] == "openai"
    assert written["enabled_channels"] == ["cli"]
    capsys.readouterr()


def test_an_agent_name_with_special_characters_falls_back_to_remedy(
    monkeypatch, capsys, written
):
    """The name becomes a skill namespace; punctuation would poison paths."""
    patch_prompts(
        monkeypatch,
        text=Answers("My Agent!!", "INFO"),
        integer=lambda *a, **kw: 1,
        number=lambda *a, **kw: 0.8,
        confirm=Answers(True, True),
    )
    W.run_wizard(skip_providers=True, skip_messaging=True, skip_skills=True)
    assert written["name"] == "remedy"
    assert "special chars" in capsys.readouterr().out


def test_an_acceptable_name_is_kept_exactly_as_typed(monkeypatch, capsys, written):
    """validate_skill_name lowercases its return value; the wizard must not
    quietly rename the agent the user just chose."""
    patch_prompts(
        monkeypatch,
        text=Answers("Aria", "DEBUG"),
        integer=lambda *a, **kw: 2,
        number=lambda *a, **kw: 0.5,
        confirm=Answers(False, True),
    )
    W.run_wizard(skip_providers=True, skip_messaging=True, skip_skills=True)
    assert written["name"] == "Aria"
    assert written["persona"] == "concise"
    assert written["log_level"] == "DEBUG"
    assert written["auto_approve_threshold"] == 0.5
    assert written["allow_skill_creation"] is False
    capsys.readouterr()


def test_refusing_to_save_exits_cleanly_and_writes_nothing(monkeypatch, capsys, written):
    patch_prompts(
        monkeypatch,
        text=Answers("Remedy", "INFO"),
        integer=lambda *a, **kw: 1,
        number=lambda *a, **kw: 0.8,
        confirm=Answers(True, False),  # allow skill creation / save?
    )
    with pytest.raises(SystemExit) as exc:
        W.run_wizard(skip_providers=True, skip_messaging=True, skip_skills=True)
    assert exc.value.code == 0  # a deliberate cancel is not an error
    assert written == {}
    assert "No changes made" in capsys.readouterr().out


def test_skipped_sections_leave_no_provider_in_the_saved_config(
    monkeypatch, capsys, written
):
    patch_prompts(
        monkeypatch,
        text=Answers("Remedy", "INFO"),
        integer=lambda *a, **kw: 1,
        number=lambda *a, **kw: 0.8,
        confirm=Answers(True, True),
    )
    W.run_wizard(skip_providers=True, skip_messaging=True, skip_skills=True)
    assert "llm_provider" not in written
    assert "llm_api_key" not in written
    capsys.readouterr()


def test_the_wizard_targets_the_home_it_was_pointed_at(
    monkeypatch, capsys, written, tmp_path
):
    """It hardcoded ~/.remedy, so `remedy setup` on a portable or --home
    install configured the real user home instead of its own."""
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setenv("REMEDY_HOME", str(elsewhere))
    patch_prompts(monkeypatch)
    W.run_wizard(quick=True)
    assert written["home_dir"] == elsewhere.resolve().as_posix()
    capsys.readouterr()


# --- terminal capability probe ----------------------------------------------


def test_a_terminal_with_no_encoding_crashes_the_probe(monkeypatch):
    """Documents a rough edge: only UnicodeError/LookupError are caught, so a
    stream whose encoding is None (a detached pythonw stdout) raises."""
    monkeypatch.setattr(sys, "stdout", type("O", (), {"encoding": None})())
    with pytest.raises(TypeError):
        W._supports_unicode()


def test_a_missing_stdout_crashes_the_probe(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    with pytest.raises(AttributeError):
        W._supports_unicode()
