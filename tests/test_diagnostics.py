"""Health diagnostics snapshot (GET /api/diagnostics collector)."""

from __future__ import annotations

import pytest

from remedy.interfaces.diagnostics import (
    _derive_issues,
    _human_uptime,
    _overall_status,
    collect_diagnostics,
)


def test_human_uptime():
    assert _human_uptime(45) == "45s"
    assert "m" in _human_uptime(125)
    assert "h" in _human_uptime(3700)


def test_derive_issues_rmb_missing_model():
    issues = _derive_issues(
        {
            "rmb": {
                "enabled": True,
                "model_present": False,
                "not_ready_hint": "place gguf",
            },
            "hardware": {"memory": {}, "gpu": {"gpus": []}, "disks": []},
            "providers": {"active": {}, "providers": []},
            "remedy": {"home_disk": {}},
        }
    )
    assert any(i["area"] == "rmb" for i in issues)
    assert _overall_status(issues, {}) in ("degraded", "error", "ok")


@pytest.mark.asyncio
async def test_collect_diagnostics_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    snap = await collect_diagnostics(
        runtime=None,
        gateway=None,
        memory=None,
        probe_providers=False,
    )
    assert "overall" in snap
    assert snap["overall"] in ("ok", "degraded", "error")
    assert "remedy" in snap
    assert "rmb" in snap
    assert "hardware" in snap
    assert "providers" in snap
    assert "computer" in snap
    assert "vision" in snap
    assert isinstance(snap.get("issues"), list)
    assert "version" in (snap.get("remedy") or {})
