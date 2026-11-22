"""Mind + muscle co-residency: VRAM planning, discovery, routing, scheduling.

The unified Qwen3.5-4B mind stays always-on (one llama-server). RMB and any
user-run OpenAI-compatible local server (Ollama, LM Studio, vLLM, another
llama-server) are optional muscle. This module:

* probes VRAM/RAM/CPU (via autofit.HardwareProbe)
* splits GPU layers and KV so both can live on one card when they fit
* discovers already-running local servers and does not manage their memory
* avoids starting a second copy of a model already served locally
* routes easy work to the mind, coding/heavy work to local muscle, and
  the hardest work to a frontier provider when one is configured
* serializes GPU work when the split is tight so they do not thrash

All tunables are Advanced knobs with safe ``auto`` defaults. See docs/RMB.md.
"""

from __future__ import annotations

import json
import os
import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from remedy.core.organism_knobs import resolve, resolve_bool, resolve_int
from remedy.runtime.rmb.autofit import HardwareProbe, probe_hardware
from remedy.runtime.rmb.catalog import DEFAULT_RMB_MODEL_ID, RMB_MODELS, get_model_spec
from remedy.runtime.rmb.config import DEFAULT_CHAT_PORT

logger = logging.getLogger(__name__)

# Unified mind host and the RMB host we manage ourselves — never "discover" them
# as an external muscle (that would look like a double-load of our own process).
OWN_PORTS = frozenset({8740, DEFAULT_CHAT_PORT})

# Common OpenAI-compatible / native ports. Short probes only.
DISCOVERY_TARGETS: tuple[tuple[str, int, str], ...] = (
    ("ollama", 11434, "/api/tags"),
    ("lmstudio", 1234, "/v1/models"),
    ("vllm", 8000, "/v1/models"),
    ("llama-server", 8080, "/v1/models"),
    ("openai-compat", 5000, "/v1/models"),
    ("openai-compat", 8001, "/v1/models"),
)

# Catalog / public mind weight estimates (MiB), including the frozen mmproj.
MIND_WEIGHT_Q6_MIB = 4003
MIND_WEIGHT_Q4_MIB = 3255
MIND_LAYERS = 32
# KV for Qwen3.5-4B at q8_0 is small relative to weights (~0.5–0.8 GiB / 8k slot).
MIND_KV_Q8_MIB_PER_8K_SLOT = 640

DEFAULT_HEADROOM_MIB = 768
PROBE_TIMEOUT_S = 0.15

_discovery_cache: dict[str, tuple[float, list[LocalMuscleServer]]] = {}


# ---------------------------------------------------------------------------
# Knobs
# ---------------------------------------------------------------------------

def _cfg(cfg: dict[str, Any] | None) -> dict[str, Any] | None:
    return cfg if isinstance(cfg, dict) else None


def gpu_policy(cfg: dict[str, Any] | None = None) -> str:
    if resolve_bool("mind_keep_gpu", cfg):
        return "keep_mind"
    raw = str(resolve("muscle_gpu_policy", cfg) or "auto").strip().lower()
    if raw in {"auto", "share", "yield_mind", "keep_mind"}:
        return raw
    return "auto"


def schedule_policy(cfg: dict[str, Any] | None = None) -> str:
    raw = str(resolve("muscle_schedule", cfg) or "auto").strip().lower()
    return raw if raw in {"auto", "overlap", "serialize"} else "auto"


def route_policy(cfg: dict[str, Any] | None = None) -> str:
    raw = str(resolve("muscle_route", cfg) or "auto").strip().lower()
    return raw if raw in {"auto", "mind", "rmb", "local", "frontier"} else "auto"


def discover_enabled(cfg: dict[str, Any] | None = None) -> bool:
    return resolve_bool("muscle_discover", cfg)


def headroom_mib(cfg: dict[str, Any] | None = None) -> int:
    return max(128, int(resolve_int("muscle_vram_headroom_mib", cfg) or DEFAULT_HEADROOM_MIB))


# ---------------------------------------------------------------------------
# Discovery (measure, do not manage)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LocalMuscleServer:
    kind: str
    base_url: str
    port: int
    healthy: bool
    models: tuple[str, ...] = ()
    estimated_vram_mib: int | None = None
    error: str = ""

    def to_public(self) -> dict[str, Any]:
        return asdict(self)


