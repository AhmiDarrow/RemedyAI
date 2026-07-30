"""PartnerState facade — agency OS for Remedy sessions."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from remedy.memory.partner_state.models import (
    GraphEdge,
    GraphNode,
    ProspectiveItem,
    Subgoal,
    ToolTxn,
    WriteEntry,
    _id,
    _now,
)

logger = logging.getLogger(__name__)

_WRITE_TOOLS = frozenset(
    {
        "file_write",
        "file_edit",
        "file_edit_batch",
        "apply_patch",
        "bash_exec",  # may write; tracked when path-like signals present
    }
)
_READ_TOOLS = frozenset(
    {
        "file_read",
        "list_dir",
        "repo_search",
        "web_fetch",
        "web_search",
        "memory_search",
        "tool_recall",
    }
)

_registry: dict[str, "PartnerState"] = {}
_registry_lock = threading.Lock()


def _digest(text: str, *, n: int = 16) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()[:n]


def _extract_paths(args: dict[str, Any], result: str = "") -> list[str]:
    paths: list[str] = []
    for key in ("path", "file", "filepath", "target", "dest", "destination"):
        v = args.get(key)
        if isinstance(v, str) and v.strip() and len(v) < 500:
            paths.append(v.strip())
    for key in ("paths", "files"):
        v = args.get(key)
        if isinstance(v, list):
            for item in v[:12]:
                if isinstance(item, str) and item.strip():
                    paths.append(item.strip())
                elif isinstance(item, dict):
                    p = item.get("path") or item.get("file")
                    if isinstance(p, str) and p.strip():
                        paths.append(p.strip())
    # light scan of result for path-like tokens
    if result and len(paths) < 6:
        with suppress(Exception):
            from remedy.memory.harness.compressor import extract_paths_from_text

            for p in extract_paths_from_text(result, limit=4):
                if p not in paths:
                    paths.append(p)
    # dedupe preserve order
    out: list[str] = []
    seen: set[str] = set()
    for p in paths:
        k = p.replace("\\", "/").lower()
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out[:20]


class PartnerState:
    """In-session partner state (optionally persisted under home)."""

    def __init__(self, session_id: str = "", *, home: Path | str | None = None) -> None:
        self.session_id = (session_id or "").strip() or "default"
        self.home = Path(home).expanduser() if home else None
        self.subgoals: list[Subgoal] = []
        self.active_subgoal_id: str | None = None
        self.tool_txns: list[ToolTxn] = []
        self.write_set: dict[str, WriteEntry] = {}
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        self.prospective: list[ProspectiveItem] = []
        self.updated_at: datetime = _now()
        self.continuity_passes: int = 0
        self._lock = threading.RLock()

    # --- persistence -------------------------------------------------
    def _path(self) -> Path | None:
        if self.home is None:
            return None
        root = Path(self.home) / "partner_state"
        root.mkdir(parents=True, exist_ok=True)
        safe = "".join(c for c in self.session_id if c.isalnum() or c in "-_")[:48]
        return root / f"{safe or 'default'}.json"

    def save(self) -> None:
        path = self._path()
        if path is None:
            return
        with self._lock:
            data = {
                "session_id": self.session_id,
                "active_subgoal_id": self.active_subgoal_id,
                "subgoals": [s.model_dump(mode="json") for s in self.subgoals[-40:]],
                "tool_txns": [t.model_dump(mode="json") for t in self.tool_txns[-200:]],
                "write_set": {k: v.model_dump(mode="json") for k, v in list(self.write_set.items())[-80:]},
                "nodes": {k: v.model_dump(mode="json") for k, v in list(self.nodes.items())[-200:]},
                "edges": [e.model_dump(mode="json") for e in self.edges[-300:]],
                "prospective": [p.model_dump(mode="json") for p in self.prospective[-80:]],
                "continuity_passes": self.continuity_passes,
                "updated_at": self.updated_at.isoformat(),
            }
            with suppress(Exception):
                path.write_text(json.dumps(data, indent=0, default=str), encoding="utf-8")

    def load(self) -> bool:
        path = self._path()
        if path is None or not path.is_file():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        with self._lock:
            self.active_subgoal_id = data.get("active_subgoal_id")
            self.subgoals = [Subgoal.model_validate(x) for x in (data.get("subgoals") or [])]
            self.tool_txns = [ToolTxn.model_validate(x) for x in (data.get("tool_txns") or [])]
            self.write_set = {
                k: WriteEntry.model_validate(v)
                for k, v in (data.get("write_set") or {}).items()
            }
            self.nodes = {
                k: GraphNode.model_validate(v) for k, v in (data.get("nodes") or {}).items()
            }
            self.edges = [GraphEdge.model_validate(x) for x in (data.get("edges") or [])]
            self.prospective = [
                ProspectiveItem.model_validate(x) for x in (data.get("prospective") or [])
            ]
            self.continuity_passes = int(data.get("continuity_passes") or 0)
        return True

    # --- Phase A: subgoals -------------------------------------------
    def open_subgoal(self, title: str, *, notes: str = "", parent_id: str | None = None) -> Subgoal:
        with self._lock:
            sg = Subgoal(title=(title or "Untitled").strip()[:200], notes=(notes or "")[:500])
            if parent_id:
                sg.parent_id = parent_id
            self.subgoals.append(sg)
            self.active_subgoal_id = sg.id
            self.updated_at = _now()
            self.save()
            return sg

    def close_subgoal(
        self,
        subgoal_id: str | None = None,
        *,
        status: str = "closed",
        summary: str = "",
    ) -> Subgoal | None:
        with self._lock:
            sid = (subgoal_id or self.active_subgoal_id or "").strip()
            sg = next((s for s in self.subgoals if s.id == sid), None)
            if sg is None:
                return None
            sg.status = "parked" if status == "parked" else "closed"
            sg.closed_at = _now()
            if summary:
                sg.notes = ((sg.notes + "\n" if sg.notes else "") + summary.strip())[:800]
            if self.active_subgoal_id == sg.id:
                # Activate most recent still-open parent or sibling
                open_sgs = [s for s in self.subgoals if s.status == "open"]
                self.active_subgoal_id = open_sgs[-1].id if open_sgs else None
            # Promote summary into graph
            if summary or sg.title:
                self._add_node_unlocked(
                    kind="decision",
                    text=f"Closed subgoal: {sg.title}" + (f" — {summary}" if summary else ""),
                    source="agent",
                    confidence=0.85,
                )
            self.updated_at = _now()
            self.save()
            return sg

    def active_subgoal(self) -> Subgoal | None:
        with self._lock:
            if not self.active_subgoal_id:
                return None
            return next(
                (s for s in self.subgoals if s.id == self.active_subgoal_id), None
            )

    def protected_tool_call_ids(self) -> set[str]:
        """Tool results that must stay full while subgoal is open."""
        with self._lock:
            ids: set[str] = set()
            for sg in self.subgoals:
                if sg.status == "open":
                    ids.update(sg.tool_call_ids)
            return ids

    def ensure_active_subgoal(self, title_hint: str = "") -> Subgoal:
        sg = self.active_subgoal()
        if sg is not None and sg.status == "open":
            return sg
        return self.open_subgoal(title_hint or "Active work")

    # --- Phase B: tool txns + write set ------------------------------
    def record_tool(
        self,
        *,
        name: str,
        args: dict[str, Any] | None = None,
        result: str = "",
        success: bool = True,
        tool_call_id: str = "",
        offload_path: str | None = None,
        claim: str = "",
    ) -> ToolTxn:
        args = args or {}
        args_s = json.dumps(args, sort_keys=True, default=str)[:4000]
        name_l = (name or "").strip()
        paths = _extract_paths(args, result)
        if name_l in _WRITE_TOOLS:
            effect: str = "write"
        elif name_l in _READ_TOOLS:
            effect = "read"
        else:
            effect = "side_effect" if name_l else "unknown"
        # bash without path signals → side_effect not auto write-set
        if name_l == "bash_exec" and not paths:
            effect = "side_effect"

        with self._lock:
            sg = None
            if self.active_subgoal_id:
                sg = next(
                    (s for s in self.subgoals if s.id == self.active_subgoal_id), None
                )
            if sg is None and name_l:
                # Auto-open soft subgoal on first tool of a chain
                if any(s.status == "open" for s in self.subgoals):
                    sg = next(s for s in reversed(self.subgoals) if s.status == "open")
                    self.active_subgoal_id = sg.id
            if sg is not None:
                sg.touch_tool(tool_call_id, name_l, paths[0] if paths else "")

            txn = ToolTxn(
                tool_call_id=tool_call_id or "",
                name=name_l,
                args_digest=_digest(args_s),
                result_digest=_digest(result or ""),
                effect=effect,  # type: ignore[arg-type]
                artifacts=paths,
                outcome="ok" if success else "err",
                claim=(claim or "")[:400],
                result_preview=(result or "")[:240],
                offload_path=offload_path,
                subgoal_id=sg.id if sg else None,
                chars=len(result or ""),
            )
            self.tool_txns.append(txn)
            if len(self.tool_txns) > 240:
                self.tool_txns = self.tool_txns[-240:]

            if effect == "write" and success and paths:
                for p in paths:
                    self.write_set[p] = WriteEntry(
                        path=p, tool=name_l, txn_id=txn.id, verified=False
                    )
                    self._add_node_unlocked(
                        kind="artifact",
                        text=f"Touched {p}",
                        path=p,
                        source="tool",
                        confidence=0.75,
                    )

            if success and claim:
                self._add_node_unlocked(
                    kind="fact", text=claim, source="tool", confidence=0.7
                )

            self.updated_at = _now()
            # Persist every few tools to avoid disk thrash
            if len(self.tool_txns) % 4 == 0:
                self.save()
            return txn

    def verify_write(self, path: str, *, how: str = "manual") -> bool:
        with self._lock:
            key = path
            # fuzzy match
            ent = self.write_set.get(key)
            if ent is None:
                for k, v in self.write_set.items():
                    if k.replace("\\", "/").lower() == path.replace("\\", "/").lower():
                        ent = v
                        key = k
                        break
            if ent is None:
                return False
            ent.verified = True
            ent.verified_how = (how or "manual")[:80]
            ent.updated_at = _now()
            self.updated_at = _now()
            self.save()
            return True

    def get_txn(self, txn_id: str = "", *, tool_call_id: str = "") -> ToolTxn | None:
        with self._lock:
            if txn_id:
                for t in reversed(self.tool_txns):
                    if t.id == txn_id:
                        return t
            if tool_call_id:
                for t in reversed(self.tool_txns):
                    if t.tool_call_id == tool_call_id:
                        return t
            return None

    def recall_txn_body(self, txn_id: str = "", *, tool_call_id: str = "") -> str:
        txn = self.get_txn(txn_id, tool_call_id=tool_call_id)
        if txn is None:
            return "No tool transaction found for that id."
        if txn.offload_path:
            p = Path(txn.offload_path)
            if p.is_file():
                with suppress(Exception):
                    body = p.read_text(encoding="utf-8", errors="replace")
                    return (
                        f"Tool txn {txn.id} ({txn.name}) offloaded body "
                        f"({len(body)} chars):\n{body[:120_000]}"
                    )
        # Fall back to preview + metadata
        return (
            f"Tool txn {txn.id}\n"
            f"name={txn.name} outcome={txn.outcome} effect={txn.effect}\n"
            f"artifacts={txn.artifacts}\n"
            f"preview:\n{txn.result_preview}\n"
            f"(full body not offloaded — re-run tool if needed)"
        )

    def unverified_writes(self) -> list[WriteEntry]:
        with self._lock:
            return [w for w in self.write_set.values() if not w.verified]

    # --- Phase C: epistemic graph ------------------------------------
    def _add_node_unlocked(
        self,
        *,
        kind: str,
        text: str,
        why: str = "",
        rejected: str = "",
        path: str = "",
        source: str = "agent",
        confidence: float = 0.8,
        status: str = "active",
    ) -> GraphNode:
        text = (text or "").strip()
        if not text:
            return GraphNode(kind=kind, text="")  # type: ignore[arg-type]
        # Dedupe by kind+text lower
        key_l = f"{kind}:{text.lower()[:200]}"
        for n in self.nodes.values():
            if f"{n.kind}:{n.text.lower()[:200]}" == key_l and n.status == "active":
                n.confidence = max(n.confidence, confidence)
                n.last_confirmed_at = _now()
                n.touch()
                if why and not n.why:
                    n.why = why[:400]
                if path and not n.path:
                    n.path = path
                return n
        node = GraphNode(
            kind=kind,  # type: ignore[arg-type]
            text=text[:500],
            why=why[:400],
            rejected=rejected[:400],
            path=path[:400],
            source=source,
            confidence=float(confidence),
            status=status,  # type: ignore[arg-type]
        )
        self.nodes[node.id] = node
        if len(self.nodes) > 220:
            # Drop oldest decayed/active low-confidence
            ordered = sorted(self.nodes.values(), key=lambda n: n.updated_at)
            for old in ordered[:40]:
                self.nodes.pop(old.id, None)
        return node

    def add_node(
        self,
        *,
        kind: str,
        text: str,
        why: str = "",
        rejected: str = "",
        path: str = "",
        source: str = "agent",
        confidence: float = 0.8,
    ) -> GraphNode:
        with self._lock:
            n = self._add_node_unlocked(
                kind=kind,
                text=text,
                why=why,
                rejected=rejected,
                path=path,
                source=source,
                confidence=confidence,
            )
            self.updated_at = _now()
            self.save()
            return n

    def add_edge(self, src: str, dst: str, rel: str = "related") -> GraphEdge:
        with self._lock:
            e = GraphEdge(src=src, dst=dst, rel=rel)  # type: ignore[arg-type]
            self.edges.append(e)
            if len(self.edges) > 400:
                self.edges = self.edges[-400:]
            self.save()
            return e

    def graph_active(self, kind: str | None = None) -> list[GraphNode]:
        with self._lock:
            nodes = [
                n
                for n in self.nodes.values()
                if n.status in ("active", "open") and (kind is None or n.kind == kind)
            ]
            nodes.sort(key=lambda n: n.updated_at, reverse=True)
            return nodes

    def project_brief_fields(self) -> dict[str, Any]:
        """Project graph → fields suitable for SessionBrief.merge_summary."""
        with self._lock:
            decisions = [
                n.text
                for n in self.graph_active("decision")[:12]
            ]
            decision_records = [
                {"decision": n.text, "why": n.why, "rejected": n.rejected}
                for n in self.graph_active("decision")[:8]
            ]
            artifacts = [
                n.path or n.text
                for n in self.graph_active("artifact")[:20]
                if (n.path or n.text)
            ]
            commitments = [n.text for n in self.graph_active("commitment")[:8]
            ]
            open_hyp = [
                n.text for n in self.nodes.values() if n.kind == "hypothesis" and n.status == "open"
            ][:8]
            facts = [n.text for n in self.graph_active("fact")[:10]]
            sg = self.active_subgoal()
            intent = sg.title if sg else ""
            open_tasks = []
            if sg and sg.status == "open":
                open_tasks.append(f"[subgoal] {sg.title}")
            open_tasks.extend(f"[hypothesis] {h}" for h in open_hyp)
            return {
                "intent": intent,
                "decisions": decisions,
                "decision_records": decision_records,
                "artifacts": artifacts,
                "commitments": commitments,
                "facts": facts,
                "open_tasks": open_tasks,
                "blockers": open_hyp,
            }

    def apply_graph_to_brief(self, brief: Any) -> None:
        """Merge epistemic projection into SessionBrief."""
        if brief is None:
            return
        fields = self.project_brief_fields()
        with suppress(Exception):
            if fields.get("intent") and not getattr(brief, "intent", None):
                brief.intent = fields["intent"]
            for a in fields.get("artifacts") or []:
                with suppress(Exception):
                    brief.add_artifact(str(a))
            for d in fields.get("decisions") or []:
                if d and d not in (brief.decisions or []):
                    brief.decisions = list(brief.decisions or []) + [d]
            brief.decisions = list(brief.decisions or [])[-20:]
            for rec in fields.get("decision_records") or []:
                if isinstance(rec, dict):
                    with suppress(Exception):
                        brief.add_decision_record(
                            str(rec.get("decision") or ""),
                            why=str(rec.get("why") or ""),
                            rejected=str(rec.get("rejected") or ""),
                        )
            for c in fields.get("commitments") or []:
                if c and c not in (brief.user_constraints or []):
                    brief.user_constraints = list(brief.user_constraints or []) + [c]
            brief.user_constraints = list(brief.user_constraints or [])[-12:]
            # Open tasks from subgoals/hypotheses
            ot = list(fields.get("open_tasks") or [])
            if ot:
                brief.open_tasks = ot[:15]
            with suppress(Exception):
                brief.touch()

    def graph_quality_coverage(self, paths: list[str], decisions: list[str]) -> dict[str, Any]:
        """Score whether graph retains key paths/decisions (for quality gate)."""
        blob = " ".join(
            f"{n.kind}:{n.text}:{n.path}" for n in self.nodes.values() if n.status == "active"
        ).lower().replace("\\", "/")
        kept_p, lost_p = [], []
        for p in paths:
            base = p.replace("\\", "/").rsplit("/", 1)[-1].lower()
            full = p.replace("\\", "/").lower()
            if full in blob or (base and len(base) > 2 and base in blob):
                kept_p.append(p)
            else:
                lost_p.append(p)
        kept_d, lost_d = [], []
        for d in decisions:
            words = [w for w in d.lower().split() if len(w) > 3][:5]
            if not words or sum(1 for w in words if w in blob) >= max(1, len(words) // 2):
                kept_d.append(d)
            else:
                lost_d.append(d)
        n_p, n_d = len(paths), len(decisions)
        score = (
            0.65 * (len(kept_p) / n_p if n_p else 1.0)
            + 0.35 * (len(kept_d) / n_d if n_d else 1.0)
        )
        if not n_p and not n_d:
            score = 0.4
        return {
            "score": round(min(1.0, max(0.0, score)), 3),
            "paths_kept": len(kept_p),
            "paths_lost": len(lost_p),
            "decisions_kept": len(kept_d),
            "decisions_lost": len(lost_d),
            "node_count": len(self.nodes),
        }

    # --- Phase D: prospective + dual stream --------------------------
    def add_prospective(
        self,
        text: str,
        *,
        trigger: str = "manual",
        tool_name: str = "",
        project_path: str = "",
        max_fires: int = 3,
    ) -> ProspectiveItem:
        with self._lock:
            item = ProspectiveItem(
                text=(text or "").strip()[:400],
                trigger=trigger,  # type: ignore[arg-type]
                tool_name=(tool_name or "").strip(),
                project_path=(project_path or "").strip(),
                max_fires=max(1, int(max_fires)),
            )
            self.prospective.append(item)
            if len(self.prospective) > 80:
                self.prospective = self.prospective[-80:]
            self.save()
            return item

    def fire_prospectives(
        self,
        trigger: str,
        *,
        tool_name: str = "",
        project_path: str = "",
    ) -> list[ProspectiveItem]:
        fired: list[ProspectiveItem] = []
        with self._lock:
            for item in self.prospective:
                if not item.armed or item.fired_count >= item.max_fires:
                    continue
                if item.trigger != trigger:
                    continue
                if trigger == "tool_name" and item.tool_name:
                    if item.tool_name != tool_name:
                        continue
                if item.project_path and project_path:
                    if item.project_path.replace("\\", "/").lower() not in project_path.replace(
                        "\\", "/"
                    ).lower():
                        continue
                item.fired_count += 1
                item.last_fired_at = _now()
                if item.fired_count >= item.max_fires:
                    item.armed = False
                fired.append(item)
            if fired:
                self.save()
        return fired

    def dual_stream_blocks(self, *, max_chars_each: int = 900) -> tuple[str, str]:
        """Return (partner_stream, project_stream) for separate inject budgets."""
        with self._lock:
            partner_lines = ["[Partner stream — user/preferences/commitments]"]
            for n in self.graph_active("commitment")[:6]:
                partner_lines.append(f"- Commitment: {n.text}")
            for n in self.graph_active("fact")[:5]:
                if n.source in ("user", "partner") or "prefer" in n.text.lower():
                    partner_lines.append(f"- {n.text}")
            for p in self.prospective:
                if p.armed and p.trigger in ("session_start", "manual"):
                    partner_lines.append(f"- Reminder: {p.text}")
            if len(partner_lines) <= 1:
                partner = ""
            else:
                partner = "\n".join(partner_lines)
                if len(partner) > max_chars_each:
                    partner = partner[: max_chars_each - 1] + "…"

            project_lines = ["[Project stream — work state / write-set / subgoals]"]
            sg = None
            if self.active_subgoal_id:
                sg = next((s for s in self.subgoals if s.id == self.active_subgoal_id), None)
            if sg and sg.status == "open":
                project_lines.append(f"- Active subgoal: {sg.title} ({sg.id})")
                if sg.paths:
                    project_lines.append(f"- Subgoal paths: {', '.join(sg.paths[-6:])}")
            open_count = sum(1 for s in self.subgoals if s.status == "open")
            closed_count = sum(1 for s in self.subgoals if s.status == "closed")
            if open_count or closed_count:
                project_lines.append(
                    f"- Subgoals: {open_count} open, {closed_count} closed"
                )
            unverified = [w for w in self.write_set.values() if not w.verified]
            if unverified:
                project_lines.append("- Unverified writes (re-read/test before claiming done):")
                for w in unverified[-8:]:
                    project_lines.append(f"  · {w.path} via {w.tool}")
            for n in self.graph_active("decision")[:5]:
                line = f"- Decision: {n.text}"
                if n.why:
                    line += f" (why: {n.why[:80]})"
                project_lines.append(line)
            for n in self.graph_active("artifact")[:8]:
                project_lines.append(f"- Artifact: {n.path or n.text}")
            recent_tx = self.tool_txns[-5:]
            if recent_tx:
                project_lines.append("- Recent tool txns:")
                for t in recent_tx:
                    project_lines.append(
                        f"  · {t.id} {t.name} {t.outcome} "
                        f"{','.join(t.artifacts[:2])}"
                    )
            if len(project_lines) <= 1:
                project = ""
            else:
                project = "\n".join(project_lines)
                if len(project) > max_chars_each:
                    project = project[: max_chars_each - 1] + "…"
            return partner, project

    # --- Phase E: continuity core tick -------------------------------
    def continuity_tick(self, *, brief: Any = None) -> dict[str, Any]:
        """Cheap deterministic maintenance (no network). Called by Continuity Core."""
        with self._lock:
            self.continuity_passes += 1
            # Decay unconfirmed low-confidence facts older than many passes
            for n in list(self.nodes.values()):
                if n.kind == "fact" and n.confidence < 0.55 and n.source == "agent":
                    age = (_now() - n.updated_at).total_seconds()
                    if age > 86_400 * 3:
                        n.status = "decayed"
                        n.touch()
            # Sync graph → brief
            if brief is not None:
                self.apply_graph_to_brief(brief)
            self.updated_at = _now()
            if self.continuity_passes % 2 == 0:
                self.save()
            return {
                "passes": self.continuity_passes,
                "nodes": len(self.nodes),
                "open_subgoals": sum(1 for s in self.subgoals if s.status == "open"),
                "unverified_writes": len([w for w in self.write_set.values() if not w.verified]),
                "txns": len(self.tool_txns),
            }

    def status_public(self) -> dict[str, Any]:
        with self._lock:
            sg = self.active_subgoal()
            return {
                "session_id": self.session_id,
                "active_subgoal": sg.title if sg else None,
                "active_subgoal_id": self.active_subgoal_id,
                "open_subgoals": sum(1 for s in self.subgoals if s.status == "open"),
                "tool_txns": len(self.tool_txns),
                "unverified_writes": len([w for w in self.write_set.values() if not w.verified]),
                "graph_nodes": len(self.nodes),
                "prospective_armed": sum(1 for p in self.prospective if p.armed),
                "continuity_passes": self.continuity_passes,
            }


def ensure_partner_state(runtime: Any) -> PartnerState:
    """Get or create PartnerState on runtime (process registry by session).

    Never return another session's PartnerState when ``runtime._session_id``
    has changed (shared BasicRuntime across desktop tabs).
    """
    sid = str(
        getattr(runtime, "_session_id", None)
        or getattr(getattr(runtime, "config", None), "session_id", None)
        or ""
    )
    key = sid or f"anon-{id(runtime)}"

    existing = getattr(runtime, "_partner_state", None)
    if isinstance(existing, PartnerState):
        esid = str(getattr(existing, "session_id", "") or "")
        # Accept only exact session match (or anon key for unscoped turns)
        if esid == key or (not sid and esid.startswith("anon-")):
            return existing
        # Foreign session — detach
        runtime._partner_state = None

    home = None
    with suppress(Exception):
        home = getattr(getattr(runtime, "config", None), "home_dir", None)
    if home is None:
        with suppress(Exception):
            from remedy.interfaces.config import load_config

            home = (load_config() or {}).get("home_dir")
    with _registry_lock:
        st = _registry.get(key)
        if st is None:
            st = PartnerState(session_id=key, home=home)
            st.load()
            _registry[key] = st
        # Cap registry
        if len(_registry) > 64:
            for k in list(_registry.keys())[:16]:
                if k != key:
                    _registry.pop(k, None)
                    if len(_registry) <= 64:
                        break
    runtime._partner_state = st
    return st


def partner_context_blocks(runtime: Any) -> list[str]:
    """Dual-stream + write-set blocks for system context."""
    blocks: list[str] = []
    with suppress(Exception):
        st = ensure_partner_state(runtime)
        partner, project = st.dual_stream_blocks()
        if partner:
            blocks.append(partner)
        if project:
            blocks.append(project)
        # Fire session_start prospectives once per process-session
        if not getattr(runtime, "_prospective_session_fired", False):
            runtime._prospective_session_fired = True
            fired = st.fire_prospectives("session_start")
            if fired:
                lines = ["[Prospective memory — due now]"]
                for item in fired:
                    lines.append(f"- {item.text}")
                blocks.append("\n".join(lines))
    return blocks


def record_tool_from_runtime(
    runtime: Any,
    *,
    name: str,
    args: dict[str, Any] | None = None,
    result: str = "",
    success: bool = True,
    tool_call_id: str = "",
) -> ToolTxn | None:
    with suppress(Exception):
        st = ensure_partner_state(runtime)
        # Detect offload handle in result
        offload = None
        if result and "tool output offloaded" in result and "→" in result:
            with suppress(Exception):
                # "... → C:\path]"
                part = result.split("→", 1)[-1]
                path = part.split("]", 1)[0].strip().split("\n", 1)[0].strip()
                if path and len(path) < 400:
                    offload = path
        txn = st.record_tool(
            name=name,
            args=args or {},
            result=result or "",
            success=success,
            tool_call_id=tool_call_id,
            offload_path=offload,
        )
        # Prospective: tool_success / tool_name
        if success:
            st.fire_prospectives("tool_success", tool_name=name)
            st.fire_prospectives("tool_name", tool_name=name)
        return txn
    return None
