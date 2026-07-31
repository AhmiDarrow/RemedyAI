"""Visual decoder module: catalog, capabilities, attachments modes, service."""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from unittest.mock import patch

from remedy.core.providers import AnthropicProvider
from remedy.interfaces.attachments import build_multimodal_user_content, save_upload
from remedy.vision.capabilities import resolve_supports_vision, supports_vision
from remedy.vision.catalog import (
    DEFAULT_MODEL_ID,
    LLAMA_CPP_TAG,
    LLAMA_RUNTIMES,
    VISION_MODELS,
    catalog_public,
    get_model_spec,
    get_runtime_spec,
    total_install_bytes,
)
from remedy.vision.config import (
    save_vision_json,
    vision_section_from_config,
)
from remedy.vision.prompts import decode_user_prompt
from remedy.vision.service import decode_for_turn, get_status


def test_default_model_is_smolvlm2_2_2b():
    assert DEFAULT_MODEL_ID == "smolvlm2-2.2b"
    assert DEFAULT_MODEL_ID in VISION_MODELS
    spec = get_model_spec()
    assert spec.model_file.endswith("Q4_K_M.gguf")
    assert "mmproj" in spec.mmproj_file
    assert "SmolVLM2" in spec.model_file
    assert getattr(spec, "license", "") in ("Apache-2.0", "Apache 2.0", "")
    assert spec.approx_download_bytes > 1_000_000_000  # ~1.6GB model+mmproj
    assert spec.approx_download_bytes < 2_500_000_000
    pub = catalog_public()
    assert pub["default_model_id"] == DEFAULT_MODEL_ID
    assert any(m["id"] == DEFAULT_MODEL_ID for m in pub["models"])


def test_total_install_bytes_includes_runtime():
    n = total_install_bytes()
    assert n > get_model_spec().approx_download_bytes


def test_supports_vision_heuristics():
    assert supports_vision("deepseek", "deepseek-chat") is False
    assert supports_vision("openai", "gpt-4o-mini") is True
    assert supports_vision("xai", "grok-2-vision-1212") is True
    assert supports_vision("mistral", "codestral-latest") is False
    assert supports_vision("ollama", "llama3.2") is False
    assert supports_vision("anthropic", "claude-3-5-sonnet-latest") is True


def test_resolve_supports_vision_catalog_flag():
    assert resolve_supports_vision("deepseek", "deepseek-chat") is False
    assert resolve_supports_vision("openai", "gpt-4o") is True
    # force_decode overrides native
    assert (
        resolve_supports_vision(
            "openai",
            "gpt-4o",
            config={"vision": {"force_decode": True}},
        )
        is False
    )


def test_build_multimodal_decode_mode(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    meta = save_upload(
        session_id="s1",
        filename="dot.png",
        data=png,
        content_type="image/png",
        home_dir=home,
    )
    brief = "### Visual decode: dot.png\n- **Scene:** a pixel\n"
    content = build_multimodal_user_content(
        "what is this?",
        [meta],
        vision_mode="decode",
        decode_brief=brief,
    )
    assert isinstance(content, str)
    assert "Visual decode" in content
    assert "image_url" not in content


def test_build_multimodal_native_still_list(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    meta = save_upload(
        session_id="s2",
        filename="dot.png",
        data=png,
        content_type="image/png",
        home_dir=home,
    )
    content = build_multimodal_user_content(
        "see", [meta], vision_mode="native", home_dir=home
    )
    assert isinstance(content, list)
    assert any(p.get("type") == "image_url" for p in content)


def test_get_status_not_installed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "remedy-home"))
    status = get_status({"home_dir": str(tmp_path / "remedy-home"), "vision": {"enabled": False}})
    assert status["model_id"] == DEFAULT_MODEL_ID
    assert status["installed"] is False
    assert status["ready"] is False
    assert status["not_ready_hint"]


