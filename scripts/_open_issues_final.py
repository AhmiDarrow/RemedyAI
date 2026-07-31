"""Final open-issue smokes for computer-use soak. Local only."""
from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

results: list[tuple[str, str, str]] = []


def mark(name: str, ok: bool, detail: str = "") -> None:
    st = "PASS" if ok else "FAIL"
    results.append((name, st, detail))
    print(f"[{st}] {name} — {detail}")


def routing_smoke() -> None:
    from remedy.core.computer.router import ComputerTarget, resolve_target

    cases = [
        ("Open Start menu", None, "click", ComputerTarget.DESKTOP),
        ("run the installer setup.exe", None, "click", ComputerTarget.DESKTOP),
        ("open https://example.com", "https://example.com", "navigate", ComputerTarget.BROWSER),
        ("gmail wiki page", None, "navigate", ComputerTarget.BROWSER),
    ]
    all_ok = True
    details: list[str] = []
    for hint, url, action, expect in cases:
        got = resolve_target("auto", url=url, hint=hint, action=action)
        ok = got is expect
        all_ok = all_ok and ok
        details.append(
            f"{hint[:28]!r}->{got.value}" + ("" if ok else f" want {expect.value}")
        )
    mark("desktop routing start menu/installer", all_ok, "; ".join(details))


def concurrent_live() -> None:
    from remedy.core import turn_context as tc
    from remedy.core.computer import host_bridge as hbm
    from remedy.core.computer.host_bridge import ComputerHostBridge

    td = tempfile.mkdtemp(prefix="soak-conc-")
    b = ComputerHostBridge(home_dir=td)
    ja = b.enqueue("navigate", {"url": "https://a.test"}, session_id="live-A")
    jb = b.enqueue("snapshot", {}, session_id="live-B")
    old = hbm.get_host_bridge
    hbm.get_host_bridge = lambda home_dir=None: b  # type: ignore[assignment]
    try:
        tc.abort_session("live-A")
        mark(
            "live concurrent abort isolation",
            b._read(ja.id).status == "cancelled"
            and b._read(jb.id).status == "pending",
            f"A={b._read(ja.id).status} B={b._read(jb.id).status}",
        )
    finally:
        hbm.get_host_bridge = old  # type: ignore[assignment]


def mid_type_live() -> None:
    """Stop mid-type on the *turn thread* (ContextVar abort); abort fires from side thread."""
    from remedy.core import turn_context as tc
    from remedy.core.computer import desktop_win as win
    from remedy.core.computer import host_bridge as hb
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    subprocess.Popen(["notepad.exe"], shell=False)
    time.sleep(1.0)
    try:
        els = win.desktop_snapshot(limit=20, mode="windows")
        np = next(
            (
                e
                for e in (els or [])
                if "notepad" in (e.get("title") or e.get("name") or "").lower()
            ),
            None,
        )
        if np:
            ex0 = ComputerExecutor()
            ex0.run(ComputerAction.CLICK, target="desktop", ref=np["ref"])
            time.sleep(0.25)
    except Exception:
        pass

    hb._bridge = None
    ex = ComputerExecutor()
    toks = tc.begin_turn("soak-midtype", project_raw=None, active_path=".")

    def abort_soon() -> None:
        time.sleep(0.06)
        tc.abort_session("soak-midtype")

    try:
        threading.Thread(target=abort_soon, daemon=True).start()
        # Type on turn thread so is_turn_aborted() sees the Event (product path).
        r = json.loads(
            ex.run(
                ComputerAction.TYPE,
                target="desktop",
                text="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789EXTRA_LONG",
            )
        )
        aborted = r.get("aborted") is True or (
            r.get("ok") is False and "abort" in str(r.get("message") or "").lower()
        )
        typed = int(r.get("typed") or r.get("length") or 0)
        mark(
            "live mid-type abort",
            aborted and typed < 40,
            str(r)[:160],
        )
    finally:
        with contextlib.suppress(Exception):
            tc.end_turn("soak-midtype", *toks)


async def provider_tool_smoke() -> None:
    import aiohttp

    from remedy.core.computer import host_bridge as hb
    from remedy.core.computer.executor import get_computer_executor
    from remedy.core.providers import get_provider
    from remedy.interfaces.config import load_config, resolve_provider_api_key

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
            mark(f"provider {prov} tool smoke", False, "no api key")
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
                    "content": "List my monitors using the computer_monitors tool only.",
                },
            ],
            tools,
            stream=False,
            max_tokens=256,
        )
        body.setdefault("tool_choice", "required")
        try:
            timeout = aiohttp.ClientTimeout(total=75)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.post(url, headers=headers, json=body) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        mark(
                            f"provider {prov} tool smoke",
                            False,
                            f"HTTP {resp.status} {text[:180]}",
                        )
                        continue
                    data = json.loads(text)
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            tcs = msg.get("tool_calls") or []
            names = [
                ((tc.get("function") or {}).get("name")) for tc in tcs if isinstance(tc, dict)
            ]
            tool_ok = "computer_monitors" in names or bool(tcs)
            hb._bridge = None
            mon = json.loads(get_computer_executor().run("monitors", target="desktop"))
            mon_ok = bool(mon.get("ok")) and len(mon.get("monitors") or []) >= 1
            mark(
                f"provider {prov} tool smoke",
                tool_ok and mon_ok,
                f"tool_calls={names} mon_ok={mon_ok} n={len(mon.get('monitors') or [])}",
            )
        except Exception as e:
            mark(f"provider {prov} tool smoke", False, f"{type(e).__name__}: {e}")


async def main() -> int:
    print("=== OPEN ISSUES FINAL WAVE ===")
    routing_smoke()
    concurrent_live()
    mid_type_live()
    await provider_tool_smoke()
    print()
    p = sum(1 for _, s, _ in results if s == "PASS")
    f = sum(1 for _, s, _ in results if s == "FAIL")
    print(f"SUMMARY PASS={p} FAIL={f}")
    out = ROOT / "docs" / "_open_issues_final.json"
    out.write_text(
        json.dumps(
            [{"name": n, "status": s, "detail": d} for n, s, d in results],
            indent=2,
        ),
        encoding="utf-8",
    )
    print("wrote", out)
    return 1 if f else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
