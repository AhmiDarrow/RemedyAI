"""The terms gate, and shipping nothing until asked.

Both are liability surfaces, so they are tested as behaviour rather than trusted
to callers remembering to check.
"""

from __future__ import annotations

import pytest

from remedy.core import terms
from remedy.telephony import consent


@pytest.fixture(autouse=True)
def _general_terms_agreed(tmp_path):
    """These tests are about the *phone* gate specifically.

    The product-wide terms sit underneath it and are exercised in
    ``test_terms.py``; agreeing to them here keeps each test about one thing.
    """
    terms.accept(tmp_path)


def test_no_consent_by_default(tmp_path):
    assert not consent.read(tmp_path).current


def test_real_calls_are_blocked_until_agreed(tmp_path):
    with pytest.raises(consent.TermsNotAcceptedError):
        consent.require(tmp_path)


def test_the_block_says_something_a_person_can_answer(tmp_path):
    """Refusing to dial has to produce speech, not an error code."""
    with pytest.raises(consent.TermsNotAcceptedError) as err:
        consent.require(tmp_path)
    said = str(err.value)
    assert "emergency" in said.lower()
    assert said.rstrip().endswith("?")


def test_accepting_unblocks_and_is_recorded(tmp_path):
    consent.accept(tmp_path)
    consent.require(tmp_path)  # must not raise
    stored = consent.read(tmp_path)
    assert stored.current and stored.at


def test_withdrawing_blocks_again(tmp_path):
    consent.accept(tmp_path)
    consent.withdraw(tmp_path)
    with pytest.raises(consent.TermsNotAcceptedError):
        consent.require(tmp_path)


def test_withdrawing_when_nothing_was_agreed_is_harmless(tmp_path):
    consent.withdraw(tmp_path)


def test_a_material_change_re_asks_and_says_what_changed(tmp_path, monkeypatch):
    consent.accept(tmp_path)
    monkeypatch.setattr(consent, "TERMS_VERSION", consent.TERMS_VERSION + 1)
    monkeypatch.setitem(
        consent.TERMS_CHANGES, consent.TERMS_VERSION, "recording now defaults to on"
    )
    stored = consent.read(tmp_path)
    assert stored.stale and not stored.current
    said = consent.ask(tmp_path)
    assert "changed" in said
    assert "recording now defaults to on" in said


def test_nothing_more_is_said_once_agreed(tmp_path):
    consent.accept(tmp_path)
    assert consent.ask(tmp_path) == ""


def test_corrupt_consent_file_reads_as_not_agreed(tmp_path):
    (tmp_path / "telephony").mkdir()
    (tmp_path / "telephony" / "consent.json").write_text("{oops", encoding="utf-8")
    assert not consent.read(tmp_path).current


def test_emergency_warning_comes_first(tmp_path):
    """Of everything in the terms, this is the one that can get someone hurt."""
    assert "emergency" in consent.SPOKEN_POINTS[0].lower()


def test_spoken_terms_stay_short_enough_to_listen_to():
    """Terms nobody listens to protect nobody."""
    assert len(consent.SPOKEN_POINTS) <= 6
    for point in consent.SPOKEN_POINTS:
        assert len(point) < 240


def test_every_fetchable_component_declares_its_licence():
    for name, component in consent.COMPONENTS.items():
        assert component.licence, f"{name} has no licence stated"
        assert component.approx_mb > 0
        assert component.source


def test_download_is_offered_before_it_happens():
    said = consent.offer_download(["baresip", "smart-turn"])
    assert "BSD-3-Clause" in said and "BSD-2-Clause" in said
    assert said.rstrip().endswith("?")
    assert "51 MB" in said  # 6 + 45, totalled honestly


def test_unknown_components_are_ignored_rather_than_guessed():
    assert consent.offer_download(["nonsense"]) == ""


def test_singular_download_reads_correctly():
    said = consent.offer_download(["baresip"])
    assert "1 thing I do not ship with" in said


# ---------------------------------------------------------------------------
# The gate has to bite at the choke point, not merely exist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_real_line_will_not_dial_before_the_terms_are_agreed(tmp_path):
    from remedy.telephony.line import Call, Capabilities, Line

    class _RealBackend:
        name = "pretend-real"

        def capabilities(self):
            return Capabilities(outbound=True)  # simulated defaults to False

        async def place(self, number: str) -> Call:
            raise AssertionError("dialled before the owner agreed")

    with pytest.raises(consent.TermsNotAcceptedError):
        await Line(backend=_RealBackend(), home=tmp_path).place("+15550100")


@pytest.mark.asyncio
async def test_the_bench_needs_no_agreement(tmp_path):
    """Simulated calls reach nobody, so requiring consent to test would only
    teach owners to click through terms that matter elsewhere."""
    from remedy.telephony.backends.fake import FakeBackend
    from remedy.telephony.line import Line

    line = Line(backend=FakeBackend(), home=tmp_path)
    call = await line.place("+15550100")
    assert call is not None
    await call.hangup()


@pytest.mark.asyncio
async def test_a_real_line_dials_once_agreed(tmp_path):
    from remedy.telephony.line import Call, CallDirection, Capabilities, Line

    class _RealBackend:
        name = "pretend-real"

        def capabilities(self):
            return Capabilities(outbound=True)

        async def place(self, number: str) -> Call:
            return Call(remote=number, direction=CallDirection.OUTBOUND)

    consent.accept(tmp_path)
    call = await Line(backend=_RealBackend(), home=tmp_path).place("+15550100")
    assert call.remote == "+15550100"
