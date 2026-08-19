"""The product-wide agreement: every action Remedy takes is the owner's.

Remedy runs commands, edits files, and spends money through connected accounts.
The owner is told that once, plainly, before any of it — so these tests care
about the words as much as the mechanism.
"""

from __future__ import annotations

import pytest

from remedy.core import terms


def test_nothing_is_agreed_by_default(tmp_path):
    assert not terms.accepted(tmp_path)


def test_acting_is_gated_until_agreed(tmp_path):
    with pytest.raises(terms.TermsNotAcceptedError):
        terms.require(tmp_path)


def test_the_refusal_is_speech_not_an_error_code(tmp_path):
    with pytest.raises(terms.TermsNotAcceptedError) as err:
        terms.require(tmp_path)
    said = str(err.value)
    assert "?" in said
    for shouty in ("Traceback", "ERR_", "None"):
        assert shouty not in said


def test_agreeing_unblocks_and_is_dated(tmp_path):
    terms.accept(tmp_path)
    terms.require(tmp_path)
    assert terms.read(tmp_path).at


def test_withdrawing_blocks_again(tmp_path):
    terms.accept(tmp_path)
    terms.withdraw(tmp_path)
    assert not terms.accepted(tmp_path)


def test_a_material_change_re_asks_and_says_what_changed(tmp_path, monkeypatch):
    terms.accept(tmp_path)
    monkeypatch.setattr(terms, "TERMS_VERSION", terms.TERMS_VERSION + 1)
    monkeypatch.setitem(terms.TERMS_CHANGES, terms.TERMS_VERSION, "she can now send email")
    assert terms.read(tmp_path).stale
    said = terms.ask(tmp_path)
    assert "changed" in said and "she can now send email" in said


def test_corrupt_file_reads_as_not_agreed(tmp_path):
    (tmp_path / "terms.json").write_text("{oops", encoding="utf-8")
    assert not terms.accepted(tmp_path)


def test_nothing_is_said_once_agreed(tmp_path):
    terms.accept(tmp_path)
    assert terms.ask(tmp_path) == ""


def test_the_agency_point_is_made_first():
    """The single most important sentence: what she does counts as the owner
    doing it. If that is buried, the rest does not matter."""
    first = terms.SPOKEN_POINTS[0].lower()
    assert "counts as you" in first


def test_liability_is_stated_plainly():
    joined = " ".join(terms.SPOKEN_POINTS).lower()
    assert "no warranty" in joined and "liability" in joined


def test_spoken_terms_stay_short_enough_to_listen_to():
    assert len(terms.SPOKEN_POINTS) <= 6
    for point in terms.SPOKEN_POINTS:
        assert len(point) < 240


def test_telephony_requires_the_general_terms_too(tmp_path):
    """Agreeing to use a file manager is not agreeing to let a machine phone
    strangers — and vice versa, the phone terms do not stand alone."""
    from remedy.telephony import consent

    consent.accept(tmp_path)  # phone terms only
    with pytest.raises(terms.TermsNotAcceptedError):
        consent.require(tmp_path)

    terms.accept(tmp_path)
    consent.require(tmp_path)  # both now agreed


def test_general_terms_alone_do_not_unlock_the_phone(tmp_path):
    from remedy.telephony import consent

    terms.accept(tmp_path)
    with pytest.raises(consent.TermsNotAcceptedError):
        consent.require(tmp_path)


def test_the_spoken_count_matches_the_points_actually_said():
    """"five things" followed by six things is the kind of small wrongness that
    makes everything else she says sound unreliable."""
    from remedy.core import terms as t

    said = t.ask(home="/nonexistent-home-for-this-test")
    assert f"{t._count(len(t.SPOKEN_POINTS))} things" in said
    assert said.count("\n- ") == len(t.SPOKEN_POINTS)
