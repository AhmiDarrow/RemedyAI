#!/usr/bin/env python3
"""Controlled live red-team probes against a running Remedy local API.

Goal: exercise real attack surfaces (auth, path jail, SSRF helpers, computer host,
bootstrap, webhooks) and report pass/fail for hardening — NOT weaponize.

Safe by design:
  - No destructive disk writes outside a temp probe file under %TEMP%
  - No shell escape payloads that wipe data
  - Computer host probes only hello/status/next (no forged job complete on real jobs)
  - Prints JSON summary to stdout

Usage (from repo root, API on 127.0.0.1:7400)::

    .venv\\Scripts\\python.exe scripts/_redteam_live_probes.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from lib_local_token import resolve_local_api_token  # noqa: E402

BASE = (os.environ.get("REMEDY_API") or "http://127.0.0.1:7400").rstrip("/")
HOME = Path(os.environ.get("REMEDY_HOME") or (Path.home() / ".remedy"))


def _req(
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict | bytes | None = None,
    headers: dict | None = None,
    timeout: float = 12.0,
) -> tuple[int, Any, dict]:
    url = path if path.startswith("http") else f"{BASE}{path}"
    h = {"Accept": "application/json", **(headers or {})}
    data = None
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode("utf-8")
            h.setdefault("Content-Type", "application/json")
        else:
            data = body
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=h, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            resp.headers.get("Content-Type", "")
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else None
            except Exception:
                payload = raw[:500].decode("utf-8", errors="replace")
            return int(resp.status), payload, dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw = e.read() if hasattr(e, "read") else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {"error": str(e)}
        except Exception:
            payload = raw[:500].decode("utf-8", errors="replace")
        return int(e.code), payload, dict(e.headers or {})
    except Exception as e:
        return 0, {"error": str(e), "type": type(e).__name__}, {}


FINDINGS: list[dict[str, Any]] = []


def record(
    probe_id: str,
    title: str,
    *,
    severity: str,
    status: str,
    detail: str,
    evidence: Any = None,
) -> None:
    """status: blocked | open | info | error"""
    FINDINGS.append(
        {
            "id": probe_id,
            "title": title,
            "severity": severity,
            "status": status,
            "detail": detail,
            "evidence": evidence,
        }
    )
    mark = {
        "blocked": "OK ",
        "open": "OPEN",
        "info": "INFO",
        "error": "ERR ",
    }.get(status, "????")
    print(f"[{mark}] {severity:6} {probe_id}: {title} — {detail}")


def main() -> int:
    print(f"=== Remedy live red-team @ {BASE} ===\n")

    # --- A. Unauthenticated surface -----------------------------------------
    for path, expect_block in [
        ("/api/sessions", True),
        ("/api/settings", True),
        ("/api/providers", True),
        ("/api/files?path=C:/Windows/System32/drivers/etc/hosts", True),
        ("/api/workspace", True),
        ("/api/status", False),  # public
        ("/api/ping", False),
        ("/api/auth/local-bootstrap", False),  # loopback public when bootstrap on
    ]:
        code, body, _ = _req("GET", path)
        blocked = code in (401, 403)
        if expect_block:
            if blocked:
                record(
                    f"A-NOAUTH-{path.split('?')[0].replace('/', '_')}",
                    f"No-auth GET {path}",
                    severity="High",
                    status="blocked",
                    detail=f"HTTP {code}",
                    evidence={"status": code},
                )
            else:
                record(
                    f"A-NOAUTH-{path.split('?')[0].replace('/', '_')}",
                    f"No-auth GET {path}",
                    severity="Critical" if code == 200 else "High",
                    status="open",
                    detail=f"Expected 401/403, got {code}",
                    evidence={"status": code, "body": str(body)[:200]},
                )
        else:
            record(
                f"A-PUBLIC-{path.replace('/', '_')}",
                f"Public GET {path}",
                severity="Info",
                status="info" if code == 200 else "error",
                detail=f"HTTP {code} (public surface)",
                evidence={"status": code},
            )

    # Bootstrap: same-user token mint
    code, body, _ = _req("GET", "/api/auth/local-bootstrap")
    if code == 200 and isinstance(body, dict) and body.get("token"):
        record(
            "A-BOOTSTRAP",
            "HTTP local-bootstrap issues Bearer",
            severity="Med",
            status="open",
            detail="Any same-user loopback client can mint API token (owner-boundary)",
            evidence={"auth_required": body.get("auth_required"), "token_len": len(str(body.get("token")))},
        )
        token = str(body["token"])
    elif code == 403:
        record(
            "A-BOOTSTRAP",
            "HTTP local-bootstrap disabled",
            severity="Info",
            status="blocked",
            detail=str(body)[:200],
        )
        try:
            token = resolve_local_api_token(home=HOME, base=BASE)
        except Exception as e:
            record("A-TOKEN", "Could not resolve Bearer", severity="High", status="error", detail=str(e))
            _emit_summary()
            return 2
    else:
        record(
            "A-BOOTSTRAP",
            "Bootstrap unexpected",
            severity="Med",
            status="error",
            detail=f"HTTP {code}",
            evidence=body,
        )
        try:
            token = resolve_local_api_token(home=HOME, base=BASE)
        except Exception as e:
            record("A-TOKEN", "Could not resolve Bearer", severity="High", status="error", detail=str(e))
            _emit_summary()
            return 2

    # Wrong token
    code, _, _ = _req("GET", "/api/sessions", token="definitely-not-the-token-xxxx")
    if code == 401:
        record("A-BAD-TOKEN", "Invalid Bearer rejected", severity="High", status="blocked", detail="401")
    else:
        record(
            "A-BAD-TOKEN",
            "Invalid Bearer accepted?",
            severity="Critical",
            status="open",
            detail=f"HTTP {code}",
        )

    # Length-mismatch token (S-AUTH-06)
    code, body, _ = _req("GET", "/api/sessions", token="x")
    if code == 401:
        record("A-SHORT-TOKEN", "Short Bearer safe 401", severity="Low", status="blocked", detail="401")
    elif code >= 500:
        record(
            "A-SHORT-TOKEN",
            "Short Bearer 500s (compare_digest)",
            severity="Low",
            status="open",
            detail=f"HTTP {code}",
            evidence=body,
        )
    else:
        record("A-SHORT-TOKEN", "Short Bearer unexpected", severity="Med", status="open", detail=f"HTTP {code}")

    # --- B. Computer host unauth (loopback exempt) --------------------------
    for path, method in [
        ("/api/computer/host/status", "GET"),
        ("/api/computer/host/hello", "POST"),
        ("/api/computer/jobs/next", "GET"),
        ("/api/computer/ui/command", "GET"),
    ]:
        code, body, _ = _req(method, path, body={} if method == "POST" else None)
        if code == 200:
            record(
                f"B-HOST-{path.replace('/', '_')}",
                f"Unauth loopback {method} {path}",
                severity="High",
                status="open",
                detail="Computer host reachable without Bearer — expected blocked after S-AUTH-04",
                evidence={"status": code, "snippet": str(body)[:180]},
            )
        elif code in (401, 403):
            record(
                f"B-HOST-{path.replace('/', '_')}",
                f"Unauth {method} {path}",
                severity="High",
                status="blocked",
                detail=f"HTTP {code} (Bearer required on host/jobs/ui)",
            )
        else:
            record(
                f"B-HOST-{path.replace('/', '_')}",
                f"Unauth {method} {path}",
                severity="Info",
                status="info",
                detail=f"HTTP {code}",
                evidence=str(body)[:120],
            )

    # Capture should require auth (not under host prefix)
    code, body, _ = _req("POST", "/api/computer/capture", body={"label": "redteam"})
    if code in (401, 403):
        record(
            "B-CAPTURE-NOAUTH",
            "Unauth capture blocked",
            severity="High",
            status="blocked",
            detail=f"HTTP {code}",
        )
    elif code == 200:
        record(
            "B-CAPTURE-NOAUTH",
            "Unauth screenshot capture works",
            severity="High",
            status="open",
            detail="Any loopback process can screenshot without Bearer",
            evidence=str(body)[:120],
        )
    else:
        record(
            "B-CAPTURE-NOAUTH",
            "Unauth capture",
            severity="Med",
            status="info",
            detail=f"HTTP {code}",
            evidence=str(body)[:120],
        )

    # Spoof X-Forwarded-For — must NOT grant host access from "non-loopback" logic reverse
    # (client is still loopback; this checks we don't trust XFF for *widening* auth bypass)
    code, body, _ = _req(
        "GET",
        "/api/sessions",
        headers={"X-Forwarded-For": "8.8.8.8", "X-Real-IP": "8.8.8.8"},
    )
    if code == 401:
        record(
            "B-XFF-NOAUTH",
            "X-Forwarded-For does not bypass API auth",
            severity="High",
            status="blocked",
            detail="401 without Bearer even with XFF",
        )
    else:
        record(
            "B-XFF-NOAUTH",
            "XFF may confuse auth",
            severity="High",
            status="open",
            detail=f"HTTP {code}",
            evidence=str(body)[:100],
        )

    # --- C. Path jail / file API (with auth) --------------------------------
    sensitive_paths = [
        str(HOME / "auth" / "provider_keys.json"),
        str(HOME / "auth" / "local_api_token"),
        r"C:\Windows\System32\config\SAM",
        r"C:\Users\Administrator\Desktop\..\..\Windows\win.ini",
        str(Path.home() / "NTUSER.DAT"),
        "../../../Windows/System32/drivers/etc/hosts",
    ]
    for sp in sensitive_paths:
        q = urllib.parse.quote(sp, safe="")
        code, body, _ = _req("GET", f"/api/files?path={q}", token=token)
        text = str(body).lower() if body is not None else ""
        leaked = code == 200 and (
            "private_key" in text
            or "begin " in text
            or ("token" in text and "provider" in text)
            or "sk-" in text
        )
        if code in (400, 403, 404, 422) or (
            code == 200 and isinstance(body, dict) and body.get("error")
        ):
            record(
                "C-PATH-" + Path(sp).name[:40],
                f"files path jail: {sp[:60]}",
                severity="High",
                status="blocked",
                detail=f"HTTP {code}",
                evidence=str(body)[:160],
            )
        elif leaked:
            record(
                "C-PATH-" + Path(sp).name[:40],
                f"files LEAK: {sp[:60]}",
                severity="Critical",
                status="open",
                detail="Sensitive content returned",
                evidence=str(body)[:120],
            )
        elif code == 200:
            # may be legitimate workspace file or empty listing — flag for review
            record(
                "C-PATH-" + Path(sp).name[:40],
                f"files returned 200: {sp[:60]}",
                severity="Med",
                status="open",
                detail="Got 200 — review if content is sensitive",
                evidence=str(body)[:160],
            )
        else:
            record(
                "C-PATH-" + Path(sp).name[:40],
                f"files {sp[:50]}",
                severity="Info",
                status="info",
                detail=f"HTTP {code}",
                evidence=str(body)[:120],
            )

    # project scan jail (path is a *query* param, not JSON body)
    auth_q = urllib.parse.quote(str(HOME / "auth"), safe="")
    code, body, _ = _req(
        "POST",
        f"/api/projects/scan?path={auth_q}",
        token=token,
    )
    if code in (400, 403, 422) or (
        isinstance(body, dict)
        and (
            "error" in body
            or "not allowed" in str(body).lower()
            or "protected" in str(body).lower()
        )
    ):
        record(
            "C-SCAN-AUTH",
            "project scan of ~/.remedy/auth blocked",
            severity="High",
            status="blocked",
            detail=f"HTTP {code}",
            evidence=str(body)[:160],
        )
    elif code == 200:
        # Fail open only if response path actually under auth/
        scanned = ""
        if isinstance(body, dict):
            scanned = str(body.get("path") or "")
        if "auth" in scanned.replace("\\", "/").lower() and str(HOME / "auth") in scanned:
            record(
                "C-SCAN-AUTH",
                "project scan of auth dir open",
                severity="High",
                status="open",
                detail=f"scanned {scanned}",
                evidence=str(body)[:160],
            )
        else:
            record(
                "C-SCAN-AUTH",
                "project scan did not walk auth tree",
                severity="High",
                status="blocked",
                detail=f"HTTP {code} path={scanned[:80]}",
            )
    else:
        record(
            "C-SCAN-AUTH",
            "project scan auth",
            severity="Info",
            status="info",
            detail=f"HTTP {code}",
            evidence=str(body)[:120],
        )

    # --- D. Settings must not echo secrets ----------------------------------
    code, body, _ = _req("GET", "/api/settings", token=token)
    if code == 200 and isinstance(body, dict):
        blob = json.dumps(body)
        # Real leaks: long key-like values, PEM, not mere field names / help text
        import re

        leak_res = [
            re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
            re.compile(r"xai-[A-Za-z0-9_\-]{16,}", re.I),
            re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
            re.compile(r'"api_key"\s*:\s*"[^"]{12,}"'),
            re.compile(r'"client_secret"\s*:\s*"[^"]{8,}"'),
            re.compile(r'"refresh_token"\s*:\s*"[^"]{12,}"'),
            re.compile(r'"access_token"\s*:\s*"[^"]{12,}"'),
        ]
        bad = any(r.search(blob) for r in leak_res)
        if bad:
            record(
                "D-SETTINGS-LEAK",
                "Settings may echo secrets",
                severity="High",
                status="open",
                detail="Secret-like *values* in /api/settings",
            )
        else:
            record(
                "D-SETTINGS-LEAK",
                "Settings GET no raw secrets spotted",
                severity="High",
                status="blocked",
                detail="fingerprint/bool style payload",
            )
    else:
        record(
            "D-SETTINGS",
            "Settings GET",
            severity="Info",
            status="info",
            detail=f"HTTP {code}",
        )

    # providers must not echo keys
    code, body, _ = _req("GET", "/api/providers", token=token)
    if code == 200:
        blob = json.dumps(body) if not isinstance(body, str) else body
        if "sk-" in blob or "xai-" in blob.lower():
            record(
                "D-PROVIDERS-LEAK",
                "Providers list may leak key material",
                severity="High",
                status="open",
                detail="key-like substring",
            )
        else:
            record(
                "D-PROVIDERS-LEAK",
                "Providers list no raw key material",
                severity="High",
                status="blocked",
                detail=f"HTTP {code}",
            )

    # --- E. Webhooks unauth structure ---------------------------------------
    for path in (
        "/api/webhooks/teams",
        "/api/webhooks/whatsapp",
        "/api/webhooks/google_chat",
    ):
        code, body, _ = _req("POST", path, body={"text": "redteam-probe"})
        # Expect 401/403/400 not agent run
        if code in (401, 403, 400, 422, 415):
            record(
                f"E-WH-{path.split('/')[-1]}",
                f"Webhook {path} rejects junk",
                severity="Med",
                status="blocked",
                detail=f"HTTP {code}",
                evidence=str(body)[:120],
            )
        elif code == 200:
            record(
                f"E-WH-{path.split('/')[-1]}",
                f"Webhook {path} accepted unauth junk",
                severity="High",
                status="open",
                detail="200 without platform crypto",
                evidence=str(body)[:160],
            )
        else:
            record(
                f"E-WH-{path.split('/')[-1]}",
                f"Webhook {path}",
                severity="Info",
                status="info",
                detail=f"HTTP {code}",
                evidence=str(body)[:100],
            )

    # --- F. Unit-level SSRF + shell jail (no real internal network abuse) ---
    try:
        from urllib.parse import urlparse

        from remedy.core.agent_web_tools import _host_is_blocked, _pinned_fetch

        ssrf_hosts = [
            ("127.0.0.1", True),
            ("localhost", True),
            ("169.254.169.254", True),
            ("0.0.0.0", True),
            ("::1", True),
            ("10.0.0.1", True),
            ("192.168.1.1", True),
            ("example.com", False),  # public — host gate only (not full pin)
        ]
        for host, expect_block in ssrf_hosts:
            blocked_h = bool(_host_is_blocked(host))
            if expect_block and blocked_h:
                record(
                    f"F-SSRF-HOST-{host[:20]}",
                    f"SSRF host block {host}",
                    severity="High",
                    status="blocked",
                    detail="blocked",
                )
            elif expect_block and not blocked_h:
                record(
                    f"F-SSRF-HOST-{host[:20]}",
                    f"SSRF host NOT blocked {host}",
                    severity="High",
                    status="open",
                    detail="should be blocked",
                )
            elif not expect_block and not blocked_h:
                record(
                    f"F-SSRF-HOST-{host[:20]}",
                    f"Public host allowed {host}",
                    severity="Info",
                    status="info",
                    detail="not blocked at host gate",
                )
            else:
                record(
                    f"F-SSRF-HOST-{host[:20]}",
                    f"Public host blocked {host}",
                    severity="Info",
                    status="info",
                    detail="over-blocked (capability tradeoff)",
                )

        # Full pin fetch of loopback API — must fail closed
        try:
            _pinned_fetch(f"{BASE}/api/sessions", max_chars=1000, timeout=5.0)
            record(
                "F-SSRF-PIN-LOOPBACK",
                "pinned_fetch to local API",
                severity="Critical",
                status="open",
                detail="SSRF allowed fetch of local API",
            )
        except Exception as e:
            record(
                "F-SSRF-PIN-LOOPBACK",
                "pinned_fetch blocks loopback API",
                severity="High",
                status="blocked",
                detail=str(e)[:120],
            )

        # userinfo
        try:
            _pinned_fetch("http://user:pass@example.com/", max_chars=100, timeout=5.0)
            record(
                "F-SSRF-USERINFO",
                "userinfo URL accepted",
                severity="Med",
                status="open",
                detail="user:pass@ should fail closed",
            )
        except Exception as e:
            record(
                "F-SSRF-USERINFO",
                "userinfo URL blocked",
                severity="Med",
                status="blocked",
                detail=str(e)[:120],
            )
        _ = urlparse  # keep import used
    except Exception as e:
        record(
            "F-SSRF",
            "SSRF helpers failed",
            severity="Med",
            status="error",
            detail=f"{e}\n{traceback.format_exc()[:250]}",
        )

    # Shell write jail unit probes
    try:
        from remedy.core.shell_write_jail import check_shell_write_jail

        project = ROOT  # RemedyAI tree as fake project root
        write_roots = [project]
        attacks = [
            f'Set-Content -Path "{Path.home() / "Desktop" / "remedy_redteam_pwn.txt"}" -Value pwn',
            f'echo pwn > "{Path.home() / "Documents" / "remedy_redteam_pwn.txt"}"',
            r'python -c "open(r\'C:\\Users\\Public\\redteam_pwn.txt\',\'w\').write(\'x\')"',
            "powershell -enc SQBFAFgAIAAoAE4AZQB3AC0ATwBiAGoAZQBjAHQAIABOAGUAdAAuAFcAZQBiAEMAbABpAGUAbgB0ACkA",
            f'Copy-Item .\\README.md "{Path.home() / "Desktop" / "stolen_readme.md"}"',
        ]
        for i, cmd in enumerate(attacks):
            hit = check_shell_write_jail(
                cmd,
                write_roots=write_roots,
                cwd=project,
                project_bound=True,
            )
            if hit:
                record(
                    f"F-SHELL-{i}",
                    "Shell write jail blocks escape",
                    severity="High",
                    status="blocked",
                    detail=str(hit)[:120],
                )
            else:
                record(
                    f"F-SHELL-{i}",
                    "Shell write jail MISS",
                    severity="High",
                    status="open",
                    detail=f"allowed: {cmd[:80]}",
                )
    except Exception as e:
        record(
            "F-SHELL",
            "shell_write_jail import/run failed",
            severity="Med",
            status="error",
            detail=f"{e}\n{traceback.format_exc()[:300]}",
        )

    # Auth path resolve under full scope
    try:
        from remedy.core.security import SecurityError, refuse_protected_secret_path
        from remedy.core.workspace import resolve_under_roots

        auth_target = str(HOME / "auth" / "provider_keys.json")
        try:
            resolve_under_roots(
                auth_target,
                [str(Path.home())],
                access_scope="full",
            )
            record(
                "F-AUTH-RESOLVE",
                "auth path resolvable under full scope",
                severity="High",
                status="open",
                detail="resolve_under_roots allowed auth/**",
            )
        except Exception as e:
            record(
                "F-AUTH-RESOLVE",
                "auth path blocked even under full",
                severity="High",
                status="blocked",
                detail=str(e)[:160],
            )
        try:
            refuse_protected_secret_path(auth_target)
            record(
                "F-AUTH-REFUSE",
                "refuse_protected_secret_path no-op on auth",
                severity="High",
                status="open",
                detail="did not raise",
            )
        except Exception as e:
            record(
                "F-AUTH-REFUSE",
                "refuse_protected_secret_path blocks auth",
                severity="High",
                status="blocked",
                detail=str(e)[:120],
            )
        _ = SecurityError
    except Exception as e:
        record(
            "F-AUTH-RESOLVE",
            "auth resolve probe failed",
            severity="Med",
            status="error",
            detail=str(e)[:200],
        )

    # --- G. CORS star with auth ---------------------------------------------
    # Live server should not reflect Access-Control-Allow-Origin: * for API with credentials
    code, body, hdrs = _req(
        "OPTIONS",
        "/api/sessions",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    acao = hdrs.get("Access-Control-Allow-Origin") or hdrs.get("access-control-allow-origin")
    if acao == "*":
        record(
            "G-CORS-STAR",
            "CORS * reflected with auth API",
            severity="High",
            status="open",
            detail="Would enable browser steal via bootstrap",
            evidence=dict(hdrs),
        )
    else:
        record(
            "G-CORS-STAR",
            "CORS * not open",
            severity="Med",
            status="blocked",
            detail=f"ACAO={acao!r} OPTIONS={code}",
        )

    # --- H. Data at rest plaintext check (info) -----------------------------
    mem = HOME / "memory.db"
    tok_path = HOME / "auth" / "local_api_token"
    if mem.is_file():
        record(
            "H-MEMORY-DB",
            "memory.db present (plaintext SQLite expected)",
            severity="Med",
            status="info",
            detail=f"size={mem.stat().st_size}",
        )
    if tok_path.is_file():
        raw = tok_path.read_text(encoding="utf-8", errors="replace")[:80]
        if raw.lstrip().startswith("{"):
            record(
                "H-TOKEN-SEAL",
                "local_api_token appears sealed/JSON",
                severity="Med",
                status="blocked",
                detail="DPAPI-style envelope",
            )
        else:
            record(
                "H-TOKEN-SEAL",
                "local_api_token plaintext on disk",
                severity="Med",
                status="open",
                detail="readable by same user (ACL only)",
            )

    _emit_summary()
    open_high = [
        f
        for f in FINDINGS
        if f["status"] == "open" and f["severity"] in ("Critical", "High")
    ]
    return 1 if open_high else 0


def _emit_summary() -> None:
    counts = {"blocked": 0, "open": 0, "info": 0, "error": 0}
    for f in FINDINGS:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    print("\n=== SUMMARY ===")
    print(json.dumps(counts, indent=2))
    opens = [f for f in FINDINGS if f["status"] == "open"]
    if opens:
        print("\nOpen findings:")
        for f in opens:
            print(f"  - [{f['severity']}] {f['id']}: {f['title']} — {f['detail']}")
    out = HOME / "logs"
    out.mkdir(parents=True, exist_ok=True)
    report = ROOT / "docs" / "_redteam_live_results.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps(
            {
                "base": BASE,
                "ts": time.time(),
                "counts": counts,
                "findings": FINDINGS,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nWrote {report}")


if __name__ == "__main__":
    raise SystemExit(main())
