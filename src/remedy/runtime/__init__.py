"""Shared local runtime: pinned SmolVLM2 + llama-server for vision, nano, helper.

One model id for every local neural role. Same bytes for every install of a
given Remedy release (prebundled; no per-PC model download for the default).
"""

from __future__ import annotations

from remedy.runtime.catalog import (
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
    default_runtime_id,
    get_model_spec,
    get_runtime_spec,
    host_runtime_ids,
    normalize_runtime_id,
    total_install_bytes,
)
from remedy.runtime.roles import LocalRole, role_uses_mmproj

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
    "LocalRole",
    "VisionModelSpec",
    "catalog_public",
    "default_runtime_id",
    "get_model_spec",
    "get_runtime_spec",
    "host_runtime_ids",
    "normalize_runtime_id",
    "role_uses_mmproj",
    "total_install_bytes",
]
