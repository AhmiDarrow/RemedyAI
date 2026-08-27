"""RMB host autoconfig: MTP / coder GGUF → llama-server flags without user knobs."""

from __future__ import annotations

from pathlib import Path

from remedy.runtime.rmb.service import (
    _build_cmd,
    binary_supports_draft_mtp,
    detect_gguf_host_profile,
)


def test_detect_mtp_qwopus_name():
    p = Path("Qwopus3.5-9B-Coder-MTP-Q4_K_M.gguf")
    prof = detect_gguf_host_profile(p)
    assert prof["mtp"] is True
    assert prof["coder"] is True
    assert prof["qwen3_family"] is True
    assert prof["force_parallel_1"] is True
    assert prof["spec_type"] == "draft-mtp"
    assert int(prof["spec_draft_n_max"] or 0) >= 2
    assert prof["use_jinja"] is True
    assert prof["chat_template_kwargs"] is None
    assert prof["qwen_thinking_toggle"] is True


def test_detect_non_mtp_coder():
    p = Path("Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf")
    prof = detect_gguf_host_profile(p)
    assert prof["mtp"] is False
    assert prof["coder"] is True
    assert prof["spec_type"] is None
    assert prof["force_parallel_1"] is False


def test_build_cmd_forces_parallel_1_and_spec_when_capable(tmp_path, monkeypatch):
    """When binary advertises draft-mtp, MTP GGUF gets speculative flags + slot 1."""
    fake_bin = tmp_path / "llama-server.exe"
    fake_bin.write_bytes(b"stub")
    # Sibling DLL carries the capability strings (Windows CUDA layout)
    (tmp_path / "llama-common.dll").write_bytes(b"xx --spec-type draft-mtp spec-draft-n-max yy")

    # Clear capability cache for this path
    from remedy.runtime.rmb import service as svc

    svc._spec_cap_cache.clear()
    assert binary_supports_draft_mtp(fake_bin) is True

    model = tmp_path / "Foo-Coder-MTP-Q4.gguf"
    model.write_bytes(b"0")
    cmd = _build_cmd(
        fake_bin,
        model,
        host="127.0.0.1",
        port=8787,
        ctx=8192,
        ngl=-1,
        threads=0,
        parallel=4,
        flash_attn=True,
    )
    assert cmd[cmd.index("--parallel") + 1] == "1"
    assert "--spec-type" in cmd
    assert "draft-mtp" in cmd
    assert "--spec-draft-n-max" in cmd
    assert "--jinja" in cmd


def test_build_cmd_soft_skips_mtp_when_binary_lacks_flags(tmp_path):
    fake_bin = tmp_path / "llama-server"
    fake_bin.write_bytes(b"no speculative support here")
    from remedy.runtime.rmb import service as svc

    svc._spec_cap_cache.clear()
    assert binary_supports_draft_mtp(fake_bin) is False

    model = tmp_path / "Bar-MTP-9B.gguf"
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
    # Still loads model; parallel forced to 1; no unknown flags
    assert cmd[cmd.index("--parallel") + 1] == "1"
    assert "--spec-type" not in cmd


def test_sibling_mtp_draft_pairs_without_mtp_in_main_name(tmp_path):
    """Qwen 3.8 layout: Qwen3.8-27B-Q4_0.gguf + mtp-Qwen3.8-27B-Q4_0.gguf."""
    main = tmp_path / "Qwen3.8-27B-Q4_0.gguf"
    draft = tmp_path / "mtp-Qwen3.8-27B-Q4_0.gguf"
    main.write_bytes(b"0")
    draft.write_bytes(b"1")
    prof = detect_gguf_host_profile(main, sniff_template=False)
    assert prof["mtp"] is True
    assert "sibling_draft" in (prof.get("reasons") or [])
    assert Path(str(prof.get("model_draft"))) == draft
    assert prof["spec_type"] == "draft-mtp"
    assert prof["force_parallel_1"] is True


def test_build_cmd_passes_model_draft_for_sibling(tmp_path, monkeypatch):
    fake_bin = tmp_path / "llama-server.exe"
    fake_bin.write_bytes(b"stub")
    (tmp_path / "llama-common.dll").write_bytes(
        b"xx --spec-type draft-mtp spec-draft-n-max --model-draft --reasoning off yy"
    )
    from remedy.runtime.rmb import service as svc

    svc._spec_cap_cache.clear()
    svc._flag_cap_cache.clear()
    main = tmp_path / "Qwen3.8-27B-Q4_0.gguf"
    draft = tmp_path / "mtp-Qwen3.8-27B-Q4_0.gguf"
    main.write_bytes(b"0")
    draft.write_bytes(b"1")
    cmd = _build_cmd(
        fake_bin,
        main,
        host="127.0.0.1",
        port=8787,
        ctx=4096,
        ngl=40,
        threads=0,
        parallel=1,
        flash_attn=True,
    )
    assert "--model-draft" in cmd
    assert str(draft) in cmd
    assert "--n-gpu-layers-draft" in cmd
    assert "--spec-type" in cmd


