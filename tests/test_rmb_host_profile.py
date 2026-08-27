"""RMB auto-load: GGUF → host knobs without user finesse."""

from __future__ import annotations

from pathlib import Path

from remedy.runtime.rmb.host_profile import (
    apply_host_profile_to_state,
    detect_gguf_host_profile,
    model_switch_should_refit,
    overlay_owner_on_profile,
)
from remedy.runtime.rmb.service import _build_cmd


def test_detect_qwen35_coder_does_not_force_thinking_off():
    p = Path("qwen3.5-4b-agentic-coder-v4.i1-IQ4_XS.gguf")
    prof = detect_gguf_host_profile(p)
    assert prof["qwen3_family"] is True
    assert prof["coder"] is True
    assert prof["use_jinja"] is True
    assert prof["no_mmap"] is False
    assert prof["qwen_thinking_toggle"] is True
    assert prof["chat_template_kwargs"] is None
    assert prof["reasoning_budget"] is None
    assert "thinking" in prof["summary"]
    assert "thinking off" not in prof["summary"]
    assert prof["chat_style"] == "instruct"
    on = overlay_owner_on_profile(prof, {})
    assert on["thinking_mode"] == "on"
    assert on["chat_template_kwargs"] == '{"enable_thinking": true}'
    off = overlay_owner_on_profile(prof, {"thinking": "off"})
    assert off["thinking_mode"] == "off"
    assert off["chat_template_kwargs"] == '{"enable_thinking": false}'
    assert "thinking off" in off["summary"]


def test_detect_r1_thinking_does_not_cap_budget_by_default():
    p = Path("DeepSeek-R1-STEM-Coder-7B.Q6_K.gguf")
    prof = detect_gguf_host_profile(p)
    assert prof["thinking"] is True
    assert prof["coder"] is True
    assert prof["use_jinja"] is True
    assert prof["reasoning_budget"] is None
    assert prof["chat_style"] == "thinking"
    assert any("thinking" in w.lower() or "reasoning" in w.lower() for w in prof["warnings"])
    off = overlay_owner_on_profile(prof, {"thinking": "off"})
    assert off["reasoning_budget"] == 0


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
    assert prof["chat_template_kwargs"] is None
    assert prof["qwen_thinking_toggle"] is True


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


def test_apply_settings_thinking_off_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    from remedy.runtime.rmb.config import load_rmb_json, merge_state, save_rmb_json
    from remedy.runtime.rmb.service import apply_rmb_settings

    save_rmb_json(merge_state({}), tmp_path)
    out = apply_rmb_settings(
        {"thinking": "off", "enable_mtp": False, "n_cpu_moe": 99},
        home_dir=str(tmp_path),
        live=False,
    )
    assert out.get("ok") is not False
    st = load_rmb_json(tmp_path)
    assert st.get("thinking") == "off"
    assert st.get("enable_mtp") is False
    assert int(st.get("n_cpu_moe") or 0) == 99


def test_overlay_owner_can_disable_mtp():
    prof = detect_gguf_host_profile(Path("Qwopus3.5-9B-Coder-MTP-Q4_K_M.gguf"))
    assert prof["mtp"] is True
    off = overlay_owner_on_profile(prof, {"enable_mtp": False})
    assert off["mtp"] is False
    assert off["force_parallel_1"] is False
    assert off.get("mtp_owner_off") is True


def test_apply_host_profile_preserves_user_jinja():
    # Owner explicitly set use_jinja in Settings → the marker pins it.
    state: dict = {"use_jinja": False, "use_jinja_owner": True}
    prof = detect_gguf_host_profile(Path("Qwen3.5-9B-Q4_K_M.gguf"))
    apply_host_profile_to_state(state, prof, preserve={"use_jinja"})
    assert state["use_jinja"] is False


