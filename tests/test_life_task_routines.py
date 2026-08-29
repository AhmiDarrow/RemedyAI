"""Goal + URL + vault expand into drive steps without model JSON."""

from __future__ import annotations

from remedy.core.life_task_drive import drive_life_task, step_is_checkpoint
from remedy.core.life_task_routines import expand_recipe, infer_recipe


def test_infer_recipe_from_plain_goal():
    assert infer_recipe("buy milk on instacart") == "shop"
    assert infer_recipe("sign in to gmail") == "sign_in"
    assert infer_recipe("search for elephants") == "search"
    assert infer_recipe("open https://example.com") == "open"
    assert infer_recipe("fill the checkout form") == "fill"


def test_shop_plan_stops_at_place_order():
    steps = expand_recipe(goal="buy milk on instacart")
    urls = [s.get("url") for s in steps if s.get("action") == "navigate"]
    assert urls and "instacart" in str(urls[0])
    titles = " ".join(s.get("title") or "" for s in steps)
    assert "milk" in titles.lower()
    last = steps[-1]
    assert step_is_checkpoint(last)
    assert last.get("text") == "Place order"


def test_fill_uses_vault_token_not_a_value():
    steps = expand_recipe(
        recipe="fill",
        url="https://shop.example/checkout",
        vault="card-visa",
    )
    fill = next(s for s in steps if s.get("action") == "fill")
    fields = fill["fields"]
    assert fields[0]["value"] == "{{vault:card-visa}}"
    assert "4111" not in str(fields)
    assert step_is_checkpoint(steps[-1])


def test_open_recipe_is_navigate_then_snapshot():
    steps = expand_recipe(recipe="open", url="https://example.com")
    assert steps[0]["action"] == "navigate"
    assert steps[0]["url"] == "https://example.com"
    assert steps[-1]["action"] == "snapshot"


def test_drive_accepts_recipe_instead_of_json():
    calls: list[str] = []

    def run(action, **kw):
        calls.append(str(getattr(action, "value", action)).lower())
        return '{"ok": true, "message": "SUCCESS"}'

    out = drive_life_task(
        goal="open docs",
        recipe="open",
        url="https://example.com",
        run_action=run,
    )
    assert "navigate" in calls
    assert out["ok"] is True
    assert not any(step_is_checkpoint(s) for s in (out.get("steps") or []))


def test_shop_drive_does_not_press_place_order():
    calls: list[str] = []

    def run(action, **_kw):
        calls.append(str(getattr(action, "value", action)).lower())
        return '{"ok": true, "message": "SUCCESS"}'

    out = drive_life_task(
        goal="buy milk on instacart",
        run_action=run,
    )
    assert out["status"] == "need_you"
    assert not any("click" in c for c in calls)
    assert any(s.get("status") == "need_you" for s in out["steps"])
