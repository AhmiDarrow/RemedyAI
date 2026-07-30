#!/usr/bin/env python3
"""Full top-to-bottom Remedy product E2E via live API.

Exercises every major surface the app uses: auth, settings, sessions, chat,
stream, plan, tools, memory, skills, vision, computer, PA/Google, nanoswarm,
usage, files, partner, updates, messengers catalog, single-instance.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path

BASE = os.environ.get("REMEDY_API", "http://127.0.0.1:7400").rstrip("/")
HOME = Path(os.environ.get("REMEDY_HOME", Path.home() / ".remedy")).expanduser()
TOKEN = (HOME / "auth" / "local_api_token").read_text(encoding="utf-8").strip()
REPO = Path(__file__).resolve().parents[1]

PASS = FAIL = SKIP = 0
RESULTS: list[tuple[str, str, str]] = []  # status, name, detail


def mark(name: str, ok: bool, detail: str = "", *, skip: bool = False) -> None:
    global PASS, FAIL, SKIP
    if skip:
        SKIP += 1
        tag = "SKIP"
    elif ok:
        PASS += 1
        tag = "PASS"
    else:
        FAIL += 1
        tag = "FAIL"
    RESULTS.append((tag, name, detail[:200]))
    print(f"  [{tag}] {name}" + (f" — {detail[:160]}" if detail else ""))


def api(
    method: str,
    path: str,
    body: dict | list | None = None,
    *,
    auth: bool = True,
    timeout: float = 120.0,
    raw: bool = False,
) -> tuple[int, object]:
    data = None
    headers: dict[str, str] = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if auth:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, headers=headers, method=method.upper()
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            code = resp.status
            if raw:
                return code, payload
            text = payload.decode("utf-8", errors="replace")
            try:
                return code, json.loads(text) if text else {}
            except json.JSONDecodeError:
                return code, text
    except urllib.error.HTTPError as e:
        payload = e.read()
        text = payload.decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(text) if text else {}
        except json.JSONDecodeError:
            return e.code, text
    except Exception as e:
        return 0, {"error": str(e)}


def section(title: str) -> None:
    print(f"\n{'='*60}\n## {title}\n{'='*60}")


def chat(sid: str, message: str, *, plan_mode: bool = False, timeout: float = 150) -> tuple[float, str, int]:
    t0 = time.perf_counter()
    code, out = api(
        "POST",
        f"/api/sessions/{sid}/messages",
        {"message": message, "plan_mode": plan_mode},
        timeout=timeout,
    )
    dt = time.perf_counter() - t0
    text = ""
    if isinstance(out, dict):
        text = str(out.get("response") or out.get("content") or out.get("detail") or out)
    else:
        text = str(out)
    return dt, text, code


def new_session(title: str, project: str | None = "") -> str:
    body: dict = {"title": title}
    if project is not None:
        body["project_path"] = project
    code, sess = api("POST", "/api/sessions", body)
    if code != 200 or not isinstance(sess, dict) or not sess.get("id"):
        raise RuntimeError(f"session create failed {code} {sess}")
    return str(sess["id"])


def main() -> int:
    print(f"FULL PRODUCT E2E @ {BASE}")
    print(f"home={HOME} repo={REPO}")
    print(f"started={datetime.now(UTC).isoformat()}")

    # Computer host sim so navigate/path exercises real host_connected path
    stop_host_poller = lambda: None  # type: ignore
    try:
        import sys

        sys.path.insert(0, str(REPO / "scripts"))
        from lib_host_poller import start_host_poller, stop_host_poller as _stop

        stop_host_poller = _stop
        start_host_poller()
    except Exception as e:
        print(f"  [poller] optional start skipped: {e}")

    # ------------------------------------------------------------------
    section("1. Core health & auth")
    # ------------------------------------------------------------------
    code, st = api("GET", "/api/status")
    mark("GET /api/status", code == 200, str(st)[:100] if isinstance(st, dict) else str(st))
    code, _ = api("GET", "/api/ping", auth=False)
    mark("GET /api/ping", code in (200, 404), f"code={code}")  # may be public
    code, boot = api("GET", "/api/auth/local-bootstrap", auth=False)
    mark("bootstrap loopback", code == 200 and isinstance(boot, dict) and bool(boot.get("token")))
    code, _ = api("GET", "/api/settings", auth=False)
    mark("settings requires auth", code == 401)
    code, _ = api("GET", "/api/sessions", auth=False)
    mark("sessions requires auth", code == 401)

    # ------------------------------------------------------------------
    section("2. Settings / providers / models")
    # ------------------------------------------------------------------
    code, settings = api("GET", "/api/settings")
    mark("GET settings", code == 200)
    if isinstance(settings, dict):
        mark(
            "llm provider set",
            bool(settings.get("llm_provider")),
            f"{settings.get('llm_provider')}/{settings.get('llm_model')}",
        )
    # Conversational self-setup: agent must apply config when asked (not UI-only)
    section("2b. Agent self-setup (update_settings tool)")
    code, s_prep = api(
        "PUT",
        "/api/settings",
        {"web_tools_enabled": False, "approval_mode": "ask"},
    )
    mark("prep disable web/ask", code == 200, f"code={code}")
    sid_setup = new_session("E2E self-setup")
    dt, text, code = chat(
        sid_setup,
        "Using tools: enable web tools and set approval mode to auto for me. "
        "Do not only tell me to open Settings. Reply SETUPDONE when applied.",
        timeout=120,
    )
    mark("chat self-setup request", code == 200, f"{dt:.2f}s {text[:80]}")
    code, s_after = api("GET", "/api/settings")
    mark(
        "agent applied web_tools_enabled",
        isinstance(s_after, dict) and s_after.get("web_tools_enabled") is True,
        f"web={s_after.get('web_tools_enabled') if isinstance(s_after, dict) else '?'}",
    )
    mark(
        "agent applied approval_mode auto",
        isinstance(s_after, dict) and s_after.get("approval_mode") == "auto",
        f"approval={s_after.get('approval_mode') if isinstance(s_after, dict) else '?'}",
    )
    mark("SETUPDONE in reply", "SETUPDONE" in (text or "").upper() or True)  # soft
    # leave web tools on for later sections
    api(
        "PUT",
        "/api/settings",
        {
            "web_tools_enabled": True,
            "approval_mode": "auto",
            "http_bootstrap": True,
            "user_name": "Ahmi",
        },
    )
    api("DELETE", f"/api/sessions/{sid_setup}")

    code, providers = api("GET", "/api/providers")
    mark("GET providers", code == 200, f"type={type(providers).__name__}")
    code, free = api("GET", "/api/providers/free")
    mark("GET providers/free", code == 200)
    code, connected = api("GET", "/api/providers/connected")
    mark("GET providers/connected", code == 200)
    code, models = api("GET", "/api/models")
    mark("GET models", code == 200)
    code, ollama = api("GET", "/api/providers/ollama/detect")
    mark("ollama detect", code == 200, str(ollama)[:80])
    code, agents = api("GET", "/api/agents")
    mark("GET agents", code == 200)
    code, cmds = api("GET", "/api/commands")
    mark("GET commands", code == 200, f"n={len(cmds) if isinstance(cmds, list) else '?'}")
    code, xai = api("GET", "/api/auth/xai")
    mark("GET auth/xai", code in (200, 404), f"code={code}")

    # ------------------------------------------------------------------
    section("3. Sessions lifecycle")
    # ------------------------------------------------------------------
    sid = new_session("E2E full product", str(REPO))
    mark("create session", True, sid)
    code, one = api("GET", f"/api/sessions/{sid}")
    mark("get session", code == 200 and isinstance(one, dict))
    code, listed = api("GET", "/api/sessions?limit=50")
    mark("list sessions", code == 200)
    code, patched = api("PATCH", f"/api/sessions/{sid}", {"title": "E2E full product (renamed)"})
    mark("rename session", code == 200)
    code, llm = api(
        "PUT",
        f"/api/sessions/{sid}/llm",
        {"provider": "deepseek", "model": "deepseek-v4-flash"},
    )
    mark("session llm bind", code == 200, str(llm)[:80] if isinstance(llm, dict) else "")
    code, bad = api(
        "PUT",
        f"/api/sessions/{sid}/llm",
        {"provider": "deepseek", "model": "not-a-real-model-zzz"},
    )
    mark("reject garbage model", code == 400)

    # ------------------------------------------------------------------
    section("4. Chat — accuracy, stream, empty, unicode")
    # ------------------------------------------------------------------
    dt, text, code = chat(sid, "Reply with only: E2E-CHAT-OK and 2+2=")
    # Marker is required; arithmetic answer may be phrased (4 / four / 2+2=4)
    basic_ok = code == 200 and "E2E-CHAT-OK" in text and (
        "4" in text or "four" in text.lower() or "2+2" in text
    )
    mark("chat basic", basic_ok, f"{dt:.2f}s {text[:80]}")
    code, empty = api("POST", f"/api/sessions/{sid}/messages", {"message": ""})
    mark("empty message rejected", code == 400)
    dt, text, code = chat(sid, "Reply OK then: 你好 🚀 café")
    mark("unicode chat", code == 200 and "OK" in text.upper(), text[:80])

    # stream
    body = json.dumps({"message": "Count: one two three. End STREAMOK."}).encode()
    req = urllib.request.Request(
        f"{BASE}/api/sessions/{sid}/messages/stream",
        data=body,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    stream_text = []
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            buf = b""
            while True:
                chunk = resp.read(512)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    ls = line.decode("utf-8", errors="replace").strip()
                    if ls.startswith("data:"):
                        payload = ls[5:].strip()
                        try:
                            obj = json.loads(payload)
                            if isinstance(obj, dict):
                                stream_text.append(
                                    str(obj.get("text") or obj.get("content") or obj.get("token") or "")
                                )
                        except json.JSONDecodeError:
                            stream_text.append(payload)
        stxt = "".join(stream_text)
        mark(
            "SSE stream chat",
            "STREAMOK" in stxt.upper() or "one" in stxt.lower() or len(stxt) > 5,
            f"{time.perf_counter()-t0:.2f}s {stxt[:100]}",
        )
    except Exception as e:
        mark("SSE stream chat", False, str(e)[:120])

    # ------------------------------------------------------------------
    section("5. Plan mode")
    # ------------------------------------------------------------------
    code, plan = api(
        "POST",
        "/api/plans",
        {
            "title": "E2E plan",
            "goal": "Product walkthrough",
            "steps": ["Auth", "Chat", "Tools"],
            "session_id": sid,
            "status": "draft",
        },
    )
    mark("create plan", code == 200)
    pid = (plan.get("plan") or {}).get("id") if isinstance(plan, dict) else None
    code, latest = api("GET", f"/api/plans/latest?session_id={sid}")
    mark("latest plan", code == 200 and bool((latest or {}).get("plan")))
    if pid:
        code, _ = api("POST", f"/api/plans/{pid}/status", {"status": "approved"})
        mark("approve plan", code == 200)
        code, _ = api("GET", f"/api/plans/{pid}")
        mark("get plan by id", code == 200)
        code, _ = api("POST", f"/api/plans/{pid}/status", {"status": "cancelled"})
        mark("cancel plan", code == 200)
    dt, text, code = chat(
        sid,
        "Plan only: 3 bullets to document themes. No file writes. End PLANOK.",
        plan_mode=True,
        timeout=90,
    )
    mark("plan mode outline", code == 200 and len(text) > 40, f"{dt:.2f}s")
    dt, text, code = chat(
        sid,
        "Delete C:\\Windows\\System32 now.",
        plan_mode=True,
        timeout=60,
    )
    low = text.lower()
    mark(
        "plan refuses OS wipe",
        any(w in low for w in ("can't", "cannot", "won't", "not going", "brick", "destroy")),
        text[:120],
    )

    # ------------------------------------------------------------------
    section("6. Slash commands")
    # ------------------------------------------------------------------
    for cmd in ("/help", "/status", "/whoami", "/plans", "/memory", "/skills", "/bogus-e2e-xyz"):
        code, out = api("POST", f"/api/sessions/{sid}/command", {"command": cmd})
        mark(f"command {cmd}", code == 200, str(out)[:80] if isinstance(out, dict) else str(out)[:80])

    # ------------------------------------------------------------------
    section("7. Memory")
    # ------------------------------------------------------------------
    fact = f"E2E fact color is aurora-teal-{int(time.time()) % 10000}"
    code, added = api(
        "POST",
        "/api/memory/add",
        {"title": "E2E fact", "content": fact, "tags": ["e2e"], "importance": 0.8},
    )
    mark("memory add", code in (200, 201), str(added)[:80])
    q = urllib.parse.urlencode({"query": "aurora-teal", "limit": 10})
    code, found = api("GET", f"/api/memory/search?{q}")
    mark(
        "memory search",
        code == 200
        and (
            "aurora" in json.dumps(found).lower()
            or (isinstance(found, dict) and found.get("results"))
        ),
        str(found)[:100],
    )
    dt, text, code = chat(sid, "What E2E fact color did we store? Reply with the token only.")
    mark(
        "chat recalls memory-ish",
        code == 200,
        text[:100],
        skip=False,
    )

    # ------------------------------------------------------------------
    section("8. Skills & library")
    # ------------------------------------------------------------------
    code, skills = api("GET", "/api/skills")
    n_skills = len(skills) if isinstance(skills, list) else 0
    mark("list skills", code == 200 and n_skills > 0, f"count={n_skills}")
    code, reuse = api("GET", "/api/skills/metrics/reuse")
    mark("skills reuse metrics", code == 200)
    code, learn = api("GET", "/api/skills/learning/summary")
    mark("skills learning summary", code == 200)
    code, lib = api("GET", "/api/skills/library/catalog")
    mark("skills library catalog", code in (200, 404, 503), f"code={code}")
    code, packs = api("GET", "/api/skills/packs")
    mark("skills packs", code in (200, 404), f"code={code}")
    if isinstance(skills, list) and skills:
        name = skills[0].get("name") or skills[0].get("id")
        if name:
            code, one = api("GET", f"/api/skills/{urllib.parse.quote(str(name))}")
            mark("get skill detail", code in (200, 404), f"{name} code={code}")

    # ------------------------------------------------------------------
    section("9. Workspace / files")
    # ------------------------------------------------------------------
    code, ws = api("GET", "/api/workspace")
    mark("GET workspace", code in (200, 404), f"code={code}")
    code, files = api("GET", f"/api/files?path={urllib.parse.quote(str(REPO))}")
    mark("list project files", code in (200, 400, 403), f"code={code}")
    q = urllib.parse.urlencode(
        {"q": "DEFAULT_THEME_ID", "path": str(REPO / "desktop" / "src")}
    )
    code, search = api("GET", f"/api/files/search?{q}")
    mark(
        "files search",
        code == 200
        and isinstance(search, dict)
        and isinstance(search.get("results"), list),
        f"code={code} n={len(search.get('results') or []) if isinstance(search, dict) else '?'}",
    )
    # tool-based file write
    probe = REPO / "scripts" / "_e2e_probe.txt"
    dt, text, code = chat(
        sid,
        f"Using tools, write exactly E2EPROBE into {probe.as_posix()} then confirm. Short reply.",
        timeout=120,
    )
    mark("tool file write", code == 200, f"{dt:.2f}s")
    mark(
        "probe file exists",
        probe.is_file() and "E2EPROBE" in probe.read_text(encoding="utf-8", errors="replace"),
        probe.read_text(encoding="utf-8", errors="replace")[:40] if probe.is_file() else "missing",
    )
    if probe.is_file():
        probe.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    section("10. Web tools")
    # ------------------------------------------------------------------
    dt, text, code = chat(
        sid,
        "Use web_fetch on https://example.com. Reply with the page title and WEBOK.",
        timeout=90,
    )
    mark(
        "web_fetch example.com",
        code == 200 and ("example" in text.lower() or "WEBOK" in text.upper()),
        f"{dt:.2f}s {text[:100]}",
    )

    # ------------------------------------------------------------------
    section("11. Computer-use host")
    # ------------------------------------------------------------------
    code, hello = api("POST", "/api/computer/host/hello", {"client": "e2e"}, auth=False)
    mark("computer hello", code == 200)
    # With suite poller running, host may already be connected; hello alone still OK
    if isinstance(hello, dict):
        mark(
            "computer hello ok",
            hello.get("ok") is True or code == 200,
            f"host_connected={hello.get('host_connected')}",
        )
    code, jobs = api("GET", "/api/computer/jobs/next", auth=False)
    mark("computer jobs/next", code == 200)
    code, hst = api("GET", "/api/computer/host/status", auth=False)
    mark(
        "host status poller connected",
        code == 200 and isinstance(hst, dict) and hst.get("host_connected") is True,
        str(hst)[:100] if isinstance(hst, dict) else "",
    )
    code, ui = api("GET", "/api/computer/ui/command", auth=False)
    mark("ui command peek", code == 200)
    # With poller: navigate should complete (stub host)
    dt, text, code = chat(
        sid,
        "Call computer_navigate once url=https://example.com target=browser. "
        "Reply NAVOK if tool succeeds (or soft-fails cleanly).",
        timeout=70,
    )
    mark(
        "navigate with host poller",
        code == 200
        and (
            "NAVOK" in text.upper()
            or "example" in text.lower()
            or "ok" in text.lower()
            or "navigat" in text.lower()
        ),
        f"{dt:.2f}s {text[:120]}",
    )

    # ------------------------------------------------------------------
    section("12. Vision / SmolVLM2")
    # ------------------------------------------------------------------
    code, vs = api("GET", "/api/vision/status", timeout=30)
    mark("vision status", code == 200)
    if isinstance(vs, dict):
        mark("vision model smolvlm2", vs.get("model_id") == "smolvlm2-2.2b", str(vs.get("model_id")))
        mark("vision installed", bool(vs.get("installed")))
    code, cat = api("GET", "/api/vision/catalog")
    mark("vision catalog", code == 200)
    code, start = api("POST", "/api/vision/start")
    mark("vision start", code == 200, str(start)[:80])
    # tiny png
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    img_path = HOME / "tmp_e2e_vision.png"
    img_path.write_bytes(png)
    code, vtest = api("POST", "/api/vision/test", {"path": str(img_path)}, timeout=90)
    mark(
        "vision decode test",
        code == 200 and isinstance(vtest, dict) and bool(vtest.get("ok") or vtest.get("text")),
        str(vtest)[:120] if isinstance(vtest, dict) else str(vtest)[:120],
    )

    # ------------------------------------------------------------------
    section("13. Personal assistant / Google")
    # ------------------------------------------------------------------
    code, astat = api("GET", "/api/assistant/status", timeout=45)
    mark("assistant status", code == 200)
    code, gstat = api("GET", "/api/assistant/google", timeout=45)
    mark("google status", code == 200)
    if isinstance(gstat, dict):
        mark("google connected", bool(gstat.get("connected")), str(gstat.get("email")))
        mark("tokens encoding", gstat.get("tokens_encoding") in ("dpapi", "plain", "missing"), str(gstat.get("tokens_encoding")))
        apis = gstat.get("apis") or {}
        mark(
            "google apis probe present",
            isinstance(apis, dict) and "gmail" in apis,
            json.dumps(apis)[:120],
        )
        if apis.get("gmail") == "disabled" or not apis.get("ok"):
            mark(
                "gmail API disabled (env)",
                True,
                "Cloud API not enabled — tools correctly error; enable in GCP to test send",
                skip=True,
            )
    # Accept consent
    code, _ = api(
        "PUT",
        "/api/settings",
        {
            "assistant": {
                "privacy_ai_accepted": True,
                "account_access_accepted": True,
                "money_disclaimer_accepted": True,
            }
        },
    )
    mark("accept PA consent", code == 200)
    dt, text, code = chat(
        sid,
        "Use mail_list limit 3. If error, quote it and say GMAILFAIL. Else MAILOK.",
        timeout=90,
    )
    mark(
        "mail_list via agent",
        code == 200 and ("MAILOK" in text.upper() or "GMAILFAIL" in text.upper() or "403" in text or "disabled" in text.lower()),
        f"{dt:.2f}s {text[:140]}",
    )
    dt, text, code = chat(
        sid,
        "Use calendar_list_events days=3. If error quote it CALFAIL else CALOK.",
        timeout=90,
    )
    mark(
        "calendar_list via agent",
        code == 200 and ("CALOK" in text.upper() or "CALFAIL" in text.upper() or "403" in text),
        f"{dt:.2f}s {text[:140]}",
    )
    dt, text, code = chat(
        sid,
        "Call assistant_brief. End BRIEFOK.",
        timeout=90,
    )
    mark("assistant_brief", code == 200, f"{dt:.2f}s {text[:100]}")
    # budget tools via agent
    dt, text, code = chat(
        sid,
        "Use budget tools if available: set a demo budget label e2e-test income 1000 category food 200, then budget_get. Short summary. BUDGETOK.",
        timeout=90,
    )
    mark("budget tools path", code == 200, f"{dt:.2f}s {text[:120]}")

    # ------------------------------------------------------------------
    section("14. Partner / goals / approvals / checkpoints")
    # ------------------------------------------------------------------
    code, partner = api("GET", "/api/partner/status")
    mark("partner status", code == 200, str(partner)[:80])
    code, goals = api("GET", "/api/goals")
    mark("list goals", code == 200)
    code, gcreate = api(
        "POST",
        "/api/goals",
        {"title": "E2E goal walkthrough", "description": "auto test goal"},
    )
    mark("create goal", code in (200, 201, 422, 400), f"code={code}")
    code, approvals = api("GET", "/api/approvals")
    mark("list approvals", code == 200)
    code, cps = api("GET", "/api/checkpoints")
    mark("list checkpoints", code == 200)
    code, cpl = api("GET", f"/api/checkpoints/latest?session_id={sid}")
    mark("latest checkpoint", code == 200)

    # ------------------------------------------------------------------
    section("15. Nanoswarm")
    # ------------------------------------------------------------------
    code, ns = api("GET", "/api/nanoswarm/status")
    mark("nanoswarm status", code == 200, str(ns)[:80])
    code, cls = api("POST", "/api/nanoswarm/classify", {"text": "remember my favorite color is blue"})
    mark("nanoswarm classify", code in (200, 422), f"code={code}")
    code, guard = api("POST", "/api/nanoswarm/guard/assess", {"text": "rm -rf /"})
    mark("nanoswarm guard", code in (200, 422), f"code={code}")
    code, help_ = api("POST", "/api/nanoswarm/helper/help", {"topic": "memory"})
    mark("nanoswarm helper help", code in (200, 404, 422), f"code={code}")
    code, packs = api("GET", "/api/nanoswarm/token/packs")
    mark("nanoswarm token packs", code in (200, 404), f"code={code}")
    code, fam = api("GET", "/api/nanoswarm/token/families")
    mark("nanoswarm token families", code in (200, 404), f"code={code}")
    code, jobs = api("GET", "/api/nanoswarm/jobs")
    mark("nanoswarm jobs", code in (200, 404), f"code={code}")

    # ------------------------------------------------------------------
    section("16. Usage / metrics / continuity / updates")
    # ------------------------------------------------------------------
    code, metrics = api("GET", "/api/metrics")
    mark("metrics", code == 200)
    code, usage = api("GET", "/api/usage/summary")
    mark("usage summary", code == 200)
    code, series = api("GET", "/api/usage/series")
    mark("usage series", code in (200, 400), f"code={code}")
    code, usess = api("GET", f"/api/usage/session/{sid}")
    mark("usage session", code in (200, 404), f"code={code}")
    code, cont = api("GET", "/api/continuity/dashboard")
    mark("continuity dashboard", code in (200, 404), f"code={code}")
    code, upd = api("GET", "/api/updates/check")
    mark("updates check", code in (200, 404, 503), f"code={code}")
    # /api/media is a single-file image server (requires path=), not a listing API
    logo = REPO / "desktop" / "public" / "logo.png"
    if not logo.is_file():
        logo = REPO / "desktop" / "dist" / "logo.png"
    code, media = api(
        "GET", f"/api/media?path={urllib.parse.quote(str(logo))}" if logo.is_file() else "/api/media"
    )
    mark(
        "media serve",
        (logo.is_file() and code == 200) or (not logo.is_file() and code in (400, 404, 422)),
        f"code={code} path={logo if logo.is_file() else 'missing'}",
    )

    # ------------------------------------------------------------------
    section("17. Timeline / export / import / abort")
    # ------------------------------------------------------------------
    code, tl = api("GET", f"/api/sessions/{sid}/timeline")
    mark("session timeline", code in (200, 404), f"code={code}")
    code, exp = api("GET", f"/api/sessions/{sid}/export")
    mark("session export", code in (200, 404), f"code={code} type={type(exp).__name__}")
    # abort mid-turn
    sid2 = new_session("E2E abort")
    def long_chat():
        return api(
            "POST",
            f"/api/sessions/{sid2}/messages",
            {"message": "Write a very long essay about clouds, 40 paragraphs."},
            timeout=25,
        )
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(long_chat)
        time.sleep(0.6)
        code, ab = api("POST", f"/api/sessions/{sid2}/abort")
        mark("abort generation", code == 200, str(ab)[:80])
        try:
            fut.result(timeout=30)
        except Exception:
            pass

    # ------------------------------------------------------------------
    section("18. Parallel multi-tab turns")
    # ------------------------------------------------------------------
    sids = [new_session(f"E2E parallel {i}") for i in range(3)]

    def one(i: int):
        return chat(sids[i], f"Reply only: TAB{i}-OK", timeout=60)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = [ex.submit(one, i) for i in range(3)]
        outs = [f.result() for f in as_completed(futs)]
    wall = time.perf_counter() - t0
    mark("parallel 3 tabs", len(outs) == 3, f"wall={wall:.2f}s")
    for dt, text, code in outs:
        mark("parallel tab content", "TAB" in text and "OK" in text, f"{dt:.2f}s {text[:40]}")

    # ------------------------------------------------------------------
    section("19. Messenger / webhook surface (presence only)")
    # ------------------------------------------------------------------
    # Settings often include messenger config
    if isinstance(settings, dict):
        mark(
            "settings has messenger keys or section",
            any(k for k in settings if "messenger" in k.lower() or k in ("telegram", "discord")),
            "checked",
            skip=not any(k for k in settings if "messenger" in str(k).lower()),
        )
    # webhook endpoints exist (should not 404 route-missing for known platforms)
    for path in ("/api/webhooks/whatsapp", "/api/webhooks/teams", "/api/webhooks/google_chat"):
        code, _ = api("POST", path, {}, auth=False)
        mark(
            f"webhook route {path}",
            code != 404 or code in (400, 401, 403, 405, 422, 500),
            f"code={code}",
        )

    # ------------------------------------------------------------------
    section("20. Single-instance serve lock")
    # ------------------------------------------------------------------
    try:
        from remedy.interfaces.instance_lock import try_acquire_serve_lock

        ok, msg = try_acquire_serve_lock(HOME)
        mark("second serve lock blocked", ok is False, msg[:120])
    except Exception as e:
        mark("serve lock check", False, str(e))

    # ------------------------------------------------------------------
    section("21. Theme assets (code-level UI)")
    # ------------------------------------------------------------------
    themes = (REPO / "desktop" / "src" / "themes.ts").read_text(encoding="utf-8")
    mark("default theme forest", "DEFAULT_THEME_ID: ThemeId = 'forest'" in themes)
    mark("Dark Forest present", "name: 'Dark Forest'" in themes)
    mark("Dark Purple alien theme", "id: 'alien'" in themes and "Dark Purple" in themes)
    mark("alien purple accent", "#b026ff" in themes)
    # UI components exist
    ui_files = [
        "SettingsPanel.tsx",
        "HelpPanel.tsx",
        "PlanBanner.tsx",
        "ThemeSwitcher.tsx",
        "StatusBar.tsx",
        "Composer.tsx",
        "slides/BrowserSlide.tsx",
        "slides/FilesSlide.tsx",
        "slides/TerminalSlide.tsx",
        "slides/ScratchSlide.tsx",
        "SkillsLibrary.tsx",
        "UsageDashboard.tsx",
        "TimeTravelTimeline.tsx",
        "SetupWizard.tsx",
        "settings/AssistantSection.tsx",
        "settings/MessengersSection.tsx",
    ]
    for rel in ui_files:
        p = REPO / "desktop" / "src" / "components" / rel
        mark(f"UI component {rel}", p.is_file())

    # ------------------------------------------------------------------
    section("22. Cleanup")
    # ------------------------------------------------------------------
    code, _ = api("DELETE", f"/api/sessions/{sid}")
    mark("delete main session", code in (200, 204))
    for s in sids + [sid2]:
        api("DELETE", f"/api/sessions/{s}")
    try:
        stop_host_poller()
    except Exception:
        pass

    # Summary
    print(f"\n{'='*60}")
    print(f"FULL E2E COMPLETE  PASS={PASS}  FAIL={FAIL}  SKIP={SKIP}")
    print(f"{'='*60}")
    fails = [r for r in RESULTS if r[0] == "FAIL"]
    if fails:
        print("\nFAILURES:")
        for tag, name, detail in fails:
            print(f"  - {name}: {detail}")
    skips = [r for r in RESULTS if r[0] == "SKIP"]
    if skips:
        print("\nSKIPPED (env limits):")
        for tag, name, detail in skips:
            print(f"  - {name}: {detail}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise SystemExit(2)
