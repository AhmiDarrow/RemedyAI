"""Pinned local model catalog — re-exports shared runtime catalog.

All local roles (vision, nano, helper) use the same DEFAULT_MODEL_ID / Qwen GGUF.
"""

from __future__ import annotations

# Re-export shared single-model catalog (do not define a second model here).
from remedy.runtime.catalog import (  # noqa: F401
    BUNDLED_RUNTIME_IDS,
    DEFAULT_HOST,
    DEFAULT_LOCAL_MODEL_ID,
    DEFAULT_MODEL_ID,
    DEFAULT_PORT,
    DEFAULT_RUNTIME_ID,
    LLAMA_CPP_TAG,
    LLAMA_RUNTIMES,
    LOCAL_ROLES,
    VISION_MODELS,
    DownloadAsset,
    LlamaRuntimeSpec,
    LocalModelSpec,
    VisionModelSpec,
    catalog_public,
    get_model_spec,
    get_runtime_spec,
    total_bundle_bytes,
    total_install_bytes,
)

__all__ = [
    "BUNDLED_RUNTIME_IDS",
    "DEFAULT_HOST",
    "DEFAULT_LOCAL_MODEL_ID",
    "DEFAULT_MODEL_ID",
    "DEFAULT_PORT",
    "DEFAULT_RUNTIME_ID",
    "LLAMA_CPP_TAG",
    "LLAMA_RUNTIMES",
    "LOCAL_ROLES",
    "VISION_MODELS",
    "DownloadAsset",
    "LlamaRuntimeSpec",
    "LocalModelSpec",
    "VisionModelSpec",
    "catalog_public",
    "get_model_spec",
    "get_runtime_spec",
    "total_bundle_bytes",
    "total_install_bytes",
]
