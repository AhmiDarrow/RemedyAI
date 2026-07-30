"""Nano swarm + shared local runtime catalog tests."""

from __future__ import annotations

from remedy.nanoswarm import get_swarm
from remedy.nanoswarm.events import SwarmEvent
from remedy.nanoswarm.token_nanobot import (
    TokenNanobot,
    UsageCalibrator,
    estimate_messages_tokens,
    estimate_text_tokens,
)
from remedy.runtime.catalog import (
    BUNDLED_RUNTIME_IDS,
    DEFAULT_LOCAL_MODEL_ID,
    LOCAL_ROLES,
    catalog_public,
    get_model_spec,
)


def test_single_local_model_id_for_all_roles():
    pub = catalog_public()
    assert pub["default_local_model_id"] == DEFAULT_LOCAL_MODEL_ID
    assert pub["default_model_id"] == DEFAULT_LOCAL_MODEL_ID
    assert set(pub["roles"]) == set(LOCAL_ROLES)
    assert "vision" in LOCAL_ROLES and "nano" in LOCAL_ROLES and "helper" in LOCAL_ROLES
    models = pub["models"]
    assert len(models) == 1
    assert models[0]["id"] == DEFAULT_LOCAL_MODEL_ID
    assert models[0]["roles"] == list(LOCAL_ROLES)
    # Option B: CPU + CUDA bundled
    assert set(BUNDLED_RUNTIME_IDS) == {"win-cpu-x64", "win-cuda-12.4-x64"}
    assert pub["bundle_policy"] == "cpu_and_cuda"


def test_get_model_spec_rejects_unknown():
    try:
        get_model_spec("some-other-qwen")
        raise AssertionError("expected KeyError")
    except KeyError as e:
        assert "single local VLM" in str(e) or "Unknown" in str(e)


def test_token_nanobot_class_weighted():
    prose = "hello world " * 20
    code = "x=1;y=2;{" * 40
    # code punct should not estimate to zero
    assert estimate_text_tokens(prose) > 10
    assert estimate_text_tokens(code) > 10
    msgs = [
        {"role": "user", "content": prose},
        {"role": "assistant", "content": code},
    ]
    assert estimate_messages_tokens(msgs) > estimate_text_tokens(prose)


def test_usage_calibrator_adjusts():
    cal = UsageCalibrator(min_samples=3, max_samples=20)
    bot = TokenNanobot(calibrator=cal)
    for _ in range(5):
        cal.observe(100, 150, provider="openai", model="gpt-test")
    adj, method = cal.adjust(100, provider="openai", model="gpt-test")
    assert method == "calibrated"
    assert 120 <= adj <= 180
    n = bot.measure_messages(
        [{"role": "user", "content": "a" * 400}],
        provider="openai",
        model="gpt-test",
    )
    assert n >= 1


def test_swarm_dispatch_message_and_tool():
    swarm = get_swarm()
    r1 = swarm.dispatch(
        SwarmEvent.message_added("user", "please run git status and remember this"),
        messages=[{"role": "user", "content": "please run git status"}],
    )
    assert "signals" in r1
    assert r1["signals"].get("router", {}).get("label") in (
        "tool",
        "skill",
        "chat",
        "memory",
        "plan",
    )
    r2 = swarm.dispatch(SwarmEvent.tool_step("bash", success=True, duration_ms=10))
    assert r2["signals"]["pattern"]["step_count"] >= 1
    st = swarm.status()
    assert st["local_model_id"] == DEFAULT_LOCAL_MODEL_ID
    assert st["bots"]["token"]["bot"] == "token"


def test_nanoswarm_clear_session_purges_pattern_and_goal():
    """Session delete/reset must drop residual pattern windows + open goals."""
    from remedy.core.session_reset import purge_session_disk_artifacts

    swarm = get_swarm()
    sid = "gauntlet-clear-sess-xyz"
    swarm.pattern.on_tool_step("bash_exec", success=True, session_id=sid)
    swarm.goal.sync_from_brief(None, session_id=sid)
    # Manually plant an open goal
    s = swarm.goal._sess(sid)
    s["open"] = ["ship release"]
    assert swarm.pattern.for_session(sid).steps
    assert swarm.goal.snapshot(sid)["open"]

    stats = purge_session_disk_artifacts(sid, home=None)
    assert stats.get("nanoswarm_cleared") is True
    # After clear, for_session recreates empty buffer; steps list is empty
    assert swarm.pattern.for_session(sid).steps == []
    assert swarm.goal.snapshot(sid)["open"] == []


