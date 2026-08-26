"""Living Machine Map — session-scoped world model of this PC.

Consult first; re-snapshot only on invalidation. No secrets stored.
"""

from __future__ import annotations

import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MapSlice:
    """One cached observation of the machine."""

    kind: str  # browser | window | desktop | file | root
    key: str
    data: dict[str, Any]
    ts: float = field(default_factory=time.time)
    ttl_s: float = 30.0

    def fresh(self, now: float | None = None) -> bool:
        t = now if now is not None else time.time()
        return (t - self.ts) <= self.ttl_s

    def to_public(self) -> dict[str, Any]:
        # Never dump large blobs; only small scalars
        safe = {
            k: v
            for k, v in (self.data or {}).items()
            if isinstance(v, (str, int, float, bool, type(None)))
            and k.lower() not in ("password", "token", "cookie", "authorization")
        }
        if "url" in (self.data or {}):
            url = str(self.data.get("url") or "")[:500]
            # Never expose userinfo credentials in map public views
            try:
                from remedy.core.metabolism.cua_macros import _sanitize_url

                url = _sanitize_url(url)
            except Exception:
                if "?" in url:
                    url = url.split("?", 1)[0]
                if "@" in url and "://" in url:
                    try:
                        pre, rest = url.split("://", 1)
                        if "@" in rest:
                            rest = rest.split("@", 1)[1]
                            url = f"{pre}://{rest}"
                    except Exception:
                        pass
            safe["url"] = url[:500]
        if "title" in (self.data or {}):
            safe["title"] = str(self.data.get("title") or "")[:200]
        if "refs" in (self.data or {}) and isinstance(self.data["refs"], list):
            safe["ref_count"] = len(self.data["refs"])
        return {
            "kind": self.kind,
            "key": self.key,
            "data": safe,
            "ts": self.ts,
            "ttl_s": self.ttl_s,
            "fresh": self.fresh(),
        }


