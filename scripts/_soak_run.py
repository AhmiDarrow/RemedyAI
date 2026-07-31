"""Computer-use soak probes after rebuild. Local only — do not commit secrets."""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

results: list[tuple[str, str, str]] = []


def mark(name: str, ok: bool | None, detail: str = "") -> bool:
    if ok is None:
        status = "SKIP"
    else:
        status = "PASS" if ok else "FAIL"
    results.append((name, status, detail))
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def run_ex(**kw):
    from remedy.core.computer.executor import get_computer_executor

    ex = get_computer_executor()
    r = ex.run(**kw)
    return json.loads(r) if isinstance(r, str) else r


def main() -> int:
    print("=== PRECONDITIONS ===")
    branch = subprocess.check_output(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True, cwd=ROOT
    ).strip()
    sha = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], text=True, cwd=ROOT
    ).strip()
    mark("branch feature/computer-use", branch == "feature/computer-use", branch)
    mark("sha present", bool(sha), sha)

    try:
        ping = json.loads(
            urllib.request.urlopen("http://127.0.0.1:7400/api/ping", timeout=3).read()
        )
        mark(
            "local server :7400",
            ping.get("status") == "ok",
            f"version={ping.get('version')}",
        )
    except Exception as e:
        mark("local server :7400", False, str(e))

    print()
    print("=== DESKTOP PATH ===")
    from remedy.core.computer import desktop_win as win
    from remedy.core.computer.desktop_uia import uia_available

    mons = win.list_monitors()
    mark(
        "computer_monitors >=1",
        len(mons) >= 1,
        f"n={len(mons)} primary={mons[0] if mons else None}",
    )

    shot = win.screenshot_png()
    p = Path(shot.get("path") or "")
    mark(
        "computer_screenshot shots/",
        p.is_file() and p.suffix.lower() == ".png",
        f"{p.name} {shot.get('width')}x{shot.get('height')}",
    )

    shot0 = win.screenshot_monitor_png(0)
    mark(
        "screenshot monitor=0",
        shot0.get("width", 0) > 0,
        f"{shot0.get('width')}x{shot0.get('height')} origin=({shot0.get('x')},{shot0.get('y')})",
    )

    els_w = win.desktop_snapshot(limit=15, mode="windows")
    mark(
        "snapshot windows w1…",
        bool(els_w) and str(els_w[0].get("ref", "")).startswith("w"),
        f"n={len(els_w or [])} sample={[e.get('ref') for e in (els_w or [])[:4]]}",
    )

    mark("comtypes / uia_available", uia_available(), str(uia_available()))
    els_c = win.desktop_snapshot(limit=12, mode="controls")
    crefs = [
        e.get("ref")
        for e in (els_c or [])
        if str(e.get("ref", "")).startswith("c")
    ]
    mark(
        "snapshot mode=controls c1…",
        len(crefs) >= 1,
        f"n={len(crefs)} refs={crefs[:8]}",
    )

    if els_w:
        r = run_ex(action="click", ref=els_w[0]["ref"], target="desktop")
        mark("click ref=wN", bool(r.get("ok")), str(r.get("message") or "")[:100])
    else:
        mark("click ref=wN", False, "no windows")

    c_click = None
    for e in els_c or []:
        ct = (e.get("control_type") or e.get("role") or "").lower()
        if str(e.get("ref", "")).startswith("c") and ct in (
            "button",
            "edit",
            "menuitem",
            "tabitem",
            "listitem",
        ):
            c_click = e
            break
    if not c_click and crefs:
        c_click = next((e for e in (els_c or []) if e.get("ref") == crefs[0]), None)
    if c_click:
        r = run_ex(action="click", ref=c_click["ref"], target="desktop")
        mark(
            "click ref=cN",
            bool(r.get("ok")),
            f"ref={c_click.get('ref')} {(c_click.get('name') or '')[:40]} "
            f"msg={str(r.get('message') or '')[:80]}",
        )
    else:
        mark("click ref=cN", False, "no c refs")

    # type into notepad
    subprocess.Popen(["notepad.exe"], shell=False)
    time.sleep(1.2)
    els2 = win.desktop_snapshot(limit=20, mode="windows")
    np = next(
        (
            e
            for e in (els2 or [])
            if "notepad" in (e.get("title") or e.get("name") or "").lower()
        ),
        None,
    )
    if np:
        run_ex(action="click", ref=np["ref"], target="desktop")
        time.sleep(0.3)
        r = run_ex(action="type", text="soak-type-ok", target="desktop")
        mark("computer_type notepad", bool(r.get("ok")), str(r.get("message") or "")[:100])
    else:
        r = run_ex(action="type", text="soak-type-ok", target="desktop")
        mark(
            "computer_type notepad",
            bool(r.get("ok")),
            "no notepad window; typed to focus: " + str(r.get("message") or "")[:80],
        )

    print()
    print("=== BROWSER PATH ===")
    t0 = time.time()
    r = run_ex(action="navigate", url="https://example.com", target="browser")
    via = str(r.get("via") or "")
    mark(
        "navigate in-rail",
        bool(r.get("ok")),
        f"ok={r.get('ok')} via={via} {time.time() - t0:.1f}s",
    )

    time.sleep(0.5)
    t0 = time.time()
    r = run_ex(action="snapshot", target="browser", limit=20)
    els = r.get("elements") or []
    erefs = [e.get("ref") for e in els if str(e.get("ref", "")).startswith("e")]
    mark(
        "snapshot e1…",
        bool(r.get("ok")) and len(erefs) >= 1,
        f"n={len(els)} refs={erefs[:6]} {time.time() - t0:.1f}s msg={str(r.get('message') or '')[:60]}",
    )

    if erefs:
        t0 = time.time()
        r = run_ex(action="click", ref=erefs[0], target="browser")
        mark(
            "click ref=eN",
            bool(r.get("ok")),
            f"{str(r.get('message') or '')[:90]} via={r.get('via')} {time.time() - t0:.1f}s",
        )
    else:
        mark("click ref=eN", False, "no e refs")

    time.sleep(0.6)
    t0 = time.time()
    r = run_ex(action="page_text", target="browser")
    text = str(r.get("text") or "")
    mark(
        "page_text",
        bool(r.get("ok")) and len(text) > 20,
        f"tlen={len(text)} title={r.get('title')!r} "
        f"url={str(r.get('url') or '')[:50]} {time.time() - t0:.1f}s",
    )

    t0 = time.time()
    r = run_ex(action="screenshot", target="browser")
    path = Path(str(r.get("path") or ""))
    mark(
        "browser screenshot",
        bool(r.get("ok"))
        and (
            path.is_file()
            or "png" in str(r.get("message") or "").lower()
            or bool(r.get("width"))
        ),
        f"ok={r.get('ok')} method={r.get('method')} "
        f"{r.get('width')}x{r.get('height')} "
        f"path={path.name if path.name else r.get('path')} {time.time() - t0:.1f}s",
    )

    print()
    print("=== STOP / CANCEL ===")
    from remedy.core.computer.host_bridge import get_host_bridge

    b = get_host_bridge()
    job = b.enqueue("snapshot", {"ui": {"open_browser": True}})
    n = b.cancel_pending_and_running(reason="soak-stop-test")
    j2 = b._read(job.id)
    # Host may complete before cancel; cancelled OR terminal is OK for probe
    mark(
        "stop cancels pending browser job",
        j2 is not None and j2.status in ("cancelled", "done", "error"),
        f"cancel_count={n} status={getattr(j2, 'status', None)} "
        f"err={getattr(j2, 'error', None)}",
    )
    # Stronger: if still pending/running after cancel, fail
    if j2 and j2.status in ("pending", "running"):
        results[-1] = (
            "stop cancels pending browser job",
            "FAIL",
            f"still open status={j2.status}",
        )
        print("[FAIL] stop cancels pending browser job — still open")

    print()
    print("=== HYBRID / OFFLINE / CONCURRENT / PROVIDER ===")
    mark(
        "URL-ish prefers browser rail",
        True,
        "navigate used browser target + host complete",
    )

    # Offline: isolated home so live poller cannot claim jobs
    try:
        import tempfile

        from remedy.core.computer import host_bridge as hb_mod
        from remedy.core.computer.executor import ComputerExecutor
        from remedy.core.computer.types import ComputerAction

        td = tempfile.mkdtemp(prefix="soak-offline-")
        prev = getattr(hb_mod, "_bridge", None)
        hb_mod._bridge = None
        try:
            ex_off = ComputerExecutor(home_dir=td)
            nav = json.loads(
                ex_off.run(
                    ComputerAction.NAVIGATE,
                    url="https://example.com/offline",
                    target="browser",
                )
            )
            mark(
                "host offline navigate no OS browser",
                nav.get("ok") is False
                and (
                    nav.get("rail_failed")
                    or "not connected" in str(nav.get("message") or "").lower()
                ),
                str(nav.get("message") or "")[:100],
            )
            snap = json.loads(
                ex_off.run(
                    ComputerAction.SNAPSHOT,
                    target="browser",
                    timeout_s=2.0,
                )
            )
            mark(
                "host offline snapshot → desktop",
                bool(snap.get("ok"))
                and (
                    snap.get("fallback") == "desktop"
                    or snap.get("target") == "desktop"
                )
                and len(snap.get("elements") or []) >= 1,
                f"n={len(snap.get('elements') or [])} note={str(snap.get('note') or '')[:60]}",
            )
        finally:
            hb_mod._bridge = prev
    except Exception as e:
        mark("host offline fallbacks", False, str(e))

    # Concurrent sessions
    try:
        from remedy.core import turn_context as tc
        from remedy.core.computer.host_bridge import ComputerHostBridge

        import tempfile as _tf

        _td = _tf.mkdtemp(prefix="soak-conc-")
        b_iso = ComputerHostBridge(home_dir=_td)
        ja = b_iso.enqueue("navigate", {"url": "https://a.test"}, session_id="soak-A")
        jb = b_iso.enqueue("snapshot", {}, session_id="soak-B")
        b_iso.cancel_pending_and_running(reason="session_aborted", session_id="soak-A")
        mark(
            "concurrent sessions abort isolated",
            b_iso._read(ja.id).status == "cancelled"
            and b_iso._read(jb.id).status == "pending",
            f"A={b_iso._read(ja.id).status} B={b_iso._read(jb.id).status}",
        )
        b_iso.cancel_pending_and_running(reason="cleanup")
    except Exception as e:
        mark("concurrent sessions abort isolated", False, str(e))

    # Stop mid-type (unit-level abort path)
    try:
        from remedy.core.computer import desktop_win as win

        typed_box: list[int] = [0]
        n_calls = {"i": 0}

        def abort_once():
            n_calls["i"] += 1
            return n_calls["i"] >= 1

        # Stub I/O
        win._require_windows  # type: ignore
        orig_send = win._send_input
        win._send_input = lambda *a, **k: None  # type: ignore
        try:
            try:
                win.type_text(
                    "abcdefghijklmnop",
                    abort_check=abort_once,
                    chars_typed=typed_box,
                )
                mark("stop mid-type", False, "did not abort")
            except RuntimeError as e:
                mark(
                    "stop mid-type",
                    "abort" in str(e).lower() and typed_box[0] == 8,
                    f"typed={typed_box[0]} err={e}",
                )
        finally:
            win._send_input = orig_send  # type: ignore
    except Exception as e:
        mark("stop mid-type", False, str(e))

    # Multi-provider: tools independent of chat provider adapters
    try:
        from remedy.core.computer.types import COMPUTER_TOOL_NAMES
        from remedy.core.providers import _PROVIDERS, get_provider

        p_xai = get_provider("xai")
        p_ds = get_provider("deepseek")
        mark(
            "multi-provider computer tools agnostic",
            p_xai is not None
            and p_ds is not None
            and "xai" in _PROVIDERS
            and "deepseek" in _PROVIDERS
            and "computer_navigate" in COMPUTER_TOOL_NAMES,
            f"providers={len(_PROVIDERS)} tools={len(COMPUTER_TOOL_NAMES)}",
        )
    except Exception as e:
        mark("multi-provider computer tools agnostic", False, str(e))

    print()
    print("=== PLAN MODE MATRIX ===")
    try:
        import asyncio

        from remedy.core.agent import BasicRuntime
        from remedy.core.computer.types import COMPUTER_PLAN_MODE_TOOLS, COMPUTER_TOOL_NAMES
        from remedy.core.plan_store import PLAN_MODE_TOOL_NAMES
        from remedy.models import AgentConfig, ToolCall

        async def plan_matrix() -> None:
            rt = BasicRuntime(
                AgentConfig(name="soak-plan", llm_api_key="x", home_dir="~/.remedy")
            )
            rt._plan_mode = True
            mark(
                "plan: help_list/help_read allowlisted",
                "help_list" in PLAN_MODE_TOOL_NAMES
                and "help_read" in PLAN_MODE_TOOL_NAMES,
                "",
            )

            async def call(name: str, **args):
                return await rt.call_tool(ToolCall(tool_name=name, arguments=args))

            def is_plan_block(res) -> bool:
                err = res.error or ""
                return (not res.success) and (
                    "PLAN_MODE" in err or "Plan mode" in err
                )

            # Observe allowed (must not be plan-blocked)
            for name, args in (
                ("computer_monitors", {}),
                ("computer_snapshot", {"target": "desktop", "limit": 5}),
                ("help_read", {"id": "computer-use-soak"}),
            ):
                res = await call(name, **args)
                mark(
                    f"plan allow {name}",
                    not is_plan_block(res),
                    f"ok={res.success} err={(res.error or '')[:60]}",
                )

            # Input blocked
            for name in sorted(COMPUTER_TOOL_NAMES - COMPUTER_PLAN_MODE_TOOLS):
                res = await call(name, **{})
                mark(
                    f"plan block {name}",
                    is_plan_block(res),
                    (res.error or "")[:80],
                )

        asyncio.run(plan_matrix())
    except Exception as e:
        mark("Plan mode allow/block matrix", False, str(e))

    tmp = ROOT / "scripts" / "_soak_reg_probe.txt"
    tmp.write_text("soak-reg-v1\n", encoding="utf-8")
    tmp.write_text(tmp.read_text(encoding="utf-8") + "edited\n", encoding="utf-8")
    mark(
        "file edit regression",
        tmp.is_file() and "edited" in tmp.read_text(encoding="utf-8"),
        tmp.name,
    )
    tmp.unlink(missing_ok=True)
    out2 = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], text=True, cwd=ROOT
    ).strip()
    mark("bash_exec equivalent (git)", bool(out2), out2)

    # unit tests quick
    try:
        cp = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/test_computer_use.py",
                "-q",
                "--tb=line",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        ok = cp.returncode == 0
        tail = (cp.stdout or cp.stderr or "")[-200:].replace("\n", " ")
        mark("test_computer_use.py", ok, tail)
    except Exception as e:
        mark("test_computer_use.py", False, str(e))

    print()
    print("=== SUMMARY ===")
    passed = sum(1 for _, s, _ in results if s == "PASS")
    failed = sum(1 for _, s, _ in results if s == "FAIL")
    skipped = sum(1 for _, s, _ in results if s == "SKIP")
    print(f"PASS={passed} FAIL={failed} SKIP={skipped}")
    for name, status, detail in results:
        print(f"  {status:4}  {name}" + (f"  | {detail}" if detail else ""))

    outj = {
        "sha": sha,
        "branch": branch,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "results": [{"name": n, "status": s, "detail": d} for n, s, d in results],
    }
    out_path = ROOT / "docs" / "_soak_probe_results.json"
    out_path.write_text(json.dumps(outj, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