def test_compressor_uses_token_nanobot():
    from remedy.memory.harness.compressor import estimate_tokens, should_nudge_compress

    msgs = [{"role": "user", "content": "x" * 1000}]
    est = estimate_tokens(msgs)
    assert est >= 1
    assert should_nudge_compress(est, context_window=10, min_pct=0.1, max_pct=0.5) in (
        "soft",
        "strong",
    )


def test_vision_catalog_reexports_same_id():
    from remedy.vision.catalog import DEFAULT_MODEL_ID
    from remedy.vision.catalog import LOCAL_ROLES as VR

    assert DEFAULT_MODEL_ID == DEFAULT_LOCAL_MODEL_ID
    assert "nano" in VR


def test_bundle_available_diagnostic():
    from remedy.runtime.bundle import bundle_available

    d = bundle_available()
    assert "model_present" in d
    assert "searched_roots" in d


def test_activate_local_bundle_uses_legacy_vision_home(tmp_path, monkeypatch):
    """When GGUF files exist under a fake vision home, activate writes vision.json."""
    from pathlib import Path

    from remedy.runtime.catalog import get_model_spec

    mid = DEFAULT_LOCAL_MODEL_ID
    spec = get_model_spec(mid)
    # Create tiny placeholder files (activate checks is_file, not size)
    model_dir = tmp_path / "models" / mid
    model_dir.mkdir(parents=True)
    (model_dir / spec.model_file).write_bytes(b"fake-gguf")
    (model_dir / spec.mmproj_file).write_bytes(b"fake-mmproj")
    rt = tmp_path / "runtime"
    rt.mkdir()
    (rt / "llama-server.exe").write_bytes(b"fake")

    monkeypatch.setenv("REMEDY_LOCAL_BUNDLE", str(tmp_path))
    home = tmp_path / "remedy-home"
    home.mkdir()

    from remedy.runtime.bundle import activate_local_bundle

    result = activate_local_bundle(home, enabled=True, nvidia_detected=False)
    assert result.get("ok") is True
    state = result.get("state") or {}
    assert state.get("model_id") == mid
    assert Path(state["model_path"]).is_file()
    assert Path(state["runtime_binary"]).is_file()


def test_helper_bot_offline_surface():
    from remedy.nanoswarm.helper_nanobot import HelperNanobot

    h = HelperNanobot()
    st = h.status()
    assert st["role_model"] == DEFAULT_LOCAL_MODEL_ID
    # Offline FAQ/error drafts are on; neural assist still reserved
    assert st["enabled"] is True
    assert st.get("neural_enabled") is False
    out = h.draft_help("approvals")
    assert out.get("ok") is True


def test_maybe_autostart_skips_when_not_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "h"))
    from remedy.vision.service import maybe_autostart_local_model

    r = maybe_autostart_local_model(
        {"home_dir": str(tmp_path / "h"), "vision": {"enabled": True, "auto_start": True}}
    )
    assert r.get("skipped") is True
    assert r.get("reason") in ("not_installed", "disabled", "auto_start_off")


def test_idle_stop_disabled():
    from remedy.vision.runtime import maybe_idle_stop

    r = maybe_idle_stop(idle_stop_s=0)
    assert r.get("stopped") is False
    assert r.get("reason") == "disabled"


def test_job_queue_handlers_register():
    from remedy.runtime.jobs import default_queue
    from remedy.runtime.local_infer import ensure_handlers_registered

    ensure_handlers_registered()
    st = default_queue().status()
    assert "vision_decode" in st["handlers"]
    assert "nano_classify" in st["handlers"]


def test_local_text_complete_refuses_non_loopback():
    """Poisoned base_url must not open metadata/LAN (no network)."""
    from unittest.mock import patch

    from remedy.runtime.local_infer import local_text_complete

    with patch("remedy.core.security.urlopen_no_redirect") as mock_open:
        out = local_text_complete("hi", base_url="http://169.254.169.254/v1")
        assert out["ok"] is False
        assert "loopback" in (out.get("error") or "").lower()
        mock_open.assert_not_called()
        out2 = local_text_complete("hi", base_url="http://10.0.0.5:8080/v1")
        assert out2["ok"] is False
        mock_open.assert_not_called()