@dataclass
class MachineMap:
    session_id: str = ""
    slices: dict[str, MapSlice] = field(default_factory=dict)
    work_roots: list[str] = field(default_factory=list)
    last_invalidate: float = 0.0
    hit_count: int = 0
    miss_count: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _k(self, kind: str, key: str) -> str:
        return f"{kind}:{key}"

    def put(
        self,
        kind: str,
        key: str,
        data: dict[str, Any],
        *,
        ttl_s: float = 30.0,
    ) -> MapSlice:
        clean = dict(data or {})
        # Scrub secrets at admit time so TTL cache never holds credentials.
        # Fail closed: crude strip if sanitize helper is unavailable.
        if "url" in clean and clean["url"]:
            u = str(clean["url"])
            try:
                from remedy.core.metabolism.cua_macros import _sanitize_url

                clean["url"] = _sanitize_url(u)
            except Exception:
                # Never store raw userinfo/query on failure
                if "?" in u:
                    u = u.split("?", 1)[0]
                if "#" in u:
                    u = u.split("#", 1)[0]
                if "@" in u:
                    try:
                        pre, rest = u.split("://", 1)
                        if "@" in rest:
                            u = f"{pre}://{rest.rsplit('@', 1)[-1]}"
                    except Exception:
                        u = u.split("@", 1)[-1]
                clean["url"] = u[:200]
        for secret_k in ("password", "token", "cookie", "authorization", "api_key"):
            clean.pop(secret_k, None)
        sl = MapSlice(kind=kind, key=key, data=clean, ttl_s=ttl_s)
        with self._lock:
            self.slices[self._k(kind, key)] = sl
            # Cap
            if len(self.slices) > 64:
                # drop oldest
                ordered = sorted(self.slices.items(), key=lambda kv: kv[1].ts)
                for old_k, _ in ordered[: max(0, len(self.slices) - 64)]:
                    self.slices.pop(old_k, None)
        return sl

    def get(self, kind: str, key: str = "default") -> MapSlice | None:
        with self._lock:
            sl = self.slices.get(self._k(kind, key))
            if sl is None:
                self.miss_count += 1
                return None
            if not sl.fresh():
                self.miss_count += 1
                return None
            self.hit_count += 1
            return sl

    def invalidate(self, kind: str | None = None) -> None:
        with self._lock:
            self.last_invalidate = time.time()
            if kind is None:
                self.slices.clear()
                return
            dead = [k for k, s in self.slices.items() if s.kind == kind]
            for k in dead:
                self.slices.pop(k, None)

    def set_work_roots(self, roots: list[str]) -> None:
        with self._lock:
            self.work_roots = [str(r) for r in (roots or []) if r][:32]

    def note_file_touch(self, path: str) -> None:
        if not path:
            return
        self.put("file", path[:200], {"path": path[:500], "touched": True}, ttl_s=120.0)

    def note_browser(
        self,
        *,
        url: str = "",
        title: str = "",
        settled: bool = False,
        ref_count: int = 0,
    ) -> None:
        self.put(
            "browser",
            "rail",
            {
                "url": (url or "")[:500],
                "title": (title or "")[:200],
                "settled": settled,
                "ref_count": ref_count,
            },
            ttl_s=20.0,
        )

    def note_desktop_windows(self, count: int, titles: list[str] | None = None) -> None:
        self.put(
            "desktop",
            "windows",
            {
                "count": int(count),
                "titles": [str(t)[:80] for t in (titles or [])[:12]],
            },
            ttl_s=15.0,
        )

    def note_organ(self, name: str, data: dict[str, Any], *, ttl_s: float = 45.0) -> None:
        """Self-organs (RMB, vision, serve) — her body, not the owner's desk."""
        key = str(name or "").strip().lower()[:32]
        if not key:
            return
        self.put("organ", key, dict(data or {}), ttl_s=ttl_s)

    def refresh_house_organs(self, home: Any = None) -> None:
        """Cheap no-wake snapshot of her own doors. Does not start processes."""
        if home is None:
            return
        existing = self.get("organ", "rmb")
        if existing is not None:
            return
        rmb: dict[str, Any] = {"running": False, "ready": False}
        with suppress(Exception):
            from pathlib import Path as _P

            from remedy.runtime.rmb.config import load_rmb_json, merge_state
            from remedy.runtime.rmb.service import is_running

            state = merge_state(load_rmb_json(home))
            running = bool(is_running(home, force=False, require_http=False))
            model = ""
            if state.get("model_path"):
                model = _P(str(state["model_path"])).stem
            if not model:
                model = str(state.get("model_id") or "")
            rmb = {
                "running": running,
                "ready": running,
                "enabled": bool(state.get("enabled", True)),
                "auto_start": bool(state.get("auto_start", False)),
                "model": model[:80],
                "profile": str(state.get("profile") or "autofit")[:24],
                "ctx": int(state.get("ctx_size") or 0),
                "port": int(state.get("port") or 8787),
                "vision_suspended": bool(state.get("vision_suspended")),
            }
        self.note_organ("rmb", rmb, ttl_s=45.0)
        vis: dict[str, Any] = {}
        cfg: dict[str, Any] = {}
        with suppress(Exception):
            from pathlib import Path as _P

            from remedy.interfaces.config import get_home_dir, load_config

            live = str(_P(get_home_dir()).expanduser().resolve())
            here = str(_P(home).expanduser().resolve())
            if here == live:
                raw = load_config() or {}
                if isinstance(raw, dict):
                    cfg = raw
            vis["enabled"] = bool(cfg.get("vision_enabled", True)) if cfg else True
        with suppress(Exception):
            from remedy.vision.runtime import is_running as vision_running

            vis["running"] = bool(vision_running(home, force=False, require_http=False))
        self.note_organ("vision", vis, ttl_s=45.0)
        with suppress(Exception):
            from remedy.core import vault

            items = vault.vault_list(home) or []
            self.note_organ("vault", {"count": len(items), "present": True}, ttl_s=90.0)
        chans: list[str] = []
        with suppress(Exception):
            raw_ch = cfg.get("enabled_channels") or []
            if isinstance(raw_ch, list):
                chans = [
                    str(c).strip().lower()
                    for c in raw_ch
                    if str(c).strip() and str(c).strip().lower() not in ("cli", "web", "api")
                ]
        if chans:
            self.note_organ("channels", {"names": chans[:8]}, ttl_s=90.0)
        with suppress(Exception):
            from remedy.execution.host.stretch import load_census

            c = load_census(home)
            if c is not None and c.stretched_at:
                open_d = [k for k, v in (c.doors or {}).items() if v]
                self.note_organ(
                    "pc",
                    {
                        "os": (c.os_name or "")[:24],
                        "ram_mb": int(c.ram_total_mb or 0),
                        "gpu": (c.gpu_name or "")[:40],
                        "doors_open": open_d[:8],
                    },
                    ttl_s=180.0,
                )

    def organ_hint(self) -> str:
        """One line: her body + this PC. Empty if organs were never refreshed."""
        with self._lock:
            bits: list[str] = []
            pc = self.slices.get(self._k("organ", "pc"))
            if pc and pc.fresh():
                d = pc.data or {}
                if d.get("os"):
                    bits.append(str(d["os"]))
                ram = int(d.get("ram_mb") or 0)
                if ram:
                    bits.append(f"{ram / 1024:.0f}G")
                if d.get("gpu"):
                    bits.append(str(d["gpu"])[:28])
            r = self.slices.get(self._k("organ", "rmb"))
            if r and r.fresh():
                d = r.data or {}
                state = "ready" if d.get("ready") else ("up" if d.get("running") else "stopped")
                bits.append(f"RMB={state}")
                if d.get("model"):
                    bits.append(str(d.get("model") or "")[:36])
                if d.get("vision_suspended"):
                    bits.append("vision_suspended")
            v = self.slices.get(self._k("organ", "vision"))
            if v and v.fresh():
                if v.data.get("running"):
                    bits.append("vision=up")
                elif v.data.get("enabled") is False:
                    bits.append("vision=off")
            vault = self.slices.get(self._k("organ", "vault"))
            if vault and vault.fresh():
                n = int(vault.data.get("count") or 0)
                bits.append(f"vault={n}")
            ch = self.slices.get(self._k("organ", "channels"))
            if ch and ch.fresh():
                names = ch.data.get("names") or []
                if isinstance(names, list) and names:
                    bits.append("ch=" + ",".join(str(n) for n in names[:4]))
            if not bits:
                return ""
            return "[House] " + " · ".join(bits)

    def system_hint(self) -> str:
        """Compact orientation for the model — never secrets."""
        with self._lock:
            parts: list[str] = []
            if self.work_roots:
                parts.append("work_roots=" + "; ".join(self.work_roots[:4]))
            b = self.slices.get(self._k("browser", "rail"))
            if b and b.fresh():
                u = str(b.data.get("url") or "")
                if u:
                    parts.append(f"browser_url={u[:120]}")
                if b.data.get("settled"):
                    parts.append("browser_settled=1")
            d = self.slices.get(self._k("desktop", "windows"))
            if d and d.fresh():
                parts.append(f"windows={d.data.get('count', '?')}")
            r = self.slices.get(self._k("organ", "rmb"))
            if r and r.fresh():
                rd = r.data or {}
                st = "ready" if rd.get("ready") else ("up" if rd.get("running") else "stopped")
                parts.append(f"rmb={st}")
                if rd.get("model"):
                    parts.append(f"rmb_model={str(rd.get('model'))[:40]}")
            if not parts:
                return ""
            return "[Machine map] " + " | ".join(parts)

    def snapshot(self, *, lean: bool = False) -> dict[str, Any]:
        """Map stats. *lean* skips per-slice public copies."""
        with self._lock:
            total = self.hit_count + self.miss_count
            out: dict[str, Any] = {
                "session_id": self.session_id,
                "slice_count": len(self.slices),
                "work_roots": list(self.work_roots)[:8],
                "hit_count": self.hit_count,
                "miss_count": self.miss_count,
                "hit_rate": round(self.hit_count / total, 4) if total else 0.0,
            }
            if not lean:
                out["slices"] = [
                    s.to_public() for s in list(self.slices.values())[-12:]
                ]
            return out


_maps: dict[str, MachineMap] = {}
_lock = threading.Lock()


def get_machine_map(session_id: str | None = None) -> MachineMap:
    from remedy.core.metabolism.session_registry import registry_get

    key = (session_id or "").strip() or "_default"
    with _lock:
        return registry_get(_maps, key, lambda: MachineMap(session_id=key))


def reset_machine_map(session_id: str | None = None) -> None:
    key = (session_id or "").strip() or "_default"
    with _lock:
        _maps.pop(key, None)
