"""Download + extract llama-server and the pinned vision GGUF/mmproj.

Supports cancel, HTTP Range resume of .partial files, and skip of verified assets.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import shutil
import tarfile
import threading
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from remedy.vision import progress as prog
from remedy.vision.catalog import (
    DEFAULT_MODEL_ID,
    DownloadAsset,
    get_model_spec,
    get_runtime_spec,
    normalize_runtime_id,
    total_install_bytes,
)
from remedy.vision.config import (
    downloads_dir,
    models_dir,
    runtime_dir,
    save_vision_json,
    vision_root,
)

logger = logging.getLogger(__name__)

_install_lock = threading.Lock()
_install_thread: threading.Thread | None = None
_cancel = threading.Event()


class InstallCancelled(Exception):  # noqa: N818 — public cancel signal name
    """Raised when the user cancels an in-flight install."""


def is_installing() -> bool:
    s = prog.snapshot()
    return s.get("phase") in ("downloading", "extracting", "verifying", "uninstalling")


def model_files_present(model_id: str, home_dir: str | Path | None = None) -> bool:
    spec = get_model_spec(model_id)
    d = models_dir(model_id, home_dir)
    return (d / spec.model_file).is_file() and (d / spec.mmproj_file).is_file()


def runtime_binary_path(home_dir: str | Path | None = None) -> Path | None:
    root = runtime_dir(home_dir)
    candidates = [
        root / "llama-server.exe",
        root / "llama-server",
    ]
    for c in candidates:
        if c.is_file():
            return c
    if root.is_dir():
        for p in root.rglob("llama-server.exe"):
            return p
        for p in root.rglob("llama-server"):
            return p
    return None


def is_installed(
    model_id: str | None = None,
    home_dir: str | Path | None = None,
) -> bool:
    """True if pinned model + runtime are available (this home or product bundle).

    Does **not** treat another user's/home's ``~/.remedy/vision`` as installed
    when a different ``home_dir`` is passed (keeps tests and multi-profile correct).
    Product bundles: ``REMEDY_LOCAL_BUNDLE`` / app ``resources/local`` only.
    """
    mid = model_id or DEFAULT_MODEL_ID
    if model_files_present(mid, home_dir) and runtime_binary_path(home_dir) is not None:
        return True
    try:
        from remedy.runtime.bundle import (
            model_paths_from_bundle,
            runtime_binary_from_bundle,
        )

        paths = model_paths_from_bundle(mid)
        if not paths:
            return False
        # Product bundle only (resources/local or REMEDY_LOCAL_BUNDLE) — not user-data
        root = paths["bundle_root"]
        root_s = str(root).replace("\\", "/").lower()
        is_product = (
            root.name == "local"
            or "/resources/local" in root_s
            or "/bundled/local" in root_s
        )
        env_b = __import__("os").environ.get("REMEDY_LOCAL_BUNDLE")
        if env_b:
            with contextlib.suppress(OSError):
                is_product = is_product or Path(env_b).resolve() in root.resolve().parents or Path(
                    env_b
                ).resolve() == root.resolve()
        if not is_product:
            return False
        if runtime_binary_from_bundle() is not None:
            return True
        if runtime_binary_path(home_dir) is not None:
            return True
    except Exception:
        pass
    return False


def _sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _check_cancel() -> None:
    if _cancel.is_set():
        raise InstallCancelled("Install cancelled by user")


def _asset_complete(path: Path, asset: DownloadAsset) -> bool:
    if not path.is_file():
        return False
    if asset.size_bytes and path.stat().st_size != asset.size_bytes:
        return False
    if asset.sha256:
        try:
            return _sha256_file(path).lower() == asset.sha256.lower()
        except OSError:
            return False
    return True


def _download_asset(
    asset: DownloadAsset,
    dest: Path,
    *,
    on_chunk: Callable[[int], None] | None = None,
    on_resume_bytes: Callable[[int], None] | None = None,
) -> None:
    """Download *asset* to *dest*, resuming from ``dest.partial`` when possible."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _asset_complete(dest, asset):
        if on_resume_bytes and asset.size_bytes:
            on_resume_bytes(asset.size_bytes)
        return

    partial = dest.with_suffix(dest.suffix + ".partial")
    existing = partial.stat().st_size if partial.is_file() else 0
    if existing and asset.size_bytes and existing > asset.size_bytes:
        partial.unlink(missing_ok=True)
        existing = 0

    def _stream_to_partial(*, resume: bool) -> None:
        nonlocal existing
        headers = {"User-Agent": "RemedyAI-vision/1.0"}
        mode = "wb"
        if resume and existing > 0:
            headers["Range"] = f"bytes={existing}-"
            mode = "ab"
            logger.info("Resuming %s from byte %s", asset.name, existing)
        req = Request(asset.url, headers=headers)
        with urlopen(req, timeout=120) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            # Range request ignored → full body; rewrite partial from scratch
            if resume and existing > 0 and status == 200:
                partial.unlink(missing_ok=True)
                existing = 0
                mode = "wb"
            with partial.open(mode) as out:
                while True:
                    _check_cancel()
                    block = resp.read(1024 * 256)
                    if not block:
                        break
                    out.write(block)
                    if on_chunk:
                        on_chunk(len(block))

    try:
        _stream_to_partial(resume=existing > 0)
    except HTTPError as e:
        if existing > 0 and e.code in (400, 416, 501):
            # Cannot resume — restart full download (keep cancel support)
            partial.unlink(missing_ok=True)
            existing = 0
            _stream_to_partial(resume=False)
        else:
            raise

    _check_cancel()
    if not partial.is_file():
        raise RuntimeError(f"Download produced no file for {asset.name}")
    got = partial.stat().st_size
    if asset.size_bytes and abs(got - asset.size_bytes) > max(
        1024 * 1024, asset.size_bytes * 0.02
    ):
        if got == 0:
            partial.unlink(missing_ok=True)
        raise RuntimeError(
            f"Size mismatch for {asset.name}: expected {asset.size_bytes}, got {got}"
        )
    if asset.sha256:
        digest = _sha256_file(partial)
        if digest.lower() != asset.sha256.lower():
            partial.unlink(missing_ok=True)
            raise RuntimeError(
                f"SHA256 mismatch for {asset.name}: expected {asset.sha256}, got {digest}"
            )
    partial.replace(dest)


