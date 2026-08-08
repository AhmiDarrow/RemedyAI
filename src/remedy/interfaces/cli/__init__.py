"""Remedy CLI package.

Public entry: ``main`` / ``build_parser``. Private helpers re-exported for tests.
"""

from __future__ import annotations

from remedy.interfaces.cli.cmd_runtime import _cmd_chat, _cmd_desktop, _cmd_serve
from remedy.interfaces.cli.cmd_settings import (
    _cmd_auth,
    _cmd_computer,
    _cmd_config,
    _cmd_settings,
)
from remedy.interfaces.cli.cmd_skills import _cmd_exec, _cmd_learn, _cmd_skill, _cmd_tool
from remedy.interfaces.cli.cmd_store import (
    _cmd_handoff,
    _cmd_memory,
    _cmd_migrate,
    _cmd_session,
    _cmd_user,
)
from remedy.interfaces.cli.main import main
from remedy.interfaces.cli.parser import build_parser
from remedy.interfaces.cli.util import _get_db_path, console

__all__ = [
    "main",
    "build_parser",
    "console",
    "_get_db_path",
    "_cmd_skill",
    "_cmd_tool",
    "_cmd_learn",
    "_cmd_exec",
    "_cmd_memory",
    "_cmd_user",
    "_cmd_session",
    "_cmd_handoff",
    "_cmd_migrate",
    "_cmd_auth",
    "_cmd_config",
    "_cmd_settings",
    "_cmd_computer",
    "_cmd_serve",
    "_cmd_chat",
    "_cmd_desktop",
]

if __name__ == "__main__":
    main()
