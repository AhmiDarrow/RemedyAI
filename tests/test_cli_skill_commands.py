"""`remedy skill …` — listing, inspecting, and the probation filter.

The filter is the part with a history. Auto-learned skills go into probation
before they are trusted, and once auto-learning was working there were enough
of them to bury the hand-written ones — `remedy skill list` became unusable for
the thing it is actually for. So probation skills are hidden by default, `--all`
brings them back, and `--learned` shows only them. All three have to keep
working, and the count has to say what is being hidden rather than quietly
showing a short list.

The registry is stubbed; nothing is discovered from disk.
"""

from __future__ import annotations

import argparse

import pytest

from remedy.interfaces.cli import cmd_skills as S


def args(**kw):
    return argparse.Namespace(**kw)


class Manifest:
    def __init__(self, name, *, learned=False, status="active", description="") -> None:
        self.name = name
        self.version = "1.0.0"
        self.description = description or f"{name} does a thing"
        self.metadata = {"auto_generated": True} if learned else {}
        self.status = type("St", (), {"value": status})()
        self.kind = type("K", (), {"value": "script"})()
        self.tags = ["demo"]
        self.path = f"/skills/{name}"


class Skill:
    def __init__(self, name, **kw) -> None:
        self.manifest = Manifest(name, **kw)
        self.instructions = f"how to use {name}"
        self.source_skill_dir = f"/skills/{name}"


@pytest.fixture()
def registry(monkeypatch):
    """Install a stub registry; returns a setter for its contents."""
    holder: dict = {"skills": [], "discovered": []}

    class Reg:
        def __init__(self) -> None:
            self.skills = holder["skills"]

        @property
        def count(self) -> int:
            return len(self.skills)

        def discover_defaults(self):
            return len(self.skills)

        def discover(self, path, recurse=True):
            holder["discovered"].append((path, recurse))
            return 2

        def get(self, name):
            return next((s for s in self.skills if s.manifest.name == name), None)

        def load_single(self, path):
            return Skill("loaded-one")

    monkeypatch.setattr(S, "SkillRegistry", Reg)
    return holder


# --- listing ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_empty_registry_says_how_to_populate_it(registry, capsys):
    await S._cmd_skill(args(skill_cmd="list"))
    out = capsys.readouterr().out
    assert "No skills registered" in out
    assert "skill discover" in out


@pytest.mark.asyncio
async def test_hand_written_skills_are_listed(registry, capsys):
    registry["skills"][:] = [Skill("deploy"), Skill("backup")]
    await S._cmd_skill(args(skill_cmd="list"))
    out = capsys.readouterr().out
    assert "deploy" in out and "backup" in out
    assert "2 skill(s)" in out


@pytest.mark.asyncio
async def test_skills_are_listed_in_name_order(registry, capsys):
    registry["skills"][:] = [Skill("zebra"), Skill("alpha")]
    await S._cmd_skill(args(skill_cmd="list"))
    out = capsys.readouterr().out
    assert out.index("alpha") < out.index("zebra")


@pytest.mark.asyncio
async def test_probation_skills_are_hidden_by_default(registry, capsys):
    """They used to bury the hand-written ones."""
    registry["skills"][:] = [
        Skill("deploy"),
        Skill("learned-a", learned=True, status="probation"),
        Skill("learned-b", learned=True, status="probation"),
    ]
    await S._cmd_skill(args(skill_cmd="list"))
    out = capsys.readouterr().out
    assert "deploy" in out
    assert "learned-a" not in out


@pytest.mark.asyncio
async def test_the_count_admits_what_it_is_hiding(registry, capsys):
    """A short list with no explanation reads as "those skills are gone"."""
    registry["skills"][:] = [
        Skill("deploy"),
        Skill("learned-a", learned=True, status="probation"),
    ]
    await S._cmd_skill(args(skill_cmd="list"))
    out = capsys.readouterr().out
    assert "1 hidden" in out or "hidden" in out
    assert "--all" in out


