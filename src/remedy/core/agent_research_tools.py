"""Literature discovery and a citation library that can be checked against reality.

Seven tools: ``lit_search`` / ``lit_fetch`` find and pull papers from the five
public scholarly APIs (arXiv, Crossref, OpenAlex, PubMed E-utilities, Semantic
Scholar); ``cite_add`` / ``cite_import`` / ``cite_list`` / ``cite_export`` keep a
BibTeX + CSL-JSON library beside the project; ``cite_check`` is the point of the
module — it takes a manuscript and tells you which of its citations are real.

Rules this module holds itself to:

* **Nothing is invented.** A field the source did not give is ``""`` / ``0`` /
  ``[]``, never guessed and never back-filled from a different record unless an
  identifier matched exactly. Every record carries ``source`` and
  ``retrieved_utc``.
* **"Verified" is earned, once, here.** ``cite_check`` is the only place in the
  codebase allowed to call a citation verified, and only for an identifier it
  actually resolved in *that* call. A verification carried over from an earlier
  run is re-labelled ``OK_CACHED`` with its timestamp.
* **A source that fails is reported, not dropped.** Partial answers name the
  source that was unavailable and why, in ``sources_failed``.
* **One network path.** Everything goes through :func:`_fetch_bytes` /
  :func:`_fetch_json`, which wrap ``agent_web_tools._pinned_fetch`` — DNS pinned
  to a public IP, every redirect hop re-validated. No fresh urllib/httpx path
  exists here on purpose: that would be an SSRF bypass. Networked tools are
  gated on the existing ``web_tools_enabled`` opt-in; there is no new switch.
* **No heavy dependencies.** This module ships inside the PyInstaller sidecar,
  which excludes pandas/numpy/scipy/sklearn/matplotlib/torch/transformers. Only
  the standard library is imported, at module scope or anywhere else. PDF text
  extraction tries a lazily-imported pure-python reader and otherwise degrades
  to a clearly-flagged lossy stdlib parse — it never installs anything.

Test seam: monkeypatch :func:`_fetch_json` and :func:`_fetch_bytes`. Nothing else
in this module opens a socket.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode

from remedy.core.agent_web_tools import _pinned_fetch, web_tools_enabled
from remedy.core.errors import format_tool_error

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

#: Hard cap on bytes pulled per HTTP call (the sidecar has no streaming parser).
_MAX_FETCH_BYTES = 4_000_000

#: Sources this module knows how to normalise.
SOURCES = ("arxiv", "crossref", "openalex", "pubmed", "semanticscholar")

#: Semantic Scholar's unauthenticated tier 429s often; back off once, then move on.
_S2_BACKOFF_S = 1.0

#: Most URLs in a manuscript are not citations — cap the link-rot pass.
_URL_CHECK_CAP = 10

#: Files that make up a citation library.
BIB_FILENAME = "refs.bib"
CSL_FILENAME = "refs.csl.json"
INDEX_FILENAME = "library.json"

#: Directory name used when a project is bound.
RESEARCH_DIRNAME = ".remedy-research"

_ATOM_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>,;)\]}]+")
_ARXIV_ID_RE = re.compile(r"\d{4}\.\d{4,5}(?:v\d+)?")
_ARXIV_TAGGED_RE = re.compile(r"arxiv:\s*([A-Za-z0-9./-]+)", re.I)
_PMID_TAGGED_RE = re.compile(r"pmid:?\s*(\d{4,9})", re.I)
_PMC_RE = re.compile(r"(PMC\d{4,9})", re.I)
_URL_RE = re.compile(r"https?://[^\s\"'<>)\]}]+")

# \cite \citep \citet \citeauthor \autocite \parencite \nocite \textcite …
_CITE_CMD_RE = re.compile(
    r"\\(?:no|auto|paren|foot|full|smart|text|super|possess)?cite[a-zA-Z*]*"
    r"\s*(?:\[[^\]]*\]\s*)*\{([^{}]*)\}"
)
# pandoc / Quarto: [@key; @other], -@key, @key
_PANDOC_CITE_RE = re.compile(
    r"(?<![A-Za-z0-9_@./:-])-?@([A-Za-z][A-Za-z0-9_:.#$%&+?<>~/-]*[A-Za-z0-9_])"
)

_MARKDOWN_SUFFIXES = {".md", ".markdown", ".qmd", ".rmd"}

_CLINICAL_HINT_RE = re.compile(
    r"\b(patients?|clinical|randomi[sz]ed|trial|cohort|in vivo|in vitro|mice|murine|"
    r"placebo|dose|therap\w+|diagnos\w+|prognos\w+|carcinom\w+|mesh\b|epidemiolog\w+|"
    r"incidence|prevalence|comorbid\w+|vaccine|antibod\w+)\b",
    re.I,
)

#: CSL/BibTeX type mapping (only what the five sources actually emit).
_BIB_TYPE = {
    "journal-article": "article",
    "article": "article",
    "article-journal": "article",
    "proceedings-article": "inproceedings",
    "paper-conference": "inproceedings",
    "book": "book",
    "book-chapter": "incollection",
    "chapter": "incollection",
    "monograph": "book",
    "dissertation": "phdthesis",
    "thesis": "phdthesis",
    "report": "techreport",
    "posted-content": "misc",
    "preprint": "misc",
    "dataset": "misc",
    "peer-review": "misc",
}
_CSL_TYPE = {
    "article": "article-journal",
    "inproceedings": "paper-conference",
    "book": "book",
    "incollection": "chapter",
    "phdthesis": "thesis",
    "techreport": "report",
    "misc": "article",
}


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        n = int(value if value is not None else default)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def _clamp_float(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        f = float(value if value is not None else default)
    except (TypeError, ValueError):
        f = default
    return max(lo, min(hi, f))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        for item in value:
            s = _text(item)
            if s:
                return s
        return ""
    return str(value).strip()


def _strip_markup(raw: str) -> str:
    """Drop XML/HTML tags and squeeze whitespace (JATS abstracts, Atom summaries)."""
    if not raw:
        return ""
    import html as html_lib

    out = _TAG_RE.sub(" ", raw)
    out = html_lib.unescape(out)
    return _WS_RE.sub(" ", out).strip()


def _fold(raw: str) -> str:
    """ASCII-fold and lowercase — for keys and title comparison."""
    norm = unicodedata.normalize("NFKD", raw or "")
    return "".join(c for c in norm if not unicodedata.combining(c)).lower()


def _title_tokens(title: str) -> set[str]:
    folded = _fold(title)
    return {t for t in re.split(r"[^a-z0-9]+", folded) if t}


def jaccard(a: str, b: str) -> float:
    """Case/punctuation-folded token Jaccard of two titles (0.0-1.0)."""
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def normalise_doi(raw: str) -> str:
    """``https://doi.org/10.X/Y`` / ``doi:10.X/Y`` -> ``10.x/y`` (lowercased)."""
    s = (raw or "").strip()
    if not s:
        return ""
    s = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    s = s.strip().rstrip(".,;)]}")
    return s.lower() if s.lower().startswith("10.") else ""


def normalise_arxiv(raw: str) -> str:
    """``arXiv:2401.00001v2`` / a bare id -> ``2401.00001`` (version dropped)."""
    s = (raw or "").strip()
    if not s:
        return ""
    s = re.sub(r"^(?:https?://arxiv\.org/(?:abs|pdf)/)", "", s, flags=re.I)
    s = re.sub(r"^arxiv:\s*", "", s, flags=re.I)
    s = s.strip().removesuffix(".pdf")
    s = re.sub(r"v\d+$", "", s)
    return s


def parse_identifier(raw: str) -> tuple[str, str]:
    """Classify a user-supplied identifier.

    Returns ``(kind, value)`` where kind is one of doi / arxiv / pmid / pmc /
    openalex / s2 / url / query. Never guesses beyond the documented prefixes.
    """
    s = (raw or "").strip()
    if not s:
        return ("query", "")
    low = s.lower()
    if low.startswith(("http://", "https://")):
        doi = normalise_doi(s)
        if doi:
            return ("doi", doi)
        if "arxiv.org" in low:
            arx = normalise_arxiv(s)
            if arx:
                return ("arxiv", arx)
        if "openalex.org/w" in low:
            return ("openalex", s.rsplit("/", 1)[-1])
        return ("url", s)
    if low.startswith("openalex:"):
        return ("openalex", s.split(":", 1)[1].strip())
    if low.startswith("s2:") or low.startswith("semanticscholar:"):
        return ("s2", s.split(":", 1)[1].strip())
    if low.startswith("pmc") and _PMC_RE.fullmatch(s):
        return ("pmc", s.upper())
    if low.startswith("pmc:"):
        return ("pmc", s.split(":", 1)[1].strip().upper())
    m = _PMID_TAGGED_RE.fullmatch(s)
    if m:
        return ("pmid", m.group(1))
    doi = normalise_doi(s)
    if doi:
        return ("doi", doi)
    m = _ARXIV_TAGGED_RE.fullmatch(s)
    if m:
        return ("arxiv", normalise_arxiv(m.group(1)))
    if _ARXIV_ID_RE.fullmatch(s):
        return ("arxiv", normalise_arxiv(s))
    if re.fullmatch(r"W\d{5,12}", s):
        return ("openalex", s)
    if re.fullmatch(r"\d{5,9}", s):
        return ("pmid", s)
    return ("query", s)


def _polite_mailto() -> str:
    """Contact address for Crossref/OpenAlex/NCBI politeness params (opt-in).

    ``REMEDY_RESEARCH_MAILTO`` wins, else config ``research_mailto``. Never
    hard-coded, never sent anywhere but those three query parameters. Absent =
    the anonymous pool, which still works.
    """
    raw = (os.environ.get("REMEDY_RESEARCH_MAILTO") or "").strip()
    if not raw:
        try:
            from remedy.interfaces.config import load_config

            raw = str((load_config() or {}).get("research_mailto") or "").strip()
        except Exception:
            raw = ""
    if "@" not in raw or _WS_RE.search(raw) or len(raw) > 200:
        return ""
    return raw


def _web_disabled(tool: str) -> str:
    return format_tool_error(
        "Web tools are disabled, so nothing can be looked up online. Enable with "
        f"update_settings(web_tools_enabled=true), then retry {tool}.",
        code="WEB_DISABLED",
        tool_name=tool,
        suggestion="Call update_settings(web_tools_enabled=true) for the user, then retry.",
    )


def _source_failure(source: str, exc: BaseException) -> dict[str, str]:
    """One row for ``sources_failed`` — a source that broke is named, not hidden."""
    if isinstance(exc, HTTPError):
        return {
            "source": source,
            "code": f"HTTP_{exc.code}",
            "error": f"HTTP {exc.code}: {exc.reason}",
        }
    if isinstance(exc, URLError):
        return {
            "source": source,
            "code": "NETWORK_ERROR",
            "error": str(getattr(exc, "reason", exc)),
        }
    if isinstance(exc, json.JSONDecodeError):
        return {"source": source, "code": "BAD_RESPONSE", "error": f"invalid JSON: {exc}"}
    if isinstance(exc, ET.ParseError):
        return {"source": source, "code": "BAD_RESPONSE", "error": f"invalid XML: {exc}"}
    if isinstance(exc, ValueError):
        msg = str(exc)
        if msg == "ABORTED":
            return {"source": source, "code": "ABORTED", "error": "aborted by user"}
        if "USERINFO" in msg:
            return {"source": source, "code": "URL_USERINFO_BLOCKED", "error": msg}
        if "SSRF" in msg:
            return {"source": source, "code": "SSRF_BLOCKED", "error": msg}
        return {"source": source, "code": "BAD_RESPONSE", "error": msg}
    return {"source": source, "code": "FETCH_ERROR", "error": str(exc)}


# --------------------------------------------------------------------------
# The only two network calls in this module (test seam)
# --------------------------------------------------------------------------


def _fetch_bytes(url: str, *, timeout: float = 30.0) -> tuple[str, bytes, str]:
    """SSRF-guarded GET -> ``(final_url, raw_bytes, charset)``.

    Wraps ``agent_web_tools._pinned_fetch``. Tests monkeypatch this symbol; it
    is the only place raw HTTP happens here.
    """
    return _pinned_fetch(url, max_chars=_MAX_FETCH_BYTES, timeout=timeout)


def _fetch_json(url: str, *, timeout: float = 30.0) -> Any:
    """SSRF-guarded GET decoded as JSON. Raises ``json.JSONDecodeError`` on junk."""
    _final, raw, charset = _fetch_bytes(url, timeout=timeout)
    return json.loads(raw.decode(charset or "utf-8", errors="replace"))


def _fetch_text(url: str, *, timeout: float = 30.0) -> str:
    _final, raw, charset = _fetch_bytes(url, timeout=timeout)
    return raw.decode(charset or "utf-8", errors="replace")


# --------------------------------------------------------------------------
# The normalised record
# --------------------------------------------------------------------------

RECORD_FIELDS = (
    "source",
    "sources",
    "retrieved_utc",
    "id",
    "doi",
    "arxiv_id",
    "pmid",
    "pmcid",
    "openalex_id",
    "s2_id",
    "title",
    "authors",
    "year",
    "month",
    "venue",
    "volume",
    "issue",
    "pages",
    "publisher",
    "abstract",
    "url",
    "pdf_url",
    "oa_status",
    "license",
    "cited_by_count",
    "type",
    "keywords",
    "raw_ref",
)