def test_apply_host_profile_heals_stale_auto_jinja_without_owner_marker():
    # A use_jinja=False auto-written by 0.41.4 (no owner marker) must not
    # silently break the chat template of the next instruct GGUF.
    state: dict = {"use_jinja": False}
    prof = detect_gguf_host_profile(Path("Qwen3.5-9B-Q4_K_M.gguf"))
    apply_host_profile_to_state(state, prof, preserve={"use_jinja"})
    assert state["use_jinja"] is True


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
                "use_jinja_owner": True,
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
    # Owner jinja/mmap survive a GGUF switch (auto-load does not clobber).
    assert st.get("use_jinja") is False
    assert st.get("no_mmap") is True
    ha = st.get("host_auto") or {}
    assert ha.get("qwen3_family") is True
    assert ha.get("thinking_mode") == "on"
    assert ha.get("chat_template_kwargs") == '{"enable_thinking": true}'


def test_build_cmd_default_keeps_thinking_on(tmp_path):
    fake_bin = tmp_path / "llama-server.exe"
    fake_bin.write_bytes(b"stub")
    (tmp_path / "llama-common.dll").write_bytes(
        b"xx --chat-template-kwargs --reasoning-budget --reasoning off yy"
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
    assert "--reasoning" not in cmd
    assert "--chat-template-kwargs" in cmd
    assert cmd[cmd.index("--chat-template-kwargs") + 1] == '{"enable_thinking": true}'
    assert "--reasoning-budget" not in cmd


def test_build_cmd_thinking_off_emits_reasoning_off(tmp_path):
    fake_bin = tmp_path / "llama-server.exe"
    fake_bin.write_bytes(b"stub")
    (tmp_path / "llama-common.dll").write_bytes(
        b"xx --chat-template-kwargs --reasoning-budget --reasoning off yy"
    )
    from remedy.runtime.rmb import service as svc

    svc._flag_cap_cache.clear()
    model = tmp_path / "Qwen3.5-4B-Instruct.gguf"
    model.write_bytes(b"0")
    prof = overlay_owner_on_profile(
        detect_gguf_host_profile(model), {"thinking": "off"}
    )
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
        host_profile=prof,
    )
    assert "--reasoning" in cmd
    assert cmd[cmd.index("--reasoning") + 1] == "off"


def test_build_cmd_emits_reasoning_budget_for_r1_when_thinking_off(tmp_path):
    fake_bin = tmp_path / "llama-server.exe"
    fake_bin.write_bytes(b"stub")
    (tmp_path / "llama-common.dll").write_bytes(b"--reasoning-budget --chat-template-kwargs")
    from remedy.runtime.rmb import service as svc

    svc._flag_cap_cache.clear()
    model = tmp_path / "DeepSeek-R1-Distill-7B.gguf"
    model.write_bytes(b"0")
    on_cmd = _build_cmd(
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
    assert "--reasoning-budget" not in on_cmd
    prof = overlay_owner_on_profile(
        detect_gguf_host_profile(model), {"thinking": "off"}
    )
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
        host_profile=prof,
    )
    assert "--reasoning-budget" in cmd
    assert cmd[cmd.index("--reasoning-budget") + 1] == "0"


def test_thinking_off_annotation_only_on_models_with_a_knob():
    """A plain instruct GGUF has no thinking mode — the card must not claim one."""
    prof = detect_gguf_host_profile(Path("Kanana-2-9B-instruct-Q4_K_M.gguf"))
    assert "thinking" not in prof["summary"]
    off = overlay_owner_on_profile(prof, {"thinking": "off"})
    assert off["thinking_mode"] == "off"
    assert "thinking" not in off["summary"]
    # Toggling back on must not rewrite the card into claiming a thinking model.
    healed = overlay_owner_on_profile(off, {"thinking": "on"})
    assert "thinking" not in healed["summary"]


def test_thinking_off_summary_heals_legacy_corrupted_card():
    """0.41.5 wrote 'instruct · jinja · thinking off' onto non-thinking cards."""
    stale = dict(detect_gguf_host_profile(Path("Kanana-2-9B-instruct-Q4_K_M.gguf")))
    stale["summary"] = "instruct · jinja · thinking off"
    on = overlay_owner_on_profile(stale, {"thinking": "on"})
    assert "thinking" not in on["summary"]
    off = overlay_owner_on_profile(stale, {"thinking": "off"})
    assert "thinking" not in off["summary"]


