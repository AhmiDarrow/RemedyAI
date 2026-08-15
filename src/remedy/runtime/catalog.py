"""Pinned local model + llama-server catalog — single source of truth.

Every local role (vision, nano, helper) uses DEFAULT_LOCAL_MODEL_ID only.
Prebundle policy: CPU + CUDA runtimes (option B); same weights everywhere.

Default model: SmolVLM2-2.2B (Apache 2.0) — commercial-friendly dependency.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

# Logical id used in config / vision.json / API / nanoswarm.
# Stable across Remedy versions until an intentional catalog bump.
DEFAULT_LOCAL_MODEL_ID = "smolvlm2-2.2b"
DEFAULT_MODEL_ID = DEFAULT_LOCAL_MODEL_ID  # alias for vision callers

# HF repo hosting GGUF + mmproj (llama.cpp multimodal).
# SmolVLM2-2.2B-Instruct — Apache 2.0 (HuggingFaceTB / ggml-org GGUF).
_SMOL_HF_REPO = "ggml-org/SmolVLM2-2.2B-Instruct-GGUF"
_SMOL_MODEL_FILE = "SmolVLM2-2.2B-Instruct-Q4_K_M.gguf"
_SMOL_MMPROJ_FILE = "mmproj-SmolVLM2-2.2B-Instruct-Q8_0.gguf"
_SMOL_MODEL_SHA256 = "0cf76814555b8665149075b74ab6b5c1d428ea1d3d01c1918c12012e8d7c9f58"
_SMOL_MMPROJ_SHA256 = "ae07ea1facd07dd3230c4483b63e8cda96c6944ad2481f33d531f79e892dd024"
_SMOL_MODEL_BYTES = 1_112_602_656
_SMOL_MMPROJ_BYTES = 592_523_200

# Pinned llama.cpp release (Windows + Linux first-run download).
LLAMA_CPP_TAG = "b10107"
_LLAMA_CPU_ZIP = "llama-b10107-bin-win-cpu-x64.zip"
_LLAMA_CUDA_ZIP = "llama-b10107-bin-win-cuda-12.4-x64.zip"
_LLAMA_CUDART_ZIP = "cudart-llama-bin-win-cuda-12.4-x64.zip"
_LLAMA_LINUX_CPU_TGZ = "llama-b10107-bin-ubuntu-x64.tar.gz"
_LLAMA_LINUX_VULKAN_TGZ = "llama-b10107-bin-ubuntu-vulkan-x64.tar.gz"

# Option B: ship both CPU and CUDA; pick at runtime via NVIDIA detect.
BUNDLED_RUNTIME_IDS: tuple[str, ...] = ("win-cpu-x64", "win-cuda-12.4-x64")

# Roles that share this model (helper reserved for later product UI).
LOCAL_ROLES: tuple[str, ...] = ("vision", "nano", "helper")


@dataclass(frozen=True)
class DownloadAsset:
    """A single downloadable/bundled file with optional integrity check."""

    name: str
    url: str
    size_bytes: int
    sha256: str | None = None


@dataclass(frozen=True)
class LocalModelSpec:
    """Catalog entry for the sole bundled multimodal local model."""

    id: str
    name: str
    hf_repo: str
    model_file: str
    mmproj_file: str
    model_sha256: str | None
    mmproj_sha256: str | None
    model_bytes: int
    mmproj_bytes: int
    min_ram_gb: int = 6
    license: str = "Apache-2.0"
    notes: str = ""

    @property
    def approx_download_bytes(self) -> int:
        return int(self.model_bytes) + int(self.mmproj_bytes)

    def model_url(self) -> str:
        return f"https://huggingface.co/{self.hf_repo}/resolve/main/{self.model_file}"

    def mmproj_url(self) -> str:
        return f"https://huggingface.co/{self.hf_repo}/resolve/main/{self.mmproj_file}"

    def assets(self) -> list[DownloadAsset]:
        return [
            DownloadAsset(
                name=self.model_file,
                url=self.model_url(),
                size_bytes=self.model_bytes,
                sha256=self.model_sha256,
            ),
            DownloadAsset(
                name=self.mmproj_file,
                url=self.mmproj_url(),
                size_bytes=self.mmproj_bytes,
                sha256=self.mmproj_sha256,
            ),
        ]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "hf_repo": self.hf_repo,
            "model_file": self.model_file,
            "mmproj_file": self.mmproj_file,
            "approx_download_bytes": self.approx_download_bytes,
            "approx_download_gb": round(self.approx_download_bytes / (1024**3), 2),
            "min_ram_gb": self.min_ram_gb,
            "license": self.license,
            "notes": self.notes,
            "is_default": self.id == DEFAULT_LOCAL_MODEL_ID,
            "bundled": True,
            "roles": list(LOCAL_ROLES),
        }


# Backward-compatible name used throughout vision package.
VisionModelSpec = LocalModelSpec

LOCAL_MODELS: dict[str, LocalModelSpec] = {
    DEFAULT_LOCAL_MODEL_ID: LocalModelSpec(
        id=DEFAULT_LOCAL_MODEL_ID,
        name="SmolVLM2 2.2B",
        hf_repo=_SMOL_HF_REPO,
        model_file=_SMOL_MODEL_FILE,
        mmproj_file=_SMOL_MMPROJ_FILE,
        model_sha256=_SMOL_MODEL_SHA256,
        mmproj_sha256=_SMOL_MMPROJ_SHA256,
        model_bytes=_SMOL_MODEL_BYTES,
        mmproj_bytes=_SMOL_MMPROJ_BYTES,
        min_ram_gb=4,
        license="Apache-2.0",
        notes=(
            "Required local model (Apache 2.0) — vision, nano swarm, helper. "
            "First-run download; same weights on every PC for a given Remedy release."
        ),
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
        size_bytes=18_213_827,
        sha256="52133a0a5a8f6035b1bdd2f89c3425ea8b742413d9bdb9a2dee30e3a1681b18c",
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
        size_bytes=247_064_556,
        sha256="1e43bbec9691cd0bc636603c366769148fa6265fd261c5f7c67050b450bbc237",
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
        size_bytes=16_275_561,
        sha256="afe1ae0b706c4a0830b218a9249037b7a6cc723f81deb78825662128b25453e6",
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
        size_bytes=32_239_108,
        sha256="28f86dfce8c3723d4e9fd971b8456d946e09324708880533091399d284fe9add",
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


def get_model_spec(model_id: str | None = None) -> LocalModelSpec:
    mid = (model_id or DEFAULT_LOCAL_MODEL_ID).strip() or DEFAULT_LOCAL_MODEL_ID
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


def total_install_bytes(model_id: str | None = None, runtime_id: str | None = None) -> int:
    model = get_model_spec(model_id)
    runtime = get_runtime_spec(runtime_id)
    extra = sum(a.size_bytes for a in runtime.extra_zips)
    return model.approx_download_bytes + runtime.size_bytes + extra


def total_bundle_bytes() -> int:
    """Approx payload for option B: one model + CPU + CUDA (+ cudart)."""
    model = get_model_spec()
    total = model.approx_download_bytes
    for rid in BUNDLED_RUNTIME_IDS:
        r = get_runtime_spec(rid)
        total += r.size_bytes + sum(a.size_bytes for a in r.extra_zips)
    return total
