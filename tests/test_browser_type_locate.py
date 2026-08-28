"""Host type/type_text relocates by visible field text via __rmdyPick."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "desktop" / "src-tauri" / "src" / "browser_host.rs"

# Exact-token lock — "add" must not hit "address" / "happening" via includes.
SCORE_TOKEN = "const hit=use.filter(t=>nt.some(n=>n===t)).length;"


def _host() -> str:
    return HOST.read_text(encoding="utf-8")


def _score_body(src: str) -> str:
    start = src.find("window.__rmdyScore=function")
    end = src.find("window.__rmdyPick=function")
    assert start >= 0 and end > start
    return src[start:end]


def _locate_fn(src: str) -> str:
    start = src.find("fn type_locate_js(")
    end = src.find("fn resolve_point(")
    assert start >= 0 and end > start
    return src[start:end]


def test_host_type_sel_and_field_of_exist() -> None:
    src = _host()
    assert "window.__rmdyTypeSel=" in src
    assert "window.__rmdyFieldOf=function" in src
    assert "fn type_locate_js(" in src


def test_rmdy_score_tokens_stay_exact() -> None:
    src = _host()
    assert src.count("window.__rmdyScore=function") == 1
    assert SCORE_TOKEN in src
    body = _score_body(src)
    token_region = body.split("const hit=")[1].split("let ctx")[0]
    assert "n===t" in token_region
    assert ".includes(" not in token_region


def test_empty_type_locate_keeps_active_element() -> None:
    fn = _locate_fn(_host())
    assert "document.activeElement||document.body" in fn
    assert "__rmdyTypeSel" in fn


def test_type_locate_uses_pick_like_click_text() -> None:
    fn = _locate_fn(_host())
    assert "window.__rmdyPick(q, sel)" in fn
    assert "window.__rmdyTypeSel" in fn
    assert "window.__rmdyFieldOf" in fn
    assert "no-match:" in fn
    assert "missing-ref:" in fn
    assert "bestS<40" in fn


def test_type_and_type_text_call_type_locate_js() -> None:
    src = _host()
    assert src.count("type_locate_js(") >= 3  # def + synthetic + trusted
    assert '"type" | "type_text"' in src
    # query/label/hint ride in key for the type job
    assert '"query"' in src and '"label"' in src
    assert 'else if action == "type"' in src or 'action == "type" || action == "type_text"' in src


def test_trusted_type_focus_relocates_by_query() -> None:
    src = _host()
    assert "key_cdp.as_deref()" in src
    assert "ref_cdp.as_deref()" in src
    # both synthetic type JS and trusted focus go through type_locate_js
    assert src.count("type_locate_js(") >= 3
    assert "ok:trusted-type" in src


def test_routing_hints_are_not_field_queries() -> None:
    """hint=browser/grove is routing, not a visible field label."""
    src = _host()
    fn = _locate_fn(src)
    if "const ROUTING" in fn or "ROUTING:" in fn:
        assert '"browser"' in fn
        assert '"grove"' in fn
        assert "eq_ignore_ascii_case" in fn
