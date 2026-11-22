"""Unified local-mind engine: catalog, command line, digest, knobs."""

from __future__ import annotations

from pathlib import Path

from remedy.core import organism_knobs as knobs
from remedy.runtime.catalog import (
    DEFAULT_LOCAL_MODEL_ID,
    LLAMA_CPP_TAG,
    catalog_public,
    get_model_spec,
)
from remedy.runtime.media_digest import (
    DIGEST_SCHEMA,
    format_digest_for_muscle,
    parse_digest,
)
from remedy.runtime.mind_engine import (
    build_mind_server_command,
    command_fallbacks,
    mind_n_gpu_layers,
    resolve_spec_type,
    select_quant,
)
from remedy.runtime.mdl import MDL_TIERS
from remedy.runtime.rmb.mode import should_skip_vision_stack


def test_catalog_is_the_4b_mind() -> None:
    assert DEFAULT_LOCAL_MODEL_ID == "qwen35-4b-mind"
    assert LLAMA_CPP_TAG == "b12628"
    spec = get_model_spec()
    assert spec.model_file.endswith("Q6_K.gguf")
    assert spec.fallback_model_file.endswith("Q4_K_M.gguf")
    assert spec.mmproj_file == "mmproj-F16.gguf"
    assert spec.model_sha256 == "TODO"
    assert spec.mmproj_sha256 and len(spec.mmproj_sha256) == 64
    assert spec.has_mtp is True
    pub = catalog_public()
    assert pub["required"] is True
    assert pub["llama_cpp_tag"] == "b12628"


def test_legacy_ids_resolve_to_the_mind() -> None:
    assert get_model_spec("smolvlm2-2.2b").id == "qwen35-4b-mind"
    assert get_model_spec("qwen2.5-vl-3b").id == "qwen35-4b-mind"


def test_mdl_tiers_share_one_port() -> None:
    ports = {t.port for t in MDL_TIERS.values()}
    assert ports == {8740}


def test_rmb_auto_start_does_not_park_the_mind(tmp_path, monkeypatch) -> None:
    from remedy.runtime.rmb.config import load_rmb_json, merge_state, save_rmb_json

    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    st = merge_state(load_rmb_json(str(tmp_path)))
    st["auto_start"] = True
    st["vision_suspended"] = False
    save_rmb_json(st, str(tmp_path))
    assert should_skip_vision_stack({"home_dir": str(tmp_path)}) is False
    st["vision_suspended"] = True
    save_rmb_json(st, str(tmp_path))
    assert should_skip_vision_stack({"home_dir": str(tmp_path)}) is True


def test_command_includes_efficiency_flags(tmp_path: Path) -> None:
    binary = tmp_path / "llama-server"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    model = tmp_path / "Qwen3.5-4B-Q6_K.gguf"
    model.write_bytes(b"xx")
    mmproj = tmp_path / "mmproj-F16.gguf"
    mmproj.write_bytes(b"yy")
    # Pretend the binary supports the modern flags.
    help_blob = (
        b"--parallel --flash-attn --jinja --chat-template-kwargs "
        b"--cache-prompt --cache-ram --cache-idle-slots --mmap "
        b"--cache-type-k --image-max-tokens --spec-type draft-mtp "
        b"ngram-mod --spec-draft-n-max --kv-unified-per-slot"
    )
    cmd = build_mind_server_command(
        binary,
        model,
        mmproj,
        host="127.0.0.1",
        port=8740,
        n_gpu_layers=-1,
        help_blob=help_blob,
        allow_mtp=True,
    )
    joined = " ".join(cmd)
    assert "--mmproj" in cmd
    assert "--parallel" in cmd
    assert "--flash-attn" in cmd
    assert "--jinja" in cmd
    assert "--cache-prompt" in cmd
    assert "--mmap" in cmd
    assert "--image-max-tokens" in cmd
    assert "--cache-type-k" in cmd
    assert "enable_thinking" in joined
    assert "false" in joined
    # MTP is allowed when the catalog marks has_mtp (default).
    assert "--spec-type" in cmd


def test_command_fallbacks_drop_spec() -> None:
    cmd = [
        "llama-server",
        "--mmproj",
        "m.gguf",
        "--spec-type",
        "draft-mtp",
        "--spec-draft-n-max",
        "3",
        "--flash-attn",
        "on",
    ]
    variants = command_fallbacks(cmd)
    assert variants
    # At least one variant drops MTP entirely; another may swap in ngram-mod.
    assert any("--spec-type" not in v for v in variants)
    assert any("ngram-mod" in v for v in variants)


def test_mind_yields_gpu_when_rmb_runs() -> None:
    assert mind_n_gpu_layers(requested=-1, rmb_running=True) == 0
    assert mind_n_gpu_layers(requested=-1, rmb_running=False) == -1


def test_spec_off_knob() -> None:
    assert resolve_spec_type(cfg={"advanced": {"mind_spec": "off"}}) is None
    assert resolve_spec_type(cfg={"advanced": {"mind_spec": "ngram-mod"}}) == "ngram-mod"


def test_select_quant_honors_knob() -> None:
    assert select_quant({"advanced": {"mind_quant": "q4_k_m"}}) == "q4_k_m"
    assert select_quant({"advanced": {"mind_quant": "q6_k"}}) == "q6_k"


def test_digest_parse_and_format() -> None:
    raw = (
        '{"summary": "A red login form", "text_in_image": "Sign in", '
        '"objects": ["button"], "layout": "centered card", '
        '"ui_elements": ["Sign in"], "metadata": {}, '
        '"needs_pixels": false, "reason": "readable"}'
    )
    d = parse_digest(raw)
    assert d["summary"] == "A red login form"
    assert d["needs_pixels"] is False
    assert "Sign in" in d["text_in_image"]
    text = format_digest_for_muscle(d, path="shot.png")
    assert "pixels not attached" in text
    assert "shot.png" in text
    assert DIGEST_SCHEMA["required"] == ["summary", "needs_pixels"]


def test_mind_knob_defaults() -> None:
    table = knobs.effective_table({})
    assert table["mind_slots"] == 2
    assert table["mind_ctx"] == 8192
    assert table["mind_flash_attn"] is True
    assert table["mind_kv_cache"] == "q8_0"
    assert table["mind_spec"] == "auto"
    assert table["mind_image_max_tokens"] == 1024
    assert table["mind_thinking"] is False
    assert table["always_send_images"] is False
    assert table["mind_quant"] == "auto"