def _extract_zip(zip_path: Path, dest_dir: Path) -> None:
    """Extract with Zip-Slip protection (same rules as skill pack import)."""
    _check_cancel()
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        try:
            from remedy.skills.exporter import _safe_extract_zip

            # Vision runtimes are large (GGUF / CUDA bins); SHA256 already checked.
            _safe_extract_zip(
                zf,
                dest_dir,
                max_files=50_000,
                max_member_bytes=8_000_000_000,  # 8 GiB per member
            )
        except ValueError:
            raise
        except Exception:
            # Fallback: manual safe extract if importer signature changes
            dest = dest_dir.resolve()
            for info in zf.infolist():
                name = info.filename
                if not name or name.endswith("/"):
                    continue
                cleaned = name.replace("\\", "/").lstrip("/")
                if ".." in cleaned.split("/"):
                    raise ValueError(f"Zip Slip blocked: {name}") from None
                target = (dest / cleaned).resolve()
                try:
                    target.relative_to(dest)
                except ValueError as exc:
                    raise ValueError(f"Zip Slip blocked: {name}") from exc
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info, "r") as src, open(target, "wb") as out:
                    out.write(src.read())


def _safe_member_path(dest: Path, name: str, *, kind: str) -> Path:
    cleaned = name.replace("\\", "/").lstrip("/")
    if not cleaned or ".." in cleaned.split("/"):
        raise ValueError(f"{kind} Slip blocked: {name}")
    if len(cleaned) > 1 and cleaned[1] == ":":
        raise ValueError(f"{kind} Slip blocked (absolute): {name}")
    target = (dest / cleaned).resolve()
    try:
        target.relative_to(dest)
    except ValueError as exc:
        raise ValueError(f"{kind} Slip blocked: {name}") from exc
    return target


