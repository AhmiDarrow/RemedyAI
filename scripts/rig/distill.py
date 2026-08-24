"""Turn harness traces into fine-tuning data for a small local model.

Every scored run records the exact request bodies the provider saw. Because a
ReAct step re-sends the whole conversation, the *last* record of a session
already contains the full trajectory: Remedy's assembled system prompt, the
tool schemas after local slimming, the user's task, and every assistant
``tool_calls`` / ``tool`` result pair the model produced along the way.

That is precisely the shape a tool-calling SFT run wants. So a teacher run
(a strong model driving the real product) doubles as a training set for a small
one, with no separate data-collection step.

    python -m rig.distill build --traces run/traces --scorecard out/teacher.json \\
        --out data/remedy-sft.jsonl --passed-only

Output is one JSON object per line::

    {"tools": [...], "messages": [...], "meta": {...}}

which unsloth / axolotl / trl all accept for chat-with-tools training.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class TraceRecord:
    seq: int
    ts: float
    provider: str
    model: str
    step: int
    session_id: str
    body: dict[str, Any]

    @property
    def messages(self) -> list[dict[str, Any]]:
        m = self.body.get("messages")
        return m if isinstance(m, list) else []

    @property
    def tools(self) -> list[dict[str, Any]]:
        t = self.body.get("tools")
        return t if isinstance(t, list) else []


def load_traces(trace_dir: Path) -> dict[str, list[TraceRecord]]:
    """Group every trace record by session, ordered by step then sequence."""
    sessions: dict[str, list[TraceRecord]] = defaultdict(list)
    for path in sorted(Path(trace_dir).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            body = raw.get("body")
            if not isinstance(body, dict):
                continue
            rec = TraceRecord(
                seq=int(raw.get("seq") or 0),
                ts=float(raw.get("ts") or 0.0),
                provider=str(raw.get("provider") or ""),
                model=str(raw.get("model") or ""),
                step=int(raw.get("step") or 0),
                session_id=str(raw.get("session_id") or path.stem),
                body=body,
            )
            sessions[rec.session_id or path.stem].append(rec)
    for recs in sessions.values():
        recs.sort(key=lambda r: (r.step, r.seq))
    return dict(sessions)


def _assistant_turns(messages: Iterable[dict[str, Any]]) -> int:
    return sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "assistant")


def _has_tool_calls(messages: Iterable[dict[str, Any]]) -> bool:
    return any(
        isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
        for m in messages
    )


def build_samples(
    sessions: dict[str, list[TraceRecord]],
    *,
    require_tool_calls: bool = True,
    min_assistant_turns: int = 1,
    final_answers: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """One training sample per session, taken from its richest trace record."""
    samples: list[dict[str, Any]] = []
    for sid, recs in sessions.items():
        if not recs:
            continue
        # Richest = most messages. Usually the last step, but a retry or a
        # context compaction can make a later step shorter than an earlier one.
        best = max(recs, key=lambda r: len(r.messages))
        messages = [m for m in best.messages if isinstance(m, dict)]
        if not messages:
            continue

        # The final assistant answer arrives after the last request, so it is
        # never in the trace. Append it when the scorecard captured it.
        tail = (final_answers or {}).get(sid, "").strip()
        if tail and (not messages or messages[-1].get("role") != "assistant"):
            messages = messages + [{"role": "assistant", "content": tail}]

        if _assistant_turns(messages) < min_assistant_turns:
            continue
        if require_tool_calls and not _has_tool_calls(messages):
            continue

        samples.append(
            {
                "tools": best.tools,
                "messages": messages,
                "meta": {
                    "session_id": sid,
                    "teacher_provider": best.provider,
                    "teacher_model": best.model,
                    "steps": len(recs),
                    "assistant_turns": _assistant_turns(messages),
                    "tool_count": len(best.tools),
                },
            }
        )
    return samples


def load_scorecard(path: Path) -> tuple[set[str], dict[str, Any]]:
    """Return (passed scenario ids, raw scorecard)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    passed = {
        str(o.get("id"))
        for o in data.get("outcomes", [])
        if o.get("passed")
    }
    return passed, data


