"""VRAM planner, local-server discovery, routing, and schedule for mind+muscle."""

from __future__ import annotations

from dataclasses import replace

from remedy.core import organism_knobs as knobs
from remedy.runtime.mind_engine import mind_n_gpu_layers
from remedy.runtime.rmb.autofit import HardwareProbe
from remedy.runtime.rmb.coresidence import (
    CoresidencePlan,
    GpuSchedule,
    LocalMuscleServer,
    already_served,
    apply_plan_to_rmb_state,
    estimate_difficulty,
    gpu_policy,
    plan_coresidence,
    route_request,
)


def _gpu(vram_mb: int, **kwargs: object) -> HardwareProbe:
    return HardwareProbe(
        nvidia=True,
        vram_total_mb=int(vram_mb),
        vram_free_mb=int(vram_mb) - 512,
        gpu_name=str(kwargs.get("gpu_name") or "RTX 3080"),
        ram_total_mb=int(kwargs.get("ram_total_mb") or 32_000),
        ram_avail_mb=int(kwargs.get("ram_avail_mb") or 24_000),
        cpu_count=int(kwargs.get("cpu_count") or 8),
        gpu_vendor="nvidia",
        gpu_backend="cuda",
    )


def _cpu() -> HardwareProbe:
    return HardwareProbe(
        nvidia=False,
        vram_total_mb=0,
        vram_free_mb=0,
        gpu_name="",
        ram_total_mb=16_000,
        ram_avail_mb=12_000,
        cpu_count=8,
        gpu_vendor="",
        gpu_backend="",
    )


def test_knob_defaults() -> None:
    table = knobs.effective_table({})
    assert table["muscle_gpu_policy"] == "auto"
    assert table["muscle_schedule"] == "auto"
    assert table["muscle_route"] == "auto"
    assert table["muscle_discover"] is True
    assert table["muscle_vram_headroom_mib"] == 768
    assert table["muscle_kv_cache"] == "auto"
    assert table["muscle_flash_attn"] is True
    assert table["muscle_spec"] == "auto"
    keys = {item["key"] for item in knobs.schema()}
    assert "muscle_gpu_policy" in keys
    assert "muscle_discover" in keys


def test_mind_keep_gpu_forces_keep_mind_policy() -> None:
    assert gpu_policy({"advanced": {"mind_keep_gpu": True}}) == "keep_mind"
    assert gpu_policy({"advanced": {"muscle_gpu_policy": "yield_mind"}}) == "yield_mind"


def test_24gb_card_shares_mind_and_9b() -> None:
    plan = plan_coresidence(
        hardware=_gpu(24576),
        muscle_model_id="qwen35-9b",
        servers=[],
    )
    assert plan.mode == "share"
    assert plan.mind_n_gpu_layers == -1
    assert plan.muscle_n_gpu_layers == -1
    assert plan.schedule == "overlap"
    assert plan.skip_rmb_start is False


def test_12gb_card_keeps_mind_and_partially_offloads_dense_9b() -> None:
    plan = plan_coresidence(
        hardware=_gpu(12288),
        muscle_model_id="qwen35-9b",
        servers=[],
    )
    # The always-on mind stays on the GPU; the 9B takes the leftover layers.
    assert plan.mode == "share"
    assert plan.mind_n_gpu_layers == -1
    assert plan.muscle_n_gpu_layers >= 4
    assert plan.muscle_n_gpu_layers != -1


def test_8gb_card_yields_mind_for_dense_9b() -> None:
    plan = plan_coresidence(
        hardware=_gpu(8192, gpu_name="RTX 3070 laptop"),
        muscle_model_id="qwen35-9b",
        servers=[],
    )
    assert plan.mode == "yield_mind"
    assert plan.mind_n_gpu_layers == 0
    assert plan.muscle_n_gpu_layers == -1


def test_12gb_share_policy_keeps_mind_and_offloads_muscle() -> None:
    plan = plan_coresidence(
        hardware=_gpu(12288),
        muscle_model_id="qwen35-9b",
        servers=[],
        cfg={"advanced": {"muscle_gpu_policy": "share"}},
    )
    assert plan.mode == "share"
    assert plan.mind_n_gpu_layers == -1
    assert plan.muscle_n_gpu_layers >= 4
    assert plan.muscle_n_gpu_layers != -1  # not a full offload of a 7.5GB 9B


def test_12gb_moe_muscle_shares_with_mind() -> None:
    plan = plan_coresidence(
        hardware=_gpu(12288),
        muscle_model_id="qwen36-35b-a3b",
        servers=[],
    )
    assert plan.mode == "share"
    assert plan.mind_n_gpu_layers == -1
    assert plan.muscle_n_cpu_moe >= 1


def test_keep_mind_on_tight_card_puts_muscle_on_cpu() -> None:
    plan = plan_coresidence(
        hardware=_gpu(6144),
        muscle_model_id="qwen35-9b",
        servers=[],
        cfg={"advanced": {"mind_keep_gpu": True}},
    )
    assert plan.policy == "keep_mind"
    assert plan.mind_n_gpu_layers == -1
    assert plan.mode in {"muscle_cpu", "share"}
    if plan.mode == "muscle_cpu":
        assert plan.muscle_n_gpu_layers == 0


