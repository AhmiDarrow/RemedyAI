"""Pinned visual-decoder catalog — Remedy always knows which model it installs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Logical id used in config / vision.json / API.
DEFAULT_MODEL_ID = "qwen2.5-vl-3b"

# HF repo hosting GGUF + mmproj (llama.cpp multimodal).
_QWEN_HF_REPO = "ggml-org/Qwen2.5-VL-3B-Instruct-GGUF"
_QWEN_MODEL_FILE = "Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf"
_QWEN_MMPROJ_FILE = "mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf"
# LFS content OIDs from Hugging Face (sha256 of file bytes).
_QWEN_MODEL_SHA256 = "d02fe9b69ad8cadbbd228e387667af66612c44bed29ffc8eb1e7caf9ac486c12"
_QWEN_MMPROJ_SHA256 = "980c9b2f78c04e6cff93d277ada09e768394f112d75db3b4e9dea8a69f9fb904"
_QWEN_MODEL_BYTES = 1_929_901_056
_QWEN_MMPROJ_BYTES = 844_757_728

# Pinned llama.cpp release (Windows focus for v1).
LLAMA_CPP_TAG = "b10107"
_LLAMA_CPU_ZIP = "llama-b10107-bin-win-cpu-x64.zip"
_LLAMA_CUDA_ZIP = "llama-b10107-bin-win-cuda-12.4-x64.zip"
_LLAMA_CUDART_ZIP = "cudart-llama-bin-win-cuda-12.4-x64.zip"


@dataclass(frozen=True)
class DownloadAsset:
    """A single downloadable file with optional integrity check."""

    name: str
    url: str
    size_bytes: int
    sha256: str | None = None


@dataclass(frozen=True)
class VisionModelSpec:
    """Catalog entry for a managed visual decoder model."""

    id: str
    name: str
    hf_repo: str
    model_file: str
    mmproj_file: str
    model_sha256: str
    mmproj_sha256: str
    model_bytes: int
    mmproj_bytes: int
    min_ram_gb: int = 6
    notes: str = ""

    @property
    def approx_download_bytes(self) -> int:
        return int(self.model_bytes) + int(self.mmproj_bytes)

    def model_url(self) -> str:
        return (
            f"https://huggingface.co/{self.hf_repo}/resolve/main/{self.model_file}"
        )

    def mmproj_url(self) -> str:
        return (
            f"https://huggingface.co/{self.hf_repo}/resolve/main/{self.mmproj_file}"
        )

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
            "notes": self.notes,
            "is_default": self.id == DEFAULT_MODEL_ID,
        }


VISION_MODELS: dict[str, VisionModelSpec] = {
    DEFAULT_MODEL_ID: VisionModelSpec(
        id=DEFAULT_MODEL_ID,
        name="Qwen2.5-VL 3B",
        hf_repo=_QWEN_HF_REPO,
        model_file=_QWEN_MODEL_FILE,
        mmproj_file=_QWEN_MMPROJ_FILE,
        model_sha256=_QWEN_MODEL_SHA256,
        mmproj_sha256=_QWEN_MMPROJ_SHA256,
        model_bytes=_QWEN_MODEL_BYTES,
        mmproj_bytes=_QWEN_MMPROJ_BYTES,
        min_ram_gb=6,
        notes="Default visual decoder — screenshots, OCR, general photos",
    ),
}


@dataclass(frozen=True)
class LlamaRuntimeSpec:
    """Pinned llama-server runtime package for a platform flavor."""

    id: str
    tag: str
    platform: str  # win-cpu-x64 | win-cuda-12.4-x64
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
}

DEFAULT_RUNTIME_ID = "win-cpu-x64"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8740


def get_model_spec(model_id: str | None = None) -> VisionModelSpec:
    mid = (model_id or DEFAULT_MODEL_ID).strip() or DEFAULT_MODEL_ID
    if mid not in VISION_MODELS:
        raise KeyError(
            f"Unknown vision model_id {mid!r}. "
            f"Known: {', '.join(sorted(VISION_MODELS))}"
        )
    return VISION_MODELS[mid]


def get_runtime_spec(runtime_id: str | None = None) -> LlamaRuntimeSpec:
    rid = (runtime_id or DEFAULT_RUNTIME_ID).strip() or DEFAULT_RUNTIME_ID
    if rid not in LLAMA_RUNTIMES:
        raise KeyError(
            f"Unknown vision runtime {rid!r}. "
            f"Known: {', '.join(sorted(LLAMA_RUNTIMES))}"
        )
    return LLAMA_RUNTIMES[rid]


def catalog_public() -> dict[str, Any]:
    return {
        "default_model_id": DEFAULT_MODEL_ID,
        "models": [m.to_public_dict() for m in VISION_MODELS.values()],
        "llama_cpp_tag": LLAMA_CPP_TAG,
        "default_port": DEFAULT_PORT,
        "runtimes": [
            {
                "id": r.id,
                "platform": r.platform,
                "tag": r.tag,
                "zip_name": r.zip_name,
                "size_bytes": r.size_bytes,
            }
            for r in LLAMA_RUNTIMES.values()
        ],
    }


def total_install_bytes(model_id: str | None = None, runtime_id: str | None = None) -> int:
    model = get_model_spec(model_id)
    runtime = get_runtime_spec(runtime_id)
    extra = sum(a.size_bytes for a in runtime.extra_zips)
    return model.approx_download_bytes + runtime.size_bytes + extra
