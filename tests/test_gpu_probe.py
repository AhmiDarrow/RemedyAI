"""Vendor-neutral GPU probe — any card counts as home.

If this module is wrong, Remedy either forgets the machine has a GPU at all
(everything falls back to slow CPU inference), or it invents one that is not
there (a model is loaded with layers offloaded to a card that cannot take
them, and the runtime dies on the owner's desktop). The probe shells out to
vendor tools that may be absent, hang, print garbage or exit non-zero, so the
properties that matter most here are the negative ones: nothing may raise,
nothing may block forever, and a sensor that says nothing must contribute
nothing rather than a plausible-looking guess.

Every test in this file fakes the sensors. Nothing here starts a real process
or reads the real machine.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from remedy.runtime import gpu_probe
from remedy.runtime.gpu_probe import (
    GpuDevice,
    GpuSnapshot,
    _find_nvidia_smi,
    _mb_from_unknown,
    _parse_amd_smi_json,
    _parse_cim_json,
    _parse_rocm_smi,
    _probe_amd_smi,
    _probe_linux_sysfs,
    _probe_nvidia_smi,
    _probe_windows_cim,
    _probe_windows_wmic,
    _run,
    _skip_name,
    _sysfs_name,
    _sysfs_vendor,
    _sysfs_vram_mb,
    _which_any,
    classify_backend,
    classify_vendor,
    invalidate_gpu_cache,
    looks_like_igpu,
    probe_gpus,
    probe_primary_vram,
    runtime_matches_gpu,
)
from remedy.runtime.rmb.autofit import HardwareProbe, plan_autofit


@pytest.fixture(autouse=True)
def _no_leaked_gpu_cache():
    """The probe memoises into module globals; never inherit or leave one."""
    invalidate_gpu_cache()
    yield
    invalidate_gpu_cache()


def _fake_run(responses: dict[str, str], calls: list[tuple[str, float]]):
    """Stand-in for _run: matches on the joined argv, records every call."""

    def run(argv: list[str], *, timeout: float = 4.0) -> str:
        joined = " ".join(str(a) for a in argv)
        calls.append((joined, timeout))
        for needle, text in responses.items():
            if needle in joined:
                return text
        return ""

    return run


def _posix(monkeypatch) -> None:
    monkeypatch.setattr(gpu_probe, "os", SimpleNamespace(name="posix"))


def _windows(monkeypatch) -> None:
    monkeypatch.setattr(gpu_probe, "os", SimpleNamespace(name="nt"))


# --------------------------------------------------------------------------
# vendor / integrated classification
# --------------------------------------------------------------------------


def test_classify_vendor_from_name_and_pnp() -> None:
    assert classify_vendor("Radeon RX 7800 XT") == "amd"
    assert classify_vendor("Intel Arc A770") == "intel"
    assert classify_vendor("GeForce RTX 4070") == "nvidia"
    assert classify_vendor("Unknown", "PCI\\VEN_1002&DEV_7480") == "amd"
    assert classify_vendor("Unknown", "PCI\\VEN_8086&DEV_56A0") == "intel"
    assert classify_vendor("Unknown", "PCI\\VEN_10DE&DEV_2782") == "nvidia"


@pytest.mark.parametrize(
    ("name", "vendor"),
    [
        ("NVIDIA Tesla V100", "nvidia"),
        ("Quadro P2000", "nvidia"),
        ("TITAN RTX", "nvidia"),
        ("AMD Instinct MI300X", "amd"),
        ("AMD FirePro W7100", "amd"),
        ("Radeon Vega 8", "amd"),
        ("Intel Iris Xe Graphics", "intel"),
        ("Intel UHD Graphics 630", "intel"),
        ("Apple M2 Max", "apple"),
        ("Matrox G200eR2", "other"),
        ("", "other"),
    ],
)
def test_classify_vendor_reads_the_marketing_name(name: str, vendor: str) -> None:
    assert classify_vendor(name) == vendor


def test_a_pnp_vendor_id_outranks_the_marketing_name() -> None:
    # Rebadged or mislabelled adapters: the PCI id is the harder evidence.
    assert classify_vendor("GeForce RTX 4090", "PCI\\VEN_1002&DEV_7480") == "amd"


def test_a_pnp_vendor_id_is_matched_case_insensitively() -> None:
    assert classify_vendor("whatever", "pci\\ven_10de&dev_2782") == "nvidia"


def test_an_unknown_pnp_vendor_id_does_not_shadow_the_name() -> None:
    # 1414 is Microsoft's; the name is still the best signal left.
    assert classify_vendor("Radeon RX 7600", "PCI\\VEN_1414&DEV_008E") == "amd"
    assert classify_vendor("Mystery Adapter", "PCI\\VEN_1414&DEV_008E") == "other"


def test_igpu_vs_discrete() -> None:
    assert looks_like_igpu("AMD Radeon Graphics", "amd") is True
    assert looks_like_igpu("Radeon RX 7800 XT", "amd") is False
    assert looks_like_igpu("Intel UHD Graphics", "intel") is True
    assert looks_like_igpu("Intel Arc A770", "intel") is False


@pytest.mark.parametrize(
    ("name", "vendor", "integrated"),
    [
        ("AMD Radeon(TM) Graphics", "amd", True),
        ("AMD Radeon 780M Graphics", "amd", True),
        ("Graphics", "amd", True),
        ("AMD Radeon Pro W6800", "amd", False),
        ("AMD Radeon RX 7900 XTX", "amd", False),
        ("AMD Radeon VII", "amd", False),
        ("Intel Iris Xe Graphics", "intel", True),
        ("Intel Arc B580", "intel", False),
        ("NVIDIA GeForce RTX 4070", "nvidia", False),
        ("Apple M3 Max", "apple", False),
        ("Matrox G200", "other", False),
    ],
)
def test_integrated_parts_are_recognised_by_name(name: str, vendor: str, integrated: bool) -> None:
    assert looks_like_igpu(name, vendor) is integrated


def test_looks_like_igpu_survives_an_empty_name() -> None:
    assert looks_like_igpu("", "nvidia") is False
    assert looks_like_igpu("", "intel") is True


@pytest.mark.parametrize(
    ("vendor", "backend"),
    [("nvidia", "cuda"), ("intel", "vulkan"), ("apple", "metal"), ("other", ""), ("", "")],
)
def test_classify_backend_never_shells_out_for_non_amd(monkeypatch, vendor: str, backend: str) -> None:
    def boom(_names: tuple[str, ...]) -> str | None:
        raise AssertionError("no tool lookup should happen for this vendor")

    monkeypatch.setattr(gpu_probe, "_which_any", boom)
    assert classify_backend(vendor) == backend


def test_amd_falls_back_to_vulkan_when_rocm_is_not_installed(monkeypatch) -> None:
    monkeypatch.setattr(gpu_probe, "_which_any", lambda names: None)
    assert classify_backend("amd") == "vulkan"


def test_amd_claims_hip_only_when_a_rocm_tool_exists(monkeypatch) -> None:
    monkeypatch.setattr(gpu_probe, "_which_any", lambda names: "/opt/rocm/bin/rocm-smi")
    assert classify_backend("amd") == "hip"


# --------------------------------------------------------------------------
# name filtering
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Microsoft Basic Display Adapter",
        "Remote Desktop Graphics Adapter",
        "VirtualBox Graphics Adapter",
        "VMware SVGA 3D",
        "Microsoft Hyper-V Video",
        "Citrix Indirect Display Adapter",
        "Parsec Virtual Display Adapter",
        "",
        "   ",
    ],
)
def test_virtual_and_nameless_adapters_are_refused(name: str) -> None:
    assert _skip_name(name) is True


@pytest.mark.parametrize("name", ["NVIDIA GeForce RTX 4070", "AMD Radeon RX 7600", "Intel Arc A770"])
def test_real_adapters_are_not_refused(name: str) -> None:
    assert _skip_name(name) is False


# --------------------------------------------------------------------------
# subprocess plumbing
# --------------------------------------------------------------------------


def test_run_returns_stdout_and_hides_the_console_window(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def fake(argv, **kwargs):
        seen["argv"] = argv
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="hello", stderr="")

    monkeypatch.setattr(gpu_probe.subprocess, "run", fake)
    assert _run(["tool", "--x"], timeout=1.5) == "hello"
    assert seen["argv"] == ["tool", "--x"]
    assert seen["timeout"] == 1.5
    assert seen["capture_output"] is True
    assert seen["text"] is True
    if sys.platform == "win32":
        assert isinstance(seen["creationflags"], int)


def test_run_uses_a_bounded_default_timeout(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def fake(argv, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(gpu_probe.subprocess, "run", fake)
    _run(["tool"])
    assert seen["timeout"] == 4.0


def test_a_failing_exit_code_discards_whatever_was_printed(monkeypatch) -> None:
    monkeypatch.setattr(
        gpu_probe.subprocess,
        "run",
        lambda argv, **kw: SimpleNamespace(returncode=9, stdout="garbage on stdout", stderr="boom"),
    )
    assert _run(["tool"]) == ""


def test_run_never_returns_none_for_an_empty_stdout(monkeypatch) -> None:
    monkeypatch.setattr(
        gpu_probe.subprocess,
        "run",
        lambda argv, **kw: SimpleNamespace(returncode=0, stdout=None, stderr=None),
    )
    assert _run(["tool"]) == ""


@pytest.mark.parametrize(
    "exc",
    [
        gpu_probe.subprocess.TimeoutExpired(cmd="tool", timeout=4.0),
        FileNotFoundError("no such tool"),
        PermissionError("denied"),
        OSError("winerror 87"),
        RuntimeError("something unforeseen"),
    ],
)
def test_a_sensor_that_explodes_is_reported_as_silence_not_raised(monkeypatch, exc: Exception) -> None:
    def fake(argv, **kw):
        raise exc

    monkeypatch.setattr(gpu_probe.subprocess, "run", fake)
    assert _run(["tool"]) == ""


def test_which_any_takes_the_first_tool_that_exists(monkeypatch) -> None:
    asked: list[str] = []

    def which(name: str) -> str | None:
        asked.append(name)
        return "/usr/bin/second" if name == "second" else None

    monkeypatch.setattr(gpu_probe.shutil, "which", which)
    _posix(monkeypatch)
    assert _which_any(("first", "second", "third")) == "/usr/bin/second"
    assert "third" not in asked


def test_which_any_retries_with_exe_only_on_windows(monkeypatch) -> None:
    asked: list[str] = []

    def which(name: str) -> str | None:
        asked.append(name)
        return r"C:\rocm\amd-smi.exe" if name.endswith(".exe") else None

    monkeypatch.setattr(gpu_probe.shutil, "which", which)
    _windows(monkeypatch)
    assert _which_any(("amd-smi",)) == r"C:\rocm\amd-smi.exe"
    assert asked == ["amd-smi", "amd-smi.exe"]

    asked.clear()
    _posix(monkeypatch)
    assert _which_any(("amd-smi",)) is None
    assert asked == ["amd-smi"]


def test_which_any_returns_none_when_nothing_is_installed(monkeypatch) -> None:
    monkeypatch.setattr(gpu_probe.shutil, "which", lambda name: None)
    _windows(monkeypatch)
    assert _which_any(("amd-smi", "rocm-smi")) is None


# --------------------------------------------------------------------------
# nvidia-smi
# --------------------------------------------------------------------------


def test_find_nvidia_smi_prefers_an_existing_well_known_path(monkeypatch, tmp_path: Path) -> None:
    real = tmp_path / "nvidia-smi.exe"
    real.write_text("", encoding="utf-8")
    monkeypatch.setattr(gpu_probe, "_NVIDIA_SMI", (str(tmp_path / "missing.exe"), str(real)))
    monkeypatch.setattr(gpu_probe.shutil, "which", lambda name: "/should/not/be/used")
    assert _find_nvidia_smi() == str(real)


def test_find_nvidia_smi_ignores_a_directory_of_the_same_name(monkeypatch, tmp_path: Path) -> None:
    trap = tmp_path / "nvidia-smi"
    trap.mkdir()
    monkeypatch.setattr(gpu_probe, "_NVIDIA_SMI", (str(trap),))
    monkeypatch.setattr(gpu_probe.shutil, "which", lambda name: None)
    assert _find_nvidia_smi() is None


def test_find_nvidia_smi_falls_back_to_path_lookup(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gpu_probe, "_NVIDIA_SMI", (str(tmp_path / "nope"),))
    monkeypatch.setattr(gpu_probe.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    assert _find_nvidia_smi() == "/usr/bin/nvidia-smi"


def test_no_nvidia_smi_means_no_devices_and_no_process(monkeypatch) -> None:
    monkeypatch.setattr(gpu_probe, "_find_nvidia_smi", lambda: None)

    def boom(*a, **kw):
        raise AssertionError("must not run anything without a tool")

    monkeypatch.setattr(gpu_probe, "_run", boom)
    assert _probe_nvidia_smi() == []


def test_nvidia_smi_csv_is_parsed_per_card(monkeypatch) -> None:
    calls: list[tuple[str, float]] = []
    monkeypatch.setattr(gpu_probe, "_find_nvidia_smi", lambda: "nvidia-smi")
    monkeypatch.setattr(
        gpu_probe,
        "_run",
        _fake_run(
            {"--query-gpu": "24564, 23000, NVIDIA GeForce RTX 4090\n8192, 7000, NVIDIA RTX A2000\n"},
            calls,
        ),
    )
    devices = _probe_nvidia_smi()
    assert [d.name for d in devices] == ["NVIDIA GeForce RTX 4090", "NVIDIA RTX A2000"]
    assert [d.vram_total_mb for d in devices] == [24564, 8192]
    assert [d.vram_free_mb for d in devices] == [23000, 7000]
    assert all(d.vendor == "nvidia" and d.backend == "cuda" and d.dedicated for d in devices)
    assert all(d.source == "nvidia-smi" for d in devices)
    # No fallback to -L once the query worked.
    assert not any("-L" in argv for argv, _ in calls)


@pytest.mark.parametrize(
    "line",
    [
        "",
        "24564",  # a single column is not a reading
        "[N/A], [N/A], NVIDIA GeForce RTX 4090",
        "not-a-number, 7000, Card",
        "0, 0, NVIDIA GeForce RTX 4090",  # zero VRAM is not a usable card
    ],
)
def test_an_unreadable_nvidia_smi_line_is_dropped_not_guessed(monkeypatch, line: str) -> None:
    monkeypatch.setattr(gpu_probe, "_find_nvidia_smi", lambda: "nvidia-smi")
    monkeypatch.setattr(gpu_probe, "_run", _fake_run({"--query-gpu": line + "\n"}, []))
    assert _probe_nvidia_smi() == []


def test_a_nameless_nvidia_reading_still_counts(monkeypatch) -> None:
    monkeypatch.setattr(gpu_probe, "_find_nvidia_smi", lambda: "nvidia-smi")
    monkeypatch.setattr(gpu_probe, "_run", _fake_run({"--query-gpu": "8192, 7000\n"}, []))
    (dev,) = _probe_nvidia_smi()
    assert dev.name == "NVIDIA GPU"
    assert dev.vram_total_mb == 8192


def test_an_absurdly_long_card_name_is_truncated(monkeypatch) -> None:
    monkeypatch.setattr(gpu_probe, "_find_nvidia_smi", lambda: "nvidia-smi")
    monkeypatch.setattr(gpu_probe, "_run", _fake_run({"--query-gpu": f"8192, 7000, {'X' * 500}\n"}, []))
    (dev,) = _probe_nvidia_smi()
    assert len(dev.name) == 80


def test_a_driver_too_old_for_query_gpu_falls_back_to_listing(monkeypatch) -> None:
    calls: list[tuple[str, float]] = []
    monkeypatch.setattr(gpu_probe, "_find_nvidia_smi", lambda: "nvidia-smi")
    monkeypatch.setattr(
        gpu_probe,
        "_run",
        _fake_run({"-L": "GPU 0: NVIDIA GeForce RTX 3060 (UUID: GPU-abc)\n"}, calls),
    )
    (dev,) = _probe_nvidia_smi()
    assert dev.source == "nvidia-smi-L"
    assert dev.name.startswith("GPU 0: NVIDIA GeForce RTX 3060")
    # The listing carries no memory reading, so the numbers are an admitted guess.
    assert (dev.vram_total_mb, dev.vram_free_mb) == (8192, 6144)
    assert any("-L" in argv and timeout == 3.0 for argv, timeout in calls)


def test_a_silent_nvidia_smi_contributes_nothing(monkeypatch) -> None:
    monkeypatch.setattr(gpu_probe, "_find_nvidia_smi", lambda: "nvidia-smi")
    monkeypatch.setattr(gpu_probe, "_run", _fake_run({"-L": "   \n"}, []))
    assert _probe_nvidia_smi() == []


# --------------------------------------------------------------------------
# amd-smi / rocm-smi
# --------------------------------------------------------------------------


def test_no_amd_tool_means_no_devices_and_no_process(monkeypatch) -> None:
    monkeypatch.setattr(gpu_probe, "_which_any", lambda names: None)

    def boom(*a, **kw):
        raise AssertionError("must not run anything without a tool")

    monkeypatch.setattr(gpu_probe, "_run", boom)
    assert _probe_amd_smi() == []


def test_amd_smi_json_is_preferred_and_stops_the_probe(monkeypatch) -> None:
    calls: list[tuple[str, float]] = []
    payload = json.dumps([{"market_name": "Radeon RX 7900 XTX", "vram": {"size": 24576}}])
    monkeypatch.setattr(gpu_probe, "_which_any", lambda names: "/opt/rocm/bin/amd-smi")
    monkeypatch.setattr(gpu_probe, "_run", _fake_run({"static --json": payload}, calls))
    (dev,) = _probe_amd_smi()
    assert (dev.name, dev.vendor, dev.vram_total_mb, dev.source) == (
        "Radeon RX 7900 XTX",
        "amd",
        24576,
        "amd-smi",
    )
    assert len(calls) == 1


def test_rocm_smi_is_never_asked_for_amd_smi_only_json(monkeypatch) -> None:
    calls: list[tuple[str, float]] = []
    monkeypatch.setattr(gpu_probe, "_which_any", lambda names: "/opt/rocm/bin/rocm-smi")
    monkeypatch.setattr(gpu_probe, "_run", _fake_run({}, calls))
    _probe_amd_smi()
    assert not any("static --json" in argv for argv, _ in calls)


def test_amd_falls_through_to_meminfo_when_the_json_is_useless(monkeypatch) -> None:
    calls: list[tuple[str, float]] = []
    meminfo = "Total Memory (B): 17163091968\nUsed Memory (B): 1073741824\n"
    monkeypatch.setattr(gpu_probe, "_which_any", lambda names: "/opt/rocm/bin/amd-smi")
    monkeypatch.setattr(
        gpu_probe,
        "_run",
        _fake_run({"static --json": "not json at all", "--showmeminfo": meminfo}, calls),
    )
    (dev,) = _probe_amd_smi()
    assert dev.source == "rocm-smi"
    assert dev.vram_total_mb == 16368
    assert dev.vram_free_mb == 16368 - 1024


def test_amd_presence_only_is_the_last_resort(monkeypatch) -> None:
    listing = "Fan speed unavailable\nCard series: Radeon RX 6800 XT\n"
    monkeypatch.setattr(gpu_probe, "_which_any", lambda names: "/opt/rocm/bin/rocm-smi")
    monkeypatch.setattr(gpu_probe, "_run", _fake_run({"-i": listing}, []))
    (dev,) = _probe_amd_smi()
    assert dev.name == "Card series: Radeon RX 6800 XT"
    assert (dev.vram_total_mb, dev.vram_free_mb) == (8192, 6144)
    assert dev.backend == "hip"


def test_an_amd_tool_that_says_nothing_yields_nothing(monkeypatch) -> None:
    monkeypatch.setattr(gpu_probe, "_which_any", lambda names: "/opt/rocm/bin/amd-smi")
    monkeypatch.setattr(gpu_probe, "_run", _fake_run({}, []))
    assert _probe_amd_smi() == []


@pytest.mark.parametrize("raw", ["", "   \n", "not json", "{oops", "null", "[1, 2, 3]", '"a string"'])
def test_amd_json_that_is_not_a_device_list_is_ignored(raw: str) -> None:
    assert _parse_amd_smi_json(raw) == []


def test_amd_json_accepts_a_single_object() -> None:
    (dev,) = _parse_amd_smi_json(json.dumps({"product_name": "Instinct MI210", "memory": {"total": 65536}}))
    assert (dev.name, dev.vram_total_mb) == ("Instinct MI210", 65536)


def test_amd_json_reads_the_nested_board_name() -> None:
    (dev,) = _parse_amd_smi_json(json.dumps([{"board": {"product_name": "Radeon PRO W7900"}}]))
    assert dev.name == "Radeon PRO W7900"


def test_amd_json_without_memory_admits_a_default_rather_than_zero() -> None:
    (dev,) = _parse_amd_smi_json(json.dumps([{"market_name": "Radeon RX 7600"}]))
    assert (dev.vram_total_mb, dev.vram_free_mb) == (8192, 7168)


def test_amd_json_ignores_a_memory_field_of_the_wrong_shape() -> None:
    (dev,) = _parse_amd_smi_json(json.dumps([{"market_name": "Radeon", "vram": "16 GB"}]))
    assert dev.vram_total_mb == 8192


def test_amd_json_drops_entries_that_are_not_objects() -> None:
    raw = json.dumps([{"market_name": "Radeon RX 7600"}, "junk", 5, None])
    assert [d.name for d in _parse_amd_smi_json(raw)] == ["Radeon RX 7600"]


def test_amd_json_truncates_a_runaway_name() -> None:
    (dev,) = _parse_amd_smi_json(json.dumps([{"market_name": "R" * 300}]))
    assert len(dev.name) == 80


def test_rocm_meminfo_reports_free_as_total_minus_used() -> None:
    (dev,) = _parse_rocm_smi("Total Memory (B): 8589934592\nUsed Memory (B): 2147483648\n")
    assert (dev.vram_total_mb, dev.vram_free_mb) == (8192, 8192 - 2048)


def test_rocm_meminfo_never_reports_negative_free_memory() -> None:
    (dev,) = _parse_rocm_smi("Total Memory (B): 1073741824\nUsed Memory (B): 8589934592\n")
    assert (dev.vram_total_mb, dev.vram_free_mb) == (1024, 0)


@pytest.mark.parametrize("raw", ["", "no numbers here", "Used Memory (B): 1073741824\n"])
def test_rocm_meminfo_without_a_total_yields_nothing(raw: str) -> None:
    assert _parse_rocm_smi(raw) == []


def test_a_card_index_prefix_defeats_the_rocm_parser() -> None:
    # Known limitation, pinned deliberately: the first number on the line wins,
    # so rocm-smi's own "GPU[0]" prefix is read as the memory total (0) and the
    # reading is discarded. Silence rather than a fabricated card is the safe
    # direction, and the AMD path still has its presence-only fallback.
    raw = "GPU[0]\t: vram Total Memory (B): 17163091968\n"
    assert _parse_rocm_smi(raw) == []


# --------------------------------------------------------------------------
# Windows CIM / WMI
# --------------------------------------------------------------------------


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


@pytest.mark.parametrize("raw", ["", "   ", "Get-CimInstance : access denied", "{broken"])
def test_cim_output_that_is_not_json_is_ignored(raw: str) -> None:
    assert _parse_cim_json(raw) == []


def test_cim_accepts_the_single_adapter_shape() -> None:
    raw = json.dumps(
        {"Name": "NVIDIA GeForce RTX 4070", "AdapterRAM": 8589934592, "PNPDeviceID": "PCI\\VEN_10DE"}
    )
    (dev,) = _parse_cim_json(raw)
    assert (dev.vendor, dev.vram_total_mb, dev.source, dev.backend) == ("nvidia", 8192, "wmi", "cuda")
    assert dev.vram_free_mb == 8192 - 512


def test_cim_ignores_entries_that_are_not_objects() -> None:
    raw = json.dumps(["junk", 7, {"Name": "Radeon RX 7600", "AdapterRAM": 8589934592}])
    assert [d.name for d in _parse_cim_json(raw)] == ["Radeon RX 7600"]


def test_the_32_bit_adapter_ram_overflow_is_treated_as_a_lower_bound() -> None:
    # Win32_VideoController reports a 32-bit truncation for anything above 4 GB.
    raw = json.dumps(
        [{"Name": "NVIDIA GeForce RTX 4090", "AdapterRAM": 4293918720, "PNPDeviceID": "PCI\\VEN_10DE"}]
    )
    (dev,) = _parse_cim_json(raw)
    assert dev.vram_total_mb == 4096


def test_a_discrete_card_with_no_ram_reading_gets_a_conservative_floor() -> None:
    raw = json.dumps([{"Name": "NVIDIA GeForce RTX 4070", "AdapterRAM": None, "PNPDeviceID": "PCI\\VEN_10DE"}])
    (dev,) = _parse_cim_json(raw)
    assert dev.vram_total_mb == 4096
    assert dev.dedicated is True


def test_an_integrated_part_with_no_ram_reading_is_dropped_not_invented() -> None:
    raw = json.dumps([{"Name": "Intel(R) UHD Graphics 770", "AdapterRAM": 0, "PNPDeviceID": "PCI\\VEN_8086"}])
    assert _parse_cim_json(raw) == []


def test_an_adapter_with_no_name_is_dropped() -> None:
    raw = json.dumps([{"Name": None, "AdapterRAM": 8589934592, "PNPDeviceID": "PCI\\VEN_10DE"}])
    assert _parse_cim_json(raw) == []


def test_cim_is_not_attempted_off_windows(monkeypatch) -> None:
    _posix(monkeypatch)

    def boom(name: str):
        raise AssertionError("no powershell lookup off Windows")

    monkeypatch.setattr(gpu_probe.shutil, "which", boom)
    assert _probe_windows_cim() == []


def test_cim_asks_powershell_without_touching_the_users_profile(monkeypatch) -> None:
    calls: list[tuple[str, float]] = []
    _windows(monkeypatch)
    monkeypatch.setattr(
        gpu_probe.shutil, "which", lambda name: "powershell" if name == "powershell" else None
    )
    raw = json.dumps([{"Name": "Radeon RX 7600", "AdapterRAM": 8589934592, "PNPDeviceID": "PCI\\VEN_1002"}])
    monkeypatch.setattr(gpu_probe, "_run", _fake_run({"Get-CimInstance": raw}, calls))
    monkeypatch.setattr(gpu_probe, "_probe_windows_wmic", lambda: pytest.fail("wmic must not be needed"))
    (dev,) = _probe_windows_cim()
    assert dev.name == "Radeon RX 7600"
    argv, timeout = calls[0]
    assert "-NoProfile" in argv and "-NonInteractive" in argv
    assert timeout == 6.0


def test_cim_falls_back_to_wmic_when_no_shell_is_present(monkeypatch) -> None:
    _windows(monkeypatch)
    monkeypatch.setattr(gpu_probe.shutil, "which", lambda name: None)
    sentinel = [GpuDevice("Radeon RX 7600", "amd", 8192, source="wmic")]
    monkeypatch.setattr(gpu_probe, "_probe_windows_wmic", lambda: sentinel)
    assert _probe_windows_cim() == sentinel


def test_cim_falls_back_to_wmic_when_powershell_answers_nonsense(monkeypatch) -> None:
    _windows(monkeypatch)
    monkeypatch.setattr(gpu_probe.shutil, "which", lambda name: "powershell")
    monkeypatch.setattr(gpu_probe, "_run", _fake_run({"Get-CimInstance": "ObjectNotFound: Get-CimInstance"}, []))
    sentinel = [GpuDevice("Radeon RX 7600", "amd", 8192, source="wmic")]
    monkeypatch.setattr(gpu_probe, "_probe_windows_wmic", lambda: sentinel)
    assert _probe_windows_cim() == sentinel


def test_no_wmic_means_no_devices(monkeypatch) -> None:
    monkeypatch.setattr(gpu_probe.shutil, "which", lambda name: None)

    def boom(*a, **kw):
        raise AssertionError("must not run anything without wmic")

    monkeypatch.setattr(gpu_probe, "_run", boom)
    assert _probe_windows_wmic() == []


def test_wmic_csv_is_parsed_regardless_of_column_order(monkeypatch) -> None:
    csv = (
        "Node,AdapterRAM,Name,PNPDeviceID\n"
        "\n"
        "MYPC,8589934592,NVIDIA GeForce RTX 4070,PCI\\VEN_10DE&DEV_2786\n"
        "MYPC,PCI\\VEN_1002&DEV_164E,AMD Radeon Graphics,1073741824\n"
    )
    monkeypatch.setattr(gpu_probe.shutil, "which", lambda name: "wmic")
    monkeypatch.setattr(gpu_probe, "_run", _fake_run({"win32_videocontroller": csv}, []))
    monkeypatch.setattr(gpu_probe, "_which_any", lambda names: None)
    devices = _probe_windows_wmic()
    assert [(d.name, d.vendor, d.dedicated) for d in devices] == [
        ("NVIDIA GeForce RTX 4070", "nvidia", True),
        ("AMD Radeon Graphics", "amd", False),
    ]
    assert devices[0].source == "wmic"
    assert devices[1].vram_total_mb == 1024


def test_wmic_skips_the_header_and_ragged_lines(monkeypatch) -> None:
    csv = "Node,AdapterRAM,Name,PNPDeviceID\nMYPC,8589934592\n\n   \n"
    monkeypatch.setattr(gpu_probe.shutil, "which", lambda name: "wmic")
    monkeypatch.setattr(gpu_probe, "_run", _fake_run({"win32_videocontroller": csv}, []))
    assert _probe_windows_wmic() == []


def test_wmic_drops_virtual_adapters(monkeypatch) -> None:
    csv = "MYPC,0,Microsoft Basic Display Adapter,PCI\\VEN_1414&DEV_008E\n"
    monkeypatch.setattr(gpu_probe.shutil, "which", lambda name: "wmic")
    monkeypatch.setattr(gpu_probe, "_run", _fake_run({"win32_videocontroller": csv}, []))
    assert _probe_windows_wmic() == []


def test_wmic_drops_an_integrated_part_with_no_ram(monkeypatch) -> None:
    csv = "MYPC,0,Intel(R) UHD Graphics 770,PCI\\VEN_8086&DEV_4680\n"
    monkeypatch.setattr(gpu_probe.shutil, "which", lambda name: "wmic")
    monkeypatch.setattr(gpu_probe, "_run", _fake_run({"win32_videocontroller": csv}, []))
    assert _probe_windows_wmic() == []


def test_wmic_gives_a_discrete_card_with_no_ram_a_floor(monkeypatch) -> None:
    csv = "MYPC,,NVIDIA GeForce RTX 4070,PCI\\VEN_10DE&DEV_2786\n"
    monkeypatch.setattr(gpu_probe.shutil, "which", lambda name: "wmic")
    monkeypatch.setattr(gpu_probe, "_run", _fake_run({"win32_videocontroller": csv}, []))
    (dev,) = _probe_windows_wmic()
    assert dev.vram_total_mb == 4096
    assert dev.vram_free_mb == 4096 - 512


# --------------------------------------------------------------------------
# Linux sysfs
# --------------------------------------------------------------------------


def _fake_drm(monkeypatch, root: Path) -> None:
    """Point the module's /sys/class/drm at a throwaway tree."""
    real = gpu_probe.Path

    def factory(arg: Any = "", *rest: Any):
        if str(arg) == "/sys/class/drm":
            return real(root)
        return real(arg, *rest)

    monkeypatch.setattr(gpu_probe, "Path", factory)


