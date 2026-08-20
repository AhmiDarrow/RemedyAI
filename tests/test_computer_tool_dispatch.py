"""Computer/companion tool dispatch: what gets built, what gets refused.

These two modules are the last pure layer before Remedy touches the owner's
actual mouse, keyboard and browser. Everything tested here happens *before*
the job leaves the process: the summary line the approval card shows, whether
the owner is asked at all, which target ("browser" vs "desktop") the action is
routed to, and how empty/zero arguments are coerced.

What breaks if this code is wrong, in plain language:

- A typed password or card number leaks into an approval banner or a log,
  because the summary embedded the text instead of its length.
- A "Place order" click sails through without asking, because the label came
  from a snapshot ref and nobody resolved it.
- One "yes" on a checkout page gets replayed on a different site, or replayed
  a second time on the same one.
- A blocked action still reaches the machine, because the handler built the
  refusal string and then ran the job anyway.
- press_hold sends a click at (0, 0) — the top-left corner of the screen —
  because an unset coordinate default was treated as a real point.
- A bridge that is merely unavailable raises out of a read-only helper and
  kills the turn.

The executor and the host bridge are stubbed at the boundary: no handler in
this file is ever allowed to reach the job queue or Win32.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from remedy.core import agent_companion_tools as companion_tools
from remedy.core import agent_computer_tools as act
from remedy.core import companion as companion_mod
from remedy.core import companion_inbox as inbox_mod
from remedy.core import companion_observe as observe_mod
from remedy.core.agent_companion_tools import register_companion_tools
from remedy.core.agent_computer_tools import (
    _page_context,
    _page_origin,
    _resolve_ref_label,
    register_computer_tools,
)
from remedy.core.approvals import SENSITIVE_PREFIX, ApprovalQueue
from remedy.core.companion import FakeCompanionBackend, set_companion_backend
from remedy.core.computer.types import COMPUTER_TOOL_NAMES, ComputerAction

CHECKOUT_URL = "https://shop.example.com/checkout"
NORMAL_URL = "https://news.example.com/article"


# --------------------------------------------------------------------------
# Doubles. None of these can reach the queue, the desktop, or the network.
# --------------------------------------------------------------------------


class _Bridge:
    """Host bridge double: only the three read-only accessors the module uses."""

    def __init__(
        self,
        *,
        elements: list[dict] | None = None,
        target: str = "",
        navigate_url: str = "",
        observed_url: str = "",
        explode: bool = False,
    ) -> None:
        self.elements = elements if elements is not None else []
        self.target = target
        self.navigate_url = navigate_url
        self.observed_url = observed_url
        self.explode = explode

    def _boom(self) -> None:
        if self.explode:
            raise RuntimeError("host bridge is not running")

    def last_elements_info(self) -> dict:
        self._boom()
        return {"target": self.target, "elements": self.elements}

    def last_navigate_url(self) -> str:
        self._boom()
        return self.navigate_url

    def last_observed_url(self) -> str:
        self._boom()
        return self.observed_url


class _OldBridge:
    """An older host with no last_observed_url() — the attribute check matters."""

    def __init__(self, navigate_url: str = "") -> None:
        self.navigate_url = navigate_url

    def last_elements_info(self) -> dict:
        return {"target": "", "elements": []}

    def last_navigate_url(self) -> str:
        return self.navigate_url


class _Executor:
    """Records what would have been dispatched. Never dispatches anything."""

    def __init__(self) -> None:
        self.bridge = _Bridge()
        self.calls: list[tuple] = []
        self.result = "ok"
        self.raises: Exception | None = None

    def run(self, action, **kwargs):  # noqa: ANN001 - mirrors the real signature
        self.calls.append((action, kwargs))
        if self.raises is not None:
            raise self.raises
        return self.result

    # convenience for assertions
    @property
    def last(self) -> tuple:
        assert self.calls, "expected a dispatched action, got none"
        return self.calls[-1]

    @property
    def last_kwargs(self) -> dict:
        return self.last[1]


class _Registry:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}
        self.schemas: dict[str, dict] = {}
        self.descriptions: dict[str, str] = {}

    def register_builtin_handler(self, name, description, handler, parameters=None):
        self.tools[name] = handler
        self.schemas[name] = parameters or {}
        self.descriptions[name] = description


class _Runtime:
    def __init__(self, home) -> None:
        self.config = SimpleNamespace(home_dir=str(home))
        self.tool_registry = _Registry()
        self._session_id = "sess-under-test"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def approvals(monkeypatch):
    """A private approval queue, so no test can leak state into another."""
    queue = ApprovalQueue()
    monkeypatch.setattr("remedy.core.approvals.APPROVALS", queue)
    # needs_ask() re-reads config.toml; pin it so the owner's real settings
    # (or their absence) cannot change what these tests assert.
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config", lambda: {}, raising=False
    )
    return queue


@pytest.fixture
def tools(monkeypatch, approvals, tmp_path):
    """Registered computer tools wired to a recording executor."""
    ex = _Executor()
    monkeypatch.setattr(act, "get_computer_executor", lambda home=None: ex)
    runtime = _Runtime(tmp_path)
    register_computer_tools(runtime)
    return SimpleNamespace(
        ex=ex,
        runtime=runtime,
        reg=runtime.tool_registry,
        t=runtime.tool_registry.tools,
        approvals=approvals,
    )


@pytest.fixture
def companion(monkeypatch, tmp_path):
    """Registered companion tools on a fake clipboard/foreground backend."""
    backend = FakeCompanionBackend()
    set_companion_backend(backend)
    # recent_files() walks the real Desktop/Downloads; pin it.
    monkeypatch.setattr(companion_mod, "recent_files", lambda *a, **k: [])
    runtime = _Runtime(tmp_path)
    register_companion_tools(runtime)
    try:
        yield SimpleNamespace(
            backend=backend,
            runtime=runtime,
            reg=runtime.tool_registry,
            t=runtime.tool_registry.tools,
        )
    finally:
        set_companion_backend(None)


# --------------------------------------------------------------------------
# _resolve_ref_label — a label lookup that must never raise
# --------------------------------------------------------------------------


@pytest.mark.parametrize("ref", ["", "   ", "\n"])
def test_a_blank_ref_resolves_to_no_label(ref):
    ex = _Executor()
    ex.bridge = _Bridge(elements=[{"ref": "e1", "name": "Buy now"}])
    assert _resolve_ref_label(ex, ref) == ""


def test_a_known_ref_resolves_to_its_accessible_name():
    ex = _Executor()
    ex.bridge = _Bridge(elements=[{"ref": "e3", "name": "Place order"}])
    assert _resolve_ref_label(ex, "e3") == "Place order"
    # Surrounding whitespace from the model must not defeat the match.
    assert _resolve_ref_label(ex, "  e3 ") == "Place order"


def test_a_ref_with_no_name_falls_back_to_its_text():
    ex = _Executor()
    ex.bridge = _Bridge(elements=[{"ref": "e3", "text": "Pay now"}])
    assert _resolve_ref_label(ex, "e3") == "Pay now"


def test_an_unknown_ref_resolves_to_no_label():
    ex = _Executor()
    ex.bridge = _Bridge(elements=[{"ref": "e1", "name": "Cancel"}])
    assert _resolve_ref_label(ex, "e99") == ""


def test_a_dead_bridge_is_reported_as_no_label_not_raised():
    ex = _Executor()
    ex.bridge = _Bridge(explode=True)
    assert _resolve_ref_label(ex, "e1") == ""


# --------------------------------------------------------------------------
# _page_context / _page_origin — evidence for the money checkpoint
# --------------------------------------------------------------------------


def test_the_observed_url_wins_over_the_last_requested_navigate():
    """SPA checkout hops change the URL without a navigate call."""
    ex = _Executor()
    ex.bridge = _Bridge(navigate_url=NORMAL_URL, observed_url=CHECKOUT_URL)
    ctx = _page_context(ex)
    assert CHECKOUT_URL in ctx
    assert NORMAL_URL not in ctx


def test_the_requested_navigate_url_is_used_when_nothing_was_observed():
    ex = _Executor()
    ex.bridge = _Bridge(navigate_url=CHECKOUT_URL, observed_url="")
    assert CHECKOUT_URL in _page_context(ex)


def test_a_host_without_observed_url_support_still_yields_context():
    ex = _Executor()
    ex.bridge = _OldBridge(navigate_url=CHECKOUT_URL)
    assert CHECKOUT_URL in _page_context(ex)


def test_page_context_includes_the_target_and_element_labels():
    ex = _Executor()
    ex.bridge = _Bridge(
        target="Chrome — Checkout",
        elements=[{"name": "Place order"}, {"text": "Order total"}],
    )
    ctx = _page_context(ex)
    assert "Chrome — Checkout" in ctx
    assert "Place order" in ctx
    assert "Order total" in ctx


def test_page_context_reads_at_most_forty_elements():
    ex = _Executor()
    ex.bridge = _Bridge(elements=[{"name": f"ZZ{i}"} for i in range(50)])
    ctx = _page_context(ex)
    assert "ZZ39" in ctx
    assert "ZZ40" not in ctx


def test_page_context_is_capped_so_it_cannot_bloat_an_approval_card():
    ex = _Executor()
    ex.bridge = _Bridge(elements=[{"name": "x" * 500} for _ in range(40)])
    assert len(_page_context(ex)) <= 2000


def test_a_dead_bridge_yields_empty_page_context_not_an_exception():
    ex = _Executor()
    ex.bridge = _Bridge(explode=True)
    assert _page_context(ex) == ""


@pytest.mark.parametrize(
    ("blob", "expected"),
    [
        ("https://www.shop.example.com/checkout Place order", "shop.example.com"),
        ("https://shop.example.com/cart", "shop.example.com"),
        ("", ""),
        ("Place order Order total", ""),
    ],
)
def test_page_origin_extracts_the_www_stripped_host(blob, expected):
    assert _page_origin(blob) == expected


# --------------------------------------------------------------------------
# Registration surface
# --------------------------------------------------------------------------


def test_every_declared_computer_tool_is_actually_registered(tools):
    assert set(tools.t) >= COMPUTER_TOOL_NAMES


def test_vault_list_is_registered_alongside_the_computer_tools(tools):
    assert "vault_list" in tools.t


def test_registration_alone_dispatches_nothing(tools):
    """Building the tool table must not touch the machine."""
    assert tools.ex.calls == []


def test_every_computer_handler_is_a_coroutine(tools):
    for name, handler in tools.t.items():
        assert inspect.iscoroutinefunction(handler), name


@pytest.mark.parametrize(
    ("name", "required"),
    [
        ("computer_app", ["app"]),
        ("computer_type", ["text"]),
        ("computer_key", ["key"]),
        ("computer_navigate", ["url"]),
        ("computer_drag", ["x", "y", "x2", "y2"]),
        ("computer_fill", ["fields"]),
    ],
)
def test_schemas_declare_the_arguments_the_action_cannot_work_without(
    tools, name, required
):
    assert tools.reg.schemas[name].get("required") == required


def test_read_only_tools_declare_nothing_as_required(tools):
    for name in ("computer_screenshot", "computer_snapshot", "computer_monitors"):
        assert "required" not in tools.reg.schemas[name]


# --------------------------------------------------------------------------
# Read-only tools are never gated
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "computer_screenshot",
        "computer_snapshot",
        "computer_monitors",
        "computer_page_text",
        "computer_find",
        "computer_wait",
        "computer_scroll",
        "computer_navigate",
        "computer_windows",
    ],
)
async def test_looking_at_the_screen_never_asks_the_owner_for_approval(tools, name):
    """Ask mode is the cautious default; it must not make reading painful."""
    out = await tools.t[name]()
    assert "APPROVAL_REQUIRED" not in out
    assert tools.ex.calls, f"{name} should have dispatched"


# --------------------------------------------------------------------------
# Argument coercion — the zero/empty defaults that mean "unset"
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("monitor", ["", "   "])
async def test_a_blank_monitor_means_the_whole_virtual_screen(tools, monitor):
    await tools.t["computer_screenshot"](monitor=monitor)
    assert tools.ex.last_kwargs["monitor"] is None


@pytest.mark.asyncio
async def test_a_named_monitor_is_passed_through_untouched(tools):
    await tools.t["computer_screenshot"](monitor="1")
    assert tools.ex.last_kwargs["monitor"] == "1"


@pytest.mark.asyncio
async def test_screenshot_defaults_to_auto_routing_and_no_marks(tools):
    await tools.t["computer_screenshot"]()
    action, kwargs = tools.ex.last
    assert action is ComputerAction.SCREENSHOT
    assert kwargs["target"] == "auto"
    assert kwargs["mark"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    ["computer_screenshot", "computer_snapshot", "computer_find", "computer_scroll"],
)
async def test_an_empty_target_falls_back_to_auto_routing(tools, name):
    await tools.t[name](target="")
    assert tools.ex.last_kwargs["target"] == "auto"


@pytest.mark.asyncio
async def test_snapshot_treats_hwnd_zero_as_no_window_scope(tools):
    await tools.t["computer_snapshot"](hwnd=0)
    assert tools.ex.last_kwargs["hwnd"] is None


@pytest.mark.asyncio
async def test_snapshot_passes_a_real_hwnd_through(tools):
    await tools.t["computer_snapshot"](hwnd=12345, mode="controls", limit=7)
    kwargs = tools.ex.last_kwargs
    assert (kwargs["hwnd"], kwargs["mode"], kwargs["limit"]) == (12345, "controls", 7)


@pytest.mark.asyncio
async def test_page_text_reads_the_browser_rail_by_default(tools):
    await tools.t["computer_page_text"]()
    assert tools.ex.last_kwargs["target"] == "browser"


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["desktop", "DESKTOP", " Desktop "])
async def test_page_text_honours_an_explicit_desktop_target(tools, target):
    await tools.t["computer_page_text"](target=target)
    assert tools.ex.last_kwargs["target"] == "desktop"


@pytest.mark.asyncio
async def test_naming_a_window_implies_reading_the_desktop_not_the_rail(tools):
    """hwnd= only means something for a native window."""
    await tools.t["computer_page_text"](hwnd=99, target="browser")
    kwargs = tools.ex.last_kwargs
    assert kwargs["target"] == "desktop"
    assert kwargs["hwnd"] == 99


@pytest.mark.asyncio
async def test_find_accepts_either_text_or_query_and_fills_in_the_other(tools):
    await tools.t["computer_find"](query="Sign in")
    kwargs = tools.ex.last_kwargs
    assert kwargs["text"] == "Sign in"
    assert kwargs["query"] == "Sign in"


@pytest.mark.asyncio
async def test_find_uses_the_search_term_as_the_routing_hint_when_none_given(tools):
    await tools.t["computer_find"](text="Cart")
    assert tools.ex.last_kwargs["hint"] == "Cart"


@pytest.mark.asyncio
async def test_an_explicit_hint_beats_the_search_term(tools):
    await tools.t["computer_find"](text="Cart", hint="on the shop page")
    assert tools.ex.last_kwargs["hint"] == "on the shop page"


@pytest.mark.asyncio
async def test_navigate_defaults_to_the_in_app_rail_not_the_system_browser(tools):
    await tools.t["computer_navigate"](url="https://example.com", target="")
    action, kwargs = tools.ex.last
    assert action is ComputerAction.NAVIGATE
    assert kwargs["target"] == "browser"


@pytest.mark.asyncio
async def test_windows_defaults_to_the_desktop_and_uses_the_title_as_hint(tools):
    await tools.t["computer_windows"](mode="focus", title="Notepad", target="")
    kwargs = tools.ex.last_kwargs
    assert kwargs["target"] == "desktop"
    assert kwargs["hint"] == "Notepad"
    assert kwargs["mode"] == "focus"


@pytest.mark.asyncio
async def test_windows_forwards_unset_geometry_as_none_not_zero(tools):
    """(0, 0) is a legal window position — 'not given' must stay distinct."""
    await tools.t["computer_windows"](mode="list")
    kwargs = tools.ex.last_kwargs
    assert (kwargs["x"], kwargs["y"], kwargs["width"], kwargs["height"]) == (
        None,
        None,
        None,
        None,
    )


@pytest.mark.asyncio
async def test_monitors_always_asks_the_desktop_never_the_rail(tools):
    await tools.t["computer_monitors"]()
    action, kwargs = tools.ex.last
    assert action is ComputerAction.MONITORS
    assert kwargs["target"] == "desktop"


@pytest.mark.asyncio
async def test_wait_forwards_the_requested_pause(tools):
    await tools.t["computer_wait"](seconds=1.25)
    assert tools.ex.last_kwargs["seconds"] == 1.25


# --------------------------------------------------------------------------
# The approval gate — what must be refused
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "kwargs"),
    [
        ("computer_click", {"text": "Read more"}),
        ("computer_type", {"text": "hello"}),
        ("computer_key", {"key": "tab"}),
        ("computer_drag", {"x": 1, "y": 2, "x2": 3, "y2": 4}),
        ("computer_act", {"click": "Search"}),
        ("computer_app", {"app": "notepad"}),
        ("computer_select", {"value": "Oregon"}),
        ("computer_fill", {"fields": [{"text": "Name", "value": "Ada"}]}),
    ],
)
async def test_in_ask_mode_a_mutation_stops_before_it_reaches_the_machine(
    tools, name, kwargs
):
    out = await tools.t[name](**kwargs)
    assert out.startswith("APPROVAL_REQUIRED id=")
    assert tools.ex.calls == [], "a blocked action must dispatch nothing"


@pytest.mark.asyncio
async def test_the_refusal_names_the_tool_to_retry_and_forbids_faking_success(tools):
    out = await tools.t["computer_click"](text="Read more")
    assert "Do not invent success" in out
    assert "retry computer_click" in out
    assert "/approve " in out


@pytest.mark.asyncio
async def test_the_refusal_carries_a_real_pending_item(tools):
    out = await tools.t["computer_click"](text="Read more")
    approval_id = out.split("id=", 1)[1].split("\n", 1)[0].strip()
    item = tools.approvals.get(approval_id)
    assert item is not None
    assert item.status == "pending"
    assert item.tool_name == "computer_click"
    assert item.session_id == "sess-under-test"


@pytest.mark.asyncio
async def test_a_very_long_summary_is_trimmed_in_the_refusal_banner(tools):
    out = await tools.t["computer_click"](text="L" * 900)
    command_line = [ln for ln in out.splitlines() if ln.startswith("command=")][0]
    assert len(command_line) <= len("command=") + 400


@pytest.mark.asyncio
async def test_approving_once_lets_the_same_action_through_afterwards(tools):
    first = await tools.t["computer_click"](text="Read more")
    approval_id = first.split("id=", 1)[1].split("\n", 1)[0].strip()
    tools.approvals.resolve(approval_id, approve=True, scope="session")
    second = await tools.t["computer_click"](text="Read more")
    assert "APPROVAL_REQUIRED" not in second
    assert tools.ex.calls, "the approved action should now dispatch"


@pytest.mark.asyncio
async def test_denying_leaves_the_action_blocked(tools):
    first = await tools.t["computer_click"](text="Read more")
    approval_id = first.split("id=", 1)[1].split("\n", 1)[0].strip()
    tools.approvals.resolve(approval_id, approve=False)
    second = await tools.t["computer_click"](text="Read more")
    assert second.startswith("APPROVAL_REQUIRED")
    assert tools.ex.calls == []


@pytest.mark.asyncio
async def test_approving_one_click_does_not_approve_a_different_one(tools):
    first = await tools.t["computer_click"](text="Read more")
    approval_id = first.split("id=", 1)[1].split("\n", 1)[0].strip()
    tools.approvals.resolve(approval_id, approve=True, scope="always")
    other = await tools.t["computer_click"](text="Delete account")
    assert other.startswith("APPROVAL_REQUIRED")
    assert tools.ex.calls == []


# --------------------------------------------------------------------------
# Owner checkpoints — money, which no approval mode may waive
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ask", "auto", "full"])
async def test_a_purchase_click_always_stops_for_the_owner(tools, mode):
    tools.approvals.set_mode(mode)
    out = await tools.t["computer_click"](text="Place order")
    assert out.startswith("APPROVAL_REQUIRED")
    assert SENSITIVE_PREFIX in out
    assert tools.ex.calls == []


@pytest.mark.asyncio
async def test_an_ordinary_click_in_auto_mode_flows_without_a_prompt(tools):
    tools.approvals.set_mode("auto")
    out = await tools.t["computer_click"](text="Read more")
    assert "APPROVAL_REQUIRED" not in out
    assert tools.ex.calls


@pytest.mark.asyncio
async def test_a_purchase_click_by_ref_is_caught_via_the_snapshot_label(tools):
    """The model usually clicks by ref; the label has to be resolved first."""
    tools.approvals.set_mode("auto")
    tools.ex.bridge = _Bridge(elements=[{"ref": "e7", "name": "Place order"}])
    out = await tools.t["computer_click"](ref="e7")
    assert SENSITIVE_PREFIX in out
    assert tools.ex.calls == []


@pytest.mark.asyncio
async def test_a_pixel_click_on_a_checkout_page_stops_in_ask_mode(tools):
    """No readable target + a payment surface open = fail closed."""
    tools.ex.bridge = _Bridge(observed_url=CHECKOUT_URL)
    out = await tools.t["computer_click"](x=400, y=310)
    assert SENSITIVE_PREFIX in out
    assert tools.ex.calls == []


@pytest.mark.asyncio
async def test_a_pixel_click_on_a_checkout_page_is_effortless_in_full_mode(tools):
    """auto/full is a standing grant — the owner handed over the keys."""
    tools.approvals.set_mode("full")
    tools.ex.bridge = _Bridge(observed_url=CHECKOUT_URL)
    out = await tools.t["computer_click"](x=400, y=310)
    assert "APPROVAL_REQUIRED" not in out
    assert tools.ex.calls


@pytest.mark.asyncio
async def test_typing_a_raw_card_number_stops_in_ask_mode(tools):
    out = await tools.t["computer_type"](text="4242 4242 4242 4242")
    assert SENSITIVE_PREFIX in out
    assert tools.ex.calls == []


@pytest.mark.asyncio
async def test_filling_a_raw_card_number_stops_like_typing_one_does(tools):
    """computer_fill types too — a PAN in fields[].value is the same moment."""
    out = await tools.t["computer_fill"](
        fields=[{"text": "Card number", "value": "4242 4242 4242 4242"}]
    )
    assert SENSITIVE_PREFIX in out
    assert tools.ex.calls == []
    out2 = await tools.t["computer_fill"](
        fields='[{"text": "Card number", "value": "4242424242424242"}]'
    )
    assert SENSITIVE_PREFIX in out2
    assert tools.ex.calls == []


@pytest.mark.asyncio
async def test_a_vault_handle_is_an_owner_moment_in_every_mode(tools):
    tools.approvals.set_mode("full")
    out = await tools.t["computer_type"](text="{{vault:card-visa}}")
    assert SENSITIVE_PREFIX in out
    assert tools.ex.calls == []


@pytest.mark.asyncio
async def test_a_money_go_ahead_is_single_use(tools):
    """One 'yes' is one action, not a standing permission."""
    tools.approvals.set_mode("auto")
    first = await tools.t["computer_click"](text="Place order")
    approval_id = first.split("id=", 1)[1].split("\n", 1)[0].strip()
    # Even "always" downgrades to a one-shot grant for a money action.
    tools.approvals.resolve(approval_id, approve=True, scope="always")

    second = await tools.t["computer_click"](text="Place order")
    assert "APPROVAL_REQUIRED" not in second
    assert len(tools.ex.calls) == 1

    third = await tools.t["computer_click"](text="Place order")
    assert third.startswith("APPROVAL_REQUIRED")
    assert len(tools.ex.calls) == 1, "the grant must not be replayable"


@pytest.mark.asyncio
async def test_a_money_go_ahead_does_not_carry_to_another_site(tools):
    tools.approvals.set_mode("auto")
    tools.ex.bridge = _Bridge(observed_url="https://shop.example.com/checkout")
    first = await tools.t["computer_click"](text="Place order")
    approval_id = first.split("id=", 1)[1].split("\n", 1)[0].strip()
    tools.approvals.resolve(approval_id, approve=True, scope="session")

    # Same button label, different host: a new decision for the owner.
    tools.ex.bridge = _Bridge(observed_url="https://evil.example.org/checkout")
    second = await tools.t["computer_click"](text="Place order")
    assert second.startswith("APPROVAL_REQUIRED")
    assert tools.ex.calls == []


# --------------------------------------------------------------------------
# Summaries — what the owner reads, and what must never appear in it
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_typed_secret_never_appears_in_the_approval_summary(tools):
    secret = "hunter2-correct-horse"
    out = await tools.t["computer_type"](text=secret)
    assert secret not in out
    assert f"chars={len(secret)}" in out


@pytest.mark.asyncio
async def test_a_vault_handle_is_named_in_the_summary_but_no_value_is(tools):
    out = await tools.t["computer_type"](text="prefix {{vault:card-visa}} suffix")
    assert "vault=card-visa" in out
    assert "prefix" not in out


@pytest.mark.asyncio
async def test_act_reports_only_the_length_of_what_it_would_type(tools):
    out = await tools.t["computer_act"](click="Sign in", type="s3cr3t-password")
    assert "s3cr3t-password" not in out
    assert "type_chars=15" in out


@pytest.mark.asyncio
async def test_act_says_plainly_when_it_would_type_nothing(tools):
    out = await tools.t["computer_act"](click="Sign in")
    assert "type=-" in out


@pytest.mark.asyncio
async def test_a_click_summary_shows_the_resolved_label_from_a_ref(tools):
    tools.ex.bridge = _Bridge(elements=[{"ref": "e2", "name": "Membership options"}])
    out = await tools.t["computer_click"](ref="e2")
    assert "Membership options" in out
    assert "ref='e2'" in out


@pytest.mark.asyncio
async def test_a_press_hold_summary_falls_back_to_coordinates_with_no_label(tools):
    tools.ex.bridge = _Bridge(observed_url=CHECKOUT_URL)
    out = await tools.t["computer_press_hold"](x=12, y=34, hold_ms=1500)
    assert out.startswith("APPROVAL_REQUIRED")
    assert "(12,34)" in out
    assert "1500ms" in out


@pytest.mark.asyncio
async def test_a_press_hold_summary_prefers_the_control_it_found(tools):
    tools.ex.bridge = _Bridge(elements=[{"ref": "c9", "name": "Confirm purchase"}])
    out = await tools.t["computer_press_hold"](ref="c9")
    assert SENSITIVE_PREFIX in out
    assert "press-and-hold Confirm purchase" in out


@pytest.mark.asyncio
async def test_a_hold_gesture_on_an_ordinary_page_is_not_gated_in_ask_mode(tools):
    """Deliberate, not a gap: a press-and-hold is how a CAPTCHA challenge is
    answered, so prompting on every one would make them unusable. It stays in
    _MUTATION_COMPUTER_TOOLS, so the payment-surface checkpoint still stops it
    on a checkout page — see test_press_hold_is_mutation_not_high_impact.
    """
    tools.ex.bridge = _Bridge(observed_url=NORMAL_URL)
    out = await tools.t["computer_press_hold"](text="Hold to reveal")
    assert "APPROVAL_REQUIRED" not in out
    assert tools.ex.calls


# --------------------------------------------------------------------------
# Payload construction for the mutating tools (with the gate stood down)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_click_forwards_every_locator_it_was_given(tools):
    tools.approvals.set_mode("auto")
    await tools.t["computer_click"](
        x=5, y=6, button="right", clicks=2, ref="e1", text="Open", target=""
    )
    action, kwargs = tools.ex.last
    assert action is ComputerAction.CLICK
    assert kwargs["x"] == 5
    assert kwargs["y"] == 6
    assert kwargs["button"] == "right"
    assert kwargs["clicks"] == 2
    assert kwargs["ref"] == "e1"
    assert kwargs["text"] == "Open"
    assert kwargs["target"] == "auto"


@pytest.mark.asyncio
async def test_type_forwards_the_target_control_ref(tools):
    tools.approvals.set_mode("auto")
    await tools.t["computer_type"](text="hello", ref="c4")
    action, kwargs = tools.ex.last
    assert action is ComputerAction.TYPE
    assert kwargs["text"] == "hello"
    assert kwargs["ref"] == "c4"


@pytest.mark.asyncio
async def test_select_forwards_the_option_and_ref(tools):
    tools.approvals.set_mode("auto")
    await tools.t["computer_select"](value="Oregon", ref="e4")
    action, kwargs = tools.ex.last
    assert action is ComputerAction.SELECT
    assert kwargs["value"] == "Oregon"
    assert kwargs["ref"] == "e4"
    assert kwargs["target"] == "browser"


@pytest.mark.asyncio
async def test_fill_forwards_the_field_list(tools):
    tools.approvals.set_mode("auto")
    fields = [{"text": "Name", "value": "Ada"}, {"ref": "e4", "select": "CA"}]
    await tools.t["computer_fill"](fields=fields)
    action, kwargs = tools.ex.last
    assert action is ComputerAction.FILL
    assert kwargs["fields"] == fields
    assert kwargs["target"] == "browser"


@pytest.mark.asyncio
async def test_act_sends_the_typed_text_under_both_names_the_host_accepts(tools):
    tools.approvals.set_mode("auto")
    await tools.t["computer_act"](click="Sign in", type="me@example.com")
    kwargs = tools.ex.last_kwargs
    assert kwargs["type"] == "me@example.com"
    assert kwargs["type_text"] == "me@example.com"


@pytest.mark.asyncio
async def test_act_sends_the_click_label_as_the_text_locator_too(tools):
    tools.approvals.set_mode("auto")
    await tools.t["computer_act"](click="Sign in")
    assert tools.ex.last_kwargs["text"] == "Sign in"


@pytest.mark.asyncio
async def test_act_uses_the_goal_as_the_routing_hint_when_none_given(tools):
    tools.approvals.set_mode("auto")
    await tools.t["computer_act"](click="Sign in", goal="log into mail")
    assert tools.ex.last_kwargs["hint"] == "log into mail"


@pytest.mark.asyncio
async def test_act_forwards_the_verification_expectations(tools):
    """Without these the host reports success it never checked."""
    tools.approvals.set_mode("auto")
    await tools.t["computer_act"](
        url="https://shop.example.com",
        expect_url="/cart",
        expect_text="added to cart",
    )
    kwargs = tools.ex.last_kwargs
    assert kwargs["expect_url"] == "/cart"
    assert kwargs["expect_text"] == "added to cart"


@pytest.mark.asyncio
async def test_act_names_a_vault_handle_and_still_stops_for_the_owner(tools):
    tools.approvals.set_mode("auto")
    out = await tools.t["computer_act"](click="Pay", type="{{vault:card-visa}}")
    assert SENSITIVE_PREFIX in out
    assert "vault=card-visa" in out
    assert "{{vault:" not in out, "the raw token belongs at the input path, not here"
    assert tools.ex.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "kwargs"), [("computer_type", {"text": "hi"}), ("computer_act", {"type": "hi"})]
)
async def test_a_broken_vault_does_not_take_the_typing_path_down_with_it(
    tools, monkeypatch, name, kwargs
):
    """The handle scan is decoration on the banner, not a precondition."""
    from remedy.core import vault

    def boom(text):
        raise RuntimeError("vault index unreadable")

    monkeypatch.setattr(vault, "token_handles", boom)
    tools.approvals.set_mode("auto")
    out = await tools.t[name](**kwargs)
    assert "APPROVAL_REQUIRED" not in out
    assert tools.ex.calls


@pytest.mark.asyncio
async def test_launching_an_app_targets_the_desktop_not_the_rail(tools):
    tools.approvals.set_mode("auto")
    await tools.t["computer_app"](app="notepad")
    action, kwargs = tools.ex.last
    assert action is ComputerAction.APP
    assert kwargs["target"] == "desktop"
    assert kwargs["app"] == "notepad"


@pytest.mark.asyncio
async def test_a_key_combo_is_forwarded_verbatim(tools):
    tools.approvals.set_mode("auto")
    await tools.t["computer_key"](key="ctrl+s", target="")
    action, kwargs = tools.ex.last
    assert action is ComputerAction.KEY
    assert kwargs["key"] == "ctrl+s"
    assert kwargs["target"] == "auto"


@pytest.mark.asyncio
async def test_drag_forwards_both_endpoints(tools):
    tools.approvals.set_mode("auto")
    await tools.t["computer_drag"](x=1, y=2, x2=30, y2=40)
    action, kwargs = tools.ex.last
    assert action is ComputerAction.DRAG
    assert (kwargs["x"], kwargs["y"], kwargs["x2"], kwargs["y2"]) == (1, 2, 30, 40)


@pytest.mark.asyncio
async def test_press_hold_with_no_locator_sends_no_coordinates(tools):
    """(0, 0) is the schema default — never a click on the screen corner."""
    tools.approvals.set_mode("auto")
    await tools.t["computer_press_hold"]()
    kwargs = tools.ex.last_kwargs
    assert kwargs["x"] is None
    assert kwargs["y"] is None
    assert kwargs["text"] is None
    assert kwargs["ref"] is None


@pytest.mark.asyncio
async def test_press_hold_keeps_a_real_edge_coordinate(tools):
    """x=0 on a genuine point must survive; only (0, 0) means 'unset'."""
    tools.approvals.set_mode("auto")
    await tools.t["computer_press_hold"](x=0, y=250)
    kwargs = tools.ex.last_kwargs
    assert kwargs["x"] == 0
    assert kwargs["y"] == 250


@pytest.mark.asyncio
async def test_press_hold_with_a_label_still_sends_the_origin_point(tools):
    tools.approvals.set_mode("auto")
    await tools.t["computer_press_hold"](text="Hold to confirm")
    kwargs = tools.ex.last_kwargs
    assert kwargs["text"] == "Hold to confirm"
    assert (kwargs["x"], kwargs["y"]) == (0, 0)


@pytest.mark.asyncio
async def test_press_hold_falls_back_to_a_usable_default_duration(tools):
    tools.approvals.set_mode("auto")
    await tools.t["computer_press_hold"](text="Hold", hold_ms=0)
    assert tools.ex.last_kwargs["hold_ms"] == 2600


# --------------------------------------------------------------------------
# Dispatch plumbing
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_session_id_is_stamped_before_the_worker_thread_runs(tools):
    """A worker without the ContextVar must not inherit a sibling tab's id."""
    await tools.t["computer_screenshot"]()
    assert tools.ex.last_kwargs["session_id"] == "sess-under-test"


