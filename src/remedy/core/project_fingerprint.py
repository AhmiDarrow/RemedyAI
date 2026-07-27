"""Opportunistic stack fingerprint + orientation pointers for a work path.

Hints only — never gates search or requires a project root.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Orientation files (read path only + tiny excerpt when small).
_ORIENT_NAMES = (
    "AGENTS.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "README.md",
    "README.rst",
    "README.txt",
)

_ORIENT_MAX_BYTES = 6_000
_EXCERPT_CHARS = 280
_BLOCK_CHAR_CAP = 2_800


@dataclass
class StackFingerprint:
    """Detected stack signals under a directory."""

    path: Path
    stacks: list[str] = field(default_factory=list)
    markers: list[str] = field(default_factory=list)
    suggest_verify: str | None = None
    hints: list[str] = field(default_factory=list)

    def context_lines(self) -> list[str]:
        if not self.stacks and not self.hints:
            return []
        lines = [f"Stack fingerprint ({self.path}):"]
        if self.stacks:
            lines.append("  stacks: " + ", ".join(self.stacks))
        if self.suggest_verify:
            lines.append(f"  suggested verify: {self.suggest_verify}")
        for h in self.hints[:6]:
            lines.append(f"  - {h}")
        return lines


def fingerprint_path(root: Path | str | None) -> StackFingerprint:
    """Inspect *root* for common project markers (one level + shallow data)."""
    if root is None:
        return StackFingerprint(path=Path("."))
    path = Path(root)
    try:
        path = path.expanduser().resolve()
    except OSError:
        path = path.expanduser().absolute()

    fp = StackFingerprint(path=path)
    if not path.is_dir():
        return fp

    def has(name: str) -> bool:
        try:
            return (path / name).exists()
        except OSError:
            return False

    # Godot
    if has("project.godot"):
        fp.stacks.append("godot")
        fp.markers.append("project.godot")
        local_godot = _find_local_godot(path)
        smoke = _find_godot_smoke_script(path)
        if local_godot and smoke:
            fp.hints.append(
                f"Godot: {local_godot.name} + smoke {smoke.as_posix()} "
                "(raise bash timeout_seconds for headless)"
            )
            fp.suggest_verify = (
                f'.\\{local_godot.name} --headless --path . -s {smoke.as_posix()}'
            )
        elif local_godot:
            fp.hints.append(
                f"Godot binary nearby: {local_godot.name} — "
                f"prefer tools/smoke_*.gd or tools/diag_*.gd with -s; "
                f"fallback --quit-after 1"
            )
            fp.suggest_verify = (
                f'.\\{local_godot.name} --headless --path . --quit-after 1'
            )
        else:
            fp.hints.append(
                "Godot project: use local Godot*_console.exe if present; "
                "not required on PATH"
            )
            fp.suggest_verify = None

    # Node
    if has("package.json"):
        fp.stacks.append("node")
        fp.markers.append("package.json")
        fp.hints.append("Node: prefer npm/pnpm/yarn scripts from package.json")
        if not fp.suggest_verify:
            if has("pnpm-lock.yaml"):
                fp.suggest_verify = "pnpm test"
            elif has("yarn.lock"):
                fp.suggest_verify = "yarn test"
            else:
                fp.suggest_verify = "npm test"

    # Python
    if has("pyproject.toml") or has("pytest.ini") or has("setup.py") or has("setup.cfg"):
        fp.stacks.append("python")
        for m in ("pyproject.toml", "pytest.ini", "setup.py"):
            if has(m):
                fp.markers.append(m)
        fp.hints.append("Python: uv run pytest / pytest -q when tests exist")
        if not fp.suggest_verify:
            if has("uv.lock") or (path / ".venv").is_dir():
                fp.suggest_verify = "uv run pytest -q"
            else:
                fp.suggest_verify = "pytest -q"

    # Rust
    if has("Cargo.toml"):
        fp.stacks.append("rust")
        fp.markers.append("Cargo.toml")
        fp.hints.append("Rust: cargo test / cargo check")
        if not fp.suggest_verify:
            fp.suggest_verify = "cargo test"

    # Go
    if has("go.mod"):
        fp.stacks.append("go")
        fp.markers.append("go.mod")
        fp.hints.append("Go: go test ./…")
        if not fp.suggest_verify:
            fp.suggest_verify = "go test ./..."

    # Make / just
    if has("Makefile") or has("makefile"):
        fp.stacks.append("make")
        fp.markers.append("Makefile")
        fp.hints.append("Makefile present — check targets before inventing commands")
        if not fp.suggest_verify:
            fp.suggest_verify = "make test"
    if has("justfile") or has("Justfile"):
        fp.stacks.append("just")
        fp.markers.append("justfile")
        if not fp.suggest_verify:
            fp.suggest_verify = "just test"

    # .NET
    if any(path.glob("*.sln")) or any(path.glob("*.csproj")):
        fp.stacks.append("dotnet")
        fp.hints.append("dotnet: dotnet test")
        if not fp.suggest_verify:
            fp.suggest_verify = "dotnet test"

    # Git (informational)
    if (path / ".git").exists():
        fp.markers.append(".git")

    # Dedupe preserve order
    seen_s: set[str] = set()
    unique_stacks: list[str] = []
    for s in fp.stacks:
        if s not in seen_s:
            seen_s.add(s)
            unique_stacks.append(s)
    fp.stacks = unique_stacks
    return fp


def _find_local_godot(root: Path) -> Path | None:
    try:
        for p in sorted(root.glob("Godot*.exe")):
            if p.is_file() and "console" in p.name.lower():
                return p
        for p in sorted(root.glob("Godot*.exe")):
            if p.is_file():
                return p
    except OSError:
        pass
    return None


def _find_godot_smoke_script(root: Path) -> Path | None:
    """Prefer tools/smoke_*.gd or tools/diag_*.gd for real verify."""
    tools = root / "tools"
    if not tools.is_dir():
        return None
    patterns = ("smoke_*.gd", "diag_*.gd", "boot_*.gd", "validate_*.gd")
    found: list[Path] = []
    try:
        for pat in patterns:
            found.extend(tools.glob(pat))
    except OSError:
        return None
    # Prefer shorter names / smoke over diag
    def rank(p: Path) -> tuple[int, str]:
        n = p.name.lower()
        if n.startswith("smoke"):
            return (0, n)
        if n.startswith("boot"):
            return (1, n)
        if n.startswith("diag"):
            return (2, n)
        return (3, n)

    found = [p for p in found if p.is_file()]
    if not found:
        return None
    best = sorted(found, key=rank)[0]
    try:
        return best.relative_to(root)
    except ValueError:
        return Path("tools") / best.name


def orientation_block(root: Path | str | None, *, max_chars: int = _BLOCK_CHAR_CAP) -> str:
    """Budget-capped pointers to convention / handoff files under *root*."""
    if root is None:
        return ""
    path = Path(root)
    try:
        path = path.expanduser().resolve()
    except OSError:
        path = path.expanduser().absolute()
    if not path.is_dir():
        return ""

    lines: list[str] = ["Orientation (read these first if relevant):"]
    found = 0

    for name in _ORIENT_NAMES:
        p = path / name
        if not p.is_file():
            continue
        found += 1
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        rel = name
        if size > _ORIENT_MAX_BYTES:
            lines.append(f"- {rel} ({size} bytes) — file_read path for sections as needed")
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            lines.append(f"- {rel} (unreadable)")
            continue
        excerpt = " ".join(text.strip().split())
        if len(excerpt) > _EXCERPT_CHARS:
            excerpt = excerpt[: _EXCERPT_CHARS - 1] + "…"
        lines.append(f"- {rel}: {excerpt}" if excerpt else f"- {rel}")

    # Handoff pointer
    latest = path / "memory" / "LATEST_HANDOFF.md"
    if latest.is_file():
        found += 1
        try:
            pointer = latest.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            target = pointer[0].strip() if pointer else ""
        except OSError:
            target = ""
        lines.append(f"- memory/LATEST_HANDOFF.md → {target or '(empty pointer)'}")
        if target:
            handoff = path / "memory" / "SESSION_NOTES" / target
            if not handoff.is_file():
                handoff = path / "memory" / target
            if not handoff.is_file() and not target.endswith(".md"):
                handoff = path / "memory" / "SESSION_NOTES" / f"{target}.md"
            if handoff.is_file():
                try:
                    rel = handoff.relative_to(path).as_posix()
                except ValueError:
                    rel = str(handoff)
                lines.append(f"  handoff file: {rel} (file_read before continuing long work)")

    arch = path / "docs" / "ARCHITECTURE.md"
    if arch.is_file():
        found += 1
        try:
            size = arch.stat().st_size
        except OSError:
            size = 0
        lines.append(
            f"- docs/ARCHITECTURE.md ({size} bytes) — read when changing system design"
        )

    if found == 0:
        return ""

    block = "\n".join(lines)
    if len(block) > max_chars:
        block = block[: max_chars - 1] + "…"
    return block


def local_bin_dirs(workdir: Path | str | None) -> list[Path]:
    """Directories to prepend to PATH for project-local tools."""
    if workdir is None:
        return []
    root = Path(workdir)
    try:
        root = root.expanduser().resolve()
    except OSError:
        root = root.expanduser().absolute()
    if not root.is_dir():
        return []

    candidates = [
        root / ".venv" / "Scripts",
        root / ".venv" / "bin",
        root / "venv" / "Scripts",
        root / "venv" / "bin",
        root / "node_modules" / ".bin",
        root,  # local Godot / tools in repo root
    ]
    out: list[Path] = []
    for c in candidates:
        try:
            if c.is_dir():
                out.append(c)
        except OSError:
            continue
    return out


def path_env_with_local_bins(workdir: Path | str | None, base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Copy env with project-local bin dirs prepended to PATH."""
    import os

    env = dict(base_env) if base_env is not None else dict(os.environ)
    dirs = local_bin_dirs(workdir)
    if not dirs:
        return env
    sep = ";" if os.name == "nt" else ":"
    prefix = sep.join(str(d) for d in dirs)
    # Normalize to PATH for children; keep Path on Windows too
    current = env.get("PATH") or env.get("Path") or ""
    env["PATH"] = prefix + (sep + current if current else "")
    if os.name == "nt":
        env["Path"] = env["PATH"]
    return env
