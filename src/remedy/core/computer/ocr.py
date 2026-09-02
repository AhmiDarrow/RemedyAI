"""On-screen OCR for computer use — word boxes the agent can click.

DOM/UIA first. This is the path when those are empty or SmolVLM is idle
(RMB holds VRAM): read the pixels, return labeled boxes, click by text/ref.

Backends (first that works, no extra required install):
- Windows.Media.Ocr via winrt (optional) or built-in PowerShell
- tesseract CLI if present (Windows + Linux)
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_PS_TIMEOUT_S = 12.0
_TESS_TIMEOUT_S = 8.0

# Short labels that should score as buttons (matches elements._ACTION_VERBS).
_ACTION = frozenset(
    {
        "post",
        "submit",
        "send",
        "tweet",
        "publish",
        "share",
        "continue",
        "next",
        "save",
        "create",
        "reply",
        "comment",
        "search",
        "go",
        "done",
        "apply",
        "confirm",
        "update",
        "login",
        "signin",
    }
)
_COMPOSER = (
    "happening",
    "what's on",
    "whats on",
    "write a",
    "write your",
    "compose",
    "post text",
    "caption",
)

_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() |
  Where-Object {
    $_.Name -eq 'AsTask' -and
    $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
  })[0]
function Await-WinRT($async, [type]$type) {
  $task = $asTask.MakeGenericMethod($type).Invoke($null, @($async))
  $null = $task.Wait(20000)
  if (-not $task.IsCompleted) { throw 'OCR await timed out' }
  return $task.Result
}
[void][Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
[void][Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
$file = Await-WinRT ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$stream = Await-WinRT ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await-WinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await-WinRT ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$ocr = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $ocr) { Write-Output '[]'; return }
$result = Await-WinRT ($ocr.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$items = New-Object System.Collections.Generic.List[object]
$li = 0
foreach ($line in $result.Lines) {
  foreach ($word in $line.Words) {
    $b = $word.BoundingRect
    $items.Add([pscustomobject]@{
      text = [string]$word.Text
      x = [int]$b.X; y = [int]$b.Y
      w = [int]$b.Width; h = [int]$b.Height
      line = $li
    })
  }
  $li++
}
if ($items.Count -eq 0) { Write-Output '[]' }
elseif ($items.Count -eq 1) { Write-Output ('[' + ($items[0] | ConvertTo-Json -Compress) + ']') }
else { $items | ConvertTo-Json -Compress }
"""


def _cache_key(path: Path) -> str:
    try:
        st = path.stat()
        return f"{path.resolve()}:{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return str(path)


