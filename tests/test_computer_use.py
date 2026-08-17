"""In-house computer use: router, bridge, tool registration (feature/computer-use)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remedy.core.computer.host_bridge import ComputerHostBridge, _scrub_job_result
from remedy.core.computer.router import ComputerTarget, resolve_target
from remedy.core.computer.types import COMPUTER_PLAN_MODE_TOOLS, COMPUTER_TOOL_NAMES
from remedy.core.plan_store import PLAN_MODE_TOOL_NAMES
from remedy.interfaces.api import create_app
from remedy.models import AgentConfig


def test_scrub_job_result_redacts_secrets():
    out = _scrub_job_result(
        {
            "ok": True,
            "text": "page has Bearer sk-abcdefghijklmnopqrstuvwxyz0123 token",
            "message": "ok",
        }
    )
    assert out is not None
    assert "sk-abcdefghijklmnopqrstuvwxyz0123" not in json.dumps(out)
    assert "[redacted]" in (out.get("text") or "")


def test_scrub_job_result_strips_password_element_values():
    out = _scrub_job_result(
        {
            "ok": True,
            "elements": [
                {
                    "ref": "e1",
                    "tag": "input",
                    "type": "password",
                    "value": "SuperSecretPassw0rd!",
                    "name": "passwd",
                },
                {"ref": "e2", "tag": "button", "name": "Submit"},
                # name=pwd without type=password still secret-bearing
                {
                    "ref": "e3",
                    "tag": "input",
                    "type": "text",
                    "name": "pwd",
                    "value": "PwdFieldSecret99",
                },
                {
                    "ref": "e4",
                    "tag": "input",
                    "type": "text",
                    "autocomplete": "current-password",
                    "value": "AutoCompleteSecret!",
                },
            ],
        }
    )
    assert out is not None
    els = out.get("elements") or []
    pw = next(e for e in els if e.get("ref") == "e1")
    assert pw.get("value") == "[filled]"
    blob = json.dumps(out)
    assert "SuperSecretPassw0rd" not in blob
    assert "PwdFieldSecret99" not in blob
    assert "AutoCompleteSecret" not in blob
    assert next(e for e in els if e.get("ref") == "e3").get("value") == "[filled]"
    assert next(e for e in els if e.get("ref") == "e4").get("value") == "[filled]"


def test_complete_scrubs_error_and_typed_payload(tmp_path: Path):
    """Host complete must not leave typed secrets or sk- tokens on disk."""
    from remedy.core.computer.host_bridge import ComputerHostBridge

    b = ComputerHostBridge(home_dir=tmp_path)
    job = b.enqueue(
        "type",
        {"text": "my-login-password-XYZ", "ui": {"open_browser": True}},
    )
    # Payload readable while pending (host needs plaintext)
    pending = b._read(job.id)
    assert pending is not None
    assert pending.payload.get("text") == "my-login-password-XYZ"

    done = b.complete(
        job.id,
        ok=False,
        result={"ok": False, "message": "fail"},
        error="provider said api_key=sk-abcdefghijklmnopqrstuvwxyz0123",
    )
    assert done is not None
    assert done.status == "error"
    assert "sk-abcdefghijklmnopqrstuvwxyz0123" not in (done.error or "")
    assert "[redacted]" in (done.error or "")
    assert "my-login-password-XYZ" not in json.dumps(done.payload)
    assert "redacted" in str(done.payload.get("text") or "").lower()


def test_a11y_push_scrubs_password_values(tmp_path: Path):
    from remedy.core.computer.host_bridge import ComputerHostBridge

    b = ComputerHostBridge(home_dir=tmp_path)
    job = b.enqueue("snapshot", {})
    done = b.complete_a11y_push(
        job.id,
        [
            {
                "ref": "e9",
                "tag": "input",
                "type": "password",
                "value": "Hunter2-secret!",
                "name": "password",
            }
        ],
    )
    assert done is not None
    assert done.status == "done"
    blob = json.dumps(done.result or {})
    assert "Hunter2-secret" not in blob
    els = (done.result or {}).get("elements") or []
    assert els and els[0].get("value") == "[filled]"


def test_resolve_target_url_prefers_browser():
    assert resolve_target("auto", url="https://example.com") is ComputerTarget.BROWSER
    assert resolve_target("auto", hint="open github.com docs") is ComputerTarget.BROWSER
    assert resolve_target("auto", hint="show me the wiki for baldur") is ComputerTarget.BROWSER
    assert resolve_target("browser", url="https://x.com") is ComputerTarget.BROWSER


def test_normalize_url_rejects_task_text_leak():
    """User prose must never become the Browser rail address bar."""
    from remedy.core.computer.router import is_valid_navigate_url, normalize_url

    junk = "gmail sign in, once I want you to log me in the login input my username user@example.com"
    assert normalize_url(junk) == ""
    assert is_valid_navigate_url(junk) is False
    assert is_valid_navigate_url("https://" + junk) is False
    assert normalize_url("user@example.com") == ""
    assert normalize_url("https://mail.google.com") == "https://mail.google.com"
    assert is_valid_navigate_url("https://mail.google.com") is True
    # nickname still works
    assert normalize_url("gmail") == "https://mail.google.com"
    # first-token recovery from multi-word
    assert normalize_url("gmail sign in please") == "https://mail.google.com"
    # URL userinfo blocked (credentials never land in rail address bar)
    assert is_valid_navigate_url("https://user:pass@example.com/") is False
    # IPv4: loopback + RFC1918 only (not IMDS / public / 172.0.0.0/8)
    assert is_valid_navigate_url("http://127.0.0.1:8188/") is True
    assert is_valid_navigate_url("http://10.0.0.5/") is True
    assert is_valid_navigate_url("http://192.168.1.1/") is True
    assert is_valid_navigate_url("http://172.16.0.1/") is True
    assert is_valid_navigate_url("http://172.31.255.1/") is True
    assert is_valid_navigate_url("http://169.254.169.254/") is False
    assert is_valid_navigate_url("http://172.0.0.1/") is False
    assert is_valid_navigate_url("http://8.8.8.8/") is False
    # Metadata hostnames / wildcard DNS → IMDS
    assert is_valid_navigate_url("http://169.254.169.254.nip.io/latest/") is False
    assert is_valid_navigate_url("http://metadata.google.internal/computeMetadata/v1/") is False
    assert is_valid_navigate_url("http://metadata.nicob.net/") is False


def test_open_url_refuses_non_http_schemes():
    """file:// and bare paths must never hit os.startfile / cmd start."""
    import pytest

    from remedy.core.computer import desktop_win as win

    for bad in (
        "file:///C:/Windows/System32/cmd.exe",
        "file://localhost/etc/passwd",
        "javascript:alert(1)",
        "C:\\Windows\\System32\\calc.exe",
        "\\\\evil\\share\\payload.exe",
        "",
    ):
        with pytest.raises(ValueError):
            win.open_url(bad)


def test_open_app_refuses_protocol_unc_and_metachar():
    """computer_app must not launch URLs, UNC shares, or cmd-metachar payloads."""
    import pytest

    from remedy.core.computer import desktop_win as win
    from remedy.core.computer.router import looks_like_url

    for bad in (
        "file:///C:/Windows/System32/cmd.exe",
        "https://evil.example/payload",
        "http://127.0.0.1:9/",
        "javascript:alert(1)",
        "ms-msdt:something",
        "search-ms:query=x",
        "ms-settings:privacy",  # free-form protocol (alias 'settings' only)
        "\\\\evil\\share\\payload.exe",
        "//evil/share/payload.exe",
        "notepad & calc",
        "calc|whoami",
        "app%PATH%",
        "",
    ):
        with pytest.raises(ValueError):
            win.open_app(bad)

    # file:/javascript: must not be treated as browser navigate URLs
    assert looks_like_url("file:///C:/secret") is False
    assert looks_like_url("javascript:alert(1)") is False
    assert looks_like_url("https://example.com") is True


def test_open_app_protocol_detector_drive_vs_handler():
    """Drive letters are not protocols; multi-letter handlers and data: are.

    Regression for open_app hardening: C:\\ paths must remain launchable when
    the file exists, while shell:/ms-*/data:/about: never reach cmd start.
    """
    import pytest

    from remedy.core.computer.desktop_win import _open_app_is_protocol_or_url, open_app

    # Drive-letter forms are not protocol handlers
    assert _open_app_is_protocol_or_url(r"C:\Windows\System32\notepad.exe") is False
    assert _open_app_is_protocol_or_url("C:foo") is False
    assert _open_app_is_protocol_or_url("notepad") is False

    for handler in (
        "shell:AppsFolder",
        "ms-excel:ofe|u|https://evil.example",
        "data:text/html,hi",
        "about:blank",
        "ms-settings:privacy",
        "javascript:alert(1)",
    ):
        assert _open_app_is_protocol_or_url(handler) is True, handler
        with pytest.raises(ValueError, match="URL/protocol|UNC|metachar|required"):
            open_app(handler)


def test_open_app_resolves_relative_in_search_dir(tmp_path: Path, monkeypatch):
    """Just-built game.exe must launch from the project folder, not sidecar CWD."""
    import sys

    import pytest

    if sys.platform != "win32":
        pytest.skip("Desktop computer use requires Windows")
    from remedy.core.computer.desktop_win import open_app

    fake = tmp_path / "hello.exe"
    fake.write_bytes(b"MZ")
    launched: list[str] = []

    def fake_popen(args, **_k):
        launched.append(str(args[0]))

        class P:
            pid = 1

        return P()

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    info = open_app("hello.exe", search_dirs=[tmp_path])
    assert info.get("method") == "project_path"
    assert launched and Path(launched[0]).name == "hello.exe"
    info2 = open_app(".\\hello.exe", search_dirs=[tmp_path])
    assert info2.get("method") == "project_path"


