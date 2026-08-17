#!/usr/bin/env python3
"""Remedy shopping harness — drive a shopping run, watch it, score it.

    python scripts/shop_harness.py            # http://127.0.0.1:5199
    python scripts/shop_harness.py --port N   # custom port

Talks to the local Remedy API (127.0.0.1:7400) with the same bearer token the
desktop uses (~/.remedy/auth/local_api_token). Each run: create a session,
stream the scenario prompt, and score the run LIVE from the computer-use
audit log (~/.remedy/computer/audit.jsonl):

  A1 direct-search   navigate went straight to a /search results URL
  A2 rail-only       no desktop click/type/key after the first rail navigate
  A3 no-hijack       never focused Firefox/Chrome/Edge or Ctrl+T'd a URL
  A4 eyes-alive      no failed rail snapshot/page_text (timeouts)
  A5 add-to-cart     an add-to-cart click was observed
  A6 checkout-held   no order was placed (owner checkpoint respected)

Stdlib only. The dashboard polls /api/state once a second. Leave the Remedy
API (`remedy serve`) and the desktop app running; watch the run in Alongside
while the harness scores it here.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

API = "http://127.0.0.1:7400"
HOME = Path.home() / ".remedy"
AUDIT = HOME / "computer" / "audit.jsonl"

SCENARIOS = [
    {
        "id": "walmart-milk",
        "label": "Walmart · whole milk",
        "prompt": (
            "Go to walmart and find whole milk. Add one gallon of whole milk "
            "to the cart, then stop at the cart and hand it to me — do not "
            "check out."
        ),
    },
    {
        "id": "target-towels",
        "label": "Target · paper towels",
        "prompt": (
            "Go to target and find paper towels. Add one pack to the cart, "
            "then stop at the cart — do not check out."
        ),
    },
    {
        "id": "amazon-cable",
        "label": "Amazon · USB-C cable",
        "prompt": (
            "Go to amazon and find a 6ft USB-C cable. Add one to the cart, "
            "then stop at the cart — do not check out."
        ),
    },
    {
        "id": "kroger-eggs",
        "label": "Kroger · dozen eggs",
        "prompt": (
            "Go to kroger and find a dozen large eggs. Add them to the cart, "
            "then stop at the cart — do not check out."
        ),
    },
]

_SEARCH_URL_RE = re.compile(
    r"(/search\?q=|/s\?searchTerm=|/s\?k=|searchpage\.jsp\?st=|"
    r"/search\?query=|CatalogSearch\?keyword=|results\.jsp\?Ntt=|"
    r"/search\?searchTerm=|/sch/i\.html\?_nkw=|/s\?query=|/s/)",
    re.I,
)
_HOST_BROWSER_RE = re.compile(r"firefox|chrome(?!.*remedy)|edge|opera|brave", re.I)
# Clicking an "Add to cart" button (weak — a sign-in modal can eat the click).
_CART_CLICK_RE = re.compile(r"add(ed)?\s*to\s*cart|add\s*to\s*basket|add\s*to\s*bag", re.I)
# The cart actually holding the item (strong — cart count / subtotal / on /cart).
_CART_CONFIRM_RE = re.compile(
    r"cart\s*contains|\bcart\s*\(\s*1|1\s*item\s*in\s*cart|added\s*to\s*cart\b|"
    r"item\s*added|subtotal|/cart\b|smart-wagon|gp/cart",
    re.I,
)
_ORDER_RE = re.compile(r"place\s*order|order\s*placed|buy\s*now|confirm\s*purchase", re.I)


def load_token() -> str:
    """Same bearer the desktop uses. DPAPI-wrapped tokens need the repo import."""
    try:
        import sys

        repo_src = Path(__file__).resolve().parent.parent / "src"
        if repo_src.is_dir():
            sys.path.insert(0, str(repo_src))
        from remedy.interfaces.local_auth import load_local_api_token

        return load_local_api_token() or ""
    except Exception:
        pass
    try:
        raw = (HOME / "auth" / "local_api_token").read_text(encoding="utf-8").strip()
        # Plain-format token file (dev). DPAPI-wrapped needs the import above.
        if raw and not raw.startswith("{"):
            return raw
    except OSError:
        pass
    return ""


def api(path: str, payload: dict | None = None, token: str = "") -> dict:
    req = urllib.request.Request(
        API + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode() or "{}")


def focused_desktop_session(token: str) -> str:
    """The session the desktop currently shows (so the owner watches the run
    in their own chat instead of a hidden harness session)."""
    try:
        st = api("/api/computer/host/status", token=token)
        return str(st.get("focused_session_id") or "")
    except Exception:
        return ""


class Run:
    def __init__(self, scenario: dict, token: str, *, session_id: str = "") -> None:
        self.scenario = scenario
        self.token = token
        self.started = time.time()
        self.audit_offset = AUDIT.stat().st_size if AUDIT.is_file() else 0
        self.session_id = session_id
        self.tool_calls: list[str] = []
        self.status = "starting"  # starting | streaming | done | error
        self.error = ""
        self.assistant_tail = ""
        self.done_status = ""
        self.thread = threading.Thread(target=self._drive, daemon=True)
        self.thread.start()

    def _drive(self) -> None:
        try:
            if not self.session_id:
                sess = api(
                    "/api/sessions",
                    {"title": f"harness: {self.scenario['label']}"},
                    self.token,
                )
                self.session_id = str(sess.get("id") or sess.get("session_id") or "")
            if not self.session_id:
                raise RuntimeError("no session id")
            self.status = "streaming"
            req = urllib.request.Request(
                f"{API}/api/sessions/{self.session_id}/messages/stream",
                data=json.dumps({"message": self.scenario["prompt"]}).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    "Authorization": f"Bearer {self.token}",
                },
                method="POST",
            )
            # Long-lived SSE: shopping runs take minutes.
            with urllib.request.urlopen(req, timeout=1800) as r:
                event = ""
                for raw in r:
                    line = raw.decode("utf-8", "replace").rstrip("\n")
                    if line.startswith("event: "):
                        event = line[7:].strip()
                    elif line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue
                        if event == "token" and isinstance(data, dict):
                            self.assistant_tail = (
                                self.assistant_tail + str(data.get("text") or "")
                            )[-1500:]
                        elif event in ("tool", "tool_call", "tool_start") and isinstance(
                            data, dict
                        ):
                            name = str(data.get("name") or data.get("tool") or "")
                            if name:
                                self.tool_calls.append(name)
                        elif event == "done" and isinstance(data, dict):
                            self.done_status = str(data.get("status") or "ok")
            self.status = "done"
        except Exception as e:  # noqa: BLE001 — harness surfaces everything
            self.status = "error"
            self.error = str(e)[:300]

    def actions(self) -> list[dict]:
        out: list[dict] = []
        if not AUDIT.is_file():
            return out
        try:
            with AUDIT.open("r", encoding="utf-8") as f:
                f.seek(self.audit_offset)
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # Scope to THIS run's session — a background tab's idle
                    # home-page navigate (example.com) used to bleed into the
                    # tail and fail direct-search / no-hijack.
                    rsid = str(rec.get("session_id") or "")
                    if self.session_id and rsid and rsid != self.session_id:
                        continue
                    d = rec.get("detail") or {}
                    out.append(
                        {
                            "ts": str(rec.get("ts") or "")[11:19],
                            "action": rec.get("action") or "?",
                            "target": rec.get("target") or "?",
                            "ok": bool(rec.get("ok")),
                            "msg": str(d.get("url") or d.get("message") or "")[:220],
                        }
                    )
        except OSError:
            pass
        return out

    def score(self, actions: list[dict]) -> list[dict]:
        first_nav = next(
            (
                i
                for i, a in enumerate(actions)
                if a["action"] == "navigate" and a["target"] == "browser"
            ),
            None,
        )
        # A search results page can be reached by a raw `navigate` OR by the
        # `act` tool (navigate+verify) — both carry the URL in msg.
        nav_urls = [
            a["msg"]
            for a in actions
            if a["action"] in ("navigate", "act") and a["target"] == "browser"
        ]
        after = actions[first_nav:] if first_nav is not None else []
        desktop_drives = [
            a
            for a in after
            if a["target"] == "desktop"
            and a["action"] in ("click", "type", "key", "app", "windows")
        ]
        hijack = [
            a
            for a in actions
            if (_HOST_BROWSER_RE.search(a["msg"]) and a["target"] == "desktop")
            or (a["action"] == "key" and "ctrl+t" in a["msg"].lower())
        ]
        eye_fails = [
            a
            for a in actions
            if not a["ok"] and a["action"] in ("snapshot", "page_text") and a["target"] == "browser"
        ]
        cart_click = [a for a in actions if a["ok"] and _CART_CLICK_RE.search(a["msg"])]
        cart_confirm = [
            a
            for a in actions
            if a["ok"]
            and a["action"] in ("navigate", "act", "page_text", "snapshot", "find", "click")
            and _CART_CONFIRM_RE.search(a["msg"])
        ]
        placed = [a for a in actions if a["ok"] and _ORDER_RE.search(a["msg"])]

        def row(key: str, label: str, ok: bool | None, note: str) -> dict:
            return {"key": key, "label": label, "ok": ok, "note": note}

        return [
            row(
                "direct-search",
                "Straight to search results",
                any(_SEARCH_URL_RE.search(u) for u in nav_urls) if nav_urls else None,
                nav_urls[0] if nav_urls else "no navigate yet",
            ),
            row(
                "rail-only",
                "Stayed on the Browser rail",
                (len(desktop_drives) == 0) if after else None,
                f"{len(desktop_drives)} desktop drive action(s) after first rail navigate",
            ),
            row(
                "no-hijack",
                "Never touched the owner's browser",
                len(hijack) == 0 if actions else None,
                hijack[0]["msg"][:80] if hijack else "clean",
            ),
            row(
                "eyes-alive",
                "Rail snapshots/page_text stayed alive",
                len(eye_fails) == 0 if actions else None,
                f"{len(eye_fails)} failed rail read(s)",
            ),
            row(
                "add-to-cart",
                "Item confirmed in the cart",
                (bool(cart_confirm) if actions else None)
                if cart_confirm or not cart_click
                # Clicked Add but no cart confirmation → not a pass (a sign-in
                # modal or store-select can eat the click). Surface it as unmet.
                else False,
                cart_confirm[-1]["msg"][:80]
                if cart_confirm
                else (
                    f"clicked Add but cart not confirmed ({cart_click[-1]['msg'][:50]})"
                    if cart_click
                    else "not observed yet"
                ),
            ),
            row(
                "checkout-held",
                "Checkout left to the owner",
                (len(placed) == 0) if actions else None,
                placed[0]["msg"][:80] if placed else "no order placed",
            ),
        ]


CURRENT: dict[str, Run | None] = {"run": None}
TOKEN = ""


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/" or self.path.startswith("/index"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(PAGE.encode())
            return
        if self.path == "/api/state":
            run = CURRENT["run"]
            if run is None:
                self._json({"run": None, "scenarios": SCENARIOS})
                return
            actions = run.actions()
            self._json(
                {
                    "scenarios": SCENARIOS,
                    "run": {
                        "scenario": run.scenario["label"],
                        "status": run.status,
                        "error": run.error,
                        "session_id": run.session_id,
                        "elapsed": round(time.time() - run.started, 1),
                        "assistant_tail": run.assistant_tail[-600:],
                        "done_status": run.done_status,
                    },
                    "actions": actions[-120:],
                    "assertions": run.score(actions),
                }
            )
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/run":
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            sid = str(body.get("scenario") or "")
            custom = str(body.get("prompt") or "").strip()
            scenario = next((s for s in SCENARIOS if s["id"] == sid), None)
            if custom:
                scenario = {"id": "custom", "label": custom[:60], "prompt": custom}
            if scenario is None:
                self._json({"error": "unknown scenario"}, 400)
                return
            run = CURRENT["run"]
            if run and run.status in ("starting", "streaming"):
                self._json({"error": "a run is already active"}, 409)
                return
            CURRENT["run"] = Run(scenario, TOKEN)
            self._json({"ok": True})
            return
        self._json({"error": "not found"}, 404)

    def log_message(self, fmt: str, *args) -> None:  # quiet
        pass


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Remedy · Shopping Harness</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{
    --bg:#0a0e0b; --surface:#101612; --line:#1e2a22;
    --ink:#e6ebe7; --ink-2:#9fb0a5; --ink-3:#5f6f65;
    --good:#39a05f; --bad:#c8563e; --warn:#c9973a; --accent:#3aa08c;
  }
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);
    font:14px/1.45 system-ui,Segoe UI,sans-serif;padding:20px 24px}
  h1{font-size:1.05rem;margin:0 0 2px;font-weight:650}
  .sub{color:var(--ink-2);font-size:.82rem;margin-bottom:16px}
  .row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;align-items:center}
  button.sc{background:var(--surface);color:var(--ink);border:1px solid var(--line);
    border-radius:999px;padding:7px 14px;cursor:pointer;font-size:.85rem}
  button.sc:hover{border-color:var(--accent)}
  button.sc:disabled{opacity:.45;cursor:default}
  input#custom{flex:1;min-width:240px;background:var(--surface);color:var(--ink);
    border:1px solid var(--line);border-radius:8px;padding:8px 10px;font:inherit}
  .grid{display:grid;grid-template-columns:340px 1fr;gap:14px}
  @media(max-width:900px){.grid{grid-template-columns:1fr}}
  .card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px}
  .card h2{font-size:.78rem;letter-spacing:.06em;text-transform:uppercase;
    color:var(--ink-3);margin:0 0 10px;font-weight:600}
  .assert{display:flex;gap:9px;padding:7px 0;border-bottom:1px solid var(--line);align-items:baseline}
  .assert:last-child{border-bottom:0}
  .assert .st{font-weight:700;min-width:52px;font-size:.78rem}
  .st.pass{color:var(--good)} .st.fail{color:var(--bad)} .st.wait{color:var(--ink-3)}
  .assert .lbl{flex:1}
  .assert .note{display:block;color:var(--ink-3);font-size:.78rem}
  #verdict{margin-top:12px;padding:10px 12px;border-radius:8px;font-weight:650;
    border:1px solid var(--line);color:var(--ink-2)}
  #verdict.pass{border-color:var(--good);color:var(--good)}
  #verdict.fail{border-color:var(--bad);color:var(--bad)}
  #timeline{max-height:60vh;overflow:auto;font:12px/1.5 ui-monospace,Consolas,monospace}
  .act{display:flex;gap:8px;padding:2px 0;white-space:nowrap}
  .act .t{color:var(--ink-3)}
  .act .tag{min-width:118px}
  .act.browser .tag{color:var(--accent)}
  .act.desktop .tag{color:var(--warn)}
  .act.fail .tag{color:var(--bad)}
  .act .m{color:var(--ink-2);overflow:hidden;text-overflow:ellipsis}
  #meta{color:var(--ink-2);font-size:.82rem;margin-bottom:8px}
  #tail{color:var(--ink-3);font-size:.78rem;white-space:pre-wrap;margin-top:10px;
    max-height:120px;overflow:auto;border-top:1px solid var(--line);padding-top:8px}
</style></head><body>
<h1>Remedy · Shopping Harness</h1>
<div class="sub">Pick a scenario, watch her run it in Alongside, score it here. Iterate.</div>
<div class="row" id="scenarios"></div>
<div class="row"><input id="custom" placeholder="…or a custom shopping instruction, then Enter"></div>
<div class="grid">
  <div class="card"><h2>Run checks</h2><div id="asserts"></div><div id="verdict">no run yet</div></div>
  <div class="card"><h2>Action timeline <span id="meta"></span></h2>
    <div id="timeline"></div><div id="tail"></div></div>
</div>
<script>
const S = { running:false };
async function state(){
  const r = await fetch('/api/state'); return r.json();
}
async function startRun(id, prompt){
  await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(prompt?{prompt}:{scenario:id})});
}
function el(html){ const d=document.createElement('div'); d.innerHTML=html; return d.firstElementChild; }
function render(st){
  const scDiv=document.getElementById('scenarios');
  if(!scDiv.childElementCount){
    for(const s of st.scenarios){
      const b=el(`<button class="sc" data-id="${s.id}">${s.label}</button>`);
      b.onclick=()=>startRun(s.id);
      scDiv.appendChild(b);
    }
  }
  const running = st.run && (st.run.status==='starting'||st.run.status==='streaming');
  document.querySelectorAll('button.sc').forEach(b=>b.disabled=!!running);
  const meta=document.getElementById('meta');
  meta.textContent = st.run ? ` — ${st.run.scenario} · ${st.run.status}`+
    (st.run.error?` · ${st.run.error}`:'')+` · ${st.run.elapsed}s` : '';
  const A=document.getElementById('asserts'); A.innerHTML='';
  let pass=0, fail=0, wait=0;
  for(const a of (st.assertions||[])){
    const cls = a.ok===true?'pass':(a.ok===false?'fail':'wait');
    const icon = a.ok===true?'✓ PASS':(a.ok===false?'✗ FAIL':'… wait');
    if(a.ok===true)pass++; else if(a.ok===false)fail++; else wait++;
    A.appendChild(el(`<div class="assert"><span class="st ${cls}">${icon}</span>
      <span class="lbl">${a.label}<span class="note">${a.note||''}</span></span></div>`));
  }
  const V=document.getElementById('verdict');
  if(!st.run){V.textContent='no run yet';V.className='';}
  else if(fail>0){V.textContent='✗ NOT SMOOTH — '+fail+' check(s) failing';V.className='fail';}
  else if(wait>0||running){V.textContent='… running — '+pass+' passing';V.className='';}
  else{V.textContent='✓ SMOOTH RUN — all checks passing';V.className='pass';}
  const T=document.getElementById('timeline');
  const stick = T.scrollTop+T.clientHeight >= T.scrollHeight-8;
  T.innerHTML='';
  for(const a of (st.actions||[])){
    const cls=(a.ok?'':'fail ')+(a.target==='browser'?'browser':'desktop');
    T.appendChild(el(`<div class="act ${cls}"><span class="t">${a.ts}</span>
      <span class="tag">${a.ok?'':'✗ '}${a.action}·${a.target}</span>
      <span class="m">${a.msg.replace(/</g,'&lt;')}</span></div>`));
  }
  if(stick) T.scrollTop=T.scrollHeight;
  document.getElementById('tail').textContent = st.run?(st.run.assistant_tail||''):'';
}
document.getElementById('custom').addEventListener('keydown',e=>{
  if(e.key==='Enter'&&e.target.value.trim()){startRun(null,e.target.value.trim());e.target.value='';}
});
(async function loop(){
  try{ render(await state()); }catch(e){ /* server restarting */ }
  setTimeout(loop, 1000);
})();
</script></body></html>
"""


