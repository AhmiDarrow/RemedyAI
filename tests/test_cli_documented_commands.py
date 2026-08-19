"""Every `remedy …` line in the docs must be a command the parser accepts.

The CLI and the manual drift in opposite directions: a subcommand gets renamed
and the docs keep telling owners to type the old one, or a chapter invents a
flag that was never added. Neither toolchain notices — argparse does not read
Markdown, and the docs gate checks the *slash* commands, not these.
"""

from __future__ import annotations

import contextlib
import io
import re
import shlex
from pathlib import Path

import pytest

from remedy.interfaces.cli.parser import build_parser

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    "README.md",
    "docs/USAGE.md",
    "docs/manual/10-cli-and-api.md",
    "docs/manual/11-reference-commands.md",
    "docs/manual/03-providers-and-auth.md",
    "docs/manual/21-personal-assistant.md",
    "docs/manual/24-telephony.md",
]

#: argparse prints and exits 0 for these — a refusal in form only.
PRINTS_AND_EXITS = {"--help", "-h", "--version"}

#: Only inside fenced blocks. A heading that merely *names* a command is not an
#: invocation of it, and the real line beneath usually carries a placeholder
#: like <session_id> that we cannot supply.
_FENCE = re.compile(r"(?ms)^```[a-z]*$(.*?)^```")
_PLACEHOLDERS = ("…", "...", "<", ">", "|")


def _documented() -> set[str]:
    found: set[str] = set()
    for rel in SOURCES:
        path = ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for block in _FENCE.findall(text):
            for raw in block.splitlines():
                line = raw.strip().rstrip("\\").strip()
                if not line.startswith("remedy "):
                    continue
                line = line[len("remedy ") :].split("#")[0].strip()
                if not line or any(t in line for t in _PLACEHOLDERS):
                    continue
                found.add(line)
    return found


def test_the_docs_actually_contain_command_lines():
    assert len(_documented()) > 30, "the extraction stopped finding `remedy …` lines"


def test_every_documented_command_is_one_the_cli_accepts():
    parser = build_parser()
    refused: list[str] = []
    for command in sorted(_documented()):
        try:
            argv = shlex.split(command)
        except ValueError:  # pragma: no cover — unbalanced quotes in a doc
            continue
        if not argv or argv[0] in PRINTS_AND_EXITS:
            continue
        try:
            with (
                contextlib.redirect_stderr(io.StringIO()),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                parser.parse_args(argv)
        except SystemExit:
            refused.append(command)
        except Exception as exc:
            refused.append(f"{command}   ({type(exc).__name__}: {exc})")

    assert not refused, (
        "the docs tell owners to type commands the CLI refuses:\n  remedy "
        + "\n  remedy ".join(refused)
    )


@pytest.mark.parametrize(
    "command",
    [
        "auth status",
        "auth apikey anthropic",
        "chat",
        "serve",
        "memory list",
        "skill list",
        "config show",
        "setup",
    ],
)
def test_the_commands_an_owner_reaches_for_first(command):
    """Spot-checks, so a rename shows up as the specific command that broke."""
    parser = build_parser()
    with (
        contextlib.redirect_stderr(io.StringIO()),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        parser.parse_args(shlex.split(command))