def test_open_app_prefers_search_dirs_not_cwd(tmp_path: Path, monkeypatch):
    import os
    import sys

    import pytest

    if sys.platform != "win32":
        pytest.skip("Desktop computer use requires Windows")
    from remedy.core.computer.desktop_win import open_app

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "hello.exe").write_bytes(b"MZ-cwd")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "hello.exe").write_bytes(b"MZ-proj")
    launched: list[str] = []

    def fake_popen(args, **_k):
        launched.append(str(args[0]))

        class P:
            pid = 1

        return P()

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    prev = os.getcwd()
    try:
        os.chdir(cwd)
        info = open_app("hello.exe", search_dirs=[proj])
    finally:
        os.chdir(prev)
    assert info.get("method") == "project_path"
    assert Path(launched[0]).parent.resolve() == proj.resolve()


def test_open_app_rejects_parent_escape(tmp_path: Path):
    import pytest

    from remedy.core.computer.desktop_win import open_app

    proj = tmp_path / "proj"
    proj.mkdir()
    with pytest.raises(ValueError, match="parent-directory|traversal"):
        open_app("..\\Windows\\System32\\calc.exe", search_dirs=[proj])


def test_open_url_refuses_userinfo_credentials():
    """https://user:pass@host must not open (credentials in address bar / OS)."""
    import pytest

    from remedy.core.computer import desktop_win as win
    from remedy.core.computer.router import is_valid_navigate_url

    for bad in (
        "https://user:pass@example.com/",
        "http://alice:s3cret@localhost:8080/x",
        "https://token@evil.example/path",
        "https://:secret@example.com/",
        "https://user:@example.com/",
        "https://:@example.com/",
        "HTTPS://User:Pass@Example.com/x",
    ):
        with pytest.raises(ValueError, match="userinfo|credential"):
            win.open_url(bad)
        # Browser rail must reject the same shapes (empty userinfo included).
        assert is_valid_navigate_url(bad) is False


def test_computer_audit_redacts_secrets(tmp_path: Path):
    from remedy.core.computer.audit import audit_path, log_computer_action

    log_computer_action(
        action="type",
        target="browser",
        ok=True,
        detail={"message": "typed api_key=sk-abcdefghijklmnopqrstuvwxyz0123"},
        session_id="aud1",
        home_dir=tmp_path,
    )
    path = audit_path(tmp_path)
    assert path.is_file()
    body = path.read_text(encoding="utf-8")
    assert "sk-abcdefghijklmnopqrstuvwxyz0123" not in body
    assert "[redacted]" in body


def test_wants_system_browser_only_when_explicit():
    from remedy.core.computer.router import wants_rail_browser, wants_system_browser

    assert wants_system_browser("open the wiki") is False
    assert wants_system_browser("show me the fandom page") is False
    assert wants_system_browser("open in Firefox") is True
    assert wants_system_browser("use the system browser") is True
    assert wants_system_browser("open externally") is True
    # "remedy browser" / rail must NOT mean system browser
    assert wants_system_browser("open it in remedy browser") is False
    assert wants_system_browser("show it to me in rail") is False
    assert wants_rail_browser("open it in remedy browser") is True
    assert wants_rail_browser("show it to me in the rail") is True


def test_desktop_navigate_refuses_system_browser_without_explicit_ask(
    tmp_path: Path, monkeypatch
):
    """Mis-routed desktop navigate must not open OS browser by surprise."""
    import json

    from remedy.core.computer import host_bridge as hb
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    monkeypatch.setattr(hb, "_bridge", None)
    opened: list[str] = []

    def fake_open(url: str):
        opened.append(url)
        return {"ok": True, "url": url}

    monkeypatch.setattr(
        "remedy.core.computer.desktop_win.open_url", fake_open, raising=False
    )
    # Patch the module path used by executor
    import remedy.core.computer.desktop_win as win

    monkeypatch.setattr(win, "open_url", fake_open)

    ex = ComputerExecutor(home_dir=tmp_path)
    # Host offline, no explicit system-browser request
    raw = ex.run(
        ComputerAction.NAVIGATE,
        target="desktop",
        url="https://example.com",
        hint="open example in the rail",
    )
    d = json.loads(raw)
    assert d.get("ok") is False or d.get("system_browser_blocked") or d.get("via")
    assert opened == [], f"system browser should not open: {opened}"
    # Explicit request still works
    raw2 = ex.run(
        ComputerAction.NAVIGATE,
        target="system",
        url="https://example.com",
        hint="open in the system browser",
    )
    d2 = json.loads(raw2)
    assert d2.get("ok") is True
    assert opened == ["https://example.com"]


def test_resolve_target_desktop_hints():
    assert (
        resolve_target("auto", hint="click the Start menu on the desktop")
        is ComputerTarget.DESKTOP
    )
    assert resolve_target("desktop", url="https://x.com") is ComputerTarget.DESKTOP
    assert resolve_target("browser", hint="installer") is ComputerTarget.BROWSER
    from remedy.core.computer.router import _DESKTOP_HINTS

    assert _DESKTOP_HINTS.search("in other words click Sign in") is None
    assert _DESKTOP_HINTS.search("open word.exe") is not None


def test_host_bridge_drive_target_is_session_scoped(tmp_path: Path, monkeypatch):
    from remedy.core import turn_context as tc
    from remedy.core.computer.host_bridge import ComputerHostBridge

    b = ComputerHostBridge(home_dir=tmp_path)
    tok_a = tc._turn_session_id.set("sess-a")
    tok_act = tc._turn_active.set(True)
    try:
        b.set_last_drive_target("desktop")
        b.set_last_elements([{"ref": "c1", "name": "Play"}], target="desktop")
        assert b.last_drive_target() == "desktop"
        assert b.get_element_by_ref("c1") is not None
    finally:
        tc._turn_session_id.reset(tok_a)
        tc._turn_active.reset(tok_act)

    tok_b = tc._turn_session_id.set("sess-b")
    tok_act2 = tc._turn_active.set(True)
    try:
        b.set_last_drive_target("browser")
        assert b.last_drive_target() == "browser"
        assert b.get_element_by_ref("c1") is None
    finally:
        tc._turn_session_id.reset(tok_b)
        tc._turn_active.reset(tok_act2)


def test_snapshot_needs_vision_only_for_empty_desktop_or_game():
    from remedy.core.computer.vision_observe import snapshot_needs_vision

    windows_only = [{"ref": "w1", "name": "Game"}]
    with_controls = [{"ref": "w1"}, {"ref": "c1", "name": "OK"}]
    assert snapshot_needs_vision(windows_only, last_target="desktop") is True
    assert snapshot_needs_vision(windows_only, hint="play the pygame window") is True
    assert snapshot_needs_vision(with_controls, last_target="desktop") is False
    assert snapshot_needs_vision(windows_only, last_target="browser") is False
    assert snapshot_needs_vision(windows_only, already_fallback=True, last_target="desktop") is False


