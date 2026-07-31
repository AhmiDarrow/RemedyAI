#!/usr/bin/env python3
"""Stress-test Remedy local API the way Desktop uses it (20 passes).

Covers: auth, settings thrash, provider switch races, live /models discovery,
connected picker, sessions LLM pin consistency, parallel storms, ping health.

Exit 0 only if all passes and critical checks succeed.
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
from pathlib import Path as _PathForToken

_SCRIPTS = _PathForToken(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from typing import Any

from lib_local_token import resolve_local_api_token

HOME = Path(os.environ.get("REMEDY_HOME", Path.home() / ".remedy"))
BASE = os.environ.get("REMEDY_API", "http://127.0.0.1:7400")
PASSES = int(os.environ.get("REMEDY_STRESS_PASSES", "20"))
TOKEN_PATH = HOME / "auth" / "local_api_token"

# Desktop-like provider flip sequence (the thrash scenario)
FLIP_SEQ = [
    ("demo", "codestral-latest"),
    ("xai", "grok-4.5"),
    ("deepseek", "deepseek-v4-flash"),
    ("demo", "gpt-oss:20b"),
    ("xai", "grok-4.5"),
    ("poe", "Claude-Sonnet-4.6"),
    ("deepseek", "deepseek-v4-pro"),
    ("demo", "gemini-3.1-flash-lite"),
    ("xai", "grok-4.5"),
]


class Fail(Exception):
    pass


def load_token() -> str:
    try:
        return resolve_local_api_token(home=HOME, base=BASE)
    except Exception as e:
        raise Fail(f"token resolve failed ({TOKEN_PATH}): {e}") from e


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


def must_ok(code: int, path: str, payload: Any) -> Any:
    if code < 200 or code >= 300:
        raise Fail(f"{path} -> HTTP {code}: {payload!r}"[:400])
    return payload


def pass_health(token: str) -> dict[str, float]:
    times: dict[str, float] = {}
    for path in ("/ping", "/status", "/settings", "/agents", "/commands"):
        code, body, ms = req("GET", path, token, timeout=15)
        must_ok(code, path, body)
        times[path] = ms
    return times


def pass_connected_and_models(token: str) -> dict[str, Any]:
    code, conn, ms_c = req("GET", "/providers/connected", token, timeout=25)
    must_ok(code, "/providers/connected", conn)
    picker = conn.get("picker") or []
    ids = {p.get("id") for p in picker}
    if "demo" not in ids:
        raise Fail(f"demo missing from picker: {sorted(ids)}")
    # live models for every picker provider
    models_meta = {}
    for pid in sorted(ids):
        code, body, ms = req("GET", f"/models?provider={pid}", token, timeout=20)
        must_ok(code, f"/models?provider={pid}", body)
        mods = body.get("models") or []
        if not mods:
            raise Fail(f"empty models for {pid}")
        if body.get("provider") and body["provider"] != pid:
            raise Fail(f"models provider mismatch want={pid} got={body.get('provider')}")
        sources = {}
        for m in mods:
            sources[m.get("source") or "?"] = sources.get(m.get("source") or "?", 0) + 1
        models_meta[pid] = {
            "count": len(mods),
            "ms": round(ms, 1),
            "sources": sources,
            "sample": [m.get("id") for m in mods[:5]],
        }
    # demo must stay catalog-only (no endpoint dump)
    demo_src = models_meta.get("demo", {}).get("sources") or {}
    if demo_src.get("endpoint", 0) > 0:
        raise Fail(f"demo leaked endpoint models: {demo_src}")
    return {"picker": sorted(ids), "connected_ms": round(ms_c, 1), "models": models_meta}


def pass_session_llm_pin(token: str, session_id: str) -> dict[str, Any]:
    """Simulate status-bar thrash: flip providers rapidly, assert settle stable."""
    flips = []
    for prov, model in FLIP_SEQ:
        code, body, ms = req(
            "PUT",
            f"/sessions/{session_id}/llm",
            token,
            {"provider": prov, "model": model, "make_default": True},
            timeout=25,
        )
        if code >= 400:
            # some models may 400 if not valid for provider — try first catalog model
            code2, mods, _ = req("GET", f"/models?provider={prov}", token, timeout=20)
            must_ok(code2, f"/models?provider={prov}", mods)
            fallback = (mods.get("models") or [{}])[0].get("id") or model
            code, body, ms = req(
                "PUT",
                f"/sessions/{session_id}/llm",
                token,
                {"provider": prov, "model": fallback, "make_default": True},
                timeout=25,
            )
            model = fallback
        must_ok(code, f"PUT session llm {prov}/{model}", body)
        got_p = (body.get("provider") or body.get("llm_provider") or prov)
        got_m = (body.get("model") or body.get("llm_model") or model)
        flips.append({"want": f"{prov}/{model}", "got": f"{got_p}/{got_m}", "ms": round(ms, 1)})

    # Final pin: xai + grok-4.5 (the reported thrash target)
    want_p, want_m = "xai", "grok-4.5"
    code, body, _ = req(
        "PUT",
        f"/sessions/{session_id}/llm",
        token,
        {"provider": want_p, "model": want_m, "make_default": True},
        timeout=25,
    )
    must_ok(code, "final pin xai", body)

    # Poll like desktop: settings + connected + session list should stay on pin
    drifts = []
    for i in range(12):
        code_s, settings, _ = req("GET", "/settings", token, timeout=15)
        must_ok(code_s, "/settings", settings)
        code_c, conn, _ = req("GET", "/providers/connected", token, timeout=20)
        must_ok(code_c, "/providers/connected", conn)
        code_l, sessions, _ = req("GET", "/sessions", token, timeout=15)
        must_ok(code_l, "/sessions", sessions)
        sp = str(settings.get("llm_provider") or "").lower()
        sm = str(settings.get("llm_model") or "")
        ap = str(conn.get("active_provider") or "").lower()
        am = str(conn.get("active_model") or "")
        # find session
        sess_list = sessions.get("sessions") or sessions if isinstance(sessions, dict) else []
        if isinstance(sessions, list):
            sess_list = sessions
        me = next((s for s in sess_list if s.get("id") == session_id), None)
        ssp = str((me or {}).get("llm_provider") or "").lower()
        ssm = str((me or {}).get("model") or "")
        if sp != want_p or sm != want_m:
            drifts.append(f"settings@{i}={sp}/{sm}")
        if ap and (ap != want_p or am != want_m):
            drifts.append(f"connected@{i}={ap}/{am}")
        if me and (ssp != want_p or ssm != want_m):
            drifts.append(f"session@{i}={ssp}/{ssm}")
        time.sleep(0.15)

    if drifts:
        raise Fail(f"LLM pin drifted after settle: {drifts[:8]}")

    return {"flips": len(flips), "final": f"{want_p}/{want_m}", "polls_ok": 12}


def pass_parallel_storm(token: str) -> dict[str, Any]:
    """Boot-like parallel storm Desktop fires on load / provider switch."""
    paths = [
        ("GET", "/ping"),
        ("GET", "/settings"),
        ("GET", "/providers/connected"),
        ("GET", "/models"),
        ("GET", "/models?provider=demo"),
        ("GET", "/models?provider=deepseek"),
        ("GET", "/models?provider=xai"),
        ("GET", "/models?provider=poe"),
        ("GET", "/sessions"),
        ("GET", "/agents"),
        ("GET", "/commands"),
        ("GET", "/vision/status"),
    ]
    # fire 3 waves of the full set
    jobs = paths * 3
    errors = []
    latencies = []

    def one(item: tuple[str, str]):
        method, path = item
        return path, req(method, path, token, timeout=25)

    with ThreadPoolExecutor(max_workers=16) as pool:
        futs = [pool.submit(one, j) for j in jobs]
        for f in as_completed(futs):
            path, (code, body, ms) = f.result()
            latencies.append(ms)
            if code < 200 or code >= 300:
                # vision may 404 when disabled — soft
                if path.startswith("/vision") and code in (404, 503):
                    continue
                errors.append(f"{path}->{code}")

    if errors:
        raise Fail(f"parallel storm errors: {errors[:10]}")
    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95) - 1] if latencies else 0
    return {
        "requests": len(latencies),
        "p50_ms": round(latencies[len(latencies) // 2], 1),
        "p95_ms": round(p95, 1),
        "max_ms": round(max(latencies), 1),
    }


def pass_settings_session_race(token: str, session_id: str) -> dict[str, Any]:
    """Classic thrash: PUT settings demo while PUT session xai in parallel."""
    def set_demo():
        return req(
            "PUT",
            "/settings",
            token,
            {"llm_provider": "demo", "llm_model": "codestral-latest"},
            timeout=25,
        )

    def set_xai_session():
        return req(
            "PUT",
            f"/sessions/{session_id}/llm",
            token,
            {"provider": "xai", "model": "grok-4.5", "make_default": True},
            timeout=25,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(set_demo) for _ in range(3)] + [
            pool.submit(set_xai_session) for _ in range(3)
        ]
        for f in as_completed(futs):
            code, body, _ = f.result()
            if code < 200 or code >= 300:
                raise Fail(f"race write failed {code}: {body!r}"[:300])

    # Last writer wins on global — pin xai once more as desktop does after pick
    code, body, _ = req(
        "PUT",
        f"/sessions/{session_id}/llm",
        token,
        {"provider": "xai", "model": "grok-4.5", "make_default": True},
        timeout=25,
    )
    must_ok(code, "race settle", body)
    time.sleep(0.2)
    code, settings, _ = req("GET", "/settings", token, timeout=15)
    must_ok(code, "/settings", settings)
    if str(settings.get("llm_provider")).lower() != "xai":
        raise Fail(f"after race settle global not xai: {settings.get('llm_provider')}")
    if str(settings.get("llm_model")) != "grok-4.5":
        raise Fail(f"after race settle model not grok-4.5: {settings.get('llm_model')}")
    return {"settled": "xai/grok-4.5"}


def ensure_session(token: str) -> str:
    code, body, _ = req(
        "POST",
        "/sessions",
        token,
        {"title": f"stress-{int(time.time())}"},
        timeout=20,
    )
    if code in (200, 201) and isinstance(body, dict) and body.get("id"):
        return str(body["id"])
    # fallback list
    code, sessions, _ = req("GET", "/sessions", token, timeout=15)
    must_ok(code, "/sessions", sessions)
    sess_list = sessions.get("sessions") if isinstance(sessions, dict) else sessions
    if not sess_list:
        raise Fail("no sessions available")
    return str(sess_list[0]["id"])


def main() -> int:
    print(f"Remedy stress: base={BASE} passes={PASSES} home={HOME}")
    token = load_token()
    # wait for serve
    for i in range(40):
        code, _, _ = req("GET", "/ping", token, timeout=3)
        if code == 200:
            break
        time.sleep(0.25)
    else:
        print("FAIL: server not up on /api/ping")
        return 2

    session_id = ensure_session(token)
    print(f"session={session_id}")

    failures: list[str] = []
    summary: list[dict[str, Any]] = []

    for n in range(1, PASSES + 1):
        t0 = time.perf_counter()
        row: dict[str, Any] = {"pass": n}
        try:
            row["health"] = pass_health(token)
            row["catalog"] = pass_connected_and_models(token)
            row["pin"] = pass_session_llm_pin(token, session_id)
            row["storm"] = pass_parallel_storm(token)
            row["race"] = pass_settings_session_race(token, session_id)
            row["ok"] = True
            row["sec"] = round(time.perf_counter() - t0, 2)
            print(
                f"PASS {n:02d}/{PASSES} ok  "
                f"{row['sec']}s  storm_p95={row['storm']['p95_ms']}ms  "
                f"picker={','.join(row['catalog']['picker'])}"
            )
        except Exception as e:
            row["ok"] = False
            row["error"] = str(e)[:500]
            row["sec"] = round(time.perf_counter() - t0, 2)
            failures.append(f"pass {n}: {e}")
            print(f"FAIL {n:02d}/{PASSES}  {e}")
        summary.append(row)

    ok_n = sum(1 for r in summary if r.get("ok"))
    print("---")
    print(f"Result: {ok_n}/{PASSES} passes OK")
    out = HOME / "logs" / "stress_desktop_api_last.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Wrote {out}")
    if failures:
        print("Failures:")
        for f in failures:
            print(" ", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
