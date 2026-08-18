"""The setup conversation: offering line choices, and remembering the pick.

The owner chooses their transport on first run by being asked, in sentences.
These tests care about the sentences as much as the data — the text here is
read aloud, and a menu that miscounts or recommends something impossible is
worse than no menu.
"""

from __future__ import annotations

from remedy.telephony.line import Capabilities
from remedy.telephony.options import LineOption, choose, chosen, offer
from remedy.telephony.registry import line_options, offer_lines

READY = Capabilities(outbound=True, inbound=True)


def _opt(name: str, **kw) -> LineOption:
    kw.setdefault("title", name.title())
    kw.setdefault("summary", f"the {name} way.")
    kw.setdefault("cost", "nothing")
    kw.setdefault("catch", "Some catch.")
    kw.setdefault("capabilities", READY)
    return LineOption(name=name, **kw)


def test_every_option_states_a_cost_and_a_catch():
    """No option gets to look free of downsides. If one has no catch we have
    not thought about it hard enough to offer it."""
    for option in line_options():
        assert option.cost.strip(), f"{option.name} has no stated cost"
        assert option.catch.strip(), f"{option.name} has no stated catch"
        assert option.summary.strip()


def test_menu_counts_its_own_options_correctly():
    text = offer([_opt("a"), _opt("b"), _opt("c")])
    assert text.startswith("There are three ways")
    assert "3)" in text and "4)" not in text


def test_menu_hides_what_this_machine_cannot_do():
    """Offering a choice the owner cannot take wastes their time."""
    text = offer([_opt("a"), _opt("impossible", achievable=False)])
    assert "impossible" not in text.lower()
    # Grammar guard: dropping an option must not leave "There are one way".
    assert text.startswith("There is one way")


def test_standalone_options_are_offered_first():
    """Least dependence on a second device being awake and nearby wins the top
    slot — that ordering is the accessibility argument made concrete."""
    text = offer([_opt("tethered", standalone=False), _opt("independent", standalone=True)])
    assert text.index("Independent") < text.index("Tethered")


def test_recommendation_is_marked_and_only_once():
    text = offer([_opt("a"), _opt("b")], recommend="b")
    assert text.count("(my suggestion)") == 1
    assert "B —" in text or "B -" in text


def test_unset_option_is_not_marked_ready():
    text = offer([_opt("a", missing=("not set up",), action="do the thing")])
    assert "(ready now)" not in text
    assert "To use it: do the thing" in text


def test_ready_option_says_so():
    assert "(ready now)" in offer([_opt("a")])


def test_bluetooth_is_never_the_recommendation():
    """It is the only option with a proximity failure mode. A partner an owner
    cannot rely on when they cannot cross the room is not a partner."""
    text = offer_lines(recommend="sip")
    assert "(my suggestion)" in text
    suggestion_line = next(ln for ln in text.splitlines() if "(my suggestion)" in ln)
    assert "wirelessly" not in suggestion_line.lower()


def test_bluetooth_carries_its_warning_when_it_is_offered_at_all():
    bt = next(o for o in line_options() if o.name == "bluetooth_hfp")
    assert "drop out" in bt.catch or "range" in bt.catch


def test_vm_option_is_honest_that_the_number_is_not_the_owners():
    """A VM has no SIM and no baseband: it can never place a carrier call on
    the owner's number. The menu must not imply otherwise."""
    vm = next(o for o in line_options() if o.name == "vm_voip")
    assert not vm.keeps_your_number
    assert vm.standalone


def test_wired_phone_keeps_the_owners_real_number():
    wired = next(o for o in line_options() if o.name == "phone_wired")
    assert wired.keeps_your_number
    assert not wired.standalone  # the phone still has to be docked


def test_sip_is_the_only_fully_standalone_local_option():
    sip = next(o for o in line_options() if o.name == "sip")
    assert sip.standalone and sip.local_audio


def test_choice_is_remembered_and_changeable(tmp_path):
    assert chosen(tmp_path) == ""
    choose("vm_voip", tmp_path)
    assert chosen(tmp_path) == "vm_voip"
    choose("sip", tmp_path)
    assert chosen(tmp_path) == "sip"


def test_unreadable_choice_file_reads_as_unset(tmp_path):
    (tmp_path / "telephony").mkdir()
    (tmp_path / "telephony" / "line.json").write_text("{not json", encoding="utf-8")
    assert chosen(tmp_path) == ""


def test_menu_ends_by_asking():
    assert offer_lines().rstrip().endswith("?")
