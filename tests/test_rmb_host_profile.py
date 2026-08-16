"""RMB auto-load: GGUF → host knobs without user finesse."""

from __future__ import annotations

from pathlib import Path

from remedy.runtime.rmb.host_profile import (
    apply_host_profile_to_state,
    detect_gguf_host_profile,
    model_switch_should_refit,
)
from remedy.runtime.rmb.service import _build_cmd


def test_detect_qwen35_coder_turns_thinking_off():
    p = Path("qwen3.5-4b-agentic-coder-v4.i1-IQ4_XS.gguf")
    prof = detect_gguf_host_profile(p)
    assert prof["qwen3_family"] is True
    assert prof["coder"] is True
    assert prof["use_jinja"] is True
    assert prof["no_mmap"] is False
    assert prof["chat_template_kwargs"] == '{"enable_thinking": false}'
    assert prof["reasoning_budget"] is None
    assert "thinking off" in prof["summary"]
    assert prof["chat_style"] == "instruct"


def test_detect_r1_thinking_caps_reasoning_budget():
    p = Path("DeepSeek-R1-STEM-Coder-7B.Q6_K.gguf")
    prof = detect_gguf_host_profile(p)
    assert prof["thinking"] is True
    assert prof["coder"] is True
    assert prof["use_jinja"] is True
    assert prof["reasoning_budget"] == 0
    assert prof["chat_style"] == "thinking"
    assert any("thinking" in w.lower() or "reasoning" in w.lower() for w in prof["warnings"])


def test_detect_base_model_warns():
    p = Path("LFM2.5-2.6B-Base.Q8_0.gguf")
    prof = detect_gguf_host_profile(p)
    assert prof["base_model"] is True
    assert prof["chat_style"] == "base"
    assert prof["thinking"] is False
    assert any("base" in w.lower() for w in prof["warnings"])


def test_status_path_skips_template_sniff(monkeypatch):
    """Status poll must not open the GGUF (17GB files on an 8s timer)."""

    def _boom(*_a, **_k):
        raise AssertionError("status path must not sniff GGUF KV")

    monkeypatch.setattr(
        "remedy.runtime.rmb.host_profile.read_gguf_chat_signals", _boom
    )
    prof = detect_gguf_host_profile(
        Path("Qwen3.5-9B-Q4_K_M.gguf"), sniff_template=False
    )
    assert prof["qwen3_family"] is True
    assert prof["chat_template_kwargs"] == '{"enable_thinking": false}'


def test_detect_kanana_instruct_is_not_thinking():
    p = Path("kanana-2-1.3b-instruct-Q8_0.gguf")
    prof = detect_gguf_host_profile(p)
    assert prof["thinking"] is False
    assert prof["instruct"] is True
    assert prof["reasoning_budget"] is None
    assert prof["chat_style"] == "instruct"
    assert "instruct" in prof["summary"]


def test_detect_vision_warns():
    p = Path("qwen3-vl-4b-heretic-Q4_K_M.gguf")
    prof = detect_gguf_host_profile(p)
    assert prof["vision"] is True
    assert prof["qwen3_family"] is True
    assert any("vision" in w.lower() or "projector" in w.lower() for w in prof["warnings"])


