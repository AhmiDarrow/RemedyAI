"""RMB engine-knob bugsweep: parity knobs must persist, gate, and restart.

Regression coverage for the QoL pass:
- KoboldCpp-class knobs (typical/tfs/mirostat/presence/frequency/tensor-split/
  samplers/rope-scaling/yarn/no-kv-offload) were silently dropped by
  apply_rmb_settings -> PATCHes vanished, engine never got the flags.
- _build_cmd always emitted --tfs 0.0 / --mirostat-tau 0.0 / --presence-penalty 0.0.
- ctx_size was hard-capped at 131072 (user wants no caps).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.runtime.rmb import service as svc
from remedy.runtime.rmb.config import (
    default_state,
    load_rmb_json,
    merge_state,
    save_rmb_json,
)

_PARITY = {
    "typical_p",
    "tfs_z",
    "mirostat",
    "mirostat_tau",
    "mirostat_eta",
    "presence_penalty",
    "frequency_penalty",
    "main_gpu",
    "threads_batch",
    "tensor_split",
    "samplers",
    "rope_scaling",
    "yarn_orig_ctx",
    "yarn_factor",
    "yarn_beta_fast",
    "yarn_beta_slow",
    "no_kv_offload",
    "dry_multiplier",
    "dry_base",
    "dry_allowed_length",
    "dry_penalty_last_n",
    "xtc_probability",
    "xtc_threshold",
}


def _noop_runtime(monkeypatch, *, running: bool = False):
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.is_running",
        lambda *a, **k: running,
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.managed_process_alive",
        lambda: running,
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.start_rmb_server",
        lambda **k: {"ok": True, "ctx_size": 8192, "base_url": "http://127.0.0.1:8787/v1"},
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.stop_rmb_server",
        lambda **k: {"ok": True},
    )


def test_default_state_has_dry_xtc():
    st = default_state()
    for key in _PARITY:
        assert key in st, f"default_state missing {key}"
    assert st["dry_multiplier"] == 0.0
    assert st["xtc_probability"] == 0.0


def test_process_keys_include_parity_knobs():
    assert _PARITY <= svc._RMB_PROCESS_KEYS, (
        "parity knobs missing from _RMB_PROCESS_KEYS -> live apply never restarts"
    )


def test_engine_kwargs_include_parity_knobs():
    state = merge_state(
        {
            "typical_p": 0.9,
            "tfs_z": 0.8,
            "mirostat": 2,
            "mirostat_tau": 5.0,
            "mirostat_eta": 0.1,
            "presence_penalty": 0.5,
            "frequency_penalty": 0.4,
            "tensor_split": "0,512",
            "samplers": "top_k;top_p;min_p;temp",
            "rope_scaling": "yarn",
            "yarn_orig_ctx": 8192,
            "yarn_factor": 4.0,
            "no_kv_offload": True,
            "dry_multiplier": 1.2,
            "dry_base": 1.75,
            "dry_allowed_length": 2,
            "dry_penalty_last_n": -1,
            "xtc_probability": 0.5,
            "xtc_threshold": 0.2,
        }
    )
    kw = svc._engine_kwargs(state)
    assert kw["typical_p"] == 0.9
    assert kw["mirostat"] == 2
    assert kw["tensor_split"] == "0,512"
    assert kw["rope_scaling"] == "yarn"
    assert kw["yarn_orig_ctx"] == 8192
    assert kw["dry_multiplier"] == 1.2
    assert kw["xtc_probability"] == 0.5
    assert kw["no_kv_offload"] is True


def test_build_cmd_omits_zero_parity_flags():
    cmd = svc._build_cmd(
        Path("C:/fake/llama-server.exe"),
        Path("C:/fake/model.gguf"),
        host="127.0.0.1",
        port=8787,
        ctx=8192,
        ngl=-1,
        threads=8,
        parallel=1,
        flash_attn=True,
        host_profile={},
        typical_p=0.0,
        tfs_z=0.0,
        mirostat=0,
        mirostat_tau=0.0,
        mirostat_eta=0.0,
        presence_penalty=0.0,
        frequency_penalty=0.0,
        dry_multiplier=0.0,
        xtc_probability=0.0,
    )
    text = " ".join(cmd)
    assert "--tfs" not in text
    assert "--mirostat-tau" not in text
    assert "--mirostat-eta" not in text
    assert "--presence-penalty" not in text
    assert "--frequency-penalty" not in text
    assert "--dry-multiplier" not in text
    assert "--xtc-probability" not in text


def test_build_cmd_mirostat_tau_requires_active_mirostat():
    cmd = svc._build_cmd(
        Path("C:/fake/llama-server.exe"),
        Path("C:/fake/model.gguf"),
        host="127.0.0.1",
        port=8787,
        ctx=8192,
        ngl=-1,
        threads=8,
        parallel=1,
        flash_attn=True,
        host_profile={},
        mirostat=0,
        mirostat_tau=5.0,
        mirostat_eta=0.1,
    )
    text = " ".join(cmd)
    assert "--mirostat-tau" not in text, "tau must not emit when mirostat is off"
    assert "--mirostat 0" in text


def test_build_cmd_emits_active_parity_flags():
    cmd = svc._build_cmd(
        Path("C:/fake/llama-server.exe"),
        Path("C:/fake/model.gguf"),
        host="127.0.0.1",
        port=8787,
        ctx=8192,
        ngl=-1,
        threads=8,
        parallel=1,
        flash_attn=True,
        host_profile={},
        typical_p=0.9,
        tfs_z=0.8,
        mirostat=2,
        mirostat_tau=5.0,
        mirostat_eta=0.1,
        presence_penalty=0.5,
        frequency_penalty=0.4,
        main_gpu=0,
        threads_batch=4,
        tensor_split="0,512",
        samplers="top_k;top_p;min_p;temp",
        rope_scaling="yarn",
        yarn_orig_ctx=8192,
        yarn_factor=4.0,
        no_kv_offload=True,
        dry_multiplier=1.2,
        dry_base=1.75,
        dry_allowed_length=2,
        dry_penalty_last_n=-1,
        xtc_probability=0.5,
        xtc_threshold=0.2,
    )
    text = " ".join(cmd)
    assert "--typical 0.9" in text
    assert "--tfs 0.8" in text
    assert "--mirostat 2" in text
    assert "--mirostat-tau 5.0" in text
    assert "--mirostat-eta 0.1" in text
    assert "--presence-penalty 0.5" in text
    assert "--frequency-penalty 0.4" in text
    assert "--tensor-split 0,512" in text
    assert "--samplers top_k;top_p;min_p;temp" in text
    assert "--rope-scaling yarn" in text
    assert "--yarn-orig-ctx 8192" in text
    assert "--no-kv-offload" in text
    assert "--dry-multiplier 1.2" in text
    assert "--dry-base 1.75" in text
    assert "--dry-allowed-length 2" in text
    assert "--dry-penalty-last-n -1" in text
    assert "--xtc-probability 0.5" in text
    assert "--xtc-threshold 0.2" in text


def test_apply_rmb_settings_persists_parity_knobs(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    home = str(tmp_path)
    save_rmb_json(
        merge_state(
            {
                "enabled": True,
                "ctx_size": 8192,
                "model_id": "qwen25-coder-7b",
            }
        ),
        home,
    )
    _noop_runtime(monkeypatch)
    out = svc.apply_rmb_settings(
        {
            "typical_p": 0.9,
            "tfs_z": 0.8,
            "mirostat": 2,
            "mirostat_tau": 5.0,
            "mirostat_eta": 0.1,
            "presence_penalty": 0.5,
            "frequency_penalty": 0.4,
            "tensor_split": "0,512",
            "samplers": "top_k;top_p;min_p;temp",
            "rope_scaling": "yarn",
            "yarn_orig_ctx": 8192,
            "yarn_factor": 4.0,
            "no_kv_offload": True,
            "dry_multiplier": 1.2,
            "dry_allowed_length": 2,
            "dry_penalty_last_n": -1,
            "xtc_probability": 0.5,
            "xtc_threshold": 0.2,
        },
        home_dir=home,
        live=False,
    )
    st = merge_state(load_rmb_json(home))
    assert float(st["typical_p"]) == pytest.approx(0.9)
    assert float(st["tfs_z"]) == pytest.approx(0.8)
    assert int(st["mirostat"]) == 2
    assert float(st["mirostat_tau"]) == pytest.approx(5.0)
    assert float(st["presence_penalty"]) == pytest.approx(0.5)
    assert str(st["tensor_split"]) == "0,512"
    assert str(st["samplers"]) == "top_k;top_p;min_p;temp"
    assert str(st["rope_scaling"]) == "yarn"
    assert int(st["yarn_orig_ctx"]) == 8192
    assert bool(st["no_kv_offload"]) is True
    assert float(st["dry_multiplier"]) == pytest.approx(1.2)
    assert int(st["dry_allowed_length"]) == 2
    assert int(st["dry_penalty_last_n"]) == -1
    assert float(st["xtc_probability"]) == pytest.approx(0.5)
    assert out.get("ok", True) is not False


def test_apply_live_restarts_on_parity_knob_change(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    home = str(tmp_path)
    save_rmb_json(
        merge_state(
            {
                "enabled": True,
                "ctx_size": 8192,
                "model_id": "qwen25-coder-7b",
                "typical_p": 0.0,
            }
        ),
        home,
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.is_running",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.managed_process_alive",
        lambda: True,
    )
    stops: list[int] = []
    starts: list[int] = []
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.stop_rmb_server",
        lambda **k: stops.append(1) or {"ok": True},
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.start_rmb_server",
        lambda **k: starts.append(1)
        or {"ok": True, "ctx_size": 8192, "base_url": "http://127.0.0.1:8787/v1"},
    )

    out = svc.apply_rmb_settings(
        {"typical_p": 0.95},
        home_dir=home,
        live=True,
        wait_s=1.0,
    )
    assert stops and starts, "parity knob change must restart the running host"
    la = out.get("live_apply", {})
    assert la.get("restarted") is True
    assert "typical_p" in la.get("process_keys_changed", [])


def test_ctx_size_no_hard_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    home = str(tmp_path)
    save_rmb_json(merge_state({"enabled": True, "ctx_size": 8192}), home)
    _noop_runtime(monkeypatch)
    svc.apply_rmb_settings(
        {"ctx_size": 262144},
        home_dir=home,
        live=False,
    )
    st = merge_state(load_rmb_json(home))
    assert int(st["ctx_size"]) == 262144, "ctx must not be capped at 131072"
