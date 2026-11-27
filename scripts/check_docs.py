"""Documentation sync checker — keep prose surfaces aligned with code truth.

Mirrors scripts/sync_version.py: one command to **check** (CI gate) or **sync**
(safe auto-fixes only).

Canonical sources (do not invent second homes for these):
  - Version numbers     → pyproject.toml          (sync_version.py)
  - Help chapter bodies → docs/manual/*.md        (sync_help_manual.py;
                           copied to desktop/src/help/articles and
                           desktop/remedyui/help/articles)
  - Slash commands      → native/go/httpapi/session_command*.go  builtinSlashCommands,
                           moreSlashCommands, fullSlashCommands
                           (canonical: docs/manual/11-reference-commands.md;
                            README slash table is optional)
  - Keyboard shortcuts  → desktop/src/hotkeys.ts  HOTKEYS
  - Help catalog ids    → docs/manual chapter files ↔ catalog.ts META

Usage:
  python scripts/check_docs.py              # check all (exit 1 on drift)
  python scripts/check_docs.py check        # same
  python scripts/check_docs.py sync         # auto-fix help copies only
  python scripts/check_docs.py --list       # print check inventory
  python scripts/check_docs.py help|version|commands|hotkeys|catalog|tests|readme
                                            # run a single surface
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


def _configure_stdio() -> None:
    """Avoid UnicodeEncodeError on Windows cp1252 consoles (arrows in hotkeys)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            reconf = getattr(stream, "reconfigure", None)
            if callable(reconf):
                reconf(encoding="utf-8", errors="replace")
        except Exception:
            pass


_configure_stdio()

ROOT = Path(__file__).resolve().parent.parent
MANUAL = ROOT / "docs" / "manual"
ARTICLES = ROOT / "desktop" / "src" / "help" / "articles"
ARTICLES_REMEDYUI = ROOT / "desktop" / "remedyui" / "help" / "articles"
CATALOG_TS = ROOT / "desktop" / "src" / "help" / "catalog.ts"
HOTKEYS_TS = ROOT / "desktop" / "src" / "hotkeys.ts"
# Canonical slash command list: three Go slices, each `var X = []map[string]any{`.
SLASH_SOURCES = (
    (ROOT / "native" / "go" / "httpapi" / "session_command.go", "builtinSlashCommands"),
    (ROOT / "native" / "go" / "httpapi" / "session_command_more.go", "moreSlashCommands"),
    (ROOT / "native" / "go" / "httpapi" / "session_command_full.go", "fullSlashCommands"),
)
README = ROOT / "README.md"
COMMANDS_MD = MANUAL / "11-reference-commands.md"
SHORTCUTS_MD = MANUAL / "12-reference-shortcuts.md"


@dataclass
class CheckResult:
    name: str
    ok: bool
    messages: list[str] = field(default_factory=list)
    fix_hint: str = ""


def _chapter_names(directory: Path) -> set[str]:
    if not directory.is_dir():
        return set()
    return {p.name for p in directory.glob("*.md") if p.name != "README.md"}


def _chapter_ids(directory: Path) -> set[str]:
    return {n.removesuffix(".md") for n in _chapter_names(directory)}


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_help_manual() -> CheckResult:
    """docs/manual ↔ both help article dirs, byte-for-byte."""
    msgs: list[str] = []
    dests = (ARTICLES, ARTICLES_REMEDYUI)
    if not MANUAL.is_dir() or not all(d.is_dir() for d in dests):
        return CheckResult(
            "help-manual",
            False,
            ["docs/manual or a desktop help articles directory missing"],
            "python scripts/sync_help_manual.py",
        )
    src = {p.name: p for p in MANUAL.glob("*.md") if p.name != "README.md"}
    bad = 0
    for dest in dests:
        where = dest.relative_to(ROOT).as_posix()
        dst = {p.name: p for p in dest.glob("*.md") if p.name != "README.md"}
        for name in sorted(set(src) | set(dst)):
            if name not in src:
                msgs.append(f"only in {where}: {name}")
                bad += 1
                continue
            if name not in dst:
                msgs.append(f"only in docs, missing from {where}: {name}")
                bad += 1
                continue
            if src[name].read_text(encoding="utf-8") != dst[name].read_text(encoding="utf-8"):
                msgs.append(f"drift in {where}: {name}")
                bad += 1
            else:
                msgs.append(f"ok: {where}/{name}")
    return CheckResult(
        "help-manual",
        bad == 0,
        msgs,
        "python scripts/sync_help_manual.py",
    )


