"""Session file attachments (drag-drop / paste / picker).

Files land under ``~/.remedy/attachments/<session_id>/`` and are referenced
in chat so the agent can read them via tools or (for images) multimodal input.
"""

from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

# Per-file cap; total batch should stay under ~25 MiB for API sanity.
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
MAX_IMAGE_VISION_BYTES = 4 * 1024 * 1024
# Prefer full attachments; only emergency OOM guard if ever needed.
MAX_TEXT_INJECT_CHARS = 0  # 0 = no inject truncation

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\- ()\[\]]+")

IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/gif",
    "image/webp",
    "image/bmp",
}

TEXT_LIKE = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/html",
    "text/css",
    "text/javascript",
    "application/json",
    "application/xml",
    "application/x-yaml",
    "application/yaml",
    "application/javascript",
    "application/typescript",
}


def attachments_root(home_dir: str | Path | None = None) -> Path:
    if home_dir:
        return Path(home_dir).expanduser() / "attachments"
    return Path.home() / ".remedy" / "attachments"


def session_attachments_dir(session_id: str, home_dir: str | Path | None = None) -> Path:
    safe_sid = re.sub(r"[^A-Za-z0-9._-]", "_", session_id)[:80]
    d = attachments_root(home_dir) / safe_sid
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_path_under_attachments(
    path: str | Path,
    *,
    home_dir: str | Path | None = None,
    session_id: str | None = None,
) -> bool:
    """True when *path* resolves under the session (or global) attachments tree.

    Client-supplied ``AttachmentRef.path`` values must pass this before
    snippet inject / multimodal read — otherwise a local API caller can
    exfiltrate arbitrary files into LLM context via forged attachment paths.
    """
    raw = str(path or "").strip()
    if not raw or "\x00" in raw:
        return False
    try:
        candidate = Path(raw).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return False
    roots: list[Path] = []
    if session_id:
        try:
            roots.append(session_attachments_dir(session_id, home_dir).resolve())
        except OSError:
            pass
    try:
        roots.append(attachments_root(home_dir).resolve())
    except OSError:
        pass
    # Always allow default ~/.remedy/attachments even when home_dir is custom.
    try:
        roots.append((Path.home() / ".remedy" / "attachments").resolve())
    except OSError:
        pass
    for root in roots:
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def filter_jailed_attachments(
    attachments: list[dict[str, Any]] | None,
    *,
    home_dir: str | Path | None = None,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """Drop attachment refs whose path escapes the attachments tree."""
    out: list[dict[str, Any]] = []
    for a in attachments or []:
        if not isinstance(a, dict):
            continue
        p = a.get("path")
        if p is None or p == "":
            # Pathless metadata only — keep for name display, never read body.
            out.append(a)
            continue
        if is_path_under_attachments(p, home_dir=home_dir, session_id=session_id):
            out.append(a)
    return out


def sanitize_filename(name: str) -> str:
    base = Path(name or "file").name
    base = _SAFE_NAME.sub("_", base).strip("._") or "file"
    if len(base) > 180:
        stem, suf = Path(base).stem[:140], Path(base).suffix[:40]
        base = f"{stem}{suf}"
    return base


def unique_path(directory: Path, filename: str) -> Path:
    """Legacy helper: pick a free path with _1, _2… if needed."""
    name = sanitize_filename(filename)
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, suf = Path(name).stem, Path(name).suffix
    for i in range(1, 1000):
        alt = directory / f"{stem}_{i}{suf}"
        if not alt.exists():
            return alt
    return directory / f"{stem}_{uuid4().hex[:8]}{suf}"


def storage_path(directory: Path, filename: str) -> tuple[Path, str]:
    """Return (disk_path, display_name).

    Always keeps the original sanitized filename for the UI. Re-uploads of the
    same name in a session overwrite the previous file (no notes_1.txt / notes_3.txt).
    """
    name = sanitize_filename(filename)
    return directory / name, name


def guess_mime(filename: str, declared: str | None = None) -> str:
    if declared and declared != "application/octet-stream":
        return declared
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


def is_probably_text(mime: str, filename: str) -> bool:
    if mime in TEXT_LIKE or mime.startswith("text/"):
        return True
    ext = Path(filename).suffix.lower()
    return ext in {
        ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".txt", ".csv",
        ".yml", ".yaml", ".toml", ".ini", ".cfg", ".rs", ".go", ".java",
        ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".sh", ".ps1",
        ".sql", ".html", ".css", ".scss", ".vue", ".svelte", ".xml", ".log",
        ".env", ".gitignore", ".dockerfile", ".makefile", ".r", ".swift",
        ".kt", ".scala", ".lua", ".pl", ".ex", ".exs", ".zig",
    }


def is_image(mime: str) -> bool:
    return mime in IMAGE_TYPES or mime.startswith("image/")


def save_upload(
    *,
    session_id: str,
    filename: str,
    data: bytes,
    content_type: str | None = None,
    home_dir: str | Path | None = None,
) -> dict[str, Any]:
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise ValueError(
            f"File too large ({len(data)} bytes). Max is {MAX_ATTACHMENT_BYTES // (1024*1024)} MB."
        )
    directory = session_attachments_dir(session_id, home_dir)
    path, display_name = storage_path(directory, filename)
    path.write_bytes(data)
    mime = guess_mime(display_name, content_type)
    return {
        "id": uuid4().hex[:12],
        "name": display_name,  # original name — never notes_3.txt from unique_path
        "path": str(path.resolve()),
        "mime": mime,
        "size": len(data),
        "is_image": is_image(mime),
        "is_text": is_probably_text(mime, display_name),
    }


def markdown_image_embed(name: str, path: str) -> str:
    """Markdown image for chat display (provider-agnostic).

    Uses forward slashes and angle-bracket targets when the path has spaces
    or parens so CommonMark/GFM parsers keep the whole path.
    """
    alt = (name or Path(path).name or "image").replace("]", "").replace("\n", " ")
    p = str(path or "").replace("\\", "/").strip()
    if not p:
        return ""
    if any(ch in p for ch in (" ", "(", ")", "[", "]")):
        return f"![{alt}](<{p}>)"
    return f"![{alt}]({p})"


def build_attachment_prompt_block(attachments: list[dict[str, Any]]) -> str:
    """Human-readable block appended to the user message for history + tools.

    Images are embedded as markdown ``![…](path)`` so the chat UI renders them
    for **every** model (display is independent of provider vision).
    """
    if not attachments:
        return ""
    lines = ["", "---"]
    # Inline previews first — MessageFeed / ChatImage render these regardless of LLM.
    for a in attachments:
        name = str(a.get("name") or Path(str(a.get("path", ""))).name or "file")
        path = str(a.get("path") or "")
        mime = str(a.get("mime") or "unknown")
        if path and (a.get("is_image") or is_image(mime)):
            emb = markdown_image_embed(name, path)
            if emb:
                lines.append(emb)
    lines.append("Attached files (saved for this session):")
    for a in attachments:
        name = a.get("name") or Path(str(a.get("path", ""))).name
        path = a.get("path") or ""
        mime = a.get("mime") or "unknown"
        size = int(a.get("size") or 0)
        size_s = f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} B"
        lines.append(f"- `{path}` ({name}, {mime}, {size_s})")
    lines.append(
        "Use `file_read` on these paths when you need the full content. "
        "Images above are shown in chat; vision-capable models (or the local "
        "visual decoder) can also understand their pixels."
    )
    return "\n".join(lines)


