"""The /api/vision/* REST surface: what it persists, forwards and refuses.

These routes are the only way the Settings UI turns the local visual decoder on
and off. Two things go wrong here in practice. First, the config write: install
and activate flip ``vision.enabled`` in config.toml before delegating, and if
that write throws (read-only file, half-written TOML) the user must still get an
answer instead of a 500 — but equally, a write that goes to the wrong keys
leaves the decoder switched off after a successful install, or resurrects a
retired model id that the service then has to un-pin at every status poll.

Second, ``/api/vision/test``. It is the "does my webcam/screenshot pipeline
work" button, so it must refuse early — before it starts a llama-server — when
nothing is installed, and it must not hand a caller-supplied path to the decoder
without checking it. What it does with a *bad* path is surprising enough to be
pinned down here (see the fallback tests below).

Nothing in this file touches the real ~/.remedy: the service layer, the decoder,
the config writer and ``Path.home()`` are all replaced with recorders.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from remedy.interfaces.routes.vision import register_vision_routes

SERVICE_FUNCS = (
    "get_status",
    "activate_bundle",
    "start_install",
    "cancel_install",
    "reinstall_runtime",
    "uninstall",
    "ensure_server",
    "stop",
)


class FakeService:
    """Stands in for remedy.vision.service + decoder; records every call."""

    def __init__(self) -> None:
        self.calls: dict[str, list[tuple[tuple, dict]]] = {}
        self.returns: dict[str, dict] = {
            "get_status": {"installed": True, "base_url": "http://127.0.0.1:8081"},
            "activate_bundle": {"ok": True, "mode": "local_files"},
            "start_install": {"ok": True, "state": "downloading"},
            "cancel_install": {"ok": True, "cancelled": True},
            "reinstall_runtime": {"ok": True, "runtime": "cpu"},
            "uninstall": {"ok": True, "removed": 3},
            "ensure_server": {"ok": True, "base_url": "http://127.0.0.1:8081"},
            "stop": {"ok": True, "stopped": True},
            "decode_image": {"ok": True, "text": "a red square"},
        }

    def _make(self, name: str):
        def fn(*args, **kwargs):
            self.calls.setdefault(name, []).append((args, copy.deepcopy(kwargs)))
            return copy.deepcopy(self.returns[name])

        return fn

    def called(self, name: str) -> bool:
        return name in self.calls

    def one(self, name: str) -> tuple[tuple, dict]:
        """The single recorded call to `name` (asserts it happened exactly once)."""
        assert self.calls.get(name), f"{name} was never called"
        assert len(self.calls[name]) == 1, f"{name} called {len(self.calls[name])}x"
        return self.calls[name][0]


@pytest.fixture()
def cfg() -> dict:
    return {"home_dir": "", "vision": {"enabled": False, "n_gpu_layers": 7}}


@pytest.fixture()
def svc(monkeypatch, cfg) -> FakeService:
    fake = FakeService()
    for name in SERVICE_FUNCS:
        monkeypatch.setattr(f"remedy.vision.service.{name}", fake._make(name))
    monkeypatch.setattr("remedy.vision.decoder.decode_image", fake._make("decode_image"))
    # Routes call load_config() per request; the real one hands back a fresh copy.
    monkeypatch.setattr(
        "remedy.interfaces.routes.vision.load_config", lambda: copy.deepcopy(cfg)
    )
    return fake


@pytest.fixture()
def written(monkeypatch, tmp_path) -> list[tuple[Path, dict]]:
    """Every config.toml write the routes attempt, without touching disk."""
    log: list[tuple[Path, dict]] = []
    monkeypatch.setattr(
        "remedy.interfaces.api_support._find_config_path",
        lambda: tmp_path / "config.toml",
    )
    monkeypatch.setattr(
        "remedy.interfaces.api_support._write_config",
        lambda path, conf: log.append((path, copy.deepcopy(conf))),
    )
    return log


@pytest.fixture()
def client(svc, written) -> TestClient:
    app = FastAPI()
    register_vision_routes(app)
    return TestClient(app)


def vision_written(written) -> dict:
    """The vision section of the last recorded config write."""
    assert written, "no config write was recorded"
    return written[-1][1]["vision"]


# --- status -------------------------------------------------------------------


def test_the_status_poll_asks_for_the_cheap_answer_by_default(client, svc):
    """The UI polls this every 1.5s; a full status probes GPU and costs seconds."""
    assert client.get("/api/vision/status").status_code == 200
    _, kwargs = svc.one("get_status")
    assert kwargs["light"] is True


@pytest.mark.parametrize("value", ["1", "true", "on"])
def test_asking_for_a_full_status_turns_the_light_flag_off(client, svc, value):
    client.get(f"/api/vision/status?full={value}")
    assert svc.one("get_status")[1]["light"] is False


@pytest.mark.parametrize("value", ["0", "false", "off"])
def test_an_explicitly_false_full_flag_still_gets_the_light_answer(client, svc, value):
    client.get(f"/api/vision/status?full={value}")
    assert svc.one("get_status")[1]["light"] is True


def test_a_full_flag_that_is_not_a_boolean_is_rejected_not_guessed(client, svc):
    assert client.get("/api/vision/status?full=maybe").status_code == 422
    assert not svc.called("get_status")


def test_the_status_body_is_whatever_the_service_reported(client, svc):
    svc.returns["get_status"] = {"installed": False, "state": "missing"}
    assert client.get("/api/vision/status").json() == {"installed": False, "state": "missing"}


def test_status_hands_the_service_the_loaded_config(client, svc, cfg):
    client.get("/api/vision/status")
    args, _ = svc.one("get_status")
    assert args[0] == cfg


# --- catalog ------------------------------------------------------------------


def test_the_catalog_route_returns_the_catalog_verbatim(client, monkeypatch):
    monkeypatch.setattr("remedy.vision.catalog.catalog_public", lambda: {"models": ["x"]})
    assert client.get("/api/vision/catalog").json() == {"models": ["x"]}


def test_the_real_catalog_advertises_the_single_pinned_model(client):
    from remedy.vision.catalog import DEFAULT_MODEL_ID

    body = client.get("/api/vision/catalog").json()
    assert body["default_model_id"] == DEFAULT_MODEL_ID


# --- activate -----------------------------------------------------------------


def test_activating_switches_vision_on_in_the_saved_config(client, written):
    from remedy.vision.catalog import DEFAULT_MODEL_ID

    assert client.post("/api/vision/activate").status_code == 200
    section = vision_written(written)
    assert section["enabled"] is True
    assert section["model_id"] == DEFAULT_MODEL_ID


def test_activating_keeps_the_rest_of_the_vision_settings(client, written):
    """A tuned n_gpu_layers must survive being switched on."""
    client.post("/api/vision/activate")
    assert vision_written(written)["n_gpu_layers"] == 7


def test_a_corrupt_vision_section_is_replaced_rather_than_crashing(client, cfg, written):
    cfg["vision"] = "not-a-table"
    assert client.post("/api/vision/activate").status_code == 200
    assert vision_written(written)["enabled"] is True


def test_activating_with_no_config_file_on_disk_writes_nothing(client, monkeypatch, svc, written):
    monkeypatch.setattr("remedy.interfaces.api_support._find_config_path", lambda: None)
    assert client.post("/api/vision/activate").status_code == 200
    assert written == []
    assert svc.called("activate_bundle")  # activation itself must still happen


def test_a_failed_config_write_does_not_sink_the_activation(client, monkeypatch, svc):
    def boom(path, conf):
        raise OSError("read-only")

    monkeypatch.setattr("remedy.interfaces.api_support._write_config", boom)
    r = client.post("/api/vision/activate")
    assert r.status_code == 200
    assert r.json() == svc.returns["activate_bundle"]


def test_activation_is_always_requested_as_enabled(client, svc):
    client.post("/api/vision/activate")
    _, kwargs = svc.one("activate_bundle")
    assert kwargs["enabled"] is True
    assert kwargs["cfg"]["vision"]["enabled"] is True


# --- install ------------------------------------------------------------------


def test_install_works_with_no_request_body_at_all(client, svc):
    assert client.post("/api/vision/install").status_code == 200
    assert svc.called("start_install")


def test_install_pins_the_product_model_and_ignores_the_requested_one(client, svc, written):
    """model_id is accepted by the schema but deliberately never honoured."""
    client.post("/api/vision/install", json={"model_id": "some-other-vlm"})
    assert svc.one("start_install")[1]["model_id"] == "smolvlm2-2.2b"
    assert vision_written(written)["model_id"] == "smolvlm2-2.2b"


def test_the_hardcoded_install_model_id_is_still_the_catalog_default():
    """If the catalog default ever moves, the literal in the route must move too."""
    from remedy.vision.catalog import DEFAULT_MODEL_ID

    assert DEFAULT_MODEL_ID == "smolvlm2-2.2b"


def test_installing_switches_vision_on(client, written):
    client.post("/api/vision/install")
    assert vision_written(written)["enabled"] is True


def test_an_explicit_runtime_id_is_saved_and_forwarded(client, svc, written):
    client.post("/api/vision/install", json={"runtime_id": "win-cpu-x64"})
    assert vision_written(written)["runtime_id"] == "win-cpu-x64"
    assert svc.one("start_install")[1]["runtime_id"] == "win-cpu-x64"


def test_asking_for_cuda_without_a_runtime_id_picks_the_gpu_flavour(client, written):
    from remedy.runtime.catalog import default_runtime_id

    client.post("/api/vision/install", json={"prefer_cuda": True})
    assert vision_written(written)["runtime_id"] == default_runtime_id(prefer_gpu=True)


def test_an_explicit_runtime_id_beats_prefer_cuda(client, written):
    client.post(
        "/api/vision/install", json={"runtime_id": "win-cpu-x64", "prefer_cuda": True}
    )
    assert vision_written(written)["runtime_id"] == "win-cpu-x64"


def test_a_plain_install_does_not_invent_a_runtime_id(client, svc, written):
    client.post("/api/vision/install", json={})
    assert "runtime_id" not in vision_written(written)
    assert svc.one("start_install")[1]["runtime_id"] is None


def test_prefer_cuda_reaches_the_installer_even_when_the_config_is_unwritable(
    client, monkeypatch, svc
):
    monkeypatch.setattr("remedy.interfaces.api_support._find_config_path", lambda: None)
    client.post("/api/vision/install", json={"prefer_cuda": True})
    assert svc.one("start_install")[1]["prefer_cuda"] is True


def test_a_non_boolean_prefer_cuda_is_rejected(client, svc):
    assert client.post("/api/vision/install", json={"prefer_cuda": "banana"}).status_code == 422
    assert not svc.called("start_install")


def test_unknown_fields_in_the_install_body_are_ignored(client, svc):
    assert client.post("/api/vision/install", json={"nonsense": 1}).status_code == 200
    assert svc.called("start_install")


def test_cancelling_an_install_takes_no_arguments(client, svc):
    r = client.post("/api/vision/install/cancel")
    assert r.json() == {"ok": True, "cancelled": True}
    assert svc.one("cancel_install") == ((), {})


# --- reinstall runtime --------------------------------------------------------


def test_reinstalling_the_runtime_defaults_to_cuda(client, svc):
    """Opposite default to install: this button exists to get off the CPU build."""
    client.post("/api/vision/reinstall-runtime")
    assert svc.one("reinstall_runtime")[1]["prefer_cuda"] is True


@pytest.mark.parametrize("prefer", [True, False])
def test_the_requested_runtime_flavour_is_forwarded(client, svc, prefer):
    client.post("/api/vision/reinstall-runtime", json={"prefer_cuda": prefer})
    assert svc.one("reinstall_runtime")[1]["prefer_cuda"] is prefer


def test_reinstalling_the_runtime_does_not_rewrite_the_config_itself(client, written):
    """That persistence belongs to the service; a second writer would race it."""
    client.post("/api/vision/reinstall-runtime")
    assert written == []


# --- uninstall ----------------------------------------------------------------


@pytest.mark.parametrize("keep", [True, False])
def test_keeping_the_model_weights_is_forwarded(client, svc, keep):
    client.post("/api/vision/uninstall", json={"keep_models": keep})
    assert svc.one("uninstall")[1]["keep_models"] is keep


def test_uninstalling_defaults_to_removing_the_weights(client, svc):
    client.post("/api/vision/uninstall")
    assert svc.one("uninstall")[1]["keep_models"] is False


def test_uninstalling_switches_vision_off_in_the_config(client, written):
    client.post("/api/vision/uninstall")
    assert vision_written(written)["enabled"] is False


def test_the_uninstall_result_is_returned_untouched(client, svc, monkeypatch):
    """Even when the follow-up config write blows up, the caller sees the result."""

    def boom(path, conf):
        raise OSError("read-only")

    monkeypatch.setattr("remedy.interfaces.api_support._write_config", boom)
    assert client.post("/api/vision/uninstall").json() == {"ok": True, "removed": 3}


# --- start / stop -------------------------------------------------------------


def test_starting_the_server_passes_the_loaded_config(client, svc, cfg):
    assert client.post("/api/vision/start").json() == {
        "ok": True,
        "base_url": "http://127.0.0.1:8081",
    }
    assert svc.one("ensure_server")[0][0] == cfg


def test_stopping_the_server_passes_the_loaded_config(client, svc, cfg):
    assert client.post("/api/vision/stop").json() == {"ok": True, "stopped": True}
    assert svc.one("stop")[0][0] == cfg


# --- test / decode ------------------------------------------------------------


@pytest.fixture()
def home(tmp_path, monkeypatch) -> Path:
    """Keep every fallback path inside tmp — never the owner's real ~/.remedy."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setattr("remedy.interfaces.config.load_config", lambda: {"home_dir": str(h)})
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "never-real-home")
    return h