def _extract_tar(tar_path: Path, dest_dir: Path) -> None:
    """Extract tar.gz with path-traversal + symlink protection."""
    _check_cancel()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir.resolve()
    max_files = 50_000
    max_member_bytes = 8_000_000_000
    max_total_bytes = min(max_files * max_member_bytes, max_member_bytes * 50)
    count = 0
    total_written = 0
    with tarfile.open(tar_path, "r:*") as tf:
        for info in tf.getmembers():
            _check_cancel()
            if info.issym() or info.islnk():
                raise ValueError(f"Tar symlink blocked: {info.name}")
            if not info.isfile():
                continue
            name = info.name or ""
            target = _safe_member_path(dest, name, kind="Tar")
            if info.size > max_member_bytes:
                raise ValueError(f"Tar member too large: {name}")
            count += 1
            if count > max_files:
                raise ValueError(f"Tar has too many files (>{max_files})")
            src = tf.extractfile(info)
            if src is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with src, target.open("wb") as out:
                while True:
                    block = src.read(1024 * 256)
                    if not block:
                        break
                    total_written += len(block)
                    if total_written > max_total_bytes:
                        raise ValueError(f"Tar too large after {name}")
                    out.write(block)
            if info.mode & 0o111:
                with contextlib.suppress(OSError):
                    target.chmod(target.stat().st_mode | 0o755)


def _ensure_posix_executables(root: Path) -> None:
    """llama-server + .so from official tarballs must be executable on Linux."""
    if os.name == "nt" or not root.is_dir():
        return
    names = {"llama-server", "llama-cli", "llama-quantize", "llama-bench"}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.name in names or p.suffix == ".so" or ".so." in p.name:
            with contextlib.suppress(OSError):
                p.chmod(p.stat().st_mode | 0o755)


def _extract_archive(archive_path: Path, dest_dir: Path) -> None:
    """Extract a llama.cpp zip or tar.gz into dest_dir, then chmod binaries."""
    name = archive_path.name.lower()
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        _extract_tar(archive_path, dest_dir)
    else:
        _extract_zip(archive_path, dest_dir)
    _ensure_posix_executables(dest_dir)


def _existing_progress_bytes(
    *,
    model_id: str,
    runtime_id: str,
    home_dir: str | Path | None,
) -> tuple[int, bool]:
    """Sum complete or partial bytes already on disk for progress resume display."""
    model = get_model_spec(model_id)
    runtime = get_runtime_spec(runtime_id)
    dl = downloads_dir(home_dir)
    mdir = models_dir(model_id, home_dir)
    total = 0
    any_partial = False
    for asset in (runtime.primary_asset(), *runtime.extra_zips):
        full = dl / asset.name
        part = full.with_suffix(full.suffix + ".partial")
        if _asset_complete(full, asset):
            total += asset.size_bytes or full.stat().st_size
        elif part.is_file():
            total += part.stat().st_size
            any_partial = True
    for asset in model.assets():
        full = mdir / asset.name
        part = full.with_suffix(full.suffix + ".partial")
        if _asset_complete(full, asset):
            total += asset.size_bytes or full.stat().st_size
        elif part.is_file():
            total += part.stat().st_size
            any_partial = True
    return total, any_partial