def inject_text_file_snippets(
    attachments: list[dict[str, Any]],
    *,
    home_dir: str | Path | None = None,
    session_id: str | None = None,
) -> str:
    """Inline small text files so the model sees them immediately.

    Only reads paths under the attachments tree (path jail).
    """
    chunks: list[str] = []
    for a in filter_jailed_attachments(
        attachments, home_dir=home_dir, session_id=session_id
    ):
        path = Path(str(a.get("path") or ""))
        if not path.is_file():
            continue
        mime = str(a.get("mime") or "")
        name = a.get("name") or path.name
        if not (a.get("is_text") or is_probably_text(mime, name)):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not text.strip():
            continue
        if MAX_TEXT_INJECT_CHARS > 0 and len(text) > MAX_TEXT_INJECT_CHARS:
            text = text[:MAX_TEXT_INJECT_CHARS] + "\n…[truncated]"
        lang = path.suffix.lstrip(".") or "text"
        chunks.append(f"\n### Attached: {name}\n```{lang}\n{text}\n```\n")
    return "".join(chunks)


def build_multimodal_user_content(
    message: str,
    attachments: list[dict[str, Any]] | None,
    *,
    vision_mode: str = "native",
    decode_brief: str | None = None,
    home_dir: str | Path | None = None,
    session_id: str | None = None,
) -> str | list[dict[str, Any]]:
    """Build OpenAI-style user content (string or multimodal parts).

    ``vision_mode``:
      - ``native`` — attach image_url parts when images are present
      - ``decode`` — inject local visual-decoder text; no image_url parts
      - ``text_only`` — paths only (no pixels, no decoder brief)

    Attachment paths outside the attachments tree are dropped (path jail).
    """
    atts = filter_jailed_attachments(
        attachments, home_dir=home_dir, session_id=session_id
    )
    block = build_attachment_prompt_block(atts)
    snippets = inject_text_file_snippets(
        atts, home_dir=home_dir, session_id=session_id
    )
    text = (message or "").strip()
    if block:
        text = f"{text}{block}" if text else block.lstrip()
    if snippets:
        text = f"{text}\n{snippets}"

    mode = (vision_mode or "native").strip().lower()
    brief = (decode_brief or "").strip()
    if mode == "decode" and brief:
        text = f"{text}\n\n{brief}".strip() if text else brief
        return text

    if mode != "native":
        return text

    image_parts: list[dict[str, Any]] = []
    for a in atts:
        path = Path(str(a.get("path") or ""))
        mime = str(a.get("mime") or "image/png")
        if not path.is_file() or not (a.get("is_image") or is_image(mime)):
            continue
        # Content-type validation: refuse SVG/scriptable image/* as vision payload
        # (SVG can carry script; browser/webview XSS surface).
        mime_l = mime.lower().split(";")[0].strip()
        if mime_l in ("image/svg+xml", "image/svg") or path.suffix.lower() == ".svg":
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if len(raw) > MAX_IMAGE_VISION_BYTES:
            # Still referenced by path; skip vision payload.
            continue
        # Magic-byte soft check for common raster types (skip polyglot / empty).
        if not _looks_like_raster_image(raw):
            continue
        b64 = base64.standard_b64encode(raw).decode("ascii")
        image_parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            }
        )

    if not image_parts:
        return text

    parts: list[dict[str, Any]] = [{"type": "text", "text": text or "(see attached image)"}]
    parts.extend(image_parts)
    return parts


def _looks_like_raster_image(data: bytes) -> bool:
    """True for common safe raster magic (PNG/JPEG/GIF/WEBP/BMP). Fail closed."""
    if not data or len(data) < 8:
        return False
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    if data[:2] == b"BM":
        return True
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return True
    return False
