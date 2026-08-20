"""Self-improvement triggers on faults Remedy actually hit — not on a hunt.

The old loop read pytest's stale lastfailed cache and targeted a *network
flake*: unfixable by any edit, so it burned rounds forever and rolled back over
concurrent work. Now a round needs a real fault, and the world's failures
(network, auth, missing toolchain) are recorded but never targeted.
"""

from __future__ import annotations

import pytest

from remedy.core import error_journal as EJ


@pytest.fixture()
def home(tmp_path):
    return tmp_path / "rhome"


# --- recording --------------------------------------------------------------


def test_record_and_list(home) -> None:
    f = EJ.record_fault("turn_crash", "KeyError: 'muscle'", where="loop.py:120", home=home)
    assert f is not None and f.status == EJ.STATUS_OPEN and f.count == 1
    assert [x.message for x in EJ.list_faults(home=home)] == ["KeyError: 'muscle'"]


def test_repeats_collapse_and_count_up(home) -> None:
    for _ in range(3):
        EJ.record_fault("turn_crash", "KeyError: 'muscle'", where="loop.py:120", home=home)
    faults = EJ.list_faults(home=home)
    assert len(faults) == 1
    assert faults[0].count == 3


def test_volatile_numbers_do_not_split_a_fault(home) -> None:
    EJ.record_fault("tool_error", "timeout after 30 s at 0xAB12", where="x.py:1", home=home)
    EJ.record_fault("tool_error", "timeout after 45 s at 0xFF99", where="x.py:1", home=home)
    assert len(EJ.list_faults(home=home)) == 1


def test_empty_message_is_ignored(home) -> None:
    assert EJ.record_fault("other", "   ", home=home) is None


# --- environmental classification ------------------------------------------


@pytest.mark.parametrize(
    "msg",
    [
        "Connection refused",
        "Read timed out",
        "HTTP 429 rate limit exceeded",
        "401 Unauthorized: invalid api key",
        "getaddrinfo failed",
        "'gcc' is not recognized as an internal or external command",
        "SSL certificate verify failed",
    ],
)
def test_world_failures_are_environmental(msg: str, home) -> None:
    f = EJ.record_fault("tool_error", msg, home=home)
    assert f.status == EJ.STATUS_ENVIRONMENTAL
    # and therefore never a self-improvement target
    assert EJ.next_target_fault(home=home) is None


def test_real_code_bug_is_targetable(home) -> None:
    f = EJ.record_fault(
        "turn_crash", "AttributeError: 'NoneType' has no attribute 'muscle'",
        where="coordination.py:88", home=home,
    )
    assert f.status == EJ.STATUS_OPEN
    assert EJ.next_target_fault(home=home).id == f.id


def test_network_flake_would_not_be_targeted(home) -> None:
    """The exact first round: a network-dependent test failure."""
    EJ.record_fault(
        "tool_error",
        "assert 'Hello world' in ... (fetch https://example.com timed out)",
        home=home,
    )
    assert EJ.next_target_fault(home=home) is None


# --- attempt accounting -----------------------------------------------------


def test_stubborn_fault_gets_parked(home) -> None:
    f = EJ.record_fault("turn_crash", "TypeError: bad thing", where="a.py:2", home=home)
    for _ in range(EJ.MAX_FIX_ATTEMPTS):
        EJ.note_fix_attempt(f.id, home=home)
    parked = EJ.list_faults(home=home)[0]
    assert parked.status == EJ.STATUS_PARKED
    assert EJ.next_target_fault(home=home) is None  # stops burning rounds


def test_mark_fixed_closes_it(home) -> None:
    f = EJ.record_fault("turn_crash", "ValueError: nope", where="b.py:3", home=home)
    EJ.mark_fixed(f.id, home=home)
    assert EJ.next_target_fault(home=home) is None


def test_a_fixed_fault_that_returns_reopens(home) -> None:
    f = EJ.record_fault("turn_crash", "ValueError: nope", where="b.py:3", home=home)
    EJ.mark_fixed(f.id, home=home)
    again = EJ.record_fault("turn_crash", "ValueError: nope", where="b.py:3", home=home)
    assert again.status == EJ.STATUS_OPEN
    assert again.fix_attempts == 0
    assert EJ.next_target_fault(home=home) is not None