@pytest.mark.asyncio
async def test_all_brings_the_probation_skills_back(registry, capsys):
    registry["skills"][:] = [
        Skill("deploy"),
        Skill("learned-a", learned=True, status="probation"),
    ]
    await S._cmd_skill(args(skill_cmd="list", all=True))
    out = capsys.readouterr().out
    assert "learned-a" in out and "deploy" in out


@pytest.mark.asyncio
async def test_learned_shows_only_the_learned_ones(registry, capsys):
    registry["skills"][:] = [
        Skill("deploy"),
        Skill("learned-a", learned=True, status="probation"),
    ]
    await S._cmd_skill(args(skill_cmd="list", learned=True))
    out = capsys.readouterr().out
    assert "learned-a" in out
    assert "deploy" not in out


@pytest.mark.asyncio
async def test_a_learned_skill_that_graduated_is_shown_by_default(registry, capsys):
    """Probation is what hides it, not having been learned."""
    registry["skills"][:] = [Skill("learned-good", learned=True, status="active")]
    await S._cmd_skill(args(skill_cmd="list"))
    assert "learned-good" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_a_learned_skill_is_badged_as_learned(registry, capsys):
    registry["skills"][:] = [Skill("learned-good", learned=True, status="active")]
    await S._cmd_skill(args(skill_cmd="list"))
    assert "learned" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_a_long_description_is_trimmed_in_the_list(registry, capsys):
    registry["skills"][:] = [Skill("verbose", description="x" * 500)]
    await S._cmd_skill(args(skill_cmd="list"))
    assert "x" * 200 not in capsys.readouterr().out


# --- info ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_info_describes_a_skill(registry, capsys):
    registry["skills"][:] = [Skill("deploy")]
    await S._cmd_skill(args(skill_cmd="info", name="deploy"))
    out = capsys.readouterr().out
    assert "deploy" in out
    assert "1.0.0" in out


@pytest.mark.asyncio
async def test_info_shows_the_instructions(registry, capsys):
    registry["skills"][:] = [Skill("deploy")]
    await S._cmd_skill(args(skill_cmd="info", name="deploy"))
    assert "how to use deploy" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_info_on_an_unknown_skill_exits_nonzero_and_says_what_to_do(
    registry, capsys
):
    with pytest.raises(SystemExit) as exc:
        await S._cmd_skill(args(skill_cmd="info", name="nope"))
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "not found" in out
    assert "discover" in out


# --- discover and load ---------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_reports_what_it_found(registry, capsys):
    await S._cmd_skill(args(skill_cmd="discover", path="/some/dir", no_recurse=False))
    out = capsys.readouterr().out
    assert "Discovered 2 skill(s)" in out
    assert "/some/dir" in out


@pytest.mark.asyncio
async def test_no_recurse_is_passed_through(registry):
    await S._cmd_skill(args(skill_cmd="discover", path="/d", no_recurse=True))
    assert registry["discovered"][0] == ("/d", False)


@pytest.mark.asyncio
async def test_loading_a_single_skill_names_it(registry, capsys):
    await S._cmd_skill(args(skill_cmd="load", path="/skills/one"))
    assert "loaded-one" in capsys.readouterr().out


# --- running ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_running_an_unknown_skill_exits_nonzero(registry, capsys):
    with pytest.raises(SystemExit) as exc:
        await S._cmd_skill(args(skill_cmd="run", name="nope", script=None))
    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_a_script_outside_the_skill_directory_is_refused(
    registry, capsys, monkeypatch
):
    """A skill may only run scripts from its own folder."""
    from remedy.skills.script_path import SkillScriptJailError

    registry["skills"][:] = [Skill("deploy")]

    def boom(_dir, _script):
        raise SkillScriptJailError("script escapes the skill directory")

    monkeypatch.setattr("remedy.skills.script_path.resolve_jailed_skill_script", boom)
    with pytest.raises(SystemExit):
        await S._cmd_skill(
            args(skill_cmd="run", name="deploy", script="../../etc/passwd")
        )
    assert "escapes" in capsys.readouterr().out
