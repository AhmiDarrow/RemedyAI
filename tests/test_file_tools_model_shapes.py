"""Tool errors a model can actually act on, and arg shapes it actually sends.

Two failures found by driving Remedy with a local model:

* ``file_edit`` refused ``edits=[{path, old_string, new_string}]`` with
  MISSING_PATH, even though ``file_edit_batch`` accepts exactly that shape.
  Every edit the model attempted failed, so it could never fix a bug.
* A write into ``mypkg/__init__.py`` failed with
  ``[WinError 183] ... already exists: ...mypkg`` because ``mypkg`` existed as
  a *file*. The message named the target while the problem was an ancestor,
  which reads as a contradiction and left the model with no move.
"""

from __future__ import annotations

from pathlib import Path

from remedy.core.workspace_tools.files import _edits_carry_paths
from remedy.core.workspace_tools.guards import blocking_file_ancestor


class TestEditsCarryPaths:
    def test_json_string_with_paths(self) -> None:
        assert _edits_carry_paths(
            '[{"path":"calc.py","old_string":"a","new_string":"b"}]'
        )

    def test_parsed_list_with_paths(self) -> None:
        assert _edits_carry_paths(
            [{"path": "a.py", "old_string": "x", "new_string": "y"}]
        )

    def test_single_dict_with_path(self) -> None:
        assert _edits_carry_paths({"path": "a.py", "old_string": "x", "new_string": "y"})

    def test_hunks_without_paths_still_need_top_level_path(self) -> None:
        assert not _edits_carry_paths('[{"old_string":"a","new_string":"b"}]')

    def test_mixed_hunks_are_not_self_describing(self) -> None:
        """One hunk missing a path means the batch tool cannot place it."""
        assert not _edits_carry_paths(
            '[{"path":"a.py","old_string":"x","new_string":"y"},'
            ' {"old_string":"p","new_string":"q"}]'
        )

    def test_blank_and_malformed_are_rejected(self) -> None:
        assert not _edits_carry_paths("")
        assert not _edits_carry_paths("not json")
        assert not _edits_carry_paths("[]")
        assert not _edits_carry_paths(None)

    def test_empty_path_value_does_not_count(self) -> None:
        assert not _edits_carry_paths('[{"path":"  ","old_string":"a","new_string":"b"}]')


class TestBlockingFileAncestor:
    def test_names_the_file_that_blocks_the_folder(self, tmp_path: Path) -> None:
        blocker = tmp_path / "mypkg"
        blocker.write_text("stray", encoding="utf-8")
        found = blocking_file_ancestor(blocker / "__init__.py")
        assert found == blocker

    def test_none_when_parents_are_directories(self, tmp_path: Path) -> None:
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        assert blocking_file_ancestor(pkg / "__init__.py") is None

    def test_none_for_a_plain_new_path(self, tmp_path: Path) -> None:
        assert blocking_file_ancestor(tmp_path / "fresh" / "deep" / "f.py") is None

    def test_finds_a_blocker_several_levels_up(self, tmp_path: Path) -> None:
        blocker = tmp_path / "pkg"
        blocker.write_text("stray", encoding="utf-8")
        assert blocking_file_ancestor(blocker / "sub" / "deeper" / "f.py") == blocker

    def test_empty_input(self) -> None:
        assert blocking_file_ancestor(None) is None
        assert blocking_file_ancestor("") is None
