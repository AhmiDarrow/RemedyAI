"""Stdlib HTML → markdown extract (no extra dependency).

Drops chrome (script/nav/footer), keeps article/main text, emits a small
markdown subset so ``web_fetch`` returns readable page body instead of raw HTML.
"""

from __future__ import annotations

import html as html_lib
import re
from html.parser import HTMLParser

_DROP = frozenset(
    {
        "script",
        "style",
        "noscript",
        "svg",
        "canvas",
        "iframe",
        "template",
        "form",
        "nav",
        "footer",
        "aside",
        "header",
        "button",
        "select",
        "textarea",
    }
)
_HEADING = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}
_JS_HINTS = (
    "enable javascript",
    "enable js",
    "requires javascript",
    "turn on javascript",
    "please enable cookies",
    "checking your browser",
    "just a moment",
    "cf-browser-verification",
)
_WS_RE = re.compile(r"[ \t\f\v]+")
_NL_RE = re.compile(r"\n{3,}")
_HTML_HEAD_RE = re.compile(r"(?is)^\s*(<!doctype\s+html|<html\b)")


def looks_like_html(raw: bytes | str, *, content_type: str = "") -> bool:
    ctype = (content_type or "").lower()
    if "html" in ctype:
        return True
    if any(t in ctype for t in ("json", "xml", "javascript", "octet-stream", "image/", "pdf")):
        return False
    sample = raw[:800] if isinstance(raw, (bytes, bytearray)) else str(raw)[:800]
    if isinstance(sample, (bytes, bytearray)):
        try:
            sample = sample.decode("utf-8", errors="ignore")
        except Exception:
            return False
    return bool(_HTML_HEAD_RE.match(sample) or "<body" in sample.lower()[:800])


class _ExtractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._in_pre = 0
        self._in_code = 0
        self._in_a = 0
        self._a_href = ""
        self._a_text: list[str] = []
        self._parts: list[str] = []
        self.title = ""
        self._in_title = 0
        self._title_bits: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in _DROP:
            self._skip += 1
            return
        if self._skip:
            return
        if t == "title":
            self._in_title += 1
            return
        if t == "br":
            self._parts.append("\n")
            return
        if t == "hr":
            self._parts.append("\n---\n")
            return
        if t in _HEADING:
            self._parts.append(f"\n{_HEADING[t]} ")
            return
        if t == "li":
            self._parts.append("\n- ")
            return
        if t == "blockquote":
            self._parts.append("\n> ")
            return
        if t in ("pre",):
            self._in_pre += 1
            self._parts.append("\n```\n")
            return
        if t == "code" and not self._in_pre:
            self._in_code += 1
            self._parts.append("`")
            return
        if t == "a":
            href = ""
            for k, v in attrs:
                if k.lower() == "href" and v:
                    href = v.strip()
                    break
            self._in_a += 1
            self._a_href = href
            self._a_text = []
            return
        if t == "p":
            self._parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in _DROP:
            if self._skip:
                self._skip -= 1
            return
        if self._skip:
            return
        if t == "title":
            if self._in_title:
                self._in_title -= 1
            if not self.title:
                self.title = "".join(self._title_bits).strip()
            return
        if t in _HEADING or t in ("p", "li", "blockquote", "tr"):
            self._parts.append("\n")
            return
        if t == "pre" and self._in_pre:
            self._in_pre -= 1
            self._parts.append("\n```\n")
            return
        if t == "code" and self._in_code:
            self._in_code -= 1
            self._parts.append("`")
            return
        if t == "a" and self._in_a:
            self._in_a -= 1
            label = "".join(self._a_text).strip()
            href = self._a_href
            if label and href and href.startswith(("http://", "https://", "/")):
                self._parts.append(f"[{label}]({href})")
            elif label:
                self._parts.append(label)
            self._a_href = ""
            self._a_text = []

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self._title_bits.append(data)
            return
        if self._in_a:
            self._a_text.append(data)
            return
        if self._in_pre:
            self._parts.append(data)
            return
        text = _WS_RE.sub(" ", data)
        if text.strip():
            self._parts.append(text)

    def text(self) -> str:
        raw = "".join(self._parts)
        raw = raw.replace("\r\n", "\n").replace("\r", "\n")
        raw = _NL_RE.sub("\n\n", raw)
        return raw.strip()


def html_to_markdown(html: str, *, max_chars: int = 50_000) -> dict[str, str | int | bool]:
    """Extract readable markdown from *html*.

    Returns ``title``, ``markdown``, ``chars``, and ``js_shell`` (thin SPA / bot wall).
    """
    parser = _ExtractParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        stripped = _TAG_RE_FALLBACK.sub(" ", html or "")
        md = html_lib.unescape(_WS_RE.sub(" ", stripped)).strip()
        return {
            "title": "",
            "markdown": md[:max_chars],
            "chars": len(md),
            "js_shell": _looks_js_shell(md, html or ""),
        }
    md = parser.text()
    title = parser.title
    if title and not md.lower().startswith("# "):
        md = f"# {title}\n\n{md}".strip()
    js_shell = _looks_js_shell(md, html or "")
    if len(md) > max_chars:
        md = md[:max_chars] + f"\n\n…[truncated at {max_chars} chars]"
    return {
        "title": title,
        "markdown": md,
        "chars": len(md),
        "js_shell": js_shell,
    }


_TAG_RE_FALLBACK = re.compile(r"(?is)<[^>]+>")


def _looks_js_shell(extracted: str, html: str) -> bool:
    body = (extracted or "").strip().lower()
    if any(h in body for h in _JS_HINTS):
        return True
    if any(h in (html or "").lower()[:4000] for h in _JS_HINTS):
        if len(body) < 400:
            return True
    if len(html or "") >= 4000 and len(body) < 240:
        return True
    return False