def test_overlay_owner_mtp_off_clears_armed_and_marks_summary():
    prof = detect_gguf_host_profile(Path("Qwopus3.5-9B-Coder-MTP-Q4_K_M.gguf"))
    recorded = {**prof, "mtp_armed": True}
    off = overlay_owner_on_profile(recorded, {"enable_mtp": False})
    assert off["mtp"] is False
    assert off["mtp_armed"] is False
    assert "MTP off" in off["summary"]
    back_on = overlay_owner_on_profile(off, {"enable_mtp": True})
    assert "MTP off" not in back_on["summary"]


def test_overlay_owner_n_cpu_moe_minus_one_forces_gpu():
    prof = detect_gguf_host_profile(Path("Qwen3.5-30B-A3B-Q4_K_M.gguf"))
    out = overlay_owner_on_profile(prof, {"n_cpu_moe": -1})
    assert out["n_cpu_moe"] == 0
    assert out["n_cpu_moe_owner"] is True


def test_overlay_owner_marks_typed_draft():
    prof = detect_gguf_host_profile(Path("Qwen3.5-9B-Q4_K_M.gguf"))
    out = overlay_owner_on_profile(prof, {"model_draft": r"C:\x\draft.gguf"})
    assert out["model_draft"] == r"C:\x\draft.gguf"
    assert out["model_draft_owner"] is True


def test_normalize_thinking_vocab_and_validation():
    from remedy.runtime.rmb.host_profile import (
        normalize_thinking,
        thinking_value_known,
    )

    for word in ("off", "false", "0", "no", "disable", "disabled", "none", "never"):
        assert normalize_thinking(word) == "off"
    for word in ("on", "true", "1", "yes", "auto", ""):
        assert normalize_thinking(word) == "on"
    assert thinking_value_known("disable") is True
    assert thinking_value_known("on") is True
    # Typos are not silently mapped to on — entry points reject them.
    assert thinking_value_known("of") is False
    assert thinking_value_known("disbled") is False