def _make_card(root: Path, card: str, **files: str) -> Path:
    dev = root / card / "device"
    dev.mkdir(parents=True)
    for name, text in files.items():
        (dev / name).write_text(text, encoding="utf-8")
    return dev


def test_sysfs_is_skipped_where_there_is_no_drm_tree(monkeypatch, tmp_path: Path) -> None:
    _fake_drm(monkeypatch, tmp_path / "does-not-exist")
    assert _probe_linux_sysfs() == []


def test_sysfs_reads_vendor_name_and_vram(monkeypatch, tmp_path: Path) -> None:
    _make_card(
        tmp_path,
        "card0",
        vendor="0x1002\n",
        product_name="AMD Radeon RX 7900 XTX\n",
        mem_info_vram_total=str(24 * 1024**3),
    )
    _fake_drm(monkeypatch, tmp_path)
    monkeypatch.setattr(gpu_probe, "_which_any", lambda names: None)
    (dev,) = _probe_linux_sysfs()
    assert (dev.name, dev.vendor, dev.vram_total_mb, dev.source) == (
        "AMD Radeon RX 7900 XTX",
        "amd",
        24576,
        "sysfs",
    )
    assert dev.backend == "vulkan"


def test_sysfs_ignores_connectors_and_cards_without_a_device_link(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "card0-DP-1" / "device").mkdir(parents=True)
    (tmp_path / "card1").mkdir()  # no device/ underneath
    _fake_drm(monkeypatch, tmp_path)
    assert _probe_linux_sysfs() == []


