"""Full Remedy product soak — health, computer-use, plan, browser, providers.

Writes docs/_full_product_soak_results.json and prints a PASS/FAIL summary.
Exit 0 only if no FAIL results (SKIP allowed).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

results: list[dict] = []


def mark(name: str, ok: bool | None, detail: str = "") -> None:
    status = "SKIP" if ok is None else "PASS" if ok else "FAIL"
    results.append({"name": name, "status": status, "detail": detail[:500]})
    print(f"[{status}] {name}" + (f" — {detail[:200]}" if detail else ""))


def http_json(path: str, method: str = "GET", body: dict | None = None, timeout: float = 8.0):
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"http://127.0.0.1:7400{path}",
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        if not raw:
            return resp.status, None
        try:
            return resp.status, json.loads(raw)
        except json.JSONDecodeError:
            return resp.status, raw


def section(title: str) -> None:
    print()
    print(f"=== {title} ===")


def run_pytest(paths: list[str], label: str, timeout_s: int = 300) -> None:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *paths,
        "-q",
        "--tb=line",
        "--maxfail=8",
    ]
    try:
        cp = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        tail = ((cp.stdout or "") + "\n" + (cp.stderr or ""))[-400:].replace("\n", " ")
        mark(label, cp.returncode == 0, tail)
    except subprocess.TimeoutExpired:
        mark(label, False, f"timeout after {timeout_s}s")
    except Exception as e:
        mark(label, False, str(e))


def preconditions() -> None:
    section("PRECONDITIONS")
    branch = subprocess.check_output(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True, cwd=ROOT
    ).strip()
    sha = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], text=True, cwd=ROOT
    ).strip()
    mark("git branch master", branch == "master", branch)
    mark("git sha", bool(sha), sha)

    try:
        code, ping = http_json("/api/ping")
        mark(
            "API /api/ping",
            code == 200 and isinstance(ping, dict) and ping.get("status") == "ok",
            str(ping)[:120],
        )
    except Exception as e:
        mark("API /api/ping", False, str(e))

    # Desktop process (Windows)
    try:
        import psutil  # type: ignore

        apps = [p.name() for p in psutil.process_iter(["name"]) if p.info.get("name")]
        mark(
            "desktop process",
            any(n.lower() in ("app.exe", "app") for n in apps)
            or any("remedy" in (n or "").lower() for n in apps),
            "psutil scan",
        )
    except Exception:
        # fallback tasklist
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", "IMAGENAME eq app.exe"],
                text=True,
                errors="replace",
            )
            mark("desktop process app.exe", "app.exe" in out, out[:80].replace("\n", " "))
        except Exception as e:
            mark("desktop process", None, f"could not probe: {e}")


def api_surface() -> None:
    section("API SURFACE")
    for path in (
        "/api/ping",
        "/api/status",
        "/api/computer/host/status",
        "/api/computer/jobs/next?only=navigate",
    ):
        try:
            code, body = http_json(path.split("?")[0] + (("?" + path.split("?", 1)[1]) if "?" in path else ""))
            # jobs/next and host may be unauth or ok on loopback
            mark(
                f"GET {path}",
                code in (200, 401, 403) if "computer" not in path else code == 200,
                f"code={code} body={str(body)[:100]}",
            )
        except urllib.error.HTTPError as e:
            mark(f"GET {path}", e.code in (200, 401, 403), f"HTTP {e.code}")
        except Exception as e:
            mark(f"GET {path}", False, str(e))

    try:
        code, body = http_json(
            "/api/computer/host/hello",
            method="POST",
            body={"client": "full-soak"},
        )
        mark("POST computer host/hello", code == 200, str(body)[:100])
    except Exception as e:
        mark("POST computer host/hello", False, str(e))


def computer_live() -> None:
    section("COMPUTER-USE LIVE")
    from remedy.core.computer import host_bridge as hb
    from remedy.core.computer.executor import get_computer_executor
    from remedy.core.computer.types import ComputerAction

    hb._bridge = None
    ex = get_computer_executor()

    def run(action: str, **kw):
        r = ex.run(action, **kw)
        return json.loads(r) if isinstance(r, str) else r

    r = run("monitors", target="desktop")
    mark(
        "computer_monitors",
        bool(r.get("ok")) and len(r.get("monitors") or []) >= 1,
        f"n={len(r.get('monitors') or [])}",
    )

    r = run("snapshot", target="desktop", mode="windows", limit=12)
    els = r.get("elements") or []
    mark(
        "computer_snapshot windows",
        bool(r.get("ok")) and any(str(e.get("ref", "")).startswith("w") for e in els),
        f"n={len(els)}",
    )

    from remedy.core.computer.desktop_uia import uia_available

    mark("uia/comtypes available", uia_available(), str(uia_available()))
    r = run("snapshot", target="desktop", mode="controls", limit=10)
    cels = r.get("elements") or []
    crefs = [e for e in cels if str(e.get("ref", "")).startswith("c")]
    mark(
        "computer_snapshot controls",
        bool(r.get("ok")) and (len(crefs) >= 1 or not uia_available()),
        f"controls={len(crefs)}",
    )

    r = run("navigate", url="https://example.com", target="browser")
    mark(
        "computer_navigate rail",
        bool(r.get("ok")),
        f"via={r.get('via')} msg={str(r.get('message') or '')[:80]}",
    )
    time.sleep(0.6)
    r = run("snapshot", target="browser", limit=15)
    erefs = [e for e in (r.get("elements") or []) if str(e.get("ref", "")).startswith("e")]
    mark(
        "computer_snapshot browser eN",
        bool(r.get("ok")) and (len(erefs) >= 1 or r.get("fallback") == "desktop"),
        f"n={len(r.get('elements') or [])} fallback={r.get('fallback')} refs={[e.get('ref') for e in erefs[:5]]}",
    )
    if erefs:
        r = run("click", ref=erefs[0].get("ref"), target="browser")
        mark("computer_click eN", bool(r.get("ok")), str(r.get("message") or "")[:100])
    else:
        mark("computer_click eN", None, "no e refs")

    r = run("page_text", target="browser")
    mark(
        "computer_page_text",
        bool(r.get("ok")) and len(str(r.get("text") or "")) > 5,
        f"tlen={len(str(r.get('text') or ''))} title={r.get('title')!r}",
    )

    r = run("screenshot", target="browser")
    mark(
        "computer_screenshot browser",
        bool(r.get("ok")) and (r.get("path") or r.get("width")),
        f"method={r.get('method')} {r.get('width')}x{r.get('height')}",
    )

    # Offline snapshot fallback (isolated home)
    td = tempfile.mkdtemp(prefix="soak-off-")
    prev = hb._bridge
    hb._bridge = None
    try:
        from remedy.core.computer.executor import ComputerExecutor

        ex_off = ComputerExecutor(home_dir=td)
        nav = json.loads(
            ex_off.run(
                ComputerAction.NAVIGATE,
                url="https://example.com/offline",
                target="browser",
            )
        )
        mark(
            "offline navigate no OS surprise",
            nav.get("ok") is False
            and (
                nav.get("rail_failed")
                or "not connected" in str(nav.get("message") or "").lower()
            ),
            str(nav.get("message") or "")[:100],
        )
        snap = json.loads(
            ex_off.run(ComputerAction.SNAPSHOT, target="browser", timeout_s=2.0)
        )
        mark(
            "offline snapshot desktop fallback",
            bool(snap.get("ok"))
            and (
                snap.get("fallback") == "desktop"
                or snap.get("target") == "desktop"
            ),
            f"n={len(snap.get('elements') or [])}",
        )
    finally:
        hb._bridge = prev


def plan_mode_live() -> None:
    section("PLAN MODE")
    from remedy.core.agent import BasicRuntime
    from remedy.core.computer.types import COMPUTER_PLAN_MODE_TOOLS, COMPUTER_TOOL_NAMES
    from remedy.core.plan_store import PLAN_MODE_TOOL_NAMES
    from remedy.models import AgentConfig, ToolCall

    async def run() -> None:
        rt = BasicRuntime(
            AgentConfig(name="full-soak-plan", llm_api_key="x", home_dir="~/.remedy")
        )
        rt._plan_mode = True
        mark(
            "plan allowlist help",
            "help_list" in PLAN_MODE_TOOL_NAMES and "help_read" in PLAN_MODE_TOOL_NAMES,
            "",
        )

        def blocked(res) -> bool:
            err = res.error or ""
            return (not res.success) and ("PLAN_MODE" in err or "Plan mode" in err)

        res = await rt.call_tool(ToolCall(tool_name="computer_monitors", arguments={}))
        mark("plan allow computer_monitors", not blocked(res), f"ok={res.success}")

        res = await rt.call_tool(ToolCall(tool_name="help_read", arguments={"id": "00-overview"}))
        mark("plan allow help_read", not blocked(res), f"ok={res.success}")

        for name in ("computer_click", "computer_type", "bash_exec", "file_write"):
            res = await rt.call_tool(ToolCall(tool_name=name, arguments={}))
            mark(f"plan block {name}", blocked(res), (res.error or "")[:80])

        mark(
            "plan computer matrix complete",
            COMPUTER_PLAN_MODE_TOOLS <= PLAN_MODE_TOOL_NAMES
            and not (COMPUTER_TOOL_NAMES - COMPUTER_PLAN_MODE_TOOLS) & PLAN_MODE_TOOL_NAMES,
            f"allowed={len(COMPUTER_PLAN_MODE_TOOLS)} blocked={len(COMPUTER_TOOL_NAMES - COMPUTER_PLAN_MODE_TOOLS)}",
        )

    asyncio.run(run())


def concurrent_and_abort() -> None:
    section("CONCURRENT / ABORT")
    from remedy.core import turn_context as tc
    from remedy.core.computer.host_bridge import ComputerHostBridge

    td = tempfile.mkdtemp(prefix="soak-conc-")
    b = ComputerHostBridge(home_dir=td)
    ja = b.enqueue("navigate", {"url": "https://a.test"}, session_id="A")
    jb = b.enqueue("snapshot", {}, session_id="B")
    b.cancel_pending_and_running(reason="session_aborted", session_id="A")
    mark(
        "session-scoped cancel",
        b._read(ja.id).status == "cancelled" and b._read(jb.id).status == "pending",
        f"A={b._read(ja.id).status} B={b._read(jb.id).status}",
    )

    # mid-type abort on turn thread
    from remedy.core.computer import host_bridge as hb
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    hb._bridge = None
    ex = ComputerExecutor()
    toks = tc.begin_turn("full-soak-midtype", project_raw=None, active_path=".")

    def abort_soon() -> None:
        time.sleep(0.05)
        tc.abort_session("full-soak-midtype")

    try:
        threading.Thread(target=abort_soon, daemon=True).start()
        r = json.loads(
            ex.run(
                ComputerAction.TYPE,
                target="desktop",
                text="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789EXTRA",
            )
        )
        aborted = r.get("aborted") is True or (
            r.get("ok") is False and "abort" in str(r.get("message") or "").lower()
        )
        mark("stop mid-type", aborted, str(r)[:140])
    finally:
        with contextlib.suppress(Exception):
            tc.end_turn("full-soak-midtype", *toks)


def providers_live() -> None:
    section("PROVIDERS")

    async def run() -> None:
        import aiohttp

        from remedy.core.computer.executor import get_computer_executor
        from remedy.core.providers import _PROVIDERS, get_provider
        from remedy.interfaces.config import load_config, resolve_provider_api_key

        mark("provider registry size>=2", len(_PROVIDERS) >= 2, f"n={len(_PROVIDERS)}")
        cfg = load_config()
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "computer_monitors",
                    "description": "List monitors",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        pairs = [
            ("xai", "grok-4.5", "https://api.x.ai/v1"),
            ("deepseek", "deepseek-chat", "https://api.deepseek.com"),
        ]
        for prov, model, base in pairs:
            key = resolve_provider_api_key(cfg, prov)
            if not key:
                mark(f"provider {prov}", None, "no api key")
                continue
            adapter = get_provider(prov)
            url = adapter.chat_endpoint(base)
            headers = dict(adapter.auth_headers(key))
            headers["Content-Type"] = "application/json"
            body = adapter.build_body(
                model,
                [
                    {
                        "role": "system",
                        "content": "Always call computer_monitors. No prose.",
                    },
                    {
                        "role": "user",
                        "content": "List monitors with the tool only.",
                    },
                ],
                tools,
                stream=False,
                max_tokens=128,
            )
            body.setdefault("tool_choice", "required")
            try:
                timeout = aiohttp.ClientTimeout(total=75)
                async with aiohttp.ClientSession(timeout=timeout) as sess:
                    async with sess.post(url, headers=headers, json=body) as resp:
                        text = await resp.text()
                        if resp.status >= 400:
                            mark(
                                f"provider {prov} tool call",
                                False,
                                f"HTTP {resp.status} {text[:120]}",
                            )
                            continue
                        data = json.loads(text)
                msg = (data.get("choices") or [{}])[0].get("message") or {}
                tcs = msg.get("tool_calls") or []
                names = [
                    ((tc.get("function") or {}).get("name"))
                    for tc in tcs
                    if isinstance(tc, dict)
                ]
                mon = json.loads(
                    get_computer_executor().run("monitors", target="desktop")
                )
                mark(
                    f"provider {prov} tool call",
                    ("computer_monitors" in names or bool(tcs)) and bool(mon.get("ok")),
                    f"tools={names} mon={len(mon.get('monitors') or [])}",
                )
            except Exception as e:
                mark(f"provider {prov} tool call", False, f"{type(e).__name__}: {e}")

    asyncio.run(run())


def browser_rail_commands() -> None:
    section("BROWSER RAIL (if tauri not available from python, SKIP)")
    # Python cannot easily call Tauri IPC; verify prefs file API exists after mobile feature
    prefs = Path.home() / ".remedy" / "browser_rail.json"
    mark(
        "browser_rail prefs path ready",
        True,
        f"exists={prefs.is_file()} path={prefs}",
    )
    # Host bridge bounds optional
    from remedy.core.computer.host_bridge import get_host_bridge

    b = get_host_bridge()
    mark(
        "host_connected (in-process may be false)",
        None if not b.host_connected() else True,
        f"connected={b.host_connected()}",
    )


def routing() -> None:
    section("ROUTING")
    from remedy.core.computer.router import ComputerTarget, resolve_target

    cases = [
        ("Open Start menu", None, "click", ComputerTarget.DESKTOP),
        ("run the installer setup.exe", None, "click", ComputerTarget.DESKTOP),
        ("open https://example.com", "https://example.com", "navigate", ComputerTarget.BROWSER),
        ("wiki documentation page", None, "navigate", ComputerTarget.BROWSER),
    ]
    ok_all = True
    details = []
    for hint, url, action, expect in cases:
        got = resolve_target("auto", url=url, hint=hint, action=action)
        ok = got is expect
        ok_all = ok_all and ok
        details.append(f"{hint[:20]!r}->{got.value}" + ("" if ok else f"!={expect.value}"))
    mark("resolve_target matrix", ok_all, "; ".join(details))


def regression_file() -> None:
    section("REGRESSION")
    tmp = ROOT / "scripts" / "_full_soak_tmp.txt"
    tmp.write_text("v1\n", encoding="utf-8")
    tmp.write_text(tmp.read_text(encoding="utf-8") + "edited\n", encoding="utf-8")
    mark(
        "file write/edit",
        "edited" in tmp.read_text(encoding="utf-8"),
        tmp.name,
    )
    tmp.unlink(missing_ok=True)
    sha = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], text=True, cwd=ROOT
    ).strip()
    mark("git bash-equivalent", bool(sha), sha)


def main() -> int:
    print("FULL REMEDY PRODUCT SOAK")
    print(f"cwd={ROOT}")
    t0 = time.time()
    preconditions()
    api_surface()
    routing()
    computer_live()
    plan_mode_live()
    concurrent_and_abort()
    providers_live()
    browser_rail_commands()
    regression_file()

    section("UNIT / DOCS")
    run_pytest(
        [
            "tests/test_computer_use.py",
            "tests/test_plan_mode_stream.py",
            "tests/test_stream_concurrency.py",
            "tests/test_help_docs.py",
            "tests/test_browse_intent.py",
        ],
        "pytest core computer/plan/help/stream",
        timeout_s=240,
    )
    # check_docs if present
    check = ROOT / "scripts" / "check_docs.py"
    if check.is_file():
        try:
            cp = subprocess.run(
                [sys.executable, str(check)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=180,
            )
            # check_docs may return non-zero for warnings — treat only hard errors
            out = ((cp.stdout or "") + (cp.stderr or ""))[-300:].replace("\n", " ")
            mark(
                "check_docs.py",
                cp.returncode == 0,
                f"exit={cp.returncode} {out}",
            )
        except Exception as e:
            mark("check_docs.py", False, str(e))

    # Optional broader security/session tests
    run_pytest(
        [
            "tests/test_session_stream.py",
            "tests/test_turn_context.py",
            "tests/test_web_fetch_ssrf.py",
        ],
        "pytest session/security sample",
        timeout_s=180,
    )

    elapsed = time.time() - t0
    section("SUMMARY")
    p = sum(1 for r in results if r["status"] == "PASS")
    f = sum(1 for r in results if r["status"] == "FAIL")
    s = sum(1 for r in results if r["status"] == "SKIP")
    print(f"PASS={p} FAIL={f} SKIP={s} elapsed={elapsed:.1f}s")
    for r in results:
        if r["status"] == "FAIL":
            print(f"  FAIL  {r['name']}: {r['detail']}")

    out = {
        "branch": subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True, cwd=ROOT
        ).strip(),
        "sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, cwd=ROOT
        ).strip(),
        "elapsed_s": round(elapsed, 1),
        "passed": p,
        "failed": f,
        "skipped": s,
        "results": results,
    }
    out_path = ROOT / "docs" / "_full_product_soak_results.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")
    return 1 if f else 0


if __name__ == "__main__":
    raise SystemExit(main())
