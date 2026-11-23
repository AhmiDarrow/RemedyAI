"""House rules: first-run conversation, skip-to-defaults, talk-to-change, audit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from remedy.core import house_rules as hr
from remedy.core import organism_knobs as knobs


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        'name = "Remedy"\nsetup_completed = false\n',
        encoding="utf-8",
    )
    return tmp_path


def test_defaults_and_questions_cover_setup_topics() -> None:
    ids = {q.id for q in hr.QUESTIONS}
    for needed in (
        "names", "language", "tone", "provider", "workspace", "proactive",
        "privacy", "tokens", "gpu", "consent", "interop", "updates", "launch",
    ):
        assert needed in ids
    d = hr.defaults()
    assert d["name"] == "Remedy"
    assert d["ui_language"] == "auto"
    assert d["proactive_reach_out"] is False
    assert d["always_send_images"] is False
    assert d["mind_handles_easy_chats"] is True
    assert d["muscle_gpu_policy"] == "auto"
    assert d["mind_keep_gpu"] is False
    assert d["interop_inbound"] is False
    assert d["update_check"] == "auto"
    assert d["approval_mode"] == "auto"
    assert "llm_api_key" not in d


def test_skip_all_uses_defaults_and_keeps_prior_answers(home: Path) -> None:
    hr.mark_onboarding_pending(home)
    hr.update_draft({"user_name": "Joe", "persona": "playful"}, home=home, origin="desktop")
    out = hr.skip_all(home=home, origin="desktop")
    assert out["ok"] is True
    assert out["status"] == hr.STATUS_SKIPPED
    rules = hr.load_rules(home)
    assert rules["answers"]["user_name"] == "Joe"
    assert rules["answers"]["persona"] == "playful"
    assert rules["answers"]["ui_language"] == "auto"
    assert rules["answers"]["interop_inbound"] is False
    cfg = (home / "config.toml").read_text(encoding="utf-8")
    assert "setup_completed = true" in cfg
    assert "Joe" in cfg


def test_take_onboarding_offer_is_once(home: Path) -> None:
    assert hr.take_onboarding_offer(home) is False  # not pending yet
    hr.mark_onboarding_pending(home)
    assert hr.take_onboarding_offer(home) is True
    assert hr.take_onboarding_offer(home) is False  # no nag
    assert hr.load_rules(home)["status"] == hr.STATUS_OFFERED


def test_onboarding_off_never_offers(home: Path, monkeypatch) -> None:
    hr.mark_onboarding_pending(home)
    monkeypatch.setenv("REMEDY_HOME", str(home))
    # Force knob off via config advanced table.
    (home / "config.toml").write_text(
        'setup_completed = false\n\n[advanced]\nonboarding = false\n',
        encoding="utf-8",
    )
    assert knobs.resolve_bool("onboarding", {"advanced": {"onboarding": False}}) is False
    # dispatch path uses resolve from disk; take_onboarding_offer too.
    assert hr.take_onboarding_offer(home) is False


def test_mode_switch_keeps_draft(home: Path) -> None:
    hr.update_draft({"user_name": "Sam"}, home=home, origin="desktop")
    hr.set_mode("traditional", home)
    hr.set_mode("conversation", home)
    assert hr.load_session(home)["draft"]["user_name"] == "Sam"
    assert hr.load_session(home)["mode"] == "conversation"


def test_confirm_onboarding_writes_provenance(home: Path) -> None:
    hr.mark_onboarding_pending(home)
    hr.record_answers({"user_name": "Ada", "privacy_mode": True}, skipped=["gpu"], home=home)
    out = hr.confirm_onboarding(home=home, origin="desktop")
    assert out["ok"] is True
    rules = hr.load_rules(home)
    assert rules["status"] == hr.STATUS_COMPLETE
    assert rules["answers"]["user_name"] == "Ada"
    assert rules["provenance"]["user_name"]["set_by"] == "owner"
    assert "gpu" in rules["skipped"]


def test_peers_cannot_change_house_rules(home: Path) -> None:
    hr.mark_onboarding_pending(home)
    for origin in ("a2a:bot", "mcp:claude", "telegram:1", "hive:parent", "partner:child:x"):
        assert hr.skip_all(home=home, origin=origin)["ok"] is False
        assert hr.propose({"persona": "concise"}, home=home, origin=origin)["ok"] is False
        assert hr.update_draft({"user_name": "X"}, home=home, origin=origin)["ok"] is False
    assert hr.dispatch({"action": "skip_all", "caller_origin": "a2a:peer", "home_dir": str(home)})["ok"] is False
    # Draft must still be empty — nothing leaked through.
    assert not hr.load_session(home).get("draft")


def test_plain_request_applies_ordinary_pref(home: Path) -> None:
    out = hr.propose({"persona": "concise", "user_name": "Kit"}, home=home, origin="desktop")
    assert out["ok"] is True
    assert out.get("needs_confirmation") is not True
    assert hr.load_rules(home)["answers"]["persona"] == "concise"
    assert "Kit" in (home / "config.toml").read_text(encoding="utf-8")


def test_sensitive_change_needs_confirmation_then_applies(home: Path) -> None:
    out = hr.propose({"privacy_mode": True, "interop_inbound": True}, home=home, origin="desktop")
    assert out["ok"] is True
    assert out["needs_confirmation"] is True
    assert hr.load_pending(home) is not None
    cfg = (home / "config.toml").read_text(encoding="utf-8")
    assert "privacy_mode = true" not in cfg.lower() or "privacy_mode = true" not in cfg
    # More reliable: not applied yet
    from remedy.interfaces.config import load_config

    assert not bool(load_config(home / "config.toml").get("privacy_mode"))
    ok = hr.confirm_pending(home=home, origin="desktop")
    assert ok["ok"] is True
    assert bool(load_config(home / "config.toml").get("privacy_mode")) is True
    assert hr.load_pending(home) is None


def test_decline_pending_leaves_settings(home: Path) -> None:
    hr.propose({"approval_mode": "full"}, home=home, origin="desktop")
    hr.decline_pending(home=home, origin="desktop")
    from remedy.interfaces.config import load_config

    assert load_config(home / "config.toml").get("approval_mode") != "full"
    assert hr.load_pending(home) is None


def test_rollback_restores_previous(home: Path) -> None:
    hr.propose({"persona": "concise"}, home=home, origin="desktop")
    hr.propose({"persona": "playful"}, home=home, origin="desktop")
    assert hr.load_rules(home)["answers"]["persona"] == "playful"
    out = hr.rollback(home=home, origin="desktop")
    assert out["ok"] is True
    assert hr.load_rules(home)["answers"]["persona"] == "concise"


def test_secrets_never_enter_audit_or_files(home: Path) -> None:
    # Stash a fake key through the draft path; it must go to the store only.
    hr.update_draft(
        {"llm_provider": "openai", "llm_api_key": "sk-live-secret-value-not-for-logs"},
        home=home,
        origin="desktop",
    )
    session_text = (home / "setup_session.json").read_text(encoding="utf-8")
    assert "sk-live-secret-value-not-for-logs" not in session_text
    if (home / "house_rules_audit.jsonl").exists():
        audit = (home / "house_rules_audit.jsonl").read_text(encoding="utf-8")
        assert "sk-live-secret-value-not-for-logs" not in audit
    if (home / "house_rules.toml").exists():
        assert "sk-live-secret-value-not-for-logs" not in (home / "house_rules.toml").read_text(encoding="utf-8")
    # Confirm / skip must also stay clean.
    hr.skip_all(home=home, origin="desktop")
    for name in ("setup_session.json", "house_rules.toml", "house_rules_audit.jsonl", "config.toml"):
        path = home / name
        if path.exists():
            assert "sk-live-secret-value-not-for-logs" not in path.read_text(encoding="utf-8")


def test_revisit_reopens_conversation(home: Path) -> None:
    hr.skip_all(home=home, origin="desktop")
    out = hr.revisit(home=home, origin="cli")
    assert out["ok"] is True
    assert hr.load_session(home)["onboarding_pending"] is True
    assert "house rules" in out["prompt"].lower()


def test_classify_mind_failure_cpu_is_ok() -> None:
    assert hr.classify_mind_failure({"installed": True, "ready": True, "health": {"nvidia_detected": False}}) is None
    fail = hr.classify_mind_failure({"progress": {"error": "sha256 mismatch", "phase": "verifying"}})
    assert fail is not None and fail["reason"] == "verify"
    ram = hr.classify_mind_failure({"health": {"ram_gb": 3, "min_ram_gb": 8}})
    assert ram is not None and ram["reason"] == "low_ram"
    assert hr.mind_ready({"installed": True, "running": True}) is True
    assert hr.mind_ready({"installed": False, "progress": {"error": "download failed"}}) is False


def test_knobs_defaults() -> None:
    table = knobs.effective_table({})
    assert table["onboarding"] is True
    assert table["onboarding_reask"] is False
    assert table["house_rules_permission"] == "confirm_sensitive"
    assert table["house_rules_audit"] is True
    assert table["interop_inbound"] is False
    assert table["muscle_gpu_policy"] == "auto"
    assert table["mind_handles_easy_chats"] is True
    assert table["mind_keep_gpu"] is False


def test_dispatch_status_and_summarize(home: Path) -> None:
    hr.update_draft({"user_name": "Bo"}, home=home, origin="desktop")
    st = hr.dispatch({"action": "status", "home_dir": str(home), "caller_origin": "desktop"})
    assert st["ok"] is True
    assert st["draft"]["user_name"] == "Bo"
    sm = hr.dispatch({"action": "summarize", "home_dir": str(home)})
    assert "Names" in sm["summary"]


def test_skip_setup_inherits_without_nag(home: Path) -> None:
    """CLI --skip-setup writes setup_completed and must not offer later."""
    from remedy.interfaces.config import mark_setup_completed

    mark_setup_completed(config_path=home / "config.toml")
    rules = hr.load_rules(home)
    assert rules["status"] == hr.STATUS_INHERITED
    assert hr.take_onboarding_offer(home) is False


def test_update_offer_once_for_existing_install(home: Path) -> None:
    """Existing / inherited installs get one pop-up; Let's go or Not now ends it."""
    (home / "config.toml").write_text("setup_completed = true\nname = \"Remedy\"\n", encoding="utf-8")
    offer = hr.update_offer_public(home)
    assert offer["available"] is True
    assert offer["primary"] == "Let's go"
    assert offer["secondary"] == "Not now"
    assert "smarter token saving" in offer["body"]
    assert "A2A and MCP" in offer["body"]
    assert "house rules" in offer["body"]
    assert "built-in mind" not in offer["body"]
    assert "worth a few minutes" in offer["body"]
    # First-run chat inject still stays quiet.
    assert hr.take_onboarding_offer(home) is False

    later = hr.resolve_update_offer("later", home=home, origin="desktop")
    assert later["ok"] is True
    assert later["choice"] == "declined"
    assert later["update_offer"]["available"] is False
    assert hr.update_offer_available(home) is False
    # Not now is final — never a second pop-up.
    again = hr.resolve_update_offer("go", home=home, origin="desktop")
    assert again["update_offer"]["available"] is False
    assert hr.load_session(home)["update_offer"] == "declined"