def test_sysfs_drops_a_virtual_card_of_unknown_vendor(monkeypatch, tmp_path: Path) -> None:
    _make_card(tmp_path, "card0", vendor="0x15ad\n", product_name="VMware SVGA 3D\n")
    _fake_drm(monkeypatch, tmp_path)
    assert _probe_linux_sysfs() == []


def test_sysfs_gives_a_discrete_card_without_a_vram_file_a_floor(monkeypatch, tmp_path: Path) -> None:
    _make_card(tmp_path, "card0", vendor="0x10de\n", product_name="NVIDIA GeForce RTX 4070\n")
    _fake_drm(monkeypatch, tmp_path)
    (dev,) = _probe_linux_sysfs()
    assert (dev.vram_total_mb, dev.dedicated, dev.backend) == (4096, True, "cuda")


def test_sysfs_drops_an_integrated_card_without_a_vram_file(monkeypatch, tmp_path: Path) -> None:
    _make_card(tmp_path, "card0", vendor="0x8086\n", product_name="Intel UHD Graphics 770\n")
    _fake_drm(monkeypatch, tmp_path)
    assert _probe_linux_sysfs() == []


@pytest.mark.parametrize(
    ("raw", "vendor"),
    [
        ("0x10de\n", "nvidia"),
        ("0x1002", "amd"),
        ("0x1022", "amd"),
        ("0x8086\n", "intel"),
        ("0X106B\n", "apple"),
        ("0x15ad\n", "other"),
        ("", "other"),
    ],
)
def test_sysfs_vendor_ids_map_to_vendors(tmp_path: Path, raw: str, vendor: str) -> None:
    p = tmp_path / "vendor"
    p.write_text(raw, encoding="ascii")
    assert _sysfs_vendor(p) == vendor


