"""Reproducible-analysis tools: environment probe, headless runs, run ledger,
dataset profile and dataset drift.

Everything here runs in the *project's* own environment — ``uv run python``,
``.venv/bin/python``, ``Rscript``, ``julia --project``, ``quarto`` — never in
Remedy's. Remedy's sidecar ships without pandas/numpy/scipy, so the numerics in
this module are pure stdlib (``csv``, ``math``, ``statistics``) and anything
heavier is shelled out to the project interpreter and parsed back as JSON.

Every subprocess goes through the same ``SubprocessSandbox`` +
``allowed_paths_for_shell`` + approval gate that ``bash_exec`` uses; there is no
free-form command parameter anywhere in this module (that is ``bash_exec``'s
job). Writes resolve through ``runtime.resolve_tool_path(..., for_write=True)``
and are checked against the write roots, returning the same ``WRITE_JAIL``
error shape ``bash_exec`` returns.

Test seams (monkeypatch these, never a real papermill/Rscript/julia/quarto):
``_sandbox_run`` and ``_approval_block``.
"""

from __future__ import annotations

import asyncio
import csv
import fnmatch
import hashlib
import json
import math
import os
import random
import re
import shlex
import shutil
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from remedy.core.errors import format_tool_error
from remedy.home import default_home

_TAIL_CHARS = 6_000
_ARTIFACT_FILE_CAP = 200
_ARTIFACT_BYTES_CAP = 50 * 1024 * 1024
_SCAN_FILE_CAP = 20_000
_UNIQUE_CAP = 20_000
_TOPVALUE_CAP = 2_000
_SAMPLE_CAP = 2_000
_DUP_HASH_CAP = 500_000
_PURITY_CARD_CAP = 200
_JSON_BYTES_CAP = 64 * 1024 * 1024
_PROFILE_MARKER = "REMEDY_PROFILE_JSON "
_ENV_MARKER = "REMEDY_ENV_JSON "

_DEFAULT_ARTIFACT_GLOB = (
    "*.png,*.pdf,*.svg,*.csv,*.tsv,*.parquet,*.json,*.html,*.tex,*.md,"
    "figures/**,outputs/**,results/**"
)

_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".ipynb_checkpoints",
    ".remedy-build",
    ".remedy-research",
    ".remedy-analysis",
    ".quarto",
    "renv",
}

# Column names that *often* leak an outcome. A match is a prompt to look, never
# a verdict — see ``_leakage_suspects``.
_LEAK_NAME_RE = re.compile(
    r"(?i)(^|[_\W])(outcome|label|target|y_true|y_pred|ytrue|ypred|gold|"
    r"ground_?truth|score|prediction|predicted|leak)([_\W]|$)|^(post|final)_|_leak$"
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?")
_BOOL_TRUE = {"true", "t", "yes", "y", "1"}
_BOOL_FALSE = {"false", "f", "no", "n", "0"}
_MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "nil", "-", "?"}

# The ledger is appended from the turn path and (potentially) a background
# thread — serialise appends the way build_ledger.py serialises its saves.
_ledger_lock = threading.RLock()


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _dump(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=str)


def _tail(text: str, cap: int = _TAIL_CHARS) -> str:
    text = text or ""
    if len(text) <= cap:
        return text
    return f"…[{len(text) - cap} chars cut]\n" + text[-cap:]


