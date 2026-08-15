"""Pull GGUF weights from Hugging Face into ~/.remedy/rmb/models/.

Public Hub HTTP only — no SDK. Inputs:

- file URL (``/resolve/`` or ``/blob/``) → download that file
- ``owner/repo`` → list ``.gguf`` files, user picks one
- bare name → search, then the user picks a repo (same model, many hosts)

Downloads are a single GET to ``/{repo}/resolve/{rev}/{file}`` with HTTP Range
resume. Dest is always the RMB models directory (basename only so the disk
scan finds it).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, build_opener

from remedy.runtime.rmb.config import models_dir

logger = logging.getLogger(__name__)

HF_API = "https://huggingface.co"
_UA = "RemedyAI/1.0"
_REPO_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)+$"
)
_ALLOWED_HOSTS = frozenset(
    {
        "huggingface.co",
        "www.huggingface.co",
        "hf.co",
        "www.hf.co",
    }
)
_QUANT_RANK = (
    "q4_k_m",
    "q4_k_s",
    "q5_k_m",
    "q5_k_s",
    "q4_k",
    "q5_k",
    "q6_k",
    "q8_0",
    "q3_k_m",
    "q4_0",
    "q5_0",
    "q3_k",
    "iq4_xs",
    "iq4_nl",
    "q2_k",
)

_progress_lock = threading.Lock()
_cancel = threading.Event()
_pull_thread: threading.Thread | None = None
_progress: dict[str, Any] = {
    "phase": "idle",
    "query": "",
    "repo": "",
    "filename": "",
    "bytes_done": 0,
    "bytes_total": 0,
    "pct": 0,
    "error": None,
    "path": None,
    "message": "",
}


class HfError(ValueError):
    """User-facing Hugging Face input / API error."""


class HfCancelled(Exception):  # noqa: N818 — public cancel signal
    """Raised when the user cancels an in-flight pull."""


@dataclass(frozen=True)
class HfHint:
    kind: str  # url | repo | search
    query: str
    repo: str | None = None
    revision: str | None = None
    filename: str | None = None
    url: str | None = None

    def to_public(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "query": self.query,
            "repo": self.repo,
            "revision": self.revision,
            "filename": self.filename,
            "url": self.url,
        }


_MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024 * 1024
_MAX_HF_JSON_BYTES = 8 * 1024 * 1024
_pull_guard = threading.Lock()


def _host_allowed(host: str) -> bool:
    h = (host or "").lower().split("@")[-1].split(":")[0].strip(".")
    if h in _ALLOWED_HOSTS:
        return True
    return h.endswith(".huggingface.co") or h.endswith(".hf.co")


def _auth_headers() -> dict[str, str]:
    token = (
        os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or ""
    ).strip()
    headers = {"User-Agent": _UA, "Accept": "*/*"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class _HfRedirectHandler:
    """Follow redirects only onto Hugging Face / HF CDN hosts."""

    def __init__(self) -> None:
        import urllib.request

        class _Handler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
                parsed = urlparse(newurl)
                if parsed.scheme != "https" or not _host_allowed(
                    parsed.hostname or parsed.netloc
                ):
                    raise HfError("Hugging Face redirect left allowed hosts")
                return urllib.request.HTTPRedirectHandler.redirect_request(
                    self, req, fp, code, msg, headers, newurl
                )

        self._opener = build_opener(_Handler)

    def open(self, req: Request, timeout: float = 30.0) -> Any:
        return self._opener.open(req, timeout=timeout)


_opener = _HfRedirectHandler()


def _urlopen(req: Request, *, timeout: float = 30.0) -> Any:
    return _opener.open(req, timeout=timeout)


def progress_snapshot() -> dict[str, Any]:
    with _progress_lock:
        return dict(_progress)


def _set_progress(**kwargs: Any) -> None:
    with _progress_lock:
        _progress.update(kwargs)
        total = int(_progress.get("bytes_total") or 0)
        done = int(_progress.get("bytes_done") or 0)
        _progress["pct"] = int(done * 100 / total) if total > 0 else 0


def reset_progress() -> None:
    _set_progress(
        phase="idle",
        query="",
        repo="",
        filename="",
        bytes_done=0,
        bytes_total=0,
        pct=0,
        error=None,
        path=None,
        message="",
    )


def cancel_pull() -> dict[str, Any]:
    snap = progress_snapshot()
    if snap.get("phase") not in ("downloading", "loading"):
        return {"ok": False, "error": "No download in progress"}
    _cancel.set()
    return {"ok": True, "progress": progress_snapshot()}


def _check_cancel() -> None:
    if _cancel.is_set():
        raise HfCancelled("Download cancelled")


def sanitize_repo(repo: str) -> str:
    raw = (repo or "").strip().strip("/")
    if not raw or not _REPO_RE.match(raw) or ".." in raw:
        raise HfError(f"Invalid Hugging Face repo id: {repo!r}")
    if raw.lower().startswith(("datasets/", "spaces/", "models/")):
        raise HfError("Only model repos are supported")
    return raw


def sanitize_filename(name: str) -> str:
    raw = unquote((name or "").strip().replace("\\", "/")).lstrip("/")
    if not raw or ".." in raw.split("/"):
        raise HfError(f"Invalid GGUF path: {name!r}")
    base = raw.split("/")[-1]
    if not base.lower().endswith(".gguf"):
        raise HfError("Only .gguf files can be pulled")
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._+-]*\.gguf$", base, re.I):
        raise HfError(f"Unsafe GGUF filename: {base!r}")
    return raw


def sanitize_revision(rev: str | None) -> str:
    raw = (rev or "main").strip() or "main"
    if not re.match(r"^[A-Za-z0-9._/-]+$", raw) or ".." in raw:
        raise HfError(f"Invalid revision: {rev!r}")
    return raw


def resolve_url(repo: str, filename: str, revision: str | None = None) -> str:
    repo_id = sanitize_repo(repo)
    file_id = sanitize_filename(filename)
    rev = sanitize_revision(revision)
    encoded = "/".join(quote(part, safe="") for part in file_id.split("/"))
    return f"{HF_API}/{repo_id}/resolve/{rev}/{encoded}"


def parse_hf_hint(raw: str) -> HfHint:
    """Classify a name, ``owner/repo``, or Hugging Face URL."""
    text = (raw or "").strip()
    if not text:
        raise HfError("Enter a model name, owner/repo, or Hugging Face URL")
    if len(text) > 500:
        raise HfError("Query is too long")
    if text.endswith("?download=true"):
        text = text[: -len("?download=true")]
    # Drop other query / fragment
    if "://" in text:
        parsed = urlparse(text.split()[0])
        if parsed.scheme not in ("http", "https"):
            raise HfError("Only http(s) Hugging Face URLs are allowed")
        if not _host_allowed(parsed.netloc):
            raise HfError("Only huggingface.co / hf.co URLs are allowed")
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2:
            raise HfError("URL is missing owner/repo")
        repo = sanitize_repo(f"{parts[0]}/{parts[1]}")
        rest = parts[2:]
        revision = "main"
        filename: str | None = None
        if rest and rest[0] in ("resolve", "blob", "tree"):
            if len(rest) >= 2:
                revision = sanitize_revision(unquote(rest[1]))
            if rest[0] in ("resolve", "blob") and len(rest) >= 3:
                filename = sanitize_filename("/".join(unquote(p) for p in rest[2:]))
        elif rest and rest[-1].lower().endswith(".gguf"):
            filename = sanitize_filename("/".join(unquote(p) for p in rest))
        url = resolve_url(repo, filename, revision) if filename else None
        kind = "url" if filename else "repo"
        return HfHint(
            kind=kind,
            query=text,
            repo=repo,
            revision=revision,
            filename=filename,
            url=url,
        )

    cleaned = text.replace("\\", "/").strip("/")
    if "://" in cleaned:
        raise HfError("Only huggingface.co / hf.co URLs are allowed")
    bits = [b for b in cleaned.split("/") if b]
    if len(bits) >= 2 and _REPO_RE.match(f"{bits[0]}/{bits[1]}"):
        repo = sanitize_repo(f"{bits[0]}/{bits[1]}")
        rest = bits[2:]
        filename = None
        if rest and rest[-1].lower().endswith(".gguf"):
            filename = sanitize_filename("/".join(rest))
        if filename:
            url = resolve_url(repo, filename, "main")
            return HfHint(
                kind="url",
                query=text,
                repo=repo,
                revision="main",
                filename=filename,
                url=url,
            )
        return HfHint(kind="repo", query=text, repo=repo, revision="main")
    return HfHint(kind="search", query=text)


def _hf_json(url: str, *, timeout: float = 30.0) -> Any:
    req = Request(url, headers={**_auth_headers(), "Accept": "application/json"})
    try:
        with _urlopen(req, timeout=timeout) as resp:
            chunks: list[bytes] = []
            total = 0
            while True:
                block = resp.read(65536)
                if not block:
                    break
                total += len(block)
                if total > _MAX_HF_JSON_BYTES:
                    raise HfError("Hugging Face API response too large")
                chunks.append(block)
            raw = b"".join(chunks)
            headers = {k.lower(): v for k, v in (resp.headers.items() if resp.headers else [])}
    except HTTPError as exc:
        raise HfError(f"Hugging Face API HTTP {exc.code}") from exc
    except URLError as exc:
        raise HfError(f"Hugging Face API unreachable: {exc.reason}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HfError("Hugging Face API returned invalid JSON") from exc
    return data, headers


def search_gguf_repos(query: str, *, limit: int = 12) -> list[dict[str, Any]]:
    """Search model repos that publish GGUF. Never auto-picks a host."""
    q = (query or "").strip()
    if not q:
        raise HfError("Enter a model name to search")
    if len(q) > 200:
        raise HfError("Search query is too long")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def _ingest(rows: Any) -> None:
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            rid = str(row.get("id") or "").strip()
            if not rid or rid in seen:
                continue
            try:
                rid = sanitize_repo(rid)
            except HfError:
                continue
            seen.add(rid)
            tags = row.get("tags") if isinstance(row.get("tags"), list) else []
            out.append(
                {
                    "id": rid,
                    "downloads": int(row.get("downloads") or 0),
                    "likes": int(row.get("likes") or 0),
                    "tags": [str(t) for t in tags[:16]],
                    "pipeline_tag": row.get("pipeline_tag"),
                    "private": bool(row.get("private")),
                }
            )

    lim = max(1, min(int(limit), 30))
    # Library filter first — precise. Fallback prefixes GGUF when the filter
    # is too strict for a given name.
    params = [
        f"search={quote(q)}&filter=gguf&sort=downloads&direction=-1&limit={lim}",
        f"search={quote('GGUF ' + q)}&sort=downloads&direction=-1&limit={lim}",
        f"search={quote(q)}&sort=downloads&direction=-1&limit={max(6, lim // 2)}",
    ]
    for qs in params:
        try:
            data, _headers = _hf_json(f"{HF_API}/api/models?{qs}")
        except HfError as exc:
            logger.info("Hugging Face search failed (%s): %s", qs, exc)
            continue
        _ingest(data)
        if len(out) >= 3:
            break
    out.sort(key=lambda r: (-int(r.get("downloads") or 0), str(r.get("id") or "")))
    return out[:lim]


def _is_multipart_skip(path: str) -> bool:
    """Keep only the first shard of split GGUFs (``-00001-of-``)."""
    lower = path.lower()
    if "-of-0" not in lower and "-of-00" not in lower:
        return False
    name = Path(path).name.lower()
    return "00001" not in name and "00000" not in name


def _quant_key(name: str) -> tuple[int, str]:
    stem = name.lower().replace(".gguf", "")
    for i, tag in enumerate(_QUANT_RANK):
        if tag in stem:
            return (i, stem)
    return (len(_QUANT_RANK) + 1, stem)


def _file_role(path: str) -> str:
    name = Path(path).name.lower()
    if "mmproj" in name:
        return "mmproj"
    if "-00001-of-" in name:
        return "shard"
    return "weights"


def list_gguf_files(repo: str, *, revision: str | None = None) -> list[dict[str, Any]]:
    repo_id = sanitize_repo(repo)
    rev = sanitize_revision(revision)
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(20):
        url = f"{HF_API}/api/models/{repo_id}/tree/{quote(rev, safe='')}?recursive=true"
        if cursor:
            url += f"&cursor={quote(cursor)}"
        data, headers = _hf_json(url)
        rows = data if isinstance(data, list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("type") or "") != "file":
                continue
            path = str(row.get("path") or "")
            if ".gguf" not in path.lower():
                continue
            if _is_multipart_skip(path):
                continue
            try:
                safe = sanitize_filename(path)
            except HfError:
                continue
            size = int(row.get("size") or 0)
            role = _file_role(safe)
            items.append(
                {
                    "path": safe,
                    "name": Path(safe).name,
                    "size": size,
                    "size_gb": round(size / (1024**3), 2) if size else 0.0,
                    "role": role,
                    "recommended": False,
                    "url": resolve_url(repo_id, safe, rev),
                }
            )
        cursor = headers.get("x-next-cursor")
        if not cursor or not rows:
            break
    items.sort(key=lambda f: (_quant_key(str(f.get("name") or "")), str(f.get("name"))))
    # Mark the first non-mmproj as recommended so the UI can pre-select
    # without hiding the rest.
    for row in items:
        if row.get("role") == "weights":
            row["recommended"] = True
            break
    if items and not any(r.get("recommended") for r in items):
        items[0]["recommended"] = True
    return items


def _listed_file_size(repo_id: str, filename: str, revision: str | None) -> int:
    """Tree listing size for *filename*, or 0 if the listing has none."""
    try:
        want = Path(filename).name.lower()
        want_path = filename.replace("\\", "/").lower()
        for row in list_gguf_files(repo_id, revision=revision):
            path = str(row.get("path") or "").replace("\\", "/").lower()
            name = str(row.get("name") or "").lower()
            if path == want_path or name == want:
                return int(row.get("size") or 0)
    except Exception:
        return 0
    return 0


def dest_path(
    filename: str,
    *,
    home_dir: str | Path | None = None,
    repo: str | None = None,
    expected_size: int = 0,
) -> Path:
    """Flatten to models/{basename}.gguf so the disk scan finds it.

    When *repo* is set, always use ``{owner}--{repo}--{basename}`` so two
    hosts with the same GGUF name cannot skip/clobber each other.
    """
    _ = expected_size  # callers still pass size; dest no longer branches on it
    base = Path(sanitize_filename(filename)).name
    root = models_dir(home_dir)
    dest = root / base
    if not repo:
        return dest
    try:
        slug = sanitize_repo(repo).replace("/", "--")
    except HfError:
        return dest
    return root / f"{slug}--{base}"


def _content_length(resp: Any) -> int:
    try:
        return int(resp.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        return 0


def _download_one(
    url: str,
    dest: Path,
    *,
    expected_size: int = 0,
    on_chunk: Any | None = None,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if expected_size and expected_size > _MAX_DOWNLOAD_BYTES:
        raise HfError(
            f"{dest.name} is {expected_size} bytes — over the "
            f"{_MAX_DOWNLOAD_BYTES // (1024**3)} GiB pull cap"
        )
    if dest.is_file() and dest.stat().st_size > 64:
        if expected_size and dest.stat().st_size == expected_size:
            if on_chunk:
                on_chunk(expected_size)
            return dest

    partial = dest.with_suffix(dest.suffix + ".partial")
    existing = partial.stat().st_size if partial.is_file() else 0
    if existing and expected_size and existing > expected_size:
        partial.unlink(missing_ok=True)
        existing = 0

    def _stream(*, resume: bool) -> None:
        nonlocal existing
        headers = _auth_headers()
        mode = "wb"
        if resume and existing > 0:
            headers["Range"] = f"bytes={existing}-"
            mode = "ab"
        req = Request(url, headers=headers)
        with _urlopen(req, timeout=120.0) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            if resume and existing > 0 and status == 200:
                partial.unlink(missing_ok=True)
                existing = 0
                mode = "wb"
            total = existing + _content_length(resp)
            if expected_size:
                total = max(total, expected_size)
            if total:
                _set_progress(bytes_total=total, bytes_done=existing)
            with partial.open(mode) as out:
                wrote = existing if mode == "ab" else 0
                while True:
                    _check_cancel()
                    block = resp.read(1024 * 256)
                    if not block:
                        break
                    wrote += len(block)
                    if wrote > _MAX_DOWNLOAD_BYTES:
                        raise HfError(
                            f"{dest.name} exceeded the "
                            f"{_MAX_DOWNLOAD_BYTES // (1024**3)} GiB pull cap"
                        )
                    out.write(block)
                    if on_chunk:
                        on_chunk(len(block))

    try:
        _stream(resume=existing > 0)
    except HTTPError as exc:
        if existing > 0 and exc.code in (400, 416, 501):
            partial.unlink(missing_ok=True)
            existing = 0
            _stream(resume=False)
        else:
            raise HfError(f"Download failed (HTTP {exc.code})") from exc
    except URLError as exc:
        raise HfError(f"Download failed: {exc.reason}") from exc

    _check_cancel()
    if not partial.is_file():
        raise HfError(f"Download produced no file for {dest.name}")
    got = partial.stat().st_size
    if expected_size and abs(got - expected_size) > max(1024 * 1024, expected_size * 0.02):
        if got == 0:
            partial.unlink(missing_ok=True)
        raise HfError(f"Size mismatch for {dest.name}: expected {expected_size}, got {got}")
    if got < 64:
        partial.unlink(missing_ok=True)
        raise HfError(f"Download too small for {dest.name}")
    partial.replace(dest)
    return dest


def _sibling_parts(filename: str) -> list[str]:
    name = Path(sanitize_filename(filename)).name
    m = re.search(r"-(\d{5})-of-(\d{5})\.", name, re.I)
    if not m:
        return [filename]
    total = int(m.group(2))
    if total <= 1 or total > 64:
        return [filename]
    out: list[str] = []
    parent = str(Path(filename).parent).replace("\\", "/")
    for i in range(1, total + 1):
        part = re.sub(
            r"-\d{5}-of-\d{5}",
            f"-{i:05d}-of-{total:05d}",
            name,
            count=1,
        )
        out.append(f"{parent}/{part}" if parent not in (".", "") else part)
    return out


def download_gguf(
    *,
    repo: str | None = None,
    filename: str | None = None,
    revision: str | None = None,
    url: str | None = None,
    home_dir: str | Path | None = None,
    expected_size: int = 0,
) -> dict[str, Any]:
    """Download one GGUF (and sibling shards) into the RMB models dir."""
    if url and not repo:
        hint = parse_hf_hint(url)
        repo = hint.repo
        filename = hint.filename
        revision = hint.revision
        url = hint.url
    if not repo or not filename:
        raise HfError("Need a repo and GGUF filename (or a file URL)")
    repo_id = sanitize_repo(repo)
    file_id = sanitize_filename(filename)
    rev = sanitize_revision(revision)
    if not expected_size:
        expected_size = _listed_file_size(repo_id, file_id, rev)
    dest = dest_path(
        file_id, home_dir=home_dir, repo=repo_id, expected_size=expected_size
    )
    parts = _sibling_parts(file_id)
    _set_progress(
        phase="downloading",
        repo=repo_id,
        filename=Path(file_id).name,
        bytes_done=0,
        bytes_total=expected_size,
        error=None,
        path=None,
        message=f"Downloading {Path(file_id).name}",
    )

    def _bump(n: int) -> None:
        snap = progress_snapshot()
        _set_progress(bytes_done=int(snap.get("bytes_done") or 0) + int(n))

    last = dest
    for part in parts:
        part_url = resolve_url(repo_id, part, rev)
        part_size = (
            expected_size
            if part == file_id
            else _listed_file_size(repo_id, part, rev)
        )
        part_dest = dest_path(
            part,
            home_dir=home_dir,
            repo=repo_id,
            expected_size=part_size,
        )
        last = _download_one(
            part_url,
            part_dest,
            expected_size=part_size,
            on_chunk=_bump,
        )
    _set_progress(
        phase="loading",
        path=str(dest),
        bytes_done=dest.stat().st_size if dest.is_file() else 0,
        message=f"Saved {dest.name}",
    )
    return {
        "ok": True,
        "path": str(dest),
        "repo": repo_id,
        "filename": Path(file_id).name,
        "url": resolve_url(repo_id, file_id, rev),
        "parts": len(parts),
        "already": last == dest and dest.is_file(),
    }


def is_pulling() -> bool:
    t = _pull_thread
    return bool(t and t.is_alive())


def start_pull(
    *,
    repo: str | None = None,
    filename: str | None = None,
    revision: str | None = None,
    url: str | None = None,
    home_dir: str | Path | None = None,
    expected_size: int = 0,
    load: bool = True,
) -> dict[str, Any]:
    """Start a background pull. Returns immediately."""
    global _pull_thread
    hint_url = url
    if hint_url and not repo:
        parsed = parse_hf_hint(hint_url)
        repo = parsed.repo
        filename = parsed.filename
        revision = parsed.revision
    if not repo or not filename:
        raise HfError("Need a repo and GGUF filename (or a file URL)")

    def _worker() -> None:
        try:
            result = download_gguf(
                repo=repo,
                filename=filename,
                revision=revision,
                url=url,
                home_dir=home_dir,
                expected_size=expected_size,
            )
            if load and result.get("path"):
                try:
                    from remedy.runtime.rmb.service import apply_rmb_settings

                    _check_cancel()
                    _set_progress(
                        phase="loading",
                        path=result["path"],
                        message=f"Loading {Path(str(result['path'])).name}…",
                    )
                    live = True
                    try:
                        from remedy.core.turn_context import any_stream_claimed

                        if any_stream_claimed():
                            live = False
                    except Exception:
                        live = True
                    apply_rmb_settings(
                        {
                            "model_path": result["path"],
                            "enabled": True,
                            "use_as_chat_provider": True,
                        },
                        home_dir=home_dir,
                        live=live,
                        wait_s=120.0,
                    )
                    _check_cancel()
                    if live:
                        _set_progress(
                            phase="ready",
                            path=result["path"],
                            message=f"Loaded {Path(str(result['path'])).name}",
                        )
                    else:
                        _set_progress(
                            phase="ready",
                            path=result["path"],
                            message=(
                                f"Downloaded {Path(str(result['path'])).name} — "
                                "load after the current turn"
                            ),
                        )
                except HfCancelled:
                    raise
                except Exception as exc:
                    logger.exception("RMB load after Hugging Face pull failed")
                    _set_progress(
                        phase="error",
                        path=result.get("path"),
                        error=str(exc),
                        message=f"Downloaded, load failed: {exc}",
                    )
            else:
                _set_progress(
                    phase="ready",
                    path=result.get("path"),
                    message=f"Saved {Path(str(result.get('path') or 'model')).name}",
                )
        except HfCancelled:
            _set_progress(phase="cancelled", error="Cancelled", message="Cancelled")
        except Exception as exc:
            logger.exception("Hugging Face pull failed")
            _set_progress(phase="error", error=str(exc), message=str(exc))

    with _pull_guard:
        if is_pulling():
            return {"ok": True, "started": False, "progress": progress_snapshot()}
        _cancel.clear()
        _set_progress(
            phase="downloading",
            query=url or f"{repo}/{filename}",
            repo=repo,
            filename=Path(filename).name,
            bytes_done=0,
            bytes_total=expected_size,
            error=None,
            path=None,
            message="Starting download…",
        )
        t = threading.Thread(target=_worker, name="rmb-hf-pull", daemon=True)
        _pull_thread = t
        t.start()
    return {"ok": True, "started": True, "progress": progress_snapshot()}


def resolve_query(query: str) -> dict[str, Any]:
    """Parse *query* and fetch the next choice list (repos or files)."""
    hint = parse_hf_hint(query)
    out: dict[str, Any] = {
        "ok": True,
        "hint": hint.to_public(),
        "repos": [],
        "files": [],
        "need": hint.kind,
    }
    if hint.kind == "search":
        out["repos"] = search_gguf_repos(hint.query)
        if not out["repos"]:
            out["ok"] = False
            out["error"] = f"No GGUF repos matched {hint.query!r}"
        else:
            out["need"] = "repo"
    elif hint.kind == "repo" and hint.repo:
        out["files"] = list_gguf_files(hint.repo, revision=hint.revision)
        if not out["files"]:
            out["ok"] = False
            out["error"] = f"No .gguf files in {hint.repo}"
        else:
            out["need"] = "file"
    elif hint.kind == "url" and hint.filename:
        out["files"] = [
            {
                "path": hint.filename,
                "name": Path(hint.filename).name,
                "size": 0,
                "size_gb": 0.0,
                "role": _file_role(hint.filename),
                "recommended": True,
                "url": hint.url,
            }
        ]
        out["need"] = "pull"
    return out