def test_an_unreadable_sysfs_vendor_file_is_not_an_error(tmp_path: Path) -> None:
    assert _sysfs_vendor(tmp_path / "missing") == "other"


def test_sysfs_name_prefers_product_name_then_label_then_driver(tmp_path: Path) -> None:
    dev = tmp_path / "device"
    dev.mkdir()
    assert _sysfs_name(dev) == ""

    (dev / "uevent").write_text("DRIVER=amdgpu\nPCI_ID=1002:744C\n", encoding="utf-8")
    assert _sysfs_name(dev) == "amdgpu"

    (dev / "label").write_text("Radeon Board\n", encoding="utf-8")
    assert _sysfs_name(dev) == "Radeon Board"

    (dev / "product_name").write_text("AMD Radeon RX 7900 XTX\n", encoding="utf-8")
    assert _sysfs_name(dev) == "AMD Radeon RX 7900 XTX"


def test_a_blank_product_name_does_not_win_over_the_driver(tmp_path: Path) -> None:
    dev = tmp_path / "device"
    dev.mkdir()
    (dev / "product_name").write_text("   \n", encoding="utf-8")
    (dev / "uevent").write_text("DRIVER=i915\n", encoding="utf-8")
    assert _sysfs_name(dev) == "i915"


def test_a_uevent_without_a_driver_line_names_nothing(tmp_path: Path) -> None:
    dev = tmp_path / "device"
    dev.mkdir()
    (dev / "uevent").write_text("PCI_SLOT_NAME=0000:03:00.0\n", encoding="utf-8")
    assert _sysfs_name(dev) == ""