def _http_get_json(url: str, timeout: float = PROBE_TIMEOUT_S) -> Any | None:
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(1 << 16)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    try:
        return json.loads(raw.decode("utf-8", errors="replace") or "null")
    except ValueError:
        return None


def _names_from_payload(kind: str, data: Any) -> tuple[str, ...]:
    names: list[str] = []
    if kind == "ollama" and isinstance(data, dict):
        for row in data.get("models") or []:
            if isinstance(row, dict) and row.get("name"):
                names.append(str(row["name"]))
            elif isinstance(row, dict) and row.get("model"):
                names.append(str(row["model"]))
    elif isinstance(data, dict):
        rows = data.get("data") or data.get("models") or []
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    mid = row.get("id") or row.get("name") or row.get("model")
                    if mid:
                        names.append(str(mid))
                elif isinstance(row, str):
                    names.append(row)
    return tuple(dict.fromkeys(names))


def _estimate_vram_for_names(names: Iterable[str]) -> int | None:
    """Rough weight-size guess from a model name. None if we cannot tell."""
    total = 0
    known = False
    for name in names:
        h = name.lower()
        for mid, spec in RMB_MODELS.items():
            if mid in h or spec.filename.lower().replace(".gguf", "") in h or spec.name.lower().split("(")[0].strip().lower() in h:
                # MoE with CPU experts uses far less VRAM than the file size.
                if spec.n_cpu_moe:
                    total += 4096
                else:
                    total += int(spec.approx_gb * 1024)
                known = True
                break
        else:
            if "70b" in h or "72b" in h:
                total += 40 * 1024
                known = True
            elif "32b" in h or "35b" in h:
                total += 20 * 1024
                known = True
            elif "14b" in h or "13b" in h:
                total += 9 * 1024
                known = True
            elif "9b" in h or "8b" in h or "7b" in h:
                total += 6 * 1024
                known = True
    return total if known else None


def discover_local_servers(
    *,
    cfg: dict[str, Any] | None = None,
    extra_urls: Iterable[str] | None = None,
    timeout_s: float = PROBE_TIMEOUT_S,
) -> list[LocalMuscleServer]:
    """Probe well-known local ports. Never starts, stops, or tunes them."""
    if not discover_enabled(cfg):
        return []
    now = time.monotonic()
    cached = _discovery_cache.get("v1")
    if cached and now - cached[0] < 15.0 and extra_urls is None:
        return list(cached[1])
    found: list[LocalMuscleServer] = []
    seen_ports: set[int] = set(OWN_PORTS)
    found_lock = threading.Lock()

    def consider(kind: str, host: str, port: int, path: str) -> None:
        if port in seen_ports:
            return
        seen_ports.add(port)
        # Cheap TCP first so we do not wait on HTTP to closed ports.
        try:
            with socket.create_connection((host, port), timeout=timeout_s):
                pass
        except OSError:
            return
        url = f"http://{host}:{port}{path}"
        data = _http_get_json(url, timeout=timeout_s)
        if data is None and path != "/v1/models":
            data = _http_get_json(f"http://{host}:{port}/v1/models", timeout=timeout_s)
        if data is None:
            item = LocalMuscleServer(
                kind=kind,
                base_url=f"http://{host}:{port}/v1",
                port=port,
                healthy=False,
                error="no OpenAI-compatible models endpoint",
            )
        else:
            names = _names_from_payload(kind, data)
            item = LocalMuscleServer(
                kind=kind,
                base_url=f"http://{host}:{port}/v1",
                port=port,
                healthy=True,
                models=names,
                estimated_vram_mib=_estimate_vram_for_names(names),
            )
        with found_lock:
            found.append(item)

    jobs: list[tuple[str, str, int, str]] = [
        (kind, "127.0.0.1", port, path) for kind, port, path in DISCOVERY_TARGETS
    ]
    for raw in extra_urls or ():
        try:
            parsed = urlparse(raw if "://" in raw else "http://" + raw)
            host = parsed.hostname or "127.0.0.1"
            port = int(parsed.port or 80)
            jobs.append(("openai-compat", host, port, "/v1/models"))
        except ValueError:
            continue
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(jobs)))) as pool:
        list(pool.map(lambda j: consider(*j), jobs))
    found.sort(key=lambda s: s.port)
    if extra_urls is None:
        _discovery_cache["v1"] = (now, list(found))
    return found


