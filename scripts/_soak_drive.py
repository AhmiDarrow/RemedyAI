"""Drive one live turn: help_read computer-use-soak + walk checklist."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from remedy.interfaces.local_auth import ensure_local_api_token

SID = "2eca4a86-3cc2-438f-87ad-3639a5e7d4a4"
OUT = Path(__file__).resolve().parents[1] / ".soak_stream.txt"

MSG = (
    "Read the F1 help article computer-use-soak using the help_read tool "
    "(id=computer-use-soak). Then walk me through the checklist: for each "
    "checkbox item, say PASS/FAIL/SKIP with a one-line reason based on what "
    "you can actually verify with tools right now (help_list, help_read, "
    "computer_monitors, computer_screenshot, list_dir, file_read). "
    "Do NOT claim help is outside access scope. Start with help_read."
)


def main() -> int:
    token = ensure_local_api_token()
    url = f"http://127.0.0.1:7400/api/sessions/{SID}/messages/stream"
    body = json.dumps({"message": MSG}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    chunks: list[str] = []
    tools: list[str] = []
    try:
        with urllib.request.urlopen(req, timeout=360) as resp:
            buf = b""
            while True:
                block = resp.read(4096)
                if not block:
                    break
                buf += block
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    s = line.decode("utf-8", errors="replace").rstrip("\r")
                    if not s.startswith("data:"):
                        continue
                    data = s[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    chunks.append(data)
                    low = data.lower()
                    if (
                        "@@tool" in low
                        or "help_read" in low
                        or "help_list" in low
                        or "computer_" in low
                    ):
                        tools.append(data[:240])
                    if len(chunks) % 25 == 0:
                        print(f"... {len(chunks)} events", flush=True)
    except Exception as e:
        print("STREAM_ERROR", type(e).__name__, e, flush=True)
        # Fallback non-stream
        url2 = f"http://127.0.0.1:7400/api/sessions/{SID}/messages"
        req2 = urllib.request.Request(
            url2,
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req2, timeout=360) as resp2:
                raw = resp2.read().decode("utf-8", errors="replace")
                OUT.write_text(raw, encoding="utf-8")
                print("FALLBACK_OK", len(raw), flush=True)
                print(raw[:4000], flush=True)
                return 0
        except Exception as e2:
            print("FALLBACK_ERROR", e2, flush=True)
            return 1

    text = "\n".join(chunks)
    OUT.write_text(text, encoding="utf-8")
    print("EVENTS", len(chunks), "TOOL_HINTS", len(tools), flush=True)
    for t in tools[:40]:
        print("TOOL", t[:200], flush=True)
    print("---TAIL---", flush=True)
    print(text[-5000:] if len(text) > 5000 else text, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
