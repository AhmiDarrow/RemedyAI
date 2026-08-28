"""RMB exclusive-host mode + vision skip policy."""

from __future__ import annotations

import tempfile

from remedy.runtime.rmb.config import load_rmb_json, merge_state, save_rmb_json
from remedy.runtime.rmb.mode import (
    force_path_only_images,
    is_local_agent_mode,
    is_rmb_base_url,
    is_rmb_provider,
    should_skip_vision_stack,
    silent_context_for_local_agent,
)
from remedy.runtime.rmb.service import stop_rmb_server


def test_llamacpp_is_not_rmb_without_8787(tmp_path, monkeypatch):
    # Isolate from host ~/.remedy rmb.json + any live RMB process left by other tests.
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.is_starting", lambda: False, raising=False
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.managed_process_alive", lambda: False, raising=False
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.mode.rmb_server_running", lambda home_dir=None: False
    )
    home = str(tmp_path)
    assert is_rmb_provider("llamacpp") is False
    assert is_rmb_provider("llamacpp", "http://127.0.0.1:8740/v1") is False
    assert is_local_agent_mode(
        {
            "home_dir": home,
            "llm_provider": "llamacpp",
            "llm_base_url": "http://127.0.0.1:8740/v1",
        }
    ) is False
    assert should_skip_vision_stack(
        {
            "home_dir": home,
            "llm_provider": "llamacpp",
            "llm_base_url": "http://127.0.0.1:8740/v1",
        }
    ) is False


def test_rmb_provider_and_port():
    assert is_rmb_provider("rmb") is True
    assert is_rmb_provider("custom", "http://127.0.0.1:8787/v1") is True
    assert is_rmb_base_url("http://127.0.0.1:8787/v1") is True
    assert is_rmb_base_url("https://api.example.com/rmb-proxy") is False


def test_force_path_only_only_for_rmb_chat():
    assert force_path_only_images({"llm_provider": "rmb"}) is True
    assert force_path_only_images({"llm_provider": "openai"}) is False
    assert force_path_only_images(
        {"llm_provider": "openai"}, provider="openai"
    ) is False


def test_silent_context_rmb_only():
    assert silent_context_for_local_agent({"llm_provider": "rmb"}) is True
    assert silent_context_for_local_agent({"llm_provider": "openai"}) is False
    assert silent_context_for_local_agent({"llm_provider": "llamacpp"}) is False


def test_leftover_rmb_provider_does_not_skip_vision_when_host_is_off(
    tmp_path, monkeypatch
):
    """config.toml llm_provider=rmb is not a call-on while 8787 is closed."""
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.is_starting", lambda: False, raising=False
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.managed_process_alive", lambda: False, raising=False
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.mode.rmb_server_running", lambda home_dir=None: False
    )
    st = merge_state(load_rmb_json(str(tmp_path)))
    st["enabled"] = True
    st["auto_start"] = False
    st["vision_suspended"] = False
    save_rmb_json(st, str(tmp_path))
    assert should_skip_vision_stack(
        {
            "home_dir": str(tmp_path),
            "llm_provider": "rmb",
            "llm_base_url": "http://127.0.0.1:8787/v1",
        }
    ) is False
    st["auto_start"] = True
    save_rmb_json(st, str(tmp_path))
    assert should_skip_vision_stack(
        {"home_dir": str(tmp_path), "llm_provider": "xai"}
    ) is True


def test_vision_suspended_skips_stack(monkeypatch):
    home = tempfile.mkdtemp(prefix="rmb-mode-")
    monkeypatch.setenv("REMEDY_HOME", home)
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.is_starting", lambda: False, raising=False
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.managed_process_alive", lambda: False, raising=False
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.mode.rmb_server_running", lambda home_dir=None: False
    )
    st = merge_state(load_rmb_json(home))
    st["vision_suspended"] = True
    save_rmb_json(st, home)
    assert should_skip_vision_stack(
        {"home_dir": home, "llm_provider": "openai"}
    ) is True
    # stop with no resume should clear for heal path via explicit clear
    st["vision_suspended"] = False
    save_rmb_json(st, home)
    assert should_skip_vision_stack(
        {"home_dir": home, "llm_provider": "openai"}
    ) is False


def test_stop_accepts_resume_vision_false():
    home = tempfile.mkdtemp(prefix="rmb-stop-")
    r = stop_rmb_server(home_dir=home, resume_vision=False)
    assert r.get("ok") is True
    assert r.get("vision_suspended") is False


def test_session_brief_store_is_per_session():
    from types import SimpleNamespace

    from remedy.memory.harness.send_policy import _ensure_session_brief

    rt = SimpleNamespace(_session_brief=None, _session_briefs={}, _session_id="a")
    b1 = _ensure_session_brief(rt, "sess-a")
    b1.intent = "alpha"
    rt._session_id = "sess-b"
    b2 = _ensure_session_brief(rt, "sess-b")
    assert b2 is not b1
    assert b2.intent == ""
    b2.intent = "beta"
    b1b = _ensure_session_brief(rt, "sess-a")
    assert b1b.intent == "alpha"
    assert rt._session_briefs["sess-a"].intent == "alpha"
    assert rt._session_briefs["sess-b"].intent == "beta"


def test_safe_insert_and_soft_brief():
    from remedy.memory.harness.brief import SessionBrief
    from remedy.memory.harness.send_policy import (
        _safe_insert_before_last,
        _soft_inject_brief_pointer,
    )

    msgs: list[dict] = []
    _safe_insert_before_last(msgs, "only")
    assert len(msgs) == 1
    msgs = [{"role": "user", "content": "hi"}]
    _safe_insert_before_last(msgs, "sys")
    assert msgs[0]["role"] == "system"
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    _safe_insert_before_last(msgs, "mid")
    assert msgs[1]["content"] == "mid"

    brief = SessionBrief(session_id="x", intent="ship RMB", decisions=["use llama"])
    m2 = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "go"},
    ]
    assert _soft_inject_brief_pointer(m2, brief, silent=True) is True
    assert any("Working memory" in str(m.get("content") or "") for m in m2)
