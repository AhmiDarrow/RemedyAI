"""Mission store and advance."""

from remedy.core.mission import (
    MissionStore,
    advance_step,
    create_mission,
    mission_summary,
)


def test_create_and_advance(tmp_path):
    m = create_mission(
        "Ship feature",
        steps=["Write code", "Run tests", "Docs"],
        verify_command="pytest -q",
        home=tmp_path,
    )
    assert m.status == "active"
    assert m.steps[0].status == "active"
    m = advance_step(m, status="done")
    store = MissionStore(tmp_path)
    store.save(m)
    loaded = store.latest()
    assert loaded is not None
    assert loaded.steps[0].status == "done"
    assert loaded.steps[1].status == "active"
    text = mission_summary(loaded)
    assert "Ship feature" in text
    assert "Verify:" in text
