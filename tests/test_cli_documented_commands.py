"""Every `remedy …` line in the docs must name a command the Go CLI has.

The CLI and the manual drift in opposite directions: a subcommand gets renamed
and the docs keep telling owners to type the old one. Neither toolchain
notices: the Go command tree does not read Markdown, and the docs gate checks
the *slash* commands, not these.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

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

#: Answered before any command is looked up.
PRINTS_AND_EXITS = {"--help", "-h", "--version", "help"}

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


def _go_commands() -> dict[str, set[str]]:
    """Top-level commands of the Go CLI and, for a group, its subcommands.

    Read from the command tree's source: `name := group("x", …` opens a group,
    the `leaf("y", …` lines under it are its subcommands, and a `leaf` that is
    not under a group is a command of its own.
    """
    tree = (ROOT / "native" / "go" / "cli" / "tree.go").read_text(encoding="utf-8")
    commands: dict[str, set[str]] = {}
    current: str | None = None
    for line in tree.splitlines():
        opened = re.match(r'^\t(?:\w+ := |return )?group\("([\w-]+)"', line) or re.match(
            r'^\t\w+ := &Command\{Name: "([\w-]+)".*Sub: \[\]\*Command\{$', line
        )
        if opened:
            current = opened.group(1)
            commands.setdefault(current, set())
            continue
        if line.rstrip() in ("\t)", "\t}}"):
            current = None
            continue
        for name in re.findall(r'(?:leaf|group)\("([\w-]+)"', line):
            if current is not None and line.startswith("\t\t"):
                commands[current].add(name)
            else:
                commands.setdefault(name, set())
    return commands


def test_every_documented_command_is_one_the_cli_has():
    commands = _go_commands()
    assert {"serve", "memory", "skill", "config", "auth"} <= set(commands), sorted(commands)
    unknown: list[str] = []
    for line in sorted(_documented()):
        try:
            words = shlex.split(line)
        except ValueError:
            continue
        while words and words[0] == "--home":
            words = words[2:]
        if not words or words[0] in PRINTS_AND_EXITS:
            continue
        subs = commands.get(words[0])
        if subs is None:
            unknown.append(line)
            continue
        rest = [w for w in words[1:] if not w.startswith("-")]
        if subs and rest and rest[0] not in subs and rest[0] not in PRINTS_AND_EXITS:
            # a nested group's own subcommands are listed under it too
            if not any(rest[0] in s for s in commands.values()):
                unknown.append(line)
    assert not unknown, "documented but not in the Go CLI:\n  " + "\n  ".join(unknown)