def blank_record(source: str = "") -> dict[str, Any]:
    """An empty normalised record. Missing stays missing — nothing is guessed."""
    return {
        "source": source,
        "sources": [source] if source else [],
        "retrieved_utc": _iso_now(),
        "id": "",
        "doi": "",
        "arxiv_id": "",
        "pmid": "",
        "pmcid": "",
        "openalex_id": "",
        "s2_id": "",
        "title": "",
        "authors": [],
        "year": 0,
        "month": 0,
        "venue": "",
        "volume": "",
        "issue": "",
        "pages": "",
        "publisher": "",
        "abstract": "",
        "url": "",
        "pdf_url": "",
        "oa_status": "",
        "license": "",
        "cited_by_count": 0,
        "type": "",
        "keywords": [],
        "raw_ref": "",
    }


def _author(family: str = "", given: str = "", orcid: str = "") -> dict[str, str]:
    return {"family": (family or "").strip(), "given": (given or "").strip(), "orcid": orcid or ""}


def _split_name(full: str) -> dict[str, str]:
    """"Smith J" / "John Smith" / "Smith, John" -> {family, given}. Best effort, flagged."""
    s = (full or "").strip()
    if not s:
        return _author()
    if "," in s:
        fam, _, giv = s.partition(",")
        return _author(fam.strip(), giv.strip())
    parts = s.split()
    if len(parts) == 1:
        return _author(parts[0])
    # PubMed emits "Smith JA"; everything else is Given … Family.
    if len(parts[-1]) <= 3 and parts[-1].isupper():
        return _author(" ".join(parts[:-1]), parts[-1])
    return _author(parts[-1], " ".join(parts[:-1]))


def first_author_family(record: dict[str, Any]) -> str:
    authors = record.get("authors") or []
    if authors and isinstance(authors[0], dict):
        return str(authors[0].get("family") or "")
    return ""


# --------------------------------------------------------------------------
# Source adapters — each returns (records, notes) and raises on transport error
# --------------------------------------------------------------------------


