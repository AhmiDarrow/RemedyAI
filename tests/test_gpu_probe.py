"""Vendor-neutral GPU probe — any card counts as home."""

from __future__ import annotations

from unittest.mock import patch

from remedy.runtime.gpu_probe import (
    GpuDevice,
    GpuSnapshot,
    _parse_cim_json,
    classify_vendor,
    looks_like_igpu,
    runtime_matches_gpu,
)
from remedy.runtime.rmb.autofit import HardwareProbe, plan_autofit


def test_classify_vendor_from_name_and_pnp() -> None:
    assert classify_vendor("Radeon RX 7800 XT") == "amd"
    assert classify_vendor("Intel Arc A770") == "intel"
    assert classify_vendor("GeForce RTX 4070") == "nvidia"
    assert classify_vendor("Unknown", "PCI\\VEN_1002&DEV_7480") == "amd"
    assert classify_vendor("Unknown", "PCI\\VEN_8086&DEV_56A0") == "intel"
    assert classify_vendor("Unknown", "PCI\\VEN_10DE&DEV_2782") == "nvidia"


def test_igpu_vs_discrete() -> None:
    assert looks_like_igpu("AMD Radeon Graphics", "amd") is True
    assert looks_like_igpu("Radeon RX 7800 XT", "amd") is False
    assert looks_like_igpu("Intel UHD Graphics", "intel") is True
    assert looks_like_igpu("Intel Arc A770", "intel") is False


def test_skip_basic_display_in_cim() -> None:
    raw = (
        '[{"Name":"Microsoft Basic Display Adapter","AdapterRAM":0,'
        '"PNPDeviceID":"PCI\\\\VEN_1414"},'
        '{"Name":"Radeon RX 7600","AdapterRAM":8589934592,'
        '"PNPDeviceID":"PCI\\\\VEN_1002&DEV_7480"}]'
    )
    devs = _parse_cim_json(raw)
    names = [d.name for d in devs]
    assert "Microsoft Basic Display Adapter" not in names
    assert any("7600" in n for n in names)
    assert all(d.vendor == "amd" for d in devs)


def test_snapshot_prefers_discrete() -> None:
    snap = GpuSnapshot(
        devices=[
            GpuDevice("Intel UHD Graphics", "intel", 128, dedicated=False, source="wmi"),
            GpuDevice("Radeon RX 7800 XT", "amd", 16384, dedicated=True, source="wmi"),
        ]
    )
    prim = snap.primary
    assert prim is not None
    assert prim.vendor == "amd"
    assert prim.vram_total_mb == 16384


def test_runtime_matches_gpu_is_capability_not_brand() -> None:
    assert runtime_matches_gpu("nvidia", runtime_id="win-cuda-12.4-x64") is True
    assert runtime_matches_gpu("amd", runtime_id="win-cuda-12.4-x64") is False
    assert runtime_matches_gpu("amd", runtime_id="win-vulkan-x64") is True
    assert runtime_matches_gpu("intel", binary="llama-server-vulkan.exe") is True
    assert runtime_matches_gpu("amd", runtime_id="win-cpu-x64") is False
    assert runtime_matches_gpu("amd", runtime_id="linux-vulkan-x64") is True
    assert runtime_matches_gpu("nvidia", runtime_id="linux-vulkan-x64") is True
    assert runtime_matches_gpu("amd", runtime_id="linux-cpu-x64") is False


def _hw(**kw: object) -> HardwareProbe:
    return HardwareProbe(
        nvidia=bool(kw.get("nvidia", False)),
        vram_total_mb=int(kw.get("vram", 12288)),
        vram_free_mb=int(kw.get("free", 10000)),
        gpu_name=str(kw.get("name", "GPU")),
        ram_total_mb=32768,
        ram_avail_mb=16000,
        cpu_count=12,
        gpu_vendor=str(kw.get("vendor", "")),
        gpu_backend=str(kw.get("backend", "")),
    )


def test_autofit_uses_non_nvidia_vram(tmp_path) -> None:
    model = tmp_path / "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
    model.write_bytes(b"0" * 1024)
    hw = _hw(nvidia=False, vendor="amd", backend="vulkan", name="Radeon RX 7800 XT", vram=16384)
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
    assert plan.n_gpu_layers != 0
    assert plan.flash_attn is False  # flash is CUDA-path only
    assert hw.usable_gpu is True


def test_usable_gpu_not_tied_to_nvidia_flag() -> None:
    amd = _hw(nvidia=False, vendor="amd", vram=12288, name="Radeon")
    assert amd.usable_gpu is True
    assert amd.nvidia is False
    tiny = _hw(nvidia=False, vendor="intel", vram=128, name="UHD")
    assert tiny.usable_gpu is False
