"""Opportunistic stack fingerprint + orientation pointers for a work path.

Hints only — never gates search or requires a project root.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from remedy.core import game_engines

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
    # Game engine signals (name, version, binary, lang, verify) — only known keys.
    engine: dict[str, str] = field(default_factory=dict)
    # Research signals: kinds/markers/domains/packs (lists) + entry (str).
    # Empty unless _fingerprint_research found something; see research_summary().
    research: dict[str, list[str] | str] = field(default_factory=dict)

    def context_lines(self) -> list[str]:
        if not self.stacks and not self.hints:
            return []
        lines = [f"Stack fingerprint ({self.path}):"]
        if self.stacks:
            lines.append("  stacks: " + ", ".join(self.stacks))
        if self.engine.get("name"):
            lines.append("  engine: " + game_engines.engine_summary(self))
            skill = game_engines.engine_skill(self.engine["name"])
            skills = f"{skill}, game-dev-studio" if skill else "game-dev-studio"
            lines.append(f"  studio: game project — skills: {skills}")
        if self.research.get("kinds"):
            lines.append("  research: " + research_summary(self))
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
        _fingerprint_godot(path, fp)

    # Node
    if has("package.json"):
        fp.stacks.append("node")
        fp.markers.append("package.json")
        fp.hints.append("Node: prefer npm/pnpm/yarn scripts from package.json")
        _fingerprint_web_game(path, fp)
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
    _fingerprint_python_game(path, fp)

    # Rust
    if has("Cargo.toml"):
        fp.stacks.append("rust")
        fp.markers.append("Cargo.toml")
        fp.hints.append("Rust: cargo test / cargo check")
        if _cargo_has_dep(path / "Cargo.toml", "bevy"):
            fp.stacks.append("bevy")
            fp.engine.setdefault("name", "bevy")
            fp.engine.setdefault("lang", "rust")
            fp.hints.append(
                "Bevy: cargo check is the fast gate; enable the bevy/dynamic_linking "
                "feature for quicker dev rebuilds"
            )
            if not fp.suggest_verify:
                fp.suggest_verify = "cargo check"
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

    # LÖVE
    if has("main.lua"):
        fp.stacks.append("love2d")
        fp.markers.append("main.lua")
        _fingerprint_love(path, fp)

    # Unity
    if (path / "ProjectSettings" / "ProjectVersion.txt").is_file():
        fp.stacks.append("unity")
        fp.markers.append("ProjectSettings/ProjectVersion.txt")
        _fingerprint_unity(path, fp)

    # Unreal
    uprojects = _safe_glob(path, "*.uproject")
    if uprojects:
        fp.stacks.append("unreal")
        fp.markers.append(uprojects[0].name)
        _fingerprint_unreal(path, uprojects[0], fp)

    # .NET
    if any(path.glob("*.sln")) or any(path.glob("*.csproj")):
        fp.stacks.append("dotnet")
        fp.hints.append("dotnet: dotnet test")
        if not fp.suggest_verify:
            fp.suggest_verify = "dotnet test"

    # Research (notebooks / R / Julia / workflow / manuscript / data)
    _fingerprint_research(path, fp)

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


def _safe_glob(root: Path, pattern: str) -> list[Path]:
    try:
        return sorted(p for p in root.glob(pattern) if p.is_file())
    except OSError:
        return []


def _read_small(p: Path, limit: int = 12_000) -> str:
    try:
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def _find_local_godot(root: Path) -> Path | None:
    return game_engines.find_repo_godot(root)


def _find_godot_smoke_script(root: Path) -> Path | None:
    return game_engines.find_godot_smoke_script(root)


_GODOT_CONFIG_VERSION_RE = re.compile(r"^\s*config_version\s*=\s*(\d+)", re.M)
_GODOT_FEATURES_RE = re.compile(
    r"^\s*config/features\s*=\s*PackedStringArray\(([^)]*)\)", re.M
)
_GODOT_SRC_CAP = 400


def _godot_source_langs(root: Path) -> tuple[int, int]:
    """(gd_count, cs_count) over a capped walk skipping .godot/ and addons/."""
    gd = cs = 0
    seen = 0
    try:
        for p in root.rglob("*"):
            parts = p.parts[len(root.parts) :]
            if any(part in (".godot", "addons", ".git") for part in parts[:-1]):
                continue
            if not p.is_file():
                continue
            seen += 1
            if p.suffix == ".gd":
                gd += 1
            elif p.suffix == ".cs":
                cs += 1
            if seen >= _GODOT_SRC_CAP:
                break
    except OSError:
        pass
    return gd, cs


def _fingerprint_godot(path: Path, fp: StackFingerprint) -> None:
    text = _read_small(path / "project.godot")
    fp.engine["name"] = "godot"
    version = ""
    features: list[str] = []
    m = _GODOT_FEATURES_RE.search(text)
    if m:
        features = re.findall(r'"([^"]*)"', m.group(1))
        for f in features:
            if re.fullmatch(r"\d+(?:\.\d+)*", f):
                version = f
                break
    if not version:
        m = _GODOT_CONFIG_VERSION_RE.search(text)
        if m:
            version = {"5": "4", "4": "3"}.get(m.group(1), "")
    if version:
        fp.engine["version"] = version
    csharp = "C#" in features or bool(_safe_glob(path, "*.csproj"))
    gd, cs = _godot_source_langs(path)
    if gd and cs:
        lang = "mixed"
    elif csharp or cs:
        lang = "csharp"
    else:
        lang = "gdscript"
    fp.engine["lang"] = lang

    binary = game_engines.find_engine_binary("godot", project_root=path, version=version or None)
    smoke = _find_godot_smoke_script(path)
    if binary is not None:
        fp.engine["binary"] = str(binary)
        verify = game_engines.godot_verify_command(path, binary)
        if verify:
            fp.engine["verify"] = verify
        if smoke:
            fp.hints.append(
                f"Godot: {binary.name} + smoke {smoke.as_posix()} "
                "(raise bash timeout_seconds for headless)"
            )
        else:
            fp.hints.append(
                f"Godot binary: {binary.name} — "
                "prefer tools/smoke_*.gd or tools/diag_*.gd with -s; "
                "fallback --quit-after 1"
            )
        fp.suggest_verify = verify
    else:
        fp.hints.append(
            "Godot project: binary not found — set GODOT or drop Godot*_console.exe "
            "in the repo root; not required on PATH"
        )
        fp.suggest_verify = None


def _package_deps(path: Path) -> set[str]:
    try:
        data = json.loads(_read_small(path / "package.json", 64_000))
    except (ValueError, OSError):
        return set()
    if not isinstance(data, dict):
        return set()
    deps: set[str] = set()
    for key in ("dependencies", "devDependencies"):
        block = data.get(key)
        if isinstance(block, dict):
            deps.update(str(k).lower() for k in block)
    return deps


def _fingerprint_web_game(path: Path, fp: StackFingerprint) -> None:
    deps = _package_deps(path)
    name = ""
    if "phaser" in deps:
        name = "phaser"
    elif "pixi.js" in deps:
        name = "pixi"
    if not name:
        return
    fp.stacks.append(name)
    fp.engine.setdefault("name", name)
    fp.engine.setdefault("lang", "javascript")
    fp.hints.append(
        f"{name}: `npm run dev` starts a dev server — run it in the background "
        "(never block on it); keep npm test as the gate"
    )


def _cargo_has_dep(cargo: Path, dep: str) -> bool:
    text = _read_small(cargo, 64_000)
    in_deps = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            in_deps = s.startswith("[dependencies") or s.startswith("[dev-dependencies")
            continue
        if in_deps and re.match(rf"^{re.escape(dep)}\s*=", s):
            return True
    return False


def _python_declared_deps(path: Path) -> str:
    text = ""
    for name in ("pyproject.toml", "requirements.txt", "requirements-dev.txt"):
        p = path / name
        if p.is_file():
            text += _read_small(p, 32_000).lower() + "\n"
    return text


def _fingerprint_python_game(path: Path, fp: StackFingerprint) -> None:
    from remedy.core.interactive_launch import _GUI_SRC_RE

    deps = _python_declared_deps(path)
    name = ""
    main: Path | None = None
    if re.search(r"\bpygame(?:-ce)?\b", deps):
        name = "pygame"
    elif re.search(r"\barcade\b", deps):
        name = "arcade"
    for py in _safe_glob(path, "*.py")[:40]:
        src = _read_small(py)
        if not _GUI_SRC_RE.search(src):
            continue
        if re.search(r"^\s*(?:import|from)\s+pygame\b", src, re.M):
            name = name or "pygame"
            main = main or py
        elif re.search(r"^\s*(?:import|from)\s+arcade\b", src, re.M):
            name = name or "arcade"
            main = main or py
    if not name:
        return
    if "python" not in fp.stacks:
        fp.stacks.append("python")
    fp.stacks.append(name)
    fp.engine.setdefault("name", name)
    fp.engine.setdefault("lang", "python")
    fp.hints.append(
        f"{name}: windowed game — run it in the background, never block the gate on it"
    )
    has_tests = (path / "tests").is_dir() or bool(_safe_glob(path, "test_*.py"))
    if not has_tests:
        if main is None:
            for cand in ("main.py", "game.py", "app.py"):
                if (path / cand).is_file():
                    main = path / cand
                    break
        if main is not None:
            fp.suggest_verify = f"python -m py_compile {main.name}"


def _fingerprint_love(path: Path, fp: StackFingerprint) -> None:
    fp.engine.setdefault("name", "love2d")
    fp.engine.setdefault("lang", "lua")
    binary = game_engines.find_engine_binary("love2d", project_root=path)
    if binary is not None:
        fp.engine["binary"] = str(binary)
    if shutil.which("luac"):
        verify = "luac -p main.lua"
        fp.engine["verify"] = verify
        if not fp.suggest_verify:
            fp.suggest_verify = verify
        fp.hints.append(
            "LÖVE: luac -p main.lua is the syntax gate; `love .` runs windowed (background)"
        )
    else:
        fp.hints.append(
            "LÖVE: no luac on PATH — install Lua for a `luac -p main.lua` gate; "
            "`love .` runs windowed (background)"
        )


def _fingerprint_unity(path: Path, fp: StackFingerprint) -> None:
    fp.engine.setdefault("name", "unity")
    fp.engine.setdefault("lang", "csharp")
    text = _read_small(path / "ProjectSettings" / "ProjectVersion.txt")
    m = re.search(r"^m_EditorVersion:\s*(\S+)", text, re.M)
    version = m.group(1) if m else ""
    if version:
        fp.engine["version"] = version
    binary = game_engines.find_engine_binary("unity", project_root=path, version=version or None)
    if binary is not None:
        fp.engine["binary"] = str(binary)
    exe = f'"{binary}"' if binary is not None else "Unity"
    fp.hints.append(
        f"Unity {version or ''}: no cheap gate — compile check is "
        f"{exe} -batchmode -nographics -quit -projectPath . -logFile - (slow; background)"
    )


def _fingerprint_unreal(path: Path, uproject: Path, fp: StackFingerprint) -> None:
    fp.engine.setdefault("name", "unreal")
    fp.engine.setdefault("lang", "cpp")
    assoc = ""
    try:
        data = json.loads(_read_small(uproject, 64_000))
        assoc = str(data.get("EngineAssociation", "") or "") if isinstance(data, dict) else ""
    except ValueError:
        assoc = ""
    if assoc:
        fp.engine["version"] = assoc
    binary = game_engines.find_engine_binary("unreal", project_root=path, version=assoc or None)
    if binary is not None:
        fp.engine["binary"] = str(binary)
    runuat = f'"{binary}"' if binary is not None else "RunUAT"
    fp.hints.append(
        f"Unreal {assoc or ''}: no cheap gate — "
        f"{runuat} BuildCookRun -project={uproject.name} -build (very slow; background); "
        "set UE_ROOT if the engine is elsewhere"
    )


# --- research projects -------------------------------------------------------
# A folder holding a paper, a notebook and a CSV is invisible to the markers
# above. Detection here is a HINT, never a gate — the research packs must still
# work in a bare folder. Every probe is a stat, one capped directory listing, or
# a small capped read; no recursive walks.

_RESEARCH_SCAN_DIRS = ("", "data", "data/raw", "raw", "datasets", "results")
_RESEARCH_SCAN_CAP = 240  # entries listed per directory
_RESEARCH_HIT_CAP = 6  # data files named in the record
_NOTEBOOK_DIRS = ("", "notebooks", "analysis")
_MANUSCRIPT_DIRS = ("", "paper", "manuscript")
_TEX_READ_CAP = 6  # *.tex files opened looking for \documentclass

# Declared-dependency sniffs (lowercased text).
_SCIENCE_DEP_RE = re.compile(r"\b(numpy|pandas|scipy|scikit-learn)\b")
_PY_DEP_FILES = (
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "environment.yml",
    "environment.yaml",
    "conda-lock.yml",
    "conda-lock.yaml",
)
# Julia [deps] entries that mark a project as analysis rather than a library.
_JULIA_SCI_RE = re.compile(
    r"^\s*(DataFrames|CSV|Plots|Makie|Gadfly|StatsBase|StatsPlots|Distributions|"
    r"StatsModels|HypothesisTests|GLM|DifferentialEquations|Turing|MCMCChains|"
    r"Flux|JuMP|Pluto|Optim|Unitful)\s*=",
    re.M,
)
_TEX_DOCUMENTCLASS_RE = re.compile(r"^\s*\\documentclass", re.M)
_MAKE_DATA_TARGET_RE = re.compile(r"^(?:data|datasets?|data/\S+)\s*:(?!=)", re.M)
_COMPRESSION_SUFFIXES = (".gz", ".bz2", ".zst", ".xz")

# A data format that names its own field. Suffix -> the pack that fits.
_DATA_DOMAINS: dict[str, str] = {
    ".fastq": "bioinformatics",
    ".fq": "bioinformatics",
    ".fasta": "bioinformatics",
    ".fa": "bioinformatics",
    ".sam": "bioinformatics",
    ".bam": "bioinformatics",
    ".cram": "bioinformatics",
    ".vcf": "bioinformatics",
    ".bcf": "bioinformatics",
    ".nii": "neuroscience",
    ".nc": "earth-climate",
    ".nc4": "earth-climate",
    ".grib": "earth-climate",
    ".grib2": "earth-climate",
    ".grb": "earth-climate",
    ".mzml": "chemistry-research",
}
# Bulk formats that say "data-heavy" without naming a field.
_DATA_SUFFIXES = (".parquet", ".h5", ".hdf5", ".feather")
# Kinds that imply a pack on their own. Domains (above) are stronger evidence.
_KIND_PACKS: dict[str, str] = {"r": "statistics", "julia": "computational-science"}


def _rel_name(root: Path, p: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.name


def _data_suffix(name: str) -> str:
    """Lowercased suffix with one compression layer stripped (.nii.gz -> .nii)."""
    low = name.lower()
    for c in _COMPRESSION_SUFFIXES:
        if low.endswith(c):
            low = low[: -len(c)]
            break
    dot = low.rfind(".")
    return low[dot:] if dot > 0 else ""


def _scan_research_data(path: Path) -> tuple[list[str], list[str]]:
    """(sample data files, domains) from capped listings of data-ish dirs."""
    hits: list[str] = []
    domains: list[str] = []
    for rel in _RESEARCH_SCAN_DIRS:
        d = path / rel if rel else path
        try:
            if not d.is_dir():
                continue
            for i, p in enumerate(d.iterdir()):
                if i >= _RESEARCH_SCAN_CAP:
                    break
                suffix = _data_suffix(p.name)
                if not suffix:
                    continue
                domain = _DATA_DOMAINS.get(suffix)
                if domain is None and suffix not in _DATA_SUFFIXES:
                    continue
                if not p.is_file():
                    continue
                name = f"{rel}/{p.name}" if rel else p.name
                if len(hits) < _RESEARCH_HIT_CAP and name not in hits:
                    hits.append(name)
                if domain and domain not in domains:
                    domains.append(domain)
        except OSError:
            continue
    return hits, domains


def _looks_bids(path: Path) -> bool:
    """dataset_description.json beside sub-*/ — the BIDS neuroimaging layout."""
    for rel in ("", "data", "bids"):
        d = path / rel if rel else path
        try:
            if not (d / "dataset_description.json").is_file():
                continue
            for i, sub in enumerate(d.glob("sub-*")):
                if sub.is_dir():
                    return True
                if i >= 20:
                    break
        except OSError:
            continue
    return False


def research_summary(fp: StackFingerprint) -> str:
    """One-line ``research project (notebooks, data) — skills: …`` summary."""
    rec = getattr(fp, "research", None) or {}
    kinds = [str(k) for k in (rec.get("kinds") or [])]
    if not kinds:
        return ""
    packs = [str(p) for p in (rec.get("packs") or [])]
    head = "research project (" + ", ".join(kinds) + ")"
    if packs:
        head += " — skills: " + ", ".join(packs)
    return head


def _fingerprint_research(path: Path, fp: StackFingerprint) -> None:
    """Record research signals on *fp* (kinds, markers, domain, packs)."""

    def has(name: str) -> bool:
        try:
            return (path / name).exists()
        except OSError:
            return False

    kinds: list[str] = []
    markers: list[str] = []
    domains: list[str] = []
    stacks: list[str] = []
    entry = ""

    def note(kind: str, stack: str, found: list[str]) -> None:
        if kind not in kinds:
            kinds.append(kind)
        if stack not in stacks:
            stacks.append(stack)
        for m in found:
            if m and m not in markers:
                markers.append(m)

    # Notebooks — shallow glob, root + notebooks/ + analysis/.
    notebooks: list[Path] = []
    for rel in _NOTEBOOK_DIRS:
        notebooks.extend(_safe_glob(path / rel if rel else path, "*.ipynb")[:5])
    if notebooks:
        note("notebooks", "notebook", [_rel_name(path, p) for p in notebooks[:3]])
        entry = entry or _rel_name(path, notebooks[0])

    # Python science env — supporting evidence, never a research verdict on its
    # own (numpy in a pyproject does not make an engineering repo a study).
    science_deps: list[str] = []
    science_files: list[str] = []
    for name in _PY_DEP_FILES:
        p = path / name
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        found = _SCIENCE_DEP_RE.findall(_read_small(p, 32_000).lower())
        if found:
            science_files.append(name)
            for dep in found:
                if dep not in science_deps:
                    science_deps.append(dep)

    # R
    r_markers = [n for n in ("DESCRIPTION", "renv.lock", ".Rprofile") if has(n)]
    for pattern in ("*.Rproj", "*.Rmd", "*.rmd"):
        for p in _safe_glob(path, pattern)[:2]:
            if p.name not in r_markers:
                r_markers.append(p.name)
    if r_markers:
        note("r", "r", r_markers)

    # Julia — Project.toml whose deps are analysis packages.
    proj = path / "Project.toml"
    try:
        julia = proj.is_file() and bool(_JULIA_SCI_RE.search(_read_small(proj, 32_000)))
    except OSError:
        julia = False
    if julia:
        found = ["Project.toml"] + (["Manifest.toml"] if has("Manifest.toml") else [])
        note("julia", "julia", found)

    # Workflow engines
    wf = [
        n
        for n in ("Snakefile", "workflow/Snakefile", "nextflow.config", "main.nf")
        if has(n)
    ]
    for rel in ("", "workflow"):
        for p in _safe_glob(path / rel if rel else path, "*.cwl")[:2]:
            wf.append(_rel_name(path, p))
    for mk in ("Makefile", "makefile"):
        if has(mk) and _MAKE_DATA_TARGET_RE.search(_read_small(path / mk, 32_000)):
            wf.append(mk)
            break
    if wf:
        note("workflow", "pipeline", wf)
        entry = entry or wf[0]

    # Manuscript
    ms: list[str] = []
    for rel in _MANUSCRIPT_DIRS:
        d = path / rel if rel else path
        for tex in _safe_glob(d, "*.tex")[:_TEX_READ_CAP]:
            if _TEX_DOCUMENTCLASS_RE.search(_read_small(tex, 8_000)):
                ms.append(_rel_name(path, tex))
                break
    for n in ("_quarto.yml", "_quarto.yaml"):
        if has(n):
            ms.append(n)
    for rel in _MANUSCRIPT_DIRS:
        d = path / rel if rel else path
        for p in _safe_glob(d, "*.qmd")[:2] + _safe_glob(d, "*.bib")[:2]:
            ms.append(_rel_name(path, p))
    if ms:
        note("manuscript", "manuscript", ms)

    # Data-heavy layout
    data_markers: list[str] = []
    outdir = next((d for d in ("results", "figures", "outputs") if (path / d).is_dir()), "")
    if (path / "data").is_dir() and outdir:
        data_markers += ["data/", f"{outdir}/"]
    data_markers += [n for n in ("dvc.yaml", "dvc.lock", ".dvc") if has(n)]
    hits, found_domains = _scan_research_data(path)
    data_markers += hits
    domains += [d for d in found_domains if d not in domains]
    if _looks_bids(path):
        data_markers.append("dataset_description.json")
        if "neuroscience" not in domains:
            domains.append("neuroscience")
    if data_markers:
        note("data", "data", data_markers)

    if not kinds:
        return

    # Analysis entry point — a real file, never a guessed command.
    if not entry:
        for cand in ("analysis.R", "analysis.py", "analyse.py", "analysis.jl", "main.R"):
            if has(cand):
                entry = cand
                break
        if not entry and ms:
            entry = ms[0]

    domains.sort()  # directory listing order is not stable across filesystems
    packs = ["research-method"]
    for d in domains:
        if d not in packs:
            packs.append(d)
    for k in kinds:
        p_name = _KIND_PACKS.get(k)
        if p_name and p_name not in packs:
            packs.append(p_name)
    packs = packs[:3]  # registry.triggered_skills() surfaces 3 by default

    fp.stacks.append("research")
    fp.stacks.extend(stacks)
    for m in markers:
        if m not in fp.markers:
            fp.markers.append(m)

    record: dict[str, list[str] | str] = {
        "kinds": kinds,
        "markers": markers,
        "domains": domains,
        "packs": packs,
    }
    if entry:
        record["entry"] = entry
    if science_deps:
        record["python_science"] = science_deps
        for name in science_files:
            if name not in fp.markers:
                fp.markers.append(name)
    fp.research = record

    # Verify: the project's own test command only. A notebook has no cheap gate,
    # so it is named as the entry point in a hint instead of being invented into
    # a command (suggest_verify is executed verbatim by jobs.verify).
    if not fp.suggest_verify:
        if "r" in kinds and (path / "tests" / "testthat").is_dir():
            fp.suggest_verify = "Rscript -e 'testthat::test_dir(\"tests\")'"
        elif "julia" in kinds and (path / "test" / "runtests.jl").is_file():
            fp.suggest_verify = "julia --project -e 'using Pkg; Pkg.test()'"
        elif has("Snakefile") or has("workflow/Snakefile"):
            # The workflow's own dry run — resolves the DAG, computes nothing.
            fp.suggest_verify = "snakemake -n"

    fp.hints.append(
        "Research project: analysis_env first; every result through analysis_run "
        "so it lands in the ledger; cite_check before the manuscript is done"
    )
    if entry and not fp.suggest_verify:
        fp.hints.append(
            f"Analysis lives in {entry} — no test command in this project; "
            "run it through analysis_run rather than inventing a gate"
        )


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
