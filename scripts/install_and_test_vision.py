"""Install the visual decoder and smoke-test a decode.

Usage:
  python scripts/install_and_test_vision.py
  python scripts/install_and_test_vision.py --skip-install  # only decode if already installed
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Ensure src on path when run from repo root
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from remedy.vision import progress as prog  # noqa: E402
from remedy.vision.catalog import DEFAULT_MODEL_ID, default_runtime_id  # noqa: E402
from remedy.vision.decoder import decode_image  # noqa: E402
from remedy.vision.install import is_installed, start_install  # noqa: E402
from remedy.vision.service import ensure_server, get_status  # noqa: E402


def wait_install(timeout_s: float = 7200.0) -> dict:
    t0 = time.time()
    last = ""
    while True:
        s = prog.snapshot()
        phase = s.get("phase")
        done = int(s.get("bytes_done") or 0)
        total = max(1, int(s.get("bytes_total") or 0))
        pct = min(100.0, 100.0 * done / total)
        msg = (
            f"{phase} {pct:5.1f}% {done // (1024 * 1024)}MB/"
            f"{total // (1024 * 1024)}MB "
            f"{s.get('current_file') or ''} {s.get('message') or ''}"
        )
        if msg != last:
            print(msg, flush=True)
            last = msg
        if phase == "ready":
            return s
        if phase == "error":
            raise RuntimeError(s.get("error") or "install failed")
        if time.time() - t0 > timeout_s:
            raise TimeoutError(f"install timeout: {s}")
        time.sleep(5)


def make_test_png(path: Path) -> Path:
    """Write a 256x256 RGB PNG (solid UI-like shapes). 1x1 PNG fails some VLMs."""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    w, h = 256, 256

    def px(x: int, y: int) -> bytes:
        if 40 <= y <= 80 and 20 <= x <= 230:
            return bytes([20, 20, 20])
        if 100 <= y <= 180 and 60 <= x <= 190:
            return bytes([220, 50, 50])
        return bytes([240, 240, 245])

    raw = b""
    for y in range(h):
        raw += b"\x00"
        for x in range(w):
            raw += px(x, y)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-install", action="store_true")
    ap.add_argument("--timeout", type=float, default=7200.0)
    args = ap.parse_args()

    cfg = {
        "vision": {
            "enabled": True,
            "model_id": DEFAULT_MODEL_ID,
            "force_decode": False,
        }
    }

    if not args.skip_install and not is_installed():
        print("Starting install (CPU llama-server + SmolVLM2 2.2B)...", flush=True)
        r = start_install(
            model_id=DEFAULT_MODEL_ID,
            runtime_id=default_runtime_id(),
            enable=True,
            prefer_cuda=False,
        )
        print("kickoff", r.get("ok"), r.get("phase"), flush=True)
        if not r.get("ok") and "already" not in str(r.get("error") or "").lower():
            # still wait if install in progress
            pass
        wait_install(timeout_s=args.timeout)
    else:
        print("Skip install / already installed:", is_installed(), flush=True)

    st = get_status(cfg)
    print("status", json.dumps({k: st.get(k) for k in (
        "enabled", "installed", "ready", "running", "model_id", "base_url"
    )}, default=str), flush=True)
    if not st.get("installed"):
        print("NOT INSTALLED", flush=True)
        return 1

    # Ensure vision.json has enabled
    from remedy.vision.config import load_vision_json, save_vision_json

    side = load_vision_json()
    side["enabled"] = True
    save_vision_json(side)

    print("Starting llama-server...", flush=True)
    started = ensure_server(cfg)
    print("start", started, flush=True)
    if not started.get("ok"):
        return 2

    st = get_status(cfg)
    base = st.get("base_url") or started.get("base_url")
    img = make_test_png(Path.home() / ".remedy" / "vision" / "test_dot.png")
    print("Decoding", img, "via", base, flush=True)
    result = decode_image(
        img,
        base_url=str(base),
        timeout_s=180.0,
        extra_question="Describe this image briefly.",
    )
    print("decode_ok", result.get("ok"), flush=True)
    print("decode_error", result.get("error"), flush=True)
    text = (result.get("text") or "")[:1500]
    print("decode_text:\n", text, flush=True)
    if not result.get("ok") or not text.strip():
        return 3
    print("VISION SMOKE TEST PASSED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