def test_the_test_route_requires_a_body(client, svc):
    assert client.post("/api/vision/test").status_code == 422
    assert not svc.called("get_status")


def test_testing_an_uninstalled_decoder_refuses_before_starting_a_server(client, svc, home):
    svc.returns["get_status"] = {"installed": False}
    r = client.post("/api/vision/test", json={})
    assert r.json() == {"ok": False, "error": "Visual decoder not installed"}
    assert not svc.called("ensure_server")


def test_a_server_that_will_not_start_is_reported_as_is(client, svc, home):
    svc.returns["ensure_server"] = {"ok": False, "error": "llama-server missing"}
    r = client.post("/api/vision/test", json={})
    assert r.json() == {"ok": False, "error": "llama-server missing"}
    assert not svc.called("decode_image")


def test_the_status_for_the_test_route_is_the_full_one(client, svc, home):
    """Unlike the poll, this one pays for the full status (GPU + catalog probe)."""
    client.post("/api/vision/test", json={})
    assert "light" not in svc.one("get_status")[1]


def test_an_existing_image_is_decoded_with_the_question(client, svc, home, tmp_path):
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    r = client.post("/api/vision/test", json={"path": str(img), "question": "what colour?"})
    assert r.json() == {"ok": True, "text": "a red square"}
    args, kwargs = svc.one("decode_image")
    assert args[0] == img
    assert kwargs["extra_question"] == "what colour?"
    assert kwargs["base_url"] == "http://127.0.0.1:8081"