def stats(samples: list[dict[str, Any]]) -> str:
    if not samples:
        return "  no samples"
    turns = [s["meta"]["assistant_turns"] for s in samples]
    tools = [s["meta"]["tool_count"] for s in samples]
    msgs = [len(s["messages"]) for s in samples]
    def avg(xs: list[int]) -> float:
        return sum(xs) / len(xs)
    return (
        f"  samples          : {len(samples)}\n"
        f"  assistant turns  : avg {avg(turns):.1f}, max {max(turns)}\n"
        f"  messages/sample  : avg {avg(msgs):.1f}, max {max(msgs)}\n"
        f"  tool schemas     : avg {avg(tools):.1f}"
    )


def cmd_build(args: argparse.Namespace) -> int:
    trace_dir = Path(args.traces)
    if not trace_dir.is_dir():
        raise SystemExit(f"trace dir not found: {trace_dir}")

    sessions = load_traces(trace_dir)
    print(f"  loaded {len(sessions)} session(s) from {trace_dir}")

    final_answers: dict[str, str] = {}
    if args.scorecard:
        passed, _card = load_scorecard(Path(args.scorecard))
        print(f"  scorecard: {len(passed)} scenario(s) passed")
        if args.passed_only:
            # Sessions are titled rig:<scenario id>; the trace filename is the
            # session uuid, so match on the scenario recorded in the sample.
            print("  note: --passed-only filters by scenario tag in the user turn")

    samples = build_samples(
        sessions,
        require_tool_calls=not args.allow_no_tools,
        min_assistant_turns=args.min_turns,
        final_answers=final_answers,
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for s in samples:
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(stats(samples))
    print(f"  wrote {out}")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    """Print one trajectory in readable form - sanity-check before training."""
    sessions = load_traces(Path(args.traces))
    if not sessions:
        raise SystemExit("no traces found")
    sid = args.session or next(iter(sessions))
    recs = sessions.get(sid)
    if not recs:
        raise SystemExit(f"session {sid} not in traces ({len(sessions)} available)")
    best = max(recs, key=lambda r: len(r.messages))
    print(f"\n  session {sid}  ({len(recs)} steps, {len(best.tools)} tools)\n")
    for m in best.messages:
        role = m.get("role", "?")
        content = str(m.get("content") or "")
        if role == "assistant" and m.get("tool_calls"):
            names = [
                tc.get("function", {}).get("name", "?")
                for tc in m["tool_calls"]
                if isinstance(tc, dict)
            ]
            print(f"  [assistant] -> tools: {', '.join(names)}")
            if content.strip():
                print(f"              {content.strip()[:200]}")
        else:
            body = content.replace("\n", " ")[: args.width]
            print(f"  [{role}] {body}")
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rig.distill", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="Write an SFT JSONL from traces")
    b.add_argument("--traces", required=True, help="Directory of trace JSONL files")
    b.add_argument("--out", required=True, help="Output JSONL path")
    b.add_argument("--scorecard", default="", help="Scorecard JSON for quality filtering")
    b.add_argument("--passed-only", action="store_true", help="Keep only passing runs")
    b.add_argument("--min-turns", type=int, default=1)
    b.add_argument(
        "--allow-no-tools",
        action="store_true",
        help="Keep trajectories that never called a tool",
    )

    i = sub.add_parser("inspect", help="Print one trajectory")
    i.add_argument("--traces", required=True)
    i.add_argument("--session", default="")
    i.add_argument("--width", type=int, default=160)

    args = ap.parse_args(argv)
    if args.cmd == "build":
        return cmd_build(args)
    return cmd_inspect(args)


if __name__ == "__main__":
    raise SystemExit(main())
