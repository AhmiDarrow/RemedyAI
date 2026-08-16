"""Appliances — what's in her house, and how to switch any of it on.

The stretch census maps the house's bones: hardware, rooms (user folders),
doors (local ports), workshop tools (PATH binaries). But a home is lived
in through its **appliances** — the applications the owner actually
installed: their browser, their music, their photo editor, their games.
Until now Remedy could only name a dozen hardcoded ones; the rest of her
own house was invisible to her.

This module is the appliance inventory:

- **Scan** — walk the Start Menu Programs folders (the exact set of things
  the owner sees when they press Start; on Linux, XDG .desktop entries).
  Bounded walk, no disk crawl, no registry, no processes, no secrets.
  Persists to ``~/.remedy/host/appliances.json``; refreshes when stale.
- **Resolve** — natural-name lookup with fuzzy scoring, so
  ``computer_app app="spotify"`` — or "word", or "steam" — just works,
  and a near-miss returns *did-you-mean* suggestions instead of a dead
  error.
- **Launch** — on Windows an appliance is started via its own shortcut
  (``os.startfile`` on the ``.lnk``), which preserves the app's intended
  arguments and working directory.

Zero-command principle: the inventory builds itself in the background the
first time computer tools register; the owner never runs anything.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

APPLIANCES_REL = Path("host") / "appliances.json"
SCHEMA_VERSION = 1
STALE_DAYS = 7
MAX_APPLIANCES = 800
MAX_WALK_DEPTH = 3
# Shortcuts that are chrome around the house, not appliances
_JUNK_BITS = (
    "uninstall",
    "remove ",
    "readme",
    "release notes",
    "help",
    "website",
    "documentation",
    "eula",
    "license",
)


@dataclass
class Appliance:
    """One launchable thing in her house."""

    name: str = ""
    path: str = ""  # .lnk on Windows, .desktop on Linux
    kind: str = "lnk"  # lnk | desktop
    room: str = ""  # Start Menu folder / category it lives under

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApplianceMatch:
    appliance: Appliance
    score: int


@dataclass
class ApplianceInventory:
    schema: int = SCHEMA_VERSION
    scanned_at: float = 0.0
    appliances: list[Appliance] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "scanned_at": self.scanned_at,
            "appliances": [a.to_dict() for a in self.appliances],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> ApplianceInventory:
        raw = raw or {}
        apps: list[Appliance] = []
        for a in raw.get("appliances") or []:
            if not isinstance(a, dict):
                continue
            name = str(a.get("name") or "").strip()
            path = str(a.get("path") or "").strip()
            if not name or not path:
                continue
            apps.append(
                Appliance(
                    name=name[:120],
                    path=path[:500],
                    kind=str(a.get("kind") or "lnk")[:12],
                    room=str(a.get("room") or "")[:80],
                )
            )
        return cls(
            schema=int(raw.get("schema") or SCHEMA_VERSION),
            scanned_at=float(raw.get("scanned_at") or 0.0),
            appliances=apps[:MAX_APPLIANCES],
        )


def _home(home: str | Path | None = None) -> Path:
    if home:
        return Path(home).expanduser()
    env = (os.environ.get("REMEDY_HOME") or "").strip()
    return Path(env or "~/.remedy").expanduser()


def inventory_path(home: str | Path | None = None) -> Path:
    return _home(home) / APPLIANCES_REL


def default_scan_roots() -> list[Path]:
    """Where appliances live: exactly what the owner sees in Start / apps."""
    roots: list[Path] = []
    if os.name == "nt":
        for env, rel in (
            ("APPDATA", r"Microsoft\Windows\Start Menu\Programs"),
            ("PROGRAMDATA", r"Microsoft\Windows\Start Menu\Programs"),
        ):
            base = os.environ.get(env, "")
            if base:
                roots.append(Path(base) / rel)
    else:
        roots.append(Path("/usr/share/applications"))
        roots.append(Path.home() / ".local" / "share" / "applications")
    return [r for r in roots if r.is_dir()]


def _looks_junk(name: str) -> bool:
    low = name.lower()
    return any(bit in low for bit in _JUNK_BITS)


def _desktop_entry_name(path: Path) -> str:
    """Minimal .desktop parse: first Name= line; NoDisplay entries skipped."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:4000]
    except OSError:
        return ""
    if "NoDisplay=true" in text:
        return ""
    for line in text.splitlines():
        if line.startswith("Name="):
            return line[5:].strip()
    return ""


def scan_appliances(
    home: str | Path | None = None,
    *,
    roots: list[Path] | None = None,
    persist: bool = True,
) -> ApplianceInventory:
    """Walk the appliance shelves. Bounded; junk shortcuts skipped."""
    scan_roots = roots if roots is not None else default_scan_roots()
    seen: dict[str, Appliance] = {}
    for root in scan_roots:
        try:
            root = root.expanduser()
            if not root.is_dir():
                continue
            base_depth = len(root.parts)
            for p in sorted(root.rglob("*")):
                if len(seen) >= MAX_APPLIANCES:
                    break
                if len(p.parts) - base_depth > MAX_WALK_DEPTH:
                    continue
                suffix = p.suffix.lower()
                if suffix == ".lnk":
                    name = p.stem.strip()
                    kind = "lnk"
                elif suffix == ".desktop":
                    name = _desktop_entry_name(p)
                    kind = "desktop"
                else:
                    continue
                if not name or _looks_junk(name):
                    continue
                key = name.lower()
                if key in seen:
                    continue
                room = p.parent.name if p.parent != root else ""
                seen[key] = Appliance(
                    name=name, path=str(p), kind=kind, room=room
                )
        except OSError:
            continue
    inv = ApplianceInventory(
        scanned_at=time.time(), appliances=list(seen.values())
    )
    if persist:
        save_inventory(inv, home)
    logger.info("Appliance scan: %d found in %d roots", len(inv.appliances), len(scan_roots))
    return inv


