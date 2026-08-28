"""Host type/type_text relocates by visible label via __rmdyPick."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "desktop" / "src-tauri" / "src" / "browser_host.rs"
SPA = ROOT / "desktop" / "src" / "hooks" / "useComputerHost.ts"


def _host() -> str:
    return HOST.read_text(encoding="utf-8")


def test_score_body_keeps_exact_token_scoring() -> None:
    t = _host()
    start = t.index("window.__rmdyScore=function(el,q){")
    end = t.index("window.__rmdyPick=function(q, sel){", start)
    body = t[start:end]
    assert "Exact tokens only" in body
    assert "n===t" in body
    assert "name.includes(q)" in body


def test_type_sel_covers_editable_fields_and_labels() -> None:
    t = _host()
    i = t.index("window.__rmdyTypeSel=")
    sel = t[i : i + 700]
    assert "textarea" in sel
    assert "contenteditable" in sel
    assert "label" in sel
    assert "window.__rmdyFieldOf=function(el)" in t
    assert "window.__rmdyIsField=function(el)" in t


def test_type_text_uses_type_locate_js() -> None:
    t = _host()
    assert "fn type_locate_js(" in t
    arm = t[t.index('"type" | "type_text" => {') :][:2200]
    assert "type_locate_js" in arm
    assert "if let Some(rf) = r#ref.clone()" not in arm


def test_type_locate_picks_then_maps_label_to_field() -> None:
    t = _host()
    fn = t[t.index("fn type_locate_js") : t.index("mod type_locate_tests")]
    assert "__rmdyPick" in fn
    assert "__rmdyTypeSel" in fn
    assert "__rmdyFieldOf" in fn
    assert "missing-ref" in fn
    assert "no-match" in fn
    assert "if(!el && q)" in fn


def test_trusted_type_focus_uses_type_locate() -> None:
    t = _host()
    i = t.index('if matches!(act.as_str(), "type" | "type_text")')
    chunk = t[i : i + 2500]
    assert "type_locate_js" in chunk
    assert "rm-editable" in chunk


def test_handle_job_type_reads_query_label_hint() -> None:
    t = _host()
    i = t.index('if matches!(action.as_str(), "type" | "key" | "scroll" | "drag" | "select")')
    chunk = t[i : i + 1800]
    assert 'action == "type"' in chunk
    assert '"query"' in chunk
    assert '"label"' in chunk
    assert '"hint"' in chunk


def test_spa_passes_hint_query_for_type() -> None:
    t = SPA.read_text(encoding="utf-8")
    assert "action === 'type' || action === 'select'" in t
    assert "p.query" in t
    assert "p.hint" in t