def test_detect_unfit_27b_on_12gb(monkeypatch):
    # Filename is enough for MTP; size comes from a stub stat so CI stays offline.
    p = Path("Qwen3.6-27B-Fable-MTP-Q4_K_M.gguf")

    class _Stat:
        st_size = 17 * 1024 * 1024 * 1024

    monkeypatch.setattr(
        "remedy.runtime.rmb.host_profile.read_gguf_chat_signals",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(Path, "stat", lambda self: _Stat())
    prof = detect_gguf_host_profile(p, hardware={"vram_total_mb": 12288})
    assert prof["mtp"] is True
    assert prof["unfit"] is True
    assert any("partial" in w.lower() or "gb" in w.lower() for w in prof["warnings"])


def test_apply_host_profile_sets_jinja_and_mmap():
    state: dict = {"use_jinja": False, "no_mmap": True, "parallel": 4}
    prof = detect_gguf_host_profile(Path("Qwen3.5-9B-Q4_K_M.gguf"))
    apply_host_profile_to_state(state, prof)
    assert state["use_jinja"] is True
    assert state["no_mmap"] is False
    assert state["host_auto"]["qwen3_family"] is True


def test_apply_host_profile_preserves_user_jinja():
    state: dict = {"use_jinja": False}
    prof = detect_gguf_host_profile(Path("Qwen3.5-9B-Q4_K_M.gguf"))
    apply_host_profile_to_state(state, prof, preserve={"use_jinja"})
    assert state["use_jinja"] is False


def test_model_switch_refits_autofit_not_turbo():
    assert model_switch_should_refit({"profile": "autofit"}) is True
    assert model_switch_should_refit({"profile": "agent"}) is True
    assert model_switch_should_refit({"profile": "turbo"}) is False
    assert model_switch_should_refit({"profile": "quality"}) is False


def test_apply_settings_new_gguf_clears_last_good_and_applies_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    from remedy.runtime.rmb.config import merge_state, save_rmb_json
    from remedy.runtime.rmb.service import apply_rmb_settings

    md = tmp_path / "rmb" / "models"
    md.mkdir(parents=True)
    a = md / "DeepSeek-R1-STEM-Coder-7B.Q6_K.gguf"
    b = md / "qwen3.5-4b-agentic-coder-v4.i1-IQ4_XS.gguf"
    a.write_bytes(b"gguf-a")
    b.write_bytes(b"gguf-b")
    save_rmb_json(
        merge_state(
            {
                "model_path": str(a),
                "model_id": a.stem,
                "use_jinja": False,
                "no_mmap": True,
                "last_good_fit": {
                    "model_path": str(a),
                    "ctx_size": 32768,
                    "n_gpu_layers": -1,
                },
                "autofit_locked": True,
                "profile": "autofit",
            }
        ),
        tmp_path,
    )
    out = apply_rmb_settings(
        {"model_path": str(b), "enabled": True},
        home_dir=str(tmp_path),
        live=False,
    )
    assert out.get("ok") is not False
    from remedy.runtime.rmb.config import load_rmb_json

    st = load_rmb_json(tmp_path)
    assert Path(str(st.get("model_path"))).name == b.name
    assert st.get("last_good_fit") is None
    assert st.get("autofit_locked") is False
    assert st.get("use_jinja") is True
    assert st.get("no_mmap") is False
    ha = st.get("host_auto") or {}
    assert ha.get("qwen3_family") is True
    assert ha.get("chat_template_kwargs") == '{"enable_thinking": false}'


def test_build_cmd_emits_thinking_kwargs_when_capable(tmp_path):
    fake_bin = tmp_path / "llama-server.exe"
    fake_bin.write_bytes(b"stub")
    (tmp_path / "llama-common.dll").write_bytes(
        b"xx --chat-template-kwargs --reasoning-budget yy"
    )
    from remedy.runtime.rmb import service as svc

    svc._flag_cap_cache.clear()
    model = tmp_path / "Qwen3.5-4B-Instruct.gguf"
    model.write_bytes(b"0")
    cmd = _build_cmd(
        fake_bin,
        model,
        host="127.0.0.1",
        port=8787,
        ctx=8192,
        ngl=-1,
        threads=0,
        parallel=1,
        flash_attn=False,
    )
    assert "--jinja" in cmd
    assert "--chat-template-kwargs" in cmd
    assert cmd[cmd.index("--chat-template-kwargs") + 1] == '{"enable_thinking": false}'
    assert "--reasoning-budget" not in cmd


def test_build_cmd_emits_reasoning_budget_for_r1(tmp_path):
    fake_bin = tmp_path / "llama-server.exe"
    fake_bin.write_bytes(b"stub")
    (tmp_path / "llama-common.dll").write_bytes(b"--reasoning-budget --chat-template-kwargs")
    from remedy.runtime.rmb import service as svc

    svc._flag_cap_cache.clear()
    model = tmp_path / "DeepSeek-R1-Distill-7B.gguf"
    model.write_bytes(b"0")
    cmd = _build_cmd(
        fake_bin,
        model,
        host="127.0.0.1",
        port=8787,
        ctx=8192,
        ngl=0,
        threads=0,
        parallel=1,
        flash_attn=False,
    )
    assert "--reasoning-budget" in cmd
    assert cmd[cmd.index("--reasoning-budget") + 1] == "0"
