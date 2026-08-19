"""Repo-relative path normalisation — and the dotfile it used to eat.

Eight call sites wrote ``path.replace("\\\\", "/").lstrip("./")`` to drop a
leading ``./``. ``str.lstrip`` takes a *set of characters*, so it also stripped
the leading dot of every dotfile. Wherever one side of a comparison normalised
and the other did not, ``.github/...`` and ``.gitignore`` stopped matching.
"""

from __future__ import annotations

import pytest

from remedy.core.relpath import norm_rel

#: A literal backslash, spelled without one, so no editor or heredoc on the
#: way in can turn it into an escape sequence.
_BS = chr(92)


@pytest.mark.parametrize(
    ("raw", "expect"),
    [
        ("./src/foo.py", "src/foo.py"),
        ("." + _BS + "src" + _BS + "foo.py", "src/foo.py"),
        ("./././a/b.py", "a/b.py"),
        ("src/foo.py", "src/foo.py"),
        (r"src\win\path.py", "src/win/path.py"),
        # The whole point: a dotfile keeps its dot.
        (".github/workflows/ci.yml", ".github/workflows/ci.yml"),
        (".gitignore", ".gitignore"),
        (".claude/settings.json", ".claude/settings.json"),
        ("...odd", "...odd"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalises_without_eating_dotfiles(raw, expect):
    assert norm_rel(raw) == expect


def test_the_old_idiom_really_did_eat_them():
    """Kept as the reason this module exists."""
    assert ".github/workflows/ci.yml".lstrip("./") == "github/workflows/ci.yml"
    assert norm_rel(".github/workflows/ci.yml") != "github/workflows/ci.yml"


def test_normalising_twice_changes_nothing():
    for raw in ("./a/.hidden/b.py", ".gitignore", "src/x.py"):
        once = norm_rel(raw)
        assert norm_rel(once) == once


def test_a_self_inject_round_recognises_the_dotfile_it_wrote():
    """``git_restore`` compares its write set against ``git ls-files --others``.
    git reports ``.github/workflows/ci.yml``; the write set used to normalise to
    ``github/workflows/ci.yml``, so the two never matched."""
    round_paths = [".github/workflows/ci.yml", "./src/remedy/new_thing.py"]
    mine = {norm_rel(p) for p in round_paths}
    from_git = {".github/workflows/ci.yml", "src/remedy/new_thing.py"}
    assert mine == from_git


def test_glob_matching_sees_dotfiles():
    from remedy.core.file_glob import match_glob

    assert match_glob(".github/workflows/ci.yml", ".github/**/*.yml")
    assert match_glob("./src/remedy/telephony/line.py", "src/**/*.py")
