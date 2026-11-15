"""Browser check for the chat feed's smart scrolling.

Run the harness first (from desktop/):
    npx vite --config harness/vite.config.ts
then:
    python harness/scroll_check.py [--chrome /path/to/chrome]

Needs the `playwright` Python package. Exits non-zero on any failure.
"""

from __future__ import annotations

import argparse
import sys

from playwright.sync_api import Page, sync_playwright

BASE = "http://127.0.0.1:5199/harness/live-turn/"
FEED = ".message-feed"


def metrics(page: Page) -> dict:
    return page.evaluate(
        f"""() => {{
        const f = document.querySelector('{FEED}');
        return {{ top: f.scrollTop, max: f.scrollHeight - f.clientHeight }};
    }}"""
    )


def at_bottom(page: Page, slack: int = 4) -> bool:
    m = metrics(page)
    return m["max"] - m["top"] <= slack


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chrome", default=None, help="Chrome/Chromium executable")
    args = ap.parse_args()
    failures: list[str] = []

    def check(ok: bool, name: str) -> None:
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            failures.append(name)

    with sync_playwright() as p:
        kw = {"args": ["--no-sandbox"]}
        if args.chrome:
            kw["executable_path"] = args.chrome
        browser = p.chromium.launch(**kw)
        page = browser.new_page(viewport={"width": 760, "height": 640})

        # 1. Follow while at the bottom, pause on scroll-up, stay put.
        page.goto(BASE + "?history=8&h=600&speed=0.6")
        page.wait_for_timeout(2500)
        check(at_bottom(page), "follows the stream while at the bottom")
        box = page.locator(FEED).bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.wheel(0, -500)
        page.wait_for_timeout(300)
        held = metrics(page)["top"]
        page.wait_for_timeout(3000)  # tokens, tool rows and progress keep arriving
        moved = abs(metrics(page)["top"] - held)
        check(moved <= 2, f"scrolling up mid-stream stays put (moved {moved:.0f}px)")
        check(page.locator(".scroll-latest-pill").is_visible(), "Jump to latest pill appears")

        # 2. Jump resumes following.
        page.locator(".scroll-latest-pill").click()
        page.wait_for_timeout(1500)
        check(at_bottom(page), "jump to latest resumes following")

        # 3. End key: ignored while typing in the composer, jumps otherwise.
        page.mouse.wheel(0, -500)
        page.wait_for_timeout(300)
        page.locator('[data-testid="composer"]').focus()
        before = metrics(page)["top"]
        page.keyboard.press("End")
        page.wait_for_timeout(200)
        focused = page.evaluate("document.activeElement?.dataset?.testid")
        check(focused == "composer", "End keeps focus in the composer")
        check(
            not at_bottom(page) and abs(metrics(page)["top"] - before) <= 40,
            "End in composer does not jump the feed",
        )
        page.evaluate("document.activeElement.blur()")
        page.keyboard.press("End")
        page.wait_for_timeout(300)
        check(at_bottom(page), "End jumps to latest")

        # 4. Expanding the Working strip does not yank the view.
        page.goto(BASE + "?done=1&history=8&h=600")
        page.wait_for_timeout(800)
        head = page.locator(".work-strip .ws-head").last
        head.scroll_into_view_if_needed()
        page.evaluate(f"document.querySelector('{FEED}').scrollTop -= 150")
        page.wait_for_timeout(300)
        y0 = head.bounding_box()["y"]
        head.click()
        page.wait_for_timeout(400)
        y1 = head.bounding_box()["y"]
        check(
            abs(y1 - y0) <= 2,
            f"expanding the strip keeps its header in place (moved {y1 - y0:.0f}px)",
        )
        y2 = head.bounding_box()["y"]
        head.click()
        page.wait_for_timeout(400)
        y3 = head.bounding_box()["y"]
        check(
            abs(y3 - y2) <= 2,
            f"collapsing the strip keeps its header in place (moved {y3 - y2:.0f}px)",
        )

        # 5. Reduced motion: no pulsing dots.
        rm = browser.new_page(viewport={"width": 760, "height": 640}, reduced_motion="reduce")
        rm.goto(BASE + "?t=4000&h=600")
        rm.wait_for_timeout(600)
        anim = rm.evaluate(
            "getComputedStyle(document.querySelector('.work-strip .live-stream-dot')).animationName"
        )
        check(anim == "none", "prefers-reduced-motion stops the live pulse")

        browser.close()

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