def test_update_offer_lets_go_starts_revisit(home: Path) -> None:
    (home / "config.toml").write_text("setup_completed = true\nname = \"Remedy\"\n", encoding="utf-8")
    out = hr.resolve_update_offer("go", home=home, origin="desktop")
    assert out["ok"] is True
    assert out["choice"] == "accepted"
    assert out["update_offer"]["available"] is False
    session = hr.load_session(home)
    assert session["update_offer"] == "accepted"
    assert session["status"] == hr.STATUS_PENDING
    assert session["onboarding_pending"] is True
    assert session["source"] == "revisit"
    # After Let's go the pop-up itself does not come back.
    assert hr.update_offer_available(home) is False


def test_update_offer_not_for_fresh_or_finished_setup(home: Path) -> None:
    # Fresh install (setup not completed) — first-run conversation, not this pop-up.
    (home / "config.toml").write_text("setup_completed = false\n", encoding="utf-8")
    assert hr.update_offer_available(home) is False

    # Finished first-run conversation — already went over house rules.
    hr.mark_onboarding_pending(home)
    hr.skip_all(home=home, origin="desktop")
    assert hr.load_rules(home)["status"] == hr.STATUS_SKIPPED
    assert hr.update_offer_available(home) is False


def test_update_offer_peers_cannot_resolve(home: Path) -> None:
    (home / "config.toml").write_text("setup_completed = true\n", encoding="utf-8")
    out = hr.resolve_update_offer("go", home=home, origin="a2a:peer")
    assert out["ok"] is False
    assert hr.update_offer_available(home) is True


