"""repo_search / file_glob / list_dir — what Remedy can actually find.

These three are how she discovers a codebase she has never seen. A gap here is
not a crash; it is a file she never learns exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.core.workspace_tools.search import register_search_tools


class Reg:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}
        self.schemas: dict[str, dict] = {}

    def register_builtin_handler(self, name, description, handler, parameters=None):
        self.tools[name] = handler
        self.schemas[name] = parameters or {}


class RT:
    """Minimal runtime: a jail rooted at one tree."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.tool_registry = Reg()
        self.config = type("C", (), {"home_dir": str(root)})()
        self.read: list[Path] = []

    def effective_project_path(self) -> Path:
        return self.root

    def resolve_tool_path(self, path: str, *, for_write: bool = False) -> Path:
        target = (self.root / (path or ".")).resolve()
        if not str(target).startswith(str(self.root.resolve())):
            raise PermissionError(f"outside jail: {path}")
        return target

    def _track_artifact(self, _p) -> None:
        pass


@pytest.fixture()
def tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "class Widget:\n    pass\n\n\ndef build_widget():\n    return Widget()\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "notes.txt").write_text("nothing here\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "workflows").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/master\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    rt = RT(tmp_path)
    register_search_tools(rt)
    return rt


def tools(tree):
    return tree.tool_registry.tools


# --- list_dir ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_dir_shows_files_and_directories(tree):
    out = await tools(tree)["list_dir"]()
    assert "dir  src" in out.replace("dir ", "dir  ", 1) or "src" in out
    assert "README.md" in out


@pytest.mark.asyncio
async def test_list_dir_shows_dotfiles(tree):
    """The regression this module used to have: .github/ was invisible."""
    out = await tools(tree)["list_dir"]()
    assert ".gitignore" in out
    assert ".github" in out


@pytest.mark.asyncio
async def test_list_dir_still_hides_machine_noise(tree):
    out = await tools(tree)["list_dir"]()
    assert ".git\n" not in out and "/.git" not in out
    assert "__pycache__" not in out


@pytest.mark.asyncio
async def test_list_dir_hides_credential_files_but_not_their_templates(tree):
    """Showing dotfiles must not mean advertising where the keys live."""
    root = tree.root
    for name in (".env", ".env.production", ".npmrc", ".pypirc", ".netrc",
                 ".git-credentials", "id_rsa", "id_rsa.pub", "server.pem", "tls.key"):
        (root / name).write_text("secret" + chr(10), encoding="utf-8")
    (root / ".ssh").mkdir()
    (root / ".aws").mkdir()
    (root / ".env.example").write_text("KEY=" + chr(10), encoding="utf-8")
    out = await tools(tree)["list_dir"]()
    names = {line.split(" ", 1)[1].strip() for line in out.splitlines() if " " in line}
    hidden = {".env", ".env.production", ".npmrc", ".pypirc", ".netrc",
              ".git-credentials", "id_rsa", "id_rsa.pub", "server.pem", "tls.key",
              ".ssh", ".aws"}
    assert not (names & hidden), names & hidden
    assert ".env.example" in names
    assert ".gitignore" in names


@pytest.mark.asyncio
async def test_list_dir_on_a_missing_path_says_where_to_look(tree):
    out = await tools(tree)["list_dir"](path="nope")
    assert "NOT_FOUND" in out
    assert "list_dir" in out


@pytest.mark.asyncio
async def test_list_dir_on_a_file_points_at_file_read(tree):
    out = await tools(tree)["list_dir"](path="README.md")
    assert "NOT_A_DIRECTORY" in out
    assert "file_read" in out


@pytest.mark.asyncio
async def test_list_dir_refuses_to_leave_the_jail(tree):
    with pytest.raises(PermissionError):
        await tools(tree)["list_dir"](path="../..")