def test_sysfs_vram_accepts_both_bytes_and_megabytes(tmp_path: Path) -> None:
    dev = tmp_path / "device"
    dev.mkdir()
    (dev / "mem_info_vram_total").write_text(str(8 * 1024**3), encoding="ascii")
    assert _sysfs_vram_mb(dev) == 8192

    (dev / "mem_info_vram_total").write_text("512", encoding="ascii")
    assert _sysfs_vram_mb(dev) == 512


def test_sysfs_vram_falls_back_to_the_visible_aperture(tmp_path: Path) -> None:
    dev = tmp_path / "device"
    dev.mkdir()
    (dev / "mem_info_vram_total").write_text("not a number", encoding="ascii")
    (dev / "mem_info_vis_vram_total").write_text(str(4 * 1024**3), encoding="ascii")
    assert _sysfs_vram_mb(dev) == 4096


@pytest.mark.parametrize("raw", ["", "0", "   ", "N/A"])
def test_an_unusable_sysfs_vram_reading_is_zero_not_a_crash(tmp_path: Path, raw: str) -> None:
    dev = tmp_path / "device"
    dev.mkdir()
    (dev / "mem_info_vram_total").write_text(raw, encoding="ascii")
    assert _sysfs_vram_mb(dev) == 0


def test_a_missing_sysfs_vram_file_is_zero(tmp_path: Path) -> None:
    dev = tmp_path / "device"
    dev.mkdir()
    assert _sysfs_vram_mb(dev) == 0


