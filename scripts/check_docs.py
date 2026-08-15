"""Documentation sync checker — keep prose surfaces aligned with code truth.

Mirrors scripts/sync_version.py: one command to **check** (CI gate) or **sync**
(safe auto-fixes only).

Canonical sources (do not invent second homes for these):
  - Version numbers     → pyproject.toml          (sync_version.py)
  - Help chapter bodies → docs/manual/*.md        (sync_help_manual.py)
  - Slash commands      → src/remedy/interfaces/slash_commands.py  _BUILTIN_COMMANDS
  - Keyboard shortcuts  → desktop/src/hotkeys.ts  HOTKEYS
  - Help catalog ids    → docs/manual chapter files ↔ catalog.ts META

Usage:
  python scripts/check_docs.py              # check all (exit 1 on drift)
  python scripts/check_docs.py check        # same
  python scripts/check_docs.py sync         # auto-fix help copies only
  python scripts/check_docs.py --list       # print check inventory
  python scripts/check_docs.py help|version|commands|hotkeys|catalog|tests|readme|pypi|urls
                                            # run a single surface
"""

from __future__ import annotations

import ast
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
CATALOG_TS = ROOT / "desktop" / "src" / "help" / "catalog.ts"
HOTKEYS_TS = ROOT / "desktop" / "src" / "hotkeys.ts"
# Canonical slash command list (re-exported from api_support for compat).
API_SUPPORT = ROOT / "src" / "remedy" / "interfaces" / "slash_commands.py"
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
    """docs/manual ↔ desktop/src/help/articles byte-for-byte."""
    msgs: list[str] = []
    if not MANUAL.is_dir() or not ARTICLES.is_dir():
        return CheckResult(
            "help-manual",
            False,
            ["docs/manual or desktop help articles directory missing"],
            "python scripts/sync_help_manual.py",
        )
    src = {p.name: p for p in MANUAL.glob("*.md") if p.name != "README.md"}
    dst = {p.name: p for p in ARTICLES.glob("*.md") if p.name != "README.md"}
    bad = 0
    for name in sorted(set(src) | set(dst)):
        if name not in src:
            msgs.append(f"only in desktop: {name}")
            bad += 1
            continue
        if name not in dst:
            msgs.append(f"only in docs: {name}")
            bad += 1
            continue
        if src[name].read_text(encoding="utf-8") != dst[name].read_text(encoding="utf-8"):
            msgs.append(f"drift: {name}")
            bad += 1
        else:
            msgs.append(f"ok: {name}")
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
    """Parse _BUILTIN_COMMANDS from api_support.py without importing remedy."""
    text = API_SUPPORT.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in tree.body:
        # plain: _BUILTIN_COMMANDS = [...]
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_BUILTIN_COMMANDS":
                    return ast.literal_eval(node.value)
        # annotated: _BUILTIN_COMMANDS: list[dict] = [...]
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "_BUILTIN_COMMANDS" and node.value is not None:
                return ast.literal_eval(node.value)
    raise RuntimeError("_BUILTIN_COMMANDS not found in api_support.py")