def _parse_arxiv_atom(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    out: list[dict[str, Any]] = []
    for entry in root.findall("a:entry", _ATOM_NS):
        rec = blank_record("arxiv")
        raw_id = _text(entry.findtext("a:id", default="", namespaces=_ATOM_NS))
        rec["arxiv_id"] = normalise_arxiv(raw_id)
        rec["id"] = f"arxiv:{rec['arxiv_id']}" if rec["arxiv_id"] else raw_id
        rec["title"] = _WS_RE.sub(" ", _text(entry.findtext("a:title", "", _ATOM_NS)))
        rec["abstract"] = _WS_RE.sub(" ", _text(entry.findtext("a:summary", "", _ATOM_NS)))
        published = _text(entry.findtext("a:published", "", _ATOM_NS))
        if len(published) >= 7:
            with suppress(ValueError):
                rec["year"] = int(published[:4])
                rec["month"] = int(published[5:7])
        for person in entry.findall("a:author", _ATOM_NS):
            name = _text(person.findtext("a:name", "", _ATOM_NS))
            if name:
                rec["authors"].append(_split_name(name))
        doi = _text(entry.findtext("arxiv:doi", "", _ATOM_NS))
        rec["doi"] = normalise_doi(doi)
        rec["venue"] = _text(entry.findtext("arxiv:journal_ref", "", _ATOM_NS)) or "arXiv"
        rec["url"] = raw_id
        for link in entry.findall("a:link", _ATOM_NS):
            if (link.get("title") or "").lower() == "pdf":
                rec["pdf_url"] = link.get("href") or ""
        for cat in entry.findall("a:category", _ATOM_NS):
            term = cat.get("term") or ""
            if term:
                rec["keywords"].append(term)
        rec["type"] = "preprint"
        rec["oa_status"] = "green"
        rec["publisher"] = "arXiv"
        out.append(rec)
    return out


def search_arxiv(
    query: str,
    *,
    max_results: int,
    sort: str = "relevance",
    timeout: float = 30.0,
) -> tuple[list[dict[str, Any]], list[str]]:
    notes: list[str] = []
    kind, value = parse_identifier(query)
    if kind == "arxiv":
        params: dict[str, Any] = {"id_list": value, "max_results": max_results}
    else:
        params = {"search_query": f"all:{query}", "start": 0, "max_results": max_results}
        if sort == "date":
            params["sortBy"] = "submittedDate"
            params["sortOrder"] = "descending"
        elif sort == "relevance":
            params["sortBy"] = "relevance"
        elif sort == "citations":
            notes.append("arxiv: the API has no citation sort; results are relevance-ordered.")
    url = "https://export.arxiv.org/api/query?" + urlencode(params)
    return _parse_arxiv_atom(_fetch_text(url, timeout=timeout)), notes


def _crossref_record(item: dict[str, Any]) -> dict[str, Any]:
    rec = blank_record("crossref")
    rec["doi"] = normalise_doi(_text(item.get("DOI")))
    rec["id"] = f"doi:{rec['doi']}" if rec["doi"] else ""
    rec["title"] = _strip_markup(_text(item.get("title")))
    for a in item.get("author") or []:
        if not isinstance(a, dict):
            continue
        orcid = _text(a.get("ORCID"))
        if a.get("family") or a.get("given"):
            rec["authors"].append(_author(_text(a.get("family")), _text(a.get("given")), orcid))
        elif a.get("name"):
            rec["authors"].append(_split_name(_text(a.get("name"))))
    issued = ((item.get("issued") or {}).get("date-parts") or [[]])[0] or []
    if issued:
        with suppress(TypeError, ValueError):
            rec["year"] = int(issued[0])
        if len(issued) > 1:
            with suppress(TypeError, ValueError):
                rec["month"] = int(issued[1])
    rec["venue"] = _text(item.get("container-title"))
    rec["volume"] = _text(item.get("volume"))
    rec["issue"] = _text(item.get("issue"))
    rec["pages"] = _text(item.get("page"))
    rec["publisher"] = _text(item.get("publisher"))
    rec["abstract"] = _strip_markup(_text(item.get("abstract")))
    rec["url"] = _text(item.get("URL")) or (f"https://doi.org/{rec['doi']}" if rec["doi"] else "")
    rec["type"] = _text(item.get("type"))
    rec["cited_by_count"] = int(item.get("is-referenced-by-count") or 0)
    for lic in item.get("license") or []:
        if isinstance(lic, dict) and lic.get("URL"):
            rec["license"] = _text(lic.get("URL"))
            break
    for link in item.get("link") or []:
        if isinstance(link, dict) and "pdf" in _text(link.get("content-type")).lower():
            rec["pdf_url"] = _text(link.get("URL"))
            break
    subjects = [s for s in (item.get("subject") or []) if isinstance(s, str)]
    rec["keywords"] = subjects
    return rec


def search_crossref(
    query: str,
    *,
    max_results: int,
    year_from: int = 0,
    year_to: int = 0,
    sort: str = "relevance",
    timeout: float = 30.0,
) -> tuple[list[dict[str, Any]], list[str]]:
    notes: list[str] = []
    mailto = _polite_mailto()
    kind, value = parse_identifier(query)
    if kind == "doi":
        url = "https://api.crossref.org/works/" + quote(value, safe="")
        if mailto:
            url += "?" + urlencode({"mailto": mailto})
        payload = _fetch_json(url, timeout=timeout)
        message = payload.get("message") if isinstance(payload, dict) else None
        return ([_crossref_record(message)] if isinstance(message, dict) else []), notes
    params: dict[str, Any] = {"query.bibliographic": query, "rows": max_results}
    filters = []
    if year_from:
        filters.append(f"from-pub-date:{year_from}-01-01")
    if year_to:
        filters.append(f"until-pub-date:{year_to}-12-31")
    if filters:
        params["filter"] = ",".join(filters)
    if sort == "date":
        params["sort"] = "published"
        params["order"] = "desc"
    elif sort == "citations":
        params["sort"] = "is-referenced-by-count"
        params["order"] = "desc"
    if mailto:
        params["mailto"] = mailto
    else:
        notes.append(
            "crossref: no polite-pool address. Set REMEDY_RESEARCH_MAILTO or config "
            "research_mailto to use the faster polite pool."
        )
    payload = _fetch_json("https://api.crossref.org/works?" + urlencode(params), timeout=timeout)
    items = ((payload or {}).get("message") or {}).get("items") or []
    return [_crossref_record(i) for i in items if isinstance(i, dict)], notes


def _openalex_abstract(inverted: Any) -> str:
    if not isinstance(inverted, dict) or not inverted:
        return ""
    positions: dict[int, str] = {}
    for word, spots in inverted.items():
        for p in spots or []:
            with suppress(TypeError, ValueError):
                positions[int(p)] = str(word)
    if not positions:
        return ""
    return " ".join(positions[i] for i in sorted(positions))


def _openalex_record(item: dict[str, Any]) -> dict[str, Any]:
    rec = blank_record("openalex")
    oa_id = _text(item.get("id"))
    rec["openalex_id"] = oa_id.rsplit("/", 1)[-1] if oa_id else ""
    rec["id"] = f"openalex:{rec['openalex_id']}" if rec["openalex_id"] else ""
    rec["doi"] = normalise_doi(_text(item.get("doi")))
    rec["title"] = _strip_markup(_text(item.get("display_name") or item.get("title")))
    for a in item.get("authorships") or []:
        if not isinstance(a, dict):
            continue
        person = a.get("author") or {}
        name = _text(person.get("display_name"))
        if name:
            parsed = _split_name(name)
            parsed["orcid"] = _text(person.get("orcid"))
            rec["authors"].append(parsed)
    with suppress(TypeError, ValueError):
        rec["year"] = int(item.get("publication_year") or 0)
    pub_date = _text(item.get("publication_date"))
    if len(pub_date) >= 7:
        with suppress(ValueError):
            rec["month"] = int(pub_date[5:7])
    location = item.get("primary_location") or item.get("host_venue") or {}
    source = location.get("source") if isinstance(location.get("source"), dict) else location
    rec["venue"] = _text((source or {}).get("display_name"))
    rec["publisher"] = _text((source or {}).get("host_organization_name"))
    biblio = item.get("biblio") or {}
    rec["volume"] = _text(biblio.get("volume"))
    rec["issue"] = _text(biblio.get("issue"))
    first, last = _text(biblio.get("first_page")), _text(biblio.get("last_page"))
    rec["pages"] = f"{first}-{last}" if first and last else first
    rec["abstract"] = _openalex_abstract(item.get("abstract_inverted_index"))
    oa = item.get("open_access") or {}
    rec["oa_status"] = _text(oa.get("oa_status"))
    rec["pdf_url"] = _text(oa.get("oa_url")) or _text(location.get("pdf_url"))
    rec["license"] = _text(location.get("license"))
    with suppress(TypeError, ValueError):
        rec["cited_by_count"] = int(item.get("cited_by_count") or 0)
    rec["type"] = _text(item.get("type"))
    ids = item.get("ids") or {}
    pmid = _text(ids.get("pmid"))
    rec["pmid"] = pmid.rsplit("/", 1)[-1] if pmid else ""
    pmcid = _text(ids.get("pmcid"))
    rec["pmcid"] = pmcid.rsplit("/", 1)[-1].upper() if pmcid else ""
    rec["url"] = (
        _text(location.get("landing_page_url"))
        or (f"https://doi.org/{rec['doi']}" if rec["doi"] else "")
        or oa_id
    )
    for concept in item.get("concepts") or []:
        if isinstance(concept, dict) and concept.get("display_name"):
            rec["keywords"].append(_text(concept.get("display_name")))
    return rec


def search_openalex(
    query: str,
    *,
    max_results: int,
    year_from: int = 0,
    year_to: int = 0,
    open_access_only: bool = False,
    sort: str = "relevance",
    timeout: float = 30.0,
) -> tuple[list[dict[str, Any]], list[str]]:
    notes: list[str] = []
    mailto = _polite_mailto()
    kind, value = parse_identifier(query)
    if kind in ("doi", "openalex", "pmid"):
        ident = {
            "doi": f"doi:{value}",
            "openalex": value,
            "pmid": f"pmid:{value}",
        }[kind]
        url = "https://api.openalex.org/works/" + quote(ident, safe=":./")
        if mailto:
            url += "?" + urlencode({"mailto": mailto})
        payload = _fetch_json(url, timeout=timeout)
        return ([_openalex_record(payload)] if isinstance(payload, dict) else []), notes
    params: dict[str, Any] = {"search": query, "per-page": max_results}
    filters = []
    if year_from:
        filters.append(f"from_publication_date:{year_from}-01-01")
    if year_to:
        filters.append(f"to_publication_date:{year_to}-12-31")
    if open_access_only:
        filters.append("is_oa:true")
    if filters:
        params["filter"] = ",".join(filters)
    if sort == "date":
        params["sort"] = "publication_date:desc"
    elif sort == "citations":
        params["sort"] = "cited_by_count:desc"
    if mailto:
        params["mailto"] = mailto
    payload = _fetch_json("https://api.openalex.org/works?" + urlencode(params), timeout=timeout)
    results = (payload or {}).get("results") or []
    return [_openalex_record(r) for r in results if isinstance(r, dict)], notes


def _eutils_common() -> dict[str, str]:
    params = {"tool": "remedy", "retmode": "json"}
    mailto = _polite_mailto()
    if mailto:
        params["email"] = mailto
    return params


def _pubmed_record(uid: str, summary: dict[str, Any]) -> dict[str, Any]:
    rec = blank_record("pubmed")
    rec["pmid"] = str(uid)
    rec["id"] = f"pmid:{uid}"
    rec["title"] = _strip_markup(_text(summary.get("title")))
    for a in summary.get("authors") or []:
        if isinstance(a, dict) and a.get("name"):
            rec["authors"].append(_split_name(_text(a.get("name"))))
    pubdate = _text(summary.get("pubdate") or summary.get("epubdate"))
    m = re.match(r"(\d{4})(?:\s+(\w{3}))?", pubdate)
    if m:
        with suppress(ValueError):
            rec["year"] = int(m.group(1))
        if m.group(2):
            months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
            with suppress(ValueError):
                rec["month"] = months.index(m.group(2).lower()[:3]) + 1
    rec["venue"] = _text(summary.get("fulljournalname") or summary.get("source"))
    rec["volume"] = _text(summary.get("volume"))
    rec["issue"] = _text(summary.get("issue"))
    rec["pages"] = _text(summary.get("pages"))
    rec["type"] = _text(summary.get("pubtype")) or "journal-article"
    for aid in summary.get("articleids") or []:
        if not isinstance(aid, dict):
            continue
        kind = _text(aid.get("idtype")).lower()
        value = _text(aid.get("value"))
        if kind == "doi":
            rec["doi"] = normalise_doi(value)
        elif kind == "pmc":
            rec["pmcid"] = value.upper()
    rec["url"] = f"https://pubmed.ncbi.nlm.nih.gov/{uid}/"
    if rec["pmcid"]:
        rec["oa_status"] = "green"
    return rec


def search_pubmed(
    query: str,
    *,
    max_results: int,
    year_from: int = 0,
    year_to: int = 0,
    sort: str = "relevance",
    timeout: float = 30.0,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Two bounded requests: esearch for UIDs, esummary for the metadata."""
    notes: list[str] = []
    kind, value = parse_identifier(query)
    if kind == "pmid":
        uids = [value]
    else:
        params = dict(_eutils_common())
        term = query
        if year_from or year_to:
            lo = year_from or 1800
            hi = year_to or datetime.now(UTC).year
            term = f"({query}) AND {lo}:{hi}[dp]"
        params.update({"db": "pubmed", "term": term, "retmax": str(max_results)})
        if sort == "date":
            params["sort"] = "pub_date"
        elif sort == "citations":
            notes.append("pubmed: E-utilities has no citation sort; using relevance.")
        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urlencode(params)
        payload = _fetch_json(url, timeout=timeout)
        uids = [str(u) for u in (((payload or {}).get("esearchresult") or {}).get("idlist") or [])]
    if not uids:
        return [], notes
    params = dict(_eutils_common())
    params.update({"db": "pubmed", "id": ",".join(uids[:max_results])})
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + urlencode(params)
    payload = _fetch_json(url, timeout=timeout)
    result = (payload or {}).get("result") or {}
    records = []
    for uid in result.get("uids") or uids:
        item = result.get(str(uid))
        if isinstance(item, dict):
            records.append(_pubmed_record(str(uid), item))
    notes.append(
        "pubmed: esummary carries no abstract — call lit_fetch(id='pmid:…', want='abstract')."
    )
    return records, notes


def _s2_record(item: dict[str, Any]) -> dict[str, Any]:
    rec = blank_record("semanticscholar")
    rec["s2_id"] = _text(item.get("paperId"))
    rec["id"] = f"s2:{rec['s2_id']}" if rec["s2_id"] else ""
    ext = item.get("externalIds") or {}
    rec["doi"] = normalise_doi(_text(ext.get("DOI")))
    rec["arxiv_id"] = normalise_arxiv(_text(ext.get("ArXiv")))
    rec["pmid"] = _text(ext.get("PubMed"))
    pmcid = _text(ext.get("PubMedCentral"))
    rec["pmcid"] = ("PMC" + pmcid) if pmcid and not pmcid.upper().startswith("PMC") else pmcid
    rec["title"] = _strip_markup(_text(item.get("title")))
    for a in item.get("authors") or []:
        if isinstance(a, dict) and a.get("name"):
            rec["authors"].append(_split_name(_text(a.get("name"))))
    with suppress(TypeError, ValueError):
        rec["year"] = int(item.get("year") or 0)
    rec["venue"] = _text(item.get("venue"))
    rec["abstract"] = _strip_markup(_text(item.get("abstract")))
    rec["url"] = _text(item.get("url"))
    oa = item.get("openAccessPdf") or {}
    rec["pdf_url"] = _text(oa.get("url"))
    rec["license"] = _text(oa.get("license"))
    if rec["pdf_url"]:
        rec["oa_status"] = "oa"
    with suppress(TypeError, ValueError):
        rec["cited_by_count"] = int(item.get("citationCount") or 0)
    types = item.get("publicationTypes") or []
    rec["type"] = _text(types) if types else ""
    rec["keywords"] = [k for k in (item.get("fieldsOfStudy") or []) if isinstance(k, str)]
    return rec


_S2_FIELDS = (
    "paperId,title,abstract,year,venue,authors,externalIds,openAccessPdf,"
    "citationCount,publicationTypes,fieldsOfStudy,url"
)


def search_semanticscholar(
    query: str,
    *,
    max_results: int,
    year_from: int = 0,
    year_to: int = 0,
    timeout: float = 30.0,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Unauthenticated tier only.

    ``_pinned_fetch`` cannot send the ``x-api-key`` header, so only the shared
    anonymous pool is reachable (~1 req/s, frequent 429). On 429 we back off
    exactly once and then give up — never a retry loop — and say so in notes.
    """
    notes: list[str] = []
    kind, value = parse_identifier(query)
    if kind in ("s2", "doi", "arxiv", "pmid"):
        ident = {
            "s2": value,
            "doi": f"DOI:{value}",
            "arxiv": f"arXiv:{value}",
            "pmid": f"PMID:{value}",
        }[kind]
        url = (
            "https://api.semanticscholar.org/graph/v1/paper/"
            + quote(ident, safe=":")
            + "?"
            + urlencode({"fields": _S2_FIELDS})
        )
    else:
        params: dict[str, Any] = {"query": query, "limit": max_results, "fields": _S2_FIELDS}
        if year_from or year_to:
            params["year"] = f"{year_from or ''}-{year_to or ''}"
        url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urlencode(params)
    notes.append(
        "semanticscholar: anonymous tier (no API key header is possible through the "
        "SSRF-guarded fetcher); 429s are expected under load."
    )
    try:
        payload = _fetch_json(url, timeout=timeout)
    except HTTPError as exc:
        if exc.code != 429:
            raise
        notes.append("semanticscholar: HTTP 429 rate limit — backed off once, then retried.")
        time.sleep(_S2_BACKOFF_S)
        payload = _fetch_json(url, timeout=timeout)
    if isinstance(payload, dict) and "data" in payload:
        data = payload.get("data") or []
        return [_s2_record(r) for r in data if isinstance(r, dict)], notes
    if isinstance(payload, dict):
        return [_s2_record(payload)], notes
    return [], notes


# --------------------------------------------------------------------------
# Source selection, dedupe, merge
# --------------------------------------------------------------------------


def auto_sources(query: str) -> list[str]:
    """Source order for ``source="auto"`` — by the shape of the query, not by luck."""
    kind, _value = parse_identifier(query)
    if kind == "doi":
        return ["crossref", "openalex"]
    if kind == "arxiv":
        return ["arxiv", "semanticscholar"]
    if kind in ("pmid", "pmc"):
        return ["pubmed", "crossref"]
    if kind == "openalex":
        return ["openalex"]
    if kind == "s2":
        return ["semanticscholar"]
    if _CLINICAL_HINT_RE.search(query or ""):
        return ["pubmed", "crossref"]
    return ["openalex", "crossref"]


def dedupe_key(record: dict[str, Any]) -> tuple[str, str]:
    """Identity for cross-source merging: DOI > PMID > arXiv > folded title+year."""
    if record.get("doi"):
        return ("doi", str(record["doi"]).lower())
    if record.get("pmid"):
        return ("pmid", str(record["pmid"]))
    if record.get("arxiv_id"):
        return ("arxiv", str(record["arxiv_id"]))
    title = " ".join(sorted(_title_tokens(str(record.get("title") or ""))))
    return ("title", f"{title}|{record.get('year') or 0}")


def merge_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Merge duplicates across sources.

    Fields are back-filled from a second source only when the match was on an
    exact identifier (DOI/PMID/arXiv). A title+year match merges the source list
    but copies **no** fields, and says so in the returned notes.
    """
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    notes: list[str] = []
    title_merges = 0
    for rec in records:
        key = dedupe_key(rec)
        if key not in merged:
            merged[key] = dict(rec)
            merged[key]["sources"] = list(rec.get("sources") or [rec.get("source") or ""])
            order.append(key)
            continue
        target = merged[key]
        for src in rec.get("sources") or [rec.get("source") or ""]:
            if src and src not in target["sources"]:
                target["sources"].append(src)
        if key[0] == "title":
            title_merges += 1
            continue
        for field, value in rec.items():
            if field in ("source", "sources", "retrieved_utc"):
                continue
            current = target.get(field)
            if current in ("", 0, [], None) and value not in ("", 0, [], None):
                target[field] = value
    if title_merges:
        notes.append(
            f"{title_merges} record(s) merged on folded title + year (no shared identifier); "
            "fields were NOT back-filled across those — verify before citing."
        )
    return [merged[k] for k in order], notes


def _sort_records(records: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    if sort == "date":
        return sorted(records, key=lambda r: (int(r.get("year") or 0), int(r.get("month") or 0)), reverse=True)
    if sort == "citations":
        return sorted(records, key=lambda r: int(r.get("cited_by_count") or 0), reverse=True)
    return records


def _looks_open_access(record: dict[str, Any]) -> bool:
    if record.get("pdf_url") or record.get("pmcid") or record.get("arxiv_id"):
        return True
    return str(record.get("oa_status") or "").lower() in ("gold", "green", "hybrid", "bronze", "oa")


def _trim_fields(record: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    keep = {"source", "sources", "id", "retrieved_utc", *fields}
    return {k: v for k, v in record.items() if k in keep}


# --------------------------------------------------------------------------
# Library location + persistence
# --------------------------------------------------------------------------


def _project_path(runtime: Any) -> Path | None:
    with suppress(Exception):
        if bool(runtime.project_path_is_unset()):
            return None
    try:
        p = Path(runtime.effective_project_path())
    except Exception:
        return None
    return p if p.is_dir() else None


def _resolve_write_path(runtime: Any, raw: str, tool: str) -> Path | str:
    try:
        return Path(runtime.resolve_tool_path(raw, for_write=True))
    except (PermissionError, ValueError) as exc:
        return format_tool_error(
            f"refused to write outside the allowed roots: {exc}",
            code="WRITE_JAIL",
            tool_name=tool,
            suggestion="Pass a path under the project folder (or a configured write root).",
        )
    except AttributeError:
        p = Path(raw).expanduser()
        return p


def _resolve_read_path(runtime: Any, raw: str) -> Path:
    with suppress(Exception):
        return Path(runtime.resolve_tool_path(raw))
    return Path(raw).expanduser()


def library_dir(runtime: Any, library: str = "", *, tool: str = "cite_add") -> Path | str:
    """Where the citation library lives.

    Explicit ``library=`` (write-jailed) → ``{project}/.remedy-research/`` when a
    project is bound → ``default_home()/research/{sha256(project)[:16]}/``. Never
    ``Path.home()/".remedy"`` — :func:`remedy.home.default_home` owns that.
    """
    from remedy.home import default_home

    raw = (library or "").strip()
    if raw:
        resolved = _resolve_write_path(runtime, raw, tool)
        if isinstance(resolved, str):
            return resolved
        target = resolved
    else:
        project = _project_path(runtime)
        if project is not None:
            target = project / RESEARCH_DIRNAME
        else:
            with suppress(Exception):
                project = Path(runtime.effective_project_path())
            key = hashlib.sha256(str(project or "none").encode("utf-8")).hexdigest()[:16]
            target = default_home() / "research" / key
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return format_tool_error(
            f"could not create the library directory {target}: {exc}",
            code="LIBRARY_UNWRITABLE",
            tool_name=tool,
            suggestion="Pass library= to a writable folder.",
        )
    return target


def load_library(directory: Path) -> dict[str, Any]:
    path = directory / INDEX_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "entries": {}}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return {"version": 1, "entries": {}}
    return data


def save_library(directory: Path, library: dict[str, Any]) -> dict[str, str]:
    """Write library.json + refs.bib + refs.csl.json atomically. Returns the paths."""
    from remedy.core.atomic_json import write_json_atomic, write_text_atomic

    entries = library.get("entries") or {}
    ordered = dict(sorted(entries.items()))
    library["entries"] = ordered
    library.setdefault("version", 1)
    library["updated_utc"] = _iso_now()
    write_json_atomic(directory / INDEX_FILENAME, library, default=str)
    write_text_atomic(directory / BIB_FILENAME, render_bibtex(ordered))
    write_json_atomic(directory / CSL_FILENAME, render_csl_json(ordered), default=str)
    return {
        "library_dir": str(directory),
        "bib_path": str(directory / BIB_FILENAME),
        "csl_path": str(directory / CSL_FILENAME),
        "index_path": str(directory / INDEX_FILENAME),
    }


def make_key(record: dict[str, Any], taken: set[str]) -> str:
    """``smith2021attention`` — ascii-folded, lowercased, suffixed a/b/c on collision."""
    family = _fold(first_author_family(record))
    family = re.sub(r"[^a-z]", "", family) or "anon"
    year = str(record.get("year") or "")
    year = year if year.isdigit() and year != "0" else "nd"
    words = [w for w in re.split(r"[^a-z0-9]+", _fold(str(record.get("title") or ""))) if w]
    stop = {"a", "an", "the", "on", "of", "in", "for", "and", "to", "with", "is", "are", "at"}
    head = next((w for w in words if w not in stop and len(w) > 2), "")
    base = f"{family}{year}{head}"[:48] or "ref"
    if base not in taken:
        return base
    for suffix in "abcdefghijklmnopqrstuvwxyz":
        candidate = f"{base}{suffix}"
        if candidate not in taken:
            return candidate
    n = 2
    while f"{base}{n}" in taken:
        n += 1
    return f"{base}{n}"


# --------------------------------------------------------------------------
# BibTeX / CSL-JSON / RIS rendering + parsing
# --------------------------------------------------------------------------

_BIB_ESCAPE = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def _bib_escape(value: str) -> str:
    return "".join(_BIB_ESCAPE.get(ch, ch) for ch in (value or ""))


def _bib_authors(record: dict[str, Any]) -> str:
    parts = []
    for a in record.get("authors") or []:
        if not isinstance(a, dict):
            continue
        family, given = (a.get("family") or "").strip(), (a.get("given") or "").strip()
        if family and given:
            parts.append(f"{family}, {given}")
        elif family or given:
            parts.append(family or given)
    return " and ".join(parts)


def bib_entry_type(record: dict[str, Any]) -> str:
    kind = str(record.get("type") or "").strip().lower()
    if kind in _BIB_TYPE:
        return _BIB_TYPE[kind]
    if record.get("arxiv_id") and not record.get("doi"):
        return "misc"
    return "article" if record.get("venue") else "misc"


def render_bibtex_entry(key: str, entry: dict[str, Any]) -> str:
    record = entry.get("record") or {}
    fields: list[tuple[str, str]] = []
    author = _bib_authors(record)
    if author:
        fields.append(("author", author))
    if record.get("title"):
        fields.append(("title", str(record["title"])))
    venue = str(record.get("venue") or "")
    if venue:
        kind = bib_entry_type(record)
        fields.append(("booktitle" if kind == "inproceedings" else "journal", venue))
    if record.get("year"):
        fields.append(("year", str(record["year"])))
    for src, dst in (("volume", "volume"), ("issue", "number"), ("pages", "pages"),
                     ("publisher", "publisher"), ("doi", "doi"), ("url", "url")):
        if record.get(src):
            fields.append((dst, str(record[src])))
    if record.get("arxiv_id"):
        fields.append(("eprint", str(record["arxiv_id"])))
        fields.append(("archivePrefix", "arXiv"))
    if record.get("pmid"):
        fields.append(("pmid", str(record["pmid"])))
    note = str(entry.get("note") or "")
    if note:
        fields.append(("note", note))
    tags = entry.get("tags") or []
    if tags:
        fields.append(("keywords", ", ".join(str(t) for t in tags)))
    body = ",\n".join(f"  {name} = {{{_bib_escape(value)}}}" for name, value in fields)
    return f"@{bib_entry_type(record)}{{{key},\n{body}\n}}\n"


def render_bibtex(entries: dict[str, Any]) -> str:
    header = (
        "% Written by Remedy (cite_add / cite_import). Do not hand-edit — "
        "regenerated on every library write.\n"
        "% Every entry records where it came from in library.json; "
        "cite_check is what proves it is real.\n\n"
    )
    return header + "\n".join(render_bibtex_entry(k, v) for k, v in entries.items())


def render_csl_entry(key: str, entry: dict[str, Any]) -> dict[str, Any]:
    record = entry.get("record") or {}
    csl: dict[str, Any] = {"id": key, "type": _CSL_TYPE.get(bib_entry_type(record), "article")}
    if record.get("title"):
        csl["title"] = record["title"]
    authors = [
        {"family": a.get("family", ""), "given": a.get("given", "")}
        for a in (record.get("authors") or [])
        if isinstance(a, dict) and (a.get("family") or a.get("given"))
    ]
    if authors:
        csl["author"] = authors
    if record.get("year"):
        parts = [int(record["year"])]
        if record.get("month"):
            parts.append(int(record["month"]))
        csl["issued"] = {"date-parts": [parts]}
    for src, dst in (
        ("venue", "container-title"),
        ("volume", "volume"),
        ("issue", "issue"),
        ("pages", "page"),
        ("publisher", "publisher"),
        ("doi", "DOI"),
        ("url", "URL"),
        ("abstract", "abstract"),
        ("pmid", "PMID"),
        ("pmcid", "PMCID"),
    ):
        if record.get(src):
            csl[dst] = record[src]
    return csl


def render_csl_json(entries: dict[str, Any]) -> list[dict[str, Any]]:
    return [render_csl_entry(k, v) for k, v in entries.items()]


def render_ris_entry(key: str, entry: dict[str, Any]) -> str:
    record = entry.get("record") or {}
    kind = bib_entry_type(record)
    ty = {"article": "JOUR", "inproceedings": "CPAPER", "book": "BOOK",
          "incollection": "CHAP", "phdthesis": "THES", "techreport": "RPRT"}.get(kind, "GEN")
    lines = [f"TY  - {ty}", f"ID  - {key}"]
    for a in record.get("authors") or []:
        if isinstance(a, dict) and (a.get("family") or a.get("given")):
            lines.append(f"AU  - {a.get('family', '')}, {a.get('given', '')}".rstrip(", "))
    for tag, value in (
        ("TI", record.get("title")),
        ("PY", record.get("year")),
        ("JO", record.get("venue")),
        ("VL", record.get("volume")),
        ("IS", record.get("issue")),
        ("SP", record.get("pages")),
        ("DO", record.get("doi")),
        ("UR", record.get("url")),
        ("AB", record.get("abstract")),
    ):
        if value:
            lines.append(f"{tag}  - {value}")
    lines.append("ER  - ")
    return "\n".join(lines) + "\n\n"


def _bib_unescape(value: str) -> str:
    out = value
    for target, source in (
        (r"\textbackslash{}", "\\"), (r"\&", "&"), (r"\%", "%"), (r"\$", "$"),
        (r"\#", "#"), (r"\_", "_"), (r"\{", "{"), (r"\}", "}"),
        (r"\textasciitilde{}", "~"), (r"\textasciicircum{}", "^"),
    ):
        out = out.replace(target, source)
    return out


def parse_bibtex(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse a .bib file into ``[(key, record)]``. Brace-aware, tolerant of junk."""
    out: list[tuple[str, dict[str, Any]]] = []
    for match in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text or ""):
        entry_type, key = match.group(1).lower(), match.group(2)
        if entry_type in ("comment", "preamble", "string"):
            continue
        i, depth = match.end(), 1
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        body = text[match.end(): i - 1]
        fields: dict[str, str] = {}
        pos = 0
        while pos < len(body):
            fm = re.compile(r"\s*([A-Za-z][\w-]*)\s*=\s*").match(body, pos)
            if not fm:
                break
            name, pos = fm.group(1).lower(), fm.end()
            if pos < len(body) and body[pos] == "{":
                depth, start = 1, pos + 1
                pos += 1
                while pos < len(body) and depth:
                    if body[pos] == "{":
                        depth += 1
                    elif body[pos] == "}":
                        depth -= 1
                    pos += 1
                value = body[start: pos - 1]
            elif pos < len(body) and body[pos] == '"':
                start = pos + 1
                pos += 1
                while pos < len(body) and body[pos] != '"':
                    pos += 1
                value = body[start:pos]
                pos += 1
            else:
                start = pos
                while pos < len(body) and body[pos] != ",":
                    pos += 1
                value = body[start:pos]
            fields[name] = _WS_RE.sub(" ", _bib_unescape(value)).strip()
            while pos < len(body) and body[pos] in ", \n\r\t":
                pos += 1
        out.append((key, _record_from_bib_fields(entry_type, fields)))
    return out


def _record_from_bib_fields(entry_type: str, fields: dict[str, str]) -> dict[str, Any]:
    rec = blank_record("import:bibtex")
    rec["title"] = fields.get("title", "")
    for chunk in re.split(r"\s+and\s+", fields.get("author", "")):
        chunk = chunk.strip()
        if chunk:
            rec["authors"].append(_split_name(chunk))
    with suppress(ValueError):
        rec["year"] = int(re.sub(r"\D", "", fields.get("year", "")) or 0)
    rec["venue"] = fields.get("journal") or fields.get("booktitle", "")
    rec["volume"] = fields.get("volume", "")
    rec["issue"] = fields.get("number", "")
    rec["pages"] = fields.get("pages", "")
    rec["publisher"] = fields.get("publisher", "")
    rec["doi"] = normalise_doi(fields.get("doi", ""))
    rec["url"] = fields.get("url", "")
    rec["abstract"] = fields.get("abstract", "")
    rec["pmid"] = re.sub(r"\D", "", fields.get("pmid", ""))
    if fields.get("eprint") and "arxiv" in (fields.get("archiveprefix", "") + fields.get("eprint", "")).lower():
        rec["arxiv_id"] = normalise_arxiv(fields["eprint"])
    elif fields.get("eprint") and _ARXIV_ID_RE.fullmatch(fields["eprint"].strip()):
        rec["arxiv_id"] = normalise_arxiv(fields["eprint"])
    rec["type"] = {"article": "journal-article", "inproceedings": "proceedings-article",
                   "incollection": "book-chapter"}.get(entry_type, entry_type)
    rec["raw_ref"] = fields.get("note", "")
    return rec


def parse_csl_json(text: str) -> list[tuple[str, dict[str, Any]]]:
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    out: list[tuple[str, dict[str, Any]]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        rec = blank_record("import:csljson")
        rec["title"] = _text(item.get("title"))
        for a in item.get("author") or []:
            if isinstance(a, dict):
                rec["authors"].append(_author(_text(a.get("family")), _text(a.get("given"))))
        parts = ((item.get("issued") or {}).get("date-parts") or [[]])[0] or []
        if parts:
            with suppress(TypeError, ValueError):
                rec["year"] = int(parts[0])
            if len(parts) > 1:
                with suppress(TypeError, ValueError):
                    rec["month"] = int(parts[1])
        rec["venue"] = _text(item.get("container-title"))
        rec["volume"] = _text(item.get("volume"))
        rec["issue"] = _text(item.get("issue"))
        rec["pages"] = _text(item.get("page"))
        rec["publisher"] = _text(item.get("publisher"))
        rec["doi"] = normalise_doi(_text(item.get("DOI")))
        rec["url"] = _text(item.get("URL"))
        rec["abstract"] = _text(item.get("abstract"))
        rec["pmid"] = _text(item.get("PMID"))
        rec["pmcid"] = _text(item.get("PMCID"))
        rec["type"] = _text(item.get("type"))
        out.append((_text(item.get("id")), rec))
    return out


def parse_ris(text: str) -> list[tuple[str, dict[str, Any]]]:
    """RIS and MEDLINE/nbib share enough shape to parse in one pass."""
    out: list[tuple[str, dict[str, Any]]] = []
    rec = blank_record("import:ris")
    key = ""
    seen = False
    last_tag = ""

    def _flush() -> None:
        nonlocal rec, key, seen
        if seen and (rec.get("title") or rec.get("doi") or rec.get("pmid")):
            out.append((key, rec))
        rec, key, seen = blank_record("import:ris"), "", False

    for raw_line in (text or "").splitlines():
        m = re.match(r"^([A-Z][A-Z0-9]{1,3})\s*-\s?(.*)$", raw_line)
        if not m:
            if last_tag and raw_line.strip():
                _apply_ris_tag(rec, last_tag, raw_line.strip(), append=True)
            continue
        tag, value = m.group(1), m.group(2).strip()
        last_tag = tag
        # A new "TY  -" (RIS) or "PMID- " (MEDLINE/nbib) starts the next record.
        if tag in ("TY", "PMID") and seen:
            _flush()
        seen = True
        if tag == "ID":
            key = value
        elif tag == "ER":
            _flush()
        else:
            _apply_ris_tag(rec, tag, value)
    _flush()
    return out


def _apply_ris_tag(rec: dict[str, Any], tag: str, value: str, *, append: bool = False) -> None:
    if append:
        if tag in ("TI", "T1"):
            rec["title"] = (rec["title"] + " " + value).strip()
        elif tag == "AB":
            rec["abstract"] = (rec["abstract"] + " " + value).strip()
        return
    if tag in ("TI", "T1", "TT"):
        rec["title"] = value
    elif tag in ("AU", "A1", "FAU"):
        rec["authors"].append(_split_name(value))
    elif tag in ("PY", "Y1", "DP"):
        m = re.search(r"(\d{4})", value)
        if m:
            rec["year"] = int(m.group(1))
    elif tag in ("JO", "JF", "T2", "JA", "JT", "TA"):
        rec["venue"] = rec["venue"] or value
    elif tag in ("VL", "VI"):
        rec["volume"] = value
    elif tag in ("IS", "IP"):
        rec["issue"] = value
    elif tag in ("SP", "PG"):
        rec["pages"] = value
    elif tag == "EP" and rec["pages"]:
        rec["pages"] = f"{rec['pages']}-{value}"
    elif tag in ("DO", "DOI"):
        rec["doi"] = normalise_doi(value)
    elif tag in ("LID", "AID"):
        parts = value.split()
        if parts and "[doi]" in value.lower():
            rec["doi"] = rec["doi"] or normalise_doi(parts[0])
    elif tag == "UR":
        rec["url"] = rec["url"] or value
    elif tag == "AB":
        rec["abstract"] = value
    elif tag == "PMID":
        rec["pmid"] = re.sub(r"\D", "", value)
    elif tag == "PMC":
        rec["pmcid"] = value.upper()
    elif tag == "PB":
        rec["publisher"] = value


def parse_library_file(text: str, fmt: str) -> list[tuple[str, dict[str, Any]]]:
    if fmt == "bibtex":
        return parse_bibtex(text)
    if fmt == "csljson":
        return parse_csl_json(text)
    if fmt in ("ris", "nbib"):
        return parse_ris(text)
    raise ValueError(f"unknown library format: {fmt}")


def detect_library_format(path: Path, text: str) -> str:
    suffix = path.suffix.lower()
    if suffix == ".bib":
        return "bibtex"
    if suffix == ".json":
        return "csljson"
    if suffix == ".ris":
        return "ris"
    if suffix in (".nbib", ".medline", ".txt"):
        return "nbib" if re.search(r"^PMID\s*-", text or "", re.M) else "ris"
    head = (text or "").lstrip()[:200]
    if head.startswith("@"):
        return "bibtex"
    if head.startswith(("[", "{")):
        return "csljson"
    if re.search(r"^TY\s+-", text or "", re.M):
        return "ris"
    if re.search(r"^PMID\s*-", text or "", re.M):
        return "nbib"
    return ""


# --------------------------------------------------------------------------
# PDF text (no heavy dependency, ever)
# --------------------------------------------------------------------------


_PDF_ESCAPES = {b"n": 10, b"r": 13, b"t": 9, b"b": 8, b"f": 12}


def _pdf_unescape(raw: bytes) -> str:
    out = bytearray()
    i = 0
    while i < len(raw):
        ch = raw[i: i + 1]
        if ch != b"\\" or i + 1 >= len(raw):
            out += ch
            i += 1
            continue
        nxt = raw[i + 1: i + 2]
        if nxt in _PDF_ESCAPES:
            out.append(_PDF_ESCAPES[nxt])
            i += 2
            continue
        if b"0" <= nxt <= b"7":
            digits = bytearray()
            j = i + 1
            while j < len(raw) and len(digits) < 3 and b"0" <= raw[j: j + 1] <= b"7":
                digits += raw[j: j + 1]
                j += 1
            out.append(int(digits, 8) & 0xFF)
            i = j
            continue
        out += nxt
        i += 2
    return out.decode("latin-1", errors="replace")


_PDF_TOKEN_RE = re.compile(rb"\((?:\\.|[^\\()])*\)|\bT[dD*]\b|\bET\b", re.S)


def _pdf_ops_to_text(stream: bytes) -> str:
    parts: list[str] = []
    for match in _PDF_TOKEN_RE.finditer(stream):
        token = match.group(0)
        if token.startswith(b"("):
            parts.append(_pdf_unescape(token[1:-1]))
        else:
            parts.append("\n")
    return "".join(parts)


def pdf_text_stdlib(data: bytes, *, max_chars: int = 40_000) -> str:
    """Last-resort PDF text: inflate FlateDecode streams, pull Tj/TJ strings.

    Lossy by construction — ligatures, columns, CID fonts and nested-paren
    strings all degrade. Callers must report ``lossy: true``.
    """
    import zlib

    chunks: list[str] = []
    total = 0
    for match in re.finditer(rb"stream\r?\n", data or b""):
        start = match.end()
        end = data.find(b"endstream", start)
        if end < 0:
            continue
        raw = data[start:end]
        try:
            inflated = zlib.decompress(raw)
        except zlib.error:
            try:
                inflated = zlib.decompressobj().decompress(raw)
            except zlib.error:
                continue
        text = _pdf_ops_to_text(inflated)
        if text.strip():
            chunks.append(text)
            total += len(text)
        if total > max_chars:
            break
    return _WS_RE.sub(" ", "\n".join(chunks))[:max_chars].strip()


def pdf_to_text(data: bytes, *, max_chars: int = 40_000) -> tuple[str, str, bool, str]:
    """``(text, extract_method, lossy, warning)``. Never installs anything."""
    try:  # pure-python, lazily imported: present in many project envs, optional here
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
            if sum(len(p) for p in pages) > max_chars:
                break
        text = "\n\n".join(pages)[:max_chars].strip()
        if text:
            return (
                text,
                "pypdf (in-process)",
                False,
                "pypdf keeps reading order but drops figures, tables and math.",
            )
    except Exception:
        pass
    text = pdf_text_stdlib(data, max_chars=max_chars)
    if text:
        return (
            text,
            "stdlib zlib + PDF text operators",
            True,
            "Fallback extractor: ligatures, columns, CID-font text and nested-paren "
            "strings are lost or mangled. Treat quotes from this text as unverified; "
            "install pypdf in the project env, or run pdftotext -layout, for a clean read.",
        )
    return (
        "",
        "none",
        True,
        "No text could be extracted (scanned or encrypted PDF, or a font encoding this "
        "module cannot decode). No PDF reader is bundled — this is not a claim the PDF "
        "is empty.",
    )


# --------------------------------------------------------------------------
# Manuscript citation extraction
# --------------------------------------------------------------------------


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _strip_tex_comments(text: str) -> str:
    out = []
    for line in text.splitlines(keepends=True):
        idx = 0
        while True:
            idx = line.find("%", idx)
            if idx < 0:
                out.append(line)
                break
            if idx > 0 and line[idx - 1] == "\\":
                idx += 1
                continue
            out.append(line[:idx] + ("\n" if line.endswith("\n") else ""))
            break
    return "".join(out)


_TEX_SUFFIXES = {".tex", ".ltx", ".latex", ".sty", ".cls"}


def extract_citations(text: str, *, suffix: str = "") -> dict[str, list[dict[str, Any]]]:
    """Every citation-shaped thing in a manuscript, with its line number.

    Returns ``{"keys": [...], "dois": [...], "arxiv": [...], "pmids": [...],
    "urls": [...]}``. Pandoc/Quarto ``@key`` syntax is only scanned in markdown
    sources, where an ``@`` is a citation rather than an email or a decorator;
    ``%`` only starts a comment in TeX, so URLs with ``%20`` survive elsewhere.
    """
    suffix_low = (suffix or "").lower()
    body = _strip_tex_comments(text or "") if suffix_low in _TEX_SUFFIXES else (text or "")
    keys: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, int]] = set()
    for m in _CITE_CMD_RE.finditer(body):
        line = _line_of(body, m.start())
        for key in m.group(1).split(","):
            key = key.strip()
            if key and (key, line) not in seen_keys:
                seen_keys.add((key, line))
                keys.append({"key": key, "line": line, "syntax": "latex"})
    if suffix_low in _MARKDOWN_SUFFIXES:
        for m in _PANDOC_CITE_RE.finditer(body):
            line = _line_of(body, m.start())
            key = m.group(1)
            if (key, line) not in seen_keys:
                seen_keys.add((key, line))
                keys.append({"key": key, "line": line, "syntax": "pandoc"})
    urls = [
        {"url": m.group(0).rstrip(".,;)"), "line": _line_of(body, m.start())}
        for m in _URL_RE.finditer(body)
    ]
    url_span = " ".join(str(u["url"]) for u in urls)
    dois: list[dict[str, Any]] = []
    seen_dois: set[str] = set()
    for m in _DOI_RE.finditer(body):
        doi = normalise_doi(m.group(0))
        if not doi or doi in seen_dois:
            continue
        seen_dois.add(doi)
        dois.append({"doi": doi, "line": _line_of(body, m.start()), "in_url": doi in url_span.lower()})
    arxiv = []
    seen_arxiv: set[str] = set()
    for m in _ARXIV_TAGGED_RE.finditer(body):
        value = normalise_arxiv(m.group(1))
        if value and _ARXIV_ID_RE.fullmatch(value) and value not in seen_arxiv:
            seen_arxiv.add(value)
            arxiv.append({"arxiv_id": value, "line": _line_of(body, m.start())})
    pmids = []
    seen_pmids: set[str] = set()
    for m in _PMID_TAGGED_RE.finditer(body):
        value = m.group(1)
        if value not in seen_pmids:
            seen_pmids.add(value)
            pmids.append({"pmid": value, "line": _line_of(body, m.start())})
    return {"keys": keys, "dois": dois, "arxiv": arxiv, "pmids": pmids, "urls": urls}


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def register_research_tools(runtime: Any) -> None:
    """Register lit_search, lit_fetch and the cite_* citation library tools."""

    # ---------------------------------------------------------------- search

    async def lit_search(
        query: str = "",
        source: str = "auto",
        max_results: int = 10,
        year_from: int = 0,
        year_to: int = 0,
        open_access_only: bool = False,
        fields: str = "",
        sort: str = "relevance",
        timeout_seconds: float = 30.0,
    ) -> str:
        """Search the public scholarly APIs and return normalised records."""
        q = (query or "").strip()
        if not q:
            return format_tool_error(
                "query is required",
                code="MISSING_QUERY",
                tool_name="lit_search",
                suggestion='lit_search(query="sparse attention long context", source="auto")',
            )
        if not web_tools_enabled(runtime):
            return _web_disabled("lit_search")
        n = _clamp_int(max_results, 1, 50, 10)
        timeout = _clamp_float(timeout_seconds, 5.0, 120.0, 30.0)
        sort_mode = (sort or "relevance").strip().lower()
        if sort_mode not in ("relevance", "date", "citations"):
            sort_mode = "relevance"
        requested = (source or "auto").strip().lower()
        if requested == "auto":
            wanted = auto_sources(q)
        elif requested == "all":
            wanted = list(SOURCES)
        elif requested in SOURCES:
            wanted = [requested]
        else:
            return format_tool_error(
                f"unknown source {source!r}",
                code="BAD_SOURCE",
                tool_name="lit_search",
                suggestion="source must be auto, all, or one of: " + ", ".join(SOURCES),
            )
        yf = _clamp_int(year_from, 0, 2200, 0)
        yt = _clamp_int(year_to, 0, 2200, 0)

        records: list[dict[str, Any]] = []
        notes: list[str] = []
        tried: list[str] = []
        failed: list[dict[str, str]] = []
        for src in wanted:
            tried.append(src)
            try:
                if src == "arxiv":
                    got, sub = search_arxiv(q, max_results=n, sort=sort_mode, timeout=timeout)
                elif src == "crossref":
                    got, sub = search_crossref(
                        q, max_results=n, year_from=yf, year_to=yt, sort=sort_mode, timeout=timeout
                    )
                elif src == "openalex":
                    got, sub = search_openalex(
                        q, max_results=n, year_from=yf, year_to=yt,
                        open_access_only=open_access_only, sort=sort_mode, timeout=timeout,
                    )
                elif src == "pubmed":
                    got, sub = search_pubmed(
                        q, max_results=n, year_from=yf, year_to=yt, sort=sort_mode, timeout=timeout
                    )
                else:
                    got, sub = search_semanticscholar(
                        q, max_results=n, year_from=yf, year_to=yt, timeout=timeout
                    )
            except Exception as exc:  # a source that fails is named, not dropped
                failed.append(_source_failure(src, exc))
                continue
            records.extend(got)
            notes.extend(sub)
        if not records and failed and len(failed) == len(tried):
            return json.dumps(
                {
                    "query": q,
                    "sources_tried": tried,
                    "sources_failed": failed,
                    "count": 0,
                    "records": [],
                    "notes": notes
                    + ["Every source failed — this is not evidence the literature is empty."],
                },
                indent=2,
                default=str,
            )

        merged, merge_notes = merge_records(records)
        notes.extend(merge_notes)
        if yf or yt:
            before = len(merged)
            merged = [
                r for r in merged
                if not r.get("year")
                or ((not yf or r["year"] >= yf) and (not yt or r["year"] <= yt))
            ]
            if len(merged) != before:
                notes.append(f"year filter dropped {before - len(merged)} record(s).")
        if open_access_only:
            before = len(merged)
            merged = [r for r in merged if _looks_open_access(r)]
            notes.append(
                f"open_access_only kept {len(merged)} of {before}; OA status is whatever the "
                "source reported (Crossref rarely reports it) — absence is not proof of paywall."
            )
        merged = _sort_records(merged, sort_mode)[:n]
        field_list = [f.strip() for f in (fields or "").split(",") if f.strip()]
        unknown = [f for f in field_list if f not in RECORD_FIELDS]
        if unknown:
            notes.append("ignored unknown fields: " + ", ".join(unknown))
            field_list = [f for f in field_list if f in RECORD_FIELDS]
        out_records = [_trim_fields(r, field_list) for r in merged] if field_list else merged
        if failed:
            notes.append(
                "PARTIAL RESULT: " + ", ".join(f"{f['source']} ({f['code']})" for f in failed)
                + " did not answer. Re-run to include them."
            )
        return json.dumps(
            {
                "query": q,
                "source": requested,
                "sources_tried": tried,
                "sources_failed": failed,
                "count": len(out_records),
                "records": out_records,
                "notes": notes,
            },
            indent=2,
            default=str,
        )

    # ----------------------------------------------------------------- fetch

    def _resolve_record(ident: str, url: str, timeout: float) -> tuple[dict[str, Any], list[str], list[dict[str, str]]]:
        """One record for an identifier, from the source that owns it."""
        notes: list[str] = []
        failed: list[dict[str, str]] = []
        kind, value = parse_identifier(ident or url)
        order: list[tuple[str, Any]] = []
        if kind == "doi":
            order = [("crossref", value), ("openalex", value)]
        elif kind == "arxiv":
            order = [("arxiv", value)]
        elif kind == "pmid":
            order = [("pubmed", value)]
        elif kind == "pmc":
            order = [("pubmed", value)]
        elif kind == "openalex":
            order = [("openalex", value)]
        elif kind == "s2":
            order = [("semanticscholar", value)]
        for src, val in order:
            try:
                if src == "crossref":
                    got, sub = search_crossref(f"doi:{val}", max_results=1, timeout=timeout)
                elif src == "openalex":
                    got, sub = search_openalex(f"doi:{val}" if kind == "doi" else val,
                                               max_results=1, timeout=timeout)
                elif src == "arxiv":
                    got, sub = search_arxiv(f"arxiv:{val}", max_results=1, timeout=timeout)
                elif src == "pubmed":
                    if kind == "pmc":
                        got, sub = [], ["pmc ids resolve through their PMID; metadata may be thin."]
                    else:
                        got, sub = search_pubmed(f"pmid:{val}", max_results=1, timeout=timeout)
                else:
                    got, sub = search_semanticscholar(f"s2:{val}", max_results=1, timeout=timeout)
            except Exception as exc:
                failed.append(_source_failure(src, exc))
                continue
            notes.extend(sub)
            if got:
                return got[0], notes, failed
        return blank_record(), notes, failed

    async def lit_fetch(
        id: str = "",
        url: str = "",
        want: str = "abstract",
        max_chars: int = 40000,
        save_path: str = "",
        timeout_seconds: float = 60.0,
    ) -> str:
        """Fetch metadata / abstract / full text / PDF text for one paper."""
        ident = (id or "").strip()
        target_url = (url or "").strip()
        if not ident and not target_url:
            return format_tool_error(
                "pass id= (doi:…, arxiv:…, pmid:…, pmc:…, openalex:…, s2:…) or url=",
                code="MISSING_ID",
                tool_name="lit_fetch",
                suggestion='lit_fetch(id="10.1038/nature14539", want="abstract")',
            )
        if not web_tools_enabled(runtime):
            return _web_disabled("lit_fetch")
        mode = (want or "abstract").strip().lower()
        if mode not in ("metadata", "abstract", "fulltext", "pdf"):
            return format_tool_error(
                f"unknown want={want!r}",
                code="BAD_WANT",
                tool_name="lit_fetch",
                suggestion="want must be metadata, abstract, fulltext or pdf.",
            )
        cap = _clamp_int(max_chars, 500, 400_000, 40_000)
        timeout = _clamp_float(timeout_seconds, 10.0, 240.0, 60.0)

        record, notes, failed = _resolve_record(ident, target_url, timeout)
        text = ""
        extract_method = "metadata only"
        lossy = False
        resolved_url = record.get("url") or target_url

        if mode == "abstract":
            text = str(record.get("abstract") or "")
            extract_method = "source abstract field"
            if not text and record.get("pmid"):
                params = dict(_eutils_common())
                params.update({"db": "pubmed", "id": str(record["pmid"]),
                               "rettype": "abstract", "retmode": "xml"})
                efetch = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urlencode(params)
                try:
                    xml_text = _fetch_text(efetch, timeout=timeout)
                except Exception as exc:
                    failed.append(_source_failure("pubmed-efetch", exc))
                else:
                    chunks = re.findall(r"<AbstractText[^>]*>(.*?)</AbstractText>", xml_text, re.S)
                    text = _strip_markup(" ".join(chunks))
                    record["abstract"] = text
                    extract_method = "pubmed efetch (AbstractText)"
            if not text:
                notes.append(
                    "No abstract was returned by the source. That is a gap in the record, not "
                    "an empty abstract — check the publisher landing page with want='fulltext'."
                )
        elif mode == "fulltext":
            if record.get("pmcid"):
                params = dict(_eutils_common())
                params.update({"db": "pmc", "id": str(record["pmcid"]).replace("PMC", ""),
                               "retmode": "xml"})
                efetch = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urlencode(params)
                try:
                    text = _strip_markup(_fetch_text(efetch, timeout=timeout))
                    extract_method = "PMC OA XML (efetch db=pmc)"
                    resolved_url = efetch
                except Exception as exc:
                    failed.append(_source_failure("pmc", exc))
            if not text:
                page = record.get("url") or target_url
                if record.get("arxiv_id"):
                    page = f"https://arxiv.org/abs/{record['arxiv_id']}"
                if page:
                    try:
                        final, raw, charset = _fetch_bytes(page, timeout=timeout)
                        from remedy.core.html_extract import html_to_markdown

                        body = raw.decode(charset or "utf-8", errors="replace")
                        extracted = html_to_markdown(body, max_chars=cap)
                        text = str(extracted.get("markdown") or "").strip()
                        extract_method = "landing page HTML → markdown"
                        resolved_url = final
                    except Exception as exc:
                        failed.append(_source_failure("landing-page", exc))
            if not text:
                text = str(record.get("abstract") or "")
                if text:
                    extract_method = "ABSTRACT ONLY (full text not obtained)"
                    notes.append(
                        "Full text was not obtained; the text below is the ABSTRACT. "
                        "Do not treat it as the paper."
                    )
        elif mode == "pdf":
            pdf_url = record.get("pdf_url") or (target_url if target_url.lower().endswith(".pdf") else "")
            if not pdf_url and record.get("arxiv_id"):
                pdf_url = f"https://arxiv.org/pdf/{record['arxiv_id']}"
            if not pdf_url:
                notes.append(
                    "No open-access PDF URL is known for this record; returning the abstract. "
                    "Check the publisher page or an institutional subscription."
                )
                text = str(record.get("abstract") or "")
                extract_method = "ABSTRACT ONLY (no PDF url)"
            else:
                try:
                    final, raw, _charset = _fetch_bytes(pdf_url, timeout=timeout)
                    resolved_url = final
                    text, extract_method, lossy, warning = pdf_to_text(raw, max_chars=cap)
                    if warning:
                        notes.append(warning)
                    if not text:
                        text = str(record.get("abstract") or "")
                        if text:
                            extract_method = "ABSTRACT ONLY (PDF text extraction failed)"
                except Exception as exc:
                    failed.append(_source_failure("pdf", exc))
                    text = str(record.get("abstract") or "")
                    extract_method = "ABSTRACT ONLY (PDF fetch failed)"

        truncated = len(text) > cap
        if truncated:
            text = text[:cap]
            notes.append(f"text truncated at {cap} chars (max_chars).")

        saved = ""
        if (save_path or "").strip():
            resolved = _resolve_write_path(runtime, save_path.strip(), "lit_fetch")
            if isinstance(resolved, str):
                return resolved
            try:
                resolved.parent.mkdir(parents=True, exist_ok=True)
                from remedy.core.atomic_json import write_text_atomic

                write_text_atomic(resolved, text)
                saved = str(resolved)
                with suppress(Exception):
                    from remedy.core.workspace_tools.guards import note_path

                    note_path(runtime, resolved)
            except OSError as exc:
                notes.append(f"could not write save_path: {exc}")
        if failed:
            notes.append(
                "Some lookups failed: "
                + ", ".join(f"{f['source']} ({f['code']})" for f in failed)
            )
        return json.dumps(
            {
                "id": ident or target_url,
                "resolved_url": resolved_url,
                "want": mode,
                "extract_method": extract_method,
                "lossy": lossy,
                "chars": len(text),
                "truncated": truncated,
                "text": text,
                "saved_to": saved,
                "record": record,
                "sources_failed": failed,
                "notes": notes,
            },
            indent=2,
            default=str,
        )

    # --------------------------------------------------------------- library

    def _entry_from_record(record: dict[str, Any], tags: str, note: str, tool: str) -> dict[str, Any]:
        return {
            "record": record,
            "tags": [t.strip() for t in (tags or "").split(",") if t.strip()],
            "note": (note or "").strip(),
            "provenance": {
                "source": record.get("source") or "",
                "sources": record.get("sources") or [],
                "retrieved_utc": record.get("retrieved_utc") or _iso_now(),
                "verified_utc": "",
                "verified_identifier": "",
                "added_by_tool": tool,
                "added_utc": _iso_now(),
            },
        }

    def _find_existing(library: dict[str, Any], record: dict[str, Any]) -> str:
        doi = normalise_doi(str(record.get("doi") or ""))
        arx = str(record.get("arxiv_id") or "")
        pmid = str(record.get("pmid") or "")
        for key, entry in (library.get("entries") or {}).items():
            other = entry.get("record") or {}
            if doi and normalise_doi(str(other.get("doi") or "")) == doi:
                return key
            if pmid and str(other.get("pmid") or "") == pmid:
                return key
            if arx and str(other.get("arxiv_id") or "") == arx:
                return key
        return ""

    async def cite_add(
        record_json: str = "",
        id: str = "",
        key: str = "",
        library: str = "",
        tags: str = "",
        note: str = "",
    ) -> str:
        """Add one entry to the citation library (idempotent by DOI/arXiv/PMID)."""
        raw_record = (record_json or "").strip()
        ident = (id or "").strip()
        if not raw_record and not ident:
            return format_tool_error(
                "pass record_json (a record from lit_search) or id (resolved online)",
                code="MISSING_INPUT",
                tool_name="cite_add",
                suggestion='cite_add(id="10.1038/nature14539") or cite_add(record_json=…)',
            )
        directory = library_dir(runtime, library, tool="cite_add")
        if isinstance(directory, str):
            return directory

        record: dict[str, Any]
        notes: list[str] = []
        if raw_record:
            try:
                parsed = json.loads(raw_record)
            except json.JSONDecodeError as exc:
                return format_tool_error(
                    f"record_json is not valid JSON: {exc}",
                    code="BAD_RECORD",
                    tool_name="cite_add",
                    suggestion="Paste one record object from lit_search's records[] array.",
                )
            if isinstance(parsed, list):
                if len(parsed) != 1:
                    return format_tool_error(
                        f"record_json holds {len(parsed)} records; cite_add takes exactly one",
                        code="BAD_RECORD",
                        tool_name="cite_add",
                        suggestion="Call cite_add once per record, or use cite_import for a file.",
                    )
                parsed = parsed[0]
            if not isinstance(parsed, dict):
                return format_tool_error(
                    "record_json must be a JSON object",
                    code="BAD_RECORD",
                    tool_name="cite_add",
                )
            record = blank_record(str(parsed.get("source") or "supplied"))
            record.update({k: v for k, v in parsed.items() if k in RECORD_FIELDS})
            record["doi"] = normalise_doi(str(record.get("doi") or ""))
            record["retrieved_utc"] = str(parsed.get("retrieved_utc") or _iso_now())
        else:
            if not web_tools_enabled(runtime):
                return _web_disabled("cite_add")
            record, sub, failed = _resolve_record(ident, "", 30.0)
            notes.extend(sub)
            if not record.get("title") and not record.get("doi"):
                return format_tool_error(
                    f"could not resolve {ident!r} at any source"
                    + (f" ({failed[0]['code']})" if failed else ""),
                    code="ID_UNRESOLVED",
                    tool_name="cite_add",
                    suggestion="Check the identifier, or paste a lit_search record with record_json=.",
                )
        if not record.get("title"):
            notes.append("entry has no title — cite_check will flag it as unverifiable.")

        lib = load_library(directory)
        entries = lib.setdefault("entries", {})
        wanted_key = re.sub(r"[^A-Za-z0-9:_.+-]", "", (key or "").strip())
        existing = wanted_key if wanted_key and wanted_key in entries else _find_existing(lib, record)
        updated = bool(existing)
        if existing:
            final_key = existing
            entry = entries[final_key]
            old = entry.get("record") or {}
            for field, value in record.items():
                if value not in ("", 0, [], None):
                    old[field] = value
            entry["record"] = old
            if tags:
                merged_tags = list(entry.get("tags") or [])
                for t in (t.strip() for t in tags.split(",")):
                    if t and t not in merged_tags:
                        merged_tags.append(t)
                entry["tags"] = merged_tags
            if note:
                entry["note"] = note.strip()
            entry.setdefault("provenance", {})["updated_utc"] = _iso_now()
            notes.append(f"existing entry {final_key} updated (matched on identifier or key).")
        else:
            final_key = wanted_key or make_key(record, set(entries))
            if final_key in entries:
                final_key = make_key(record, set(entries))
            entries[final_key] = _entry_from_record(record, tags, note, "cite_add")
        paths = save_library(directory, lib)
        return json.dumps(
            {
                "key": final_key,
                "added": not updated,
                "updated": updated,
                **paths,
                "entry": entries[final_key],
                "notes": notes
                + [
                    "Nothing here is verified yet — run cite_check(manuscript=…, resolve=true) "
                    "before the manuscript is called done."
                ],
            },
            indent=2,
            default=str,
        )

    async def cite_import(
        path: str = "",
        library: str = "",
        format: str = "auto",
        merge: str = "keep",
    ) -> str:
        """Import an existing .bib / CSL-JSON / .ris / .nbib into the library."""
        raw = (path or "").strip()
        if not raw:
            return format_tool_error(
                "path is required",
                code="MISSING_PATH",
                tool_name="cite_import",
                suggestion='cite_import(path="refs.bib")',
            )
        src = _resolve_read_path(runtime, raw)
        try:
            text = src.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return format_tool_error(
                f"cannot read {src}: {exc}",
                code="NOT_FOUND",
                tool_name="cite_import",
                suggestion="Check the path with list_dir on its parent.",
            )
        fmt = (format or "auto").strip().lower()
        if fmt == "auto":
            fmt = detect_library_format(src, text)
        if fmt not in ("bibtex", "csljson", "ris", "nbib"):
            return format_tool_error(
                f"could not tell what format {src.name} is",
                code="BAD_FORMAT",
                tool_name="cite_import",
                suggestion="Pass format=bibtex|csljson|ris|nbib.",
            )
        mode = (merge or "keep").strip().lower()
        if mode not in ("keep", "overwrite", "rename"):
            return format_tool_error(
                f"unknown merge={merge!r}",
                code="BAD_MERGE",
                tool_name="cite_import",
                suggestion="merge must be keep, overwrite or rename.",
            )
        directory = library_dir(runtime, library, tool="cite_import")
        if isinstance(directory, str):
            return directory
        try:
            parsed = parse_library_file(text, fmt)
        except (ValueError, json.JSONDecodeError) as exc:
            return format_tool_error(
                f"could not parse {src.name} as {fmt}: {exc}",
                code="PARSE_FAILED",
                tool_name="cite_import",
                suggestion="Check the file opens in a reference manager, or pass format= explicitly.",
            )
        lib = load_library(directory)
        entries = lib.setdefault("entries", {})
        imported, skipped = 0, 0
        collisions: list[dict[str, str]] = []
        for key, record in parsed:
            record["source"] = record.get("source") or f"import:{fmt}"
            record["sources"] = [record["source"]]
            candidate = re.sub(r"[^A-Za-z0-9:_.+-]", "", key or "") or make_key(record, set(entries))
            duplicate = _find_existing(lib, record)
            if duplicate and duplicate != candidate:
                collisions.append({"key": candidate, "existing": duplicate, "action": "skipped-duplicate-identifier"})
                skipped += 1
                continue
            if candidate in entries:
                if mode == "keep":
                    collisions.append({"key": candidate, "existing": candidate, "action": "kept-existing"})
                    skipped += 1
                    continue
                if mode == "overwrite":
                    collisions.append({"key": candidate, "existing": candidate, "action": "overwritten"})
                else:
                    new_key = make_key(record, set(entries))
                    collisions.append({"key": candidate, "existing": candidate, "action": f"renamed-to-{new_key}"})
                    candidate = new_key
            entries[candidate] = _entry_from_record(record, "", "", "cite_import")
            imported += 1
        paths = save_library(directory, lib)
        return json.dumps(
            {
                "source_path": str(src),
                "format": fmt,
                "imported": imported,
                "skipped": skipped,
                "collisions": collisions,
                "total_entries": len(entries),
                **paths,
                "notes": [
                    "Imported entries are unverified: nothing here has been resolved against a "
                    "registry. Run cite_check(manuscript=…, resolve=true) to find out which are real."
                ],
            },
            indent=2,
            default=str,
        )

    async def cite_list(
        library: str = "",
        query: str = "",
        tags: str = "",
        limit: int = 50,
        format: str = "summary",
    ) -> str:
        """List the citation library (summary | bibtex | csljson | keys)."""
        directory = library_dir(runtime, library, tool="cite_list")
        if isinstance(directory, str):
            return directory
        lib = load_library(directory)
        entries = lib.get("entries") or {}
        q = _fold((query or "").strip())
        want_tags = {t.strip().lower() for t in (tags or "").split(",") if t.strip()}
        selected: dict[str, Any] = {}
        for key, entry in entries.items():
            record = entry.get("record") or {}
            if want_tags and not want_tags & {str(t).lower() for t in (entry.get("tags") or [])}:
                continue
            if q:
                haystack = _fold(
                    " ".join(
                        [
                            key,
                            str(record.get("title") or ""),
                            str(record.get("year") or ""),
                            " ".join(
                                f"{a.get('family', '')} {a.get('given', '')}"
                                for a in (record.get("authors") or [])
                                if isinstance(a, dict)
                            ),
                        ]
                    )
                )
                if q not in haystack:
                    continue
            selected[key] = entry
        n = _clamp_int(limit, 1, 1000, 50)
        keys = list(selected)[:n]
        fmt = (format or "summary").strip().lower()
        if fmt == "keys":
            payload: Any = {"library_dir": str(directory), "count": len(keys), "keys": keys}
        elif fmt == "bibtex":
            return render_bibtex({k: selected[k] for k in keys})
        elif fmt == "csljson":
            payload = render_csl_json({k: selected[k] for k in keys})
        else:
            rows = []
            for key in keys:
                entry = selected[key]
                record = entry.get("record") or {}
                provenance = entry.get("provenance") or {}
                rows.append(
                    {
                        "key": key,
                        "title": record.get("title") or "",
                        "first_author": first_author_family(record),
                        "year": record.get("year") or 0,
                        "doi": record.get("doi") or "",
                        "arxiv_id": record.get("arxiv_id") or "",
                        "pmid": record.get("pmid") or "",
                        "venue": record.get("venue") or "",
                        "tags": entry.get("tags") or [],
                        "source": provenance.get("source") or record.get("source") or "",
                        "verified_utc": provenance.get("verified_utc") or "",
                    }
                )
            payload = {
                "library_dir": str(directory),
                "total": len(entries),
                "matched": len(selected),
                "shown": len(rows),
                "entries": rows,
                "notes": [
                    "verified_utc empty means this entry has never been resolved against a "
                    "registry. Only cite_check sets it."
                ],
            }
        return json.dumps(payload, indent=2, default=str)

    async def cite_export(
        library: str = "",
        format: str = "bibtex",
        out_path: str = "",
        keys: str = "",
        only_cited_in: str = "",
    ) -> str:
        """Write a filtered library beside a manuscript (bibtex | csljson | ris)."""
        fmt = (format or "bibtex").strip().lower()
        if fmt not in ("bibtex", "csljson", "ris"):
            return format_tool_error(
                f"unknown format={format!r}",
                code="BAD_FORMAT",
                tool_name="cite_export",
                suggestion="format must be bibtex, csljson or ris.",
            )
        directory = library_dir(runtime, library, tool="cite_export")
        if isinstance(directory, str):
            return directory
        lib = load_library(directory)
        entries = lib.get("entries") or {}
        notes: list[str] = []
        wanted = [k.strip() for k in (keys or "").split(",") if k.strip()]
        if (only_cited_in or "").strip():
            manuscript = _resolve_read_path(runtime, only_cited_in.strip())
            try:
                text = manuscript.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                return format_tool_error(
                    f"cannot read manuscript {manuscript}: {exc}",
                    code="NOT_FOUND",
                    tool_name="cite_export",
                )
            cited = extract_citations(text, suffix=manuscript.suffix)
            wanted = sorted({c["key"] for c in cited["keys"]} | set(wanted))
            missing = [k for k in wanted if k not in entries]
            if missing:
                notes.append(
                    "cited but NOT in the library (not exported): " + ", ".join(missing)
                    + " — run cite_check before building."
                )
        selected = {k: entries[k] for k in (wanted or list(entries)) if k in entries}
        ext = {"bibtex": ".bib", "csljson": ".json", "ris": ".ris"}[fmt]
        raw_out = (out_path or "").strip()
        if raw_out:
            resolved_out = _resolve_write_path(runtime, raw_out, "cite_export")
            if isinstance(resolved_out, str):
                return resolved_out
            resolved = resolved_out
        else:
            # No target given: stay inside the library dir, which is already ours.
            resolved = directory / f"refs.export{ext}"
        if fmt == "bibtex":
            body = render_bibtex(selected)
        elif fmt == "csljson":
            body = json.dumps(render_csl_json(selected), indent=2, default=str)
        else:
            body = "".join(render_ris_entry(k, v) for k, v in selected.items())
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            from remedy.core.atomic_json import write_text_atomic

            write_text_atomic(resolved, body)
        except OSError as exc:
            return format_tool_error(
                f"could not write {resolved}: {exc}",
                code="WRITE_FAILED",
                tool_name="cite_export",
            )
        with suppress(Exception):
            from remedy.core.workspace_tools.guards import note_path

            note_path(runtime, resolved)
        return json.dumps(
            {
                "out_path": str(resolved),
                "format": fmt,
                "count": len(selected),
                "keys": list(selected),
                "library_dir": str(directory),
                "notes": notes,
            },
            indent=2,
            default=str,
        )

    # ------------------------------------------------------------ cite_check

    def _resolve_identifier(kind: str, value: str, timeout: float) -> tuple[dict[str, Any], str]:
        """Resolve one identifier for verification.

        Returns ``(record, code)``; ``code == ""`` means it resolved with metadata
        to compare, ``"RESOLVER_ONLY"`` means the identifier exists but no title
        came back, and anything else is why it did not resolve.
        """
        trail: list[str] = []
        if kind == "doi":
            try:
                got, _n = search_crossref(f"doi:{value}", max_results=1, timeout=timeout)
                if got and (got[0].get("title") or got[0].get("doi")):
                    return got[0], ""
                trail.append("crossref:EMPTY")
            except Exception as exc:
                trail.append("crossref:" + _source_failure("crossref", exc)["code"])
            # Crossref does not hold DataCite / mEDRA registrants — ask the
            # resolver itself before calling a DOI dead.
            try:
                _fetch_bytes(f"https://doi.org/{quote(value, safe='/')}", timeout=timeout)
                rec = blank_record("doi.org")
                rec["doi"] = value
                return rec, "RESOLVER_ONLY"
            except Exception as exc:
                trail.append("doi.org:" + _source_failure("doi.org", exc)["code"])
            return blank_record(), ", ".join(trail) or "NOT_FOUND"
        try:
            if kind == "arxiv":
                got, _n = search_arxiv(f"arxiv:{value}", max_results=1, timeout=timeout)
            elif kind == "pmid":
                got, _n = search_pubmed(f"pmid:{value}", max_results=1, timeout=timeout)
            else:
                return blank_record(), "UNSUPPORTED_IDENTIFIER"
        except Exception as exc:
            return blank_record(), _source_failure(kind, exc)["code"]
        if got and (got[0].get("title") or got[0].get("id")):
            return got[0], ""
        return blank_record(), "NOT_FOUND"

    async def cite_check(
        manuscript: str = "",
        library: str = "",
        resolve: bool = True,
        strict: bool = False,
        max_checks: int = 60,
        timeout_seconds: float = 60.0,
    ) -> str:
        """Check every citation in a manuscript against the library and the registries.

        This is the anti-fabrication guard: it is the only place in Remedy that
        may call a citation verified, and only for an identifier it resolved in
        this call.
        """
        raw = (manuscript or "").strip()
        if not raw:
            return format_tool_error(
                "manuscript is required",
                code="MISSING_MANUSCRIPT",
                tool_name="cite_check",
                suggestion='cite_check(manuscript="paper.tex", resolve=true)',
            )
        path = _resolve_read_path(runtime, raw)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return format_tool_error(
                f"cannot read manuscript {path}: {exc}",
                code="NOT_FOUND",
                tool_name="cite_check",
                suggestion="Check the path with list_dir on its parent.",
            )
        directory = library_dir(runtime, library, tool="cite_check")
        if isinstance(directory, str):
            return directory
        lib = load_library(directory)
        entries = lib.get("entries") or {}
        found = extract_citations(text, suffix=path.suffix)
        cap = _clamp_int(max_checks, 1, 500, 60)
        timeout = _clamp_float(timeout_seconds, 10.0, 240.0, 60.0)
        online = bool(resolve) and web_tools_enabled(runtime)
        notes: list[str] = []
        if resolve and not online:
            notes.append(
                "resolve=true was requested but web tools are OFF, so nothing was checked "
                "against a registry. Every network row below is UNVERIFIED. Enable with "
                "update_settings(web_tools_enabled=true) and re-run."
            )
        if not resolve:
            notes.append(
                "resolve=false: structural check only. No DOI, arXiv id, PMID or URL was "
                "confirmed to exist."
            )

        rows: list[dict[str, Any]] = []
        unresolved: list[str] = []
        checks_used = 0
        verified_now: dict[str, tuple[str, str]] = {}

        def budget_left() -> bool:
            return checks_used < cap

        cited_keys: dict[str, int] = {}
        for item in found["keys"]:
            cited_keys.setdefault(item["key"], item["line"])

        for key, line in cited_keys.items():
            entry = entries.get(key)
            if entry is None:
                rows.append(
                    {
                        "kind": "key",
                        "key": key,
                        "identifier": "",
                        "status": "KEY_MISSING",
                        "evidence": f"\\cite/@{key} appears in the text but no entry exists in {directory}",
                        "resolved_title": "",
                        "library_title": "",
                        "similarity": 0.0,
                        "line": line,
                    }
                )
                unresolved.append(key)
                continue
            record = entry.get("record") or {}
            provenance = entry.get("provenance") or {}
            doi = normalise_doi(str(record.get("doi") or ""))
            arx = str(record.get("arxiv_id") or "")
            pmid = str(record.get("pmid") or "")
            kind, value = ("doi", doi) if doi else ("arxiv", arx) if arx else ("pmid", pmid) if pmid else ("", "")
            library_title = str(record.get("title") or "")
            if not kind:
                rows.append(
                    {
                        "kind": "key",
                        "key": key,
                        "identifier": "",
                        "status": "NO_IDENTIFIER",
                        "evidence": "library entry has no DOI, arXiv id or PMID — UNVERIFIABLE",
                        "resolved_title": "",
                        "library_title": library_title,
                        "similarity": 0.0,
                        "line": line,
                    }
                )
                continue
            identifier = f"{kind}:{value}"
            if not online or not budget_left():
                cached = str(provenance.get("verified_utc") or "")
                if cached and str(provenance.get("verified_identifier") or "") == identifier:
                    status = "OK_CACHED"
                    evidence = f"OK (cached {cached}) — NOT re-checked in this run"
                else:
                    status = "UNVERIFIED"
                    evidence = (
                        "not checked this run: "
                        + ("max_checks reached" if online else "network off / resolve=false")
                    )
                rows.append(
                    {
                        "kind": "key",
                        "key": key,
                        "identifier": identifier,
                        "status": status,
                        "evidence": evidence,
                        "resolved_title": "",
                        "library_title": library_title,
                        "similarity": 0.0,
                        "line": line,
                    }
                )
                continue
            checks_used += 1
            resolved_rec, code = _resolve_identifier(kind, value, timeout)
            if code and code != "RESOLVER_ONLY":
                rows.append(
                    {
                        "kind": "key",
                        "key": key,
                        "identifier": identifier,
                        "status": "DOI_UNRESOLVED" if kind == "doi" else "ID_UNRESOLVED",
                        "evidence": f"{identifier} did not resolve ({code}) — the reference may not exist",
                        "resolved_title": "",
                        "library_title": library_title,
                        "similarity": 0.0,
                        "line": line,
                    }
                )
                unresolved.append(identifier)
                continue
            resolved_title = str(resolved_rec.get("title") or "")
            if code == "RESOLVER_ONLY" or not resolved_title:
                rows.append(
                    {
                        "kind": "key",
                        "key": key,
                        "identifier": identifier,
                        "status": "RESOLVER_ONLY",
                        "evidence": (
                            "identifier exists at the resolver, but no title came back "
                            "to compare — not verified"
                        ),
                        "resolved_title": "",
                        "library_title": library_title,
                        "similarity": 0.0,
                        "line": line,
                    }
                )
                unresolved.append(identifier)
                continue
            similarity = round(jaccard(resolved_title, library_title), 3)
            resolved_family = _fold(first_author_family(resolved_rec))
            library_family = _fold(first_author_family(record))
            year_ok = (
                not record.get("year")
                or not resolved_rec.get("year")
                or abs(int(record["year"]) - int(resolved_rec["year"])) <= 1
            )
            author_ok = (
                not resolved_family
                or not library_family
                or resolved_family == library_family
            )
            if similarity >= 0.6 and year_ok and author_ok:
                rows.append(
                    {
                        "kind": "key",
                        "key": key,
                        "identifier": identifier,
                        "status": "OK",
                        "evidence": f"resolved this run; title Jaccard {similarity}",
                        "resolved_title": resolved_title,
                        "library_title": library_title,
                        "similarity": similarity,
                        "line": line,
                    }
                )
                verified_now[key] = (identifier, _iso_now())
            else:
                disagreements = []
                if similarity < 0.6:
                    disagreements.append(f"title Jaccard {similarity}")
                if not year_ok:
                    disagreements.append(
                        f"year library={record.get('year')} registry={resolved_rec.get('year')}"
                    )
                if not author_ok:
                    disagreements.append(
                        f"first author library={library_family!r} registry={resolved_family!r}"
                    )
                rows.append(
                    {
                        "kind": "key",
                        "key": key,
                        "identifier": identifier,
                        "status": "MISMATCH",
                        "evidence": (
                            "identifier resolved but the record disagrees: "
                            + "; ".join(disagreements)
                            + ". The library entry was NOT changed — check which one is right."
                        ),
                        "resolved_title": resolved_title,
                        "library_title": library_title,
                        "similarity": similarity,
                        "line": line,
                    }
                )
                unresolved.append(identifier)

        library_dois = {
            normalise_doi(str((e.get("record") or {}).get("doi") or ""))
            for e in entries.values()
        }
        for item in found["dois"]:
            doi = item["doi"]
            if doi in library_dois:
                continue
            if not online or not budget_left():
                rows.append(
                    {
                        "kind": "doi",
                        "key": "",
                        "identifier": f"doi:{doi}",
                        "status": "UNVERIFIED",
                        "evidence": "bare DOI in the text; not checked this run",
                        "resolved_title": "",
                        "library_title": "",
                        "similarity": 0.0,
                        "line": item["line"],
                    }
                )
                continue
            checks_used += 1
            resolved_rec, code = _resolve_identifier("doi", doi, timeout)
            if code and code != "RESOLVER_ONLY":
                rows.append(
                    {
                        "kind": "doi",
                        "key": "",
                        "identifier": f"doi:{doi}",
                        "status": "DOI_UNRESOLVED",
                        "evidence": (
                            f"bare DOI {doi} in the text did not resolve ({code}). "
                            "A DOI that does not resolve is very often a fabricated citation."
                        ),
                        "resolved_title": "",
                        "library_title": "",
                        "similarity": 0.0,
                        "line": item["line"],
                    }
                )
                unresolved.append(f"doi:{doi}")
            else:
                rows.append(
                    {
                        "kind": "doi",
                        "key": "",
                        "identifier": f"doi:{doi}",
                        "status": "OK",
                        "evidence": "resolved this run",
                        "resolved_title": str(resolved_rec.get("title") or ""),
                        "library_title": "",
                        "similarity": 0.0,
                        "line": item["line"],
                    }
                )

        if online:
            # Link-rot checking is bounded hard: it is the least informative row
            # type and the easiest way to burn the request budget.
            for item in found["urls"][:_URL_CHECK_CAP]:
                if not budget_left():
                    break
                url = item["url"]
                if "doi.org/" in url.lower():
                    continue
                checks_used += 1
                try:
                    _final, _raw, _cs = _fetch_bytes(url, timeout=min(timeout, 20.0))
                except Exception as exc:
                    failure = _source_failure("url", exc)
                    rows.append(
                        {
                            "kind": "url",
                            "key": "",
                            "identifier": url,
                            "status": "URL_DEAD",
                            "evidence": f"{failure['code']}: {failure['error']}",
                            "resolved_title": "",
                            "library_title": "",
                            "similarity": 0.0,
                            "line": item["line"],
                        }
                    )
                    unresolved.append(url)

        for key in entries:
            if key not in cited_keys:
                record = entries[key].get("record") or {}
                rows.append(
                    {
                        "kind": "unused",
                        "key": key,
                        "identifier": normalise_doi(str(record.get("doi") or "")),
                        "status": "KEY_UNUSED",
                        "evidence": "in the library but cited nowhere in this manuscript (not an error)",
                        "resolved_title": "",
                        "library_title": str(record.get("title") or ""),
                        "similarity": 0.0,
                        "line": 0,
                    }
                )

        if verified_now:
            for key, (identifier, stamp) in verified_now.items():
                provenance = entries[key].setdefault("provenance", {})
                provenance["verified_utc"] = stamp
                provenance["verified_identifier"] = identifier
            with suppress(OSError):
                save_library(directory, lib)

        counts: dict[str, int] = {}
        for row in rows:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        hard_fail = (
            counts.get("KEY_MISSING", 0)
            + counts.get("DOI_UNRESOLVED", 0)
            + counts.get("ID_UNRESOLVED", 0)
            + counts.get("URL_DEAD", 0)
            + counts.get("RESOLVER_ONLY", 0)
        )
        soft_fail = (
            counts.get("MISMATCH", 0)
            + counts.get("NO_IDENTIFIER", 0)
            + counts.get("UNVERIFIED", 0)
            + counts.get("OK_CACHED", 0)
        )
        verdict = "FAIL" if hard_fail or (strict and soft_fail) else "PASS"
        rerun = f'cite_check(manuscript="{raw}", resolve=true, strict=true)'
        notes.append(
            "Only rows with status OK were resolved in THIS run. OK_CACHED was verified earlier "
            "and re-checking it is the honest move before submission."
        )
        if verdict == "FAIL":
            notes.append(
                "Do not call the manuscript done. Report every unresolved citation to the owner "
                "— never quietly drop or replace one."
            )
        return json.dumps(
            {
                "manuscript": str(path),
                "library_dir": str(directory),
                "verdict": verdict,
                "resolve": bool(resolve),
                "network_used": online,
                "strict": bool(strict),
                "checked": len(rows),
                "network_checks_used": checks_used,
                "max_checks": cap,
                "counts": counts,
                "rows": rows,
                "unresolved": sorted(set(unresolved)),
                "notes": notes,
                "rerun_command": rerun,
            },
            indent=2,
            default=str,
        )

    # ------------------------------------------------------------- registry
    # tool_timeouts resolves handler._remedy_timeout FIRST, so these hold even
    # before the coordinator adds TOOL_TIMEOUTS entries.
    lit_search._remedy_timeout = 180.0  # type: ignore[attr-defined]
    lit_fetch._remedy_timeout = 300.0  # type: ignore[attr-defined]
    cite_add._remedy_timeout = 60.0  # type: ignore[attr-defined]
    cite_import._remedy_timeout = 60.0  # type: ignore[attr-defined]
    cite_list._remedy_timeout = 30.0  # type: ignore[attr-defined]
    cite_export._remedy_timeout = 60.0  # type: ignore[attr-defined]
    cite_check._remedy_timeout = 300.0  # type: ignore[attr-defined]

    reg = runtime.tool_registry
    reg.register_builtin_handler(
        "lit_search",
        "Search the scholarly literature (arXiv, Crossref, OpenAlex, PubMed, Semantic "
        "Scholar) and return records normalised to one shape. source=auto picks by query "
        "shape (bare DOI -> Crossref, arXiv id -> arXiv, clinical wording -> PubMed, else "
        "OpenAlex+Crossref); source=all queries every one and merges on DOI/PMID/arXiv id. "
        "Missing fields stay empty — nothing is guessed. A source that fails is listed in "
        "sources_failed, never silently dropped. Requires web_tools_enabled=true.",
        lit_search,
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free text, a DOI, or an arXiv/PMID id"},
                "source": {
                    "type": "string",
                    "description": "auto|all|arxiv|crossref|openalex|pubmed|semanticscholar",
                    "default": "auto",
                },
                "max_results": {"type": "integer", "default": 10},
                "year_from": {"type": "integer", "default": 0},
                "year_to": {"type": "integer", "default": 0},
                "open_access_only": {"type": "boolean", "default": False},
                "fields": {
                    "type": "string",
                    "description": "Comma list of record fields to keep (trims the payload)",
                },
                "sort": {"type": "string", "description": "relevance|date|citations", "default": "relevance"},
                "timeout_seconds": {"type": "number", "default": 30},
            },
            "required": ["query"],
        },
    )
    reg.register_builtin_handler(
        "lit_fetch",
        "Fetch one paper: want=metadata|abstract|fulltext|pdf. id accepts 10.x/y, "
        "doi:…, arxiv:2401.00001, pmid:…, pmc:PMC…, openalex:W…, s2:…; or pass url=. "
        "fulltext uses PMC OA XML or the landing page; pdf extracts text with pypdf when "
        "the environment already has it, else a lossy stdlib parse that says so "
        "(extract_method + lossy are always reported). An abstract is never presented as "
        "full text. Requires web_tools_enabled=true.",
        lit_fetch,
        {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "url": {"type": "string"},
                "want": {"type": "string", "description": "metadata|abstract|fulltext|pdf", "default": "abstract"},
                "max_chars": {"type": "integer", "default": 40000},
                "save_path": {"type": "string", "description": "Optional file to write the text to"},
                "timeout_seconds": {"type": "number", "default": 60},
            },
        },
    )
    reg.register_builtin_handler(
        "cite_add",
        "Add one entry to the project's citation library (refs.bib + refs.csl.json + "
        "library.json under .remedy-research/). Pass record_json (a record from "
        "lit_search) or id= to resolve it online. Idempotent by DOI/arXiv id/PMID; keys "
        "are stable and human (smith2021attention).",
        cite_add,
        {
            "type": "object",
            "properties": {
                "record_json": {"type": "string", "description": "One lit_search record as JSON"},
                "id": {"type": "string", "description": "DOI / arXiv / PMID to resolve online"},
                "key": {"type": "string", "description": "Force a citation key"},
                "library": {"type": "string", "description": "Library directory (default .remedy-research)"},
                "tags": {"type": "string", "description": "Comma list"},
                "note": {"type": "string"},
            },
        },
    )
    reg.register_builtin_handler(
        "cite_import",
        "Import an existing refs.bib / CSL-JSON / .ris / .nbib into the library so "
        "cite_check works on a real project immediately. format=auto|bibtex|csljson|ris|"
        "nbib, merge=keep|overwrite|rename on key collision. Imported entries are "
        "unverified until cite_check resolves them.",
        cite_import,
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "library": {"type": "string"},
                "format": {"type": "string", "default": "auto"},
                "merge": {"type": "string", "default": "keep"},
            },
            "required": ["path"],
        },
    )
    reg.register_builtin_handler(
        "cite_list",
        "List the citation library. format=summary|bibtex|csljson|keys; query matches "
        "key/title/author/year; tags filters. verified_utc shows which entries cite_check "
        "has ever resolved.",
        cite_list,
        {
            "type": "object",
            "properties": {
                "library": {"type": "string"},
                "query": {"type": "string"},
                "tags": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
                "format": {"type": "string", "default": "summary"},
            },
        },
    )
    reg.register_builtin_handler(
        "cite_export",
        "Write a filtered library beside a manuscript (bibtex|csljson|ris). "
        "only_cited_in=<manuscript> exports exactly the keys that manuscript cites — the "
        "usual way to ship a minimal refs.bib.",
        cite_export,
        {
            "type": "object",
            "properties": {
                "library": {"type": "string"},
                "format": {"type": "string", "default": "bibtex"},
                "out_path": {"type": "string"},
                "keys": {"type": "string", "description": "Comma list of keys to export"},
                "only_cited_in": {"type": "string", "description": "Manuscript path"},
            },
        },
    )
    reg.register_builtin_handler(
        "cite_check",
        "THE anti-fabrication guard. Reads a .tex/.md/.qmd manuscript, extracts every "
        "\\cite/\\citep/\\autocite/[@key]/bare DOI/arXiv id/PMID/URL, and reports per item: "
        "KEY_MISSING, NO_IDENTIFIER, DOI_UNRESOLVED, MISMATCH (registry title/author/year "
        "disagrees with the library), URL_DEAD, KEY_UNUSED, or OK. resolve=true checks "
        "against the live registries; resolve=false is a structural check and marks every "
        "network row UNVERIFIED. This is the only tool allowed to call a citation verified, "
        "and only for what it resolved in this call. No manuscript is done until this "
        "returns PASS with resolve=true.",
        cite_check,
        {
            "type": "object",
            "properties": {
                "manuscript": {"type": "string"},
                "library": {"type": "string"},
                "resolve": {"type": "boolean", "default": True},
                "strict": {"type": "boolean", "default": False},
                "max_checks": {"type": "integer", "default": 60},
                "timeout_seconds": {"type": "number", "default": 60},
            },
            "required": ["manuscript"],
        },
    )
