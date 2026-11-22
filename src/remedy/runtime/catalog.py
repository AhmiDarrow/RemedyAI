"""Pinned local model + llama-server catalog — single source of truth.

Every local role (vision, nano, helper, dreams, workers, media digest)
uses DEFAULT_LOCAL_MODEL_ID only: the unified Qwen3.5-4B mind (text +
vision). Prebundle policy: CPU + CUDA runtimes (option B).

Default model: Qwen3.5-4B (Apache 2.0) — Q6_K primary, Q4_K_M autofit
fallback, official mmproj-F16. Language-model sha256 is TODO until the
fine-tuned GGUF is published; the public unsloth base is used until then.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

# Logical id used in config / vision.json / API / nanoswarm.
# Stable across Remedy versions until an intentional catalog bump.
DEFAULT_LOCAL_MODEL_ID = "qwen35-4b-mind"
DEFAULT_MODEL_ID = DEFAULT_LOCAL_MODEL_ID  # alias for vision callers
LEGACY_LOCAL_MODEL_IDS: tuple[str, ...] = ("smolvlm2-2.2b", "qwen2.5-vl-3b")

# Interim public base (official Qwen3.5-4B GGUF + mmproj). The fine-tuned
# Remedy mind will replace the language-model files; mmproj stays frozen.
_MIND_HF_REPO = "unsloth/Qwen3.5-4B-GGUF"
_MIND_MODEL_FILE = "Qwen3.5-4B-Q6_K.gguf"
_MIND_FALLBACK_FILE = "Qwen3.5-4B-Q4_K_M.gguf"
_MIND_MMPROJ_FILE = "mmproj-F16.gguf"
# Language-model hashes are filled in when the tuned GGUF exists.
_MIND_MODEL_SHA256 = "TODO"
_MIND_FALLBACK_SHA256 = "TODO"
# Official frozen projector (unsloth mmproj-F16.gguf).
_MIND_MMPROJ_SHA256 = "cd88edcf8d031894960bb0c9c5b9b7e1fea6ebee02b9f7ce925a00d12891f864"
_MIND_MODEL_BYTES = 3_525_956_768
_MIND_FALLBACK_BYTES = 2_740_937_888
_MIND_MMPROJ_BYTES = 672_423_616

# Pinned llama.cpp release (Windows + Linux first-run download).
# b12628 (2026-11-22) is a post-v1.0.0 nightly: MTP + vision, image-max-tokens,
# ngram-mod, cache-idle-slots, FP8 KV, JSON-schema fixes.
# https://github.com/ggml-org/llama.cpp/releases/tag/b12628
LLAMA_CPP_TAG = "b12628"
_LLAMA_CPU_ZIP = "llama-b12628-bin-win-cpu-x64.zip"
_LLAMA_CUDA_ZIP = "llama-b12628-bin-win-cuda-12.4-x64.zip"
_LLAMA_CUDART_ZIP = "cudart-llama-bin-win-cuda-12.4-x64.zip"
_LLAMA_LINUX_CPU_TGZ = "llama-b12628-bin-ubuntu-x64.tar.gz"
_LLAMA_LINUX_VULKAN_TGZ = "llama-b12628-bin-ubuntu-vulkan-x64.tar.gz"

# Option B: ship both CPU and CUDA; pick at runtime via NVIDIA detect.
BUNDLED_RUNTIME_IDS: tuple[str, ...] = ("win-cpu-x64", "win-cuda-12.4-x64")

# Roles that share this model (helper reserved for later product UI).
LOCAL_ROLES: tuple[str, ...] = ("vision", "nano", "helper")


def _sha_or_none(value: str | None) -> str | None:
    """Treat TODO / empty as 'not yet hashed' so the installer skips verify."""
    raw = (value or "").strip()
    if not raw or raw.upper() == "TODO":
        return None
    return raw


@dataclass(frozen=True)
class DownloadAsset:
    """A single downloadable/bundled file with optional integrity check."""

    name: str
    url: str
    size_bytes: int
    sha256: str | None = None


@dataclass(frozen=True)
class LocalModelSpec:
    """Catalog entry for the bundled multimodal local mind."""

    id: str
    name: str
    hf_repo: str
    model_file: str
    mmproj_file: str
    model_sha256: str | None
    mmproj_sha256: str | None
    model_bytes: int
    mmproj_bytes: int
    min_ram_gb: int = 8
    license: str = "Apache-2.0"
    notes: str = ""
    fallback_model_file: str = ""
    fallback_sha256: str | None = None
    fallback_bytes: int = 0
    has_mtp: bool = False
    quant: str = "Q6_K"

    @property
    def approx_download_bytes(self) -> int:
        return int(self.model_bytes) + int(self.mmproj_bytes)

    @property
    def approx_fallback_download_bytes(self) -> int:
        if not self.fallback_model_file:
            return self.approx_download_bytes
        return int(self.fallback_bytes) + int(self.mmproj_bytes)

    def model_url(self, *, fallback: bool = False) -> str:
        name = self.fallback_model_file if fallback and self.fallback_model_file else self.model_file
        return f"https://huggingface.co/{self.hf_repo}/resolve/main/{name}"

    def mmproj_url(self) -> str:
        return f"https://huggingface.co/{self.hf_repo}/resolve/main/{self.mmproj_file}"

    def assets(self, *, fallback: bool = False) -> list[DownloadAsset]:
        use_fb = bool(fallback and self.fallback_model_file)
        return [
            DownloadAsset(
                name=self.fallback_model_file if use_fb else self.model_file,
                url=self.model_url(fallback=use_fb),
                size_bytes=self.fallback_bytes if use_fb else self.model_bytes,
                sha256=_sha_or_none(
                    self.fallback_sha256 if use_fb else self.model_sha256
                ),
            ),
            DownloadAsset(
                name=self.mmproj_file,
                url=self.mmproj_url(),
                size_bytes=self.mmproj_bytes,
                sha256=_sha_or_none(self.mmproj_sha256),
            ),
        ]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "hf_repo": self.hf_repo,
            "model_file": self.model_file,
            "mmproj_file": self.mmproj_file,
            "model_sha256": self.model_sha256 or "TODO",
            "mmproj_sha256": self.mmproj_sha256 or "TODO",
            "fallback_model_file": self.fallback_model_file,
            "fallback_sha256": self.fallback_sha256 or "TODO",
            "fallback_bytes": self.fallback_bytes,
            "approx_download_bytes": self.approx_download_bytes,
            "approx_download_gb": round(self.approx_download_bytes / (1024**3), 2),
            "approx_fallback_download_bytes": self.approx_fallback_download_bytes,
            "min_ram_gb": self.min_ram_gb,
            "license": self.license,
            "notes": self.notes,
            "is_default": self.id == DEFAULT_LOCAL_MODEL_ID,
            "bundled": True,
            "roles": list(LOCAL_ROLES),
            "has_mtp": self.has_mtp,
            "quant": self.quant,
            "required": True,
        }


# Backward-compatible name used throughout vision package.
VisionModelSpec = LocalModelSpec

LOCAL_MODELS: dict[str, LocalModelSpec] = {
    DEFAULT_LOCAL_MODEL_ID: LocalModelSpec(
        id=DEFAULT_LOCAL_MODEL_ID,
        name="Qwen3.5 4B mind (Q6_K)",
        hf_repo=_MIND_HF_REPO,
        model_file=_MIND_MODEL_FILE,
        mmproj_file=_MIND_MMPROJ_FILE,
        model_sha256=_MIND_MODEL_SHA256,
        mmproj_sha256=_MIND_MMPROJ_SHA256,
        model_bytes=_MIND_MODEL_BYTES,
        mmproj_bytes=_MIND_MMPROJ_BYTES,
        min_ram_gb=8,
        license="Apache-2.0",
        notes=(
            "Required local mind (Apache 2.0) — vision, nano, helper, dreams, "
            "workers, and media digests on one llama-server. Q6_K default, "
            "Q4_K_M autofit fallback, official mmproj-F16. First-run download; "
            "language-model sha256 is TODO until the fine-tuned GGUF ships."
        ),
        fallback_model_file=_MIND_FALLBACK_FILE,
        fallback_sha256=_MIND_FALLBACK_SHA256,
        fallback_bytes=_MIND_FALLBACK_BYTES,
        has_mtp=True,
        quant="Q6_K",
    ),
}

# Vision module historically imported VISION_MODELS.
VISION_MODELS = LOCAL_MODELS


@dataclass(frozen=True)
class LlamaRuntimeSpec:
    """Pinned llama-server runtime package for a platform flavor."""

    id: str
    tag: str
    platform: str  # win-cpu-x64 | win-cuda-12.4-x64 | linux-cpu-x64 | linux-vulkan-x64
    zip_name: str
    url: str
    size_bytes: int
    sha256: str | None
    binary_name: str = "llama-server.exe"
    extra_zips: tuple[DownloadAsset, ...] = ()

    def primary_asset(self) -> DownloadAsset:
        return DownloadAsset(
            name=self.zip_name,
            url=self.url,
            size_bytes=self.size_bytes,
            sha256=self.sha256,
        )


def _gh_asset(name: str, size: int, sha256: str) -> DownloadAsset:
    return DownloadAsset(
        name=name,
        url=f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_CPP_TAG}/{name}",
        size_bytes=size,
        sha256=sha256,
    )


LLAMA_RUNTIMES: dict[str, LlamaRuntimeSpec] = {
    "win-cpu-x64": LlamaRuntimeSpec(
        id="win-cpu-x64",
        tag=LLAMA_CPP_TAG,
        platform="win-cpu-x64",
        zip_name=_LLAMA_CPU_ZIP,
        url=(
            f"https://github.com/ggml-org/llama.cpp/releases/download/"
            f"{LLAMA_CPP_TAG}/{_LLAMA_CPU_ZIP}"
        ),
        size_bytes=25_037_881,
        sha256="e2f60be1e39ae47743e89f6210886e8d985b67108e1be19b7bcc166d863bc580",
        binary_name="llama-server.exe",
    ),
    "win-cuda-12.4-x64": LlamaRuntimeSpec(
        id="win-cuda-12.4-x64",
        tag=LLAMA_CPP_TAG,
        platform="win-cuda-12.4-x64",
        zip_name=_LLAMA_CUDA_ZIP,
        url=(
            f"https://github.com/ggml-org/llama.cpp/releases/download/"
            f"{LLAMA_CPP_TAG}/{_LLAMA_CUDA_ZIP}"
        ),
        size_bytes=357_973_598,
        sha256="dfb2574d6d5ea69d79b1ff69331b9e85b76bb6920278d3f0193b1586276f3bbd",
        binary_name="llama-server.exe",
        extra_zips=(
            _gh_asset(
                _LLAMA_CUDART_ZIP,
                391_443_627,
                "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6",
            ),
        ),
    ),
    "linux-cpu-x64": LlamaRuntimeSpec(
        id="linux-cpu-x64",
        tag=LLAMA_CPP_TAG,
        platform="linux-cpu-x64",
        zip_name=_LLAMA_LINUX_CPU_TGZ,
        url=(
            f"https://github.com/ggml-org/llama.cpp/releases/download/"
            f"{LLAMA_CPP_TAG}/{_LLAMA_LINUX_CPU_TGZ}"
        ),
        size_bytes=23_225_939,
        sha256="d84ba76c8a9066f13f55834f0171cfa9bae86e228e66f9b2bbff9769d7cbf389",
        binary_name="llama-server",
    ),
    "linux-vulkan-x64": LlamaRuntimeSpec(
        id="linux-vulkan-x64",
        tag=LLAMA_CPP_TAG,
        platform="linux-vulkan-x64",
        zip_name=_LLAMA_LINUX_VULKAN_TGZ,
        url=(
            f"https://github.com/ggml-org/llama.cpp/releases/download/"
            f"{LLAMA_CPP_TAG}/{_LLAMA_LINUX_VULKAN_TGZ}"
        ),
        size_bytes=36_908_581,
        sha256="35f49f4c3f7d66619c596f3f0b55fa73451484a03959680e37c08636e2e8c363",
        binary_name="llama-server",
    ),
}

DEFAULT_RUNTIME_ID = "win-cpu-x64"


def default_runtime_id(*, prefer_gpu: bool = False) -> str:
    """llama-server flavor for this OS (Windows CUDA / Linux Vulkan / CPU)."""
    if sys.platform == "win32":
        return "win-cuda-12.4-x64" if prefer_gpu else "win-cpu-x64"
    if sys.platform.startswith("linux"):
        return "linux-vulkan-x64" if prefer_gpu else "linux-cpu-x64"
    return "win-cpu-x64"


def host_runtime_ids() -> tuple[str, ...]:
    """Catalog ids that can actually run on this OS."""
    if sys.platform == "win32":
        return ("win-cpu-x64", "win-cuda-12.4-x64")
    if sys.platform.startswith("linux"):
        return ("linux-cpu-x64", "linux-vulkan-x64")
    return (DEFAULT_RUNTIME_ID,)


def _runtime_is_gpu(platform: str) -> bool:
    p = platform.lower()
    return any(token in p for token in ("cuda", "vulkan", "hip", "metal"))


def _runtime_matches_host(platform: str) -> bool:
    p = platform.lower()
    if sys.platform == "win32":
        return p.startswith("win")
    if sys.platform.startswith("linux"):
        return p.startswith("linux")
    return False


def normalize_runtime_id(
    runtime_id: str | None = None,
    *,
    prefer_gpu: bool = False,
) -> str:
    """Map a catalog id onto one that can run here (shared Win/Linux homes)."""
    rid = (runtime_id or "").strip()
    want_gpu = prefer_gpu
    if rid in LLAMA_RUNTIMES:
        plat = LLAMA_RUNTIMES[rid].platform
        if _runtime_matches_host(plat):
            return rid
        want_gpu = want_gpu or _runtime_is_gpu(plat)
    return default_runtime_id(prefer_gpu=want_gpu)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8740


def is_legacy_model_id(model_id: str | None) -> bool:
    mid = (model_id or "").strip()
    return bool(mid) and mid != DEFAULT_LOCAL_MODEL_ID and mid in LEGACY_LOCAL_MODEL_IDS


def get_model_spec(model_id: str | None = None) -> LocalModelSpec:
    mid = (model_id or DEFAULT_LOCAL_MODEL_ID).strip() or DEFAULT_LOCAL_MODEL_ID
    if mid in LEGACY_LOCAL_MODEL_IDS:
        mid = DEFAULT_LOCAL_MODEL_ID
    if mid not in LOCAL_MODELS:
        raise KeyError(
            f"Unknown local model_id {mid!r}. "
            f"Known: {', '.join(sorted(LOCAL_MODELS))} "
            f"(Remedy ships a single local VLM for all local roles)."
        )
    return LOCAL_MODELS[mid]


def get_runtime_spec(runtime_id: str | None = None) -> LlamaRuntimeSpec:
    rid = (runtime_id or default_runtime_id()).strip() or default_runtime_id()
    if rid not in LLAMA_RUNTIMES:
        raise KeyError(
            f"Unknown runtime {rid!r}. Known: {', '.join(sorted(LLAMA_RUNTIMES))}"
        )
    return LLAMA_RUNTIMES[rid]


def catalog_public() -> dict[str, Any]:
    return {
        "default_model_id": DEFAULT_LOCAL_MODEL_ID,
        "default_local_model_id": DEFAULT_LOCAL_MODEL_ID,
        "models": [m.to_public_dict() for m in LOCAL_MODELS.values()],
        "roles": list(LOCAL_ROLES),
        "bundled_runtime_ids": list(BUNDLED_RUNTIME_IDS),
        "host_runtime_ids": list(host_runtime_ids()),
        "default_runtime_id": default_runtime_id(),
        "bundle_policy": "cpu_and_cuda",  # option B (Windows prebundle)
        "llama_cpp_tag": LLAMA_CPP_TAG,
        "default_port": DEFAULT_PORT,
        "required": True,
        "runtimes": [
            {
                "id": r.id,
                "platform": r.platform,
                "tag": r.tag,
                "zip_name": r.zip_name,
                "size_bytes": r.size_bytes,
                "bundled": r.id in BUNDLED_RUNTIME_IDS,
            }
            for r in LLAMA_RUNTIMES.values()
        ],
    }


def total_install_bytes(
    model_id: str | None = None,
    runtime_id: str | None = None,
    *,
    fallback: bool = False,
) -> int:
    model = get_model_spec(model_id)
    runtime = get_runtime_spec(runtime_id)
    extra = sum(a.size_bytes for a in runtime.extra_zips)
    model_bytes = (
        model.approx_fallback_download_bytes if fallback else model.approx_download_bytes
    )
    return model_bytes + runtime.size_bytes + extra


def total_bundle_bytes() -> int:
    """Approx payload for option B: one model + CPU + CUDA (+ cudart)."""
    model = get_model_spec()
    total = model.approx_download_bytes
    for rid in BUNDLED_RUNTIME_IDS:
        r = get_runtime_spec(rid)
        total += r.size_bytes + sum(a.size_bytes for a in r.extra_zips)
    return total
