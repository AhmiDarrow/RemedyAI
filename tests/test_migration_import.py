"""Importing skills from a Hermes or OpenClaw installation.

Someone arriving from another agent already has work they care about. The
promise of this module is that the import either brings a skill across or says
which one it could not and why — never a silent partial that leaves the owner
believing everything came over.

So the counts and the error list are the subject: imported + skipped must
account for every skill discovered, and a failure must name the skill.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.migrate.from_hermes import (
    MigrationResult,
    migrate_from_hermes,
    migrate_from_openclaw,
)


class FakeManifest:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeSkill:
    def __init__(self, name: str, source_dir: str | None = None) -> None:
        self.manifest = FakeManifest(name)
        self.source_skill_dir = source_dir


class FakeRegistry:
    def __init__(self, *, fail_on: set[str] | None = None) -> None:
        self.registered: list[str] = []
        self._fail_on = fail_on or set()

    def register(self, skill) -> None:
        if skill.manifest.name in self._fail_on:
            raise ValueError("manifest is not valid")
        self.registered.append(skill.manifest.name)


@pytest.fixture()
def source(tmp_path):
    """A source installation with two skill directories."""
    root = tmp_path / "hermes" / "skills"
    for name in ("alpha", "beta"):
        d = root / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    return root


@pytest.fixture()
def dest(tmp_path):
    d = tmp_path / "remedy" / "skills"
    d.mkdir(parents=True)
    return d


def _discover(root: Path):
    return [FakeSkill(p.name, str(p)) for p in sorted(root.iterdir()) if p.is_dir()]


@pytest.fixture()
def discovery(monkeypatch):
    monkeypatch.setattr("remedy.migrate.from_hermes.discover_hermes_skills", _discover)
    monkeypatch.setattr("remedy.migrate.from_hermes.discover_openclaw_skills", _discover)


# --- the result object -------------------------------------------------------


def test_a_fresh_result_has_nothing_in_it():
    r = MigrationResult()
    assert r.skills_imported == 0
    assert r.skills_skipped == 0
    assert r.errors == []
    assert r.total_processed == 0


def test_the_total_accounts_for_both_outcomes():
    r = MigrationResult()
    r.skills_imported, r.skills_skipped = 3, 2
    assert r.total_processed == 5


def test_the_result_serialises_for_the_cli():
    r = MigrationResult()
    r.skills_imported = 1
    r.errors.append("boom")
    assert r.to_dict() == {"skills_imported": 1, "skills_skipped": 0, "errors": ["boom"]}


# --- a missing source --------------------------------------------------------


@pytest.mark.parametrize("migrate", [migrate_from_hermes, migrate_from_openclaw])
def test_a_missing_source_directory_is_an_error_not_a_silent_zero(
    tmp_path, discovery, migrate
):
    """"Imported 0 skills" reads as "you had none", which is a different thing."""
    reg = FakeRegistry()
    result = migrate(reg, tmp_path / "not-here")
    assert result.skills_imported == 0
    assert result.errors
    assert "not found" in result.errors[0]


def test_the_error_names_which_installation_it_looked_for(tmp_path, discovery):
    hermes = migrate_from_hermes(FakeRegistry(), tmp_path / "nope")
    openclaw = migrate_from_openclaw(FakeRegistry(), tmp_path / "nope")
    assert "Hermes" in hermes.errors[0]
    assert "OpenClaw" in openclaw.errors[0]


def test_a_file_where_a_directory_should_be_is_an_error(tmp_path, discovery):
    stray = tmp_path / "skills"
    stray.write_text("not a directory", encoding="utf-8")
    assert migrate_from_hermes(FakeRegistry(), stray).errors


# --- importing ---------------------------------------------------------------


def test_every_discovered_skill_is_registered(source, discovery):
    reg = FakeRegistry()
    result = migrate_from_hermes(reg, source, copy_to_remedy=False)
    assert result.skills_imported == 2
    assert reg.registered == ["alpha", "beta"]
    assert result.errors == []


def test_openclaw_imports_the_same_way(source, discovery):
    reg = FakeRegistry()
    assert migrate_from_openclaw(reg, source, copy_to_remedy=False).skills_imported == 2


def test_the_skill_files_are_copied_into_remedys_own_directory(source, dest, discovery):
    migrate_from_hermes(FakeRegistry(), source, remedy_skills_dir=dest)
    assert (dest / "alpha" / "SKILL.md").is_file()
    assert (dest / "beta" / "SKILL.md").is_file()


def test_a_copied_skill_points_at_its_new_home(source, dest, discovery):
    """Left pointing at the old install, the skill breaks when Hermes is removed."""
    class Collecting(FakeRegistry):
        def __init__(self) -> None:
            super().__init__()
            self.objects: list = []

        def register(self, skill) -> None:
            self.objects.append(skill)
            super().register(skill)

    reg = Collecting()
    migrate_from_hermes(reg, source, remedy_skills_dir=dest)
    assert reg.objects
    assert all(str(dest) in s.source_skill_dir for s in reg.objects)


def test_an_existing_destination_is_not_overwritten(source, dest, discovery):
    """A second import must not clobber a skill the owner has since edited."""
    (dest / "alpha").mkdir()
    (dest / "alpha" / "SKILL.md").write_text("# my edited version\n", encoding="utf-8")
    migrate_from_hermes(FakeRegistry(), source, remedy_skills_dir=dest)
    assert "my edited version" in (dest / "alpha" / "SKILL.md").read_text(encoding="utf-8")


def test_nothing_is_copied_when_copying_is_off(source, dest, discovery):
    migrate_from_hermes(FakeRegistry(), source, copy_to_remedy=False, remedy_skills_dir=dest)
    assert list(dest.iterdir()) == []


def test_copying_without_a_destination_still_registers(source, discovery):
    """No destination means register in place rather than refuse."""
    reg = FakeRegistry()
    assert migrate_from_hermes(reg, source, copy_to_remedy=True).skills_imported == 2


# --- partial failure ---------------------------------------------------------


def test_one_bad_skill_does_not_stop_the_rest(source, discovery):
    reg = FakeRegistry(fail_on={"alpha"})
    result = migrate_from_hermes(reg, source, copy_to_remedy=False)
    assert reg.registered == ["beta"]
    assert result.skills_imported == 1
    assert result.skills_skipped == 1


def test_a_failure_names_the_skill_and_the_reason(source, discovery):
    result = migrate_from_hermes(
        FakeRegistry(fail_on={"alpha"}), source, copy_to_remedy=False
    )
    assert "alpha" in result.errors[0]
    assert "manifest is not valid" in result.errors[0]


def test_the_counts_account_for_everything_discovered(source, discovery):
    result = migrate_from_hermes(
        FakeRegistry(fail_on={"alpha", "beta"}), source, copy_to_remedy=False
    )
    assert result.total_processed == 2
    assert result.skills_imported == 0
    assert len(result.errors) == 2


def test_an_empty_source_directory_imports_nothing_without_erroring(tmp_path, discovery):
    empty = tmp_path / "skills"
    empty.mkdir()
    result = migrate_from_hermes(FakeRegistry(), empty, copy_to_remedy=False)
    assert result.total_processed == 0
    assert result.errors == []


def test_a_user_path_is_expanded(tmp_path, discovery, monkeypatch):
    """`~/hermes/skills` is what people actually type."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / "skills" / "alpha").mkdir(parents=True)
    result = migrate_from_hermes(FakeRegistry(), "~/skills", copy_to_remedy=False)
    assert result.skills_imported == 1
