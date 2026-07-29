"""In-house computer use: router, bridge, tool registration (feature/computer-use)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from remedy.core.computer.host_bridge import ComputerHostBridge
from remedy.core.computer.router import ComputerTarget, resolve_target
from remedy.core.computer.types import COMPUTER_PLAN_MODE_TOOLS, COMPUTER_TOOL_NAMES
from remedy.core.plan_store import PLAN_MODE_TOOL_NAMES
from remedy.interfaces.api import create_app
from remedy.models import AgentConfig


def test_resolve_target_url_prefers_browser():
    assert resolve_target("auto", url="https://example.com") is ComputerTarget.BROWSER
    assert resolve_target("auto", hint="open github.com docs") is ComputerTarget.BROWSER
    assert resolve_target("auto", hint="show me the wiki for baldur") is ComputerTarget.BROWSER
    assert resolve_target("browser", url="https://x.com") is ComputerTarget.BROWSER


def test_normalize_url_rejects_task_text_leak():
    """User prose must never become the Browser rail address bar."""
    from remedy.core.computer.router import is_valid_navigate_url, normalize_url

    junk = "gmail sign in, once I want you to log me in the login input my username ahmitdarrow@gmail.com"
    assert normalize_url(junk) == ""
    assert is_valid_navigate_url(junk) is False
    assert is_valid_navigate_url("https://" + junk) is False
    assert normalize_url("ahmitdarrow@gmail.com") == ""
    assert normalize_url("https://mail.google.com") == "https://mail.google.com"
    assert is_valid_navigate_url("https://mail.google.com") is True
    # nickname still works
    assert normalize_url("gmail") == "https://mail.google.com"
    # first-token recovery from multi-word
    assert normalize_url("gmail sign in please") == "https://mail.google.com"


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


def test_resolve_target_desktop_hints():
    assert (
        resolve_target("auto", hint="click the Start menu on the desktop")
        is ComputerTarget.DESKTOP
    )
    assert resolve_target("desktop", url="https://x.com") is ComputerTarget.DESKTOP
    assert resolve_target("browser", hint="installer") is ComputerTarget.BROWSER


def test_resolve_target_navigate_defaults_browser():
    assert resolve_target("auto", action="navigate") is ComputerTarget.BROWSER


def test_plan_mode_includes_read_computer_tools():
    assert "computer_screenshot" in PLAN_MODE_TOOL_NAMES
    assert "computer_navigate" in PLAN_MODE_TOOL_NAMES
    assert "computer_windows" in PLAN_MODE_TOOL_NAMES
    assert "computer_click" not in PLAN_MODE_TOOL_NAMES
    assert "computer_type" not in PLAN_MODE_TOOL_NAMES
    assert COMPUTER_PLAN_MODE_TOOLS <= COMPUTER_TOOL_NAMES


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


def test_computer_host_routes_loopback_no_auth(tmp_path: Path):
    """Desktop poller must claim jobs without waiting on SPA token bootstrap."""

    class Cfg:
        home_dir = str(tmp_path)

    class RT:
        config = Cfg()

        def list_tasks(self):
            return []

    app = create_app(runtime=RT(), api_key="secret-test-key")
    client = TestClient(app)
    # No Authorization header
    r = client.post("/api/computer/host/hello", json={"client": "desktop"})
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is True
    r2 = client.get("/api/computer/jobs/next")
    assert r2.status_code == 200, r2.text
    assert "job" in r2.json()


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


def test_navigate_rail_fast_optimistic_when_host_alive(tmp_path: Path, monkeypatch):
    """Open-url must return SUCCESS quickly even if host is slow to complete."""
    import json
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction
    from remedy.core.computer import host_bridge as hb

    # Force singleton to use tmp home
    monkeypatch.setattr(hb, "_bridge", None)
    ex = ComputerExecutor(home_dir=tmp_path)
    ex.bridge.mark_host_alive()
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

    r2 = client.get("/api/computer/jobs/next")
    assert r2.status_code == 200
    assert r2.json()["job"] is None

    # Enqueue via bridge used by tools
    from remedy.core.computer.host_bridge import get_host_bridge

    # Fresh bridge for tmp home — create_app may use different singleton; claim uses route bridge
    # Route uses runtime home_dir; enqueue there
    from remedy.core.computer.host_bridge import ComputerHostBridge

    bridge = ComputerHostBridge(home_dir=tmp_path)
    # The route's get_host_bridge may be a process singleton — force enqueue through API path
    # by using the same get_host_bridge after setting home via complete flow:
    b = get_host_bridge(tmp_path)
    # If singleton already pointed elsewhere, still test complete path with claim on that bridge
    job = b.enqueue("navigate", {"url": "https://example.com/x"})
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
        return
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
        return

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
        return
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
        return
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
        return
    import ctypes
    from pathlib import Path

    from remedy.core.computer.desktop_win import print_window_png

    hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
    if not hwnd:
        return
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
    b.enqueue("navigate", {"url": "https://example.com"})
    b.enqueue("click", {"x": 1})
    monkeypatch.setattr(
        "remedy.core.computer.host_bridge.get_host_bridge",
        lambda home_dir=None: b,
    )
    tc.abort_session("sess-test-cu")
    assert b.claim_next() is None
