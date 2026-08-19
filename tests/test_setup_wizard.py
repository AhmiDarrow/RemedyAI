"""First-run setup — the gate before serve/chat, and the config it writes.

The prompts themselves are not the interesting part. What matters is that a
non-interactive launch fails *loudly* instead of hanging on a prompt nobody can
answer, that skipping is remembered so the owner is never asked twice, and that
the config written to disk never carries a plaintext API key.
"""

from __future__ import annotations

import sys

import pytest

from remedy.interfaces import wizard as W


@pytest.fixture()
def home(tmp_path):
    h = tmp_path / "remedy-home"
    h.mkdir()
    return h


# --- the launch gate --------------------------------------------------------


def test_skipping_is_remembered_so_the_owner_is_asked_once(home):
    assert W.ensure_setup_before_launch(home_dir=home, skip_setup=True) is True
    # Second launch: already marked, no prompt, still fine.
    assert W.ensure_setup_before_launch(home_dir=home) is True


def test_a_configured_install_launches_straight_through(home, monkeypatch):
    monkeypatch.setattr(W, "needs_first_run_setup", lambda *a, **kw: False)
    assert W.ensure_setup_before_launch(home_dir=home) is True


def test_a_headless_launch_exits_instead_of_hanging_on_a_prompt(home, monkeypatch):
    """`remedy serve` under systemd must not block forever on stdin."""
    monkeypatch.setattr(W, "needs_first_run_setup", lambda *a, **kw: True)
    with pytest.raises(SystemExit) as exc:
        W.ensure_setup_before_launch(home_dir=home, non_interactive=True)
    assert exc.value.code == 1


def test_a_pipe_counts_as_headless(home, monkeypatch):
    """No tty means no one can answer, whatever the flag says."""
    monkeypatch.setattr(W, "needs_first_run_setup", lambda *a, **kw: True)
    monkeypatch.setattr(sys, "stdin", None)
    with pytest.raises(SystemExit):
        W.ensure_setup_before_launch(home_dir=home)


def test_choosing_abort_cancels_the_launch(home, monkeypatch):
    monkeypatch.setattr(W, "needs_first_run_setup", lambda *a, **kw: True)
    monkeypatch.setattr(sys, "stdin", type("S", (), {"isatty": lambda self: True})())
    monkeypatch.setattr(W.Prompt, "ask", staticmethod(lambda *a, **kw: "3"))
    assert W.ensure_setup_before_launch(home_dir=home) is False


def test_choosing_skip_at_the_prompt_is_also_remembered(home, monkeypatch):
    monkeypatch.setattr(W, "needs_first_run_setup", lambda *a, **kw: True)
    monkeypatch.setattr(sys, "stdin", type("S", (), {"isatty": lambda self: True})())
    monkeypatch.setattr(W.Prompt, "ask", staticmethod(lambda *a, **kw: "2"))
    assert W.ensure_setup_before_launch(home_dir=home) is True
    # Check the file, not the patched gate: the flag has to survive to disk.
    assert "setup_completed = true" in (home / "config.toml").read_text(
        encoding="utf-8"
    )


def test_force_reruns_the_wizard_even_when_configured(home, monkeypatch):
    ran = []
    monkeypatch.setattr(W, "needs_first_run_setup", lambda *a, **kw: False)
    monkeypatch.setattr(W, "run_wizard", lambda *a, **kw: ran.append(True))
    monkeypatch.setattr(sys, "stdin", type("S", (), {"isatty": lambda self: True})())
    monkeypatch.setattr(W.Prompt, "ask", staticmethod(lambda *a, **kw: "1"))
    assert W.ensure_setup_before_launch(home_dir=home, force=True) is True
    assert ran == [True]


def test_the_home_directory_is_created_if_missing(tmp_path):
    fresh = tmp_path / "never-existed"
    W.ensure_setup_before_launch(home_dir=fresh, skip_setup=True)
    assert fresh.is_dir()


# --- writing the config -----------------------------------------------------


def test_config_is_written_where_the_app_reads_it(home):
    path = W._write_config({"home_dir": str(home), "agent_name": "Remedy"})
    assert path == home / "config.toml"
    assert "Remedy" in path.read_text(encoding="utf-8")


def test_the_written_config_never_carries_a_plaintext_key(home):
    path = W._write_config(
        {
            "home_dir": str(home),
            "llm_api_key": "sk-ant-secret-value",
            "provider_keys": {"anthropic": "sk-ant-secret-value"},
        }
    )
    assert "sk-ant-secret-value" not in path.read_text(encoding="utf-8")


def test_writing_marks_setup_complete(home):
    W._write_config({"home_dir": str(home)})
    assert "setup_completed" in (home / "config.toml").read_text(encoding="utf-8")


def test_an_explicit_setup_flag_is_not_overwritten(home):
    W._write_config({"home_dir": str(home), "setup_completed": False})
    assert "false" in (home / "config.toml").read_text(encoding="utf-8").lower()


def test_windows_paths_are_normalised_so_toml_stays_valid(home):
    path = W._write_config({"home_dir": str(home), "project_path": r"C:\Users\me\proj"})
    text = path.read_text(encoding="utf-8")
    assert "C:/Users/me/proj" in text


def test_the_database_sits_under_the_configured_home(home):
    assert W._db_path({"home_dir": str(home)}) == home / "memory.db"


def test_the_database_default_expands_the_tilde():
    assert "~" not in str(W._db_path({}))


# --- small helpers ----------------------------------------------------------


def test_a_numeric_answer_selects_by_position(monkeypatch):
    monkeypatch.setattr(W.Prompt, "ask", staticmethod(lambda *a, **kw: "2"))
    assert W._pick_option("p", ["alpha", "beta", "gamma"], "alpha") == "beta"


def test_the_option_can_be_typed_by_name(monkeypatch):
    monkeypatch.setattr(W.Prompt, "ask", staticmethod(lambda *a, **kw: "gamma"))
    assert W._pick_option("p", ["alpha", "beta", "gamma"], "alpha") == "gamma"


def test_an_unrecognised_answer_falls_back_to_the_default(monkeypatch):
    monkeypatch.setattr(W.Prompt, "ask", staticmethod(lambda *a, **kw: "nonsense"))
    assert W._pick_option("p", ["alpha", "beta"], "alpha") == "alpha"


def test_an_out_of_range_number_falls_back_to_the_default(monkeypatch):
    monkeypatch.setattr(W.Prompt, "ask", staticmethod(lambda *a, **kw: "99"))
    assert W._pick_option("p", ["alpha", "beta"], "alpha") == "alpha"


def test_unicode_support_degrades_rather_than_raising(monkeypatch):
    monkeypatch.setattr(
        sys, "stdout", type("O", (), {"encoding": "not-a-real-codec"})()
    )
    assert W._supports_unicode() is False


def test_a_utf8_terminal_gets_the_box_drawing_banner(monkeypatch):
    monkeypatch.setattr(sys, "stdout", type("O", (), {"encoding": "utf-8"})())
    assert W._supports_unicode() is True


def test_the_welcome_banner_prints_on_a_legacy_terminal(monkeypatch, capsys):
    monkeypatch.setattr(W, "_supports_unicode", lambda: False)
    W._print_welcome()
    assert capsys.readouterr().out.strip()


def test_the_summary_table_renders(capsys):
    W._print_config_summary({"agent_name": "Remedy", "llm_provider": "anthropic"})
    assert "Remedy" in capsys.readouterr().out