def test_most_frequent_fault_is_targeted_first(home) -> None:
    EJ.record_fault("turn_crash", "rare bug", where="a.py:1", home=home)
    for _ in range(4):
        EJ.record_fault("turn_crash", "common bug", where="b.py:2", home=home)
    assert EJ.next_target_fault(home=home).message == "common bug"


def test_record_exception_captures_location(home) -> None:
    try:
        raise KeyError("muscle")
    except KeyError as exc:
        f = EJ.record_exception(exc, kind="turn_crash", context="doing a thing", home=home)
    assert f.exc_type == "KeyError"
    assert "test_error_journal.py:" in f.where
    assert "doing a thing" in f.context
    assert f.traceback


# --- the loop only wakes for a real fault -----------------------------------


def test_no_faults_means_no_target(tmp_path, monkeypatch) -> None:
    from remedy.core.self_inject_draft import pick_draft_target

    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "empty"))
    monkeypatch.delenv("REMEDY_SELF_INJECT_SPECULATIVE", raising=False)
    # A real checkout, but nothing has gone wrong → no work invented.
    assert pick_draft_target(".", home=tmp_path / "empty") is None


def test_fault_becomes_a_jailed_target(tmp_path, monkeypatch) -> None:
    from remedy.core.self_inject_draft import pick_draft_target

    hm = tmp_path / "h"
    monkeypatch.delenv("REMEDY_SELF_INJECT_SPECULATIVE", raising=False)
    EJ.record_fault(
        "turn_crash",
        "AttributeError: 'NoneType' has no attribute 'claims'",
        exc_type="AttributeError",
        where="coordination.py:88",
        context="body coordination heartbeat",
        home=hm,
    )
    tgt = pick_draft_target(".", home=hm)
    assert tgt is not None
    assert tgt.kind == "fault"
    assert "AttributeError" in tgt.evidence
    assert tgt.allowed  # never an unbounded jail
    # the fault id travels so the round can close it out
    assert "Fault id " in tgt.why


def test_report_contains_problem_and_fix() -> None:
    from remedy.core.self_inject import SelfInjectRound
    from remedy.core.self_inject_draft import DraftTarget, _fault_report

    tgt = DraftTarget(
        kind="fault",
        path="src/remedy/core/coordination.py",
        evidence="AttributeError at coordination.py:88 (hit 3x): 'NoneType' has no claims",
        why="Remedy hit this while working. Fault id abc123",
    )
    rnd = SelfInjectRound(summary="guard None beacon before reading claims")
    rnd.gate_exit_codes = {"uv run pytest -q tests/test_coordination.py": 0}
    report = _fault_report(tgt, rnd, ["src/remedy/core/coordination.py"])
    assert "What went wrong" in report and "AttributeError" in report
    assert "Fix" in report and "guard None beacon" in report
    assert "Verified by" in report and "exit 0" in report
    assert "Not applied to any other install" in report


# --- model misbehaviour is not a Remedy bug ---------------------------------


@pytest.mark.parametrize(
    "msg",
    [
        "model returned no content",
        "empty answer from the provider",
        "finish_reason: length",
        "context window exceeded",
        "malformed tool call",
        "invalid tool arguments",
        "Could not parse the model JSON",
        "pseudo-tool call recovered",
        "The model refused the request",
    ],
)
def test_model_failures_are_not_code_bugs(msg: str, home) -> None:
    f = EJ.record_fault("turn_crash", msg, home=home)
    assert f.status == EJ.STATUS_MODEL
    # editing this repo cannot make an LLM behave — never a self-fix target
    assert EJ.next_target_fault(home=home) is None


def test_classify_buckets() -> None:
    assert EJ.classify("AttributeError: 'NoneType' has no attribute 'x'") == EJ.STATUS_OPEN
    assert EJ.classify("connection refused") == EJ.STATUS_ENVIRONMENTAL
    assert EJ.classify("model returned no content") == EJ.STATUS_MODEL


def test_real_bug_still_targeted_among_noise(home) -> None:
    """A genuine defect is not drowned out by model/world noise."""
    EJ.record_fault("turn_crash", "model returned no content", home=home)
    EJ.record_fault("tool_error", "Connection refused", home=home)
    real = EJ.record_fault(
        "turn_crash", "TypeError: claim_path() missing 1 argument",
        where="coordination.py:210", home=home,
    )
    target = EJ.next_target_fault(home=home)
    assert target is not None and target.id == real.id