@pytest.mark.asyncio
async def test_a_failing_executor_is_not_silently_turned_into_success(tools):
    tools.ex.raises = RuntimeError("host bridge timed out")
    with pytest.raises(RuntimeError, match="host bridge timed out"):
        await tools.t["computer_screenshot"]()


@pytest.mark.asyncio
async def test_the_executor_result_is_returned_verbatim(tools):
    tools.ex.result = "screenshot saved to C:/tmp/shot.png"
    assert await tools.t["computer_screenshot"]() == "screenshot saved to C:/tmp/shot.png"


# --------------------------------------------------------------------------
# vault_list — metadata only, and it must not blow up an empty vault
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_empty_vault_is_explained_rather_than_reported_as_an_error(
    tools, monkeypatch
):
    from remedy.core import vault

    monkeypatch.setattr(vault, "vault_list", lambda home=None: [])
    out = await tools.t["vault_list"]()
    import json

    data = json.loads(out)
    assert data["ok"] is True
    assert data["items"] == []
    assert "Settings" in data["note"]


@pytest.mark.asyncio
async def test_vault_metadata_is_returned_without_values(tools, monkeypatch):
    from remedy.core import vault

    monkeypatch.setattr(
        vault,
        "vault_list",
        lambda home=None: [{"handle": "card-visa", "domains": ["shop.example.com"]}],
    )
    out = await tools.t["vault_list"]()
    import json

    data = json.loads(out)
    assert data["ok"] is True
    assert data["items"][0]["handle"] == "card-visa"


