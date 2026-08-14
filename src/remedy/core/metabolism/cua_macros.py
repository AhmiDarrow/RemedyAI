"""CUA muscle memory — extract short macros from successful computer-use chains.

Macros are local procedure hints (not multi-agent). Secrets never stored.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from remedy.core.metabolism.redact import looks_like_secret_text

# Hard cap on stored macros (evict lowest hits first when exceeded).
MAX_CUA_MACROS = 64
# Steps kept per macro after clean (navigate→act chains stay short).
MAX_CUA_MACRO_STEPS = 8


def _sanitize_url(val: str) -> str:
    """Strip query string and userinfo (user:pass@host) from stored URLs."""
    v = (val or "")[:200]
    if not v:
        return v
    try:
        # Ensure parseable
        raw = v if "://" in v else f"https://{v}"
        p = urlparse(raw)
        # Drop credentials
        host = p.hostname or ""
        netloc = f"{host}:{p.port}" if p.port else host
        # Keep path; drop query/fragment (tokens often live there)
        cleaned = urlunparse((p.scheme or "https", netloc, p.path or "", "", "", ""))
        # If input had no scheme and we added https, strip it back only when original lacked it
        if "://" not in v and cleaned.startswith("https://"):
            cleaned = cleaned[len("https://") :]
        return cleaned[:200]
    except Exception:
        if "?" in v:
            v = v.split("?", 1)[0]
        if "@" in v:
            # crude userinfo strip: scheme://user:pass@host → scheme://host
            try:
                pre, rest = v.split("://", 1)
                if "@" in rest:
                    rest = rest.split("@", 1)[1]
                    v = f"{pre}://{rest}"
            except Exception:
                pass
        return v[:200]


@dataclass
class CuaMacro:
    name: str
    steps: list[dict[str, Any]]
    hits: int = 1
    last_ts: float = field(default_factory=time.time)

    def to_public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "steps": list(self.steps)[:12],
            "hits": self.hits,
            "last_ts": self.last_ts,
        }


@dataclass
class CuaMacroStore:
    macros: dict[str, CuaMacro] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _persist_sig: tuple[int, int] | None = field(default=None, repr=False)

    def observe_chain(
        self,
        steps: list[dict[str, Any]],
        *,
        success: bool = True,
    ) -> CuaMacro | None:
        """If chain looks like a reusable navigate→act sequence, store/update."""
        if not success or len(steps) < 2:
            return None
        comp = [
            s for s in steps if str(s.get("tool") or "").startswith("computer_")
        ]
        if len(comp) < 2:
            return None
        clean_steps: list[dict[str, Any]] = []
        for s in comp[:MAX_CUA_MACRO_STEPS]:
            tool = str(s.get("tool") or "")
            raw_args = s.get("args")
            args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
            safe_args: dict[str, Any] = {}
            for k in ("url", "ref", "monitor", "button", "key"):
                if k in args and args[k] is not None:
                    val = str(args[k])[:200]
                    try:
                        # Sanitize URLs *before* secret check so userinfo/query
                        # tokens do not drop the whole navigation step.
                        if k == "url":
                            val = _sanitize_url(val)
                        if looks_like_secret_text(val):
                            continue
                    except Exception:
                        pass
                    safe_args[k] = val
            if tool == "computer_type":
                safe_args = {"text": "[omitted]"}
            clean_steps.append({"tool": tool, "args": safe_args})
        name = "→".join(str(s.get("tool") or "") for s in clean_steps[:4])
        if len(name) < 8:
            return None
        with self._lock:
            existing = self.macros.get(name)
            if existing:
                existing.hits += 1
                existing.last_ts = time.time()
                existing.steps = clean_steps
                return existing
            m = CuaMacro(name=name, steps=clean_steps)
            self.macros[name] = m
            if len(self.macros) > MAX_CUA_MACROS:
                ordered = sorted(
                    self.macros.items(), key=lambda kv: (kv[1].hits, kv[1].last_ts)
                )
                drop_n = max(1, len(self.macros) - MAX_CUA_MACROS)
                for k, _ in ordered[:drop_n]:
                    self.macros.pop(k, None)
            return m

    def top_hints(self, limit: int = 3) -> str:
        with self._lock:
            ranked = sorted(
                self.macros.values(), key=lambda m: (-m.hits, -m.last_ts)
            )[:limit]
            if not ranked:
                return ""
            lines = [
                "[CUA macros — reuse successful computer sequences when matching]"
            ]
            for m in ranked:
                lines.append(f"- {m.name} (hits={m.hits})")
            return "\n".join(lines)

    def snapshot(self, *, lean: bool = False) -> dict[str, Any]:
        with self._lock:
            count = len(self.macros)
            if lean:
                return {"count": count}
            top = sorted(self.macros.values(), key=lambda x: -x.hits)[:8]
            return {"count": count, "top": [m.to_public() for m in top]}

    def persist(self, home: Path | str | None = None) -> Path | None:
        try:
            with self._lock:
                sig = (
                    len(self.macros),
                    sum(int(m.hits) for m in self.macros.values()),
                )
                if sig == self._persist_sig:
                    return None
            root = Path(home).expanduser() if home else Path.home() / ".remedy"
            d = root / "cua_macros"
            d.mkdir(parents=True, exist_ok=True)
            path = d / "macros.json"
            with self._lock:
                data = {
                    "version": 1,
                    "macros": {k: v.to_public() for k, v in self.macros.items()},
                }
            payload = json.dumps(data, indent=2)
            fd, tmp_name = tempfile.mkstemp(prefix=".macros.", suffix=".tmp", dir=str(d))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(payload)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp_name, path)
            except Exception:
                with contextlib.suppress(Exception):
                    Path(tmp_name).unlink(missing_ok=True)
                raise
            self._persist_sig = sig
            return path
        except Exception:
            return None


_store = CuaMacroStore()


def get_cua_macros() -> CuaMacroStore:
    return _store


def reset_cua_macros() -> None:
    global _store
    _store = CuaMacroStore()