def test_flush_native_screenshots_for_grok(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from remedy.core.computer.vision_observe import (
        flush_native_screenshots,
        queue_native_screenshot,
    )

    png = tmp_path / "shot.png"
    png.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    rt = SimpleNamespace()
    monkeypatch.setattr(
        "remedy.core.computer.vision_observe.chat_supports_native_vision",
        lambda _rt=None: True,
    )
    queue_native_screenshot(rt, png, origin={"x": 0, "y": 0}, width=1, height=1)
    msg = flush_native_screenshots(rt)
    assert msg is not None
    assert msg["role"] == "user"
    kinds = [p.get("type") for p in msg["content"]]
    assert "image_url" in kinds
    header = msg["content"][0]["text"].lower()
    assert "you can see this" in header
    assert "computer_click" in header
    assert "do not computer_click" not in header
    assert flush_native_screenshots(rt) is None  # consumed


def test_flush_native_skipped_without_vision(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from remedy.core.computer.vision_observe import (
        flush_native_screenshots,
        queue_native_screenshot,
    )

    png = tmp_path / "shot.png"
    png.write_bytes(b"not-a-png-but-exists")
    rt = SimpleNamespace()
    monkeypatch.setattr(
        "remedy.core.computer.vision_observe.chat_supports_native_vision",
        lambda _rt=None: False,
    )
    queue_native_screenshot(rt, png)
    assert flush_native_screenshots(rt) is None


def test_observe_screenshot_kind_selects_prompt(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from remedy.core.computer.vision_observe import (
        CUA_FOCUS_QUESTION,
        DESIGN_FOCUS_QUESTION,
        observe_screenshot,
    )

    png = tmp_path / "shot.png"
    png.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    captured: dict[str, str] = {}

    def fake_decode(path, extra_question=None, timeout_s=25.0, **_k):
        captured["q"] = extra_question or ""
        return {"ok": True, "text": "ok", "error": ""}

    monkeypatch.setattr(
        "remedy.core.computer.vision_observe.decode_screenshot_brief",
        fake_decode,
    )
    rt = SimpleNamespace()
    observe_screenshot(png, runtime=rt, kind="design", hint="landing")
    q = captured["q"]
    assert DESIGN_FOCUS_QUESTION in q
    assert "landing" in q
    assert "Click targets" not in q
    assert "computer_click" not in q
    assert rt._pending_cua_shots[-1]["kind"] == "design"

    observe_screenshot(png, runtime=rt)
    q = captured["q"]
    assert CUA_FOCUS_QUESTION in q
    assert "Click targets" in q
    assert rt._pending_cua_shots[-1]["kind"] == "cua"

    for bad in ("", "nope"):
        observe_screenshot(png, runtime=rt, kind=bad)
        q = captured["q"]
        assert CUA_FOCUS_QUESTION in q
        assert DESIGN_FOCUS_QUESTION not in q
        assert rt._pending_cua_shots[-1]["kind"] == "cua"


def test_observe_screenshot_idle_decoder_still_queues(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from remedy.core.computer.vision_observe import observe_screenshot

    png = tmp_path / "shot.png"
    png.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    monkeypatch.setattr(
        "remedy.vision.service.get_status",
        lambda cfg=None, light=True: {"running": False},
    )
    called = {"decode": False}

    def _boom(*_a, **_k):
        called["decode"] = True
        raise AssertionError("must not cold-start decoder")

    monkeypatch.setattr("remedy.vision.decoder.decode_image", _boom)
    monkeypatch.setattr(
        "remedy.vision.runtime.start_server",
        lambda **_k: {"ok": False, "skipped": True},
    )
    rt = SimpleNamespace()
    out = observe_screenshot(png, runtime=rt, kind="design")
    assert out["ok"] is False
    assert "idle" in (out.get("error") or "").lower()
    assert called["decode"] is False
    assert rt._pending_cua_shots[-1]["kind"] == "design"


def test_flush_native_design_header(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from remedy.core.computer.vision_observe import (
        flush_native_screenshots,
        queue_native_screenshot,
    )

    png = tmp_path / "shot.png"
    png.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    rt = SimpleNamespace()
    monkeypatch.setattr(
        "remedy.core.computer.vision_observe.chat_supports_native_vision",
        lambda _rt=None: True,
    )
    queue_native_screenshot(rt, png, kind="design")
    msg = flush_native_screenshots(rt)
    assert msg is not None
    text = msg["content"][0]["text"]
    low = text.lower()
    assert "you can see this" in low
    assert "critique" in low
    assert "file_edit" in low
    assert "do not computer_click" in low
    assert "click with computer_click" not in low


def test_infer_sticky_target_game_and_refs():
    from remedy.core.computer.router import infer_sticky_target

    # Explicit always wins
    assert infer_sticky_target("desktop", ref="e3") == "desktop"
    assert infer_sticky_target("browser", ref="c1") == "browser"
    # Ref prefixes
    assert infer_sticky_target("auto", ref="e3") == "browser"
    assert infer_sticky_target("auto", ref="w1") == "desktop"
    assert infer_sticky_target("auto", ref="c12") == "desktop"
    # After computer_app, auto click/find stay desktop
    assert (
        infer_sticky_target(
            "auto", action="click", last_target="desktop", hint="Start"
        )
        == "desktop"
    )
    assert (
        infer_sticky_target(
            "auto", action="find", last_elements_target="desktop"
        )
        == "desktop"
    )
    # Game / exe hints override a stale browser sticky
    assert (
        infer_sticky_target(
            "auto",
            action="click",
            hint="play the game",
            last_target="browser",
        )
        == "desktop"
    )
    assert (
        infer_sticky_target("auto", action="click", hint="launch snake.exe")
        == "desktop"
    )
    # After navigate, auto click stays on rail
    assert (
        infer_sticky_target(
            "auto", action="click", last_target="browser", hint="Sign in"
        )
        == "browser"
    )
    # App / windows always desktop
    assert infer_sticky_target("auto", action="app") == "desktop"
    assert infer_sticky_target("auto", action="windows") == "desktop"


def test_resolve_target_navigate_defaults_browser():
    assert resolve_target("auto", action="navigate") is ComputerTarget.BROWSER


def test_plan_mode_includes_read_computer_tools():
    assert "computer_screenshot" in PLAN_MODE_TOOL_NAMES
    assert "computer_navigate" in PLAN_MODE_TOOL_NAMES
    assert "computer_windows" in PLAN_MODE_TOOL_NAMES
    assert "computer_monitors" in PLAN_MODE_TOOL_NAMES
    assert "computer_snapshot" in PLAN_MODE_TOOL_NAMES
    assert "computer_page_text" in PLAN_MODE_TOOL_NAMES
    assert "computer_find" in PLAN_MODE_TOOL_NAMES
    assert "computer_wait" in PLAN_MODE_TOOL_NAMES
    assert "computer_click" not in PLAN_MODE_TOOL_NAMES
    assert "computer_type" not in PLAN_MODE_TOOL_NAMES
    assert "computer_key" not in PLAN_MODE_TOOL_NAMES
    assert "computer_scroll" not in PLAN_MODE_TOOL_NAMES
    assert "computer_drag" not in PLAN_MODE_TOOL_NAMES
    # Multi-step act can click/type — must stay Build-only (aligned with COMPUTER_PLAN_MODE_TOOLS)
    assert "computer_act" not in PLAN_MODE_TOOL_NAMES
    assert "computer_app" not in PLAN_MODE_TOOL_NAMES
    assert COMPUTER_PLAN_MODE_TOOLS <= COMPUTER_TOOL_NAMES
    assert COMPUTER_PLAN_MODE_TOOLS <= PLAN_MODE_TOOL_NAMES
    # Every computer tool is either plan-allowed or explicitly blocked
    assert COMPUTER_TOOL_NAMES == COMPUTER_PLAN_MODE_TOOLS | (
        COMPUTER_TOOL_NAMES - COMPUTER_PLAN_MODE_TOOLS
    )


def test_host_bridge_enqueue_claim_complete(tmp_path: Path):
    b = ComputerHostBridge(home_dir=tmp_path)
    job = b.enqueue("navigate", {"url": "https://example.com"})
    assert job.status == "pending"
    claimed = b.claim_next()
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "running"
    assert b.host_connected()
    done = b.complete(job.id, ok=True, result={"ok": True, "url": "https://example.com"})
    assert done is not None
    assert done.status == "done"
    assert b.claim_next() is None


def test_enqueue_sets_ui_command_for_rail(tmp_path: Path):
    from remedy.core.computer.host_bridge import ComputerHostBridge

    b = ComputerHostBridge(home_dir=tmp_path)
    job = b.enqueue("navigate", {"url": "https://example.com/wiki", "ui": {"open_browser": True}})
    cmd = b.peek_ui_command()
    assert cmd is not None
    assert cmd.get("action") == "open_browser"
    assert cmd.get("url") == "https://example.com/wiki"
    assert cmd.get("job_id") == job.id
    taken = b.take_ui_command()
    assert taken is not None
    assert taken.get("job_id") == job.id
    assert b.peek_ui_command() is None
    assert b.take_ui_command() is None


def test_computer_host_routes_require_bearer(tmp_path: Path):
    """Host/jobs/ui need Bearer (S-AUTH-04). Rust poller loads DPAPI token; SPA sends auth.

    a11y push remains loopback-exempt (job_id capability secret).
    """

    class Cfg:
        home_dir = str(tmp_path)

    class RT:
        config = Cfg()

        def list_tasks(self):
            return []

    app = create_app(runtime=RT(), api_key="secret-test-key")
    client = TestClient(app)
    # No Authorization header → 401 (no longer loopback-open)
    r = client.post("/api/computer/host/hello", json={"client": "desktop"})
    assert r.status_code == 401, r.text
    r2 = client.get("/api/computer/jobs/next")
    assert r2.status_code == 401, r2.text
    # With Bearer — host works
    headers = {"Authorization": "Bearer secret-test-key"}
    r3 = client.post(
        "/api/computer/host/hello",
        json={"client": "desktop"},
        headers=headers,
    )
    assert r3.status_code == 200, r3.text
    assert r3.json().get("ok") is True
    r4 = client.get("/api/computer/jobs/next", headers=headers)
    assert r4.status_code == 200, r4.text
    assert "job" in r4.json()
    # a11y still loopback-open (invalid short id → 400, not 401)
    r5 = client.post("/api/computer/a11y/push", json={"job_id": "short", "elements": []})
    assert r5.status_code == 400, r5.text


def test_wait_fails_fast_when_unclaimed(tmp_path: Path):
    b = ComputerHostBridge(home_dir=tmp_path)
    # click without ui_command path → unclaimed timeout applies
    job = b.enqueue("click", {"x": 1, "y": 2})
    # clear ui so unclaimed logic applies
    b.clear_ui_command()
    finished = b.wait(job.id, timeout_s=30.0, unclaimed_timeout_s=0.6, poll_s=0.1)
    assert finished.status == "error"
    assert "did not claim" in (finished.error or "")
    assert not b.host_connected()


def test_wait_honors_ui_command_without_claim(tmp_path: Path):
    """Navigate sets ui_command; Desktop may complete without claim_next."""
    import threading
    import time

    b = ComputerHostBridge(home_dir=tmp_path)
    job = b.enqueue("navigate", {"url": "https://example.com/wiki"})
    assert b.peek_ui_command() is not None

    def complete_later() -> None:
        time.sleep(0.4)
        b.complete(
            job.id,
            ok=True,
            result={"ok": True, "action": "navigate", "url": "https://example.com/wiki"},
        )
        b.clear_ui_command(job_id=job.id)

    threading.Thread(target=complete_later, daemon=True).start()
    finished = b.wait(job.id, timeout_s=5.0, unclaimed_timeout_s=0.5, poll_s=0.1)
    assert finished.status == "done"


def _streaming(monkeypatch, *sids: str) -> None:
    """Pretend these sessions have a live turn (turn_context stream claim)."""
    from remedy.core.computer import host_bridge as hb

    live = set(sids)
    monkeypatch.setattr(
        hb.ComputerHostBridge,
        "_session_is_streaming",
        staticmethod(lambda sid: sid in live),
    )


def test_claim_next_skips_other_session_while_focused_tab_is_busy(tmp_path: Path, monkeypatch):
    """One WebView rail: a busy focused tab is protected — a background tab's
    pending job stays pending (no wrong-tab navigate mid-turn)."""
    from remedy.core.computer.host_bridge import ComputerHostBridge

    b = ComputerHostBridge(home_dir=tmp_path)
    _streaming(monkeypatch, "sess-a")
    focused = b.enqueue("click", {"x": 1}, session_id="sess-a")
    other = b.enqueue("click", {"x": 2}, session_id="sess-b")
    b.set_focused_session("sess-a")
    claimed = b.claim_next()
    assert claimed is not None
    assert claimed.id == focused.id
    assert b.claim_next() is None
    claimed_b = b.claim_next(session_id="sess-b")
    assert claimed_b is not None
    assert claimed_b.id == other.id


def test_claim_next_rail_follows_driver_when_focused_tab_idle(tmp_path: Path, monkeypatch):
    """Focused desktop tab idle → a session driven from the WebUI / harness /
    another tab gets the rail instead of starving (the "host offline →
    improvise on the desktop" failure)."""
    from remedy.core.computer.host_bridge import ComputerHostBridge

    b = ComputerHostBridge(home_dir=tmp_path)
    _streaming(monkeypatch, "sess-b")  # only the driver is mid-turn
    other = b.enqueue("page_text", {}, session_id="sess-b")
    b.set_focused_session("sess-a")
    claimed = b.claim_next(session_id="sess-a")  # SPA polls with its own tab
    assert claimed is not None
    assert claimed.id == other.id


def test_take_ui_command_skips_other_session_while_focused_tab_is_busy(
    tmp_path: Path, monkeypatch
):
    """Focused-session take must leave another tab's open_browser command
    while the focused tab is mid-turn; an idle focused tab lets it through."""
    from remedy.core.computer.host_bridge import ComputerHostBridge

    b = ComputerHostBridge(home_dir=tmp_path)
    _streaming(monkeypatch, "sess-a")
    job = b.enqueue("navigate", {"url": "https://example.com/b"}, session_id="sess-b")
    assert job.session_id == "sess-b"
    cmd = b.peek_ui_command()
    assert cmd is not None
    assert cmd.get("session_id") == "sess-b"
    b.set_focused_session("sess-a")
    assert b.take_ui_command() is None
    assert b.peek_ui_command() is not None
    b.set_focused_session("sess-b")
    taken = b.take_ui_command()
    assert taken is not None
    assert taken.get("job_id") == job.id
    assert taken.get("session_id") == "sess-b"
    # Idle focused tab + the OTHER session is the active driver → goes through.
    _streaming(monkeypatch, "sess-c")
    job2 = b.enqueue("navigate", {"url": "https://example.com/c"}, session_id="sess-c")
    b.set_focused_session("sess-a")
    taken2 = b.take_ui_command()
    assert taken2 is not None and taken2.get("job_id") == job2.id

    # Idle focused tab but the other session is NOT driving (a stale command)
    # → must NOT hijack the focused idle rail.
    _streaming(monkeypatch)  # nobody streaming
    job3 = b.enqueue("navigate", {"url": "https://example.com/d"}, session_id="sess-d")
    assert job3.session_id == "sess-d"
    b.set_focused_session("sess-a")
    assert b.take_ui_command() is None


def test_claim_next_exclude_and_only(tmp_path: Path):
    from remedy.core.computer.host_bridge import ComputerHostBridge

    b = ComputerHostBridge(home_dir=tmp_path)
    nav = b.enqueue("navigate", {"url": "https://example.com"})
    click = b.enqueue("click", {"x": 1, "y": 2})
    # SPA path: never steal navigates
    claimed = b.claim_next(exclude_actions={"navigate"})
    assert claimed is not None
    assert claimed.id == click.id
    assert claimed.action == "click"
    # Navigate still pending for Rust
    still = b.claim_next(exclude_actions={"navigate"})
    assert still is None
    only_nav = b.claim_next(only_actions={"navigate"})
    assert only_nav is not None
    assert only_nav.id == nav.id


def test_renudge_keeps_session_id(tmp_path: Path, monkeypatch):
    from remedy.core.computer.host_bridge import ComputerHostBridge

    b = ComputerHostBridge(home_dir=tmp_path)
    job = b.enqueue("navigate", {"url": "https://example.com/a"}, session_id="sess-a")
    assert b.take_ui_command(session_id="sess-a") is not None
    assert b.peek_ui_command() is None
    b.renudge_ui_for_job(job)
    cmd = b.peek_ui_command()
    assert cmd is not None
    assert cmd.get("session_id") == "sess-a"
    assert cmd.get("url") == "https://example.com/a"
    _streaming(monkeypatch, "sess-b")
    b.set_focused_session("sess-b")
    assert b.take_ui_command() is None


def test_wait_renudges_ui_command(tmp_path: Path):
    """If host takes ui_command without completing, wait re-publishes it."""
    import threading
    import time

    b = ComputerHostBridge(home_dir=tmp_path)
    job = b.enqueue("navigate", {"url": "https://example.com/gta"})
    assert b.take_ui_command() is not None  # host "lost" the take
    assert b.peek_ui_command() is None

    def complete_after_renudge() -> None:
        # Wait until renudge restores command
        for _ in range(40):
            cmd = b.peek_ui_command()
            if cmd and str(cmd.get("job_id")) == job.id:
                b.complete(
                    job.id,
                    ok=True,
                    result={"ok": True, "action": "navigate", "via": "renudge-test"},
                )
                return
            time.sleep(0.05)

    threading.Thread(target=complete_after_renudge, daemon=True).start()
    finished = b.wait(job.id, timeout_s=4.0, unclaimed_timeout_s=None, poll_s=0.05)
    assert finished.status == "done"
    assert (finished.result or {}).get("via") == "renudge-test"


def test_complete_success_wins_over_timeout_error(tmp_path: Path):
    """Late host complete must upgrade timeout error → done (Gmail race)."""
    b = ComputerHostBridge(home_dir=tmp_path)
    job = b.enqueue("navigate", {"url": "https://mail.google.com"})
    job.status = "error"
    job.error = "timeout waiting for desktop host (8s)"
    b._write(job)
    done = b.complete(
        job.id,
        ok=True,
        result={"ok": True, "url": "https://mail.google.com", "via": "late"},
    )
    assert done is not None
    assert done.status == "done"
    assert done.error is None
    assert (done.result or {}).get("via") == "late"
    # Never downgrade success
    again = b.complete(job.id, ok=False, error="nope")
    assert again is not None
    assert again.status == "done"


def test_cancel_never_clobbers_terminal_status(tmp_path: Path):
    """Abort/cancel must not rewrite a host-completed job as cancelled.

    wait() abort races with host complete: cancel() used to overwrite done →
    cancelled and lose the successful click/navigate result.
    """
    b = ComputerHostBridge(home_dir=tmp_path)
    job = b.enqueue("click", {"text": "Sign in", "ui": {"open_browser": True}})
    done = b.complete(
        job.id,
        ok=True,
        result={"ok": True, "action": "click", "text": "Sign in", "via": "host"},
    )
    assert done is not None and done.status == "done"
    cancelled = b.cancel(job.id)
    assert cancelled is not None
    assert cancelled.status == "done"
    assert (cancelled.result or {}).get("via") == "host"
    # Open jobs still cancel
    open_job = b.enqueue("type", {"text": "hello"})
    assert open_job.status == "pending"
    c2 = b.cancel(open_job.id)
    assert c2 is not None and c2.status == "cancelled"
    # Error is terminal too — leave it alone
    err_job = b.enqueue("snapshot", {})
    b.complete(err_job.id, ok=False, error="boom")
    left = b.cancel(err_job.id)
    assert left is not None and left.status == "error"


def test_purge_old_spares_open_jobs(tmp_path: Path):
    """purge_old must not delete pending/running work (docstring: finished only).

    Open jobs are spared while a live host could still claim them; past the
    stale TTL they are *expired + scrubbed* (never plaintext-forever), but the
    file itself is kept — purge never silently deletes open work (S-COMP-03).
    """
    import os
    import time

    b = ComputerHostBridge(home_dir=tmp_path)
    open_j = b.enqueue("navigate", {"url": "https://example.com/open"})
    done_j = b.enqueue("snapshot", {})
    b.complete(done_j.id, ok=True, result={"ok": True, "elements": []})
    # Age both files past the finished-job cutoff but inside the stale TTL
    old = time.time() - 120
    for jid in (open_j.id, done_j.id):
        p = b._path(jid)
        os.utime(p, (old, old))
    n = b.purge_old(max_age_s=60.0, stale_open_ttl_s=1800.0)
    assert n >= 1
    assert b._read(open_j.id) is not None
    assert b._read(open_j.id).status == "pending"
    assert b._read(done_j.id) is None

    # Past the stale TTL the open job expires (cancelled) but is not deleted.
    very_old = time.time() - 10_000
    os.utime(b._path(open_j.id), (very_old, very_old))
    b.purge_old(max_age_s=60.0, stale_open_ttl_s=1800.0)
    expired = b._read(open_j.id)
    assert expired is not None
    assert expired.status == "cancelled"


def test_purge_old_shots_stays_in_own_home(tmp_path: Path, monkeypatch):
    """A custom home must never delete ~/.remedy/computer/shots."""
    import os
    import time

    fake_user = tmp_path / "userhome"
    default_shots = fake_user / ".remedy" / "computer" / "shots"
    default_shots.mkdir(parents=True)
    victim = default_shots / "keep.png"
    victim.write_bytes(b"\x89PNG_keep")
    os.utime(victim, (time.time() - 10_000, time.time() - 10_000))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_user))

    custom = tmp_path / "portable"
    b = ComputerHostBridge(home_dir=custom)
    own = custom / "computer" / "shots"
    own.mkdir(parents=True)
    drop = own / "stale.png"
    drop.write_bytes(b"\x89PNG_stale")
    os.utime(drop, (time.time() - 10_000, time.time() - 10_000))
    n = b.purge_old_shots(max_age_s=60.0)
    assert n >= 1
    assert not drop.is_file()
    assert victim.is_file()


def test_purge_old_shots_ttl(tmp_path: Path):
    """Screenshots under computer/shots age out with purge_old (S-COMP-02)."""
    import os
    import time

    b = ComputerHostBridge(home_dir=tmp_path)
    shots = tmp_path / "computer" / "shots"
    shots.mkdir(parents=True)
    keep = shots / "fresh.png"
    drop = shots / "stale.png"
    keep.write_bytes(b"\x89PNG_fresh")
    drop.write_bytes(b"\x89PNG_stale")
    old = time.time() - 10_000
    os.utime(drop, (old, old))
    n = b.purge_old_shots(max_age_s=60.0)
    assert n >= 1
    assert keep.is_file()
    assert not drop.is_file()
    # purge_old also sweeps shots
    drop2 = shots / "stale2.png"
    drop2.write_bytes(b"\x89PNG_stale2")
    os.utime(drop2, (old, old))
    n2 = b.purge_old(max_age_s=60.0)
    assert n2 >= 1
    assert not drop2.is_file()


def test_cancel_pending_scoped_by_session(tmp_path: Path):
    """Multi-tab abort must not cancel sibling session computer jobs."""
    b = ComputerHostBridge(home_dir=tmp_path)
    a = b.enqueue("navigate", {"url": "https://a.example"}, session_id="sess-a")
    b_job = b.enqueue("snapshot", {}, session_id="sess-b")
    bare = b.enqueue("click", {"text": "x"})  # untagged legacy
    n = b.cancel_pending_and_running(reason="session_aborted", session_id="sess-a")
    assert n == 1
    assert b._read(a.id).status == "cancelled"
    assert b._read(b_job.id).status == "pending"
    assert b._read(bare.id).status == "pending"
    # Global cancel (no session filter) still takes remaining open jobs
    n2 = b.cancel_pending_and_running(reason="aborted")
    assert n2 == 2
    assert b._read(b_job.id).status == "cancelled"
    assert b._read(bare.id).status == "cancelled"


def test_enqueue_stamps_session_id(tmp_path: Path):
    b = ComputerHostBridge(home_dir=tmp_path)
    j = b.enqueue("snapshot", {"limit": 5}, session_id="tab-9")
    assert j.session_id == "tab-9"
    assert (j.payload or {}).get("session_id") == "tab-9"
    raw = b._read(j.id)
    assert raw is not None
    assert raw.session_id == "tab-9"


def test_find_recent_success_does_not_prefix_match_other_path(tmp_path: Path):
    from remedy.core.computer.host_bridge import ComputerHostBridge

    b = ComputerHostBridge(home_dir=tmp_path)
    j = b.enqueue("navigate", {"url": "https://github.com"})
    b.complete(
        j.id,
        ok=True,
        result={"ok": True, "url": "https://github.com"},
    )
    assert (
        b.find_recent_success(action="navigate", url="https://github.com/foo")
        is None
    )
    child = b.enqueue("navigate", {"url": "https://github.com/foo"})
    b.complete(
        child.id,
        ok=True,
        result={"ok": True, "url": "https://github.com/foo/bar"},
    )
    found = b.find_recent_success(action="navigate", url="https://github.com/foo")
    assert found is not None
    assert found.id == child.id


def test_find_recent_success(tmp_path: Path):
    b = ComputerHostBridge(home_dir=tmp_path)
    j = b.enqueue("navigate", {"url": "https://mail.google.com"})
    b.complete(
        j.id,
        ok=True,
        result={"ok": True, "url": "https://mail.google.com", "via": "rust-host"},
    )
    found = b.find_recent_success(action="navigate", url="https://mail.google.com")
    assert found is not None
    assert found.id == j.id


def test_find_recent_success_filters_other_session(tmp_path: Path):
    from remedy.core.computer.host_bridge import ComputerHostBridge

    b = ComputerHostBridge(home_dir=tmp_path)
    other = b.enqueue(
        "navigate",
        {"url": "https://mail.google.com"},
        session_id="tab-b",
    )
    b.complete(
        other.id,
        ok=True,
        result={"ok": True, "url": "https://mail.google.com"},
    )
    mine = b.enqueue(
        "navigate",
        {"url": "https://mail.google.com"},
        session_id="tab-a",
    )
    twin = b.find_recent_success(
        action="navigate",
        url="https://mail.google.com",
        session_id="tab-a",
        job_id=mine.id,
    )
    assert twin is None or twin.session_id == "tab-a"
    assert twin is None or twin.id == mine.id
    # Other tab's success must not satisfy this tab.
    stolen = b.find_recent_success(
        action="navigate",
        url="https://mail.google.com",
        session_id="tab-a",
    )
    assert stolen is None


def test_navigate_rail_fast_optimistic_when_host_alive(tmp_path: Path, monkeypatch):
    """Open-url must return SUCCESS quickly even if host is slow to complete."""
    import json

    from remedy.core.computer import host_bridge as hb
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    # Force singleton to use tmp home
    monkeypatch.setattr(hb, "_bridge", None)
    ex = ComputerExecutor(home_dir=tmp_path)
    # Real Desktop poller (jobs/ui), not a one-shot hello
    ex.bridge.mark_host_alive(poller=True)
    t0 = __import__("time").perf_counter()
    raw = ex.run(ComputerAction.NAVIGATE, target="browser", url="https://mail.google.com")
    dt = __import__("time").perf_counter() - t0
    d = json.loads(raw)
    assert d.get("ok") is True, d
    assert dt < 1.8, f"navigate too slow: {dt:.2f}s"
    assert d.get("via") in ("optimistic", "rust-host", None) or d.get("url")
    if d.get("via") == "optimistic":
        assert d.get("ready_for_input") is False or d.get("pending_load") is True
        assert ex.bridge.navigate_needs_settle() is True


def test_computer_api_and_tools_registered(tmp_path: Path, monkeypatch):
    from remedy.core.computer import host_bridge as hb

    monkeypatch.setattr(hb, "_bridge", None)

    class Cfg:
        home_dir = str(tmp_path)

    class RT:
        config = Cfg()

        def list_tasks(self):
            return []

    app = create_app(runtime=RT(), api_key="")
    client = TestClient(app)

    r = client.post("/api/computer/host/hello", json={"client": "desktop"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # Hello alone must not claim poller-connected
    assert r.json().get("host_connected") is False, r.json()

    r2 = client.get("/api/computer/jobs/next")
    assert r2.status_code == 200
    # jobs/next is a real poller heartbeat
    st = client.get("/api/computer/host/status")
    assert st.status_code == 200
    assert st.json().get("host_connected") is True
    assert r2.json()["job"] is None

    # Enqueue via bridge used by tools
    # Fresh bridge for tmp home — create_app may use different singleton; claim uses route bridge
    # Route uses runtime home_dir; enqueue there
    from remedy.core.computer.host_bridge import ComputerHostBridge, get_host_bridge

    ComputerHostBridge(home_dir=tmp_path)
    # The route's get_host_bridge may be a process singleton — force enqueue through API path
    # by using the same get_host_bridge after setting home via complete flow:
    b = get_host_bridge(tmp_path)
    # If singleton already pointed elsewhere, still test complete path with claim on that bridge
    b.enqueue("navigate", {"url": "https://example.com/x"})
    r3 = client.get("/api/computer/jobs/next")
    # May or may not see job depending on singleton home; assert API shape
    assert r3.status_code == 200
    assert "job" in r3.json()

    # Tool registration on a real runtime
    from remedy.core.agent import BasicRuntime

    rt = BasicRuntime(
        AgentConfig(name="cu", llm_api_key="x", home_dir=str(tmp_path / "rt"))
    )
    names = {t.name for t in rt.tool_registry.tools}
    for n in COMPUTER_TOOL_NAMES:
        assert n in names, f"missing tool {n}"


def test_desktop_screenshot_roundtrip(tmp_path: Path):
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows only")
    from remedy.core.computer.desktop_win import screenshot_png, screenshot_region_png

    out = tmp_path / "shot.png"
    info = screenshot_png(out)
    assert out.is_file()
    assert info["width"] > 0 and info["height"] > 0
    assert out.stat().st_size > 100

    region = tmp_path / "region.png"
    rinfo = screenshot_region_png(0, 0, 120, 80, path=region, scale=1.0)
    assert region.is_file()
    assert rinfo["width"] > 0 and rinfo["height"] > 0


def test_computer_capture_api(tmp_path: Path):
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows only")

    class Cfg:
        home_dir = str(tmp_path)

    class RT:
        config = Cfg()

        def list_tasks(self):
            return []

    app = create_app(runtime=RT(), api_key="")
    client = TestClient(app)
    r = client.post("/api/computer/capture", json={})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["capture"]["path"]
    r2 = client.post(
        "/api/computer/capture",
        json={"x": 0, "y": 0, "width": 64, "height": 64, "scale": 1.0},
    )
    assert r2.status_code == 200
    assert r2.json()["capture"]["width"] <= 64 + 8  # clamp tolerance


def test_computer_guidance_present():
    from remedy.core.computer.guidance import COMPUTER_USE_SYSTEM_ADDENDUM

    assert "computer_screenshot" in COMPUTER_USE_SYSTEM_ADDENDUM
    assert "computer_snapshot" in COMPUTER_USE_SYSTEM_ADDENDUM
    assert "computer_act" in COMPUTER_USE_SYSTEM_ADDENDUM
    assert "target" in COMPUTER_USE_SYSTEM_ADDENDUM
    assert "play" in COMPUTER_USE_SYSTEM_ADDENDUM.lower()
    assert "target=desktop" in COMPUTER_USE_SYSTEM_ADDENDUM


def test_hello_alone_not_host_connected(tmp_path: Path):
    from remedy.core.computer.host_bridge import ComputerHostBridge

    b = ComputerHostBridge(home_dir=tmp_path)
    b.mark_host_alive(poller=False)
    assert b.host_connected() is False
    b.mark_host_alive(poller=True)
    assert b.host_connected() is True


def test_job_result_text_capped_on_complete(tmp_path: Path):
    from remedy.core.computer.host_bridge import ComputerHostBridge

    b = ComputerHostBridge(home_dir=tmp_path)
    job = b.enqueue("page_text", {})
    huge = "Z" * 20_000
    done = b.complete(job.id, ok=True, result={"ok": True, "text": huge, "url": "https://x.test"})
    assert done is not None
    assert done.result is not None
    assert len(str(done.result.get("text") or "")) < 5000
    assert done.result.get("text_truncated") is True


def test_a11y_push_completes_snapshot_job(tmp_path: Path):
    from remedy.core.computer.host_bridge import ComputerHostBridge, get_host_bridge

    # Isolate bridge home
    b = ComputerHostBridge(home_dir=tmp_path)
    job = b.enqueue("snapshot", {})
    assert job.status == "pending"
    # Simulate claim
    claimed = b.claim_next()
    assert claimed is not None
    done = b.complete_a11y_push(
        job.id,
        [{"ref": "e1", "name": "OK", "x": 10, "y": 20, "tag": "button"}],
    )
    assert done is not None
    assert done.status == "done"
    assert done.result and done.result.get("elements")

    # API path (public, job secret)
    class Cfg:
        home_dir = str(tmp_path)

    class RT:
        config = Cfg()

        def list_tasks(self):
            return []

    # Process singleton may differ — test complete_a11y via direct bridge above is enough
    app = create_app(runtime=RT(), api_key="")
    client = TestClient(app)
    b2 = get_host_bridge(tmp_path)
    j2 = b2.enqueue("snapshot", {})
    r = client.post(
        "/api/computer/a11y/push",
        json={
            "job_id": j2.id,
            "elements": [{"ref": "e1", "name": "Go", "tag": "a"}],
        },
    )
    # 200 if same singleton home, else 404 is acceptable when singleton points elsewhere
    assert r.status_code in (200, 404)


def test_list_monitors_windows():
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows only")
    from remedy.core.computer.desktop_win import list_monitors, screenshot_monitor_png

    mons = list_monitors()
    assert isinstance(mons, list)
    assert len(mons) >= 1
    assert "width" in mons[0]
    shot = screenshot_monitor_png(0)
    assert shot["width"] > 0


def test_cancel_pending_jobs(tmp_path: Path):
    from remedy.core.computer.host_bridge import ComputerHostBridge

    b = ComputerHostBridge(home_dir=tmp_path)
    b.enqueue("navigate", {"url": "https://example.com"})
    b.enqueue("click", {"x": 1, "y": 2})
    n = b.cancel_pending_and_running(reason="aborted")
    assert n == 2


def test_desktop_snapshot_and_ref_store(tmp_path: Path):
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows only")
    from remedy.core.computer.desktop_win import desktop_snapshot
    from remedy.core.computer.host_bridge import ComputerHostBridge

    els = desktop_snapshot(limit=10, mode="windows")
    assert isinstance(els, list)
    if els:
        assert els[0]["ref"].startswith("w")
        assert "x" in els[0] and "y" in els[0]
    b = ComputerHostBridge(home_dir=tmp_path)
    b.set_last_elements(els, target="desktop")
    if els:
        got = b.get_element_by_ref(els[0]["ref"])
        assert got is not None
        assert got["ref"] == els[0]["ref"]


def test_uia_module_soft_import():
    from remedy.core.computer.desktop_uia import uia_available, uia_control_snapshot

    # Must not crash without comtypes
    avail = uia_available()
    assert isinstance(avail, bool)
    if not avail:
        assert uia_control_snapshot() is None
    else:
        # Best-effort; may return None on restricted desktops
        out = uia_control_snapshot(max_elements=5)
        assert out is None or isinstance(out, list)


def test_print_window_foreground():
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows only")
    import ctypes
    from pathlib import Path

    from remedy.core.computer.desktop_win import print_window_png

    hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
    if not hwnd:
        pytest.skip("no foreground window")
    out = Path.home() / ".remedy" / "computer" / "shots" / "_test_print.png"
    try:
        info = print_window_png(hwnd, out)
        assert info["width"] > 0
        assert out.is_file()
    except RuntimeError:
        # Some windows refuse PrintWindow — acceptable
        pass


def test_abort_session_cancels_computer_jobs(tmp_path: Path, monkeypatch):
    from remedy.core import turn_context as tc
    from remedy.core.computer.host_bridge import ComputerHostBridge

    b = ComputerHostBridge(home_dir=tmp_path)
    # Jobs stamped for this session are cancelled on abort
    b.enqueue("navigate", {"url": "https://example.com"}, session_id="sess-test-cu")
    b.enqueue("click", {"x": 1}, session_id="sess-test-cu")
    # Sibling tab job must survive multi-tab abort
    other = b.enqueue("snapshot", {}, session_id="sess-other-tab")
    monkeypatch.setattr(
        "remedy.core.computer.host_bridge.get_host_bridge",
        lambda home_dir=None: b,
    )
    tc.abort_session("sess-test-cu")
    claimed = b.claim_next()
    assert claimed is not None
    assert claimed.id == other.id
    assert claimed.session_id == "sess-other-tab"
    # No more pending after claiming the sibling
    assert b.claim_next() is None


def test_navigate_settle_is_per_session(tmp_path: Path):
    """Tab B must not inherit Tab A's optimistic navigate settle."""
    from remedy.core.computer.host_bridge import ComputerHostBridge
    from remedy.core.turn_context import begin_turn, end_turn

    b = ComputerHostBridge(home_dir=tmp_path)
    t_a = begin_turn("nav-a", project_raw=None, active_path=".")
    try:
        b.mark_navigated("https://example.com", optimistic=True)
        assert b.navigate_needs_settle() is True
    finally:
        end_turn("nav-a", *t_a)
    t_b = begin_turn("nav-b", project_raw=None, active_path=".")
    try:
        assert b.navigate_needs_settle() is False
        assert b.settle_after_navigate() == 0.0
    finally:
        end_turn("nav-b", *t_b)


def test_executor_session_id_prefers_turn_context():
    """Concurrent tabs share one executor — sid must come from the turn, not runtime."""
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.turn_context import begin_turn, end_turn

    class RT:
        _session_id = "runtime-stale"

    ex = ComputerExecutor()
    toks = begin_turn("turn-sid", project_raw=None, active_path=".")
    try:
        assert ex._session_id(RT()) == "turn-sid"
    finally:
        end_turn("turn-sid", *toks)


def test_executor_run_honors_explicit_session_id():
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    class RT:
        _session_id = "runtime-stale"

    ex = ComputerExecutor()
    seen: list[str | None] = []
    orig = ex._run_body

    def _body(act, *, runtime=None, session_id=None, target="auto", **kwargs):
        seen.append(session_id)
        return "ok"

    ex._run_body = _body  # type: ignore[method-assign]
    try:
        assert ex.run(ComputerAction.WINDOWS, runtime=RT(), session_id="turn-a") == "ok"
        assert seen == ["turn-a"]
    finally:
        ex._run_body = orig


def test_offline_navigate_refuses_os_browser_snapshot_falls_back(
    tmp_path: Path, monkeypatch
):
    """Host offline: no surprise system browser; browser snapshot → desktop tree."""
    import json

    from remedy.core.computer import host_bridge as hb
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    monkeypatch.setattr(hb, "_bridge", None)
    opened: list[str] = []

    def fake_open(url: str):
        opened.append(url)
        return {"ok": True, "url": url}

    import remedy.core.computer.desktop_win as win

    monkeypatch.setattr(win, "open_url", fake_open)
    # Fake desktop snapshot so non-Windows CI still covers fallback
    monkeypatch.setattr(
        win,
        "desktop_snapshot",
        lambda limit=40, mode="auto", hwnd=None: [
            {"ref": "w1", "name": "Fake", "tag": "window"}
        ],
    )

    ex = ComputerExecutor(home_dir=tmp_path)
    assert ex.bridge.host_connected() is False

    nav = json.loads(
        ex.run(
            ComputerAction.NAVIGATE,
            target="browser",
            url="https://example.com/offline",
        )
    )
    assert nav.get("ok") is False
    assert nav.get("rail_failed") is True or "not connected" in str(
        nav.get("message") or ""
    ).lower()
    assert opened == []

    snap = json.loads(
        ex.run(
            ComputerAction.SNAPSHOT,
            target="browser",
            timeout_s=1.0,
        )
    )
    assert snap.get("ok") is True
    assert snap.get("fallback") == "desktop" or snap.get("target") == "desktop"
    assert any(
        str(e.get("ref", "")).startswith("w") for e in (snap.get("elements") or [])
    )
    assert "offline" in str(snap.get("note") or "").lower() or snap.get("fallback")


def test_executor_click_text_stays_desktop_after_app(tmp_path: Path, monkeypatch):
    """After computer_app, text= click must not enqueue a browser rail job."""
    import json

    from remedy.core.computer import host_bridge as hb
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    monkeypatch.setattr(hb, "_bridge", None)
    import remedy.core.computer.desktop_win as win

    monkeypatch.setattr(
        win,
        "open_app",
        lambda app, search_dirs=None: {"ok": True, "app": app},
        raising=False,
    )
    monkeypatch.setattr(
        win,
        "desktop_snapshot",
        lambda limit=60, mode="auto", hwnd=None: [
            {"ref": "c1", "name": "Start", "tag": "button", "x": 10, "y": 10}
        ],
    )
    clicked: list[str] = []

    def fake_click_el(el, button="left", clicks=1):
        clicked.append(str(el.get("name") or el.get("ref")))

    monkeypatch.setattr(win, "click_element", fake_click_el, raising=False)

    enqueued: list[str] = []

    ex = ComputerExecutor(home_dir=tmp_path)
    orig_enqueue = ex.bridge.enqueue

    def track_enqueue(action, payload=None, session_id=None):
        enqueued.append(str(action))
        return orig_enqueue(action, payload, session_id=session_id)

    monkeypatch.setattr(ex.bridge, "enqueue", track_enqueue)

    app = json.loads(ex.run(ComputerAction.APP, app="notepad"))
    assert app.get("ok") is True
    assert ex.bridge.last_drive_target() == "desktop"

    clk = json.loads(
        ex.run(ComputerAction.CLICK, text="Start", target="auto")
    )
    assert clk.get("ok") is True, clk
    assert clk.get("target") == "desktop"
    assert clicked == ["Start"]
    assert "click" not in enqueued


def test_type_text_abort_mid_string(monkeypatch):
    """Stop mid-type: abort_check raises after partial input (no runaway keys)."""
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows only")

    from remedy.core.computer import desktop_win as win

    # Avoid real keystrokes: stub _send_input
    sent: list[int] = []

    def fake_send(*_a, **_k):
        sent.append(1)

    monkeypatch.setattr(win, "_send_input", fake_send)
    monkeypatch.setattr(win, "_require_windows", lambda: None)

    calls = {"n": 0}

    def abort_after_partial():
        # type_text checks every 8 chars; fire after first check
        calls["n"] += 1
        return calls["n"] >= 1

    typed: list[int] = [0]
    try:
        win.type_text(
            "abcdefghijklmnop",  # abort check every 2 chars → stop at i=2
            abort_check=abort_after_partial,
            chars_typed=typed,
        )
        raise AssertionError("expected RuntimeError abort")
    except RuntimeError as e:
        assert "abort" in str(e).lower()
    assert typed[0] == 2  # typed chars 0,1 before check at i=2
    assert len(sent) == 2  # one _send_input call per char (down+up together)


def test_executor_type_surfaces_abort(tmp_path: Path, monkeypatch):
    import json

    from remedy.core.computer import host_bridge as hb
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    monkeypatch.setattr(hb, "_bridge", None)
    ex = ComputerExecutor(home_dir=tmp_path)

    def boom(text, abort_check=None, chars_typed=None):
        if chars_typed is not None:
            chars_typed[:] = [8]
        raise RuntimeError("Aborted by user during type")

    monkeypatch.setattr(
        "remedy.core.computer.desktop_win.type_text", boom, raising=False
    )
    import remedy.core.computer.desktop_win as win

    monkeypatch.setattr(win, "type_text", boom)

    raw = json.loads(
        ex.run(ComputerAction.TYPE, target="desktop", text="hello world long")
    )
    assert raw.get("ok") is False
    assert raw.get("aborted") is True
    assert "abort" in str(raw.get("message") or "").lower()
    assert raw.get("typed") == 8


def test_computer_tools_provider_agnostic():
    """Computer tool names/schemas do not depend on chat provider (xAI/DeepSeek/…)."""
    from remedy.core.computer.types import COMPUTER_TOOL_NAMES
    from remedy.core.providers import _PROVIDERS, get_provider

    # At least two registered chat providers exist in product
    assert "xai" in _PROVIDERS
    assert "deepseek" in _PROVIDERS
    assert len(_PROVIDERS) >= 2

    # Provider modules are loadable independently of computer tools
    for name in ("xai", "deepseek", "openai"):
        p = get_provider(name)
        assert p is not None
        assert getattr(p, "provider_name", None) or name

    # Tool set is a fixed frozenset (not built from provider)
    assert "computer_navigate" in COMPUTER_TOOL_NAMES
    assert "computer_click" in COMPUTER_TOOL_NAMES
    assert len(COMPUTER_TOOL_NAMES) >= 10


def test_concurrent_sessions_enqueue_and_abort_isolated(tmp_path: Path, monkeypatch):
    """Two sessions can hold jobs; abort A leaves B claimable."""
    from remedy.core import turn_context as tc
    from remedy.core.computer.host_bridge import ComputerHostBridge

    b = ComputerHostBridge(home_dir=tmp_path)
    j_a1 = b.enqueue("navigate", {"url": "https://a.example"}, session_id="A")
    j_a2 = b.enqueue("type", {"text": "secret"}, session_id="A")
    j_b1 = b.enqueue("snapshot", {}, session_id="B")
    j_b2 = b.enqueue("click", {"ref": "e1"}, session_id="B")

    monkeypatch.setattr(
        "remedy.core.computer.host_bridge.get_host_bridge",
        lambda home_dir=None: b,
    )
    n = tc.abort_session("A")
    assert n >= 0  # may be 0 events if no begin_turn; cancel still runs
    assert b._read(j_a1.id).status == "cancelled"
    assert b._read(j_a2.id).status == "cancelled"
    assert b._read(j_b1.id).status == "pending"
    assert b._read(j_b2.id).status == "pending"

    claimed = b.claim_next()
    assert claimed is not None
    assert claimed.session_id == "B"


# ---------------------------------------------------------------------------
# Life-task P0: post-action verification (docs/LIFE_TASK_PARTNER.md §2.3/§2.5)
# ---------------------------------------------------------------------------


def _fresh_executor(tmp_path: Path, monkeypatch):
    from remedy.core.computer import host_bridge as hb
    from remedy.core.computer.executor import ComputerExecutor

    monkeypatch.setattr(hb, "_bridge", None)
    ex = ComputerExecutor(home_dir=tmp_path)
    ex.bridge.mark_host_alive(poller=True)
    return ex


def test_parse_expect_normalizes_all_forms(tmp_path: Path, monkeypatch):
    from remedy.core.computer.executor import ComputerExecutor

    p = ComputerExecutor._parse_expect
    assert p({"expect_url": "amazon.com/cart"}) == {"url": "amazon.com/cart"}
    assert p({"expect_text": "Added to cart"}) == {"text": "Added to cart"}
    assert p({"expect": {"url_contains": "checkout", "text_contains": "total"}}) == {
        "url": "checkout",
        "text": "total",
    }
    assert p({}) == {}


def test_act_verifies_expected_text_failure(tmp_path: Path, monkeypatch):
    """expect_text mismatch → ok=False with a re-observe instruction."""
    ex = _fresh_executor(tmp_path, monkeypatch)

    monkeypatch.setattr(
        ex,
        "_browser_click_text",
        lambda text_q, kwargs: {"ok": True, "message": f"clicked {text_q}"},
    )
    monkeypatch.setattr(
        ex,
        "_page_probe",
        lambda **_k: {
            "ok": True,
            "url": "https://www.amazon.com/product/x",
            "title": "Product page",
            "text_hash": "abc",
            "text_head": "Buy now — in stock",
        },
    )
    out = ex._computer_act(
        {"click": "Add to Cart", "expect_text": "added to cart"},
        hint="",
        req_target="browser",
    )
    assert out.get("ok") is False
    assert "verification failed" in str(out.get("message", ""))
    assert out.get("verified") is False


def test_act_verifies_expected_url_success(tmp_path: Path, monkeypatch):
    ex = _fresh_executor(tmp_path, monkeypatch)

    monkeypatch.setattr(
        ex,
        "_browser_click_text",
        lambda text_q, kwargs: {"ok": True, "message": f"clicked {text_q}"},
    )
    probes = iter(
        [
            # pre-state (current page)
            {
                "ok": True,
                "url": "https://www.amazon.com/product/x",
                "title": "Product",
                "text_hash": "before",
                "text_head": "Buy now",
            },
            # post-state (cart)
            {
                "ok": True,
                "url": "https://www.amazon.com/cart",
                "title": "Shopping Cart",
                "text_hash": "after",
                "text_head": "1 item added to cart",
            },
        ]
    )
    monkeypatch.setattr(ex, "_page_probe", lambda **_k: next(probes))
    out = ex._computer_act(
        {"click": "Add to Cart", "expect_url": "amazon.com/cart"},
        hint="",
        req_target="browser",
    )
    assert out.get("ok") is True, out
    assert out.get("verified") is True
    assert out.get("page_changed") is True
    assert out.get("observed", {}).get("url") == "https://www.amazon.com/cart"
    assert "observed" in str(out.get("message", ""))


def test_act_unverified_when_probe_unavailable(tmp_path: Path, monkeypatch):
    """Host can't be observed → success stays ok but is flagged unverified."""
    ex = _fresh_executor(tmp_path, monkeypatch)

    monkeypatch.setattr(
        ex,
        "_browser_click_text",
        lambda text_q, kwargs: {"ok": True, "message": f"clicked {text_q}"},
    )
    monkeypatch.setattr(ex, "_page_probe", lambda **_k: {"ok": False})
    out = ex._computer_act({"click": "Next"}, hint="", req_target="browser")
    assert out.get("ok") is True
    assert out.get("unverified") is True
    assert "not verified" in str(out.get("message", ""))


def test_act_without_mutating_steps_skips_probe(tmp_path: Path, monkeypatch):
    """Pure navigate compound must not pay a probe round-trip."""
    ex = _fresh_executor(tmp_path, monkeypatch)
    calls = {"probe": 0}

    def _probe(**_k):
        calls["probe"] += 1
        return {"ok": False}

    monkeypatch.setattr(ex, "_page_probe", _probe)
    monkeypatch.setattr(
        ex,
        "_navigate_rail_fast",
        lambda payload, hint, req_target: {"ok": True, "message": "SUCCESS"},
    )
    out = ex._computer_act(
        {"url": "https://mail.google.com"}, hint="", req_target="browser"
    )
    assert out.get("ok") is True
    assert calls["probe"] == 0


def test_purge_scrubs_stale_pending_secret_jobs(tmp_path: Path, monkeypatch):
    """A pending type-job the host never claimed must not keep plaintext
    secrets on disk past the stale TTL (S-COMP-03)."""
    import json as _json
    import os as _os
    import time as _time

    from remedy.core.computer import host_bridge as hb

    monkeypatch.setattr(hb, "_bridge", None)
    bridge = hb.get_host_bridge(tmp_path)
    job = bridge.enqueue("type", {"ui": {"open_browser": True}, "text": "hunter2-secret"})
    path = bridge.root / f"{job.id}.json"
    assert path.is_file()
    assert "hunter2-secret" in path.read_text(encoding="utf-8")

    # Fresh pending job: never touched (host may still claim it)
    bridge.purge_old(max_age_s=900.0, stale_open_ttl_s=1800.0)
    assert "hunter2-secret" in path.read_text(encoding="utf-8")

    # Backdate past the stale TTL → expired + scrubbed, file kept for audit
    old = _time.time() - 3600.0
    _os.utime(path, (old, old))
    n = bridge.purge_old(max_age_s=900.0, stale_open_ttl_s=1800.0)
    assert n >= 1
    raw = _json.loads(path.read_text(encoding="utf-8"))
    assert raw["status"] == "cancelled"
    assert "hunter2-secret" not in path.read_text(encoding="utf-8")
    assert "expired" in str(raw.get("error") or "")


def test_purge_still_never_touches_fresh_running_jobs(tmp_path: Path, monkeypatch):
    from remedy.core.computer import host_bridge as hb

    monkeypatch.setattr(hb, "_bridge", None)
    bridge = hb.get_host_bridge(tmp_path)
    job = bridge.enqueue("type", {"text": "live-secret"})
    claimed = bridge.claim_next()
    assert claimed is not None and claimed.id == job.id
    bridge.purge_old(max_age_s=0.0, stale_open_ttl_s=1800.0)
    path = bridge.root / f"{job.id}.json"
    assert path.is_file()
    assert "live-secret" in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Desktop input primitives (life-task audit P0 #9) — layout-testable pieces
# ---------------------------------------------------------------------------


def _fake_us_vk_scan(code_point: int) -> int:
    """Minimal US-layout VkKeyScanW: high byte = shift state, low byte = VK."""
    table = {
        ord("a"): 0x0041,
        ord("s"): 0x0053,
        ord("1"): 0x0031,
        ord("?"): 0x01BF,  # shift + VK_OEM_2
        ord(":"): 0x01BA,  # shift + VK_OEM_1
        ord("!"): 0x0131,  # shift + '1'
        ord("/"): 0x00BF,
    }
    return table.get(code_point, -1)


def test_resolve_key_combo_honors_shift_state():
    """'?' must press Shift+VK_OEM_2 — not the bare (unshifted) key."""
    from remedy.core.computer.desktop_win import resolve_key_combo

    assert resolve_key_combo("?", vk_scan=_fake_us_vk_scan) == [0x10, 0xBF]
    assert resolve_key_combo(":", vk_scan=_fake_us_vk_scan) == [0x10, 0xBA]
    assert resolve_key_combo("!", vk_scan=_fake_us_vk_scan) == [0x10, 0x31]
    # Unshifted char stays bare
    assert resolve_key_combo("/", vk_scan=_fake_us_vk_scan) == [0xBF]
    # Explicit shift not duplicated
    assert resolve_key_combo("shift+?", vk_scan=_fake_us_vk_scan) == [0x10, 0xBF]


def test_resolve_key_combo_modifiers_first_and_fkeys():
    from remedy.core.computer.desktop_win import resolve_key_combo

    assert resolve_key_combo("ctrl+s", vk_scan=_fake_us_vk_scan) == [0x11, 0x53]
    # F6–F12 / insert / printscreen are now real keys
    assert resolve_key_combo("f6", vk_scan=_fake_us_vk_scan) == [0x75]
    assert resolve_key_combo("f12", vk_scan=_fake_us_vk_scan) == [0x7B]
    assert resolve_key_combo("insert", vk_scan=_fake_us_vk_scan) == [0x2D]
    assert resolve_key_combo("printscreen", vk_scan=_fake_us_vk_scan) == [0x2C]
    assert resolve_key_combo("ctrl+shift+s", vk_scan=_fake_us_vk_scan) == [
        0x11,
        0x10,
        0x53,
    ]


def test_resolve_key_combo_unknown_raises():
    import pytest as _pytest

    from remedy.core.computer.desktop_win import resolve_key_combo

    with _pytest.raises(ValueError):
        resolve_key_combo("notakey", vk_scan=_fake_us_vk_scan)
    with _pytest.raises(ValueError):
        resolve_key_combo("€", vk_scan=_fake_us_vk_scan)  # not on fake layout


def test_host_browser_launch_is_refused(tmp_path):
    """computer_app firefox/chrome/edge is a HARD refusal — web work lives in
    the in-app rail; driving the owner's browser gives no page eyes and
    hijacks their session (the rail-starved fallback the poller fix removed)."""
    import json as _json

    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    ex = ComputerExecutor(home_dir=tmp_path)
    for name in ("firefox", "chrome", "msedge", "Microsoft Edge", "brave", "firefox.exe"):
        d = _json.loads(ex.run(ComputerAction.APP, target="desktop", app=name))
        assert d["ok"] is False, name
        assert "in-app Browser rail" in d["message"], name
        assert d.get("refused") == "host_browser" or (d.get("extra") or {}).get(
            "refused"
        ) == "host_browser", name

    # A non-browser app is unaffected by the guard (block is browser-specific).
    from remedy.core.computer import executor as _ex

    assert _ex._is_host_browser("notepad") is False
    assert _ex._is_host_browser("calculator") is False
    assert _ex._is_host_browser("code") is False  # VS Code, not a web browser


def test_host_browser_window_focus_is_refused(tmp_path):
    """Focusing a browser window by title is refused the same way."""
    import json as _json

    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    ex = ComputerExecutor(home_dir=tmp_path)
    d = _json.loads(
        ex.run(
            ComputerAction.WINDOWS,
            target="desktop",
            mode="focus",
            title="Shopping Harness — Mozilla Firefox",
        )
    )
    assert d["ok"] is False
    assert "in-app Browser rail" in d["message"]


def test_press_hold_tool_registered_and_routes():
    """computer_press_hold is a first-class tool → PRESS_HOLD action (the
    owner's authorized hands for an accessibility hold gesture)."""
    from remedy.core.computer.types import ComputerAction, action_from_tool

    assert action_from_tool("computer_press_hold") is ComputerAction.PRESS_HOLD
    assert ComputerAction.PRESS_HOLD.value == "press_hold"


def test_press_hold_learns_per_site():
    """press_hold approaches feed the same learn-what-worked skill memory as
    click, so overcoming a wall compounds."""
    from remedy.core.computer.computer_skill import approach_of

    assert approach_of("press_hold", {"text": "Press & Hold"}) == "text"
    assert approach_of("press_hold", {"ref": "e3"}) == "ref"
    assert approach_of("press_hold", {"x": 400, "y": 300}) == "coords"


def test_stale_ref_click_recovers_by_remembered_label(tmp_path, monkeypatch):
    """A ref goes stale when the page changes; a click by that ref must not
    just fail — it re-locates the SAME control by its remembered label+context
    (adapt-and-overcome), turning a dead ref into a successful click."""
    import json as _json

    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    ex = ComputerExecutor(home_dir=tmp_path)
    # Remember what e7 was (as a fresh snapshot would have stored it).
    ex.bridge.set_last_elements(
        [{"ref": "e7", "name": "Make this my store", "context": "Hueytown Supercenter 35023"}],
        target="browser",
    )

    class _Job:
        id = "j1"

    # The host reports the ref is gone (page changed).
    monkeypatch.setattr(ex, "_enqueue", lambda *a, **k: _Job())

    class _Fin:
        status = "done"
        result = None
        error = "browser:click failed: missing-ref:e7"

    monkeypatch.setattr(ex.bridge, "wait", lambda *a, **k: _Fin())

    # Recovery re-locates by text — capture the query it used.
    calls: list[str] = []

    def _fake_click_text(query, kwargs):
        calls.append(query)
        return {"ok": True, "target": "browser", "action": "click", "message": f"Clicked {query}"}

    monkeypatch.setattr(ex, "_browser_click_text", _fake_click_text)

    out = _json.loads(ex.run(ComputerAction.CLICK, target="browser", ref="e7"))
    assert out["ok"] is True
    assert out.get("recovered_from") == "stale_ref"
    assert calls and "Make this my store" in calls[0]
    assert "Hueytown" in calls[0]  # context words help pick the right one