@pytest.mark.asyncio
async def test_an_unreadable_vault_reports_the_failure_as_json_not_a_traceback(
    tools, monkeypatch
):
    from remedy.core import vault

    def boom(home=None):
        raise OSError("vault.json is corrupt")

    monkeypatch.setattr(vault, "vault_list", boom)
    out = await tools.t["vault_list"]()
    import json

    data = json.loads(out)
    assert data["ok"] is False
    assert "corrupt" in data["error"]


# --------------------------------------------------------------------------
# Companion tools
# --------------------------------------------------------------------------


def test_the_companion_tool_table_is_complete(companion):
    assert set(companion.t) == {
        "companion_context",
        "clipboard_read",
        "clipboard_write",
        "companion_design",
        "companion_observe",
        "companion_taste",
        "companion_inbox",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
async def test_copying_nothing_is_refused_without_touching_the_clipboard(
    companion, text
):
    out = await companion.t["clipboard_write"](text=text)
    assert "text= required" in out
    assert companion.backend.written is None


@pytest.mark.asyncio
async def test_copying_text_reports_what_landed_on_the_clipboard(companion):
    out = await companion.t["clipboard_write"](text="hello world")
    assert companion.backend.written == "hello world"
    assert "11 chars" in out


@pytest.mark.asyncio
async def test_a_locked_clipboard_is_reported_not_claimed_as_success(companion):
    companion.backend.set_clipboard_text = lambda text: False
    out = await companion.t["clipboard_write"](text="hello")
    assert "failed" in out


@pytest.mark.asyncio
async def test_an_empty_clipboard_is_said_out_loud(companion):
    assert await companion.t["clipboard_read"]() == "clipboard is empty"


@pytest.mark.asyncio
async def test_clipboard_text_is_returned_with_its_length(companion):
    companion.backend.text = "def greet(): return 1"
    out = await companion.t["clipboard_read"]()
    assert out.startswith("clipboard text (")
    assert "def greet(): return 1" in out


@pytest.mark.asyncio
async def test_a_long_clipboard_is_marked_as_truncated(companion):
    companion.backend.text = "x" * 20000
    out = await companion.t["clipboard_read"]()
    assert "[truncated]" in out


@pytest.mark.asyncio
async def test_copied_files_are_listed_one_per_line(companion):
    companion.backend.files = ["C:/a/mock.png", "C:/a/notes.md"]
    out = await companion.t["clipboard_read"]()
    assert "- C:/a/mock.png" in out
    assert "- C:/a/notes.md" in out


@pytest.mark.asyncio
async def test_a_copied_image_is_observed_and_the_owner_is_not_re_asked(
    companion, monkeypatch, tmp_path
):
    seen: list[dict] = []

    def fake_observe(runtime, path, *, hint="", via=""):
        seen.append({"path": path, "hint": hint, "via": via})
        return {"message": "I can see the mock.", "image_md": "![shot](x.png)"}

    monkeypatch.setattr(observe_mod, "design_observe_path", fake_observe)
    companion.backend.image_png = b"\x89PNG\r\n\x1a\n" + b"z" * 32

    out = await companion.t["clipboard_read"]()
    assert seen and seen[0]["via"] == "clipboard"
    assert "I can see the mock." in out
    assert "Do not ask what they copied." in out


@pytest.mark.asyncio
async def test_reading_the_clipboard_does_not_go_scanning_the_owners_folders(
    companion, monkeypatch
):
    """clipboard_read asks for the clipboard, not a directory walk."""
    calls: list[int] = []
    monkeypatch.setattr(
        companion_mod, "recent_files", lambda *a, **k: calls.append(1) or []
    )
    await companion.t["clipboard_read"]()
    assert calls == []


@pytest.mark.asyncio
async def test_the_companion_snapshot_names_the_foreground_window(companion):
    companion.backend.fg = {"exe_name": "Code.exe", "title": "app.tsx"}
    out = await companion.t["companion_context"]()
    assert "Code.exe" in out
    assert "app.tsx" in out


@pytest.mark.asyncio
async def test_a_design_pass_admits_when_it_has_no_evidence_yet(companion):
    out = await companion.t["companion_design"](goal="tidy the settings page")
    assert "no image/file yet" in out
    assert "computer_screenshot" in out


@pytest.mark.asyncio
async def test_a_design_pass_lists_the_evidence_it_found(companion, monkeypatch):
    monkeypatch.setattr(observe_mod, "design_observe_paths", lambda *a, **k: [])
    companion.backend.files = ["C:/mocks/home.png"]
    out = await companion.t["companion_design"]()
    assert "- C:/mocks/home.png" in out
    assert "critique" in out


@pytest.mark.asyncio
async def test_a_failed_capture_forbids_inventing_the_ui_in_ascii(
    companion, monkeypatch
):
    monkeypatch.setattr(
        observe_mod,
        "capture_foreground_png",
        lambda runtime=None: {"ok": False, "via": "uia", "error": "no window"},
    )
    out = await companion.t["companion_observe"]()
    assert out.startswith("observe MISS")
    assert "Do not draw ASCII" in out


@pytest.mark.asyncio
async def test_a_successful_capture_reports_where_it_looked(companion, monkeypatch):
    monkeypatch.setattr(
        observe_mod,
        "capture_foreground_png",
        lambda runtime=None: {"ok": True, "via": "gdi", "path": "C:/tmp/fg.png"},
    )
    monkeypatch.setattr(
        observe_mod,
        "design_observe_path",
        lambda runtime, path, **kw: {
            "via": "gdi",
            "path": path,
            "message": "window looks right",
            "image_md": "",
        },
    )
    out = await companion.t["companion_observe"]()
    assert out.startswith("observe OK")
    assert "C:/tmp/fg.png" in out
    assert "window looks right" in out


@pytest.mark.asyncio
async def test_taste_starts_empty_and_says_so(companion):
    assert await companion.t["companion_taste"]() == "No taste facts yet."


@pytest.mark.asyncio
async def test_a_remembered_taste_fact_comes_back_on_the_next_look(companion):
    out = await companion.t["companion_taste"](fact="8px spacing, Inter, dark")
    assert "Remembered taste" in out
    listed = await companion.t["companion_taste"]()
    assert "8px spacing, Inter, dark" in listed


@pytest.mark.asyncio
@pytest.mark.parametrize("fact", ["", "   "])
async def test_a_blank_taste_fact_lists_instead_of_storing_an_empty_row(
    companion, fact
):
    out = await companion.t["companion_taste"](fact=fact)
    assert out == "No taste facts yet."


@pytest.mark.asyncio
async def test_an_empty_inbox_is_reported_plainly(companion, monkeypatch):
    monkeypatch.setattr(inbox_mod, "poll_new_drops", lambda runtime=None: [])
    assert await companion.t["companion_inbox"]() == "No new drops."


@pytest.mark.asyncio
async def test_new_drops_are_listed_with_their_folder_and_age(companion, monkeypatch):
    monkeypatch.setattr(
        inbox_mod,
        "poll_new_drops",
        lambda runtime=None: [
            {
                "path": "C:/Users/x/Downloads/mock.png",
                "name": "mock.png",
                "ago": "2m",
                "folder": "Downloads",
            }
        ],
    )
    out = await companion.t["companion_inbox"]()
    assert "Downloads/mock.png" in out
    assert "(2m)" in out


def test_the_companion_module_registers_handlers_not_bare_functions(companion):
    for name, handler in companion.t.items():
        assert inspect.iscoroutinefunction(handler), name
    assert companion.reg.schemas["clipboard_write"]["required"] == ["text"]
    assert companion.reg.schemas["companion_taste"]["required"] == []


def test_the_companion_registration_module_exposes_only_its_entry_point():
    assert hasattr(companion_tools, "register_companion_tools")
