"""Auto-repair scheduler — error vector → ranked hop targets.

Frontier C: red verify becomes a work queue, not a free-form prompt.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from remedy.core.relpath import norm_rel


@dataclass
class RepairTarget:
    path: str
    reason: str
    priority: int = 50  # lower = sooner
    symbol: str = ""
    node: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "reason": self.reason,
            "priority": self.priority,
            "symbol": self.symbol,
            "node": self.node,
        }


@dataclass
class RepairQueue:
    targets: list[RepairTarget] = field(default_factory=list)
    source: str = ""
    vector: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "targets": [t.to_public() for t in self.targets],
            "count": len(self.targets),
        }


def _norm_path(p: str, root: Path | None = None) -> str:
    p = (p or "").replace("\\", "/").strip()
    # strip pytest node suffix
    if "::" in p:
        p = p.split("::", 1)[0]
    p = re.sub(r":\d+$", "", p)
    if root and p:
        try:
            pp = Path(p)
            if pp.is_absolute():
                p = pp.relative_to(root.resolve()).as_posix()
        except Exception:
            pass
    return norm_rel(p)


def queue_from_error_vector(
    vector: dict[str, Any] | None,
    *,
    write_set: list[str] | None = None,
    root: Path | str | None = None,
) -> RepairQueue:
    """Build ranked repair targets from a parsed error vector dict."""
    root_p = Path(root) if root else None
    q = RepairQueue(source="error_vector", vector=dict(vector or {}))
    seen: set[str] = set()

    def add(path: str, reason: str, priority: int, node: str = "", symbol: str = "") -> None:
        path = _norm_path(path, root_p)
        if not path or path in seen:
            return
        # skip pure test files as first repair target when source exists
        seen.add(path)
        q.targets.append(
            RepairTarget(path=path, reason=reason, priority=priority, node=node, symbol=symbol)
        )

    vec = vector or {}
    for i, node in enumerate(vec.get("failing_nodes") or []):
        node_s = str(node)
        path = _norm_path(node_s, root_p)
        # Prefer mapping test → source
        src = _test_to_source_guess(path, root_p)
        if src and src != path:
            add(src, f"fails under {node_s}", 10 + i, node=node_s)
        add(path, f"failing node {node_s}", 20 + i, node=node_s)

    for i, pl in enumerate(vec.get("path_lines") or []):
        m = re.match(r"(.+?):(\d+)", str(pl))
        path = m.group(1) if m else str(pl)
        add(path, f"hotspot {pl}", 15 + i)

    for w in write_set or []:
        add(str(w), "in write_set (unverified)", 40)

    q.targets.sort(key=lambda t: (t.priority, t.path))
    # Cap
    q.targets = q.targets[:16]
    return q


def _repo_root(root: Path | str | None = None) -> Path:
    """Where to look for sources. Explicit root wins; otherwise walk up to a
    directory that actually contains ``src`` or ``pyproject.toml``."""
    if root:
        return Path(root)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() or (parent / "src").is_dir():
            return parent
    return Path.cwd()


def _source_candidates(stem: str) -> list[str]:
    """Every plausible source layout for a test stem, most specific first.

    ``telephony_line`` could be ``telephony/line.py`` or ``telephony_line.py``,
    and only the tree knows which. Underscores are the only evidence a test file
    name carries about package structure, so try each of them as a separator.
    """
    parts = stem.split("_")
    out: list[str] = []
    for cut in range(len(parts) - 1, 0, -1):
        out.append("/".join(parts[:cut]) + "/" + "_".join(parts[cut:]) + ".py")
    out.append(stem + ".py")
    return out


#: Seconds a source index stays trusted before the tree is walked again. Files
#: are scaffolded mid-build; an index cached forever never learned about them.
_SOURCE_INDEX_TTL = 5.0
_source_index_cache: dict[str, tuple[float, tuple[str, ...]]] = {}


def invalidate_source_index(root: Path | str | None = None) -> None:
    """Forget the cached source tree (one root, or all of them).

    Call after scaffolding files so the next guess sees them; otherwise the
    TTL rebuilds the index on its own shortly after.
    """
    if root is None:
        _source_index_cache.clear()
    else:
        _source_index_cache.pop(str(root), None)
    _resolve_source.cache_clear()


def _source_index(root_s: str) -> tuple[str, ...]:
    """Every source file in the tree, per root, rebuilt after a short TTL."""
    now = time.monotonic()
    hit = _source_index_cache.get(root_s)
    if hit is not None and now - hit[0] < _SOURCE_INDEX_TTL:
        return hit[1]
    root = Path(root_s)
    base = root / "src" if (root / "src").is_dir() else root
    try:
        index = tuple(
            p.relative_to(root).as_posix()
            for p in base.rglob("*.py")
            if p.is_file() and "__pycache__" not in p.parts
        )
    except OSError:
        index = ()
    if len(_source_index_cache) >= 8:
        _source_index_cache.pop(next(iter(_source_index_cache)))
    _source_index_cache[root_s] = (now, index)
    # A stale resolution would outlive the index it was computed from.
    _resolve_source.cache_clear()
    return index


@lru_cache(maxsize=1024)
def _resolve_source(stem: str, root_s: str) -> str | None:
    """Turn a bare test stem into a path that exists, or None.

    The bare name on its own was never resolvable: a queue whose top-priority
    target is ``telephony_line.py`` sends the repair loop at a file that is not
    in the tree, while the real one sits at ``src/remedy/telephony/line.py``.
    Package nesting is unknown here, so match on the tail of the path and take
    the shallowest hit.
    """
    index = _source_index(root_s)
    if not index:
        return None
    for candidate in _source_candidates(stem):
        hits = [
            p for p in index if p == candidate or p.endswith("/" + candidate)
        ]
        if hits:
            return min(hits, key=lambda p: (p.count("/"), len(p)))
    return None


def _test_to_source_guess(test_path: str, root: Path | str | None = None) -> str | None:
    """The source a failing test is probably about, as a path that exists.

    Falls back to the bare name when nothing in the tree matches, so a guess is
    never worse than it used to be — only more often usable.
    """
    rel = test_path.replace("\\", "/")
    name = Path(rel).name
    stem = ""
    if name.startswith("test_"):
        stem = name[len("test_") :]
        stem = stem[: -len(".py")] if stem.endswith(".py") else stem
    elif name.endswith("_test.py"):
        stem = name[: -len("_test.py")]
    if not stem:
        return None
    return _resolve_source(stem, str(_repo_root(root))) or (stem + ".py")


def format_repair_queue_message(queue: RepairQueue) -> dict[str, str]:
    lines = [
        "[Build engine · REPAIR QUEUE]",
        f"Machine scheduled {len(queue.targets)} target(s). Fix in order; re-verify after each.",
    ]
    for i, t in enumerate(queue.targets[:12], 1):
        extra = f" symbol={t.symbol}" if t.symbol else ""
        lines.append(f"  {i}. {t.path} — {t.reason}{extra}")
    lines.append(
        "Preferred: build_unit_hop path=… use_llm=true (or file_edit) then gate tower / verify."
    )
    return {"role": "user", "content": "\n".join(lines)}


def _project_root(runtime: Any) -> Path:
    """The project tree a repair runs in: ``runtime.effective_project_path()``
    when the runtime has one, else the same walk-up ``_repo_root`` does."""
    fn = getattr(runtime, "effective_project_path", None)
    if callable(fn):
        try:
            return Path(fn())
        except Exception:
            pass
    return _repo_root(None)


def run_auto_repair_hops(
    runtime: Any,
    queue: RepairQueue,
    *,
    use_llm: bool = True,
    max_targets: int = 3,
    max_repairs: int = 2,
    include_tests: bool = False,
) -> dict[str, Any]:
    """Execute live_unit_hop on top queue targets (machine repair loop).

    ``include_tests`` (broadened strategy): also hop test files — a failure
    can live in an over-strict or wrong test, not only the source. The
    default (narrow) strategy repairs source first.
    """
    from remedy.core.build_live_hop import live_unit_hop

    # The same root live_unit_hop resolves against. Guessing from Remedy's own
    # __file__ and testing exists() against cwd aimed the repair at the wrong
    # tree whenever the project was not also the working directory.
    root = _project_root(runtime)
    results: list[dict[str, Any]] = []
    for t in queue.targets[:max_targets]:
        # Prefer non-test sources
        path = t.path
        if path.startswith("tests/") or Path(path).name.startswith("test_"):
            # Repair the source, not the test. The guess used to be computed
            # here and then thrown away, so the "prefer non-test sources"
            # strategy above never actually happened: a lone failing target got
            # its *test* rewritten, and any other target was skipped outright.
            guess = _test_to_source_guess(path, root)
            if guess and guess != path and (root / guess).exists():
                path = guess
            elif len(queue.targets) > 1 and not include_tests:
                # Nothing to redirect to. Hopping the test itself is a last
                # resort, and only when it is the sole target.
                continue
        try:
            res = live_unit_hop(
                runtime,
                path=path,
                symbol=t.symbol or Path(path).stem,
                use_llm=use_llm,
                max_repairs=max_repairs,
                tests="",  # behavioral filled if locked spec later
            )
        except TypeError:
            res = live_unit_hop(
                runtime,
                path=path,
                symbol=t.symbol or Path(path).stem,
                use_llm=use_llm,
                max_repairs=max_repairs,
            )
        results.append({"target": t.to_public(), "result": res})
        if res.get("ok"):
            break  # one green hop at a time; re-queue after verify
    return {
        "ok": any(r.get("result", {}).get("ok") for r in results),
        "ran": len(results),
        "results": results,
    }