def test_apply_settings_rejects_unknown_thinking_word(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    from remedy.runtime.rmb.config import load_rmb_json, merge_state, save_rmb_json
    from remedy.runtime.rmb.service import apply_rmb_settings

    save_rmb_json(merge_state({}), tmp_path)
    out = apply_rmb_settings({"thinking": "disbled"}, home_dir=str(tmp_path), live=False)
    assert out.get("rejected_settings") == {"thinking": "disbled"}
    st = load_rmb_json(tmp_path)
    assert st.get("thinking") == "on"  # unchanged default, not flipped by a typo


def test_apply_settings_marks_use_jinja_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    from remedy.runtime.rmb.config import load_rmb_json, merge_state, save_rmb_json
    from remedy.runtime.rmb.service import apply_rmb_settings

    save_rmb_json(merge_state({}), tmp_path)
    apply_rmb_settings({"use_jinja": False}, home_dir=str(tmp_path), live=False)
    st = load_rmb_json(tmp_path)
    assert st.get("use_jinja") is False
    assert st.get("use_jinja_owner") is True
    # "auto" hands the knob back to detection.
    apply_rmb_settings({"use_jinja": "auto"}, home_dir=str(tmp_path), live=False)
    st = load_rmb_json(tmp_path)
    assert st.get("use_jinja_owner") is False


def test_autofit_apply_plan_keeps_owner_parallel():
    from remedy.runtime.rmb.autofit import AutofitPlan, apply_plan_to_state

    plan = AutofitPlan(
        ctx_size=8192,
        n_gpu_layers=-1,
        cache_type="",
        flash_attn=True,
        batch_size=2048,
        ubatch_size=512,
        threads=0,
        cache_reuse=256,
        parallel=1,
        target="gpu_full",
        vram_budget_mb=10000,
        kv_mb=1800,
        weight_mb=4000,
        estimated_used_mb=6000,
    )
    state = {"parallel": 4}
    apply_plan_to_state(state, plan)
    # Owner slot count survives autofit; plan slots are recorded in last_autofit.
    assert state["parallel"] == 4
    assert state["last_autofit"]["parallel"] == 1
    # Autofit never writes parallel — merge_state supplies the default.
    fresh: dict = {}
    apply_plan_to_state(fresh, plan)
    assert "parallel" not in fresh


def test_owner_flag_on_shares_thinking_off_vocab():
    from remedy.runtime.rmb.host_profile import owner_flag_on, owner_flag_value_known

    for word in ("off", "false", "0", "no", "disable", "disabled", "none", "never"):
        assert owner_flag_on(word, default=True) is False
    assert owner_flag_on("auto", default=True) is True
    assert owner_flag_value_known("disabled") is True
    assert owner_flag_value_known(True) is True
    assert owner_flag_value_known("disbled") is False


def test_reasoning_budget_cap_accepts_json_floats():
    from remedy.runtime.rmb.host_profile import reasoning_budget_cap

    assert reasoning_budget_cap(512.0) == 512
    assert reasoning_budget_cap("512.0") == 512
    assert reasoning_budget_cap("512") == 512
    assert reasoning_budget_cap(-1) is None
    assert reasoning_budget_cap("garbage") is None


def test_apply_settings_rejects_unknown_enable_mtp_word(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    from remedy.runtime.rmb.config import load_rmb_json, merge_state, save_rmb_json
    from remedy.runtime.rmb.service import apply_rmb_settings

    save_rmb_json(merge_state({}), tmp_path)
    out = apply_rmb_settings(
        {"enable_mtp": "disbled"}, home_dir=str(tmp_path), live=False
    )
    assert out.get("rejected_settings") == {"enable_mtp": "disbled"}
    st = load_rmb_json(tmp_path)
    assert st.get("enable_mtp") is True  # default untouched by a typo
    out2 = apply_rmb_settings(
        {"enable_mtp": "disabled"}, home_dir=str(tmp_path), live=False
    )
    assert out2.get("rejected_settings") is None
    st = load_rmb_json(tmp_path)
    assert st.get("enable_mtp") is False


def test_apply_settings_use_jinja_string_words(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    from remedy.runtime.rmb.config import load_rmb_json, merge_state, save_rmb_json
    from remedy.runtime.rmb.service import apply_rmb_settings

    save_rmb_json(merge_state({}), tmp_path)
    # String "off" must store False (bool("off") would store True and pin it).
    apply_rmb_settings({"use_jinja": "off"}, home_dir=str(tmp_path), live=False)
    st = load_rmb_json(tmp_path)
    assert st.get("use_jinja") is False
    assert st.get("use_jinja_owner") is True
    out = apply_rmb_settings({"use_jinja": "maybe"}, home_dir=str(tmp_path), live=False)
    assert out.get("rejected_settings") == {"use_jinja": "maybe"}
    st = load_rmb_json(tmp_path)
    assert st.get("use_jinja") is False  # unchanged by the rejected word


def test_rmb_settings_route_declares_all_owner_knobs():
    """Pydantic silently strips undeclared fields — the desktop knobs must
    survive the POST /api/rmb/settings model."""
    from remedy.interfaces.routes.rmb import RmbSettingsPatch

    body = RmbSettingsPatch(
        thinking="off",
        reasoning_budget=512,
        enable_mtp=False,
        n_cpu_moe=-1,
        spec_draft_n_max=4,
        n_gpu_layers_draft=8,
        model_draft="draft.gguf",
        use_jinja="auto",
        cache_reuse=256,
    )
    patch = body.model_dump(exclude_none=True)
    for key in (
        "thinking",
        "reasoning_budget",
        "enable_mtp",
        "n_cpu_moe",
        "spec_draft_n_max",
        "n_gpu_layers_draft",
        "model_draft",
        "use_jinja",
        "cache_reuse",
    ):
        assert key in patch, key
    assert patch["use_jinja"] == "auto"
    assert patch["n_cpu_moe"] == -1