# --------------------------------------------------------------------------
# unit coercion
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (None, "", 0),
        ("", "", 0),
        ("abc", "", 0),
        (2048, "", 2048),
        ("2048", "", 2048),
        (17163091968, "", 16368),  # bytes
        ("16", "GiB", 16384),
        ("16", "gb", 16384),
        ("8 GB", "", 8192),
        ("512", "MiB", 512),
        (0, "", 0),
        (300, "gb", 300),  # >= 256 is read as an already-converted MB count
    ],
)
def test_memory_readings_are_coerced_to_megabytes(value: Any, unit: str, expected: int) -> None:
    assert _mb_from_unknown(value, unit) == expected


# --------------------------------------------------------------------------
# snapshot / cache / public surface
# --------------------------------------------------------------------------


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


def test_a_shared_memory_igpu_never_outranks_a_real_card() -> None:
    snap = GpuSnapshot(
        devices=[
            GpuDevice("AMD Radeon Graphics", "amd", 32768, dedicated=False, source="wmi"),
            GpuDevice("NVIDIA GeForce RTX 4060", "nvidia", 8192, dedicated=True, source="nvidia-smi"),
        ]
    )
    assert snap.primary is not None
    assert snap.primary.name == "NVIDIA GeForce RTX 4060"


def test_with_only_integrated_parts_the_largest_still_wins() -> None:
    snap = GpuSnapshot(
        devices=[
            GpuDevice("Intel UHD Graphics", "intel", 128, dedicated=False),
            GpuDevice("AMD Radeon Graphics", "amd", 2048, dedicated=False),
        ]
    )
    assert snap.primary is not None
    assert snap.primary.vendor == "amd"


