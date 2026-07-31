"""One-shot proof: shell write jail blocks SecretSticky -> SecretFolder."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from remedy.core.agent_workspace_tools import register_workspace_tools
from remedy.core.approvals import APPROVALS
from remedy.core.shell_write_jail import check_shell_write_jail
from remedy.core.workspace import (
    allowed_roots_for_scope,
    resolve_under_roots,
    write_roots_for_scope,
)
from remedy.skills.tool_registry import ToolRegistry

STICKY = Path(r"C:\Users\Administrator\SecretSticky").resolve()
FOLDER = Path(r"C:\Users\Administrator\SecretFolder").resolve()
HOME = Path.home()


def main() -> int:
    cmd = f'Set-Content -Path "{FOLDER / "pwn_from_sticky.txt"}" -Value pwned'
    hit = check_shell_write_jail(
        cmd,
        write_roots=[STICKY],
        cwd=STICKY,
        project_bound=True,
        access_scope="project",
    )
    print("UNIT_BLOCK", hit is not None)
    if hit:
        print("UNIT_REASON", hit[:220])

    APPROVALS.needs_ask = lambda *a, **k: None  # type: ignore[method-assign]

    class RT:
        def access_scope(self) -> str:
            return "project"

        def effective_project_path(self) -> Path:
            return STICKY

        def write_roots(self):
            return write_roots_for_scope("project", STICKY, home=HOME)

        def allowed_roots(self):
            return allowed_roots_for_scope("project", STICKY, home=HOME)

        def project_path_is_unset(self) -> bool:
            return False

        def resolve_tool_path(self, path: str, for_write: bool = False) -> Path:
            roots = self.write_roots() if for_write else self.allowed_roots()
            enf = "project" if for_write else self.access_scope()
            return resolve_under_roots(path or ".", roots, access_scope=enf)

        def _track_artifact(self, *_a, **_k) -> None:
            pass

        def _register_comfyui_tools(self) -> None:
            pass

        def _register_vision_tools(self) -> None:
            pass

        def _register_local_discover_tools(self) -> None:
            pass

        def _register_skill_tools(self) -> None:
            pass

    async def run_tool() -> str:
        rt = RT()
        reg = ToolRegistry()
        rt.tool_registry = reg  # type: ignore[attr-defined]
        rt.config = SimpleNamespace(home_dir=str(HOME / ".remedy"))  # type: ignore[attr-defined]
        rt._session_id = "prove"  # type: ignore[attr-defined]
        register_workspace_tools(rt)
        return await reg.execute("bash_exec", command=cmd)

    out = asyncio.run(run_tool())
    target = FOLDER / "pwn_from_sticky.txt"
    print("TOOL_HAS_WRITE_JAIL", "WRITE_JAIL" in out or "jail" in out.lower())
    print("TOOL_SNIP", out.replace("\n", " | ")[:280])
    print("FILE_CREATED", target.exists())
    if target.exists():
        target.unlink(missing_ok=True)
        return 2
    return 0 if hit and ("WRITE_JAIL" in out or "jail" in out.lower()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