def test_local_text_complete_truncates_huge_prompt():
    """Runaway ranker/router must not POST multi-MB prompts to llama-server."""
    import json
    from unittest.mock import MagicMock, patch

    from remedy.runtime.local_infer import _MAX_PROMPT_CHARS, local_text_complete

    captured: dict = {}

    def _fake_urlopen(req, timeout=None):  # noqa: ANN001
        body = json.loads(req.data.decode("utf-8"))
        captured["messages"] = body["messages"]
        captured["max_tokens"] = body["max_tokens"]
        resp = MagicMock()
        resp.read.return_value = json.dumps(
            {"choices": [{"message": {"content": "ok"}}]}
        ).encode("utf-8")
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *a: None
        return resp

    huge = "x" * (_MAX_PROMPT_CHARS + 5000)
    with patch("remedy.core.security.urlopen_no_redirect", side_effect=_fake_urlopen):
        out = local_text_complete(
            huge,
            base_url="http://127.0.0.1:8080/v1",
            max_tokens=9999,
            system="s" * 5000,
        )
    assert out["ok"] is True
    user = captured["messages"][-1]["content"]
    assert len(user) <= _MAX_PROMPT_CHARS + 20
    assert "truncated" in user
    sys_msg = captured["messages"][0]["content"]
    assert len(sys_msg) <= 2100
    assert captured["max_tokens"] <= 512


def test_skill_nanobot_passes_session_id_and_skips_double_record(tmp_path):
    """Learning loop multi-session promote needs session_id; skill_run must not double-count."""
    from pathlib import Path

    from remedy.core.learning_loop import LearningLoop
    from remedy.models import Skill, SkillKind, SkillManifest, SkillStatus
    from remedy.nanoswarm.events import SwarmEvent
    from remedy.nanoswarm.skill_nanobot import SkillNanobot

    home = Path(tmp_path)
    skills = home / "skills"
    skills.mkdir()
    loop = LearningLoop(skills_dir=skills, stats_path=home / "skill_stats.json")
    skill = Skill(
        manifest=SkillManifest(
            name="sess-skill",
            description="Test skill description long enough for validation",
            version="0.1.0",
            kind=SkillKind.NATIVE,
            status=SkillStatus.VALIDATED,
            metadata={"auto_generated": True, "effort_weight": 0.2},
        ),
        instructions="# steps\n" + ("do the thing\n" * 12),
    )
    bot = SkillNanobot()
    # First record with session (as skill_run does)
    loop.record_skill_feedback("sess-skill", success=True, session_id="sess-a")
    # Nanobot secondary path must not double-count
    out = bot.on_skill_result(
        "sess-skill",
        success=True,
        learning_loop=loop,
        skill=skill,
        session_id="sess-a",
        record_feedback=False,
        auto_refine=False,
    )
    assert out.get("feedback_skipped") == "already_recorded"
    stats = loop.get_skill_stats("sess-skill")
    assert stats.total_executions == 1
    assert "sess-a" in stats.execution_by_session

    # Direct nanobot path still records with session_id
    out2 = bot.on_skill_result(
        "sess-skill",
        success=True,
        learning_loop=loop,
        skill=skill,
        session_id="sess-b",
    )
    assert out2.get("feedback_recorded") is True
    assert out2.get("session_id") == "sess-b"
    stats2 = loop.get_skill_stats("sess-skill")
    assert stats2.total_executions == 2
    assert set(stats2.execution_by_session) >= {"sess-a", "sess-b"}

    # Coordinator passes session_id from event payload
    swarm = get_swarm()
    r = swarm.dispatch(
        SwarmEvent.skill_result(
            "sess-skill",
            success=True,
            session_id="sess-c",
            duration_ms=5.0,
        ),
        learning_loop=loop,
        skill=skill,
    )
    assert r["signals"]["skill"].get("session_id") == "sess-c"
    assert "sess-c" in loop.get_skill_stats("sess-skill").execution_by_session