def check_slash_commands() -> CheckResult:
    """Every built-in slash command must appear in manual + README tables."""
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
    if not README.is_file():
        return CheckResult("slash-commands", False, [f"missing {README.relative_to(ROOT)}"])

    manual_text = COMMANDS_MD.read_text(encoding="utf-8")
    readme_text = README.read_text(encoding="utf-8")

    # Focus README on the slash-command section if present.
    readme_section = readme_text
    m = re.search(
        r"### Slash commands.*?(?=\n### |\n## |\Z)",
        readme_text,
        re.DOTALL | re.IGNORECASE,
    )
    if m:
        readme_section = m.group(0)

    bad = 0
    for name in names:
        if name not in manual_text:
            msgs.append(f"missing in docs/manual/11-reference-commands.md: {name}")
            bad += 1
        else:
            msgs.append(f"ok manual: {name}")
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
            msgs.append(f"warn: documented but not in _BUILTIN_COMMANDS: {d}")
            # treat unknown documented commands as failures so docs can't invent APIs
            bad += 1

    return CheckResult(
        "slash-commands",
        bad == 0,
        msgs,
        "Update docs/manual/11-reference-commands.md and README slash table "
        "to match _BUILTIN_COMMANDS in slash_commands.py",
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

    Public tree does not ship tests/. Skip when the suite is absent or the
    README no longer claims a count. Local clones that keep tests/ still
    validate the claim when present.

    Accepts either:
      (560+ tests; currently ~561)
      (560+ tests)
    Floor must be <= actual. If ~N is present, |actual - N| must be <= 25
    (forces a README bump after large suite growth/shrink).
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
    # Prefer the Development section claim
    floor_m = re.search(
        r"(\d+)\+\s*tests?(?:\s*;\s*currently\s*~?(\d+))?",
        text,
        re.IGNORECASE,
    )
    if not floor_m:
        return CheckResult(
            "test-count",
            True,
            ["skipped: README has no public test-count claim"],
        )

    floor = int(floor_m.group(1))
    claimed = int(floor_m.group(2)) if floor_m.group(2) else None
    actual = _collect_pytest_count()
    if actual is None:
        return CheckResult(
            "test-count",
            False,
            ["could not collect pytest count (is the env installed?)"],
        )

    bad = 0
    msgs.append(f"live pytest collection: {actual}")
    msgs.append(f"README floor: {floor}+")
    if claimed is not None:
        msgs.append(f"README currently ~{claimed}")

    if actual < floor:
        msgs.append(f"suite shrank below floor ({actual} < {floor}+) — lower the claim")
        bad += 1
    else:
        msgs.append("ok: floor <= actual")

    if claimed is not None:
        delta = abs(actual - claimed)
        # tight enough that the "currently ~N" number stays honest
        if delta > 25:
            msgs.append(
                f"currently ~{claimed} is {delta} off from live {actual} "
                f"(tolerance 25) — update README"
            )
            bad += 1
        else:
            msgs.append(f"ok: currently claim within tolerance (Δ={delta})")

    # Soft nudge: if exact-ish and delta > 5, still OK but note it
    if claimed is not None and 5 < abs(actual - claimed) <= 25:
        msgs.append(
            f"note: consider bumping 'currently ~{claimed}' → ~{actual} "
            f"(not required until Δ>25)"
        )

    return CheckResult(
        "test-count",
        bad == 0,
        msgs,
        f'Update README Development section, e.g. '
        f'"{floor}+ tests; currently ~{actual}"',
    )


_GH_REPO = "https://github.com/AhmiDarrow/RemedyAI"
_GH_RAW = "https://raw.githubusercontent.com/AhmiDarrow/RemedyAI/master"
_ALLOWED_LINK_HOSTS = (
    "https://github.com/AhmiDarrow/",
    "https://raw.githubusercontent.com/AhmiDarrow/",
    "https://pypi.org/",
    "https://www.patreon.com/",
    "https://agentskills.io",
)


def _readme_link_targets(text: str) -> list[tuple[str, str]]:
    """Return (kind, url) for href/src/markdown links in README."""
    found: list[tuple[str, str]] = []
    for m in re.finditer(r"""(?:href|src)=["']([^"']+)["']""", text, re.I):
        kind = "src" if "src=" in m.group(0).lower() else "href"
        found.append((kind, m.group(1)))
    for m in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", text):
        found.append(("md", m.group(1)))
    return found


def check_pypi_readme_urls() -> CheckResult:
    """README is the PyPI long_description — relative repo links 404 there."""
    msgs: list[str] = []
    if not README.is_file():
        return CheckResult("pypi-readme", False, ["README.md missing"])
    text = README.read_text(encoding="utf-8")
    bad = 0
    seen: set[str] = set()
    for kind, raw in _readme_link_targets(text):
        url = raw.strip()
        if not url or url.startswith(("#", "mailto:")):
            continue
        if url in seen:
            continue
        seen.add(url)
        if url.startswith(("http://", "https://")):
            if kind == "src" and not url.startswith(_GH_RAW + "/"):
                msgs.append(f"image must use raw.githubusercontent.com: {url}")
                bad += 1
                continue
            if not url.startswith(_ALLOWED_LINK_HOSTS):
                msgs.append(f"unexpected host: {url}")
                bad += 1
                continue
            # Resolve GitHub blob/tree/raw paths back to the working tree.
            rel = ""
            for prefix, _kind in (
                (_GH_RAW + "/", "raw"),
                (_GH_REPO + "/blob/master/", "blob"),
                (_GH_REPO + "/tree/master/", "tree"),
            ):
                if url.startswith(prefix):
                    rel = url[len(prefix) :].split("#", 1)[0].rstrip("/")
                    break
            if rel:
                target = ROOT / rel
                if not target.exists():
                    msgs.append(f"dead GitHub path (not in repo): {rel}")
                    bad += 1
                else:
                    msgs.append(f"ok {kind}: {rel or url}")
            else:
                msgs.append(f"ok {kind}: {url}")
            continue
        # Relative path — PyPI will 404
        if re.match(r"^(?:\./)?(?:docs/|assets/|CHANGELOG|AGENTS|LICENSE|COMMERCIAL)", url):
            msgs.append(f"relative link breaks on PyPI: {url}")
            bad += 1
        else:
            msgs.append(f"relative link breaks on PyPI: {url}")
            bad += 1
    return CheckResult(
        "pypi-readme",
        bad == 0,
        msgs or ["no links found"],
        "Use https://github.com/AhmiDarrow/RemedyAI/blob|tree|raw/master/... in README",
    )


def check_project_urls() -> CheckResult:
    """pyproject [project.urls] must expose docs/changelog/issues for PyPI sidebar."""
    msgs: list[str] = []
    pyproject = ROOT / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8") if pyproject.is_file() else ""
    required = {
        "Homepage": _GH_REPO,
        "Repository": _GH_REPO,
        "Documentation": f"{_GH_REPO}/blob/master/docs/manual/00-overview.md",
        "Changelog": f"{_GH_REPO}/blob/master/CHANGELOG.md",
        "Issues": f"{_GH_REPO}/issues",
    }
    bad = 0
    for key, expected in required.items():
        pat = re.compile(rf'^{re.escape(key)}\s*=\s*"([^"]+)"', re.M)
        m = pat.search(text)
        if not m:
            msgs.append(f"missing project.urls.{key}")
            bad += 1
            continue
        got = m.group(1).rstrip("/")
        exp = expected.rstrip("/")
        if got != exp:
            msgs.append(f"project.urls.{key}={got!r} expected {exp!r}")
            bad += 1
        else:
            msgs.append(f"ok {key}")
    return CheckResult(
        "project-urls",
        bad == 0,
        msgs,
        "Set [project.urls] Homepage/Repository/Documentation/Changelog/Issues",
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
    "pypi": check_pypi_readme_urls,
    "urls": check_project_urls,
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
    "pypi",
    "urls",
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
    print("Syncing help manual copies (docs/manual → desktop articles)…")
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
        print("  help      docs/manual ↔ desktop/src/help/articles")
        print("  version   pyproject / package.json / tauri / cargo / latest.json")
        print("  catalog   docs/manual chapters ↔ catalog.ts META ids")
        print("  commands  _BUILTIN_COMMANDS ↔ manual/11 + README slash table")
        print("  hotkeys   hotkeys.ts ↔ manual/12-reference-shortcuts")
        print("  tests     README 'N+ tests; currently ~M' vs live collection")
        print("  readme    README mentions sync/check scripts")
        print("  pypi      README links are absolute (PyPI long_description)")
        print("  urls      pyproject [project.urls] Documentation/Changelog/Issues")
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