def test_no_gpu_both_stay_on_cpu() -> None:
    plan = plan_coresidence(hardware=_cpu(), muscle_model_id="qwen35-9b", servers=[])
    assert plan.mode == "mind_cpu"
    assert plan.mind_n_gpu_layers == 0
    assert plan.muscle_n_gpu_layers == 0



def test_already_served_compacts_catalog_and_ollama_names() -> None:
    from remedy.runtime.rmb.coresidence import already_served, LocalMuscleServer

    srv = LocalMuscleServer(
        kind="ollama",
        base_url="http://127.0.0.1:11434/v1",
        port=11434,
        healthy=True,
        models=("qwen2.5:7b",),
    )
    assert already_served("qwen25-7b", [srv]) is srv
    fourteen = LocalMuscleServer(
        kind="ollama",
        base_url="http://127.0.0.1:11434/v1",
        port=11434,
        healthy=True,
        models=("qwen2.5:14b",),
    )
    assert already_served("qwen25-7b", [fourteen]) is None


def test_external_server_avoids_double_load() -> None:
    srv = LocalMuscleServer(
        kind="ollama",
        base_url="http://127.0.0.1:11434/v1",
        port=11434,
        healthy=True,
        models=("qwen3.5:9b", "Qwen3.5-9B-Q6_K"),
        estimated_vram_mib=7500,
    )
    assert already_served("qwen35-9b", [srv]) is srv
    plan = plan_coresidence(
        hardware=_gpu(12288),
        muscle_model_id="qwen35-9b",
        servers=[srv],
    )
    assert plan.skip_rmb_start is True
    assert plan.mode == "external"
    assert plan.reuse_base_url.endswith("/v1")
    assert plan.reserved_external_mib >= 7000


def test_apply_plan_respects_owner_layer_lock() -> None:
    plan = plan_coresidence(
        hardware=_gpu(24576),
        muscle_model_id="qwen35-9b",
        servers=[],
    )
    state = apply_plan_to_rmb_state({"n_gpu_layers_owner": True, "n_gpu_layers": 12}, plan)
    assert state["n_gpu_layers"] == 12
    assert state["last_coresidence"]["mode"] == "share"


def test_mind_n_gpu_layers_honors_explicit_policy() -> None:
    cfg = {"advanced": {"muscle_gpu_policy": "yield_mind"}}
    assert mind_n_gpu_layers(requested=-1, rmb_running=True, cfg=cfg) == 0
    assert mind_n_gpu_layers(requested=-1, rmb_running=False, cfg=cfg) == -1
    keep = {"advanced": {"mind_keep_gpu": True}}
    # Planner still runs; keep_mind on a CPU-only test box yields 0 because
    # there is no usable GPU. Inject a plan to assert the keep path.
    plan = plan_coresidence(
        hardware=_gpu(12288),
        muscle_model_id="qwen35-9b",
        servers=[],
        cfg=keep,
    )
    assert mind_n_gpu_layers(requested=-1, rmb_running=True, cfg=keep, plan=plan) == plan.mind_n_gpu_layers
    assert plan.mind_n_gpu_layers == -1


def test_difficulty_and_auto_route() -> None:
    assert estimate_difficulty("ok", task_kind="continuity_core") < 0.3
    hard = estimate_difficulty(
        "Please implement a multi-file refactor of the debugger and fix this stack trace:\n```python\n" + "x" * 2000,
        task_kind="coding",
    )
    assert hard >= 0.8
    easy = route_request("hi", task_kind="brief_update", has_frontier=False, rmb_running=False, servers=[])
    assert easy.target == "mind"
    mid = route_request(
        "def foo():\n    pass\nPlease refactor this module.",
        task_kind="coding",
        rmb_running=True,
        has_frontier=True,
        servers=[],
    )
    assert mid.target == "rmb"
    hardest = route_request(
        "Please implement a multi-file architecture refactor and formally prove the new algorithm. " + "code\n" * 400,
        task_kind="coding",
        rmb_running=True,
        has_frontier=True,
        servers=[],
    )
    assert hardest.target == "frontier"
    forced = route_request("anything", cfg={"advanced": {"muscle_route": "mind"}}, rmb_running=True)
    assert forced.target == "mind"


def test_schedule_serialize_holds_lock() -> None:
    plan = CoresidencePlan(
        policy="auto",
        mode="share",
        mind_n_gpu_layers=-1,
        muscle_n_gpu_layers=8,
        muscle_n_cpu_moe=0,
        mind_ctx=8192,
        muscle_ctx=8192,
        mind_kv="q8_0",
        muscle_kv="q8_0",
        muscle_quant_hint="q6_k",
        schedule="serialize",
        budget_mib=8000,
        reserved_external_mib=0,
        mind_used_mib=4000,
        muscle_used_mib=3500,
        reason="test",
    )
    with GpuSchedule("mind", plan=plan):
        # overlap plan is a no-op even while serialize is held? we only test enter/exit.
        pass
    overlap = replace(plan, schedule="overlap")
    with GpuSchedule("muscle", plan=overlap):
        pass


def test_discovery_skips_when_disabled() -> None:
    from remedy.runtime.rmb.coresidence import discover_local_servers

    assert discover_local_servers(cfg={"advanced": {"muscle_discover": False}}) == []
