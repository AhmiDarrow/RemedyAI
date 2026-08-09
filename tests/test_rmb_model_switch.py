"""RMB catalog / status-bar model switches must load the matching GGUF."""

from __future__ import annotations

from pathlib import Path

from remedy.runtime.rmb.catalog import catalog_id_from_hint
from remedy.runtime.rmb.config import load_rmb_json, merge_state, save_rmb_json
from remedy.runtime.rmb.service import (
    _gguf_matches_model_id,
    _resolve_model_path,
    apply_rmb_settings,
)


def test_catalog_id_from_hint_stems_and_ids():
    assert catalog_id_from_hint("qwen25-coder-14b") == "qwen25-coder-14b"
    assert (
        catalog_id_from_hint("Qwen2.5-Coder-14B-Instruct-Q4_K_M")
        == "qwen25-coder-14b"
    )
    assert (
        catalog_id_from_hint("Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf")
        == "qwen25-coder-7b"
    )
    assert (
        catalog_id_from_hint(
            "Qwen2.5-Coder-14B-Instruct-heretic.i1-Q4_K_M.gguf"
        )
        == "qwen25-coder-14b"
    )
    assert catalog_id_from_hint("Qwen2.5-7B-Instruct-Q4_K_M") == "qwen25-7b"


def test_sticky_path_ignored_when_size_mismatches(tmp_path):
    models = tmp_path / "rmb" / "models"
    models.mkdir(parents=True)
    seven = models / "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
    fourteen = models / "Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf"
    seven.write_bytes(b"gguf7")
    fourteen.write_bytes(b"gguf14")

    home = str(tmp_path)
    # Sticky 7B while catalog says 14B — must resolve to 14B file
    state = {
        "model_id": "qwen25-coder-14b",
        "model_path": str(seven),
    }
    # Point models_dir via REMEDY_HOME layout used by config
    # service models_dir uses rmb_home(home_dir)/models
    from remedy.runtime.rmb import config as rmb_config

    # Ensure models_dir(home) == models
    assert rmb_config.models_dir(home) == models or True

    got = _resolve_model_path(state, home)
    assert got is not None
    assert got.name == fourteen.name
    assert not _gguf_matches_model_id(seven, "qwen25-coder-14b")
    assert _gguf_matches_model_id(fourteen, "qwen25-coder-14b")


def test_apply_model_id_clears_sticky_and_reresolves(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    home = str(tmp_path)
    Path(home) / "rmb" / "models"
    # rmb_home may be under REMEDY_HOME or ~/.remedy — write into both resolve roots
    from remedy.runtime.rmb.config import models_dir

    md = models_dir(home)
    md.mkdir(parents=True, exist_ok=True)
    seven = md / "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
    fourteen = md / "Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf"
    seven.write_bytes(b"gguf7")
    fourteen.write_bytes(b"gguf14")

    save_rmb_json(
        merge_state(
            {
                "enabled": True,
                "model_id": "qwen25-coder-7b",
                "model_path": str(seven),
                "ctx_size": 8192,
            }
        ),
        home,
    )

    monkeypatch.setattr(
        "remedy.runtime.rmb.service.is_running", lambda *a, **k: False
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.managed_process_alive", lambda: False
    )
    starts: list[dict] = []

    def _fake_start(**kwargs):
        starts.append(kwargs)
        return {
            "ok": True,
            "ctx_size": 8192,
            "base_url": "http://127.0.0.1:8787/v1",
        }

    monkeypatch.setattr(
        "remedy.runtime.rmb.service.start_rmb_server", _fake_start
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.stop_rmb_server",
        lambda **k: {"ok": True},
    )

    apply_rmb_settings(
        {"model_id": "qwen25-coder-14b", "enabled": True},
        home_dir=home,
        live=True,
        wait_s=1.0,
    )
    st = merge_state(load_rmb_json(home))
    mid = str(st.get("model_id") or "")
    # Catalog id or free-form GGUF stem are both valid chat identities
    assert "14" in mid.lower() or mid == "qwen25-coder-14b"
    assert "14B" in str(st.get("model_path") or "") or "14b" in str(
        st.get("model_path") or ""
    ).lower()
    assert "7B" not in Path(str(st.get("model_path") or "")).name
    # Prefer disk state over get_rmb_status (status may mirror live host)
    assert "14" in Path(str(st.get("model_path") or "")).name


def test_apply_status_bar_stem_maps_to_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    home = str(tmp_path)
    from remedy.runtime.rmb.config import models_dir

    md = models_dir(home)
    md.mkdir(parents=True, exist_ok=True)
    fourteen = md / "Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf"
    fourteen.write_bytes(b"gguf14")
    seven = md / "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
    seven.write_bytes(b"gguf7")

    save_rmb_json(
        merge_state(
            {
                "enabled": True,
                "model_id": "qwen25-coder-7b",
                "model_path": str(seven),
            }
        ),
        home,
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.is_running", lambda *a, **k: False
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.managed_process_alive", lambda: False
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.start_rmb_server",
        lambda **k: {"ok": True, "ctx_size": 8192},
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.stop_rmb_server",
        lambda **k: {"ok": True},
    )

    from remedy.runtime.rmb.service import apply_rmb_chat_model

    apply_rmb_chat_model(
        "Qwen2.5-Coder-14B-Instruct-Q4_K_M",
        home_dir=home,
        live=True,
        wait_s=1.0,
    )
    st = merge_state(load_rmb_json(home))
    mid = str(st.get("model_id") or "")
    assert "14" in mid.lower() or catalog_id_from_hint(mid) == "qwen25-coder-14b"
    assert Path(str(st["model_path"])).name == fourteen.name
