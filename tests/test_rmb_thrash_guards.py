"""Guards that stop a local model burning its step budget on a closed door.

Both come from watching real local runs against the harness:

* ``file_edit`` with identical old/new returned an *error*, so the model
  resent the same hunk 44 times in one turn while nothing on disk needed to
  change. When that text is already present the model's desired end state is
  already true — that is a satisfied no-op, not a failure.
* The write jail refuses correctly, but a refusal does not stop a model
  retrying it. One run spent 29 consecutive calls reaching for the same
  Desktop path via ``file_write``, then ``bash_exec``, then ``host_run``.
  Every attempt was blocked; the whole budget went to a door that was never
  going to open.
"""

from __future__ import annotations

from types import SimpleNamespace

from remedy.core.file_edit import apply_search_replace
from remedy.core.workspace_tools.guards import note_denied_path

SRC = "def add(a, b):\n    return a + b\n"


class TestIdenticalHunkIsANoOp:
    def test_present_text_is_satisfied_not_failed(self) -> None:
        r = apply_search_replace(SRC, "return a + b", "return a + b")
        assert r.ok, "an already-correct file must not report an edit failure"
        assert r.hunks_applied == 0
        assert r.new_content == SRC, "content must be untouched"

    def test_message_tells_the_model_to_move_on(self) -> None:
        r = apply_search_replace(SRC, "return a + b", "return a + b")
        assert "do not resend" in r.message.lower()

    def test_absent_text_is_still_a_real_failure(self) -> None:
        """The guard must not swallow a genuine miss."""
        r = apply_search_replace(SRC, "return a - b", "return a - b")
        assert not r.ok
        assert "not in the file" in r.message.lower()

    def test_empty_old_string_still_refused(self) -> None:
        assert not apply_search_replace(SRC, "", "x").ok

    def test_a_real_edit_still_applies(self) -> None:
        r = apply_search_replace(SRC, "a + b", "a - b")
        assert r.ok and r.hunks_applied == 1
        assert "a - b" in (r.new_content or "")


class TestDeniedPathEscalation:
    def _runtime(self) -> SimpleNamespace:
        return SimpleNamespace()

    def test_quiet_for_the_first_attempts(self) -> None:
        rt = self._runtime()
        assert note_denied_path(rt, "C:/x/probe.txt") == ""

    def test_escalates_once_the_model_is_clearly_stuck(self) -> None:
        rt = self._runtime()
        msg = ""
        for _ in range(3):
            msg = note_denied_path(rt, "C:/x/probe.txt")
        assert msg, "repeated refusals must escalate"
        assert "stop attempting" in msg.lower()
        assert "tell the owner" in msg.lower()

    def test_names_shell_and_archive_workarounds(self) -> None:
        """The observed escape attempts were shell, archive and encoded."""
        rt = self._runtime()
        for _ in range(3):
            msg = note_denied_path(rt, "C:/x/probe.txt")
        low = msg.lower()
        assert "shell" in low and "archive" in low and "encoded" in low

    def test_counts_are_per_path(self) -> None:
        rt = self._runtime()
        for _ in range(3):
            note_denied_path(rt, "C:/x/a.txt")
        assert note_denied_path(rt, "C:/x/b.txt") == ""

    def test_case_and_blank_handling(self) -> None:
        rt = self._runtime()
        note_denied_path(rt, "C:/X/A.txt")
        note_denied_path(rt, "c:/x/a.txt")
        assert note_denied_path(rt, "C:/x/a.TXT"), "same path differing in case"
        assert note_denied_path(rt, "") == ""

    def test_runtime_without_attribute_support_is_safe(self) -> None:
        class Locked:
            __slots__ = ()

        assert note_denied_path(Locked(), "C:/x/a.txt") == ""


class TestReasoningSpansAreNotTheAnswer:
    """Reasoning models emit their scratchpad inline when the chat template
    does not lift it into ``reasoning_content``. Distills open *every* answer
    with a ``<think>`` block, and nothing stripped it - the owner saw the
    monologue instead of the reply.
    """

    def test_closed_span_is_removed(self) -> None:
        from remedy.core.react_policy import strip_reasoning_spans

        out = strip_reasoning_spans("<think>\nread it first\n</think>\nPort is 8123.")
        assert out == "Port is 8123."

    def test_unclosed_span_leaves_no_answer(self) -> None:
        """Generation stopped mid-thought: there is no reply in the text."""
        from remedy.core.react_policy import strip_reasoning_spans

        assert strip_reasoning_spans("<think>\nstill thinking") == ""

    def test_mid_text_span_is_removed(self) -> None:
        from remedy.core.react_policy import strip_reasoning_spans

        assert strip_reasoning_spans("A.<think>x</think> B.") == "A. B."

    def test_ordinary_html_is_untouched(self) -> None:
        from remedy.core.react_policy import strip_reasoning_spans

        assert strip_reasoning_spans("Use a <div> tag.") == "Use a <div> tag."
        assert strip_reasoning_spans("Port is 8123.") == "Port is 8123."

    def test_variant_tag_names(self) -> None:
        from remedy.core.react_policy import strip_reasoning_spans

        for tag in ("think", "thinking", "reasoning", "thought"):
            assert strip_reasoning_spans(f"<{tag}>x</{tag}>ok") == "ok"

    def test_applied_by_the_stream_cleaner(self) -> None:
        """The loop runs strip_stream_status_noise on every round's text."""
        from remedy.core.react_policy import strip_stream_status_noise

        assert strip_stream_status_noise("<think>a</think>\nDone.") == "Done."