def _run_install(
    *,
    model_id: str,
    runtime_id: str,
    home_dir: str | Path | None,
    enable: bool,
    cfg: dict[str, Any] | None = None,
) -> None:
    try:
        model = get_model_spec(model_id)
        runtime = get_runtime_spec(runtime_id)
        total = total_install_bytes(model_id, runtime_id)
        already, resumed = _existing_progress_bytes(
            model_id=model_id, runtime_id=runtime_id, home_dir=home_dir
        )
        prog.begin(model_id, runtime_id, total, bytes_done=already, resumed=resumed)
        bytes_done = already

        def bump(n: int) -> None:
            nonlocal bytes_done
            bytes_done += n
            prog.update(
                bytes_done=min(bytes_done, total),
                message=f"Downloading {prog.snapshot().get('current_file') or '…'}",
            )

        dl = downloads_dir(home_dir)
        dl.mkdir(parents=True, exist_ok=True)
        rdir = runtime_dir(home_dir)
        mdir = models_dir(model_id, home_dir)
        mdir.mkdir(parents=True, exist_ok=True)

        # Runtime zip(s)
        for asset in (runtime.primary_asset(), *runtime.extra_zips):
            _check_cancel()
            prog.update(current_file=asset.name, phase="downloading")
            zip_path = dl / asset.name
            if not _asset_complete(zip_path, asset):
                _download_asset(asset, zip_path, on_chunk=bump)
            _check_cancel()
            prog.update(phase="extracting", message=f"Extracting {asset.name}")
            _extract_archive(zip_path, rdir)

        if runtime_binary_path(home_dir) is None:
            raise RuntimeError(
                "llama-server binary not found after extract. "
                f"Checked under {rdir}"
            )

        # Model + mmproj
        for asset in model.assets():
            _check_cancel()
            prog.update(
                phase="downloading",
                current_file=asset.name,
                message=f"Downloading {asset.name}",
            )
            dest = mdir / asset.name
            if _asset_complete(dest, asset):
                continue
            _download_asset(asset, dest, on_chunk=bump)

        _check_cancel()
        prog.update(phase="verifying", message="Writing vision.json", cancellable=False)
        from remedy.vision.catalog import DEFAULT_HOST, DEFAULT_PORT

        save_vision_json(
            {
                "enabled": bool(enable),
                "backend": "llama_server",
                "model_id": model_id,
                "model_path": str((mdir / model.model_file).resolve()),
                "mmproj_path": str((mdir / model.mmproj_file).resolve()),
                "runtime_id": runtime_id,
                "runtime_version": runtime.tag,
                "runtime_binary": str(runtime_binary_path(home_dir) or ""),
                "host": DEFAULT_HOST,
                "port": DEFAULT_PORT,
                "base_url": f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/v1",
                "auto_start": True,
            },
            home_dir,
        )
        # Final progress fill
        prog.update(bytes_done=total)
        prog.succeed(
            f"Installed {model.name} (llama.cpp {runtime.tag}) — starts with Remedy"
        )
        # Start with Remedy: launch llama-server as soon as install finishes
        # unless RMB already owns the GPU host.
        if enable:
            # The guard needs the loaded config to see llm_provider=rmb;
            # with None it only notices an RMB that is already running.
            skip_start = False
            try:
                from remedy.runtime.rmb.mode import should_skip_vision_stack

                guard_cfg = cfg
                if guard_cfg is None:
                    from remedy.interfaces.api_support import load_config

                    guard_cfg = load_config()
                skip_start = bool(should_skip_vision_stack(guard_cfg))
            except Exception:
                skip_start = False
            if skip_start:
                logger.info(
                    "Skipping SmolVLM start after install — RMB exclusive host"
                )
            else:
                try:
                    from remedy.vision.runtime import start_server

                    prog.update(message="Starting local model server…")
                    started = start_server(home_dir=home_dir, n_gpu_layers=-1, wait_s=90.0)
                    if started.get("ok"):
                        logger.info("Local model server started after install")
                    else:
                        logger.warning(
                            "Install ok but server start deferred: %s",
                            started.get("error"),
                        )
                except Exception:
                    logger.exception("Post-install auto-start failed (will retry on next launch)")
    except InstallCancelled:
        logger.info("Vision install cancelled")
        prog.cancelled()
    except Exception as e:
        logger.exception("Vision install failed")
        prog.fail(str(e) or repr(e))