def load_inventory(home: str | Path | None = None) -> ApplianceInventory | None:
    p = inventory_path(home)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return ApplianceInventory.from_dict(raw)


def save_inventory(
    inv: ApplianceInventory, home: str | Path | None = None
) -> Path:
    p = inventory_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp{os.getpid()}.{threading.get_ident()}")
    tmp.write_text(json.dumps(inv.to_dict(), indent=2), encoding="utf-8")
    tmp.replace(p)
    return p


def needs_scan(
    home: str | Path | None = None, *, stale_days: int = STALE_DAYS
) -> bool:
    inv = load_inventory(home)
    if inv is None or not inv.scanned_at:
        return True
    return (time.time() - inv.scanned_at) >= stale_days * 86400


_scan_flag = threading.Lock()


def ensure_appliances(
    home: str | Path | None = None,
    *,
    force: bool = False,
    background: bool = True,
) -> ApplianceInventory | None:
    """Scan if needed. Background by default — the house maps itself."""
    if not force and not needs_scan(home):
        return load_inventory(home)
    if background:
        def _one_scan() -> None:
            # Only one scanner at a time; a second request just skips.
            if _scan_flag.acquire(blocking=False):
                try:
                    _scan_safe(home)
                finally:
                    _scan_flag.release()

        threading.Thread(
            target=_one_scan, name="remedy-appliances", daemon=True
        ).start()
        return load_inventory(home)
    return _scan_safe(home)


def _scan_safe(home: str | Path | None) -> ApplianceInventory | None:
    try:
        return scan_appliances(home)
    except Exception:
        logger.exception("appliance scan failed")
        return None


# --- resolution: natural names, forgiving ---------------------------------


def _norm(text: str) -> str:
    return "".join(
        c if (c.isalnum() or c == " ") else " " for c in (text or "").lower()
    ).strip()


def score_match(query: str, name: str) -> int:
    """0–100. Exact > prefix > word-prefix > all-tokens > substring > fuzzy."""
    q = _norm(query)
    n = _norm(name)
    if not q or not n:
        return 0
    if q == n:
        return 100
    if n.startswith(q):
        return 85
    words = n.split()
    if any(w.startswith(q) for w in words):
        return 72
    q_tokens = q.split()
    if len(q_tokens) > 1 and all(
        any(w.startswith(t) for w in words) for t in q_tokens
    ):
        return 68
    if q in n:
        return 55
    import difflib

    ratio = difflib.SequenceMatcher(None, q, n).ratio()
    return int(ratio * 50) if ratio >= 0.6 else 0


def resolve_appliance(
    query: str,
    home: str | Path | None = None,
    *,
    inventory: ApplianceInventory | None = None,
    limit: int = 5,
) -> list[ApplianceMatch]:
    """Ranked matches for a natural name. Empty list = not in the house."""
    inv = inventory if inventory is not None else load_inventory(home)
    if inv is None or not inv.appliances:
        return []
    scored = [
        ApplianceMatch(appliance=a, score=score_match(query, a.name))
        for a in inv.appliances
    ]
    scored = [m for m in scored if m.score > 0]
    scored.sort(key=lambda m: (-m.score, m.appliance.name.lower()))
    return scored[: max(1, int(limit))]


def best_appliance(
    query: str,
    home: str | Path | None = None,
    *,
    min_score: int = 60,
) -> Appliance | None:
    """The confident hit, or None (then suggestions belong in the error)."""
    matches = resolve_appliance(query, home, limit=1)
    if matches and matches[0].score >= int(min_score):
        return matches[0].appliance
    return None


def suggestions_line(query: str, home: str | Path | None = None) -> str:
    """'did you mean' text for launch errors. Empty when nothing is close."""
    matches = resolve_appliance(query, home, limit=3)
    names = [m.appliance.name for m in matches if m.score >= 40]
    if not names:
        return ""
    return "Closest appliances in this house: " + ", ".join(names)


def appliance_overview(
    query: str = "",
    home: str | Path | None = None,
    *,
    limit: int = 30,
) -> dict[str, Any]:
    """Public snapshot for the computer_apps tool: her house, listable."""
    inv = load_inventory(home)
    if inv is None:
        inv = ensure_appliances(home, background=False) or ApplianceInventory()
    if (query or "").strip():
        matches = resolve_appliance(query, home, inventory=inv, limit=limit)
        items = [
            {"name": m.appliance.name, "room": m.appliance.room, "score": m.score}
            for m in matches
        ]
    else:
        items = [
            {"name": a.name, "room": a.room}
            for a in sorted(inv.appliances, key=lambda x: x.name.lower())[:limit]
        ]
    return {
        "ok": True,
        "total_known": len(inv.appliances),
        "shown": len(items),
        "appliances": items,
        "note": "Launch any of these with computer_app app=\"<name>\".",
    }
