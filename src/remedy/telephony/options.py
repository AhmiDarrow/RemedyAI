"""The ways Remedy can get a phone line, offered as a choice.

There is no setup wizard. On first run she says what the options are, in plain
sentences, with the honest trade of each one, and the owner picks. That choice
is remembered and can be changed by saying so.

All four are real and supported. The right answer genuinely differs by owner:

* **sip** — her own number over a trunk. The only option that does not depend on
  a second device existing, being awake, or being nearby. Costs a little.
* **vm_voip** — a VoIP app (Google Voice and friends) inside an Android VM on
  this machine. No recurring cost, no proximity, but a cloud service holds the
  number.
* **phone_wired** — the owner's real SIM, audio over a cable, control over the
  network. Keeps their actual number; needs the phone docked.
* **bluetooth_hfp** — the same phone without the cable. Convenient, and the only
  one with a proximity failure mode, so it is never the default.

The proximity point is not a detail. An owner who cannot easily get up and move
a phone cannot depend on a 10-metre radio link, which is why Bluetooth sits last
here and why nothing else in the design requires it.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from remedy.telephony.line import Capabilities

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LineOption:
    """One way to get a line, described the way she would say it out loud."""

    name: str
    title: str
    #: One sentence: what this actually is.
    summary: str
    #: What it costs, in words. "nothing" is a valid answer.
    cost: str
    #: The honest failure mode. Every option has one.
    catch: str
    capabilities: Capabilities = field(default_factory=Capabilities)
    keeps_your_number: bool = False
    #: Audio never leaves this machine.
    local_audio: bool = True
    #: Works with no second device present, awake, or nearby.
    standalone: bool = False
    #: Could this ever work on this machine? Not the same as "set up already":
    #: the SIP engine is a download away, so it belongs on the menu. A PC with
    #: no Bluetooth radio genuinely cannot do Bluetooth, so that one does not.
    achievable: bool = True
    #: What is stopping it right now, in sentences.
    missing: tuple[str, ...] = ()
    #: The single next action, if a human has to take one.
    action: str = ""

    @property
    def ready(self) -> bool:
        return not self.missing

    def say(self) -> str:
        """How this option is offered aloud."""
        line = f"{self.title} — {self.summary} Cost: {self.cost}. {self.catch}"
        if self.missing:
            line += f" To use it: {self.action or self.missing[0]}"
        return line


def _cfg_path(home: Path | str | None = None) -> Path:
    base = Path(home or os.environ.get("REMEDY_HOME", "~/.remedy")).expanduser()
    d = base / "telephony"
    d.mkdir(parents=True, exist_ok=True)
    return d / "line.json"


def chosen(home: Path | str | None = None) -> str:
    """Which line the owner picked, or "" if they have not been asked yet."""
    try:
        raw = json.loads(_cfg_path(home).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(raw.get("line") or "") if isinstance(raw, dict) else ""


def choose(name: str, home: Path | str | None = None) -> str:
    """Remember the owner's pick. Changing it later is just saying so again."""
    _cfg_path(home).write_text(
        json.dumps({"line": name}, indent=2) + "\n", encoding="utf-8"
    )
    return name


def offer(options: list[LineOption], *, recommend: str = "") -> str:
    """The spoken menu.

    Ordered by how little the owner has to depend on: standalone first, the
    proximity-bound one last. Only options that could actually work here are
    offered — suggesting something impossible on this machine wastes their time.
    """
    usable = [o for o in options if o.achievable]
    if not usable:
        return "I cannot find any way to get a phone line on this machine yet."
    ordered = sorted(usable, key=lambda o: (not o.standalone, bool(o.missing)))
    counts = {2: "two ways", 3: "three ways", 4: "four ways"}
    if len(ordered) == 1:
        head = "There is one way"
    else:
        head = f"There are {counts.get(len(ordered), f'{len(ordered)} ways')}"
    lines = [f"{head} I can get a phone line."]
    for i, option in enumerate(ordered, 1):
        mark = ""
        if recommend and option.name == recommend:
            mark = " (my suggestion)"
        elif option.ready:
            mark = " (ready now)"
        lines.append(f"{i}) {option.say()}{mark}")
    lines.append("Which would you like me to set up?")
    return "\n".join(lines)