def _clamp(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        v = float(value if value is not None else default)
    except (TypeError, ValueError):
        v = default
    if math.isnan(v):
        v = default
    return max(lo, min(hi, v))


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _sha256_file(path: Path, *, cap: int = 512 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    read = 0
    try:
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                read += len(chunk)
                if read > cap:
                    return ""
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _argv_text(argv: list[str]) -> str:
    return " ".join(f'"{a}"' if " " in a else a for a in argv)


def _parse_json_obj(raw: str) -> tuple[dict[str, Any], str]:
    """``{"k": v}`` from a string param. Returns (obj, error_message)."""
    text = (raw or "").strip()
    if not text:
        return {}, ""
    try:
        obj = json.loads(text)
    except (TypeError, ValueError) as exc:
        return {}, f"not valid JSON: {exc}"
    if not isinstance(obj, dict):
        return {}, "expected a JSON object like {\"alpha\": 0.05}"
    return {str(k): v for k, v in obj.items()}, ""


def _split_list(raw: str) -> list[str]:
    """Comma list or JSON array → list of trimmed strings."""
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        with suppress(ValueError, TypeError):
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
    return [p.strip() for p in text.split(",") if p.strip()]


# --------------------------------------------------------------------------
# locations — research dir / run ledger (mirrors build_ledger.py)
# --------------------------------------------------------------------------


def research_dir_for_project(
    project_path: str | Path | None,
    *,
    home: str | Path | None = None,
) -> Path:
    """``{project}/.remedy-research`` when a project is bound, else home/research/<hash>."""
    if project_path:
        p = Path(project_path).expanduser()
        with suppress(OSError):
            if p.is_file():
                p = p.parent
            if p.is_dir():
                d = p / ".remedy-research"
                d.mkdir(parents=True, exist_ok=True)
                return d
    key = hashlib.sha256(str(project_path or "none").encode("utf-8")).hexdigest()[:16]
    base = Path(home).expanduser() if home else default_home()
    d = base / "research" / key
    d.mkdir(parents=True, exist_ok=True)
    return d


def runs_dir_for_project(
    project_path: str | Path | None,
    *,
    home: str | Path | None = None,
) -> Path:
    d = research_dir_for_project(project_path, home=home) / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ledger_path_for_project(
    project_path: str | Path | None,
    *,
    home: str | Path | None = None,
) -> Path:
    return runs_dir_for_project(project_path, home=home) / "ledger.jsonl"


def _append_ledger(runs_dir: Path, summary: dict[str, Any]) -> Path:
    path = runs_dir / "ledger.jsonl"
    line = json.dumps(summary, ensure_ascii=False, default=str)
    with _ledger_lock, path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return path


def _read_ledger(runs_dir: Path) -> list[dict[str, Any]]:
    path = runs_dir / "ledger.jsonl"
    out: list[dict[str, Any]] = []
    if not path.is_file():
        return out
    with suppress(OSError), path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            with suppress(ValueError):
                rec = json.loads(raw)
                if isinstance(rec, dict):
                    out.append(rec)
    return out


# --------------------------------------------------------------------------
# runtime plumbing — approvals, sandbox, write jail
# --------------------------------------------------------------------------


def _approval_block(runtime: Any, tool: str, command: str) -> str | None:
    """Same partner-trust gate bash_exec applies (ask mode → APPROVAL_REQUIRED)."""
    try:
        from remedy.core.approvals import APPROVALS
        from remedy.core.turn_context import turn_session_id
    except Exception:
        return None
    reason = APPROVALS.needs_ask(command, tool_name=tool)
    sid = turn_session_id(runtime)
    if not reason or APPROVALS.is_approved(tool, command, session_id=sid):
        return None
    item = APPROVALS.create(tool_name=tool, command=command, reason=reason, session_id=sid)
    return (
        f"APPROVAL_REQUIRED id={item.id}\n"
        f"reason={reason}\n"
        f"command={command[:400]}\n"
        "Do not invent success. Tell the user this needs approval in the UI "
        f"(or /approve {item.id}). After they approve, retry {tool}."
    )


def _write_roots(runtime: Any) -> list[Path]:
    try:
        return [Path(p) for p in (runtime.write_roots() or [])]
    except Exception:
        return []


async def _sandbox_run(
    runtime: Any,
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    env_extra: dict[str, str] | None = None,
) -> Any:
    """Run argv through SubprocessSandbox exactly like bash_exec does."""
    from remedy.core.project_fingerprint import path_env_with_local_bins
    from remedy.execution.sandbox import SubprocessSandbox, allowed_paths_for_shell

    roots = _write_roots(runtime) or [cwd]
    sandbox = SubprocessSandbox(allowed_paths=allowed_paths_for_shell(roots, cwd))
    env = path_env_with_local_bins(cwd)
    if env_extra:
        env.update({str(k): str(v) for k, v in env_extra.items()})
    return await sandbox.execute(argv, workdir=cwd, timeout_seconds=timeout, env=env)


def _project_root(runtime: Any) -> Path:
    try:
        return Path(runtime.effective_project_path())
    except Exception:
        return Path.cwd()


def _resolve_read(runtime: Any, raw: str) -> Path:
    p = Path((raw or "").strip()).expanduser()
    if p.is_absolute():
        return p
    try:
        return Path(runtime.resolve_tool_path(str(p)))
    except Exception:
        return _project_root(runtime) / p


def _allowed_write_roots(runtime: Any) -> list[Path]:
    roots = [_project_root(runtime), *_write_roots(runtime)]
    with suppress(Exception):
        roots.extend(Path(p) for p in (runtime.allowed_roots() or []))
    out: list[Path] = []
    for r in roots:
        with suppress(OSError):
            rr = r.expanduser().resolve(strict=False)
            if rr not in out:
                out.append(rr)
    return out


def _jail_error(tool: str, target: Path) -> str:
    return format_tool_error(
        f"{target} is outside the project / allowed write roots",
        code="WRITE_JAIL",
        tool_name=tool,
        suggestion=(
            "Stay inside the focus project. Pass a path under the project folder "
            "(e.g. outputs/, figures/), or switch the project first."
        ),
    )


def _resolve_write(runtime: Any, raw: str, tool: str, *, default: Path) -> Path | str:
    """Resolve a write target through the runtime jail. Path or error string."""
    text = (raw or "").strip()
    if not text:
        target = default
    else:
        try:
            target = Path(runtime.resolve_tool_path(text, for_write=True))
        except Exception as exc:  # PermissionError / SecurityError from the jail
            return format_tool_error(
                f"write refused for {text}: {exc}",
                code="WRITE_JAIL",
                tool_name=tool,
                suggestion="Pass a path under the focus project (e.g. outputs/run1).",
            )
    allowed = _allowed_write_roots(runtime)
    if not allowed:
        return target
    try:
        res = target.expanduser().resolve(strict=False)
    except OSError:
        return _jail_error(tool, target)
    for root in allowed:
        with suppress(ValueError, OSError):
            if res == root or res.is_relative_to(root):
                return res
    return _jail_error(tool, target)


def _note(runtime: Any, target: Path) -> None:
    with suppress(Exception):
        from remedy.core.workspace_tools.guards import note_path

        note_path(runtime, target)


def _path_env(root: Path) -> dict[str, str]:
    from remedy.core.project_fingerprint import path_env_with_local_bins

    return path_env_with_local_bins(root)


def _which(name: str, root: Path) -> str:
    env = _path_env(root)
    found = shutil.which(name, path=env.get("PATH") or env.get("Path") or os.environ.get("PATH"))
    return str(found) if found else ""


# --------------------------------------------------------------------------
# project interpreter discovery
# --------------------------------------------------------------------------


def _venv_python(root: Path) -> Path | None:
    for rel in (
        Path(".venv") / "Scripts" / "python.exe",
        Path(".venv") / "bin" / "python",
        Path("venv") / "Scripts" / "python.exe",
        Path("venv") / "bin" / "python",
        Path("env") / "Scripts" / "python.exe",
        Path("env") / "bin" / "python",
    ):
        cand = root / rel
        with suppress(OSError):
            if cand.is_file():
                return cand
    return None


def project_python(root: Path) -> tuple[list[str], str, list[str]]:
    """(argv prefix, source, notes) for the PROJECT's python — never Remedy's own.

    Order: project ``.venv`` → ``uv run python`` when uv.lock/pyproject + uv →
    the interpreter ``resolve_python_interpreter`` finds. The last one is a
    fallback and says so, because it is *not* the project environment.
    """
    notes: list[str] = []
    venv = _venv_python(root)
    if venv is not None:
        return [str(venv)], "project .venv", notes
    has_uv_marker = (root / "uv.lock").is_file() or (root / "pyproject.toml").is_file()
    uv = _which("uv", root)
    if has_uv_marker and uv:
        return [uv, "run", "python"], "uv run", notes
    if (root / "environment.yml").is_file() or (root / "environment.yaml").is_file():
        notes.append(
            "conda environment.yml present but no .venv — activate the conda env "
            "yourself, or point REMEDY_PYTHON at its interpreter; Remedy will not "
            "activate a conda env for you."
        )
    with suppress(Exception):
        from remedy.core.workspace_tools.shell import resolve_python_interpreter

        found = resolve_python_interpreter()
        if found:
            notes.append(
                "no project .venv or uv found — using a system interpreter, which "
                "may not have the project's packages installed."
            )
            return list(found), "system interpreter (not the project env)", notes
    return [], "", ["no usable Python interpreter found on PATH"]


# --------------------------------------------------------------------------
# environment probe
# --------------------------------------------------------------------------

#: name → (executable candidates, version argv)
_BINARY_RUNNERS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("uv", ("uv",), ("--version",)),
    ("jupyter", ("jupyter",), ("--version",)),
    ("papermill", ("papermill",), ("--version",)),
    ("Rscript", ("Rscript",), ("--version",)),
    ("julia", ("julia",), ("--version",)),
    ("quarto", ("quarto",), ("--version",)),
    ("pandoc", ("pandoc",), ("--version",)),
    ("latexmk", ("latexmk",), ("-version",)),
    ("pdflatex", ("pdflatex",), ("--version",)),
    ("xelatex", ("xelatex",), ("--version",)),
    ("tectonic", ("tectonic",), ("--version",)),
    ("biber", ("biber",), ("--version",)),
    ("bibtex", ("bibtex",), ("--version",)),
    ("pdftotext", ("pdftotext",), ("-v",)),
    ("git", ("git",), ("--version",)),
    ("dvc", ("dvc",), ("--version",)),
    ("snakemake", ("snakemake",), ("--version",)),
    ("nextflow", ("nextflow",), ("-version",)),
    ("make", ("make",), ("--version",)),
)

#: python modules probed in ONE subprocess inside the project env
_PY_MODULES = (
    "pandas",
    "numpy",
    "pyarrow",
    "papermill",
    "nbconvert",
    "nbformat",
    "ipykernel",
    "jupyter_core",
    "matplotlib",
    "openpyxl",
)

_R_PACKAGES = ("renv", "testthat", "knitr", "rmarkdown")

# NOTE: the probe deliberately uses importlib.util.find_spec rather than a real
# import — it must not execute heavy package init in the project env, and this
# module must never contain a literal import of pandas/numpy (sidecar excludes).
_PY_PROBE_SCRIPT = (
    "import json, sys\n"
    "from importlib import util as _u\n"
    "mods = json.loads(sys.argv[1])\n"
    "out = {}\n"
    "for m in mods:\n"
    "    try:\n"
    "        out[m] = bool(_u.find_spec(m))\n"
    "    except Exception:\n"
    "        out[m] = False\n"
    "print('" + _ENV_MARKER + "' + json.dumps("
    "{'modules': out, 'python': sys.version.split()[0], 'executable': sys.executable}))\n"
)

_R_PROBE_SCRIPT = (
    "pkgs <- c(%s); "
    "res <- sapply(pkgs, function(p) requireNamespace(p, quietly=TRUE)); "
    'cat("%s"); '
    'cat("{"); '
    'cat(paste0(sprintf("\\"%%s\\":%%s", names(res), ifelse(res,"true","false")), '
    'collapse=",")); '
    'cat("}\\n")'
)


def _marker_json(text: str, marker: str) -> dict[str, Any]:
    for line in reversed((text or "").splitlines()):
        idx = line.find(marker)
        if idx >= 0:
            with suppress(ValueError):
                obj = json.loads(line[idx + len(marker):])
                if isinstance(obj, dict):
                    return obj
    return {}


def _first_version_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return ""


# --------------------------------------------------------------------------
# tabular profiling — pure stdlib core
# --------------------------------------------------------------------------


class _Acc:
    """Streaming per-column accumulator (single pass, bounded memory)."""

    __slots__ = (
        "name",
        "n",
        "missing",
        "uniques",
        "unique_overflow",
        "counts",
        "count_overflow",
        "n_int",
        "n_float",
        "n_bool",
        "n_date",
        "num_n",
        "num_mean",
        "num_m2",
        "num_min",
        "num_max",
        "min_len",
        "max_len",
        "sample",
        "seen",
        "ws_hits",
        "target_map",
        "target_overflow",
        "class_num",
    )

    def __init__(self, name: str) -> None:
        self.name = name
        self.n = 0
        self.missing = 0
        self.uniques: set[str] = set()
        self.unique_overflow = False
        self.counts: dict[str, int] = {}
        self.count_overflow = False
        self.n_int = 0
        self.n_float = 0
        self.n_bool = 0
        self.n_date = 0
        self.num_n = 0
        self.num_mean = 0.0
        self.num_m2 = 0.0
        self.num_min: float | None = None
        self.num_max: float | None = None
        self.min_len: int | None = None
        self.max_len: int | None = None
        self.sample: list[float] = []
        self.seen = 0
        self.ws_hits = 0
        self.target_map: dict[str, set[str]] = {}
        self.target_overflow = False
        self.class_num: dict[str, list[float]] = {}

    def add(self, raw: Any, rng: random.Random, target_value: str | None) -> None:
        text = "" if raw is None else str(raw)
        stripped = text.strip()
        if stripped != text:
            self.ws_hits += 1
        if stripped.lower() in _MISSING_TOKENS:
            self.missing += 1
            return
        self.n += 1
        if not self.unique_overflow:
            self.uniques.add(stripped)
            if len(self.uniques) > _UNIQUE_CAP:
                self.unique_overflow = True
                self.uniques = set()
        if not self.count_overflow:
            self.counts[stripped] = self.counts.get(stripped, 0) + 1
            if len(self.counts) > _TOPVALUE_CAP:
                self.count_overflow = True
                self.counts = {}
        low = stripped.lower()
        if low in _BOOL_TRUE or low in _BOOL_FALSE:
            self.n_bool += 1
        num: float | None = None
        try:
            ival = int(stripped)
            self.n_int += 1
            num = float(ival)
        except ValueError:
            try:
                fval = float(stripped)
                if math.isfinite(fval):
                    self.n_float += 1
                    num = fval
            except ValueError:
                num = None
        if num is None and _DATE_RE.match(stripped):
            self.n_date += 1
        length = len(stripped)
        self.min_len = length if self.min_len is None else min(self.min_len, length)
        self.max_len = length if self.max_len is None else max(self.max_len, length)
        if num is not None:
            self.num_n += 1
            delta = num - self.num_mean
            self.num_mean += delta / self.num_n
            self.num_m2 += delta * (num - self.num_mean)
            self.num_min = num if self.num_min is None else min(self.num_min, num)
            self.num_max = num if self.num_max is None else max(self.num_max, num)
            self.seen += 1
            if len(self.sample) < _SAMPLE_CAP:
                self.sample.append(num)
            else:
                j = rng.randrange(self.seen)
                if j < _SAMPLE_CAP:
                    self.sample[j] = num
        if target_value is not None:
            if not self.target_overflow:
                bucket = self.target_map.setdefault(stripped, set())
                bucket.add(target_value)
                if len(self.target_map) > _PURITY_CARD_CAP:
                    self.target_overflow = True
                    self.target_map = {}
            if num is not None:
                vals = self.class_num.setdefault(target_value, [])
                if len(vals) < 4:
                    vals.extend([num, num])
                else:
                    vals[0] = min(vals[0], num)
                    vals[1] = max(vals[1], num)
                    vals[2] = vals[2] + 1
        return


def _quantiles(sample: list[float]) -> dict[str, float | None]:
    if not sample:
        return {"min": None, "q1": None, "median": None, "q3": None, "max": None}
    s = sorted(sample)

    def q(p: float) -> float:
        if len(s) == 1:
            return s[0]
        idx = p * (len(s) - 1)
        lo = math.floor(idx)
        hi = math.ceil(idx)
        if lo == hi:
            return s[int(idx)]
        return s[lo] + (s[hi] - s[lo]) * (idx - lo)

    return {"min": s[0], "q1": q(0.25), "median": q(0.5), "q3": q(0.75), "max": s[-1]}


def _infer_dtype(acc: _Acc, rows: int) -> str:
    if acc.n == 0:
        return "empty"
    n_unique = len(acc.uniques) if not acc.unique_overflow else _UNIQUE_CAP + 1
    if acc.n_bool == acc.n and n_unique <= 2:
        return "bool"
    if acc.n_int == acc.n:
        if n_unique == acc.n and rows and acc.n >= max(20, rows // 2):
            return "id"
        return "int"
    if acc.n_int + acc.n_float == acc.n:
        return "float"
    if acc.n_date == acc.n:
        return "datetime" if (acc.max_len or 0) > 10 else "date"
    if n_unique == acc.n and acc.n >= 20:
        return "id"
    if n_unique <= max(20, int(acc.n * 0.05)):
        return "categorical"
    return "text"


def _finish_column(acc: _Acc, rows: int) -> dict[str, Any]:
    total = acc.n + acc.missing
    n_unique = len(acc.uniques)
    top = sorted(acc.counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    qs = _quantiles(acc.sample)
    sd = None
    if acc.num_n > 1:
        sd = math.sqrt(acc.num_m2 / (acc.num_n - 1))
    col: dict[str, Any] = {
        "name": acc.name,
        "inferred_dtype": _infer_dtype(acc, rows),
        "n_missing": acc.missing,
        "pct_missing": round(100.0 * acc.missing / total, 4) if total else 0.0,
        "n_unique": None if acc.unique_overflow else n_unique,
        "n_unique_exact": not acc.unique_overflow,
        "top_values": [{"value": v, "count": c} for v, c in top],
        "top_values_exact": not acc.count_overflow,
        "min": acc.num_min,
        "q1": qs["q1"],
        "median": qs["median"],
        "q3": qs["q3"],
        "max": acc.num_max,
        "mean": acc.num_mean if acc.num_n else None,
        "sd": sd,
        "n_numeric": acc.num_n,
        "min_len": acc.min_len,
        "max_len": acc.max_len,
        "whitespace_padded_values": acc.ws_hits,
    }
    if acc.num_n and qs["q1"] is not None:
        col["quantile_source"] = (
            "exact" if acc.num_n <= _SAMPLE_CAP else f"reservoir sample of {_SAMPLE_CAP} values"
        )
    mixed = sum(1 for c in (acc.n_int, acc.n_float, acc.n_date) if c)
    if mixed > 1 or (acc.num_n and acc.num_n != acc.n):
        col["mixed_types"] = True
    return col


def _leakage_suspects(
    columns: list[dict[str, Any]],
    *,
    target: str,
    purity: dict[str, dict[str, Any]],
    rows: int,
) -> list[dict[str, Any]]:
    """Evidence, not verdicts. Every row says what was seen and what to check."""
    out: list[dict[str, Any]] = []
    date_cols = [
        c["name"] for c in columns if str(c.get("inferred_dtype")) in ("date", "datetime")
    ]
    for col in columns:
        name = str(col.get("name") or "")
        if name == target:
            continue
        if _LEAK_NAME_RE.search(name):
            out.append(
                {
                    "column": name,
                    "reason": "column name matches an outcome/label/prediction pattern",
                    "statistic": {"pattern": "outcome|label|target|y_true|y_pred|post_*|final_*|_leak"},
                    "what_to_check": (
                        "Is this column available at prediction time, or is it produced "
                        "by the outcome? Names are a hint only — confirm with the data owner."
                    ),
                }
            )
        info = purity.get(name) or {}
        pur = info.get("purity")
        if target and isinstance(pur, (int, float)) and pur >= 0.99 and rows >= 20:
            out.append(
                {
                    "column": name,
                    "reason": (
                        "each value of this column maps to a single target class in "
                        "almost every row"
                    ),
                    "statistic": {
                        "purity": round(float(pur), 6),
                        "distinct_values": info.get("distinct"),
                        "rows_considered": info.get("rows"),
                    },
                    "what_to_check": (
                        "Near-perfect separation can be a genuine strong predictor, an id "
                        "that encodes the label, or leakage. Check how the column is built."
                    ),
                }
            )
        sep = info.get("separation")
        if target and isinstance(sep, dict) and sep.get("disjoint"):
            out.append(
                {
                    "column": name,
                    "reason": "numeric ranges of this column do not overlap between target classes",
                    "statistic": sep,
                    "what_to_check": (
                        "A column whose range is disjoint per class usually encodes the "
                        "outcome. Confirm it exists before the outcome does."
                    ),
                }
            )
        if target and col.get("n_unique") and purity.get("_target_unique"):
            if col.get("n_unique") == purity.get("_target_unique") and col.get(
                "inferred_dtype"
            ) in ("categorical", "bool", "int"):
                out.append(
                    {
                        "column": name,
                        "reason": "distinct-value count equals the target's",
                        "statistic": {
                            "n_unique": col.get("n_unique"),
                            "target_n_unique": purity.get("_target_unique"),
                        },
                        "what_to_check": "Is this a re-encoding of the target?",
                    }
                )
    if len(date_cols) >= 2:
        out.append(
            {
                "column": ", ".join(date_cols[:6]),
                "reason": "more than one timestamp column",
                "statistic": {"timestamp_columns": date_cols[:6]},
                "what_to_check": (
                    "Check that no timestamp straddles the train/test split boundary or "
                    "post-dates the outcome — this tool cannot tell which one is the split."
                ),
            }
        )
    return out


def _sniff_encoding(path: Path, explicit: str) -> tuple[str, list[str]]:
    if explicit.strip():
        return explicit.strip(), []
    notes: list[str] = []
    head = b""
    with suppress(OSError), path.open("rb") as fh:
        head = fh.read(65536)
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", ["byte-order mark found — read as utf-8-sig"]
    try:
        head.decode("utf-8")
        return "utf-8", notes
    except UnicodeDecodeError:
        notes.append(
            "file is not valid utf-8 in the first 64 KB — read as latin-1; "
            "pass encoding= if you know the real codec."
        )
        return "latin-1", notes


def _sniff_delimiter(path: Path, encoding: str, explicit: str, fmt: str) -> tuple[str, list[str]]:
    if explicit:
        return explicit, []
    if fmt == "tsv":
        return "\t", []
    sample = ""
    with suppress(OSError, UnicodeDecodeError), path.open(
        "r", encoding=encoding, errors="replace", newline=""
    ) as fh:
        sample = fh.read(65536)
    if not sample:
        return ",", []
    with suppress(csv.Error):
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter, []
    first = sample.splitlines()[0] if sample.splitlines() else ""
    best = max((",", ";", "\t", "|"), key=first.count)
    if first.count(best) == 0:
        return ",", ["could not sniff a delimiter — assumed ','"]
    return best, [f"delimiter sniffing was ambiguous — assumed {best!r}"]


def _detect_format(path: Path, explicit: str) -> str:
    fmt = (explicit or "auto").strip().lower()
    if fmt and fmt != "auto":
        return fmt
    suffix = path.suffix.lower()
    return {
        ".csv": "csv",
        ".tsv": "tsv",
        ".tab": "tsv",
        ".txt": "csv",
        ".json": "json",
        ".jsonl": "jsonl",
        ".ndjson": "jsonl",
        ".parquet": "parquet",
        ".pq": "parquet",
        ".xlsx": "xlsx",
        ".xls": "xlsx",
    }.get(suffix, "csv")


def _iter_rows_stdlib(
    path: Path,
    fmt: str,
    encoding: str,
    delimiter: str,
    limit: int,
) -> tuple[list[str], Any, list[str]]:
    """(header, row-dict iterator, notes). Raises ValueError on unsupported fmt."""
    notes: list[str] = []
    if fmt in ("csv", "tsv"):
        fh = path.open("r", encoding=encoding, errors="replace", newline="")
        reader = csv.reader(fh, delimiter=delimiter)
        try:
            header = next(reader)
        except StopIteration:
            fh.close()
            return [], iter(()), ["file is empty"]
        header = [h.strip() or f"column_{i + 1}" for i, h in enumerate(header)]

        def gen() -> Any:
            try:
                for i, row in enumerate(reader):
                    if i >= limit:
                        break
                    yield row
            finally:
                fh.close()

        return header, gen(), notes
    if fmt == "jsonl":
        records: list[dict[str, Any]] = []
        with path.open("r", encoding=encoding, errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                with suppress(ValueError):
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        records.append(obj)
        return _records_to_rows(records, notes)
    if fmt == "json":
        size = path.stat().st_size if path.is_file() else 0
        if size > _JSON_BYTES_CAP:
            raise ValueError(
                f"json file is {size} bytes; the stdlib path loads it whole "
                f"(cap {_JSON_BYTES_CAP}). Convert to jsonl or use engine=project."
            )
        raw = json.loads(path.read_text(encoding=encoding, errors="replace"))
        if isinstance(raw, dict):
            for key in ("data", "records", "rows", "items"):
                if isinstance(raw.get(key), list):
                    raw = raw[key]
                    notes.append(f"read the '{key}' array from the top-level object")
                    break
        if not isinstance(raw, list):
            raise ValueError("json file is not an array of records")
        records = [r for r in raw[:limit] if isinstance(r, dict)]
        return _records_to_rows(records, notes)
    raise ValueError(f"the stdlib engine cannot read {fmt} files")


def _records_to_rows(records: list[dict[str, Any]], notes: list[str]) -> tuple[list[str], Any, list[str]]:
    header: list[str] = []
    for rec in records:
        for k in rec:
            if k not in header:
                header.append(str(k))
    rows = [[rec.get(h, "") for h in header] for rec in records]
    return header, iter(rows), notes


def _profile_stdlib(
    path: Path,
    *,
    fmt: str,
    delimiter: str,
    encoding: str,
    target: str,
    max_rows: int,
    sample_rows: int,
) -> dict[str, Any]:
    notes: list[str] = []
    enc, enc_notes = _sniff_encoding(path, encoding)
    notes.extend(enc_notes)
    delim = delimiter
    if fmt in ("csv", "tsv"):
        delim, d_notes = _sniff_delimiter(path, enc, delimiter, fmt)
        notes.extend(d_notes)
    limit = max_rows if sample_rows <= 0 else min(max_rows, sample_rows)
    header, rows, read_notes = _iter_rows_stdlib(path, fmt, enc, delim, limit + 1)
    notes.extend(read_notes)
    accs = [_Acc(h) for h in header]
    target_idx = header.index(target) if target and target in header else -1
    if target and target_idx < 0:
        notes.append(f"target column {target!r} is not in the header — class balance skipped")
    rng = random.Random(0)
    dup_hashes: set[str] = set()
    dup_rows = 0
    dup_exact = True
    target_counts: dict[str, int] = {}
    scanned = 0
    ragged = 0
    for row in rows:
        if scanned >= limit:
            break
        scanned += 1
        if len(row) != len(header):
            ragged += 1
        tval: str | None = None
        if 0 <= target_idx < len(row):
            tval = str(row[target_idx]).strip()
            if tval.lower() in _MISSING_TOKENS:
                tval = None
            else:
                target_counts[tval] = target_counts.get(tval, 0) + 1
        for i, acc in enumerate(accs):
            acc.add(row[i] if i < len(row) else "", rng, tval)
        if dup_exact:
            key = hashlib.sha1(
                "\x1f".join(str(v) for v in row).encode("utf-8", "replace")
            ).hexdigest()
            if key in dup_hashes:
                dup_rows += 1
            else:
                dup_hashes.add(key)
                if len(dup_hashes) > _DUP_HASH_CAP:
                    dup_exact = False
                    dup_hashes = set()
                    notes.append(
                        f"duplicate-row detection stopped after {_DUP_HASH_CAP} distinct "
                        "rows — the count below is a lower bound."
                    )
    truncated = scanned >= limit
    if ragged:
        notes.append(f"{ragged} rows had a different column count than the header")
    columns = [_finish_column(a, scanned) for a in accs]
    purity: dict[str, Any] = {}
    if target_idx >= 0:
        purity["_target_unique"] = len(target_counts)
        for acc in accs:
            if acc.name == target:
                continue
            entry: dict[str, Any] = {}
            if not acc.target_overflow and acc.target_map:
                pure = sum(1 for vs in acc.target_map.values() if len(vs) == 1)
                entry["purity"] = pure / len(acc.target_map) if acc.target_map else 0.0
                entry["distinct"] = len(acc.target_map)
                entry["rows"] = scanned
            if len(acc.class_num) >= 2 and len(acc.class_num) <= 10:
                ranges = {k: [v[0], v[1]] for k, v in acc.class_num.items()}
                ordered = sorted(ranges.items(), key=lambda kv: kv[1][0])
                disjoint = all(
                    ordered[i][1][1] < ordered[i + 1][1][0] for i in range(len(ordered) - 1)
                )
                entry["separation"] = {"disjoint": bool(disjoint), "ranges_by_class": ranges}
            if entry:
                purity[acc.name] = entry
    samples = {a.name: list(a.sample) for a in accs if a.sample}
    return {
        "rows_scanned": scanned,
        "truncated": truncated,
        "encoding": enc,
        "delimiter": delim if fmt in ("csv", "tsv") else "",
        "columns": columns,
        "duplicate_rows": dup_rows,
        "duplicate_rows_exact": dup_exact,
        "class_balance": target_counts,
        "purity": purity,
        "samples": samples,
        "notes": notes,
    }


# --------------------------------------------------------------------------
# tabular profiling — project (pandas) engine
# --------------------------------------------------------------------------

# importlib.import_module, not a literal import: this string is source for the
# PROJECT interpreter, and src/remedy must never contain `import pandas`.
_PROJECT_PROFILE_SCRIPT = '''\
"""Generated by Remedy analysis tools. Runs in the PROJECT env, prints JSON."""
import json
import sys
from importlib import import_module

MARKER = "REMEDY_PROFILE_JSON "
SAMPLE_CAP = 2000


def load(pd, args):
    path, fmt = args["path"], args["format"]
    nrows = int(args["max_rows"]) + 1
    if fmt in ("csv", "tsv"):
        kw = {"nrows": nrows}
        if args.get("delimiter"):
            kw["sep"] = args["delimiter"]
        elif fmt == "tsv":
            kw["sep"] = "\\t"
        else:
            kw["sep"] = None
            kw["engine"] = "python"
        if args.get("encoding"):
            kw["encoding"] = args["encoding"]
        return pd.read_csv(path, **kw)
    if fmt == "jsonl":
        return pd.read_json(path, lines=True, nrows=nrows)
    if fmt == "json":
        return pd.read_json(path)
    if fmt == "parquet":
        return pd.read_parquet(path)
    if fmt == "xlsx":
        sheet = args.get("sheet") or 0
        return pd.read_excel(path, sheet_name=sheet, nrows=nrows)
    raise ValueError("unsupported format: " + str(fmt))


def dtype_name(pd, s):
    if pd.api.types.is_bool_dtype(s):
        return "bool"
    if pd.api.types.is_integer_dtype(s):
        return "int"
    if pd.api.types.is_float_dtype(s):
        return "float"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "datetime"
    nu = int(s.nunique(dropna=True))
    n = int(s.count())
    if n and nu == n and n >= 20:
        return "id"
    if nu <= max(20, int(n * 0.05)):
        return "categorical"
    return "text"


def main():
    args = json.loads(sys.argv[1])
    pd = import_module("pandas")
    df = load(pd, args)
    max_rows = int(args["max_rows"])
    truncated = len(df) > max_rows
    if truncated:
        df = df.iloc[:max_rows]
    target = args.get("target") or ""
    cols = []
    samples = {}
    for name in df.columns:
        s = df[name]
        numeric = pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)
        col = {
            "name": str(name),
            "inferred_dtype": dtype_name(pd, s),
            "n_missing": int(s.isna().sum()),
            "pct_missing": round(100.0 * float(s.isna().sum()) / max(1, len(df)), 4),
            "n_unique": int(s.nunique(dropna=True)),
            "n_unique_exact": True,
            "top_values_exact": True,
        }
        vc = s.value_counts(dropna=True).head(5)
        col["top_values"] = [
            {"value": str(k), "count": int(v)} for k, v in vc.items()
        ]
        if numeric:
            clean = s.dropna().astype("float64")
            col["n_numeric"] = int(clean.shape[0])
            if clean.shape[0]:
                q = clean.quantile([0.25, 0.5, 0.75])
                col["min"] = float(clean.min())
                col["q1"] = float(q.loc[0.25])
                col["median"] = float(q.loc[0.5])
                col["q3"] = float(q.loc[0.75])
                col["max"] = float(clean.max())
                col["mean"] = float(clean.mean())
                col["sd"] = float(clean.std(ddof=1)) if clean.shape[0] > 1 else None
                take = clean if clean.shape[0] <= SAMPLE_CAP else clean.sample(
                    SAMPLE_CAP, random_state=0
                )
                samples[str(name)] = [float(x) for x in take.tolist()]
        else:
            lens = s.dropna().astype(str).str.len()
            col["n_numeric"] = 0
            if lens.shape[0]:
                col["min_len"] = int(lens.min())
                col["max_len"] = int(lens.max())
        cols.append(col)
    purity = {}
    if target and target in df.columns:
        purity["_target_unique"] = int(df[target].nunique(dropna=True))
        for name in df.columns:
            if str(name) == target:
                continue
            s = df[name]
            entry = {}
            try:
                if int(s.nunique(dropna=True)) <= 200:
                    g = df.groupby(s, dropna=True)[target].nunique()
                    if len(g):
                        entry["purity"] = float((g <= 1).sum()) / float(len(g))
                        entry["distinct"] = int(len(g))
                        entry["rows"] = int(len(df))
                if pd.api.types.is_numeric_dtype(s) and int(
                    df[target].nunique(dropna=True)
                ) <= 10:
                    grp = df.groupby(target)[name].agg(["min", "max"]).dropna()
                    ranges = {
                        str(k): [float(r["min"]), float(r["max"])]
                        for k, r in grp.iterrows()
                    }
                    ordered = sorted(ranges.items(), key=lambda kv: kv[1][0])
                    disjoint = all(
                        ordered[i][1][1] < ordered[i + 1][1][0]
                        for i in range(len(ordered) - 1)
                    )
                    entry["separation"] = {
                        "disjoint": bool(disjoint),
                        "ranges_by_class": ranges,
                    }
            except Exception as exc:
                entry["error"] = str(exc)
            if entry:
                purity[str(name)] = entry
    balance = {}
    if target and target in df.columns:
        balance = {
            str(k): int(v) for k, v in df[target].value_counts(dropna=True).items()
        }
    out = {
        "rows_scanned": int(len(df)),
        "truncated": bool(truncated),
        "encoding": args.get("encoding") or "",
        "delimiter": args.get("delimiter") or "",
        "columns": cols,
        "duplicate_rows": int(df.duplicated().sum()),
        "duplicate_rows_exact": True,
        "class_balance": balance,
        "purity": purity,
        "samples": samples,
        "notes": ["profiled with the project's pandas " + str(pd.__version__)],
    }
    print(MARKER + json.dumps(out, default=str))


main()
'''


def _assemble_profile(
    path: Path,
    core: dict[str, Any],
    *,
    engine_used: str,
    target: str,
    sampled: bool,
) -> dict[str, Any]:
    """Shared post-processing for both engines (one honesty surface)."""
    columns = list(core.get("columns") or [])
    rows = int(core.get("rows_scanned") or 0)
    notes = list(core.get("notes") or [])
    constant = [c["name"] for c in columns if c.get("n_unique") == 1]
    id_like = [
        c["name"]
        for c in columns
        if c.get("n_unique") is not None and rows and c["n_unique"] == rows
    ]
    high_card = [
        c["name"]
        for c in columns
        if c.get("n_unique") is not None
        and rows
        and c["name"] not in id_like
        and c["n_unique"] > max(50, rows * 0.5)
    ]
    all_missing = [c["name"] for c in columns if rows and c.get("n_missing") == rows]
    mixed = [c["name"] for c in columns if c.get("mixed_types")]
    whitespace = [c["name"] for c in columns if int(c.get("whitespace_padded_values") or 0) > 0]
    balance = dict(core.get("class_balance") or {})
    class_balance: dict[str, Any] = {}
    if balance:
        total = sum(balance.values())
        ordered = sorted(balance.items(), key=lambda kv: -kv[1])
        minority = ordered[-1]
        class_balance = {
            "target": target,
            "counts": balance,
            "proportions": {k: round(v / total, 6) for k, v in balance.items()} if total else {},
            "n_classes": len(balance),
            "minority_class": minority[0],
            "minority_count": minority[1],
            "imbalance_ratio": round(ordered[0][1] / minority[1], 4) if minority[1] else None,
        }
    warnings: list[str] = []
    if constant:
        warnings.append(f"constant columns (no information): {', '.join(constant[:10])}")
    if all_missing:
        warnings.append(f"columns that are entirely missing: {', '.join(all_missing[:10])}")
    if mixed:
        warnings.append(
            "columns holding more than one value type: " + ", ".join(mixed[:10])
        )
    if core.get("duplicate_rows"):
        exact = "" if core.get("duplicate_rows_exact", True) else " (lower bound)"
        warnings.append(f"{core['duplicate_rows']} duplicate rows{exact}")
    if class_balance and class_balance.get("imbalance_ratio", 0) and class_balance[
        "imbalance_ratio"
    ] >= 10:
        warnings.append(
            f"target {target!r} is imbalanced "
            f"({class_balance['imbalance_ratio']}:1, minority n="
            f"{class_balance['minority_count']})"
        )
    suspects = _leakage_suspects(
        columns,
        target=target,
        purity=dict(core.get("purity") or {}),
        rows=rows,
    )
    if engine_used == "stdlib":
        notes.append(
            "stdlib engine: every value was read as text, so dtypes are INFERRED from "
            "the characters (a column of '01','02' reads as int, a pandas category or "
            "a real datetime dtype is invisible), quantiles above "
            f"{_SAMPLE_CAP} numeric values come from a seeded reservoir sample, and "
            "parquet/xlsx cannot be read at all. Run with engine=project for the "
            "project's own dtypes."
        )
    return {
        "path": str(path),
        "engine_used": engine_used,
        "sampled": bool(sampled),
        "rows_scanned": rows,
        "n_columns": len(columns),
        "truncated": bool(core.get("truncated")),
        "encoding": core.get("encoding") or "",
        "delimiter": core.get("delimiter") or "",
        "columns": columns,
        "duplicate_rows": core.get("duplicate_rows"),
        "duplicate_rows_exact": core.get("duplicate_rows_exact", True),
        "duplicate_key_candidates": id_like,
        "constant_columns": constant,
        "id_like_columns": id_like,
        "high_cardinality_columns": high_card,
        "all_missing_columns": all_missing,
        "mixed_type_columns": mixed,
        "whitespace_padded_columns": whitespace,
        "class_balance": class_balance,
        "warnings": warnings,
        "leakage_suspects": suspects,
        "leakage_disclaimer": (
            "These are SUSPECTS with their evidence, not findings. This tool cannot "
            "know when each column becomes available; a human has to confirm."
        ),
        "notes": notes,
    }


# --------------------------------------------------------------------------
# two-sample Kolmogorov-Smirnov (pure stdlib)
# --------------------------------------------------------------------------


def ks_two_sample(a: list[float], b: list[float]) -> dict[str, Any]:
    """D and its asymptotic p-value. Exact D, approximate p — stated as such."""
    if len(a) < 2 or len(b) < 2:
        return {
            "d": None,
            "p_asymptotic": None,
            "n1": len(a),
            "n2": len(b),
            "method": "not computed",
            "accuracy": "needs at least 2 values per sample",
        }
    x = sorted(a)
    y = sorted(b)
    n1, n2 = len(x), len(y)
    i = j = 0
    d = 0.0
    # Step over each distinct value completely before comparing the ECDFs —
    # advancing one side at a time invents a gap wherever the samples tie.
    while i < n1 and j < n2:
        value = min(x[i], y[j])
        while i < n1 and x[i] == value:
            i += 1
        while j < n2 and y[j] == value:
            j += 1
        d = max(d, abs(i / n1 - j / n2))
    ne = math.sqrt(n1 * n2 / (n1 + n2))
    lam = (ne + 0.12 + 0.11 / ne) * d
    # Q(lam) = 2 * sum_{k=1..} (-1)^(k-1) exp(-2 k^2 lam^2), Q(0) = 1.
    # The alternating series stops converging for tiny lam; fall back to 1.0
    # there rather than returning the truncation artefact.
    p = 1.0
    if lam >= 0.05:
        total = 0.0
        converged = False
        for k in range(1, 201):
            term = ((-1) ** (k - 1)) * math.exp(-2.0 * k * k * lam * lam)
            total += term
            if abs(term) < 1e-12:
                converged = True
                break
        p = max(0.0, min(1.0, 2.0 * total)) if converged else 1.0
    small = min(n1, n2) < 35
    return {
        "d": d,
        "p_asymptotic": p,
        "n1": n1,
        "n2": n2,
        "method": "two-sample Kolmogorov-Smirnov, asymptotic Kolmogorov distribution",
        "accuracy": (
            "D is exact for the values compared; the p-value is the large-sample "
            "approximation" + (
                f" and is UNRELIABLE here (min n = {min(n1, n2)} < 35 — use an exact "
                "test in the project env)"
                if small
                else ""
            )
        ),
        "samples_are_reservoirs": True,
    }


# --------------------------------------------------------------------------
# engine resolution for analysis_run
# --------------------------------------------------------------------------


def _params_flags(engine: str, params: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    for k, v in params.items():
        val = v if isinstance(v, str) else json.dumps(v)
        if engine == "papermill":
            flags += ["-p", str(k), val]
        elif engine == "quarto":
            flags += ["-P", f"{k}:{val}"]
    return flags


def _artifact_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".pdf"):
        return "figure" if suffix != ".pdf" else "document"
    if suffix in (".csv", ".tsv", ".parquet", ".json", ".jsonl"):
        return "table"
    if suffix in (".html", ".tex", ".md", ".docx", ".ipynb"):
        return "document"
    return "file"


def _match_globs(rel: str, patterns: list[str]) -> bool:
    name = rel.rsplit("/", 1)[-1]
    for pat in patterns:
        if fnmatch.fnmatch(rel, pat):
            return True
        if "/" not in pat and fnmatch.fnmatch(name, pat):
            return True
    return False


def _scan_tree(root: Path, patterns: list[str]) -> dict[str, tuple[float, int]]:
    """rel-posix → (mtime, size) for files matching *patterns* under *root*."""
    out: dict[str, tuple[float, int]] = {}
    if not root.is_dir():
        return out
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".remedy-")]
        for fname in filenames:
            count += 1
            if count > _SCAN_FILE_CAP:
                return out
            fpath = Path(dirpath) / fname
            try:
                rel = fpath.relative_to(root).as_posix()
            except ValueError:
                continue
            if not _match_globs(rel, patterns):
                continue
            with suppress(OSError):
                st = fpath.stat()
                out[rel] = (st.st_mtime, st.st_size)
    return out


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------


def register_analysis_tools(runtime: Any) -> None:
    """Register analysis_env, analysis_run, analysis_ledger, data_profile, data_diff."""

    def _root_for(path: str) -> Path:
        raw = (path or "").strip()
        if not raw:
            return _project_root(runtime)
        p = _resolve_read(runtime, raw)
        return p.parent if p.is_file() else p

    async def _probe_python_modules(root: Path, py: list[str]) -> dict[str, Any]:
        if not py:
            return {}
        argv = [*py, "-c", _PY_PROBE_SCRIPT, json.dumps(list(_PY_MODULES))]
        with suppress(Exception):
            res = await _sandbox_run(runtime, argv, cwd=root, timeout=60.0)
            if getattr(res, "exit_code", 1) == 0:
                return _marker_json(getattr(res, "stdout", "") or "", _ENV_MARKER)
        return {}

    # ---------------------------------------------------------------- env

    async def analysis_env(
        path: str = "",
        probe: bool = True,
        timeout_seconds: float = 60.0,
    ) -> str:
        """What can actually run in this project (interpreters, runners, packages)."""
        from remedy.core.project_fingerprint import fingerprint_path

        root = _root_for(path)
        if not root.is_dir():
            return format_tool_error(
                f"not a directory: {root}",
                code="BAD_PATH",
                tool_name="analysis_env",
                suggestion="Pass path= to the project folder.",
            )
        timeout = _clamp(timeout_seconds, 10.0, 180.0, 60.0)
        fp = await asyncio.to_thread(fingerprint_path, root)
        py, py_source, notes = project_python(root)
        runners: dict[str, dict[str, Any]] = {
            "python": {
                "found": bool(py),
                "path": " ".join(py),
                "version": "",
                "source": py_source,
            }
        }
        found_bins: list[tuple[str, str, tuple[str, ...]]] = []
        for name, candidates, version_args in _BINARY_RUNNERS:
            hit = ""
            for cand in candidates:
                hit = _which(cand, root)
                if hit:
                    break
            runners[name] = {"found": bool(hit), "path": hit, "version": "", "source": "PATH"}
            if hit:
                found_bins.append((name, hit, version_args))

        if probe:
            sem = asyncio.Semaphore(4)

            async def version_of(name: str, exe: str, args: tuple[str, ...]) -> None:
                async with sem:
                    with suppress(Exception):
                        res = await _sandbox_run(
                            runtime, [exe, *args], cwd=root, timeout=min(20.0, timeout)
                        )
                        text = (getattr(res, "stdout", "") or "") + (
                            getattr(res, "stderr", "") or ""
                        )
                        runners[name]["version"] = _first_version_line(text)

            await asyncio.gather(*(version_of(n, e, a) for n, e, a in found_bins))
            mods = await _probe_python_modules(root, py)
            if mods:
                runners["python"]["version"] = str(mods.get("python") or "")
                if mods.get("executable"):
                    runners["python"]["resolved_executable"] = str(mods["executable"])
                for mod, ok in (mods.get("modules") or {}).items():
                    runners[f"py:{mod}"] = {
                        "found": bool(ok),
                        "path": "",
                        "version": "",
                        "source": "project python importable",
                    }
            elif py:
                notes.append(
                    "the project interpreter did not answer the module probe — "
                    "pandas/pyarrow availability is UNKNOWN, not absent."
                )
            if runners.get("Rscript", {}).get("found"):
                pkgs = ", ".join(f'"{p}"' for p in _R_PACKAGES)
                script = _R_PROBE_SCRIPT % (pkgs, _ENV_MARKER)
                with suppress(Exception):
                    res = await _sandbox_run(
                        runtime,
                        [runners["Rscript"]["path"], "--vanilla", "-e", script],
                        cwd=root,
                        timeout=min(60.0, timeout),
                    )
                    got = _marker_json(getattr(res, "stdout", "") or "", _ENV_MARKER)
                    for pkg, ok in got.items():
                        runners[f"r:{pkg}"] = {
                            "found": bool(ok),
                            "path": "",
                            "version": "",
                            "source": "Rscript requireNamespace",
                        }
        else:
            notes.append("probe=false — paths only, no versions and no package probes")

        missing = [k for k, v in runners.items() if not v.get("found")]
        if not runners.get("papermill", {}).get("found") and not runners.get(
            "py:papermill", {}
        ).get("found"):
            if not runners.get("py:nbconvert", {}).get("found"):
                notes.append(
                    "no notebook runner: install one in the PROJECT env "
                    "(uv add --dev papermill, or python -m pip install papermill). "
                    "Remedy will not install it for you."
                )
        return _dump(
            {
                "path": str(root),
                "stacks": list(fp.stacks),
                "markers": list(fp.markers),
                "runners": runners,
                "missing": missing,
                "suggest_verify": fp.suggest_verify or "",
                "probe": bool(probe),
                "notes": notes,
            }
        )

    # ---------------------------------------------------------------- run

    async def analysis_run(
        path: str = "",
        engine: str = "auto",
        args: str = "",
        params_json: str = "",
        workdir: str = "",
        env_json: str = "",
        artifacts_dir: str = "",
        artifacts_glob: str = "",
        record: bool = True,
        tag: str = "",
        description: str = "",
        timeout_seconds: float = 900.0,
    ) -> str:
        """Run ONE analysis file headlessly in the project's own environment."""
        _ = description
        tool = "analysis_run"
        if not (path or "").strip():
            return format_tool_error(
                "path is required",
                code="NO_PATH",
                tool_name=tool,
                suggestion="Pass path= to a .py/.R/.jl/.ipynb/.qmd/.Rmd file.",
            )
        script = _resolve_read(runtime, path)
        if not script.is_file():
            return format_tool_error(
                f"not a file: {script}",
                code="NOT_FOUND",
                tool_name=tool,
                suggestion="Check the path with list_dir on its parent.",
            )
        suffix = script.suffix.lower()
        if suffix in (".sh", ".ps1", ".bat", ".cmd"):
            return format_tool_error(
                f"{suffix} scripts are shell work, not an analysis run",
                code="USE_BASH_EXEC",
                tool_name=tool,
                suggestion="Run shell scripts with bash_exec — it owns the shell path.",
            )
        project = _project_root(runtime)
        default_wd = script.parent if script.parent.is_dir() else project
        cwd = _resolve_write(runtime, workdir, tool, default=default_wd)
        if isinstance(cwd, str):
            return cwd
        if not cwd.is_dir():
            return format_tool_error(
                f"workdir is not a directory: {cwd}",
                code="BAD_WORKDIR",
                tool_name=tool,
                suggestion="Pass workdir= to an existing folder inside the project.",
            )
        params, perr = _parse_json_obj(params_json)
        if perr:
            return format_tool_error(
                f"params_json {perr}",
                code="BAD_PARAMS",
                tool_name=tool,
                suggestion='Pass params_json=\'{"alpha": 0.05}\'.',
            )
        env_over, eerr = _parse_json_obj(env_json)
        if eerr:
            return format_tool_error(
                f"env_json {eerr}",
                code="BAD_ENV",
                tool_name=tool,
                suggestion='Pass env_json=\'{"OMP_NUM_THREADS": "1"}\'.',
            )
        try:
            extra_args = shlex.split(args or "", posix=os.name != "nt")
        except ValueError as exc:
            return format_tool_error(
                f"bad args: {exc}",
                code="BAD_ARGS",
                tool_name=tool,
                suggestion="Quote args like a shell command line.",
            )
        extra_args = [a.strip('"') for a in extra_args]

        timeout = _clamp(timeout_seconds, 10.0, 1800.0, 900.0)
        warnings: list[str] = []
        py, py_source, py_notes = project_python(cwd)
        warnings.extend(py_notes)
        want = (engine or "auto").strip().lower() or "auto"
        params_file = ""
        argv: list[str] = []
        resolved_engine = ""
        out_notebook = ""

        def missing_runner(name: str, install: str) -> str:
            return format_tool_error(
                f"{name} was not found for this project",
                code="NO_RUNNER",
                tool_name=tool,
                suggestion=(
                    f"Install it in the PROJECT environment yourself: {install}. "
                    "Remedy does not install packages on your behalf. "
                    "Run analysis_env to see what is available."
                ),
            )

        if want in ("auto", "papermill", "nbconvert") and suffix == ".ipynb":
            pm_bin = _which("papermill", cwd)
            has_pm_mod = False
            has_nb_mod = False
            if py:
                mods = (await _probe_python_modules(cwd, py)).get("modules") or {}
                has_pm_mod = bool(mods.get("papermill"))
                has_nb_mod = bool(mods.get("nbconvert"))
            out_nb = cwd / f"{script.stem}.executed.ipynb"
            out_notebook = str(out_nb)
            if want in ("auto", "papermill") and (pm_bin or has_pm_mod):
                head = [pm_bin] if pm_bin else [*py, "-m", "papermill"]
                argv = [*head, str(script), str(out_nb), *_params_flags("papermill", params)]
                resolved_engine = "papermill"
            elif want in ("auto", "nbconvert") and has_nb_mod and py:
                argv = [
                    *py,
                    "-m",
                    "nbconvert",
                    "--to",
                    "notebook",
                    "--execute",
                    f"--ExecutePreprocessor.timeout={int(timeout)}",
                    "--output",
                    out_nb.name,
                    "--output-dir",
                    str(out_nb.parent),
                    str(script),
                ]
                resolved_engine = "nbconvert"
                if params:
                    warnings.append(
                        "nbconvert cannot inject parameters — params were written to "
                        "the params file instead; papermill is the parameterised runner."
                    )
            else:
                return missing_runner(
                    "a notebook runner (papermill or nbconvert)",
                    "uv add --dev papermill   (or: python -m pip install papermill)",
                )
        elif want in ("auto", "python") and suffix == ".py":
            if not py:
                return missing_runner("a Python interpreter", "create a project .venv")
            argv = [*py, str(script), *extra_args]
            resolved_engine = "python"
        elif want in ("auto", "rscript") and suffix == ".r":
            rbin = _which("Rscript", cwd)
            if not rbin:
                return missing_runner("Rscript", "install R, or add Rscript to PATH")
            argv = [rbin, "--vanilla", str(script), *extra_args]
            resolved_engine = "rscript"
        elif want in ("auto", "julia") and suffix == ".jl":
            jbin = _which("julia", cwd)
            if not jbin:
                return missing_runner("julia", "install Julia, or add julia to PATH")
            proj = cwd if (cwd / "Project.toml").is_file() else project
            argv = [jbin, f"--project={proj}", str(script), *extra_args]
            resolved_engine = "julia"
        elif want in ("auto", "quarto", "rmarkdown") and suffix in (".qmd", ".rmd"):
            qbin = _which("quarto", cwd)
            if qbin and want != "rmarkdown":
                argv = [qbin, "render", str(script), "--execute", *_params_flags("quarto", params)]
                resolved_engine = "quarto"
            elif suffix == ".rmd":
                rbin = _which("Rscript", cwd)
                if not rbin:
                    return missing_runner(
                        "quarto or Rscript", "install Quarto, or install R with rmarkdown"
                    )
                escaped = str(script).replace("\\", "/")
                argv = [rbin, "--vanilla", "-e", f'rmarkdown::render("{escaped}")']
                resolved_engine = "rmarkdown"
            else:
                return missing_runner("quarto", "install Quarto (quarto.org)")
        else:
            return format_tool_error(
                f"no engine for {suffix or 'this file'} (engine={want})",
                code="UNSUPPORTED_ENGINE",
                tool_name=tool,
                suggestion=(
                    "Supported: .ipynb (papermill/nbconvert), .py, .R, .jl, .qmd, .Rmd. "
                    "Shell scripts go through bash_exec."
                ),
            )

        if params and resolved_engine in ("python", "rscript", "julia", "nbconvert"):
            candidate = cwd / "params.json"
            if candidate.exists():
                candidate = cwd / "remedy_params.json"
            resolved = _resolve_write(runtime, str(candidate), tool, default=candidate)
            if isinstance(resolved, str):
                return resolved
            with suppress(OSError):
                from remedy.core.atomic_json import write_json_atomic

                write_json_atomic(resolved, params, default=str)
                params_file = str(resolved)
                _note(runtime, resolved)
            if params_file:
                warnings.append(
                    f"parameters were written to {params_file} and the path is in "
                    "REMEDY_PARAMS_FILE — the script must read it; Remedy does not "
                    "rewrite your code."
                )

        command = _argv_text(argv)
        blocked = _approval_block(runtime, tool, command)
        if blocked:
            return blocked

        patterns = _split_list(artifacts_glob or _DEFAULT_ARTIFACT_GLOB)
        art_root: Path = cwd
        if (artifacts_dir or "").strip():
            resolved_art = _resolve_write(runtime, artifacts_dir, tool, default=cwd)
            if isinstance(resolved_art, str):
                return resolved_art
            art_root = resolved_art
        before = await asyncio.to_thread(_scan_tree, art_root, patterns)

        inputs: list[dict[str, Any]] = []
        input_candidates = [script]
        if params_file:
            input_candidates.append(Path(params_file))
        for candidate in input_candidates:
            with suppress(OSError):
                if candidate.is_file():
                    inputs.append(
                        {
                            "path": str(candidate),
                            "sha256": _sha256_file(candidate),
                            "bytes": candidate.stat().st_size,
                        }
                    )
        for a in extra_args:
            cand = Path(a)
            if not cand.is_absolute():
                cand = cwd / a
            with suppress(OSError):
                if cand.is_file():
                    inputs.append(
                        {
                            "path": str(cand),
                            "sha256": _sha256_file(cand),
                            "bytes": cand.stat().st_size,
                        }
                    )

        run_key = hashlib.sha1(
            ("\x1f".join(argv) + "\x1f" + "\x1f".join(i["sha256"] for i in inputs)).encode(
                "utf-8", "replace"
            )
        ).hexdigest()[:8]
        run_id = f"{_stamp()}-{run_key}"
        started_utc = _utc_now()
        started = time.monotonic()
        env_extra = dict(env_over)
        if params_file:
            env_extra["REMEDY_PARAMS_FILE"] = params_file
        env_extra["REMEDY_RUN_ID"] = run_id
        result = await _sandbox_run(
            runtime, argv, cwd=cwd, timeout=timeout, env_extra=env_extra
        )
        duration = time.monotonic() - started
        exit_code = _int(getattr(result, "exit_code", -1), -1)
        stdout = getattr(result, "stdout", "") or ""
        stderr = getattr(result, "stderr", "") or ""

        after = await asyncio.to_thread(_scan_tree, art_root, patterns)
        changed = [
            rel for rel, meta in after.items() if before.get(rel) != meta
        ]
        changed.sort()
        allowed_roots = _allowed_write_roots(runtime)

        def _inside_roots(p: Path) -> bool:
            if not allowed_roots:
                return True
            with suppress(OSError):
                rp = p.resolve(strict=False)
                for root in allowed_roots:
                    with suppress(ValueError):
                        if rp == root or rp.is_relative_to(root):
                            return True
            return False

        runs_dir = runs_dir_for_project(project)
        run_dir = runs_dir / run_id
        artifacts: list[dict[str, Any]] = []
        copied_bytes = 0
        if record:
            with suppress(OSError):
                (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        for rel in changed[:_ARTIFACT_FILE_CAP]:
            fpath = art_root / rel
            mtime, size = after[rel]
            entry: dict[str, Any] = {
                "path": str(fpath),
                "rel": rel,
                "bytes": size,
                "sha256": _sha256_file(fpath),
                "mtime": datetime.fromtimestamp(mtime, UTC).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "kind": _artifact_kind(fpath),
                "copied": False,
            }
            inside = _inside_roots(fpath)
            entry["inside_write_roots"] = inside
            if record and inside and copied_bytes + size <= _ARTIFACT_BYTES_CAP:
                dest = run_dir / "artifacts" / rel
                with suppress(OSError):
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(fpath, dest)
                    entry["copied"] = True
                    entry["copy_path"] = str(dest)
                    copied_bytes += size
            elif not inside:
                entry["note"] = "outside the write roots — reported as a path, not copied"
            artifacts.append(entry)
            _note(runtime, fpath)
        if len(changed) > _ARTIFACT_FILE_CAP:
            warnings.append(
                f"{len(changed)} files changed; only the first {_ARTIFACT_FILE_CAP} are listed"
            )
        if copied_bytes >= _ARTIFACT_BYTES_CAP:
            warnings.append(
                f"artifact copies stopped at the {_ARTIFACT_BYTES_CAP} byte cap — "
                "the rest are listed by path only"
            )

        payload: dict[str, Any] = {
            "run_id": run_id,
            "engine": resolved_engine,
            "interpreter_source": py_source if resolved_engine in ("python", "papermill", "nbconvert") else resolved_engine,
            "argv": argv,
            "command": command,
            "cwd": str(cwd),
            "exit_code": exit_code,
            "ok": exit_code == 0,
            "duration_s": round(duration, 3),
            "started_utc": started_utc,
            "timeout_s": timeout,
            "stdout_tail": _tail(stdout),
            "stderr_tail": _tail(stderr),
            "artifacts": artifacts,
            "artifacts_root": str(art_root),
            "artifacts_glob": patterns,
            "inputs": inputs,
            "params": params,
            "params_file": params_file,
            "output_notebook": out_notebook,
            "env_summary": {
                "overrides": sorted(env_over.keys()),
                "REMEDY_RUN_ID": run_id,
                "REMEDY_PARAMS_FILE": params_file,
                "path_prepends": "project .venv/Scripts, node_modules/.bin, repo tools",
            },
            "tag": str(tag or ""),
            "recorded": bool(record),
            "warnings": warnings,
        }
        if record:
            with suppress(OSError):
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "stdout.txt").write_text(stdout, encoding="utf-8", errors="replace")
                (run_dir / "stderr.txt").write_text(stderr, encoding="utf-8", errors="replace")
                payload["stdout_path"] = str(run_dir / "stdout.txt")
                payload["stderr_path"] = str(run_dir / "stderr.txt")
            with suppress(OSError, TypeError, ValueError):
                from remedy.core.atomic_json import write_json_atomic

                write_json_atomic(run_dir / "run.json", payload, default=str)
                payload["run_record_path"] = str(run_dir / "run.json")
            summary = {
                "run_id": run_id,
                "started_utc": started_utc,
                "engine": resolved_engine,
                "argv": argv,
                "cwd": str(cwd),
                "interpreter": " ".join(py) if py else resolved_engine,
                "interpreter_source": py_source,
                "exit_code": exit_code,
                "ok": exit_code == 0,
                "duration_s": round(duration, 3),
                "inputs": inputs,
                "params": params,
                "artifacts": [a["path"] for a in artifacts],
                "tag": str(tag or ""),
                "stdout_tail": _tail(stdout, 800),
                "run_dir": str(run_dir),
            }
            with suppress(OSError):
                payload["ledger_path"] = str(_append_ledger(runs_dir, summary))
        else:
            payload["ledger_path"] = ""
            warnings.append("record=false — this run is NOT in the ledger and is not traceable")
        return _dump(payload)

    # ------------------------------------------------------------- ledger

    async def analysis_ledger(
        action: str = "list",
        run_id: str = "",
        path: str = "",
        query: str = "",
        limit: int = 20,
        days: int = 0,
    ) -> str:
        """List / show / verify / diff / prune recorded analysis runs."""
        tool = "analysis_ledger"
        project = _root_for(path) if (path or "").strip() else _project_root(runtime)
        runs_dir = runs_dir_for_project(project)
        rows = _read_ledger(runs_dir)
        act = (action or "list").strip().lower()
        lim = max(1, min(500, _int(limit, 20) or 20))

        def find(rid: str) -> dict[str, Any] | None:
            for rec in reversed(rows):
                if str(rec.get("run_id")) == rid:
                    return rec
            return None

        def full_record(rid: str) -> dict[str, Any] | None:
            rec_path = runs_dir / rid / "run.json"
            if rec_path.is_file():
                with suppress(OSError, ValueError):
                    obj = json.loads(rec_path.read_text(encoding="utf-8", errors="replace"))
                    if isinstance(obj, dict):
                        return obj
            return find(rid)

        if act == "list":
            picked = list(reversed(rows))
            q = (query or "").strip().lower()
            if q:
                picked = [
                    r
                    for r in picked
                    if q in json.dumps(r, default=str).lower()
                ]
            if _int(days) > 0:
                cutoff = time.time() - _int(days) * 86400
                keep = []
                for r in picked:
                    with suppress(ValueError, TypeError):
                        ts = datetime.strptime(
                            str(r.get("started_utc") or ""), "%Y-%m-%dT%H:%M:%SZ"
                        ).replace(tzinfo=UTC).timestamp()
                        if ts >= cutoff:
                            keep.append(r)
                picked = keep
            out = [
                {
                    "run_id": r.get("run_id"),
                    "started_utc": r.get("started_utc"),
                    "engine": r.get("engine"),
                    "ok": r.get("ok"),
                    "exit_code": r.get("exit_code"),
                    "duration_s": r.get("duration_s"),
                    "tag": r.get("tag"),
                    "argv": r.get("argv"),
                    "n_artifacts": len(r.get("artifacts") or []),
                }
                for r in picked[:lim]
            ]
            return _dump(
                {
                    "action": "list",
                    "ledger_path": str(runs_dir / "ledger.jsonl"),
                    "total": len(rows),
                    "returned": len(out),
                    "runs": out,
                }
            )

        if act in ("show", "artifacts", "verify"):
            rid = (run_id or "").strip()
            if not rid:
                return format_tool_error(
                    f"run_id is required for action={act}",
                    code="NO_RUN_ID",
                    tool_name=tool,
                    suggestion="Call analysis_ledger action=list first.",
                )
            rec = full_record(rid)
            if rec is None:
                return format_tool_error(
                    f"no run {rid} in {runs_dir}",
                    code="NO_SUCH_RUN",
                    tool_name=tool,
                    suggestion="Call analysis_ledger action=list to see the run ids.",
                )
            if act == "show":
                return _dump({"action": "show", "run": rec})
            if act == "artifacts":
                arts = rec.get("artifacts") or []
                if arts and isinstance(arts[0], str):
                    arts = [{"path": a} for a in arts]
                return _dump({"action": "artifacts", "run_id": rid, "artifacts": arts})
            files: list[dict[str, Any]] = []
            drift = 0
            missing = 0
            entries = list(rec.get("inputs") or [])
            arts = rec.get("artifacts") or []
            for a in arts:
                if isinstance(a, dict):
                    entries.append(a)
            for entry in entries:
                fpath = Path(str(entry.get("path") or ""))
                expected = str(entry.get("sha256") or "")
                if not fpath.is_file():
                    state = "MISSING"
                    actual = ""
                    missing += 1
                else:
                    actual = _sha256_file(fpath)
                    if not expected:
                        state = "UNVERIFIABLE"
                    elif actual == expected:
                        state = "INTACT"
                    else:
                        state = "DRIFTED"
                        drift += 1
                files.append(
                    {
                        "rel": entry.get("rel") or fpath.name,
                        "path": str(fpath),
                        "expected_sha256": expected,
                        "actual_sha256": actual,
                        "state": state,
                    }
                )
            status = "MISSING" if missing else ("DRIFTED" if drift else "INTACT")
            return _dump(
                {
                    "action": "verify",
                    "run_id": rid,
                    "status": status,
                    "verified_utc": _utc_now(),
                    "files": files,
                    "note": (
                        "INTACT means the bytes on disk still hash to what this run "
                        "recorded — it does not re-run the analysis."
                    ),
                }
            )

        if act == "diff":
            ids = _split_list(run_id)
            if len(ids) != 2:
                return format_tool_error(
                    "action=diff needs two run ids",
                    code="BAD_RUN_IDS",
                    tool_name=tool,
                    suggestion="Pass run_id='id1,id2'.",
                )
            a, b = full_record(ids[0]), full_record(ids[1])
            if a is None or b is None:
                return format_tool_error(
                    f"unknown run id in {ids}",
                    code="NO_SUCH_RUN",
                    tool_name=tool,
                    suggestion="Call analysis_ledger action=list.",
                )

            def hashes(rec: dict[str, Any], key: str) -> dict[str, str]:
                out: dict[str, str] = {}
                for item in rec.get(key) or []:
                    if isinstance(item, dict):
                        out[str(item.get("rel") or item.get("path") or "")] = str(
                            item.get("sha256") or ""
                        )
                return out

            in_a, in_b = hashes(a, "inputs"), hashes(b, "inputs")
            ar_a, ar_b = hashes(a, "artifacts"), hashes(b, "artifacts")
            return _dump(
                {
                    "action": "diff",
                    "run_ids": ids,
                    "argv_same": a.get("argv") == b.get("argv"),
                    "argv": {ids[0]: a.get("argv"), ids[1]: b.get("argv")},
                    "params": {ids[0]: a.get("params"), ids[1]: b.get("params")},
                    "env": {
                        ids[0]: (a.get("env_summary") or {}),
                        ids[1]: (b.get("env_summary") or {}),
                    },
                    "duration_s": {ids[0]: a.get("duration_s"), ids[1]: b.get("duration_s")},
                    "inputs_changed": sorted(
                        k for k in set(in_a) | set(in_b) if in_a.get(k) != in_b.get(k)
                    ),
                    "artifacts_changed": sorted(
                        k for k in set(ar_a) | set(ar_b) if ar_a.get(k) != ar_b.get(k)
                    ),
                }
            )

        if act == "prune":
            d = _int(days)
            if d <= 0:
                return format_tool_error(
                    "action=prune needs days>0",
                    code="BAD_DAYS",
                    tool_name=tool,
                    suggestion="Pass days=30 to delete run directories older than 30 days.",
                )
            cutoff = time.time() - d * 86400
            removed: list[str] = []
            kept = 0
            for child in sorted(runs_dir.iterdir()) if runs_dir.is_dir() else []:
                if not child.is_dir():
                    continue
                with suppress(OSError):
                    if child.stat().st_mtime < cutoff:
                        shutil.rmtree(child, ignore_errors=True)
                        removed.append(child.name)
                    else:
                        kept += 1
            return _dump(
                {
                    "action": "prune",
                    "days": d,
                    "removed_run_dirs": removed,
                    "kept": kept,
                    "note": "ledger.jsonl lines are never deleted — only the run directories.",
                }
            )

        return format_tool_error(
            f"unknown action {act!r}",
            code="BAD_ACTION",
            tool_name=tool,
            suggestion="action must be list|show|artifacts|verify|diff|prune.",
        )

    # ------------------------------------------------------------ profile

    async def _profile_core(
        target_path: Path,
        *,
        fmt: str,
        delimiter: str,
        encoding: str,
        sheet: str,
        target: str,
        max_rows: int,
        sample_rows: int,
        engine: str,
        timeout: float,
        tool: str,
    ) -> tuple[dict[str, Any], str, list[str]] | str:
        """(core dict, engine_used, notes) or a formatted error string."""
        notes: list[str] = []
        want = (engine or "auto").strip().lower() or "auto"
        root = target_path.parent if target_path.parent.is_dir() else _project_root(runtime)
        needs_project = fmt in ("parquet", "xlsx")

        async def run_project() -> dict[str, Any] | str:
            py, py_source, py_notes = project_python(root)
            notes.extend(py_notes)
            if not py:
                return format_tool_error(
                    "no project Python interpreter found",
                    code="NEEDS_PROJECT_ENV",
                    tool_name=tool,
                    suggestion="Create a project .venv (uv venv) then retry.",
                )
            mods = (await _probe_python_modules(root, py)).get("modules") or {}
            if not mods.get("pandas"):
                return format_tool_error(
                    "the project environment has no importable pandas",
                    code="NEEDS_PROJECT_ENV",
                    tool_name=tool,
                    suggestion=(
                        "Install it in the PROJECT env yourself (uv add pandas, or "
                        "python -m pip install pandas"
                        + (" pyarrow" if fmt == "parquet" else "")
                        + (" openpyxl" if fmt == "xlsx" else "")
                        + "), or use engine=stdlib for csv/tsv/json/jsonl."
                    ),
                )
            if fmt == "parquet" and not mods.get("pyarrow"):
                notes.append("pyarrow was not found — pandas may still read parquet via fastparquet")
            if fmt == "xlsx" and not mods.get("openpyxl"):
                notes.append("openpyxl was not found — pandas cannot read .xlsx without it")
            from remedy.core.build_ledger import build_tmp_script_path

            script_path = build_tmp_script_path(
                "remedy_data_profile.py", str(_project_root(runtime))
            )
            with suppress(OSError):
                script_path.write_text(_PROJECT_PROFILE_SCRIPT, encoding="utf-8")
            payload_args = json.dumps(
                {
                    "path": str(target_path),
                    "format": fmt,
                    "delimiter": delimiter,
                    "encoding": encoding,
                    "sheet": sheet,
                    "target": target,
                    "max_rows": max_rows,
                }
            )
            res = await _sandbox_run(
                runtime,
                [*py, str(script_path), payload_args],
                cwd=root,
                timeout=timeout,
            )
            if _int(getattr(res, "exit_code", 1), 1) != 0:
                return format_tool_error(
                    "the project profiler failed: "
                    + _tail((getattr(res, "stderr", "") or getattr(res, "stdout", "")).strip(), 800),
                    code="PROJECT_PROFILE_FAILED",
                    tool_name=tool,
                    suggestion=(
                        "Check the file with file_read, or retry with engine=stdlib "
                        "for csv/tsv/json/jsonl."
                    ),
                )
            obj = _marker_json(getattr(res, "stdout", "") or "", _PROFILE_MARKER)
            if not obj:
                return format_tool_error(
                    "the project profiler printed no JSON",
                    code="PROJECT_PROFILE_FAILED",
                    tool_name=tool,
                    suggestion="Retry with engine=stdlib, or run the file yourself.",
                )
            return obj

        if want == "project" or (want == "auto" and needs_project):
            core = await run_project()
            # No silent fallback here: parquet/xlsx have no stdlib path, and an
            # explicit engine=project must not quietly become something else.
            if isinstance(core, str):
                return core
            return core, "project", notes
        if want == "stdlib" and needs_project:
            return format_tool_error(
                f"the stdlib engine cannot read {fmt} files",
                code="NEEDS_PROJECT_ENV",
                tool_name=tool,
                suggestion=(
                    "Use engine=project with pandas + "
                    + ("pyarrow" if fmt == "parquet" else "openpyxl")
                    + " installed in the project env, or export the file to csv first."
                ),
            )
        if want == "auto":
            py, _src, py_notes = project_python(root)
            mods = (await _probe_python_modules(root, py)).get("modules") or {} if py else {}
            if mods.get("pandas"):
                core = await run_project()
                if not isinstance(core, str):
                    return core, "project", notes
                notes.append(
                    "the project pandas path failed, fell back to stdlib: "
                    + core.splitlines()[0]
                )
        try:
            core = await asyncio.to_thread(
                _profile_stdlib,
                target_path,
                fmt=fmt,
                delimiter=delimiter,
                encoding=encoding,
                target=target,
                max_rows=max_rows,
                sample_rows=sample_rows,
            )
        except (ValueError, OSError, UnicodeError) as exc:
            return format_tool_error(
                f"could not read {target_path}: {exc}",
                code="READ_FAILED",
                tool_name=tool,
                suggestion="Check format=/delimiter=/encoding=, or use engine=project.",
            )
        return core, "stdlib", notes

    async def data_profile(
        path: str = "",
        format: str = "auto",  # noqa: A002
        delimiter: str = "",
        encoding: str = "",
        sheet: str = "",
        target: str = "",
        max_rows: int = 200000,
        sample_rows: int = 0,
        engine: str = "auto",
        timeout_seconds: float = 180.0,
    ) -> str:
        """Shape, dtypes, missingness, duplicates, class balance and leakage suspects."""
        tool = "data_profile"
        if not (path or "").strip():
            return format_tool_error(
                "path is required",
                code="NO_PATH",
                tool_name=tool,
                suggestion="Pass path= to a csv/tsv/json/jsonl/parquet/xlsx file.",
            )
        target_path = _resolve_read(runtime, path)
        if not target_path.is_file():
            return format_tool_error(
                f"not a file: {target_path}",
                code="NOT_FOUND",
                tool_name=tool,
                suggestion="Check the path with list_dir on its parent.",
            )
        fmt = _detect_format(target_path, format)
        got = await _profile_core(
            target_path,
            fmt=fmt,
            delimiter=delimiter,
            encoding=encoding,
            sheet=sheet,
            target=(target or "").strip(),
            max_rows=max(1, _int(max_rows, 200000) or 200000),
            sample_rows=max(0, _int(sample_rows, 0)),
            engine=engine,
            timeout=_clamp(timeout_seconds, 10.0, 600.0, 180.0),
            tool=tool,
        )
        if isinstance(got, str):
            return got
        core, engine_used, notes = got
        payload = _assemble_profile(
            target_path,
            core,
            engine_used=engine_used,
            target=(target or "").strip(),
            sampled=bool(sample_rows) or bool(core.get("truncated")),
        )
        payload["format"] = fmt
        payload["notes"] = list(payload.get("notes") or []) + notes
        return _dump(payload)

    async def data_diff(
        left: str = "",
        right: str = "",
        key: str = "",
        max_rows: int = 200000,
        engine: str = "auto",
    ) -> str:
        """Schema + distribution drift between two tabular files."""
        tool = "data_diff"
        if not (left or "").strip() or not (right or "").strip():
            return format_tool_error(
                "left and right are both required",
                code="NO_PATH",
                tool_name=tool,
                suggestion="Pass left= and right= to two tabular files.",
            )
        lp, rp = _resolve_read(runtime, left), _resolve_read(runtime, right)
        for p in (lp, rp):
            if not p.is_file():
                return format_tool_error(
                    f"not a file: {p}",
                    code="NOT_FOUND",
                    tool_name=tool,
                    suggestion="Check the path with list_dir on its parent.",
                )
        rows_cap = max(1, _int(max_rows, 200000) or 200000)
        cores: list[dict[str, Any]] = []
        engines: list[str] = []
        notes: list[str] = []
        for p in (lp, rp):
            got = await _profile_core(
                p,
                fmt=_detect_format(p, "auto"),
                delimiter="",
                encoding="",
                sheet="",
                target="",
                max_rows=rows_cap,
                sample_rows=0,
                engine=engine,
                timeout=300.0,
                tool=tool,
            )
            if isinstance(got, str):
                return got
            cores.append(got[0])
            engines.append(got[1])
            notes.extend(got[2])
        lc, rc = cores
        lcols = {str(c["name"]): c for c in lc.get("columns") or []}
        rcols = {str(c["name"]): c for c in rc.get("columns") or []}
        added = [c for c in rcols if c not in lcols]
        removed = [c for c in lcols if c not in rcols]
        left_order = [str(c["name"]) for c in lc.get("columns") or []]
        right_order = [str(c["name"]) for c in rc.get("columns") or []]
        shared = [c for c in left_order if c in rcols]
        retyped = [
            {
                "column": c,
                "left_dtype": lcols[c].get("inferred_dtype"),
                "right_dtype": rcols[c].get("inferred_dtype"),
            }
            for c in shared
            if lcols[c].get("inferred_dtype") != rcols[c].get("inferred_dtype")
        ]
        reordered = [c for c in shared if left_order.index(c) != right_order.index(c)]
        col_rows: list[dict[str, Any]] = []
        lsamples = lc.get("samples") or {}
        rsamples = rc.get("samples") or {}
        for c in shared:
            lcol, rcol = lcols[c], rcols[c]

            def delta(a: Any, b: Any) -> Any:
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    return round(float(b) - float(a), 10)
                return None

            row: dict[str, Any] = {
                "column": c,
                "missing_pct_left": lcol.get("pct_missing"),
                "missing_pct_right": rcol.get("pct_missing"),
                "missing_pct_delta": delta(lcol.get("pct_missing"), rcol.get("pct_missing")),
                "mean_left": lcol.get("mean"),
                "mean_right": rcol.get("mean"),
                "mean_delta": delta(lcol.get("mean"), rcol.get("mean")),
                "sd_left": lcol.get("sd"),
                "sd_right": rcol.get("sd"),
                "sd_delta": delta(lcol.get("sd"), rcol.get("sd")),
                "n_unique_left": lcol.get("n_unique"),
                "n_unique_right": rcol.get("n_unique"),
            }
            la, ra = list(lsamples.get(c) or []), list(rsamples.get(c) or [])
            if la and ra:
                row["ks"] = ks_two_sample(la, ra)
            col_rows.append(row)
        key_overlap: dict[str, Any] = {}
        keys = _split_list(key)
        if keys:
            key_overlap = {
                "keys": keys,
                "computed": False,
                "why": (
                    "key overlap needs a second pass over both files; run it in the "
                    "project env (pandas merge with indicator=True) or ask for it "
                    "explicitly — this tool does not hold both key sets in memory."
                ),
            }
        return _dump(
            {
                "left": str(lp),
                "right": str(rp),
                "engines_used": engines,
                "schema": {
                    "columns_added": added,
                    "columns_removed": removed,
                    "columns_retyped": retyped,
                    "columns_reordered": reordered,
                    "left_order": left_order,
                    "right_order": right_order,
                },
                "rows": {
                    "left": lc.get("rows_scanned"),
                    "right": rc.get("rows_scanned"),
                    "delta": _int(rc.get("rows_scanned")) - _int(lc.get("rows_scanned")),
                    "left_truncated": bool(lc.get("truncated")),
                    "right_truncated": bool(rc.get("truncated")),
                },
                "key_overlap": key_overlap,
                "columns": col_rows,
                "notes": notes
                + [
                    "KS D is computed on the seeded reservoir samples each profile kept "
                    f"(up to {_SAMPLE_CAP} numeric values per column), not on every row.",
                    "Distribution deltas describe the files as read here; they do not "
                    "explain why the data moved.",
                ],
            }
        )

    # ---------------------------------------------------------- register

    # Outer dispatcher budgets (tool_timeouts.py resolves _remedy_timeout first).
    analysis_env._remedy_timeout = 240.0  # type: ignore[attr-defined]
    analysis_run._remedy_timeout = 1800.0  # type: ignore[attr-defined]
    analysis_ledger._remedy_timeout = 60.0  # type: ignore[attr-defined]
    data_profile._remedy_timeout = 600.0  # type: ignore[attr-defined]
    data_diff._remedy_timeout = 300.0  # type: ignore[attr-defined]

    reg = runtime.tool_registry
    reg.register_builtin_handler(
        "analysis_env",
        "What can actually run in this project: the project Python (.venv / uv run), "
        "Rscript, julia, quarto, pandoc, LaTeX, pdftotext, dvc/snakemake/nextflow, and "
        "whether pandas/numpy/pyarrow/papermill/nbconvert are importable IN THE PROJECT "
        "env. Call this before analysis_run. probe=false skips version subprocesses.",
        analysis_env,
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "probe": {"type": "boolean"},
                "timeout_seconds": {"type": "number"},
            },
        },
    )
    reg.register_builtin_handler(
        "analysis_run",
        "Run ONE analysis file headlessly in the PROJECT's environment and record it in "
        "the run ledger: .ipynb via papermill (else nbconvert), .py via the project "
        "python, .R via Rscript --vanilla, .jl via julia --project, .qmd/.Rmd via quarto "
        "(else rmarkdown::render). params_json parameterises papermill/quarto and is "
        "written to a params file for python/R/julia. Figures and tables created under "
        "the workdir are collected as artifacts with their hashes. Shell scripts are "
        "refused — use bash_exec.",
        analysis_run,
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "engine": {"type": "string"},
                "args": {"type": "string"},
                "params_json": {"type": "string"},
                "workdir": {"type": "string"},
                "env_json": {"type": "string"},
                "artifacts_dir": {"type": "string"},
                "artifacts_glob": {"type": "string"},
                "record": {"type": "boolean"},
                "tag": {"type": "string"},
                "description": {"type": "string"},
                "timeout_seconds": {"type": "number"},
            },
        },
    )
    reg.register_builtin_handler(
        "analysis_ledger",
        "Query the durable run ledger. action=list (newest first, filter with query/days), "
        "show (full record), artifacts, verify (re-hash the recorded inputs and artifacts "
        "and report DRIFT — 'is this figure still the one that came out of this data?'), "
        "diff (two run ids, comma separated), prune (delete run dirs older than days; "
        "ledger lines are kept).",
        analysis_ledger,
        {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "run_id": {"type": "string"},
                "path": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer"},
                "days": {"type": "integer"},
            },
        },
    )
    reg.register_builtin_handler(
        "data_profile",
        "Profile a tabular file: rows/columns, inferred dtypes, missingness, duplicate "
        "rows, constant / id-like / high-cardinality columns, class balance for target=, "
        "and leakage SUSPECTS with their evidence (never a verdict). engine=project "
        "shells out to the project's pandas; engine=stdlib is a pure-csv fallback that "
        "says what it cannot see. parquet/xlsx need the project env.",
        data_profile,
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "format": {"type": "string"},
                "delimiter": {"type": "string"},
                "encoding": {"type": "string"},
                "sheet": {"type": "string"},
                "target": {"type": "string"},
                "max_rows": {"type": "integer"},
                "sample_rows": {"type": "integer"},
                "engine": {"type": "string"},
                "timeout_seconds": {"type": "number"},
            },
        },
    )
    reg.register_builtin_handler(
        "data_diff",
        "Schema and distribution drift between two tabular files: columns added / "
        "removed / retyped / reordered, row counts, per-column missingness and mean/sd "
        "deltas, and a two-sample Kolmogorov-Smirnov D with its asymptotic p. Answers "
        "'did the data move under my result?'.",
        data_diff,
        {
            "type": "object",
            "properties": {
                "left": {"type": "string"},
                "right": {"type": "string"},
                "key": {"type": "string"},
                "max_rows": {"type": "integer"},
                "engine": {"type": "string"},
            },
        },
    )