def test_decode_for_turn_native_skips_decoder(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    meta = save_upload(
        session_id="s3",
        filename="dot.png",
        data=png,
        content_type="image/png",
        home_dir=home,
    )
    res = decode_for_turn(
        [meta],
        provider="openai",
        model="gpt-4o-mini",
        cfg={"home_dir": str(home), "vision": {"enabled": True}},
    )
    assert res["mode"] == "native"


def test_force_decode_falls_back_to_native_when_not_ready(tmp_path: Path):
    """Prefer-local must not blind a vision model if the decoder is missing."""
    home = tmp_path / "home"
    home.mkdir()
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    meta = save_upload(
        session_id="s3b",
        filename="dot.png",
        data=png,
        content_type="image/png",
        home_dir=home,
    )
    res = decode_for_turn(
        [meta],
        provider="openai",
        model="gpt-4o-mini",
        cfg={
            "home_dir": str(home),
            "vision": {"enabled": True, "force_decode": True},
        },
    )
    assert res["mode"] == "native"
    assert any("not ready" in e.lower() or "provider vision" in e.lower() for e in res["events"])


def test_decode_for_turn_unavailable_when_not_installed(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    meta = save_upload(
        session_id="s4",
        filename="dot.png",
        data=png,
        content_type="image/png",
        home_dir=home,
    )
    res = decode_for_turn(
        [meta],
        provider="deepseek",
        model="deepseek-chat",
        cfg={
            "home_dir": str(home),
            "vision": {"enabled": True, "model_id": DEFAULT_MODEL_ID},
        },
    )
    assert res["mode"] == "unavailable"
    assert res["hint"]


def test_vision_section_defaults():
    sec = vision_section_from_config({})
    assert sec["model_id"] == DEFAULT_MODEL_ID
    assert sec["port"] == 8740
    # Bundled local model: enabled by default (no download-first UX)
    assert sec["enabled"] is True


def test_legacy_qwen_model_id_migrates_to_smolvlm2():
    sec = vision_section_from_config(
        {"vision": {"enabled": True, "model_id": "qwen2.5-vl-3b"}}
    )
    assert sec["model_id"] == DEFAULT_MODEL_ID


def test_start_server_soft_migrates_retired_vision_json(tmp_path: Path, monkeypatch):
    """Retired vision.json pins must not KeyError on auto-start."""
    from remedy.vision import runtime as rt
    from remedy.vision.config import save_vision_json, vision_json_path

    home = tmp_path / "home"
    save_vision_json(
        {
            "enabled": True,
            "model_id": "qwen2.5-vl-3b",
            "model_path": str(home / "missing-old.gguf"),
            "host": "127.0.0.1",
            "port": 8740,
        },
        home_dir=home,
    )

    def fake_activate(home_dir, enabled=True):
        # Simulate bundle rebinding to product default
        save_vision_json(
            {
                "enabled": True,
                "model_id": DEFAULT_MODEL_ID,
                "model_path": str(home / "still-missing.gguf"),
                "host": "127.0.0.1",
                "port": 8740,
            },
            home_dir=home_dir,
        )
        return {"ok": True}

    monkeypatch.setattr(
        "remedy.runtime.bundle.activate_local_bundle", fake_activate
    )
    # Already-running short-circuit off
    monkeypatch.setattr(rt, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(rt, "runtime_binary_path", lambda *a, **k: home / "no-bin.exe")

    result = rt.start_server(home_dir=home, wait_s=0.1)
    # Soft-migrate must not raise; either activates or fails cleanly on missing binary
    assert isinstance(result, dict)
    assert "Unknown local model_id" not in str(result.get("error") or "")
    side = vision_json_path(home).read_text(encoding="utf-8")
    assert DEFAULT_MODEL_ID in side
    assert "qwen2.5-vl-3b" not in side


def test_save_vision_json(tmp_path: Path):
    p = save_vision_json(
        {"enabled": True, "model_id": DEFAULT_MODEL_ID},
        home_dir=tmp_path / "h",
    )
    assert p.is_file()
    assert DEFAULT_MODEL_ID in p.read_text(encoding="utf-8")


def test_catalog_pins_are_complete():
    spec = get_model_spec(DEFAULT_MODEL_ID)
    assert spec.model_file == "SmolVLM2-2.2B-Instruct-Q4_K_M.gguf"
    assert "mmproj" in spec.mmproj_file
    assert len(spec.model_sha256) == 64
    assert len(spec.mmproj_sha256) == 64
    assert LLAMA_CPP_TAG.startswith("b")
    cpu = get_runtime_spec("win-cpu-x64")
    assert cpu.zip_name.endswith(".zip")
    assert cpu.sha256 and len(cpu.sha256) == 64
    assert "win-cuda-12.4-x64" in LLAMA_RUNTIMES


def test_decode_prompt_structure():
    p = decode_user_prompt("shot.png", "what error?")
    assert "Visual decode: shot.png" in p
    assert "OCR" in p
    assert "what error?" in p


def test_anthropic_converts_image_url_parts():
    parts = [
        {"type": "text", "text": "look"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,QUJD"},
        },
    ]
    out = AnthropicProvider._convert_user_content(parts)
    assert isinstance(out, list)
    assert out[0]["type"] == "text"
    assert out[1]["type"] == "image"
    assert out[1]["source"]["type"] == "base64"
    assert out[1]["source"]["data"] == "QUJD"
    assert out[1]["source"]["media_type"] == "image/png"


def test_install_detects_missing_binary(tmp_path: Path):
    from remedy.vision.install import is_installed, model_files_present

    assert is_installed(DEFAULT_MODEL_ID, tmp_path / "h") is False
    assert model_files_present(DEFAULT_MODEL_ID, tmp_path / "h") is False


def test_decode_for_turn_force_decode_uses_mock_when_ready(tmp_path: Path):
    """When force_decode + ready, prefer decode path over native gpt-4o."""
    home = tmp_path / "remedy-home"
    home.mkdir()
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    meta = save_upload(
        session_id="s-force",
        filename="dot.png",
        data=png,
        content_type="image/png",
        home_dir=home,
    )
    cfg = {
        "home_dir": str(home),
        "vision": {
            "enabled": True,
            "force_decode": True,
            "model_id": DEFAULT_MODEL_ID,
            "base_url": "http://127.0.0.1:8740/v1",
            "timeout_s": 5,
        },
    }

    fake_status = {
        "enabled": True,
        "installed": True,
        "ready": True,
        "running": True,
        "model_id": DEFAULT_MODEL_ID,
        "base_url": "http://127.0.0.1:8740/v1",
        "model": {"name": "SmolVLM2 2.2B"},
        "not_ready_hint": None,
    }

    mock_results = [
        {
            "ok": True,
            "path": meta["path"],
            "text": "### Visual decode: dot.png\n- **Scene:** pixel\n",
        }
    ]
    with (
        patch("remedy.vision.service.get_status", return_value=fake_status),
        patch("remedy.vision.service.is_running", return_value=True),
        patch(
            "remedy.vision.service._decode_images_queued",
            return_value=mock_results,
        ),
        patch(
            "remedy.vision.service.decode_images",
            return_value=mock_results,
        ),
    ):
        res = decode_for_turn(
            [meta],
            provider="openai",
            model="gpt-4o-mini",
            cfg=cfg,
        )
    assert res["mode"] == "decode"
    assert "pixel" in res["combined"]


def test_system_health_has_warnings_structure():
    from remedy.vision.health import system_health

    h = system_health(model_id=DEFAULT_MODEL_ID, runtime_id="win-cpu-x64")
    assert "warnings" in h
    assert isinstance(h["warnings"], list)
    assert h.get("cpu_runtime") is True
    assert h.get("install_need_bytes", 0) > 0


def test_vision_health_refuses_non_loopback_and_redirect():
    """_health must not probe metadata/LAN or follow Location off-loopback."""
    import http.server
    import threading

    from remedy.vision.runtime import _health

    with patch("remedy.core.security.urlopen_no_redirect") as mock_open:
        assert _health("http://169.254.169.254/v1") is False
        assert _health("http://8.8.8.8:8080/v1") is False
        mock_open.assert_not_called()

    class _H(http.server.BaseHTTPRequestHandler):
        follow_hits = 0

        def do_GET(self) -> None:  # noqa: N802
            if self.path.endswith("/models"):
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/latest/meta-data")
                self.end_headers()
                return
            type(self).follow_hits += 1
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return

    httpd = http.server.HTTPServer(("127.0.0.1", 0), _H)
    port = httpd.server_address[1]
    thr = threading.Thread(target=httpd.serve_forever, daemon=True)
    thr.start()
    try:
        assert _health(f"http://127.0.0.1:{port}/v1", timeout=1.0) is False
        assert _H.follow_hits == 0
    finally:
        httpd.shutdown()


def test_decode_image_refuses_non_loopback_base(tmp_path: Path):
    """decode_image must not POST to off-loopback base_url."""
    from remedy.vision.decoder import decode_image

    img = tmp_path / "t.png"
    # Minimal 1x1 PNG
    img.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
            "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
        )
    )
    with patch("remedy.core.security.urlopen_no_redirect") as mock_open:
        out = decode_image(img, base_url="http://169.254.169.254/v1")
        assert out["ok"] is False
        assert "loopback" in (out.get("error") or "").lower()
        mock_open.assert_not_called()


def test_cancel_install_when_idle():
    from remedy.vision import progress as prog
    from remedy.vision.install import cancel_install

    prog.reset()
    r = cancel_install()
    assert r["ok"] is False


def test_cancel_sets_flag_during_fake_install(tmp_path: Path, monkeypatch):
    from remedy.vision import install as inst
    from remedy.vision import progress as prog

    prog.reset()
    started = threading.Event()
    cancelled_ok = threading.Event()

    def fake_run(**kwargs):
        started.set()
        # Simulate long download observing cancel
        for _ in range(200):
            try:
                inst._check_cancel()
            except inst.InstallCancelled:
                prog.cancelled()
                cancelled_ok.set()
                return
            time.sleep(0.01)
        prog.fail("never cancelled")

    monkeypatch.setattr(inst, "_run_install", fake_run)
    r = inst.start_install(
        model_id=DEFAULT_MODEL_ID,
        runtime_id="win-cpu-x64",
        home_dir=tmp_path / "h",
        enable=True,
    )
    assert r["ok"] is True
    assert started.wait(2)
    c = inst.cancel_install()
    assert c["ok"] is True
    assert cancelled_ok.wait(3)
    snap = prog.snapshot()
    assert snap["phase"] == "cancelled"


def test_download_asset_resumes_partial(tmp_path: Path, monkeypatch):
    """Partial file + Range resume completes to dest with correct size."""
    from remedy.vision import install as inst
    from remedy.vision.catalog import DownloadAsset

    payload = b"ABCDEFGHIJ" * 100  # 1000 bytes
    asset = DownloadAsset(
        name="tiny.bin",
        url="http://example.invalid/tiny.bin",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    dest = tmp_path / "tiny.bin"
    partial = dest.with_suffix(".bin.partial")
    # pre-seed first half
    partial.write_bytes(payload[:500])

    class _Resp:
        def __init__(self, data: bytes, status: int = 206):
            self._data = data
            self.status = status
            self._i = 0

        def getcode(self):
            return self.status

        def read(self, n: int = -1):
            if self._i >= len(self._data):
                return b""
            if n < 0:
                chunk = self._data[self._i :]
                self._i = len(self._data)
                return chunk
            chunk = self._data[self._i : self._i + n]
            self._i += len(chunk)
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=120):
        # Expect Range header (urllib Request uses .headers)
        rng = req.headers.get("Range") or req.headers.get("range")
        assert rng == "bytes=500-"
        return _Resp(payload[500:], status=206)

    monkeypatch.setattr(inst, "urlopen", fake_urlopen)
    inst._cancel.clear()
    inst._download_asset(asset, dest)
    assert dest.is_file()
    assert dest.read_bytes() == payload
    assert not partial.exists()


def test_wipe_vision_data(tmp_path: Path):
    from remedy.vision.config import vision_root
    from remedy.vision.install import wipe_vision_data

    root = vision_root(tmp_path / "home")
    root.mkdir(parents=True)
    (root / "vision.json").write_text("{}", encoding="utf-8")
    (root / "models").mkdir()
    (root / "models" / "x.gguf").write_bytes(b"x")
    r = wipe_vision_data(tmp_path / "home")
    assert r["ok"] is True
    assert not root.exists()


def test_reinstall_runtime_clears_runtime_dir(tmp_path: Path, monkeypatch):
    from remedy.vision import install as inst
    from remedy.vision.config import models_dir, runtime_dir

    home = tmp_path / "h"
    rdir = runtime_dir(home)
    rdir.mkdir(parents=True)
    (rdir / "llama-server.exe").write_bytes(b"old")
    mdir = models_dir(DEFAULT_MODEL_ID, home)
    mdir.mkdir(parents=True)
    (mdir / "keep.gguf").write_bytes(b"model")

    called: dict[str, object] = {}

    def fake_start(**kwargs):
        called.update(kwargs)
        return {"ok": True, "phase": "downloading"}

    monkeypatch.setattr(inst, "start_install", fake_start)
    monkeypatch.setattr(inst, "is_installing", lambda: False)
    r = inst.reinstall_runtime(prefer_cuda=True, home_dir=home, enable=True)
    assert r["ok"] is True
    assert not (rdir / "llama-server.exe").exists()
    assert (mdir / "keep.gguf").is_file()
    assert called.get("prefer_cuda") is True
    assert called.get("runtime_id") == "win-cuda-12.4-x64"


def test_decode_metrics_recorded():
    from remedy.core.metrics import default_registry
    from remedy.vision.decoder import _record_decode_metric

    before = default_registry.snapshot()
    _record_decode_metric(ok=True, seconds=1.25)
    _record_decode_metric(ok=False, seconds=0.5)
    snap = default_registry.snapshot()
    names = {c["name"] for c in snap["counters"]}
    assert "remedy_vision_decode_total" in names
    hnames = {h["name"] for h in snap["histograms"]}
    assert "remedy_vision_decode_seconds" in hnames
    # silence unused
    assert before is not None


def test_vision_tool_registered():
    from remedy.core.agent import BasicRuntime
    from remedy.models import AgentConfig

    cfg = AgentConfig(
        name="Remedy",
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        llm_api_key="sk-test",
        home_dir=str(Path.home() / ".remedy"),
    )
    agent = BasicRuntime(config=cfg, memory=None)
    names: set[str] = set()
    if hasattr(agent.tool_registry, "_tools"):
        names = set(agent.tool_registry._tools.keys())
    openai_tools = agent._openai_tools()
    for t in openai_tools:
        fn = t.get("function") or {}
        if fn.get("name"):
            names.add(str(fn["name"]))
    assert "vision_decode" in names
