
def test_cli_bare_subcommands_require_action():
    """Bare group commands must not silently no-op — argparse requires a subcommand.

    settings/computer intentionally default (show/status); other groups print usage.
    """
    import pytest

    from remedy.interfaces.cli import build_parser

    parser = build_parser()
    # These used to exit 0 with empty output — unusable for discovery.
    for cmd in (
        "session",
        "skill",
        "memory",
        "config",
        "gateway",
        "tool",
        "user",
        "learn",
        "handoff",
        "auth",
        "mcp",
        "desktop",
    ):
        with pytest.raises(SystemExit) as ei:
            parser.parse_args([cmd])
        assert ei.value.code == 2

    # Sensible defaults still work without a subcommand.
    settings = parser.parse_args(["settings"])
    assert getattr(settings, "settings_cmd", None) in (None, "show")
    computer = parser.parse_args(["computer"])
    assert getattr(computer, "computer_cmd", None) in (None, "status")


def test_cli_skill_list_hides_learned_probation(tmp_path, capsys):
    """remedy skill list default hides auto-learned non-active skills."""
    import asyncio
    from types import SimpleNamespace

    from remedy.interfaces.cli import _cmd_skill
    from remedy.models import Skill, SkillManifest, SkillStatus
    from remedy.skills.registry import SkillRegistry

    reg = SkillRegistry()
    reg.register(
        Skill(
            manifest=SkillManifest(
                name="project-etiquette",
                description="Ship gate chain",
                status=SkillStatus.ACTIVE,
            ),
            instructions="# pe\n",
        )
    )
    reg.register(
        Skill(
            manifest=SkillManifest(
                name="file_read-bash_exec-chain",
                description="tool chain noise",
                status=SkillStatus.DISCOVERED,
                metadata={"auto_generated": True},
            ),
            instructions="# noise\n",
        )
    )
    reg.register(
        Skill(
            manifest=SkillManifest(
                name="learned-winner",
                description="promoted",
                status=SkillStatus.ACTIVE,
                metadata={"auto_generated": True},
            ),
            instructions="# win\n",
        )
    )

    # Patch discover_defaults so list uses our registry only
    original = SkillRegistry.discover_defaults

    def _noop(self, *a, **k):
        return 0

    SkillRegistry.discover_defaults = _noop  # type: ignore[method-assign]
    try:
        # Inject skills by patching SkillRegistry() construction inside cmd
        # Easier: call list filter path via registry after discover
        from remedy.interfaces import cli as cli_mod

        real_reg_cls = cli_mod.SkillRegistry

        class _Fixed(SkillRegistry):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                for s in reg.skills:
                    self.register(s)

            def discover_defaults(self, *a, **k):
                return 0

        cli_mod.SkillRegistry = _Fixed  # type: ignore[misc]
        try:
            args = SimpleNamespace(skill_cmd="list", all=False, learned=False)
            asyncio.run(_cmd_skill(args))
            out = capsys.readouterr().out
            assert "project-etiquette" in out
            assert "learned-winner" in out
            assert "file_read-bash_exec-chain" not in out
            assert "hidden" in out.lower() or "probation" in out.lower()

            args_all = SimpleNamespace(skill_cmd="list", all=True, learned=False)
            asyncio.run(_cmd_skill(args_all))
            out_all = capsys.readouterr().out
            assert "file_read-bash_exec-chain" in out_all

            args_learned = SimpleNamespace(skill_cmd="list", all=False, learned=True)
            asyncio.run(_cmd_skill(args_learned))
            out_l = capsys.readouterr().out
            assert "file_read-bash_exec-chain" in out_l
            assert "learned-winner" in out_l
            assert "project-etiquette" not in out_l
        finally:
            cli_mod.SkillRegistry = real_reg_cls  # type: ignore[misc]
    finally:
        SkillRegistry.discover_defaults = original  # type: ignore[method-assign]


def test_partner_status_session_id_scopes_metabolism(tmp_path):
    """GET /api/partner/status?session_id= scopes lean metabolism to that tab."""
    import asyncio

    from fastapi.testclient import TestClient

    from remedy.core.metabolism.decision import reset_decision_tracker
    from remedy.core.metabolism.evidence import reset_evidence_ledger
    from remedy.core.session_quality import get_session_quality, reset_session_quality
    from remedy.interfaces.api import create_app
    from remedy.memory.store import MemoryStore

    sid_a = "status_sess_a"
    sid_b = "status_sess_b"
    for s in (sid_a, sid_b):
        reset_session_quality(s)
        reset_evidence_ledger(s)
        reset_decision_tracker(s)

    get_session_quality(sid_a).record_metabolism(
        tier=2, evidence_units=7, decision_units=2
    )
    get_session_quality(sid_b).record_metabolism(
        tier=1, evidence_units=1, decision_units=0
    )

    async def _init():
        store = MemoryStore(str(tmp_path / "mem_status.db"))
        await store.initialize()
        return store

    store = asyncio.run(_init())
    rt = type(
        "RT",
        (),
        {
            "skills": type("S", (), {"count": 0, "skills": []})(),
            "_session_id": sid_b,  # runtime last-touch is B
            "_streaming_sessions": set(),
            "list_tasks": lambda self=None: [],
        },
    )()
    app = create_app(runtime=rt, memory=store, api_key="")
    with TestClient(app) as client:
        # Focused tab A must not inherit B's counters
        r = client.get(f"/api/partner/status?session_id={sid_a}")
        assert r.status_code == 200
        data = r.json()
        assert data.get("session_id") == sid_a
        meta = data.get("metabolism") or {}
        assert meta.get("lean") is True
        q = data.get("session_quality") or {}
        qm = q.get("metabolism") or {}
        assert int(qm.get("evidence_units") or 0) == 7
        assert int(qm.get("last_tier") or 0) == 2

        r2 = client.get(f"/api/partner/status?session_id={sid_b}")
        data2 = r2.json()
        assert data2.get("session_id") == sid_b
        qm2 = (data2.get("session_quality") or {}).get("metabolism") or {}
        assert int(qm2.get("evidence_units") or 0) == 1

    for s in (sid_a, sid_b):
        reset_session_quality(s)