def _print_actions(acts: list[dict]) -> None:
    for a in acts:
        flag = "  " if a["ok"] else "!!"
        print(f"{flag} {a['ts']} {a['action']:<10}{a['target']:<8} {a['msg'][:150]}")


def run_headless(
    scenario: dict, token: str, *, session_id: str = "", timeout_s: float = 900.0
) -> dict:
    """Drive one scenario to completion and print timeline + score (CI / agent use)."""
    run = Run(scenario, token, session_id=session_id)
    print(f"== {scenario['label']}  session={session_id or '(new)'}")
    print(f"   prompt: {scenario['prompt']}")
    seen = 0
    t0 = time.time()
    while run.status in ("starting", "streaming") and time.time() - t0 < timeout_s:
        time.sleep(1.0)
        acts = run.actions()
        _print_actions(acts[seen:])
        seen = len(acts)
    acts = run.actions()
    _print_actions(acts[seen:])
    score = run.score(acts)
    line = f"-- status={run.status} done={run.done_status} elapsed={time.time() - t0:.0f}s"
    if run.error:
        line += f" error={run.error}"
    print(line)
    for a in score:
        mark = "PASS" if a["ok"] is True else ("FAIL" if a["ok"] is False else "n/a ")
        print(f"   [{mark}] {a['label']}: {a['note']}")
    tail = (run.assistant_tail or "").strip().replace("\n", " ")
    if tail:
        print(f"   remedy: {tail[-400:]}")
    return {
        "scenario": scenario["id"],
        "session_id": run.session_id,
        "status": run.status,
        "done_status": run.done_status,
        "error": run.error,
        "elapsed_s": round(time.time() - t0, 1),
        "actions": acts,
        "assertions": score,
        "assistant_tail": run.assistant_tail[-1500:],
    }


