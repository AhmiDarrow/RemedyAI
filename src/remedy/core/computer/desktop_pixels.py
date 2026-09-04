"""Windows pixel / SoM helpers — re-export tip shared ``desktop_common``.

Kept as a named Phase-1 sibling so ``desktop_capture_win`` / ``desktop_win``
can import a pixels surface without twinning shot/SoM policy.
"""

from __future__ import annotations

from typing import Any

from remedy.core.computer import desktop_common as C

PASTE_THRESHOLD = C.PASTE_THRESHOLD
remedy_home = C.remedy_home
default_shot_path = C.default_shot_path
purge_old_shots = C.purge_old_shots
write_png_bgr = C.write_png_bgr
draw_marks_on_bgr = C.draw_marks_on_bgr


def detect_ui_candidates(
    raw: bytes, stride: int, width: int, height: int, *, max_marks: int = 20
) -> list[dict[str, Any]]:
    cands = C.pixel_ui_candidates(raw, stride, width, height, max_marks=max_marks)
    for c in cands:
        c.pop("source", None)
    return cands