def test_no_question_reaches_the_decoder_as_no_question(client, svc, home, tmp_path):
    img = tmp_path / "shot.png"
    img.write_bytes(b"x")
    client.post("/api/vision/test", json={"path": str(img)})
    assert svc.one("decode_image")[1]["extra_question"] is None


def test_omitting_the_path_generates_a_self_test_png_under_home(client, svc, home):
    client.post("/api/vision/test", json={})
    used = svc.one("decode_image")[0][0]
    assert used == home / "tmp_e2e_vision.png"
    assert used.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_the_generated_self_test_image_is_a_valid_eight_by_eight_png(client, svc, home):
    import struct
    import zlib

    client.post("/api/vision/test", json={})
    data = svc.one("decode_image")[0][0].read_bytes()
    # IHDR: 8-byte signature, 4-byte length, 4-byte tag, then width/height.
    assert data[12:16] == b"IHDR"
    assert struct.unpack(">II", data[16:24]) == (8, 8)
    length = struct.unpack(">I", data[8:12])[0]
    assert zlib.crc32(data[12 : 16 + length]) & 0xFFFFFFFF == struct.unpack(
        ">I", data[16 + length : 20 + length]
    )[0]


def test_an_existing_self_test_image_is_reused_not_rewritten(client, svc, home):
    stale = home / "tmp_e2e_vision.png"
    stale.write_bytes(b"\x89PNG\r\n\x1a\nstale")
    client.post("/api/vision/test", json={})
    assert stale.read_bytes() == b"\x89PNG\r\n\x1a\nstale"


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_a_path_that_is_not_a_file_is_reported_not_quietly_substituted(
    client, svc, tmp_path, kind
):
    """A typo'd path used to fall through to the 8x8 self-test image, so the
    caller got ok:true and a description of a red square for a screenshot that
    was never read."""
    path = {"missing": str(tmp_path / "nope.png"), "directory": str(tmp_path)}[kind]
    body = client.post("/api/vision/test", json={"path": path}).json()
    assert body["ok"] is False
    assert "not found" in body["error"].lower()
    assert not svc.called("decode_image"), "it decoded something anyway"


