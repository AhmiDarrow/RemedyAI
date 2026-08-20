"""Locate and activate prebundled local model + llama-server assets.

Layout (relative to bundle root)::

  local/
    models/smolvlm2-2.2b/<gguf + mmproj>
    runtime/cpu/...
    runtime/cuda/...

Also accepts legacy ``~/.remedy/vision`` (models + runtime flat extract) so
existing installs count as the same pinned SmolVLM2 without re-download.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from remedy.runtime.catalog import (
    DEFAULT_HOST,
    DEFAULT_LOCAL_MODEL_ID,
    DEFAULT_PORT,
    default_runtime_id,
    get_model_spec,
    normalize_runtime_id,
)

logger = logging.getLogger(__name__)


def _package_bundled_root() -> Path | None:
    pkg = Path(__file__).resolve().parents[1]
    cand = pkg / "bundled" / "local"
    return cand if cand.is_dir() else None


def _exe_dir_local() -> list[Path]:
    """Desktop sidecar / frozen exe: look next to binary and resource dirs."""
    out: list[Path] = []
    try:
        import sys

        if getattr(sys, "frozen", False):
            base = Path(sys.executable).resolve().parent
            out.extend(
                [
                    base / "local",
                    base / "resources" / "local",
                    base.parent / "resources" / "local",
                ]
            )
        # PyInstaller _MEIPASS
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            out.append(Path(meipass) / "local")
    except Exception:
        pass
    return out


def bundle_roots() -> list[Path]:
    roots: list[Path] = []
    env = os.environ.get("REMEDY_LOCAL_BUNDLE") or os.environ.get("REMEDY_BUNDLE_DIR")
    if env:
        roots.append(Path(env).expanduser())
    for key in ("REMEDY_RESOURCES", "TAURI_RESOURCE_DIR", "RESOURCE_DIR"):
        v = os.environ.get(key)
        if v:
            p = Path(v).expanduser()
            roots.append(p if p.name == "local" else p / "local")
    roots.extend(_exe_dir_local())
    roots.append(Path.cwd() / "resources" / "local")
    roots.append(Path.cwd() / "local")
    pkg = _package_bundled_root()
    if pkg:
        roots.append(pkg)
    # NOTE: do not add ~/.remedy/vision here — that is user-data, checked via
    # vision.install.is_installed / model_files_present(home_dir). Product
    # bundles are only app resources + REMEDY_LOCAL_BUNDLE.

    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        try:
            key = str(r.resolve()) if r.exists() else str(r)
        except OSError:
            key = str(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _model_dir_candidates(root: Path, mid: str) -> list[Path]:
    return [
        root / "models" / mid,
        root / mid,
        root / "models",
    ]


def find_bundle_root() -> Path | None:
    mid = DEFAULT_LOCAL_MODEL_ID
    spec = get_model_spec(mid)
    for root in bundle_roots():
        for d in _model_dir_candidates(root, mid):
            if (d / spec.model_file).is_file() and (d / spec.mmproj_file).is_file():
                return root
    return None


def model_paths_from_bundle(
    model_id: str | None = None,
    bundle_root: Path | None = None,
) -> dict[str, Path] | None:
    mid = model_id or DEFAULT_LOCAL_MODEL_ID
    spec = get_model_spec(mid)
    roots = [bundle_root] if bundle_root else bundle_roots()
    for root in roots:
        if root is None:
            continue
        for d in _model_dir_candidates(root, mid):
            model = d / spec.model_file
            mmproj = d / spec.mmproj_file
            if model.is_file() and mmproj.is_file():
                return {
                    "model_path": model,
                    "mmproj_path": mmproj,
                    "bundle_root": root,
                }
    return None


def _find_llama_binary(search_roots: list[Path]) -> Path | None:
    for r in search_roots:
        if r is None or not r.exists():
            continue
        candidates = [
            r / "llama-server.exe",
            r / "llama-server",
        ]
        for c in candidates:
            if c.is_file():
                return c
        if r.is_dir():
            for p in r.rglob("llama-server.exe"):
                return p
            for p in r.rglob("llama-server"):
                return p
    return None


def runtime_binary_from_bundle(
    runtime_id: str | None = None,
    bundle_root: Path | None = None,
) -> Path | None:
    rid = normalize_runtime_id(runtime_id)
    if "cuda" in rid:
        flavor = "cuda"
    elif "vulkan" in rid:
        flavor = "vulkan"
    else:
        flavor = "cpu"
    root = bundle_root or find_bundle_root()
    roots: list[Path] = []
    if root is not None:
        roots.extend(
            [
                root / "runtime" / flavor,
                root / "runtime" / rid,
                root / "runtime",
                root,  # legacy flat ~/.remedy/vision/runtime sibling
            ]
        )
        # Legacy: models under vision/, runtime under vision/runtime
        if (root / "runtime").is_dir():
            roots.insert(0, root / "runtime")
    for r in bundle_roots():
        roots.extend(
            [
                r / "runtime" / flavor,
                r / "runtime",
                r,
            ]
        )
    return _find_llama_binary(roots)


def prefer_runtime_id(*, nvidia_detected: bool) -> str:
    """GPU runtime when a card is present, else CPU — OS-correct catalog id."""
    return default_runtime_id(prefer_gpu=nvidia_detected)


def bundle_available() -> dict[str, Any]:
    """Diagnostic: what the host can see without writing state."""
    paths = model_paths_from_bundle()
    rid_cpu = default_runtime_id(prefer_gpu=False)
    rid_gpu = default_runtime_id(prefer_gpu=True)
    gpu_bin = str(runtime_binary_from_bundle(rid_gpu) or "") or None
    return {
        "model_present": paths is not None,
        "model_path": str(paths["model_path"]) if paths else None,
        "mmproj_path": str(paths["mmproj_path"]) if paths else None,
        "bundle_root": str(paths["bundle_root"]) if paths else None,
        "cpu_binary": str(runtime_binary_from_bundle(rid_cpu)) if True else None,
        "cuda_binary": gpu_bin,
        "gpu_binary": gpu_bin,
        "searched_roots": [str(r) for r in bundle_roots()[:12]],
    }


def _legacy_user_model_paths(home_dir: str | Path | None = None) -> dict[str, Path] | None:
    """Pinned files already under this home's vision/models tree."""
    try:
        from remedy.vision.config import models_dir
    except Exception:
        return None
    mid = DEFAULT_LOCAL_MODEL_ID
    spec = get_model_spec(mid)
    d = models_dir(mid, home_dir)
    model = d / spec.model_file
    mmproj = d / spec.mmproj_file
    if model.is_file() and mmproj.is_file():
        return {
            "model_path": model,
            "mmproj_path": mmproj,
            "bundle_root": d.parent.parent,  # …/vision
        }
    return None


