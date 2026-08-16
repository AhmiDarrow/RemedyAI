"""Auto-repair scheduler — error vector → ranked hop targets.

Frontier C: red verify becomes a work queue, not a free-form prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
    return p.lstrip("./")


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
        src = _test_to_source_guess(path)
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


def _test_to_source_guess(test_path: str) -> str | None:
    rel = test_path.replace("\\", "/")
    name = Path(rel).name
    if name.startswith("test_"):
        stem = name[len("test_") :]
        parent = str(Path(rel).parent).replace("\\", "/")
        # tests/test_foo.py → foo.py
        if "tests" in parent.split("/"):
            return stem  # best-effort bare name; caller may resolve
        return stem
    if name.endswith("_test.py"):
        return name[: -len("_test.py")] + ".py"
    return None


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

    results: list[dict[str, Any]] = []
    for t in queue.targets[:max_targets]:
        # Prefer non-test sources
        path = t.path
        if path.startswith("tests/") or Path(path).name.startswith("test_"):
            guess = _test_to_source_guess(path)
            if guess and not guess.endswith(".py"):
                guess = guess if "/" in guess else guess  # keep
            # skip pure test hops for auto LLM unless only target — unless the
            # broadened strategy explicitly wants to repair tests too.
            if len(queue.targets) > 1 and not include_tests:
                continue
        try:
            res = live_unit_hop(
                runtime,
                path=path if path.endswith(".py") else path,
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