def test_an_empty_snapshot_has_no_primary() -> None:
    snap = GpuSnapshot()
    assert snap.primary is None
    assert snap.to_public() == {"devices": [], "primary": None}


def test_the_public_snapshot_repeats_the_primary_inline() -> None:
    dev = GpuDevice("Radeon RX 7600", "amd", 8192, 7000, True, "wmi", "vulkan")
    pub = GpuSnapshot(devices=[dev]).to_public()
    assert pub["devices"] == [dev.to_public()]
    assert pub["primary"]["name"] == "Radeon RX 7600"
    assert set(pub["primary"]) == {
        "name",
        "vendor",
        "vram_total_mb",
        "vram_free_mb",
        "dedicated",
        "source",
        "backend",
    }


def test_a_device_reading_cannot_be_edited_after_the_fact() -> None:
    dev = GpuDevice("Radeon RX 7600", "amd", 8192)
    with pytest.raises(dataclasses.FrozenInstanceError):
        dev.vram_total_mb = 99999  # type: ignore[misc]


def _stub_sensors(monkeypatch, *, nvidia=(), amd=(), cim=(), sysfs=()) -> dict[str, int]:
    counts: dict[str, int] = {}

    def make(key: str, devices):
        def probe() -> list[GpuDevice]:
            counts[key] = counts.get(key, 0) + 1
            return list(devices)

        return probe

    monkeypatch.setattr(gpu_probe, "_probe_nvidia_smi", make("nvidia", nvidia))
    monkeypatch.setattr(gpu_probe, "_probe_amd_smi", make("amd", amd))
    monkeypatch.setattr(gpu_probe, "_probe_windows_cim", make("cim", cim))
    monkeypatch.setattr(gpu_probe, "_probe_linux_sysfs", make("sysfs", sysfs))
    return counts


def test_the_expensive_windows_query_is_skipped_once_a_real_card_is_known(monkeypatch) -> None:
    counts = _stub_sensors(
        monkeypatch,
        nvidia=[GpuDevice("NVIDIA GeForce RTX 4070", "nvidia", 12288, dedicated=True)],
        cim=[GpuDevice("should never be reached", "other", 4096)],
    )
    snap = probe_gpus(force=True)
    assert [d.name for d in snap.devices] == ["NVIDIA GeForce RTX 4070"]
    assert "cim" not in counts


def test_windows_is_still_asked_when_only_an_igpu_was_found(monkeypatch) -> None:
    counts = _stub_sensors(
        monkeypatch,
        amd=[GpuDevice("AMD Radeon Graphics", "amd", 2048, dedicated=False)],
        cim=[GpuDevice("NVIDIA GeForce RTX 4070", "nvidia", 12288, dedicated=True)],
    )
    snap = probe_gpus(force=True)
    assert counts["cim"] == 1
    assert len(snap.devices) == 2


def test_the_same_card_seen_by_two_sensors_is_recorded_once(monkeypatch) -> None:
    _stub_sensors(
        monkeypatch,
        nvidia=[GpuDevice("NVIDIA GeForce RTX 4070", "nvidia", 12288, source="nvidia-smi")],
        sysfs=[GpuDevice("nvidia geforce rtx 4070", "nvidia", 12288, source="sysfs")],
    )
    snap = probe_gpus(force=True)
    assert len(snap.devices) == 1
    assert snap.devices[0].source == "nvidia-smi"