def check_version() -> CheckResult:
    """Delegate to sync_version.py check for consistent messaging."""
    script = ROOT / "scripts" / "sync_version.py"
    proc = subprocess.run(
        [sys.executable, str(script), "check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    lines = [ln for ln in out.strip().splitlines() if ln.strip()]
    return CheckResult(
        "version",
        proc.returncode == 0,
        lines or ["version check produced no output"],
        "python scripts/sync_version.py <version>",
    )


def _parse_builtin_commands() -> list[dict]:
    """Parse the three slash-command slices of the Go runtime.

    The Go runtime answers slash commands, so its list is the one the manual
    has to match. Each entry gives a "name" and an "aliases" []string{...}.
    """
    cmds: list[dict] = []
    for path, var in SLASH_SOURCES:
        text = path.read_text(encoding="utf-8")
        m = re.search(rf"var {var} = \[\]map\[string\]any\{{(.*?)\n\}}", text, re.DOTALL)
        if not m:
            raise RuntimeError(f"{var} not found in {path.name}")
        body = m.group(1)
        found = 0
        for name_m in re.finditer(r'"name":\s*"(/[^"]+)"', body):
            rest = body[name_m.end():]
            nxt = re.search(r'"name":', rest)
            chunk = rest[: nxt.start()] if nxt else rest
            al = re.search(r'"aliases":\s*\[\]string\{([^}]*)\}', chunk)
            aliases = re.findall(r'"(/[^"]+)"', al.group(1)) if al else []
            cmds.append({"name": name_m.group(1), "aliases": aliases})
            found += 1
        if not found:
            raise RuntimeError(f"{var} in {path.name} is empty")
    return cmds


def check_slash_commands() -> CheckResult:
    """Every built-in slash command must appear in the owner-manual table.

    README is a pointer into the wiki, not a second command list. If README
    has a ``### Slash commands`` section, that copy is still checked; if it
    does not, skip README.
    """
    msgs: list[str] = []
    try:
        cmds = _parse_builtin_commands()
    except Exception as exc:
        return CheckResult("slash-commands", False, [str(exc)])

    names = [str(c["name"]) for c in cmds]
    aliases: list[str] = []
    for c in cmds:
        for a in c.get("aliases") or []:
            aliases.append(str(a))

    if not COMMANDS_MD.is_file():
        return CheckResult("slash-commands", False, [f"missing {COMMANDS_MD.relative_to(ROOT)}"])

    manual_text = COMMANDS_MD.read_text(encoding="utf-8")
    readme_text = README.read_text(encoding="utf-8") if README.is_file() else ""
    readme_section_m = re.search(
        r"### Slash commands.*?(?=\n### |\n## |\Z)",
        readme_text,
        re.DOTALL | re.IGNORECASE,
    )
    readme_section = readme_section_m.group(0) if readme_section_m else ""
    check_readme = bool(readme_section)
    if not check_readme:
        msgs.append("ok: README has no slash-command section (manual is canonical)")

    bad = 0
    for name in names:
        if name not in manual_text:
            msgs.append(f"missing in docs/manual/11-reference-commands.md: {name}")
            bad += 1
        else:
            msgs.append(f"ok manual: {name}")
        if check_readme:
            if name not in readme_section:
                msgs.append(f"missing in README slash table: {name}")
                bad += 1
            else:
                msgs.append(f"ok readme: {name}")

    # Documented-only commands (warn, don't fail hard) — catch typos in manual
    documented = set(re.findall(r"`?(/[a-z][a-z0-9-]*)`?", manual_text, re.I))
    # only rows that look like command cells
    table_cmds = set(re.findall(r"\|\s*(`?/[\w-]+`?(?:\s*·\s*`?/[\w-]+`?)*)\s*\|", manual_text))
    flat_doc: set[str] = set()
    for cell in table_cmds:
        for piece in re.findall(r"/[\w-]+", cell):
            flat_doc.add(piece)
    if not flat_doc:
        flat_doc = documented

    code_set = set(names) | set(aliases)
    for d in sorted(flat_doc):
        if d not in code_set:
            # /session-import is an alias — already in code_set if parsed
            msgs.append(f"warn: documented but not in the Go slash command slices: {d}")
            # treat unknown documented commands as failures so docs can't invent APIs
            bad += 1

    return CheckResult(
        "slash-commands",
        bad == 0,
        msgs,
        "Update docs/manual/11-reference-commands.md to match "
        "builtinSlashCommands, moreSlashCommands and fullSlashCommands in "
        "native/go/httpapi/session_command*.go",
    )


def _parse_hotkey_keys() -> list[str]:
    """Extract HOTKEYS[].keys display strings from hotkeys.ts."""
    text = HOTKEYS_TS.read_text(encoding="utf-8")
    # Match keys: '...' or keys: "..." inside the HOTKEYS array region
    block_m = re.search(r"export const HOTKEYS[^=]*=\s*\[(.*?)\n\]", text, re.DOTALL)
    if not block_m:
        raise RuntimeError("HOTKEYS array not found in hotkeys.ts")
    block = block_m.group(1)
    return re.findall(r"keys:\s*['\"]([^'\"]+)['\"]", block)


def check_hotkeys() -> CheckResult:
    """Every hotkey keys label from hotkeys.ts must appear in shortcuts manual."""
    msgs: list[str] = []
    try:
        keys = _parse_hotkey_keys()
    except Exception as exc:
        return CheckResult("hotkeys", False, [str(exc)])

    if not SHORTCUTS_MD.is_file():
        return CheckResult("hotkeys", False, [f"missing {SHORTCUTS_MD.relative_to(ROOT)}"])

    doc = SHORTCUTS_MD.read_text(encoding="utf-8")
    # Normalize: docs use **Ctrl+N**, code uses Ctrl+N
    def present(k: str) -> bool:
        if k in doc:
            return True
        # allow bold wrapping
        if f"**{k}**" in doc:
            return True
        # Ctrl+P / Ctrl+K style combined cells
        return bool(k.replace("+", r"\+") and k in doc.replace("**", ""))

    bad = 0
    for k in keys:
        if present(k):
            msgs.append(f"ok: {k}")
        else:
            msgs.append(f"missing in docs/manual/12-reference-shortcuts.md: {k}")
            bad += 1

    return CheckResult(
        "hotkeys",
        bad == 0,
        msgs,
        "Update docs/manual/12-reference-shortcuts.md to match desktop/src/hotkeys.ts",
    )


def _parse_catalog_ids() -> set[str]:
    text = CATALOG_TS.read_text(encoding="utf-8")
    # META entries: id: '00-overview'
    return set(re.findall(r"id:\s*['\"]([0-9a-z][0-9a-z-]*)['\"]", text))


def check_catalog() -> CheckResult:
    """catalog.ts META ids must match docs/manual chapter basenames 1:1."""
    msgs: list[str] = []
    if not CATALOG_TS.is_file():
        return CheckResult("catalog", False, ["catalog.ts missing"])

    try:
        meta_ids = _parse_catalog_ids()
    except Exception as exc:
        return CheckResult("catalog", False, [str(exc)])

    chapter_ids = _chapter_ids(MANUAL)
    bad = 0
    for cid in sorted(chapter_ids - meta_ids):
        msgs.append(f"chapter without catalog META: {cid}")
        bad += 1
    for mid in sorted(meta_ids - chapter_ids):
        msgs.append(f"catalog META without chapter file: {mid}")
        bad += 1
    for cid in sorted(chapter_ids & meta_ids):
        msgs.append(f"ok: {cid}")

    return CheckResult(
        "catalog",
        bad == 0,
        msgs,
        "Add/remove META in desktop/src/help/catalog.ts to match docs/manual/*.md",
    )


def _collect_pytest_count() -> int | None:
    """Return number of collected tests, or None if collection fails."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # "561 tests collected" or "no tests collected"
    m = re.search(r"(\d+)\s+tests?\s+collected", proc.stdout + proc.stderr)
    if m:
        return int(m.group(1))
    # -q short form: "....." then last line may still have count in full mode
    m2 = re.search(r"collected\s+(\d+)\s+item", proc.stdout + proc.stderr)
    if m2:
        return int(m2.group(1))
    return None


def check_test_count_claim() -> CheckResult:
    """README test-count claim must stay near live pytest collection count.

    Skip when the suite is absent (partial checkout) or the README no
    longer claims a count. The public tree ships tests/.

    One number, the live suite: ``# ~7753 tests`` next to ``uv run pytest``.
    (The old ``560+ tests; currently ~N`` form was a historical floor from
    when the suite was ~560 items, not a second class of tests.)
    ``|claimed - collected|`` must be <= 25 so a large add/drop forces a bump.
    """
    msgs: list[str] = []
    if not (ROOT / "tests").is_dir():
        return CheckResult(
            "test-count",
            True,
            ["skipped: tests/ not in this tree (local-only)"],
        )
    if not README.is_file():
        return CheckResult("test-count", False, ["README.md missing"])

    text = README.read_text(encoding="utf-8")
    # Prefer the pytest comment in Development: ``uv run pytest -q  # ~N tests``
    claim_m = re.search(
        r"uv run pytest[^\n]*#\s*~?(\d+)\s*tests",
        text,
        re.IGNORECASE,
    )
    if not claim_m:
        # Legacy: ``560+ tests; currently ~N``
        claim_m = re.search(
            r"currently\s*~(\d+)",
            text,
            re.IGNORECASE,
        )
    if not claim_m:
        return CheckResult(
            "test-count",
            True,
            ["skipped: README has no public test-count claim"],
        )

    claimed = int(claim_m.group(1))
    actual = _collect_pytest_count()
    if actual is None:
        return CheckResult(
            "test-count",
            False,
            ["could not collect pytest count (is the env installed?)"],
        )

    bad = 0
    msgs.append(f"live pytest collection: {actual}")
    msgs.append(f"README ~{claimed} tests")

    delta = abs(actual - claimed)
    if delta > 25:
        msgs.append(
            f"README ~{claimed} is {delta} off from live {actual} "
            f"(tolerance 25) — update README"
        )
        bad += 1
    else:
        msgs.append(f"ok: claim within tolerance (Δ={delta})")

    if 5 < delta <= 25:
        msgs.append(
            f"note: consider bumping '~{claimed}' → ~{actual} "
            f"(not required until Δ>25)"
        )

    return CheckResult(
        "test-count",
        bad == 0,
        msgs,
        f'Update README Development section, e.g. "# ~{actual} tests"',
    )


def check_readme_sync_pointers() -> CheckResult:
    """README maintainer section should mention the doc-sync tools."""
    msgs: list[str] = []
    text = README.read_text(encoding="utf-8") if README.is_file() else ""
    bad = 0
    for needle, label in (
        ("sync_version.py", "version sync script"),
        ("sync_help_manual.py", "help manual sync script"),
        ("check_docs.py", "docs check aggregator"),
    ):
        if needle in text:
            msgs.append(f"ok: README mentions {label}")
        else:
            # check_docs is new — require it so maintainers discover the gate
            msgs.append(f"missing pointer: {needle}")
            bad += 1
    return CheckResult(
        "readme-pointers",
        bad == 0,
        msgs,
        "Document scripts/check_docs.py in README Development / release notes",
    )


# ---------------------------------------------------------------------------
# Registry + runner
# ---------------------------------------------------------------------------

CHECKS: dict[str, Callable[[], CheckResult]] = {
    "help": check_help_manual,
    "version": check_version,
    "commands": check_slash_commands,
    "hotkeys": check_hotkeys,
    "catalog": check_catalog,
    "tests": check_test_count_claim,
    "readme": check_readme_sync_pointers,
}

# Default order for full suite
DEFAULT_ORDER = (
    "help",
    "version",
    "catalog",
    "commands",
    "hotkeys",
    "tests",
    "readme",
)


def run_checks(names: list[str] | None = None, *, quiet: bool = False) -> int:
    order = names or list(DEFAULT_ORDER)
    results: list[CheckResult] = []
    for name in order:
        fn = CHECKS.get(name)
        if not fn:
            print(f"Unknown check: {name}")
            print(f"Available: {', '.join(CHECKS)}")
            return 2
        results.append(fn())

    failed = 0
    for r in results:
        mark = "OK " if r.ok else "FAIL"
        print(f"\n=== [{mark}] {r.name} ===")
        for msg in r.messages:
            # Collapse verbose OK lines unless they are failures/warns
            if quiet and r.ok and msg.startswith("ok"):
                continue
            print(f"  {msg}")
        if not r.ok:
            failed += 1
            if r.fix_hint:
                print(f"  -> fix: {r.fix_hint}")

    print()
    if failed:
        print(f"{failed} documentation surface(s) out of sync.")
        return 1
    print(f"All {len(results)} documentation surface(s) aligned.")
    return 0


def do_sync() -> int:
    """Safe auto-fixes only: help manual copy. Everything else is manual."""
    print("Syncing help manual copies (docs/manual → desktop and remedyui articles)…")
    script = ROOT / "scripts" / "sync_help_manual.py"
    proc = subprocess.run([sys.executable, str(script)], cwd=ROOT)
    if proc.returncode != 0:
        return proc.returncode
    print("\nRe-checking all surfaces…")
    return run_checks()


def main() -> None:
    args = [a for a in sys.argv[1:] if a]
    if not args or args[0] in ("check", "--check"):
        # optional: check help commands …
        rest = args[1:] if args and args[0] in ("check", "--check") else args
        if rest and rest[0] not in ("--quiet", "-q"):
            # treat remaining as subset names
            quiet = "--quiet" in rest or "-q" in rest
            names = [a for a in rest if a not in ("--quiet", "-q")]
            raise SystemExit(run_checks(names or None, quiet=quiet))
        quiet = "--quiet" in args or "-q" in args
        raise SystemExit(run_checks(quiet=quiet))

    if args[0] in ("sync", "--sync", "fix"):
        raise SystemExit(do_sync())

    if args[0] in ("--list", "list"):
        print("Documentation sync surfaces:\n")
        print("  help      docs/manual ↔ desktop/src/help/articles + desktop/remedyui/help/articles")
        print("  version   pyproject / package.json / tauri / cargo / latest.json")
        print("  catalog   docs/manual chapters ↔ catalog.ts META ids")
        print("  commands  Go slash command slices ↔ manual/11 + README slash table")
        print("  hotkeys   hotkeys.ts ↔ manual/12-reference-shortcuts")
        print("  tests     README '~N tests' vs live pytest collection")
        print("  readme    README mentions sync/check scripts")
        print("\nCommands:")
        print("  python scripts/check_docs.py            # full check (CI)")
        print("  python scripts/check_docs.py sync       # copy help bodies")
        print("  python scripts/check_docs.py commands   # one surface")
        return

    if args[0] in CHECKS:
        quiet = "--quiet" in args or "-q" in args
        raise SystemExit(run_checks([args[0]], quiet=quiet))

    print(__doc__)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