def test_local_text_complete_does_not_follow_redirect():
    """Loopback 302 → off-host must fail closed (no SSRF follow)."""
    import http.server
    import threading

    from remedy.runtime.local_infer import local_text_complete

    class _H(http.server.BaseHTTPRequestHandler):
        hits_meta = 0

        def do_POST(self) -> None:  # noqa: N802
            if self.path.endswith("/chat/completions"):
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/latest/meta-data")
                self.end_headers()
                return
            type(self).hits_meta += 1
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return

    httpd = http.server.HTTPServer(("127.0.0.1", 0), _H)
    port = httpd.server_address[1]
    thr = threading.Thread(target=httpd.serve_forever, daemon=True)
    thr.start()
    try:
        out = local_text_complete(
            "label",
            base_url=f"http://127.0.0.1:{port}/v1",
            timeout_s=2.0,
        )
        # Fail closed: HTTPError 302, URLError, or OS-level abort on 3xx no-follow.
        assert out["ok"] is False
        assert out.get("text") == ""
        assert _H.hits_meta == 0
    finally:
        httpd.shutdown()


def test_router_heuristic_still_works():
    from remedy.nanoswarm.router_nanobot import RouterNanobot

    r = RouterNanobot()
    out = r.classify("please remember my name is Ada", use_local=False)
    assert out["label"] == "memory"
    assert out["method"] == "heuristic"


def test_router_intent_cache_identical_messages():
    """Same user text classified twice in a turn hits cache (no re-regex)."""
    from remedy.nanoswarm.router_nanobot import RouterNanobot

    r = RouterNanobot()
    msg = "search the codebase for begin_turn_metabolism"
    a = r.classify_intent(msg)
    hits0 = r.cache_hits
    b = r.classify_intent(msg)
    assert a["label"] == b["label"] == "tool"
    assert r.cache_hits == hits0 + 1
    # Mutating return value must not poison cache
    b["label"] = "chat"
    c = r.classify_intent(msg)
    assert c["label"] == "tool"
    # Status exposes cache metrics
    st = r.status()
    assert st["cache_hits"] >= 1
    assert st["cache_size"] >= 1


def test_router_classifies_browse_and_file_as_tool():
    """Browse/file phrases must be tool intent (L2 agency path), not chat."""
    from remedy.nanoswarm.router_nanobot import RouterNanobot

    r = RouterNanobot()
    for msg in (
        "open gmail and check inbox",
        "navigate to https://example.com",
        "search the codebase for begin_turn",
        "file_read src/remedy/core/agent.py",
        "take a screenshot of the desktop",
    ):
        out = r.classify_intent(msg)
        assert out["label"] == "tool", f"{msg!r} → {out['label']!r}"


def test_swarm_dispatch_router_is_fast_heuristic():
    """Agent hot path must not block on local llama classify."""
    from remedy.nanoswarm import get_swarm
    from remedy.nanoswarm.events import SwarmEvent

    swarm = get_swarm()
    r = swarm.dispatch(
        SwarmEvent.message_added("user", "run git status please"),
        messages=[{"role": "user", "content": "run git status please"}],
    )
    router = r["signals"].get("router") or {}
    assert router.get("method") == "heuristic"
    assert router.get("label") in ("tool", "chat", "skill")


def test_start_install_message_is_download_when_missing(tmp_path, monkeypatch):
    """Without files, install path is first-run download (not prebundle)."""
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "h"))
    from remedy.vision import service as vs

    def fake_start_install(**kwargs):
        return {"ok": True, "phase": "downloading"}

    monkeypatch.setattr(vs, "_start_install", fake_start_install)
    monkeypatch.setattr(vs, "is_installed", lambda *a, **k: False)
    monkeypatch.setattr(
        vs,
        "activate_bundle",
        lambda **k: {"ok": False, "error": "missing"},
    )
    monkeypatch.setattr(
        vs,
        "system_health",
        lambda **k: {"nvidia_detected": False, "warnings": []},
    )
    r = vs.start_install(cfg={"home_dir": str(tmp_path / "h"), "vision": {"enabled": True}})
    assert r.get("mode") == "download"
    assert "Download" in (r.get("message") or "") or "download" in (r.get("message") or "").lower()