def _compact_model_name(name: str) -> str:
    """Strip punctuation so catalog ids match Ollama/HF display names.

    ``qwen35-9b`` and ``qwen3.5:9b`` and ``Qwen3.5-9B-Q6_K`` all compact
    to a shared ``qwen359b`` stem.
    """
    s = (name or "").strip().lower().replace("\\", "/").split("/")[-1]
    if s.endswith(".gguf"):
        s = s[:-5]
    out = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
    return "".join(out)


def already_served(
    model_hint: str | None,
    servers: Iterable[LocalMuscleServer],
) -> LocalMuscleServer | None:
    """Return a healthy external server already hosting this (or a close) model."""
    hint = (model_hint or "").strip()
    compact = _compact_model_name(hint)
    if not compact:
        return None
    for srv in servers:
        if not srv.healthy:
            continue
        for name in srv.models:
            n = _compact_model_name(name)
            if not n:
                continue
            # Require a meaningful stem so a bare "7b" does not match everything.
            if len(compact) >= 6 and (compact in n or n in compact):
                return srv
            if compact == n:
                return srv
    return None


# ---------------------------------------------------------------------------
# VRAM budget plan
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CoresidencePlan:
    policy: str
    mode: str  # share | yield_mind | mind_cpu | muscle_cpu | external
    mind_n_gpu_layers: int
    muscle_n_gpu_layers: int
    muscle_n_cpu_moe: int
    mind_ctx: int
    muscle_ctx: int
    mind_kv: str
    muscle_kv: str
    muscle_quant_hint: str
    schedule: str  # overlap | serialize
    budget_mib: int
    reserved_external_mib: int
    mind_used_mib: int
    muscle_used_mib: int
    reason: str
    skip_rmb_start: bool = False
    reuse_base_url: str = ""
    reuse_kind: str = ""
    hardware: dict[str, Any] = field(default_factory=dict)
    muscle_flash_attn: bool = True
    muscle_spec: str = "auto"

    def to_public(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def _mind_weight_mib(cfg: dict[str, Any] | None = None) -> int:
    quant = str(resolve("mind_quant", cfg) or "auto").strip().lower()
    if quant in {"q4_k_m", "q4"}:
        return MIND_WEIGHT_Q4_MIB
    return MIND_WEIGHT_Q6_MIB


def _mind_kv_mib(slots: int, ctx: int, kv: str) -> int:
    slots = max(1, slots)
    ctx = max(2048, ctx)
    base = MIND_KV_Q8_MIB_PER_8K_SLOT * slots * (ctx / 8192)
    if kv in {"f16", "bf16"}:
        base *= 2
    elif kv == "f8_e4m3":
        base *= 0.5
    return int(base)


def _muscle_weight_mib(model_id: str | None, n_cpu_moe: int) -> int:
    spec = get_model_spec(model_id)
    if n_cpu_moe or spec.n_cpu_moe:
        # Attention / non-expert tensors only — catalog notes ~4GB for 35B-A3B.
        return 4096
    return int(spec.approx_gb * 1024)


def _muscle_kv_mib(ctx: int, kv: str, *, approx_gb: float) -> int:
    # Scale roughly with model size vs the 4B baseline.
    scale = max(1.0, approx_gb / 4.0)
    base = 800 * (ctx / 8192) * scale
    if kv in {"f16", "bf16"}:
        base *= 2
    elif kv == "f8_e4m3":
        base *= 0.5
    return int(base)


def plan_coresidence(
    *,
    cfg: dict[str, Any] | None = None,
    hardware: HardwareProbe | None = None,
    muscle_model_id: str | None = None,
    muscle_ctx: int = 8192,
    muscle_n_cpu_moe: int | None = None,
    servers: list[LocalMuscleServer] | None = None,
    rmb_running: bool = False,
) -> CoresidencePlan:
    """Decide how the always-on mind and a local muscle share this machine."""
    hw = hardware or probe_hardware()
    policy = gpu_policy(cfg)
    sched_knob = schedule_policy(cfg)
    mid = (muscle_model_id or DEFAULT_RMB_MODEL_ID).strip() or DEFAULT_RMB_MODEL_ID
    spec = get_model_spec(mid)
    moe = int(muscle_n_cpu_moe) if muscle_n_cpu_moe is not None else int(spec.n_cpu_moe or 0)
    ctx_m = 8192 if int(muscle_ctx or 0) <= 0 else int(muscle_ctx)
    slots = max(1, int(resolve_int("mind_slots", cfg) or 2))
    mind_ctx = max(2048, int(resolve_int("mind_ctx", cfg) or 8192))
    mind_kv = str(resolve("mind_kv_cache", cfg) or "q8_0").strip().lower()
    if mind_kv not in {"q8_0", "f16", "bf16", "f8_e4m3"}:
        mind_kv = "q8_0"
    muscle_kv = str(resolve("muscle_kv_cache", cfg) or "auto").strip().lower()
    if muscle_kv in {"", "auto"}:
        muscle_kv = "q8_0"
    if muscle_kv not in {"q8_0", "f16", "bf16", "f8_e4m3"}:
        muscle_kv = "q8_0"

    ext = servers if servers is not None else discover_local_servers(cfg=cfg)
    reserved = 0
    for srv in ext:
        if srv.healthy and srv.estimated_vram_mib:
            reserved += int(srv.estimated_vram_mib)

    reuse = already_served(mid, ext) or already_served(spec.filename, ext)
    head = headroom_mib(cfg)
    total = int(hw.vram_total_mb or 0)
    budget = max(0, total - reserved - head)

    mind_w = _mind_weight_mib(cfg)
    mind_kv_m = _mind_kv_mib(slots, mind_ctx, mind_kv)
    mind_need = mind_w + mind_kv_m
    muscle_w = _muscle_weight_mib(mid, moe)
    muscle_kv_m = _muscle_kv_mib(ctx_m, muscle_kv, approx_gb=spec.approx_gb if not moe else 4.0)
    muscle_need = muscle_w + muscle_kv_m

    def _plan(**kwargs: Any) -> CoresidencePlan:
        sched = kwargs.pop("schedule", None)
        if sched is None:
            tight = kwargs.get("mode") in {"yield_mind", "share"} and (
                kwargs.get("mind_used_mib", 0) + kwargs.get("muscle_used_mib", 0) > budget * 0.9
                if budget
                else kwargs.get("mode") == "share"
            )
            if sched_knob == "serialize" or (sched_knob == "auto" and tight):
                sched = "serialize"
            else:
                sched = "overlap"
        flash = resolve("muscle_flash_attn", cfg)
        spec = str(resolve("muscle_spec", cfg) or "auto").strip().lower() or "auto"
        return CoresidencePlan(
            policy=policy,
            schedule=sched,
            budget_mib=budget,
            reserved_external_mib=reserved,
            mind_ctx=mind_ctx,
            muscle_ctx=ctx_m,
            mind_kv=mind_kv,
            muscle_kv=muscle_kv,
            muscle_quant_hint="q6_k" if mid.startswith("qwen35-9b") else "q4_k_m",
            muscle_flash_attn=True if flash is None else bool(flash),
            muscle_spec=spec,
            hardware=hw.to_public() if hasattr(hw, "to_public") else {},
            **kwargs,
        )

    if reuse is not None:
        return _plan(
            mode="external",
            mind_n_gpu_layers=-1 if (hw.usable_gpu and policy != "yield_mind") else 0,
            muscle_n_gpu_layers=0,
            muscle_n_cpu_moe=moe,
            mind_used_mib=mind_need if hw.usable_gpu else 0,
            muscle_used_mib=0,
            reason=(
                f"{reuse.kind} already serves this model at {reuse.base_url}; "
                "not starting a second copy"
            ),
            skip_rmb_start=True,
            reuse_base_url=reuse.base_url,
            reuse_kind=reuse.kind,
            schedule="overlap",
        )

    if not hw.usable_gpu:
        return _plan(
            mode="mind_cpu",
            mind_n_gpu_layers=0,
            muscle_n_gpu_layers=0,
            muscle_n_cpu_moe=moe,
            mind_used_mib=0,
            muscle_used_mib=0,
            reason="no usable GPU; mind and muscle stay on CPU",
            schedule="overlap",
        )

    both = mind_need + muscle_need
    if policy != "yield_mind" and both <= budget:
        return _plan(
            mode="share",
            mind_n_gpu_layers=-1,
            muscle_n_gpu_layers=-1,
            muscle_n_cpu_moe=moe,
            mind_used_mib=mind_need,
            muscle_used_mib=muscle_need,
            reason=(
                f"both fit on the GPU ({mind_need}+{muscle_need} MiB "
                f"≤ {budget} MiB budget)"
            ),
            schedule="overlap",
        )

    # MoE experts live in RAM, so the GPU-resident slice can still share.
    muscle_moe_need = 4096 + muscle_kv_m
    can_moe = moe or spec.n_cpu_moe
    if policy != "yield_mind" and can_moe and (mind_need + muscle_moe_need) <= budget:
        return _plan(
            mode="share",
            mind_n_gpu_layers=-1,
            muscle_n_gpu_layers=-1,
            muscle_n_cpu_moe=max(moe, spec.n_cpu_moe, 1),
            mind_used_mib=mind_need,
            muscle_used_mib=muscle_moe_need,
            reason="mind stays on GPU; MoE experts offload to CPU so both fit",
            schedule="overlap",
        )

    # Auto: if the muscle cannot sit fully beside the mind but would fit
    # alone, yield the mind (CPU + mmap) so the muscle runs at full GPU speed.
    # A 12 GB card + default 9B Q6 takes this path; 24 GB still shares above.
    if policy == "auto" and muscle_need <= budget:
        return _plan(
            mode="yield_mind",
            mind_n_gpu_layers=0,
            muscle_n_gpu_layers=-1,
            muscle_n_cpu_moe=moe,
            mind_used_mib=0,
            muscle_used_mib=min(budget, muscle_need),
            reason=(
                "muscle fits on the GPU alone but not beside the mind; "
                "mind yields to CPU with mmap so the muscle can have the card. "
                "Turn on mind_keep_gpu or muscle_gpu_policy=share to keep "
                "the mind on the GPU instead."
            ),
            schedule="overlap",
        )

    if policy in {"keep_mind", "share"}:
        # Partial GPU offload for the dense muscle; mind keeps all layers.
        leftover = max(0, budget - mind_need - muscle_kv_m)
        frac = leftover / max(muscle_w, 1)
        # Heuristic layer count: 9B ~36 layers; unknown models get a fraction of 40.
        layers = max(4, int(36 * min(1.0, frac)))
        if policy == "keep_mind" and leftover < 1024:
            return _plan(
                mode="muscle_cpu",
                mind_n_gpu_layers=-1,
                muscle_n_gpu_layers=0,
                muscle_n_cpu_moe=moe,
                mind_used_mib=mind_need,
                muscle_used_mib=0,
                reason="mind_keep_gpu / keep_mind: muscle runs on CPU so the mind stays on the GPU",
                schedule="overlap",
            )
        return _plan(
            mode="share",
            mind_n_gpu_layers=-1,
            muscle_n_gpu_layers=layers,
            muscle_n_cpu_moe=moe,
            mind_used_mib=mind_need,
            muscle_used_mib=leftover + muscle_kv_m,
            reason=(
                f"mind keeps the GPU; muscle offloads to {layers} GPU layers "
                f"so a {mind_need}+partial split fits the {budget} MiB budget"
            ),
            schedule="serialize" if leftover < muscle_w * 0.6 else "overlap",
        )

    # Auto (muscle does not fit alone either) or yield_mind: give the card
    # to the muscle and let autofit shrink it. Mind stays on CPU + mmap.
    return _plan(
        mode="yield_mind",
        mind_n_gpu_layers=0,
        muscle_n_gpu_layers=-1,
        muscle_n_cpu_moe=moe,
        mind_used_mib=0,
        muscle_used_mib=min(budget, muscle_need),
        reason=(
            "dense muscle needs the card; mind yields GPU layers and stays "
            "on CPU with mmap (fast wake). Turn on mind_keep_gpu or "
            "muscle_gpu_policy=share to keep the mind on the GPU instead."
        ),
        schedule="overlap",
    )


def apply_plan_to_rmb_state(state: dict[str, Any], plan: CoresidencePlan) -> dict[str, Any]:
    """Stamp planner GPU/MoE/KV/spec choices onto rmb.json state (owner locks win)."""
    out = dict(state)
    if not out.get("autofit_locked") and not out.get("n_gpu_layers_owner"):
        out["n_gpu_layers"] = plan.muscle_n_gpu_layers
    if plan.muscle_n_cpu_moe and not out.get("n_cpu_moe_owner"):
        out["n_cpu_moe"] = plan.muscle_n_cpu_moe
    if plan.muscle_kv and not out.get("cache_type_owner"):
        out["cache_type"] = plan.muscle_kv
    if not out.get("flash_attn_owner"):
        out["flash_attn"] = bool(plan.muscle_flash_attn)
    spec = (plan.muscle_spec or "auto").strip().lower()
    if not out.get("enable_mtp_owner") and not out.get("spec_type_owner"):
        if spec in {"off", "none"}:
            out["enable_mtp"] = False
            out["spec_type"] = None
        elif spec in {"draft-mtp", "mtp"}:
            out["enable_mtp"] = True
            out["spec_type"] = "draft-mtp"
        elif spec in {"ngram-mod", "ngram"}:
            out["enable_mtp"] = False
            out["spec_type"] = "ngram-mod"
        else:
            # auto: MTP when the GGUF has a head; else ngram-mod in _build_cmd.
            out["spec_type"] = out.get("spec_type") or "auto"
    out["last_coresidence"] = plan.to_public()
    return out


# ---------------------------------------------------------------------------
# Scheduling (do not thrash a tight split)
# ---------------------------------------------------------------------------

_gate = threading.RLock()
_holders = 0
_holders_mu = threading.Lock()


def last_saved_schedule(home_dir: str | Path | None = None) -> str:
    """Read the last planner schedule from rmb.json (overlap if unknown)."""
    try:
        from remedy.runtime.rmb.config import rmb_json_path

        path = rmb_json_path(home_dir)
        if not path.is_file():
            return ""
        raw = json.loads(path.read_text(encoding="utf-8"))
        last = raw.get("last_coresidence") if isinstance(raw, dict) else None
        if isinstance(last, dict):
            sched = str(last.get("schedule") or "").strip().lower()
            if sched in {"serialize", "overlap"}:
                return sched
    except Exception:
        return ""
    return ""


def _schedule_lock_path(home_dir: str | Path | None = None) -> Path:
    from remedy.runtime.rmb.config import rmb_home

    return rmb_home(home_dir) / "gpu_schedule.lock"


def _lock_exclusive(fp: Any) -> None:
    if os.name == "nt":
        import msvcrt

        fp.seek(0, os.SEEK_END)
        if fp.tell() == 0:
            fp.write("0")
            fp.flush()
        fp.seek(0)
        msvcrt.locking(fp.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(fp.fileno(), fcntl.LOCK_EX)


def _unlock_exclusive(fp: Any) -> None:
    if os.name == "nt":
        import msvcrt

        fp.seek(0)
        msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)


class GpuSchedule:
    """Serialize mind and muscle GPU work when the plan says the split is tight.

    Overlap (the default when there is headroom) is a no-op so the mind stays
    responsive during a long muscle generation. The exclusive lock is a file
    under the RMB home so the Go runtime and Python host share it.
    """

    def __init__(self, who: str, plan: CoresidencePlan | None = None, cfg: dict[str, Any] | None = None) -> None:
        self.who = who
        self._held = False
        self._fp: Any = None
        sched = plan.schedule if plan is not None else schedule_policy(cfg)
        if plan is None and sched == "auto":
            sched = last_saved_schedule() or "overlap"
        self._serialize = sched == "serialize"

    def __enter__(self) -> "GpuSchedule":
        if self._serialize:
            _gate.acquire()
            self._held = True
            with _holders_mu:
                global _holders
                _holders += 1
            try:
                path = _schedule_lock_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                self._fp = open(path, "a+")
                _lock_exclusive(self._fp)
            except Exception:
                self._fp = None
        return self

    def __exit__(self, *exc: object) -> None:
        if self._held:
            if self._fp is not None:
                try:
                    _unlock_exclusive(self._fp)
                except Exception:
                    pass
                try:
                    self._fp.close()
                except Exception:
                    pass
                self._fp = None
            with _holders_mu:
                global _holders
                _holders = max(0, _holders - 1)
            _gate.release()
            self._held = False


# ---------------------------------------------------------------------------
# Routing by difficulty and cost
# ---------------------------------------------------------------------------

_HARD_HINTS = (
    "implement",
    "refactor",
    "debug",
    "stack trace",
    "traceback",
    "write a patch",
    "multi-file",
    "architecture",
    "prove",
    "formally",
    "optimize the algorithm",
)
_CODE_HINTS = ("```", "def ", "class ", "fn ", "func ", "#!/", "error:", "failed tests")
# Conservative: these bump a user chat out of the "easy enough for the 4B" band
# so quality does not drop when a cloud provider is the selected muscle.
_COMPLEX_HINTS = (
    "explain",
    "compare",
    "design",
    "architect",
    "tradeoff",
    "trade-off",
    "recommend",
    "analyze",
    "analyse",
    "review this",
    "step by step",
    "how should",
    "how would",
    "how can i",
    "why does",
    "why is",
    "why are",
    "write a",
    "write me",
    "draft a",
    "difference between",
    "pros and cons",
    "best way",
    "help me build",
    "help me write",
    "walk me through",
    "outline a",
    "propose a",
)
_EASY_INNER_KINDS = {
    "continuity_core",
    "nano_classify",
    "brief_update",
    "dream",
    "digest",
    "media_digest",
}
_EASY_CHAT_MAX_CHARS = 280


def estimate_difficulty(prompt: str, *, task_kind: str = "") -> float:
    """Return a 0–1 difficulty score. Inner/short tasks stay near 0."""
    text = (prompt or "").strip()
    kind = (task_kind or "").strip().lower()
    if kind in {"continuity_core", "nano_classify", "brief_update", "dream", "digest", "media_digest"}:
        return 0.15
    if kind in {"vision_decode"}:
        return 0.35
    n = len(text)
    score = 0.2
    if n > 4000:
        score += 0.25
    elif n > 1200:
        score += 0.15
    elif n < 240:
        score -= 0.05
    low = text.lower()
    hard_hits = sum(1 for h in _HARD_HINTS if h in low)
    if hard_hits:
        score += 0.25 + 0.08 * min(hard_hits - 1, 3)
    if any(h in low for h in _CODE_HINTS):
        score += 0.15
    if kind in {"agent", "coding", "tool_loop", "react"}:
        score += 0.2
    return max(0.0, min(1.0, score))


def is_easy_user_chat(prompt: str, *, task_kind: str = "", has_attachments: bool = False) -> bool:
    """True only for clearly simple chats (or inner jobs). When unsure, False.

    Used to divert easy user turns to the always-on 4B mind, including when
    the selected chat provider is a cloud model. Conservative on purpose:
    a missed divert costs tokens, a false easy divert costs quality.
    """
    kind = (task_kind or "").strip().lower()
    if kind in _EASY_INNER_KINDS:
        return True
    if kind in {"agent", "coding", "tool_loop", "react", "vision_decode"}:
        return False
    if has_attachments:
        return False
    text = (prompt or "").strip()
    if not text or len(text) > _EASY_CHAT_MAX_CHARS:
        return False
    low = text.lower()
    if any(h in low for h in _HARD_HINTS):
        return False
    if any(h in low for h in _CODE_HINTS):
        return False
    if any(h in low for h in _COMPLEX_HINTS):
        return False
    if text.count("?") > 1:
        return False
    if "\n-" in text or "\n*" in text or "\n1." in text:
        return False
    return estimate_difficulty(prompt, task_kind=task_kind) < 0.25


def mind_handles_easy_chats(cfg: dict[str, Any] | None = None) -> bool:
    raw = resolve("mind_handles_easy_chats", cfg)
    if raw is None:
        return True
    return bool(raw)


@dataclass(frozen=True)
class MuscleRoute:
    target: str  # mind | rmb | local | frontier
    base_url: str
    reason: str
    difficulty: float
    cost: str  # free-local | local-vram | frontier-tokens

    def to_public(self) -> dict[str, Any]:
        return asdict(self)


def route_request(
    prompt: str = "",
    *,
    task_kind: str = "",
    cfg: dict[str, Any] | None = None,
    has_frontier: bool = False,
    rmb_running: bool = False,
    rmb_base_url: str = f"http://127.0.0.1:{DEFAULT_CHAT_PORT}/v1",
    mind_base_url: str = "http://127.0.0.1:8740/v1",
    servers: list[LocalMuscleServer] | None = None,
    plan: CoresidencePlan | None = None,
    active_cloud: bool = False,
    has_attachments: bool = False,
) -> MuscleRoute:
    """Pick mind vs local muscle vs frontier. Owner-forced knobs always win."""
    policy = route_policy(cfg)
    diff = estimate_difficulty(prompt, task_kind=task_kind)
    easy_on = mind_handles_easy_chats(cfg)
    easy = easy_on and is_easy_user_chat(
        prompt, task_kind=task_kind, has_attachments=has_attachments
    )
    ext = [s for s in (servers or []) if s.healthy]
    local_url = ext[0].base_url if ext else ""
    local_kind = ext[0].kind if ext else ""

    def _r(target: str, url: str, reason: str, cost: str) -> MuscleRoute:
        return MuscleRoute(target=target, base_url=url, reason=reason, difficulty=diff, cost=cost)

    if policy == "mind":
        return _r("mind", mind_base_url, "muscle_route=mind", "free-local")
    if policy == "rmb":
        return _r("rmb", rmb_base_url, "muscle_route=rmb", "local-vram")
    if policy == "local":
        if local_url:
            return _r("local", local_url, f"muscle_route=local ({local_kind})", "local-vram")
        if rmb_running:
            return _r("rmb", rmb_base_url, "muscle_route=local; no external server, using RMB", "local-vram")
        return _r("mind", mind_base_url, "muscle_route=local; nothing else is up, using the mind", "free-local")
    if policy == "frontier":
        if has_frontier:
            return _r("frontier", "", "muscle_route=frontier", "frontier-tokens")
        if rmb_running:
            return _r("rmb", rmb_base_url, "muscle_route=frontier but no cloud provider; using RMB", "local-vram")
        return _r("mind", mind_base_url, "muscle_route=frontier but no cloud provider; using the mind", "free-local")

    # auto: clearly easy chats go to the 4B mind even if a cloud provider
    # is selected, to save tokens. Hard or complex chats stay on the
    # selected provider. The easy gate is conservative.
    if easy:
        return _r("mind", mind_base_url, "easy chat answered by the always-on 4B mind", "free-local")
    if active_cloud:
        return _r(
            "frontier",
            "",
            "complex chat stays on the selected cloud provider",
            "frontier-tokens",
        )
    if rmb_running:
        target, url, label = "rmb", rmb_base_url, "RMB"
    elif local_url:
        target, url, label = "local", local_url, local_kind
    else:
        target, url, label = "", "", ""
    if target and diff < 0.85:
        return _r(target, url, f"coding/heavy work on {label} (difficulty {diff:.2f})", "local-vram")
    if target and not has_frontier:
        return _r(target, url, f"hard work on {label}; no frontier provider configured", "local-vram")
    if has_frontier and diff >= 0.85:
        return _r("frontier", "", f"hard work (difficulty {diff:.2f}) escalates to the frontier", "frontier-tokens")
    if target:
        return _r(target, url, f"using {label}", "local-vram")
    if has_frontier:
        return _r("frontier", "", "no local muscle is running; frontier is configured", "frontier-tokens")
    return _r("mind", mind_base_url, "no local muscle and no frontier; the mind handles it", "free-local")


# Silence unused import on some type checkers (plan is reserved for callers).
_ = time