class TestStatusCodesNeedContext:
    """A traceback is full of line numbers, and three of them used to mean
    "not ours to fix".

    The environmental regex matched bare ``401``/``403``/``429`` anywhere in the
    blob — and ``record_fault`` classifies message *plus traceback*. So any real
    bug in a file longer than 400 lines could be filed as the world being the
    world and never become a self-improvement target: the exact failure this
    module was written to prevent, running backwards.
    """

    @pytest.mark.parametrize(
        "traceback_line",
        [
            'AttributeError: no attribute id\n  File "src/remedy/core/agent.py", line 401, in run',
            'KeyError: user_id\n  File "src/remedy/memory/store.py", line 429, in upsert',
            'TypeError: unsupported operand\n  File "src/remedy/core/loop.py", line 403, in step',
        ],
    )
    def test_a_line_number_is_not_an_http_status(self, traceback_line):
        from remedy.core.error_journal import STATUS_OPEN, classify

        assert classify(traceback_line) == STATUS_OPEN

    @pytest.mark.parametrize(
        "text",
        [
            "Error code: 401 - Unauthorized: invalid api key",
            "401 Client Error: Unauthorized for url: https://api.example/v1",
            "HTTP 403: Forbidden",
            "429 Too Many Requests: rate limit reached",
            "ConnectionRefusedError: [Errno 111] Connection refused",
            "socket.gaierror: [Errno 11001] getaddrinfo failed",
        ],
    )
    def test_the_world_being_the_world_is_still_caught(self, text):
        from remedy.core.error_journal import STATUS_ENVIRONMENTAL, classify

        assert classify(text) == STATUS_ENVIRONMENTAL

    def test_an_identifier_that_merely_contains_dns_is_not_a_network_fault(self):
        from remedy.core.error_journal import STATUS_OPEN, classify

        assert classify("AttributeError: module has no attribute parse_dns_config") == (
            STATUS_OPEN
        )

    def test_a_real_fault_with_an_unlucky_traceback_is_targetable(self, tmp_path):
        from remedy.core.error_journal import record_fault

        fault = record_fault(
            "turn_crash",
            "AttributeError: 'NoneType' object has no attribute 'id'",
            exc_type="AttributeError",
            where="agent.py:401",
            traceback='  File "src/remedy/core/agent.py", line 401, in run\n    x.id',
            home=tmp_path,
        )
        assert fault is not None
        assert fault.is_targetable(), (
            "a real crash was filed as environmental because its traceback "
            "mentioned line 401"
        )


class TestWordBoundariesAreRealWordBoundaries:
    r"""The ``\b`` anchors in ``_ENVIRONMENTAL`` were once literal 0x08 backspace
    bytes, so ``\bssl\b`` could only match the byte sequence BS-s-s-l-BS: the
    ssl/dns/socket/HTTP-status branches were silently dead. Each sample below
    carries NO other environmental keyword, so it only passes if the boundary
    branch itself matches."""

    @pytest.mark.parametrize(
        "text",
        [
            "ssl.SSLError: [SSL] record layer failure (_ssl.c:2580)",
            "socket.gaierror: [Errno 11001] name lookup failed",
            "aiohttp.ClientResponseError: 429, message='Too Many Requests'",
            "HTTP 403",
            "HTTP/1.1 401",
            "dns lookup for api.example failed",
        ],
    )
    def test_boundary_branches_match(self, text):
        assert EJ.classify(text) == EJ.STATUS_ENVIRONMENTAL

    @pytest.mark.parametrize(
        "text",
        [
            'ValueError: bad value\n  File "file.py", line 401, in run',
            'IndexError: list index\n  File "file.py", line 429',
            "AttributeError: module has no attribute sslify_url",
            "NameError: name 'websocket_frames' is not defined",
        ],
    )
    def test_boundary_branches_do_not_overreach(self, text):
        assert EJ.classify(text) == EJ.STATUS_OPEN

    def test_module_source_contains_no_control_characters(self):
        import re
        from pathlib import Path

        raw = Path(EJ.__file__).read_bytes()
        stray = re.findall(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", raw)
        assert not stray, f"control bytes in source: {sorted(set(stray))}"