def start_install(
    *,
    model_id: str | None = None,
    runtime_id: str | None = None,
    home_dir: str | Path | None = None,
    enable: bool = True,
    prefer_cuda: bool = False,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start background install. Returns current progress snapshot.

    *cfg* (the loaded Remedy config) lets the post-install start honour the
    RMB-exclusive-host guard; without it only a running RMB is detected.
    """
    global _install_thread
    mid = (model_id or DEFAULT_MODEL_ID).strip() or DEFAULT_MODEL_ID
    get_model_spec(mid)  # validate
    rid = normalize_runtime_id(runtime_id, prefer_gpu=prefer_cuda)
    get_runtime_spec(rid)

    with _install_lock:
        if is_installing() and _install_thread and _install_thread.is_alive():
            return {
                "ok": False,
                "error": "Install already in progress",
                **prog.snapshot(),
            }
        _cancel.clear()
        t = threading.Thread(
            target=_run_install,
            kwargs={
                "model_id": mid,
                "runtime_id": rid,
                "home_dir": home_dir,
                "enable": enable,
                "cfg": cfg,
            },
            name="remedy-vision-install",
            daemon=True,
        )
        _install_thread = t
        t.start()
    return {"ok": True, **prog.snapshot()}


def cancel_install() -> dict[str, Any]:
    """Request cancel of the in-flight install. Partial files are kept for resume."""
    thread_alive = bool(_install_thread and _install_thread.is_alive())
    if not is_installing() and not thread_alive:
        return {"ok": False, "error": "No install in progress", **prog.snapshot()}
    _cancel.set()
    prog.update(message="Cancelling…")
    return {"ok": True, "message": "Cancel requested", **prog.snapshot()}


def uninstall(
    *,
    home_dir: str | Path | None = None,
    keep_models: bool = False,
) -> dict[str, Any]:
    from remedy.vision.runtime import stop_server

    # Cancel any install first
    if is_installing():
        _cancel.set()

    prog.update(phase="uninstalling", message="Stopping server…", cancellable=False)
    with contextlib.suppress(Exception):
        stop_server(home_dir=home_dir)
    root = vision_root(home_dir)
    if not root.exists():
        prog.reset()
        return {"ok": True, "removed": False}
    if keep_models:
        rdir = runtime_dir(home_dir)
        if rdir.exists():
            shutil.rmtree(rdir, ignore_errors=True)
        vj = root / "vision.json"
        vj.unlink(missing_ok=True)
        dl = downloads_dir(home_dir)
        if dl.exists():
            shutil.rmtree(dl, ignore_errors=True)
    else:
        shutil.rmtree(root, ignore_errors=True)
    prog.reset()
    return {"ok": True, "removed": True, "keep_models": keep_models}


def wipe_vision_data(home_dir: str | Path | None = None) -> dict[str, Any]:
    """Full remove of managed vision tree (used by uninstall --purge / config wipe)."""
    return uninstall(home_dir=home_dir, keep_models=False)


def reinstall_runtime(
    *,
    prefer_cuda: bool = True,
    home_dir: str | Path | None = None,
    enable: bool = True,
    model_id: str | None = None,
) -> dict[str, Any]:
    """Re-download/extract llama-server (CPU or CUDA), keeping model GGUFs when present."""
    from remedy.vision.runtime import stop_server

    if is_installing():
        return {"ok": False, "error": "Install already in progress", **prog.snapshot()}
    with contextlib.suppress(Exception):
        stop_server(home_dir=home_dir)
    rdir = runtime_dir(home_dir)
    if rdir.exists():
        shutil.rmtree(rdir, ignore_errors=True)
    rid = normalize_runtime_id(None, prefer_gpu=prefer_cuda)
    # Drop matching zip so a different runtime is always fetched fresh
    dl = downloads_dir(home_dir)
    try:
        spec = get_runtime_spec(rid)
        for asset in (spec.primary_asset(), *spec.extra_zips):
            z = dl / asset.name
            z.unlink(missing_ok=True)
            z.with_suffix(z.suffix + ".partial").unlink(missing_ok=True)
    except Exception:
        pass
    return start_install(
        model_id=model_id or DEFAULT_MODEL_ID,
        runtime_id=rid,
        home_dir=home_dir,
        enable=enable,
        prefer_cuda=prefer_cuda,
    )