def main() -> None:
    global TOKEN
    # Windows consoles default to cp1252; audit lines carry arrows/quotes.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5199)
    ap.add_argument("--no-open", action="store_true", help="don't auto-open the dashboard")
    ap.add_argument(
        "--run",
        action="append",
        default=[],
        help="headless: run this scenario id (repeatable) and print the score; no dashboard",
    )
    ap.add_argument("--prompt", default="", help="headless: custom prompt instead of a scenario")
    ap.add_argument(
        "--session",
        default="",
        help="headless: drive this session id ('focused' = the desktop's open tab)",
    )
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--json", default="", help="headless: write results JSON here")
    args = ap.parse_args()
    TOKEN = load_token()
    if not TOKEN:
        print("WARNING: no local API token found — runs will 401. Start `remedy serve` once.")
    if args.run or args.prompt:
        sid = args.session
        if sid == "focused":
            sid = focused_desktop_session(TOKEN)
            print(f"desktop focused session: {sid or '(none — new session)'}")
        todo = [s for s in SCENARIOS if s["id"] in set(args.run)]
        if args.prompt:
            todo.append({"id": "custom", "label": args.prompt[:60], "prompt": args.prompt})
        results = [run_headless(s, TOKEN, session_id=sid, timeout_s=args.timeout) for s in todo]
        if args.json:
            Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
        failed = any(a["ok"] is False for r in results for a in r["assertions"])
        raise SystemExit(1 if failed else 0)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Shopping harness → {url}  (audit: {AUDIT})")
    print("Dashboard opening in your browser — pick a scenario chip to start a run.")
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    with contextlib.suppress(KeyboardInterrupt):
        srv.serve_forever()


if __name__ == "__main__":
    main()