def _norm_word(w: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(w, dict):
        return None
    text = re.sub(r"\s+", " ", str(w.get("text") or "")).strip()
    if not text:
        return None
    if len(text) == 1 and not text.isalnum():
        return None
    try:
        ix = float(w.get("x") or 0)
        iy = float(w.get("y") or 0)
        iw = float(w.get("w") or 0)
        ih = float(w.get("h") or 0)
    except (TypeError, ValueError):
        return None
    if iw < 2 or ih < 2:
        return None
    line: int | None
    try:
        raw_line = w.get("line")
        line = int(raw_line) if raw_line is not None and raw_line != "" else None
    except (TypeError, ValueError):
        line = None
    return {"text": text, "x": ix, "y": iy, "w": iw, "h": ih, "line": line}


def _same_row(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a.get("line") is not None and b.get("line") is not None:
        return a["line"] == b["line"]
    ay1, ay2 = a["y"], a["y"] + a["h"]
    by1, by2 = b["y"], b["y"] + b["h"]
    overlap = min(ay2, by2) - max(ay1, by1)
    return overlap >= 0.45 * min(a["h"], b["h"])


def _merge_run(run: list[dict[str, Any]]) -> dict[str, Any]:
    xs = [w["x"] for w in run]
    ys = [w["y"] for w in run]
    rights = [w["x"] + w["w"] for w in run]
    bottoms = [w["y"] + w["h"] for w in run]
    x = min(xs)
    y = min(ys)
    return {
        "text": " ".join(w["text"] for w in run),
        "x": x,
        "y": y,
        "w": max(rights) - x,
        "h": max(bottoms) - y,
        "line": run[0].get("line"),
    }


def _ocr_runs(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Cluster words into same-line, small-gap runs (reading order)."""
    parsed = [p for w in (words or []) if (p := _norm_word(w)) is not None]
    if not parsed:
        return []
    parsed.sort(
        key=lambda w: (
            w["line"] if w["line"] is not None else w["y"],
            w["x"],
        )
    )
    rows: list[list[dict[str, Any]]] = []
    for w in parsed:
        if rows and _same_row(rows[-1][-1], w):
            rows[-1].append(w)
        else:
            rows.append([w])
    runs: list[list[dict[str, Any]]] = []
    for row in rows:
        row.sort(key=lambda w: w["x"])
        heights = sorted(w["h"] for w in row)
        med_h = heights[len(heights) // 2]
        gap_limit = max(20.0, 1.25 * med_h)
        current = [row[0]]
        for nxt in row[1:]:
            prev = current[-1]
            gap = nxt["x"] - (prev["x"] + prev["w"])
            if gap > gap_limit:
                runs.append(current)
                current = [nxt]
            else:
                current.append(nxt)
        runs.append(current)
    return runs


def group_ocr_phrases(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join adjacent same-line words into clickable phrases.

    Single OCR words cannot match ``What's happening?`` or ``Sign in``.
    A large horizontal gap (nav items) starts a new phrase; a normal
    space between words does not.
    """
    return [_merge_run(run) for run in _ocr_runs(words)]


def ocr_click_boxes(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Phrases plus each constituent word for click-by-text scoring.

    Merged lines make ``What's happening?`` clickable. Keeping the words
    means ``Reply`` on a ``Reply Retweet Share`` bar still hits Reply,
    not the bar's center.
    """
    out: list[dict[str, Any]] = []
    for run in _ocr_runs(words):
        out.append(_merge_run(run))
        if len(run) > 1:
            out.extend(run)
    return out


def infer_ocr_role(text: str) -> str:
    """Guess control kind so click-by-text scoring can prefer Post/composer."""
    t = (text or "").strip().lower()
    if not t:
        return "text"
    compact = re.sub(r"[^a-z0-9]+", "", t)
    toks = set(re.findall(r"[a-z0-9]{3,}", t))
    if compact in _ACTION or (toks & _ACTION and len(t) <= 28):
        return "button"
    if t.endswith("?") or any(h in t for h in _COMPOSER):
        return "textbox"
    return "text"


def words_to_elements(
    words: list[dict[str, Any]],
    *,
    scale: float = 1.0,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
    space: str = "page",
) -> list[dict[str, Any]]:
    """OCR words → snapshot-shaped elements (ref o1…).

    Adjacent same-line words become one phrase box so ``What's happening?``
    / ``Sign in`` / ``Add a GIF`` are clickable labels, not three misses.
    Multi-word runs also keep each word so ``Reply`` on a toolbar still
    clicks Reply, not the bar's center.

    ``space=page``: image pixels / scale (Browser rail CSS coords).
    ``space=screen``: image pixels + origin (desktop click coords).
    """
    sc = float(scale) if scale and scale > 0 else 1.0
    out: list[dict[str, Any]] = []
    n = 0
    for w in ocr_click_boxes(words):
        text = str(w.get("text") or "").strip()
        if not text:
            continue
        ix = float(w["x"])
        iy = float(w["y"])
        iw = float(w["w"])
        ih = float(w["h"])
        cx = ix + iw / 2.0
        cy = iy + ih / 2.0
        if space == "screen":
            x, y = cx + float(origin_x), cy + float(origin_y)
        else:
            x, y = cx / sc, cy / sc
        n += 1
        role = infer_ocr_role(text)
        tag = "textarea" if role == "textbox" else "ocr"
        out.append(
            {
                "ref": f"o{n}",
                "tag": tag,
                "role": role,
                "name": text[:80],
                "text": text[:80],
                "x": int(round(x)),
                "y": int(round(y)),
                "w": int(round(iw / sc if space != "screen" else iw)),
                "h": int(round(ih / sc if space != "screen" else ih)),
                "source": "ocr",
            }
        )
        if n >= 120:
            break
    return out


def merge_ocr_elements(
    existing: list[dict[str, Any]] | None,
    ocr_els: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep DOM/UIA refs; replace previous OCR boxes."""
    kept = [
        e
        for e in list(existing or [])
        if not str(e.get("ref") or "").lower().startswith("o")
    ]
    return kept + list(ocr_els or [])


def read_screenshot_ocr(path: str | Path, *, force: bool = False) -> dict[str, Any]:
    """Return ``{ok, backend, words, text}``. Never raises."""
    p = Path(path)
    empty: dict[str, Any] = {"ok": False, "backend": "", "words": [], "text": ""}
    if not p.is_file():
        empty["error"] = "no screenshot file"
        return empty
    key = _cache_key(p)
    if not force and key in _CACHE:
        return dict(_CACHE[key][1])
    result = empty
    for fn, name in (
        (_ocr_winrt, "windows.media.ocr"),
        (_ocr_powershell, "windows.media.ocr"),
        (_ocr_tesseract, "tesseract"),
    ):
        try:
            words = fn(p)
        except Exception as exc:
            logger.debug("ocr backend %s failed: %s", name, exc)
            continue
        if words is None:
            continue
        text = " ".join(str(w.get("text") or "") for w in words).strip()
        result = {
            "ok": True,
            "backend": name,
            "words": words,
            "text": text[:4000],
        }
        break
    else:
        result = {
            "ok": False,
            "backend": "",
            "words": [],
            "text": "",
            "error": "no OCR backend (Windows.Media.Ocr / tesseract)",
        }
    _CACHE[key] = (time.time(), result)
    if len(_CACHE) > 16:
        oldest = sorted(_CACHE.items(), key=lambda kv: kv[1][0])[:8]
        for k, _ in oldest:
            _CACHE.pop(k, None)
    return dict(result)


def _ocr_winrt(path: Path) -> list[dict[str, Any]] | None:
    if sys.platform != "win32":
        return None
    try:
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage import FileAccessMode, StorageFile
    except Exception:
        return None
    import asyncio

    async def _run() -> list[dict[str, Any]]:
        file = await StorageFile.get_file_from_path_async(str(path))
        stream = await file.open_async(FileAccessMode.READ)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            return []
        recognized = await engine.recognize_async(bitmap)
        words: list[dict[str, Any]] = []
        for li, line in enumerate(recognized.lines):
            for word in line.words:
                box = word.bounding_rect
                words.append(
                    {
                        "text": str(word.text or ""),
                        "x": float(box.x),
                        "y": float(box.y),
                        "w": float(box.width),
                        "h": float(box.height),
                        "line": li,
                    }
                )
        return words

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_run())
    # Already in an event loop — do not nest. Fall through to PowerShell.
    return None


def _ocr_powershell(path: Path) -> list[dict[str, Any]] | None:
    if sys.platform != "win32":
        return None
    # Hidden, no profile — this is a local OS capability, not a shell the owner sees.
    from remedy.execution.process import hidden_subprocess_kwargs

    escaped = str(path.resolve()).replace("'", "''")
    script = f"$Path = '{escaped}'\n" + _PS_SCRIPT
    proc = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=_PS_TIMEOUT_S,
        env={**os.environ, "TERM": "dumb"},
        **hidden_subprocess_kwargs(),
    )
    if proc.returncode != 0:
        logger.debug("ocr powershell rc=%s err=%s", proc.returncode, (proc.stderr or "")[:300])
        return None
    raw = (proc.stdout or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return None
    words: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        line_v: int | None
        try:
            raw_line = item.get("line")
            line_v = int(raw_line) if raw_line is not None and raw_line != "" else None
        except (TypeError, ValueError):
            line_v = None
        rec: dict[str, Any] = {
            "text": str(item.get("text") or ""),
            "x": float(item.get("x") or 0),
            "y": float(item.get("y") or 0),
            "w": float(item.get("w") or 0),
            "h": float(item.get("h") or 0),
        }
        if line_v is not None:
            rec["line"] = line_v
        words.append(rec)
    return words


def _ocr_tesseract(path: Path) -> list[dict[str, Any]] | None:
    exe = shutil.which("tesseract")
    if not exe:
        return None
    from remedy.execution.process import hidden_subprocess_kwargs

    proc = subprocess.run(
        [exe, str(path), "stdout", "tsv", "-l", "eng"],
        capture_output=True,
        text=True,
        timeout=_TESS_TIMEOUT_S,
        **hidden_subprocess_kwargs(),
    )
    if proc.returncode != 0:
        return None
    lines = (proc.stdout or "").splitlines()
    if len(lines) < 2:
        return []
    header = lines[0].split("\t")
    try:
        i_text = header.index("text")
        i_left = header.index("left")
        i_top = header.index("top")
        i_w = header.index("width")
        i_h = header.index("height")
        i_conf = header.index("conf")
        i_level = header.index("level")
    except ValueError:
        return None
    i_block = header.index("block_num") if "block_num" in header else -1
    i_par = header.index("par_num") if "par_num" in header else -1
    i_line = header.index("line_num") if "line_num" in header else -1
    words: list[dict[str, Any]] = []
    for line in lines[1:]:
        cols = line.split("\t")
        need = [i_text, i_left, i_top, i_w, i_h, i_conf, i_level]
        if i_block >= 0:
            need.append(i_block)
        if i_par >= 0:
            need.append(i_par)
        if i_line >= 0:
            need.append(i_line)
        if len(cols) <= max(need):
            continue
        try:
            if int(float(cols[i_level])) != 5:
                continue
            conf = float(cols[i_conf])
        except ValueError:
            continue
        if conf < 40:
            continue
        text = (cols[i_text] or "").strip()
        if not text:
            continue
        rec: dict[str, Any] = {
            "text": text,
            "x": float(cols[i_left]),
            "y": float(cols[i_top]),
            "w": float(cols[i_w]),
            "h": float(cols[i_h]),
        }
        if i_line >= 0:
            try:
                block = int(float(cols[i_block])) if i_block >= 0 else 0
                par = int(float(cols[i_par])) if i_par >= 0 else 0
                rec["line"] = block * 10000 + par * 100 + int(float(cols[i_line]))
            except ValueError:
                pass
        words.append(rec)
    return words
