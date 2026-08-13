"""RMB hardware autofit: default plan, lock, OOM downgrade, live n_ctx."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from remedy.runtime.rmb.autofit import (
    AutofitPlan,
    HardwareProbe,
    classify_start_failure,
    downgrade_plan,
    estimate_model_arch,
    kv_bytes_per_token,
    plan_autofit,
    plan_from_state,
    should_autofit,
    snap_ctx,
)
from remedy.runtime.rmb.catalog import RMB_PROFILES
from remedy.runtime.rmb.config import default_state
from remedy.runtime.rmb.service import _build_cmd, apply_rmb_settings, binary_supports_cache_reuse


def _hw(*, nvidia=True, vram=12288, free=10000, ram=32768, cpus=12, name="RTX"):
    return HardwareProbe(
        nvidia=nvidia,
        vram_total_mb=vram,
        vram_free_mb=free,
        gpu_name=name,
        ram_total_mb=ram,
        ram_avail_mb=ram // 2,
        cpu_count=cpus,
    )


def test_default_profile_is_autofit():
    st = default_state()
    assert st["profile"] == "autofit"
    assert st["autofit"] is True
    assert st["autofit_locked"] is False
    assert st["cache_reuse"] == 256
    assert "autofit" in RMB_PROFILES
    assert RMB_PROFILES["autofit"]["ctx_size"] == 0


def test_should_autofit_factory_and_lock():
    assert should_autofit(default_state()) is True
    # Legacy factory agent + 8k + all layers → autofit so existing installs upgrade
    assert should_autofit({"profile": "agent", "ctx_size": 8192, "n_gpu_layers": -1}) is True
    assert should_autofit({"profile": "turbo", "ctx_size": 4096, "n_gpu_layers": -1}) is False
    assert should_autofit({"profile": "quality", "ctx_size": 16384, "n_gpu_layers": -1}) is False
    assert should_autofit({"profile": "autofit", "autofit_locked": True, "ctx_size": 16384}) is False
    assert should_autofit({"autofit": False, "profile": "agent", "ctx_size": 8192}) is False
    # User customized ctx while on agent
    assert should_autofit({"profile": "agent", "ctx_size": 32768, "n_gpu_layers": -1}) is False


def test_plan_7b_on_12gb_full_gpu_large_ctx(tmp_path):
    model = tmp_path / "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
    model.write_bytes(b"0" * int(4.7 * 1024 * 1024))  # ~4.7 MB stand-in; name drives arch
    # Fake a realistic weight size without writing 5GB
    hw = _hw(vram=12288)
    with patch("remedy.runtime.rmb.autofit.estimate_model_arch") as est:
        from remedy.runtime.rmb.autofit import ModelArch

        est.return_value = ModelArch(
            size_label="7b",
            n_params_b=7.0,
            n_layer=28,
            n_kv_head=4,
            head_dim=128,
            weight_bytes=int(4.7 * 1024**3),
            family="qwen2",
            source="test",
        )
        plan = plan_autofit(model, hardware=hw)
    assert plan.n_gpu_layers == -1
    assert plan.ctx_size >= 16384
    assert plan.flash_attn is True
    assert plan.target in ("gpu_full", "gpu_q8")
    assert plan.parallel == 1


def test_plan_14b_on_8gb_does_not_full_offload_32k():
    hw = _hw(vram=8192, free=6000, name="8GB")
    with patch("remedy.runtime.rmb.autofit.estimate_model_arch") as est:
        from remedy.runtime.rmb.autofit import ModelArch

        est.return_value = ModelArch(
            size_label="14b",
            n_params_b=14.0,
            n_layer=48,
            n_kv_head=8,
            head_dim=128,
            weight_bytes=int(9.0 * 1024**3),
            family="qwen2",
            source="test",
        )
        plan = plan_autofit(Path("Qwen2.5-Coder-14B-Q4_K_M.gguf"), hardware=hw)
    # Weights alone are ~9GB; 8GB card cannot full-offload 32k
    assert plan.ctx_size <= 8192
    assert plan.target in ("gpu_partial", "cpu", "gpu_q8")
    if plan.target == "gpu_partial":
        assert plan.n_gpu_layers != -1 or plan.cache_type in ("q8_0", "q4_0")
    if plan.target == "cpu":
        assert plan.n_gpu_layers == 0
        assert plan.flash_attn is False


def test_plan_no_gpu_uses_cpu():
    hw = _hw(nvidia=False, vram=0, free=0, ram=16384)
    with patch("remedy.runtime.rmb.autofit.estimate_model_arch") as est:
        from remedy.runtime.rmb.autofit import ModelArch

        est.return_value = ModelArch(
            size_label="7b",
            n_params_b=7.0,
            n_layer=28,
            n_kv_head=4,
            head_dim=128,
            weight_bytes=int(4.7 * 1024**3),
            family="qwen2",
            source="test",
        )
        plan = plan_autofit(Path("Qwen2.5-Coder-7B.gguf"), hardware=hw)
    assert plan.n_gpu_layers == 0
    assert plan.flash_attn is False
    assert plan.target == "cpu"
    assert plan.ctx_size >= 4096
    assert plan.threads >= 1


def test_last_good_seeds_conservative_plan(tmp_path):
    model = tmp_path / "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
    model.write_bytes(b"gguf")
    hw = _hw(vram=12288)
    last = {
        "model_path": str(model),
        "vram_total_mb": 12288,
        "ctx_size": 8192,
        "n_gpu_layers": -1,
        "cache_type": "q8_0",
        "flash_attn": True,
        "batch_size": 1024,
        "ubatch_size": 256,
        "target": "gpu_q8",
    }
    with patch("remedy.runtime.rmb.autofit.estimate_model_arch") as est:
        from remedy.runtime.rmb.autofit import ModelArch

        est.return_value = ModelArch(
            size_label="7b",
            n_params_b=7.0,
            n_layer=28,
            n_kv_head=4,
            head_dim=128,
            weight_bytes=int(4.7 * 1024**3),
            family="qwen2",
            source="test",
        )
        plan = plan_autofit(model, hardware=hw, last_good=last)
    assert plan.ctx_size == 8192
    assert plan.cache_type == "q8_0"
    assert "last_good_fit" in plan.reasons


def test_downgrade_ladder_oom():
    plan = AutofitPlan(
        ctx_size=32768,
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
        weight_mb=4800,
        estimated_used_mb=7500,
        reasons=("test",),
    )
    p1 = downgrade_plan(plan, "oom")
    assert p1 is not None and p1.cache_type == "q8_0"
    p2 = downgrade_plan(p1, "oom")
    assert p2 is not None and p2.ctx_size < p1.ctx_size
    p3 = downgrade_plan(
        AutofitPlan(
            ctx_size=4096,
            n_gpu_layers=-1,
            cache_type="q4_0",
            flash_attn=True,
            batch_size=512,
            ubatch_size=128,
            threads=0,
            cache_reuse=0,
            parallel=1,
            target="gpu_q8",
            vram_budget_mb=4000,
            kv_mb=200,
            weight_mb=4800,
            estimated_used_mb=5000,
        ),
        "oom",
    )
    assert p3 is not None and (p3.n_gpu_layers != -1 or p3.flash_attn is False)


def test_downgrade_flash_and_unknown_flag():
    plan = AutofitPlan(
        ctx_size=8192,
        n_gpu_layers=-1,
        cache_type="q8_0",
        flash_attn=True,
        batch_size=1024,
        ubatch_size=256,
        threads=0,
        cache_reuse=256,
        parallel=1,
        target="gpu_q8",
        vram_budget_mb=8000,
        kv_mb=400,
        weight_mb=4800,
        estimated_used_mb=6000,
    )
    fa = downgrade_plan(plan, "flash_attn")
    assert fa is not None and fa.flash_attn is False
    uf = downgrade_plan(plan, "unknown_flag")
    assert uf is not None and uf.cache_reuse == 0


def test_classify_start_failure():
    assert classify_start_failure("CUDA error: out of memory", exit_code=1) == "oom"
    assert classify_start_failure("failed to init flash attn", exit_code=1) == "flash_attn"
    assert classify_start_failure("error: unknown argument --cache-reuse") == "unknown_flag"
    assert classify_start_failure("", timed_out=True) == "timeout"
    assert classify_start_failure("", exit_code=1) == "oom"


def test_estimate_arch_from_filename():
    arch = estimate_model_arch(Path("Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf"))
    assert arch.size_label == "14b"
    assert arch.n_layer == 48
    assert arch.n_kv_head == 8
    assert kv_bytes_per_token(arch, "") > kv_bytes_per_token(arch, "q8_0")


def test_snap_ctx():
    assert snap_ctx(20000) == 16384
    assert snap_ctx(4096) == 4096
    assert snap_ctx(1000) == 4096


def test_apply_profile_autofit_unlocks(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.is_running", lambda *a, **k: False
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.managed_process_alive", lambda: False
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.start_rmb_server",
        lambda **k: {"ok": True, "ctx_size": 16384},
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.stop_rmb_server", lambda **k: {"ok": True}
    )
    from remedy.runtime.rmb.config import merge_state, save_rmb_json

    save_rmb_json(
        merge_state(
            {
                "enabled": True,
                "profile": "agent",
                "ctx_size": 8192,
                "autofit_locked": True,
            }
        ),
        str(tmp_path),
    )
    apply_rmb_settings({"profile": "autofit"}, home_dir=str(tmp_path), live=False)
    from remedy.runtime.rmb.config import load_rmb_json

    st = merge_state(load_rmb_json(str(tmp_path)))
    assert st["autofit"] is True
    assert st["autofit_locked"] is False
    assert st["profile"] == "autofit"


def test_apply_ctx_locks_autofit(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.is_running", lambda *a, **k: False
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.managed_process_alive", lambda: False
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.start_rmb_server",
        lambda **k: {"ok": True, "ctx_size": 4096},
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.stop_rmb_server", lambda **k: {"ok": True}
    )
    apply_rmb_settings(
        {"ctx_size": 4096, "enabled": True},
        home_dir=str(tmp_path),
        live=False,
    )
    from remedy.runtime.rmb.config import load_rmb_json, merge_state

    st = merge_state(load_rmb_json(str(tmp_path)))
    assert st["autofit_locked"] is True
    assert int(st["ctx_size"]) == 4096


def test_plan_from_state_honors_lock():
    st = {"ctx_size": 12288, "n_gpu_layers": 20, "cache_type": "q8_0", "flash_attn": False}
    plan = plan_from_state(st, Path("x-7b.gguf"), hardware=_hw())
    assert plan.ctx_size == 12288
    assert plan.n_gpu_layers == 20
    assert plan.cache_type == "q8_0"
    assert plan.target == "manual"


def test_build_cmd_cache_reuse_when_binary_advertises(tmp_path):
    fake_bin = tmp_path / "llama-server.exe"
    fake_bin.write_bytes(b"stub")
    (tmp_path / "llama-common.dll").write_bytes(b"xx --cache-reuse yy")
    from remedy.runtime.rmb import service as svc

    svc._flag_cap_cache.clear()
    assert binary_supports_cache_reuse(fake_bin) is True
    model = tmp_path / "Foo-7B.gguf"
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
        flash_attn=True,
        cache_reuse=256,
    )
    assert "--cache-reuse" in cmd
    assert cmd[cmd.index("--cache-reuse") + 1] == "256"


def test_slim_system_keeps_more_context_on_32k():
    from remedy.core.local_agent_optimize import slim_system_for_local

    blob = "Project workspace: C:/proj\n" + ("file note\n" * 800)
    small = slim_system_for_local(
        "You are Remedy.",
        blob,
        provider="rmb",
        model="Qwen2.5-Coder-7B",
        window=8192,
    )
    big = slim_system_for_local(
        "You are Remedy.",
        blob,
        provider="rmb",
        model="Qwen2.5-Coder-7B",
        window=32768,
    )
    assert "n_ctx=32768" in big
    assert len(big) > len(small)
    assert "[Local agent mode" in big