def test_interop_and_muscle_answers_bind_to_live_knobs(home: Path) -> None:
    """House-rules answers write the real #72 / #73 advanced knobs."""
    from remedy.interfaces.config import load_config

    out = hr.propose(
        {
            "muscle_gpu_policy": "yield_mind",
            "mind_handles_easy_chats": True,
            "mind_keep_gpu": False,
        },
        home=home,
        origin="desktop",
    )
    assert out["ok"] is True
    assert out.get("needs_confirmation") is not True
    cfg = load_config(home / "config.toml")
    assert knobs.resolve("muscle_gpu_policy", cfg) == "yield_mind"
    assert knobs.resolve("mind_handles_easy_chats", cfg) is True
    assert knobs.resolve("mind_keep_gpu", cfg) is False

    proposed = hr.propose({"interop_inbound": True}, home=home, origin="desktop")
    assert proposed["ok"] is True
    assert proposed["needs_confirmation"] is True
    assert knobs.resolve("interop_inbound", load_config(home / "config.toml")) is False
    ok = hr.confirm_pending(home=home, origin="desktop")
    assert ok["ok"] is True
    cfg = load_config(home / "config.toml")
    advanced = cfg.get("advanced") if isinstance(cfg.get("advanced"), dict) else {}
    assert advanced.get("interop_inbound") is True
    assert knobs.resolve("interop_inbound", cfg) is True