@pytest.mark.asyncio
async def test_list_dir_paginates_and_says_how_to_continue(tree):
    for i in range(12):
        (tree.root / f"f{i:02d}.md").write_text("x", encoding="utf-8")
    out = await tools(tree)["list_dir"](limit=5)
    assert "offset=5" in out
    page2 = await tools(tree)["list_dir"](limit=5, offset=5)
    assert page2 != out


@pytest.mark.asyncio
async def test_list_dir_survives_a_nonsense_limit(tree):
    assert "README.md" in await tools(tree)["list_dir"](limit="many")


@pytest.mark.asyncio
async def test_list_dir_caps_a_huge_limit(tree):
    assert "README.md" in await tools(tree)["list_dir"](limit=10**9)


@pytest.mark.asyncio
async def test_list_dir_of_an_empty_directory_says_so(tree):
    (tree.root / "empty").mkdir()
    assert (await tools(tree)["list_dir"](path="empty")).strip() == "(empty)"


# --- repo_search ------------------------------------------------------------


@pytest.mark.asyncio
async def test_repo_search_finds_a_literal(tree):
    out = await tools(tree)["repo_search"](pattern="build_widget")
    assert "app.py" in out


@pytest.mark.asyncio
async def test_repo_search_by_symbol_finds_the_definition(tree):
    out = await tools(tree)["repo_search"](symbol="Widget")
    assert "app.py" in out


@pytest.mark.asyncio
async def test_repo_search_needs_something_to_search_for(tree):
    out = await tools(tree)["repo_search"]()
    assert "MISSING_PATTERN" in out


@pytest.mark.asyncio
async def test_repo_search_honours_a_glob(tree):
    out = await tools(tree)["repo_search"](pattern="nothing", glob="*.py")
    assert "notes.txt" not in out


@pytest.mark.asyncio
async def test_repo_search_refuses_a_path_outside_the_jail(tree):
    out = await tools(tree)["repo_search"](pattern="x", path="../../etc")
    assert "PATH_DENIED" in out


@pytest.mark.asyncio
async def test_repo_search_case_insensitive(tree):
    assert "app.py" in await tools(tree)["repo_search"](
        pattern="WIDGET", case_insensitive=True
    )


@pytest.mark.asyncio
async def test_repo_search_returns_context_lines_when_asked(tree):
    plain = await tools(tree)["repo_search"](pattern="pass")
    ctx = await tools(tree)["repo_search"](pattern="pass", context_before=1)
    assert len(ctx) >= len(plain)


# --- file_glob --------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_glob_finds_by_extension(tree):
    out = await tools(tree)["file_glob"](pattern="*.py")
    assert "app.py" in out


@pytest.mark.asyncio
async def test_file_glob_needs_a_pattern(tree):
    assert "MISSING_PATTERN" in await tools(tree)["file_glob"]()


@pytest.mark.asyncio
async def test_file_glob_on_a_missing_root_says_so(tree):
    assert "NOT_FOUND" in await tools(tree)["file_glob"](pattern="*.py", path="nope")


@pytest.mark.asyncio
async def test_file_glob_given_a_file_searches_its_directory(tree):
    out = await tools(tree)["file_glob"](pattern="*.py", path="src/app.py")
    assert "app.py" in out


@pytest.mark.asyncio
async def test_file_glob_refuses_to_leave_the_jail(tree):
    assert "PATH_DENIED" in await tools(tree)["file_glob"](pattern="*", path="../..")


@pytest.mark.asyncio
async def test_file_glob_survives_a_nonsense_cap(tree):
    assert "app.py" in await tools(tree)["file_glob"](pattern="*.py", max_results="lots")


# --- registration -----------------------------------------------------------


def test_all_three_tools_are_registered_with_schemas(tree):
    for name in ("repo_search", "file_glob", "list_dir"):
        assert name in tree.tool_registry.tools
        assert tree.tool_registry.schemas[name]["type"] == "object"


def test_file_glob_declares_its_required_argument(tree):
    assert tree.tool_registry.schemas["file_glob"]["required"] == ["pattern"]