def test_a_virtual_adapter_is_refused_even_from_a_vendor_tool(monkeypatch) -> None:
    _stub_sensors(monkeypatch, sysfs=[GpuDevice("VMware SVGA 3D", "other", 256)])
    assert probe_gpus(force=True).devices == []


def test_a_second_probe_within_the_ttl_reuses_the_snapshot(monkeypatch) -> None:
    counts = _stub_sensors(monkeypatch, nvidia=[GpuDevice("NVIDIA GeForce RTX 4070", "nvidia", 12288)])
    first = probe_gpus()
    second = probe_gpus()
    assert second is first
    assert counts["nvidia"] == 1


def test_force_ignores_a_warm_cache(monkeypatch) -> None:
    counts = _stub_sensors(monkeypatch, nvidia=[GpuDevice("NVIDIA GeForce RTX 4070", "nvidia", 12288)])
    first = probe_gpus()
    second = probe_gpus(force=True)
    assert second is not first
    assert counts["nvidia"] == 2


def test_invalidating_the_cache_forces_the_next_probe(monkeypatch) -> None:
    counts = _stub_sensors(monkeypatch, nvidia=[GpuDevice("NVIDIA GeForce RTX 4070", "nvidia", 12288)])
    probe_gpus()
    invalidate_gpu_cache()
    probe_gpus()
    assert counts["nvidia"] == 2


def test_a_stale_snapshot_expires_so_a_swapped_card_is_noticed(monkeypatch) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr(gpu_probe, "time", SimpleNamespace(time=lambda: clock["now"]))
    counts = _stub_sensors(monkeypatch, nvidia=[GpuDevice("NVIDIA GeForce RTX 4070", "nvidia", 12288)])
    probe_gpus()
    clock["now"] += gpu_probe._GPU_TTL_S - 1
    probe_gpus()
    assert counts["nvidia"] == 1
    clock["now"] += 2
    probe_gpus()
    assert counts["nvidia"] == 2


def test_a_machine_with_no_gpu_reports_zeroes_not_a_fake_card(monkeypatch) -> None:
    _stub_sensors(monkeypatch)
    assert probe_primary_vram() == (False, 0, 0, "", "")


def test_probe_primary_vram_flags_nvidia_for_the_cuda_paths(monkeypatch) -> None:
    _stub_sensors(
        monkeypatch,
        nvidia=[GpuDevice("NVIDIA GeForce RTX 4070", "nvidia", 12288, 11000, True, "nvidia-smi", "cuda")],
    )
    assert probe_primary_vram() == (True, 12288, 11000, "NVIDIA GeForce RTX 4070", "nvidia")


def test_a_non_nvidia_primary_is_reported_without_the_nvidia_flag(monkeypatch) -> None:
    _stub_sensors(
        monkeypatch, amd=[GpuDevice("Radeon RX 7900 XTX", "amd", 24576, 23000, True, "amd-smi", "hip")]
    )
    is_nvidia, total, free, name, vendor = probe_primary_vram()
    assert is_nvidia is False
    assert (total, free, name, vendor) == (24576, 23000, "Radeon RX 7900 XTX", "amd")
    assert isinstance(total, int) and isinstance(free, int)


# --------------------------------------------------------------------------
# runtime / card compatibility
# --------------------------------------------------------------------------


def test_runtime_matches_gpu_is_capability_not_brand() -> None:
    assert runtime_matches_gpu("nvidia", runtime_id="win-cuda-12.4-x64") is True
    assert runtime_matches_gpu("amd", runtime_id="win-cuda-12.4-x64") is False
    assert runtime_matches_gpu("amd", runtime_id="win-vulkan-x64") is True
    assert runtime_matches_gpu("intel", binary="llama-server-vulkan.exe") is True
    assert runtime_matches_gpu("amd", runtime_id="win-cpu-x64") is False
    assert runtime_matches_gpu("amd", runtime_id="linux-vulkan-x64") is True
    assert runtime_matches_gpu("nvidia", runtime_id="linux-vulkan-x64") is True
    assert runtime_matches_gpu("amd", runtime_id="linux-cpu-x64") is False


@pytest.mark.parametrize("vendor", ["nvidia", "amd", "intel", "apple", "other", ""])
def test_a_cpu_only_runtime_is_refused_for_every_card(vendor: str) -> None:
    assert runtime_matches_gpu(vendor, runtime_id="win-cpu-x64") is False


@pytest.mark.parametrize(
    ("vendor", "runtime_id", "expected"),
    [
        ("apple", "mac-metal-arm64", True),
        ("apple", "mac-vulkan-arm64", False),
        ("intel", "win-sycl-x64", True),
        ("intel", "win-hip-x64", False),
        ("other", "win-vulkan-x64", True),
        ("other", "win-cuda-12.4-x64", False),
        ("amd", "linux-rocm-6.1-x64", True),
    ],
)
def test_each_vendor_demands_a_backend_it_can_actually_run(vendor: str, runtime_id: str, expected: bool) -> None:
    assert runtime_matches_gpu(vendor, runtime_id=runtime_id) is expected


def test_the_binary_path_counts_as_evidence_when_the_id_is_empty() -> None:
    assert runtime_matches_gpu("amd", binary=Path("runtimes/llama-b1-vulkan/llama-server.exe")) is True
    assert runtime_matches_gpu("apple", binary=Path("runtimes/llama-metal/llama-server")) is True
    assert runtime_matches_gpu("apple", binary=Path("runtimes/llama-vulkan/llama-server")) is False


def test_an_unlabelled_runtime_is_only_assumed_usable_on_nvidia() -> None:
    # Historic CUDA-first builds carry no marker at all; every other vendor
    # must show its backend before Remedy will offload to the card.
    assert runtime_matches_gpu("nvidia") is True
    assert runtime_matches_gpu("amd") is False
    assert runtime_matches_gpu("intel") is False
    assert runtime_matches_gpu("apple") is False
    assert runtime_matches_gpu("other") is False


# --------------------------------------------------------------------------
# what the autofit planner does with the reading
# --------------------------------------------------------------------------


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
