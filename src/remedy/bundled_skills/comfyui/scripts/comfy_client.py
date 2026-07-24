#!/usr/bin/env python3
"""Minimal ComfyUI REST helper for Remedy's bundled comfyui skill.

Stdlib only. Default base: http://127.0.0.1:8188
Override with COMFYUI_URL or REMEDY_COMFYUI_URL.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def base_url() -> str:
    return (
        os.environ.get("COMFYUI_URL", "").strip()
        or os.environ.get("REMEDY_COMFYUI_URL", "").strip()
        or "http://127.0.0.1:8188"
    ).rstrip("/")


def _request(
    method: str,
    path: str,
    *,
    data: bytes | None = None,
    timeout: float = 30.0,
) -> Any:
    url = f"{base_url()}{path}"
    headers = {"Accept": "application/json", "User-Agent": "Remedy-ComfyUI-Skill/1.0"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return None
            ctype = resp.headers.get("Content-Type", "")
            if "json" in ctype or raw[:1] in (b"{", b"["):
                return json.loads(raw.decode("utf-8"))
            return raw
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {e.code} {url}: {body[:500]}") from e
    except urllib.error.URLError as e:
        raise SystemExit(
            f"Cannot reach ComfyUI at {base_url()} ({e.reason}). "
            "Start it with: python main.py --listen"
        ) from e


def cmd_status(_: argparse.Namespace) -> int:
    stats = _request("GET", "/system_stats", timeout=5.0)
    print(json.dumps({"base_url": base_url(), "system_stats": stats}, indent=2))
    return 0


def cmd_queue(args: argparse.Namespace) -> int:
    workflow = json.loads(Path(args.workflow).read_text(encoding="utf-8"))
    # Accept raw workflow or {"prompt": {...}} wrappers
    if isinstance(workflow, dict) and "prompt" in workflow and len(workflow) <= 3:
        payload = workflow
    else:
        payload = {"prompt": workflow}
    if args.client_id:
        payload["client_id"] = args.client_id
    result = _request("POST", "/prompt", data=json.dumps(payload).encode("utf-8"))
    print(json.dumps(result, indent=2))
    if isinstance(result, dict) and result.get("prompt_id"):
        print(result["prompt_id"], file=sys.stderr)
    return 0


def cmd_wait(args: argparse.Namespace) -> int:
    prompt_id = args.prompt_id
    deadline = time.time() + float(args.timeout)
    while time.time() < deadline:
        hist = _request("GET", f"/history/{prompt_id}", timeout=10.0)
        if isinstance(hist, dict) and prompt_id in hist:
            print(json.dumps(hist[prompt_id], indent=2))
            return 0
        time.sleep(float(args.interval))
    raise SystemExit(f"Timeout waiting for prompt_id={prompt_id} ({args.timeout}s)")


def _iter_images(history_entry: dict[str, Any]) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    outputs = history_entry.get("outputs") or {}
    if not isinstance(outputs, dict):
        return images
    for _node_id, node_out in outputs.items():
        if not isinstance(node_out, dict):
            continue
        for img in node_out.get("images") or []:
            if isinstance(img, dict) and img.get("filename"):
                images.append(
                    {
                        "filename": str(img.get("filename") or ""),
                        "subfolder": str(img.get("subfolder") or ""),
                        "type": str(img.get("type") or "output"),
                    }
                )
    return images


def cmd_download(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    hist = _request("GET", f"/history/{args.prompt_id}", timeout=15.0)
    if not isinstance(hist, dict) or args.prompt_id not in hist:
        raise SystemExit(f"No history for prompt_id={args.prompt_id}")
    entry = hist[args.prompt_id]
    saved: list[str] = []
    for img in _iter_images(entry):
        qs = urllib.parse.urlencode(img)
        data = _request("GET", f"/view?{qs}", timeout=60.0)
        if not isinstance(data, (bytes, bytearray)):
            continue
        dest = out_dir / img["filename"]
        dest.write_bytes(data)
        saved.append(str(dest.resolve()))
    print(json.dumps({"saved": saved, "count": len(saved)}, indent=2))
    return 0 if saved else 1


def cmd_run(args: argparse.Namespace) -> int:
    # queue
    qns = argparse.Namespace(
        workflow=args.workflow,
        client_id=args.client_id,
    )
    # Capture prompt_id from queue response
    workflow = json.loads(Path(args.workflow).read_text(encoding="utf-8"))
    if isinstance(workflow, dict) and "prompt" in workflow and len(workflow) <= 3:
        payload = workflow
    else:
        payload = {"prompt": workflow}
    if args.client_id:
        payload["client_id"] = args.client_id
    result = _request("POST", "/prompt", data=json.dumps(payload).encode("utf-8"))
    if not isinstance(result, dict) or not result.get("prompt_id"):
        print(json.dumps(result, indent=2))
        raise SystemExit("No prompt_id in queue response")
    prompt_id = str(result["prompt_id"])
    print(json.dumps({"queued": result}, indent=2))

    # wait
    wns = argparse.Namespace(
        prompt_id=prompt_id,
        timeout=args.timeout,
        interval=args.interval,
    )
    cmd_wait(wns)

    # download
    if args.out:
        dns = argparse.Namespace(prompt_id=prompt_id, out=args.out)
        return cmd_download(dns)
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    hist = _request("GET", "/history", timeout=15.0)
    if not isinstance(hist, dict):
        print(json.dumps(hist, indent=2))
        return 0
    ids = list(hist.keys())
    if args.limit and args.limit > 0:
        ids = ids[-args.limit :]
    print(json.dumps({"count": len(hist), "ids": ids}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Remedy ComfyUI client")
    p.add_argument(
        "--base-url",
        default=None,
        help="Override COMFYUI_URL for this invocation",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="GET /system_stats")
    s.set_defaults(func=cmd_status)

    q = sub.add_parser("queue", help="POST workflow JSON to /prompt")
    q.add_argument("workflow", help="Path to API-format workflow JSON")
    q.add_argument("--client-id", default=None)
    q.set_defaults(func=cmd_queue)

    w = sub.add_parser("wait", help="Poll /history/{id} until done")
    w.add_argument("prompt_id")
    w.add_argument("--timeout", type=float, default=300.0)
    w.add_argument("--interval", type=float, default=1.5)
    w.set_defaults(func=cmd_wait)

    d = sub.add_parser("download", help="Download images for a prompt id")
    d.add_argument("prompt_id")
    d.add_argument("--out", required=True, help="Output directory")
    d.set_defaults(func=cmd_download)

    r = sub.add_parser("run", help="Queue + wait + optional download")
    r.add_argument("workflow")
    r.add_argument("--out", default=None, help="If set, download images here")
    r.add_argument("--timeout", type=float, default=300.0)
    r.add_argument("--interval", type=float, default=1.5)
    r.add_argument("--client-id", default=None)
    r.set_defaults(func=cmd_run)

    h = sub.add_parser("history", help="List history prompt ids")
    h.add_argument("--limit", type=int, default=20)
    h.set_defaults(func=cmd_history)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.base_url:
        os.environ["COMFYUI_URL"] = args.base_url.rstrip("/")
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
