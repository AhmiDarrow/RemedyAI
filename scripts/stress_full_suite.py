#!/usr/bin/env python3
"""Full Remedy desktop/API bug sweep + stress battery.

- Phase A: one-shot bug sweep (status codes + invariants)
- Phase B: N passes (default 50). Focus provider rotates every 10 passes:
    0-9 demo, 10-19 deepseek, 20-29 xai, 30-39 poe, 40-49 demo, ...

Covers: auth, settings, providers/models, sessions LLM pin, partner, nanoswarm,
vision, commands, parallel storms, multi-provider races, soft chat (demo only).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

HOME = Path(os.environ.get("REMEDY_HOME", Path.home() / ".remedy"))
BASE = os.environ.get("REMEDY_API", "http://127.0.0.1:7400")
PASSES = int(os.environ.get("REMEDY_STRESS_PASSES", "50"))
ROTATE_EVERY = int(os.environ.get("REMEDY_STRESS_ROTATE_EVERY", "10"))
TOKEN_PATH = HOME / "auth" / "local_api_token"

# Rotation order for focus provider (every ROTATE_EVERY passes)
PROVIDER_ROTATION = ["demo", "deepseek", "xai", "poe"]

FOCUS_MODELS = {
    "demo": ["codestral-latest", "gemini-3.1-flash-lite", "gpt-oss:20b"],
    "deepseek": ["deepseek-v4-flash", "deepseek-v4-pro"],
    "xai": ["grok-4.5", "grok-4.3", "grok-4"],
    "poe": ["Claude-Sonnet-4.6", "GPT-5.4", "Grok-4"],
}


class Fail(Exception):
    pass


def load_token() -> str:
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from lib_local_token import resolve_local_api_token

    try:
        return resolve_local_api_token(home=HOME, base=BASE)
    except Exception as exc:
        raise Fail(f"token resolve failed ({TOKEN_PATH}): {exc}") from exc


def req(
    method: str,
    path: str,
    token: str,
    body: dict | None = None,
    timeout: float = 30.0,
) -> tuple[int, Any, float]:
    url = BASE.rstrip("/") + "/api" + path
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            ms = (time.perf_counter() - t0) * 1000
            try:
                return resp.status, json.loads(raw) if raw else None, ms
            except json.JSONDecodeError:
                return resp.status, raw, ms
    except urllib.error.HTTPError as e:
        ms = (time.perf_counter() - t0) * 1000
        raw = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = raw
        return e.code, payload, ms
    except Exception as e:
        ms = (time.perf_counter() - t0) * 1000
        return 0, {"error": str(e)}, ms


def must_ok(code: int, path: str, payload: Any, allow: set[int] | None = None) -> Any:
    ok = allow or set(range(200, 300))
    if code not in ok:
        raise Fail(f"{path} -> HTTP {code}: {payload!r}"[:450])
    return payload


def focus_provider(pass_idx: int) -> str:
    """pass_idx is 0-based."""
    return PROVIDER_ROTATION[(pass_idx // ROTATE_EVERY) % len(PROVIDER_ROTATION)]


def pick_model(token: str, provider: str) -> str:
    code, body, _ = req("GET", f"/models?provider={provider}", token, timeout=25)
    must_ok(code, f"/models?provider={provider}", body)
    mods = body.get("models") or []
    if not mods:
        raise Fail(f"no models for {provider}")
    preferred = FOCUS_MODELS.get(provider) or []
    ids = [m.get("id") for m in mods if m.get("id")]
    for p in preferred:
        if p in ids:
            return p
    return str(ids[0])


def ensure_session(token: str) -> str:
    code, body, _ = req(
        "POST",
        "/sessions",
        token,
        {"title": f"full-stress-{int(time.time())}"},
        timeout=20,
    )
    if code in (200, 201) and isinstance(body, dict) and body.get("id"):
        return str(body["id"])
    code, sessions, _ = req("GET", "/sessions", token, timeout=15)
    must_ok(code, "/sessions", sessions)
    sess_list = sessions.get("sessions") if isinstance(sessions, dict) else sessions
    if not sess_list:
        raise Fail("no sessions")
    return str(sess_list[0]["id"])


# ── Phase A: bug sweep ─────────────────────────────────────────────────────


def bug_sweep(token: str) -> dict[str, Any]:
    """One-shot invariant + endpoint sweep."""
    findings: list[str] = []
    soft: list[str] = []

    # Expected OK endpoints (desktop-critical)
    must_200 = [
        "/ping",
        "/status",
        "/settings",
        "/providers",
        "/providers/connected",
        "/providers/free",
        "/providers/ollama/detect",
        "/models",
        "/sessions",
        "/agents",
        "/commands",
        "/commands/custom",
        "/vision/status",
        "/usage/summary",
        "/partner/status",
        "/plans/latest",
        "/checkpoints/latest",
        "/approvals",
        "/goals",
        "/plans",
        "/nanoswarm/status",
        "/nanoswarm/token/families",
        "/auth/xai",
        "/assistant/status",
        "/openapi.json",
    ]
    codes: dict[str, int] = {}
    for path in must_200:
        code, body, ms = req("GET", path, token, timeout=25)
        codes[path] = code
        if code < 200 or code >= 300:
            # soft allow some optional services
            if path in (
                "/assistant/status",
                "/auth/xai",
                "/nanoswarm/status",
                "/vision/status",
            ) and code in (404, 501, 503):
                soft.append(f"{path}->{code}")
            else:
                findings.append(f"MUST_200 {path}->{code} {body!r}"[:200])

    # 404 is OK for bare /usage (desktop uses /usage/summary)
    code, _, _ = req("GET", "/usage", token, timeout=10)
    if code not in (200, 404):
        findings.append(f"/usage unexpected {code}")

    # Connected picker invariants
    code, conn, _ = req("GET", "/providers/connected", token, timeout=25)
    must_ok(code, "/providers/connected", conn)
    picker = conn.get("picker") or []
    picker_ids = [p.get("id") for p in picker]
    if "demo" not in picker_ids:
        findings.append(f"demo not picker-eligible: {picker_ids}")
    for p in picker:
        if not p.get("connected"):
            findings.append(f"picker entry not connected: {p.get('id')}")
        if not p.get("models"):
            soft.append(f"picker {p.get('id')} has empty models list (live may fill)")

    # Live models invariants
    for pid in ("demo", "deepseek", "xai", "poe"):
        code, body, _ = req("GET", f"/models?provider={pid}", token, timeout=25)
        if code != 200:
            findings.append(f"models {pid}->{code}")
            continue
        mods = body.get("models") or []
        if not mods:
            findings.append(f"empty models {pid}")
        if body.get("provider") != pid:
            findings.append(f"provider field mismatch {pid} vs {body.get('provider')}")
        # every model tagged with this provider
        for m in mods:
            if m.get("provider") and m.get("provider") != pid:
                findings.append(f"cross-tag {pid}: {m.get('id')} provider={m.get('provider')}")
                break
        if pid == "demo":
            for m in mods:
                if m.get("source") == "endpoint":
                    findings.append(f"demo leaked endpoint model {m.get('id')}")
                    break
            # curated set should include codestral
            ids = {m.get("id") for m in mods}
            if "codestral-latest" not in ids:
                findings.append(f"demo missing codestral: {ids}")
        if pid in ("deepseek", "xai") and mods:
            # prefer live endpoint source when key present
            sources = {m.get("source") for m in mods}
            if "endpoint" not in sources and "catalog" not in sources:
                soft.append(f"{pid} models have no source tags")

    # Cross-wire: set xai/grok then read settings — must stick
    sid = ensure_session(token)
    code, body, _ = req(
        "PUT",
        f"/sessions/{sid}/llm",
        token,
        {"provider": "xai", "model": "grok-4.5", "make_default": True},
        timeout=25,
    )
    must_ok(code, "pin xai", body)
    if str(body.get("provider")).lower() != "xai":
        findings.append(f"set llm returned provider {body.get('provider')}")
    code, settings, _ = req("GET", "/settings", token, timeout=15)
    must_ok(code, "/settings", settings)
    if str(settings.get("llm_provider")).lower() != "xai":
        findings.append(f"global after pin not xai: {settings.get('llm_provider')}")
    if str(settings.get("llm_model")) != "grok-4.5":
        findings.append(f"global model after pin: {settings.get('llm_model')}")

    # Demo models must not appear under deepseek list
    code, ds, _ = req("GET", "/models?provider=deepseek", token, timeout=20)
    must_ok(code, "deepseek models", ds)
    for m in ds.get("models") or []:
        mid = str(m.get("id") or "")
        if mid in ("codestral-latest", "gpt-oss:20b", "gemini-3.1-flash-lite"):
            findings.append(f"demo id leaked into deepseek list: {mid}")

    # Invalid model should 400 for closed provider
    code, body, _ = req(
        "PUT",
        f"/sessions/{sid}/llm",
        token,
        {"provider": "deepseek", "model": "not-a-real-model-zzz", "make_default": False},
        timeout=15,
    )
    if code not in (400, 422):
        findings.append(f"garbage model should 400, got {code}")

    if findings:
        raise Fail("bug_sweep: " + " | ".join(findings[:12]))

    return {
        "endpoints_checked": len(must_200),
        "picker": picker_ids,
        "soft": soft[:10],
        "session": sid,
    }


# ── Phase B: per-pass stress ───────────────────────────────────────────────


def pass_health(token: str) -> dict[str, float]:
    times = {}
    for path in (
        "/ping",
        "/status",
        "/settings",
        "/agents",
        "/commands",
        "/partner/status",
        "/usage/summary",
    ):
        code, body, ms = req("GET", path, token, timeout=20)
        must_ok(code, path, body)
        times[path] = round(ms, 1)
    return times


def pass_catalog_all(token: str, focus: str) -> dict[str, Any]:
    code, conn, ms_c = req("GET", "/providers/connected", token, timeout=25)
    must_ok(code, "/providers/connected", conn)
    picker = [p.get("id") for p in (conn.get("picker") or [])]
    if focus not in picker and focus != "openai":
        # focus must be switchable
        if focus == "demo" or focus in picker:
            pass
        else:
            raise Fail(f"focus {focus} not in picker {picker}")
    meta = {}
    for pid in picker or PROVIDER_ROTATION:
        code, body, ms = req("GET", f"/models?provider={pid}", token, timeout=25)
        must_ok(code, f"/models?provider={pid}", body)
        mods = body.get("models") or []
        if not mods:
            raise Fail(f"empty models {pid}")
        # no demo allowlist ids on non-demo
        if pid != "demo":
            for m in mods:
                mid = str(m.get("id") or "")
                if mid in ("codestral-latest", "gpt-oss:20b") and "(demo)" in str(
                    m.get("name") or ""
                ):
                    raise Fail(f"demo leak on {pid}: {mid}")
        meta[pid] = {"n": len(mods), "ms": round(ms, 1)}
    return {"picker": picker, "connected_ms": round(ms_c, 1), "models": meta}


def pass_focus_pin(token: str, session_id: str, focus: str) -> dict[str, Any]:
    """Switch through a few models on focus provider, settle, poll for drift."""
    model = pick_model(token, focus)
    # cycle 3 models if available
    code, body, _ = req("GET", f"/models?provider={focus}", token, timeout=25)
    must_ok(code, f"/models?provider={focus}", body)
    ids = [m["id"] for m in (body.get("models") or []) if m.get("id")][:5]
    if not ids:
        raise Fail(f"no models for focus {focus}")
    flips = []
    for mid in ids[:3]:
        code, resp, ms = req(
            "PUT",
            f"/sessions/{session_id}/llm",
            token,
            {"provider": focus, "model": mid, "make_default": True},
            timeout=25,
        )
        must_ok(code, f"pin {focus}/{mid}", resp)
        got_p = str(resp.get("provider") or "").lower()
        got_m = str(resp.get("model") or "")
        if got_p != focus:
            raise Fail(f"pin snapped provider {focus}->{got_p} for model {mid}")
        flips.append({"model": mid, "got": got_m, "ms": round(ms, 1)})
        model = got_m or mid

    # settle poll
    drifts = []
    for i in range(8):
        code_s, settings, _ = req("GET", "/settings", token, timeout=15)
        must_ok(code_s, "/settings", settings)
        code_c, conn, _ = req("GET", "/providers/connected", token, timeout=20)
        must_ok(code_c, "/providers/connected", conn)
        code_l, sessions, _ = req("GET", "/sessions", token, timeout=15)
        must_ok(code_l, "/sessions", sessions)
        sp = str(settings.get("llm_provider") or "").lower()
        sm = str(settings.get("llm_model") or "")
        if sp != focus:
            drifts.append(f"settings@{i}={sp}/{sm}")
        if sm != model and focus != "poe":
            # poe may normalize bot names slightly — only require provider stick
            drifts.append(f"model@{i} want={model} got={sm}")
        sess_list = (
            sessions.get("sessions")
            if isinstance(sessions, dict)
            else sessions
            if isinstance(sessions, list)
            else []
        )
        me = next((s for s in sess_list if s.get("id") == session_id), None)
        if me:
            ssp = str(me.get("llm_provider") or "").lower()
            if ssp and ssp != focus:
                drifts.append(f"session@{i}={ssp}")
        time.sleep(0.12)

    if drifts:
        raise Fail(f"focus pin drift ({focus}): {drifts[:6]}")
    return {"focus": focus, "model": model, "flips": len(flips), "polls_ok": 8}


def pass_multi_provider_hop(token: str, session_id: str, focus: str) -> dict[str, Any]:
    """Hop away and back to focus — classic thrash pattern."""
    others = [p for p in PROVIDER_ROTATION if p != focus]
    sequence = [focus] + others[:2] + [focus]
    last = {}
    for prov in sequence:
        mid = pick_model(token, prov)
        code, body, ms = req(
            "PUT",
            f"/sessions/{session_id}/llm",
            token,
            {"provider": prov, "model": mid, "make_default": True},
            timeout=25,
        )
        must_ok(code, f"hop {prov}/{mid}", body)
        if str(body.get("provider")).lower() != prov:
            raise Fail(f"hop provider snap {prov}->{body.get('provider')}")
        last = {"provider": prov, "model": body.get("model") or mid, "ms": round(ms, 1)}
    # final must be focus
    if last.get("provider") != focus:
        raise Fail(f"hop did not end on focus {focus}: {last}")
    code, settings, _ = req("GET", "/settings", token, timeout=15)
    must_ok(code, "/settings", settings)
    if str(settings.get("llm_provider")).lower() != focus:
        raise Fail(f"after hop global not {focus}: {settings.get('llm_provider')}")
    return {"sequence": sequence, "final": last}


def pass_parallel_storm(token: str, focus: str) -> dict[str, Any]:
    paths = [
        ("GET", "/ping"),
        ("GET", "/settings"),
        ("GET", "/providers/connected"),
        ("GET", "/models"),
        ("GET", f"/models?provider={focus}"),
        ("GET", "/models?provider=demo"),
        ("GET", "/models?provider=deepseek"),
        ("GET", "/models?provider=xai"),
        ("GET", "/models?provider=poe"),
        ("GET", "/sessions"),
        ("GET", "/agents"),
        ("GET", "/commands"),
        ("GET", "/partner/status"),
        ("GET", "/usage/summary"),
        ("GET", "/vision/status"),
        ("GET", "/nanoswarm/status"),
        ("GET", "/plans/latest"),
        ("GET", "/checkpoints/latest"),
    ]
    jobs = paths * 2
    errors = []
    latencies = []

    def one(item: tuple[str, str]):
        method, path = item
        return path, req(method, path, token, timeout=30)

    with ThreadPoolExecutor(max_workers=18) as pool:
        futs = [pool.submit(one, j) for j in jobs]
        for f in as_completed(futs):
            path, (code, body, ms) = f.result()
            latencies.append(ms)
            if code < 200 or code >= 300:
                if any(
                    path.startswith(p)
                    for p in ("/vision", "/nanoswarm", "/assistant", "/auth")
                ) and code in (404, 501, 503):
                    continue
                errors.append(f"{path}->{code}")
    if errors:
        raise Fail(f"storm errors: {errors[:12]}")
    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95) - 1] if latencies else 0
    return {
        "n": len(latencies),
        "p50_ms": round(latencies[len(latencies) // 2], 1),
        "p95_ms": round(p95, 1),
        "max_ms": round(max(latencies), 1),
    }


def pass_race(token: str, session_id: str, focus: str) -> dict[str, Any]:
    """Parallel settings vs session writes; settle on focus."""
    mid = pick_model(token, focus)
    alt = "demo" if focus != "demo" else "xai"
    alt_mid = pick_model(token, alt)

    def write_settings_alt():
        return req(
            "PUT",
            "/settings",
            token,
            {"llm_provider": alt, "llm_model": alt_mid},
            timeout=25,
        )

    def write_session_focus():
        return req(
            "PUT",
            f"/sessions/{session_id}/llm",
            token,
            {"provider": focus, "model": mid, "make_default": True},
            timeout=25,
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(write_settings_alt) for _ in range(3)] + [
            pool.submit(write_session_focus) for _ in range(3)
        ]
        for f in as_completed(futs):
            code, body, _ = f.result()
            if code < 200 or code >= 300:
                raise Fail(f"race write {code}: {body!r}"[:250])

    # desktop-style: last intentional pick wins
    code, body, _ = req(
        "PUT",
        f"/sessions/{session_id}/llm",
        token,
        {"provider": focus, "model": mid, "make_default": True},
        timeout=25,
    )
    must_ok(code, "race settle", body)
    time.sleep(0.15)
    code, settings, _ = req("GET", "/settings", token, timeout=15)
    must_ok(code, "/settings", settings)
    if str(settings.get("llm_provider")).lower() != focus:
        raise Fail(f"race settle not {focus}: {settings.get('llm_provider')}")
    return {"settled": f"{focus}/{settings.get('llm_model')}", "vs": alt}


def pass_session_messages(token: str, session_id: str) -> dict[str, Any]:
    code, body, ms = req(
        "GET", f"/sessions/{session_id}/messages?limit=20", token, timeout=20
    )
    must_ok(code, "messages", body)
    msgs = body.get("messages") if isinstance(body, dict) else body
    return {"count": len(msgs or []), "ms": round(ms, 1)}


def pass_soft_chat_demo(token: str, session_id: str, focus: str) -> dict[str, Any]:
    """Only burn a real completion when focus is demo (cheap guest path)."""
    if focus != "demo":
        return {"skipped": True, "reason": f"focus={focus}"}
    # ensure demo
    code, _, _ = req(
        "PUT",
        f"/sessions/{session_id}/llm",
        token,
        {"provider": "demo", "model": "codestral-latest", "make_default": True},
        timeout=25,
    )
    must_ok(code, "demo pin for chat", _)
    code, body, ms = req(
        "POST",
        "/chat",
        token,
        {
            "message": "Reply with exactly: pong",
            "session_id": session_id,
        },
        timeout=90,
    )
    # allow 200 or structured errors under rate limit
    if code == 200:
        text = ""
        if isinstance(body, dict):
            text = str(body.get("response") or body.get("content") or body.get("message") or "")
        return {"ok": True, "ms": round(ms, 1), "chars": len(text)}
    if code in (429, 502, 503, 504):
        return {"ok": True, "soft_fail": code, "ms": round(ms, 1)}
    raise Fail(f"chat demo failed {code}: {body!r}"[:300])


def pass_partner_surface(token: str) -> dict[str, Any]:
    out = {}
    for path in ("/partner/status", "/goals", "/approvals", "/plans", "/plans/latest"):
        code, body, ms = req("GET", path, token, timeout=20)
        must_ok(code, path, body)
        out[path] = round(ms, 1)
    return out


def main() -> int:
    print(f"Remedy FULL stress: base={BASE} passes={PASSES} rotate_every={ROTATE_EVERY}")
    print(f"rotation={PROVIDER_ROTATION}")
    token = load_token()
    for _ in range(40):
        code, _, _ = req("GET", "/ping", token, timeout=3)
        if code == 200:
            break
        time.sleep(0.25)
    else:
        print("FAIL: server not up")
        return 2

    print("=== PHASE A: bug sweep ===")
    try:
        sweep = bug_sweep(token)
        print(
            f"BUG SWEEP OK  endpoints={sweep['endpoints_checked']}  "
            f"picker={sweep['picker']}  soft={len(sweep.get('soft') or [])}"
        )
        if sweep.get("soft"):
            for s in sweep["soft"][:5]:
                print(f"  soft: {s}")
        session_id = sweep["session"]
    except Exception as e:
        print(f"BUG SWEEP FAIL: {e}")
        return 1

    print("=== PHASE B: stress passes ===")
    failures: list[str] = []
    summary: list[dict[str, Any]] = []

    for n in range(1, PASSES + 1):
        focus = focus_provider(n - 1)
        t0 = time.perf_counter()
        row: dict[str, Any] = {"pass": n, "focus": focus}
        try:
            row["health"] = pass_health(token)
            row["catalog"] = pass_catalog_all(token, focus)
            row["partner"] = pass_partner_surface(token)
            row["pin"] = pass_focus_pin(token, session_id, focus)
            row["hop"] = pass_multi_provider_hop(token, session_id, focus)
            row["storm"] = pass_parallel_storm(token, focus)
            row["race"] = pass_race(token, session_id, focus)
            row["messages"] = pass_session_messages(token, session_id)
            row["chat"] = pass_soft_chat_demo(token, session_id, focus)
            row["ok"] = True
            row["sec"] = round(time.perf_counter() - t0, 2)
            print(
                f"PASS {n:02d}/{PASSES} ok  focus={focus:8s}  "
                f"{row['sec']}s  storm_p95={row['storm']['p95_ms']}ms  "
                f"pin={row['pin']['model']}"
            )
        except Exception as e:
            row["ok"] = False
            row["error"] = str(e)[:500]
            row["sec"] = round(time.perf_counter() - t0, 2)
            failures.append(f"pass {n} focus={focus}: {e}")
            print(f"FAIL {n:02d}/{PASSES} focus={focus}  {e}")
        summary.append(row)

    ok_n = sum(1 for r in summary if r.get("ok"))
    print("---")
    print(f"Result: bug_sweep=OK  stress={ok_n}/{PASSES}")
    # per-focus tally
    by_focus: dict[str, list[bool]] = {}
    for r in summary:
        by_focus.setdefault(r.get("focus") or "?", []).append(bool(r.get("ok")))
    for f, arr in by_focus.items():
        print(f"  focus {f}: {sum(arr)}/{len(arr)} ok")

    out = HOME / "logs" / "stress_full_suite_last.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"sweep": sweep, "passes": summary, "failures": failures}, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {out}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