def test_asking_for_no_path_at_all_still_runs_the_self_test(client, svc, home):
    """Sending nothing is a request for the self-test, and still works."""
    r = client.post("/api/vision/test", json={})
    assert r.json() == {"ok": True, "text": "a red square"}
    assert svc.one("decode_image")[0][0] == home / "tmp_e2e_vision.png"


def test_an_uncreatable_self_test_image_is_reported_not_raised(
    client, svc, tmp_path, monkeypatch
):
    blocked = tmp_path / "home-is-a-file"
    blocked.write_text("not a directory")
    monkeypatch.setattr(
        "remedy.interfaces.config.load_config", lambda: {"home_dir": str(blocked)}
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "never-real-home")
    r = client.post("/api/vision/test", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "path required" in body["error"]
    assert not svc.called("decode_image")


def test_a_broken_home_config_does_not_escape_to_the_real_home(
    client, svc, tmp_path, monkeypatch
):
    """When config lookup throws, the route falls back to default_home().

    Remedy's home is default_home() — never Path.home()/.remedy — so patching
    Path.home must NOT move the self-test image.
    """

    def boom():
        raise RuntimeError("config unreadable")

    fallback = tmp_path / "fallback-home"
    fallback.mkdir()
    monkeypatch.setattr("remedy.interfaces.config.load_config", boom)
    monkeypatch.setattr("remedy.interfaces.routes.vision.default_home", lambda: fallback)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "never-real-home")
    client.post("/api/vision/test", json={})
    used = svc.one("decode_image")[0][0]
    assert used == fallback / "tmp_e2e_vision.png"
    assert "never-real-home" not in str(used)


def test_a_status_without_a_base_url_borrows_the_one_from_startup(client, svc, home):
    svc.returns["get_status"] = {"installed": True}
    svc.returns["ensure_server"] = {"ok": True, "base_url": "http://127.0.0.1:9999"}
    client.post("/api/vision/test", json={})
    assert svc.one("decode_image")[1]["base_url"] == "http://127.0.0.1:9999"


def test_no_base_url_anywhere_is_an_error_rather_than_a_decode_attempt(client, svc, home):
    svc.returns["get_status"] = {"installed": True}
    svc.returns["ensure_server"] = {"ok": True}
    r = client.post("/api/vision/test", json={})
    assert r.json() == {"ok": False, "error": "No base_url"}
    assert not svc.called("decode_image")


def test_a_decode_failure_is_passed_through_unwrapped(client, svc, home, tmp_path):
    img = tmp_path / "shot.png"
    img.write_bytes(b"x")
    svc.returns["decode_image"] = {"ok": False, "error": "vision base_url must be loopback"}
    r = client.post("/api/vision/test", json={"path": str(img)})
    assert r.json() == {"ok": False, "error": "vision base_url must be loopback"}
