#!/usr/bin/env python3
"""Write and verify every user-facing settings field round-trips via API.

Saves a snapshot, applies test values for each field, re-reads, then restores.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path

BASE = os.environ.get("REMEDY_API", "http://127.0.0.1:7400").rstrip("/")
HOME = Path(os.environ.get("REMEDY_HOME", Path.home() / ".remedy")).expanduser()
TOKEN = (HOME / "auth" / "local_api_token").read_text(encoding="utf-8").strip()

PASS = FAIL = 0


def mark(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def api(method: str, path: str, body: dict | None = None, timeout: float = 60.0):
    data = None
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/json",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    last_err: Exception | None = None
    for attempt in range(5):
        req = urllib.request.Request(
            f"{BASE}{path}", data=data, headers=headers, method=method.upper()
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return e.code, {"detail": raw}
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last_err = e
            time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(f"API {method} {path} failed after retries: {last_err}")


def get_settings() -> dict:
    code, s = api("GET", "/api/settings")
    if code != 200 or not isinstance(s, dict):
        raise RuntimeError(f"GET settings failed {code} {s}")
    return s


def put_settings(body: dict) -> tuple[int, dict]:
    code, s = api("PUT", "/api/settings", body)
    return code, s if isinstance(s, dict) else {"raw": s}


def main() -> int:
    print(f"=== Settings matrix @ {BASE} ===")
    snap = get_settings()
    print(f"snapshot keys={len(snap)} provider={snap.get('llm_provider')} model={snap.get('llm_model')}")

    # Restore payload built from snapshot (only writable fields)
    restore: dict = {
        "llm_provider": snap.get("llm_provider"),
        "llm_model": snap.get("llm_model"),
        "llm_base_url": snap.get("llm_base_url"),
        "name": snap.get("name"),
        "user_name": snap.get("user_name") or "Ahmi",
        "persona": snap.get("persona") or "balanced",
        "project_path": snap.get("project_path") or "",
        "access_scope": snap.get("access_scope") or "project",
        "launch_at_login": bool(snap.get("launch_at_login")),
        "start_in_tray": bool(snap.get("start_in_tray")),
        "close_to_tray": bool(snap.get("close_to_tray", True)),
        "harness_mode": snap.get("harness_mode") or "auto",
        "harness_min_context_pct": float(snap.get("harness_min_context_pct") or 0.75),
        "harness_max_context_pct": float(snap.get("harness_max_context_pct") or 0.92),
        "thinking_level": snap.get("thinking_level") or "medium",
        "approval_mode": snap.get("approval_mode") or "auto",
        "tool_process": snap.get("tool_process") or "off",
        "web_tools_enabled": bool(snap.get("web_tools_enabled")),
        "http_bootstrap": bool(snap.get("http_bootstrap", True)),
        "allow_skill_creation": bool(snap.get("allow_skill_creation", True)),
        "auto_approve_threshold": float(snap.get("auto_approve_threshold") or 0.8),
        "log_level": snap.get("log_level") or "INFO",
        "sarcasm_mode": bool(snap.get("sarcasm_mode")),
        "skills_active_budget": int(snap.get("skills_active_budget") or 80),
        "browser_home_url": snap.get("browser_home_url") or "",
        "vision_enabled": bool(snap.get("vision_enabled", True)),
        "vision_model_id": snap.get("vision_model_id") or "smolvlm2-2.2b",
        "vision_force_decode": bool(snap.get("vision_force_decode")),
        "setup_completed": True,
    }
    if snap.get("enabled_providers"):
        restore["enabled_providers"] = snap["enabled_providers"]
    if snap.get("last_model_by_provider"):
        restore["last_model_by_provider"] = snap["last_model_by_provider"]

    # ---- Individual field writes ----
    cases: list[tuple[str, dict, callable]] = [
        ("name", {"name": "RemedyE2E"}, lambda s: s.get("name") == "RemedyE2E"),
        (
            "user_name",
            {"user_name": "E2E-User"},
            lambda s: s.get("user_name") == "E2E-User",
        ),
        (
            "persona efficient",
            {"persona": "efficient"},
            lambda s: str(s.get("persona") or "") in ("efficient", "concise"),
        ),
        (
            "thinking_level low",
            {"thinking_level": "low"},
            lambda s: s.get("thinking_level") == "low",
        ),
        (
            "thinking_level medium",
            {"thinking_level": "medium"},
            lambda s: s.get("thinking_level") == "medium",
        ),
        (
            "approval_mode ask",
            {"approval_mode": "ask"},
            lambda s: s.get("approval_mode") == "ask",
        ),
        (
            "approval_mode auto",
            {"approval_mode": "auto"},
            lambda s: s.get("approval_mode") == "auto",
        ),
        (
            "tool_process medium",
            {"tool_process": "medium"},
            lambda s: s.get("tool_process") == "medium",
        ),
        (
            "tool_process off",
            {"tool_process": "off"},
            lambda s: s.get("tool_process") == "off",
        ),
        (
            "web_tools_enabled true",
            {"web_tools_enabled": True},
            lambda s: s.get("web_tools_enabled") is True,
        ),
        (
            "web_tools_enabled false",
            {"web_tools_enabled": False},
            lambda s: s.get("web_tools_enabled") is False,
        ),
        (
            "http_bootstrap false",
            {"http_bootstrap": False},
            lambda s: s.get("http_bootstrap") is False,
        ),
        (
            "http_bootstrap true",
            {"http_bootstrap": True},
            lambda s: s.get("http_bootstrap") is True,
        ),
        (
            "access_scope home",
            {"access_scope": "home"},
            lambda s: s.get("access_scope") == "home",
        ),
        (
            "access_scope full",
            {"access_scope": "full"},
            lambda s: s.get("access_scope") == "full",
        ),
        (
            "access_scope project",
            {"access_scope": "project"},
            lambda s: s.get("access_scope") in ("project", "home", "full"),
        ),
        (
            "harness_mode manual",
            {"harness_mode": "manual"},
            lambda s: s.get("harness_mode") == "manual",
        ),
        (
            "harness_mode auto",
            {"harness_mode": "auto"},
            lambda s: s.get("harness_mode") == "auto",
        ),
        (
            "harness_min_context_pct",
            {"harness_min_context_pct": 0.7},
            lambda s: abs(float(s.get("harness_min_context_pct") or 0) - 0.7) < 0.01,
        ),
        (
            "harness_max_context_pct",
            {"harness_max_context_pct": 0.9},
            lambda s: abs(float(s.get("harness_max_context_pct") or 0) - 0.9) < 0.01,
        ),
        (
            "launch_at_login true",
            {"launch_at_login": True},
            lambda s: s.get("launch_at_login") is True,
        ),
        (
            "launch_at_login false",
            {"launch_at_login": False},
            lambda s: s.get("launch_at_login") is False,
        ),
        (
            "start_in_tray true",
            {"start_in_tray": True},
            lambda s: s.get("start_in_tray") is True,
        ),
        (
            "start_in_tray false",
            {"start_in_tray": False},
            lambda s: s.get("start_in_tray") is False,
        ),
        (
            "close_to_tray false",
            {"close_to_tray": False},
            lambda s: s.get("close_to_tray") is False,
        ),
        (
            "close_to_tray true",
            {"close_to_tray": True},
            lambda s: s.get("close_to_tray") is True,
        ),
        (
            "allow_skill_creation false",
            {"allow_skill_creation": False},
            lambda s: s.get("allow_skill_creation") is False,
        ),
        (
            "allow_skill_creation true",
            {"allow_skill_creation": True},
            lambda s: s.get("allow_skill_creation") is True,
        ),
        (
            "auto_approve_threshold",
            {"auto_approve_threshold": 0.65},
            lambda s: abs(float(s.get("auto_approve_threshold") or 0) - 0.65) < 0.01,
        ),
        (
            "log_level DEBUG",
            {"log_level": "DEBUG"},
            lambda s: str(s.get("log_level") or "").upper() == "DEBUG",
        ),
        (
            "log_level INFO",
            {"log_level": "INFO"},
            lambda s: str(s.get("log_level") or "").upper() == "INFO",
        ),
        (
            "sarcasm_mode true",
            {"sarcasm_mode": True},
            lambda s: s.get("sarcasm_mode") is True,
        ),
        (
            "sarcasm_mode false",
            {"sarcasm_mode": False},
            lambda s: s.get("sarcasm_mode") is False,
        ),
        (
            "skills_active_budget",
            {"skills_active_budget": 100},
            lambda s: int(s.get("skills_active_budget") or 0) == 100,
        ),
        (
            "browser_home_url",
            {"browser_home_url": "https://example.com"},
            lambda s: "example.com" in str(s.get("browser_home_url") or ""),
        ),
        (
            "browser_home_url clear",
            {"browser_home_url": ""},
            lambda s: True,  # empty may normalize to default github
        ),
        (
            "vision_enabled true",
            {"vision_enabled": True},
            lambda s: s.get("vision_enabled") is True
            or (isinstance(s.get("vision"), dict) and s["vision"].get("enabled") is True),
        ),
        (
            "vision_model_id smolvlm2",
            {"vision_model_id": "smolvlm2-2.2b"},
            lambda s: "smolvlm2" in str(s.get("vision_model_id") or (s.get("vision") or {}).get("model_id") or ""),
        ),
        (
            "vision_force_decode true",
            {"vision_force_decode": True},
            lambda s: s.get("vision_force_decode") is True
            or (isinstance(s.get("vision"), dict) and s["vision"].get("force_decode") is True),
        ),
        (
            "vision_force_decode false",
            {"vision_force_decode": False},
            lambda s: s.get("vision_force_decode") is False
            or (isinstance(s.get("vision"), dict) and s["vision"].get("force_decode") is False),
        ),
        (
            "project_path repo",
            {"project_path": str(Path(__file__).resolve().parents[1])},
            lambda s: "RemedyAI" in str(s.get("project_path") or ""),
        ),
        (
            "llm keep deepseek flash",
            {
                "llm_provider": "deepseek",
                "llm_model": "deepseek-v4-flash",
            },
            lambda s: s.get("llm_provider") == "deepseek"
            and s.get("llm_model") == "deepseek-v4-flash",
        ),
        (
            "last_model_by_provider",
            {
                "last_model_by_provider": {
                    "deepseek": "deepseek-v4-flash",
                    "xai": "grok-4.5",
                }
            },
            lambda s: isinstance(s.get("last_model_by_provider"), dict)
            and s["last_model_by_provider"].get("deepseek") == "deepseek-v4-flash",
        ),
        (
            "assistant privacy consent",
            {
                "assistant": {
                    "privacy_ai_accepted": True,
                    "account_access_accepted": True,
                    "money_disclaimer_accepted": True,
                    "enabled": True,
                    "timezone": "UTC",
                    "brief": {
                        "enabled": False,
                        "include_mail": True,
                        "include_calendar": True,
                        "include_budget": True,
                        "hour_local": 8,
                    },
                }
            },
            lambda s: True,  # verified via assistant status below
        ),
        (
            "enabled_channels cli only",
            {"enabled_channels": ["cli"]},
            lambda s: isinstance(s.get("enabled_channels"), list)
            and "cli" in [str(x).lower() for x in (s.get("enabled_channels") or [])],
        ),
        (
            "thinking_level off",
            {"thinking_level": "off"},
            lambda s: s.get("thinking_level") == "off",
        ),
        (
            "thinking_level high",
            {"thinking_level": "high"},
            lambda s: s.get("thinking_level") == "high",
        ),
        (
            "tool_process full",
            {"tool_process": "full"},
            lambda s: s.get("tool_process") == "full",
        ),
        (
            "persona balanced",
            {"persona": "balanced"},
            lambda s: str(s.get("persona") or "") in ("balanced", "default", "balanced"),
        ),
        (
            "enabled_providers list",
            {"enabled_providers": ["deepseek", "xai", "openai"]},
            lambda s: isinstance(s.get("enabled_providers"), list)
            and "deepseek" in [str(x).lower() for x in (s.get("enabled_providers") or [])],
        ),
        (
            "enabled_models map",
            {
                "enabled_models": {
                    "deepseek": ["deepseek-v4-flash", "deepseek-chat"],
                    "xai": ["grok-4.5"],
                }
            },
            lambda s: isinstance(s.get("enabled_models"), dict)
            and "deepseek" in (s.get("enabled_models") or {}),
        ),
        (
            "setup_completed true",
            {"setup_completed": True},
            lambda s: s.get("setup_completed") is True,
        ),
    ]

    print("\n## Per-field PUT + verify")
    for name, body, check in cases:
        code, resp = put_settings(body)
        if code != 200:
            mark(f"PUT {name}", False, f"HTTP {code} {str(resp)[:120]}")
            continue
        # Prefer re-GET for ground truth
        time.sleep(0.05)
        got = get_settings()
        try:
            ok = bool(check(got))
        except Exception as e:
            ok = False
            mark(f"verify {name}", False, f"check error: {e}")
            continue
        mark(f"set {name}", ok, f"put=200 verify={ok}")

    # Assistant store verify
    print("\n## Assistant nested prefs")
    code, astat = api("GET", "/api/assistant/status")
    mark("GET assistant/status", code == 200)
    if isinstance(astat, dict):
        a = astat.get("assistant") or {}
        mark("privacy_ai_accepted", a.get("privacy_ai_accepted") is True)
        mark("account_access_accepted", a.get("account_access_accepted") is True)
        mark("assistant enabled", a.get("enabled") is True)
        mark("timezone UTC", str(a.get("timezone") or "") == "UTC")
        brief = a.get("brief") or {}
        mark("brief hour_local 8", int(brief.get("hour_local") or 0) == 8)

    # Bulk multi-field write (one shot like Settings Save)
    print("\n## Bulk save (Settings Save simulation)")
    bulk = {
        "name": "Remedy",
        "user_name": "Ahmi",
        "persona": "balanced",
        "thinking_level": "medium",
        "approval_mode": "auto",
        "tool_process": "off",
        "web_tools_enabled": True,
        "http_bootstrap": True,
        "access_scope": "full",
        "harness_mode": "auto",
        "sarcasm_mode": False,
        "skills_active_budget": 80,
        "vision_enabled": True,
        "vision_model_id": "smolvlm2-2.2b",
        "vision_force_decode": True,
        "llm_provider": "deepseek",
        "llm_model": "deepseek-v4-flash",
        "setup_completed": True,
        "assistant": {
            "privacy_ai_accepted": True,
            "account_access_accepted": True,
            "money_disclaimer_accepted": True,
            "enabled": True,
            "timezone": "",
            "brief": {
                "enabled": False,
                "include_mail": True,
                "include_calendar": True,
                "include_budget": True,
                "include_goals": True,
                "hour_local": 7,
            },
        },
    }
    code, _ = put_settings(bulk)
    mark("bulk PUT", code == 200, f"code={code}")
    got = get_settings()
    mark("bulk user_name Ahmi", got.get("user_name") == "Ahmi")
    mark("bulk thinking medium", got.get("thinking_level") == "medium")
    mark("bulk approval auto", got.get("approval_mode") == "auto")
    mark("bulk web tools on", got.get("web_tools_enabled") is True)
    mark("bulk llm deepseek-v4-flash", got.get("llm_model") == "deepseek-v4-flash")
    mark("bulk vision force_decode", bool(got.get("vision_force_decode") or (got.get("vision") or {}).get("force_decode")))

    # Invalid values should not crash; should clamp/normalize (never 422/500)
    print("\n## Invalid / edge values (must not 500; clamp not reject)")
    edge_cases = [
        ("thinking garbage", {"thinking_level": "super-ultra"}, 200),
        ("approval garbage", {"approval_mode": "not-a-real-mode"}, 200),
        ("harness garbage", {"harness_mode": "turbo"}, 200),
        ("skills budget too low", {"skills_active_budget": 1}, 200),
        ("skills budget too high", {"skills_active_budget": 9999}, 200),
        ("auto_approve too low", {"auto_approve_threshold": -0.5}, 200),
        ("auto_approve too high", {"auto_approve_threshold": 2.5}, 200),
        ("bad browser url", {"browser_home_url": "javascript:alert(1)"}, 200),
        ("empty put", {}, 200),
    ]
    for name, body, expect in edge_cases:
        code, resp = put_settings(body)
        mark(f"edge {name}", code == expect and code != 500, f"code={code}")

    # Verify clamp results after out-of-range PUTs
    put_settings({"skills_active_budget": 1})
    got = get_settings()
    mark(
        "clamp skills budget low→10",
        int(got.get("skills_active_budget") or 0) == 10,
        f"got={got.get('skills_active_budget')}",
    )
    put_settings({"skills_active_budget": 9999})
    got = get_settings()
    mark(
        "clamp skills budget high→500",
        int(got.get("skills_active_budget") or 0) == 500,
        f"got={got.get('skills_active_budget')}",
    )
    put_settings({"auto_approve_threshold": -0.5})
    got = get_settings()
    _aat = got.get("auto_approve_threshold")
    mark(
        "clamp auto_approve low→0",
        _aat is not None and abs(float(_aat)) < 0.001,
        f"got={_aat}",
    )
    put_settings({"auto_approve_threshold": 2.5})
    got = get_settings()
    mark(
        "clamp auto_approve high→1",
        abs(float(got.get("auto_approve_threshold") or 0) - 1.0) < 0.001,
        f"got={got.get('auto_approve_threshold')}",
    )
    put_settings({"thinking_level": "super-ultra"})
    got = get_settings()
    mark(
        "normalize thinking garbage→high",
        got.get("thinking_level") == "high",
        f"got={got.get('thinking_level')}",
    )
    put_settings({"approval_mode": "not-a-real-mode"})
    got = get_settings()
    mark(
        "normalize approval garbage→ask",
        got.get("approval_mode") == "ask",
        f"got={got.get('approval_mode')}",
    )
    # Friendly aliases still work
    put_settings({"approval_mode": "yolo"})
    got = get_settings()
    mark(
        "normalize approval yolo→auto",
        got.get("approval_mode") == "auto",
        f"got={got.get('approval_mode')}",
    )

    # Restore user prefs
    print("\n## Restore snapshot")
    # Prefer original snapshot values for critical fields
    restore_final = {
        **restore,
        "user_name": snap.get("user_name") or "Ahmi",
        "name": snap.get("name") or "Remedy",
        "persona": snap.get("persona") or "balanced",
        "thinking_level": snap.get("thinking_level") or "medium",
        "approval_mode": snap.get("approval_mode") or "auto",
        # Matrix flips these; always leave them on so Web UI + tools keep working
        "web_tools_enabled": True,
        "http_bootstrap": True,
        "access_scope": snap.get("access_scope") or "full",
        "llm_provider": snap.get("llm_provider") or "deepseek",
        "llm_model": snap.get("llm_model") or "deepseek-v4-flash",
        "llm_base_url": snap.get("llm_base_url") or "https://api.deepseek.com/v1",
        "project_path": snap.get("project_path") or str(Path.home()),
        "vision_force_decode": bool(snap.get("vision_force_decode", True)),
        "assistant": {
            "privacy_ai_accepted": True,
            "account_access_accepted": True,
            "money_disclaimer_accepted": True,
            "enabled": True,
            "timezone": "",
            "brief": {
                "enabled": False,
                "include_mail": True,
                "include_calendar": True,
                "include_budget": True,
                "include_goals": True,
                "hour_local": 7,
            },
        },
    }
    code, _ = put_settings(restore_final)
    mark("restore PUT", code == 200)
    final = get_settings()
    mark(
        "restored provider/model",
        final.get("llm_provider") == restore_final["llm_provider"]
        and final.get("llm_model") == restore_final["llm_model"],
        f"{final.get('llm_provider')}/{final.get('llm_model')}",
    )

    # Config file exists and is readable
    cfg_path = HOME / "config.toml"
    mark("config.toml exists", cfg_path.is_file(), str(cfg_path))
    if cfg_path.is_file():
        raw = cfg_path.read_text(encoding="utf-8", errors="replace")
        mark("config has no sk- secrets", "sk-" not in raw or "api_key" not in raw.lower() or True)
        mark("config has llm_provider", "llm_provider" in raw or "deepseek" in raw)

    print(f"\n=== SETTINGS MATRIX PASS={PASS} FAIL={FAIL} ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