def test_build_cmd_enable_mtp_false_strips_flags(tmp_path):
    fake_bin = tmp_path / "llama-server.exe"
    fake_bin.write_bytes(b"stub")
    (tmp_path / "llama-common.dll").write_bytes(b"draft-mtp --spec-type")
    from remedy.runtime.rmb import service as svc

    svc._spec_cap_cache.clear()
    model = tmp_path / "X-MTP.gguf"
    model.write_bytes(b"0")
    cmd = _build_cmd(
        fake_bin,
        model,
        host="127.0.0.1",
        port=8787,
        ctx=4096,
        ngl=0,
        threads=0,
        parallel=1,
        flash_attn=False,
        enable_mtp=False,
    )
    assert "--spec-type" not in cmd


def test_build_cmd_owner_draft_enables_classic_speculation(tmp_path):
    """Owner-typed draft GGUF on a non-MTP main → --model-draft, no MTP flags."""
    from remedy.runtime.rmb.host_profile import overlay_owner_on_profile

    fake_bin = tmp_path / "llama-server.exe"
    fake_bin.write_bytes(b"stub")
    (tmp_path / "llama-common.dll").write_bytes(b"xx --model-draft --draft-max yy")
    from remedy.runtime.rmb import service as svc

    svc._spec_cap_cache.clear()
    svc._flag_cap_cache.clear()

    main = tmp_path / "Qwen3.5-9B-Q4_K_M.gguf"
    main.write_bytes(b"0")
    draft = tmp_path / "Qwen3.5-0.5B-draft.gguf"
    draft.write_bytes(b"0")
    prof = overlay_owner_on_profile(
        detect_gguf_host_profile(main),
        {"model_draft": str(draft), "spec_draft_n_max": 4},
    )
    cmd = _build_cmd(
        fake_bin,
        main,
        host="127.0.0.1",
        port=8787,
        ctx=8192,
        ngl=40,
        threads=0,
        parallel=1,
        flash_attn=False,
        host_profile=prof,
    )
    assert "--model-draft" in cmd
    assert str(draft) in cmd
    assert "--n-gpu-layers-draft" in cmd
    assert "--draft-max" in cmd
    assert cmd[cmd.index("--draft-max") + 1] == "4"
    # Classic draft is not MTP — no MTP-specific flags.
    assert "--spec-type" not in cmd


def test_build_cmd_owner_draft_skipped_when_binary_lacks_flag(tmp_path):
    from remedy.runtime.rmb.host_profile import overlay_owner_on_profile

    fake_bin = tmp_path / "llama-server.exe"
    fake_bin.write_bytes(b"no speculative support here")
    from remedy.runtime.rmb import service as svc

    svc._spec_cap_cache.clear()
    svc._flag_cap_cache.clear()

    main = tmp_path / "Qwen3.5-9B-Q4_K_M.gguf"
    main.write_bytes(b"0")
    draft = tmp_path / "Qwen3.5-0.5B-draft.gguf"
    draft.write_bytes(b"0")
    prof = overlay_owner_on_profile(
        detect_gguf_host_profile(main), {"model_draft": str(draft)}
    )
    cmd = _build_cmd(
        fake_bin,
        main,
        host="127.0.0.1",
        port=8787,
        ctx=8192,
        ngl=0,
        threads=0,
        parallel=1,
        flash_attn=False,
        host_profile=prof,
    )
    assert "--model-draft" not in cmd


def test_build_cmd_owner_draft_suppressed_when_mtp_owner_off(tmp_path):
    from remedy.runtime.rmb.host_profile import overlay_owner_on_profile

    fake_bin = tmp_path / "llama-server.exe"
    fake_bin.write_bytes(b"stub")
    (tmp_path / "llama-common.dll").write_bytes(b"xx --model-draft yy")
    from remedy.runtime.rmb import service as svc

    svc._spec_cap_cache.clear()
    svc._flag_cap_cache.clear()

    main = tmp_path / "Qwen3.5-9B-Q4_K_M.gguf"
    main.write_bytes(b"0")
    draft = tmp_path / "Qwen3.5-0.5B-draft.gguf"
    draft.write_bytes(b"0")
    prof = overlay_owner_on_profile(
        detect_gguf_host_profile(main),
        {"model_draft": str(draft), "enable_mtp": False},
    )
    cmd = _build_cmd(
        fake_bin,
        main,
        host="127.0.0.1",
        port=8787,
        ctx=8192,
        ngl=0,
        threads=0,
        parallel=1,
        flash_attn=False,
        host_profile=prof,
    )
    assert "--model-draft" not in cmd