def activate_local_bundle(
    home_dir: str | Path | None = None,
    *,
    nvidia_detected: bool | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    """Write vision.json pointing at pinned model + runtime. No network.

    Accepts product prebundle layout or legacy files under this home's vision/.
    """
    from remedy.vision.config import load_vision_json, runtime_dir, save_vision_json

    paths = model_paths_from_bundle() or _legacy_user_model_paths(home_dir)
    if not paths:
        diag = bundle_available()
        return {
            "ok": False,
            "error": (
                "Pinned local model files not found. Reinstall Remedy Desktop "
                f"(bundles {DEFAULT_LOCAL_MODEL_ID}) or set REMEDY_LOCAL_BUNDLE."
            ),
            "diagnostic": diag,
        }

    if nvidia_detected is None:
        try:
            from remedy.vision.health import detect_nvidia

            nvidia_detected = bool(detect_nvidia())
        except Exception:
            nvidia_detected = False

    rid = prefer_runtime_id(nvidia_detected=bool(nvidia_detected))
    bin_path = runtime_binary_from_bundle(rid, paths.get("bundle_root"))
    if bin_path is None:
        rid = default_runtime_id(prefer_gpu=False)
        bin_path = runtime_binary_from_bundle(rid, paths.get("bundle_root"))
    if bin_path is None:
        # Legacy flat runtime under this home
        try:
            from remedy.vision.install import runtime_binary_path

            bin_path = runtime_binary_path(home_dir)
        except Exception:
            bin_path = None
    if bin_path is None:
        # Last try: vision/runtime next to models
        try:
            rt = runtime_dir(home_dir)
            bin_path = _find_llama_binary([rt, paths["bundle_root"] / "runtime"])
        except Exception:
            bin_path = None
    if bin_path is None:
        return {
            "ok": False,
            "error": "llama-server binary not found next to model (CPU/CUDA bundle).",
            "diagnostic": bundle_available(),
        }

    existing = load_vision_json(home_dir)
    state = {
        **existing,
        "model_id": DEFAULT_LOCAL_MODEL_ID,
        "model_path": str(paths["model_path"]),
        "mmproj_path": str(paths["mmproj_path"]),
        "runtime_id": rid,
        "runtime_version": existing.get("runtime_version") or "bundled",
        "backend": "llama_server",
        "host": existing.get("host") or DEFAULT_HOST,
        "port": int(existing.get("port") or DEFAULT_PORT),
        "base_url": existing.get("base_url")
        or f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/v1",
        "bundled": True,
        "enabled": bool(enabled if existing.get("enabled") is None else existing.get("enabled", enabled)),
        "runtime_binary": str(bin_path),
    }
    # Prefer explicit enabled request when activating for use
    if enabled:
        state["enabled"] = True
    save_vision_json(state, home_dir)
    logger.info(
        "Activated local bundle model=%s runtime=%s binary=%s",
        state["model_path"],
        rid,
        bin_path,
    )
    return {"ok": True, "state": state, "runtime_id": rid, "bundled": True}


def ensure_vision_json_from_bundle(
    home_dir: str | Path | None = None,
    *,
    nvidia_detected: bool | None = None,
    enabled: bool = True,
) -> dict[str, Any] | None:
    """Back-compat: return state dict or None (no network)."""
    result = activate_local_bundle(
        home_dir, nvidia_detected=nvidia_detected, enabled=enabled
    )
    if result.get("ok"):
        return result.get("state")
    return None
