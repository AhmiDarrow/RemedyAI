"""Literature + citation tools: five fake sources, a tmp library, no sockets.

Every network call in ``agent_research_tools`` funnels through ``_fetch_json`` /
``_fetch_bytes``; both are monkeypatched here and an unrouted URL raises, so a
test that would have opened a socket fails loudly instead of going online.
"""

from __future__ import annotations

import asyncio
import json
import zlib
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from remedy.core import agent_research_tools as rs
from remedy.skills.tool_registry import ToolRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "research"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _fixture_json(name: str) -> Any:
    return json.loads(_fixture(name))


# ---------------------------------------------------------------- fake runtime


class _Rt:
    """Minimal runtime: a real ToolRegistry, a tmp project, a real write jail."""

    def __init__(self, root: Path, *, bound: bool = True) -> None:
        self.tool_registry = ToolRegistry()
        self._root = root
        self._bound = bound

    def effective_project_path(self) -> Path:
        return self._root

    def project_path_is_unset(self) -> bool:
        return not self._bound

    def resolve_tool_path(self, path: str, *, for_write: bool = False) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = self._root / p
        if for_write:
            try:
                p.resolve().relative_to(self._root.resolve())
            except ValueError as exc:
                raise PermissionError(f"{p} is outside the write roots") from exc
        return p

    def allowed_roots(self) -> list[Path]:
        return [self._root]

    def write_roots(self) -> list[Path]:
        return [self._root]

    def access_scope(self) -> str:
        return "project"


class _Net:
    """Substring router for the two fetch seams. Unrouted URLs raise."""

    def __init__(self) -> None:
        self.json_routes: list[tuple[str, Any]] = []
        self.byte_routes: list[tuple[str, Any]] = []
        self.json_calls: list[str] = []
        self.byte_calls: list[str] = []

    def json(self, needle: str, value: Any) -> _Net:
        self.json_routes.append((needle, value))
        return self

    def raw(self, needle: str, value: Any) -> _Net:
        self.byte_routes.append((needle, value))
        return self

    @staticmethod
    def _pick(url: str, routes: list[tuple[str, Any]]) -> Any:
        for needle, value in routes:
            if needle in url:
                if isinstance(value, BaseException):
                    raise value
                return value
        raise AssertionError(f"unrouted URL (a real socket would have opened): {url}")

    def fetch_json(self, url: str, *, timeout: float = 30.0) -> Any:
        self.json_calls.append(url)
        return self._pick(url, self.json_routes)

    def fetch_bytes(self, url: str, *, timeout: float = 30.0) -> tuple[str, bytes, str]:
        self.byte_calls.append(url)
        value = self._pick(url, self.byte_routes)
        data = value.encode("utf-8") if isinstance(value, str) else value
        return (url, data, "utf-8")


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("REMEDY_RESEARCH_MAILTO", raising=False)
    root = tmp_path / "paper"
    root.mkdir()
    return root


@pytest.fixture
def net(monkeypatch) -> _Net:
    n = _Net()
    monkeypatch.setattr(rs, "_fetch_json", n.fetch_json)
    monkeypatch.setattr(rs, "_fetch_bytes", n.fetch_bytes)
    monkeypatch.setattr(rs, "web_tools_enabled", lambda runtime=None: True)
    # config lookups must not read the owner's real ~/.remedy during tests
    monkeypatch.setattr(rs, "_polite_mailto", lambda: "")
    return n


@pytest.fixture
def rt(project: Path, net: _Net) -> _Rt:
    r = _Rt(project)
    rs.register_research_tools(r)
    return r


def run(rt: _Rt, tool: str, **kwargs: Any) -> str:
    return asyncio.run(rt.tool_registry.execute(tool, **kwargs))


def run_json(rt: _Rt, tool: str, **kwargs: Any) -> Any:
    out = run(rt, tool, **kwargs)
    assert not out.startswith("Error ["), out
    return json.loads(out)


def _http(code: int, url: str = "https://example.org") -> HTTPError:
    return HTTPError(url, code, "Not Found", {}, None)  # type: ignore[arg-type]


# ------------------------------------------------------------------ registration


def test_registers_the_seven_contract_tools(rt: _Rt) -> None:
    names = {
        "lit_search",
        "lit_fetch",
        "cite_add",
        "cite_import",
        "cite_list",
        "cite_export",
        "cite_check",
    }
    registered = {t.name for t in rt.tool_registry.tools}
    assert names <= registered


def test_long_tools_carry_their_own_timeout(rt: _Rt) -> None:
    # tool_timeouts resolves handler._remedy_timeout before its table, so these
    # must survive without the coordinator's entries.
    handlers = rt.tool_registry._handlers  # noqa: SLF001 - contract check
    assert handlers["lit_search"]._remedy_timeout == 180.0
    assert handlers["lit_fetch"]._remedy_timeout == 300.0
    assert handlers["cite_check"]._remedy_timeout == 300.0


# ---------------------------------------------------------------- pure helpers


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://doi.org/10.1038/Nature14539", ("doi", "10.1038/nature14539")),
        ("doi:10.1234/abc", ("doi", "10.1234/abc")),
        ("arXiv:2401.00001v2", ("arxiv", "2401.00001")),
        ("2401.00001", ("arxiv", "2401.00001")),
        ("pmid:26017442", ("pmid", "26017442")),
        ("PMC1234567", ("pmc", "PMC1234567")),
        ("openalex:W2963403868", ("openalex", "W2963403868")),
        ("s2:df2b0e26", ("s2", "df2b0e26")),
        ("sparse attention transformers", ("query", "sparse attention transformers")),
    ],
)
def test_parse_identifier(raw: str, expected: tuple[str, str]) -> None:
    assert rs.parse_identifier(raw) == expected


def test_auto_source_order_follows_query_shape() -> None:
    assert rs.auto_sources("10.1038/nature14539")[0] == "crossref"
    assert rs.auto_sources("arXiv:2401.00001")[0] == "arxiv"
    assert rs.auto_sources("aspirin in patients with acute stroke")[0] == "pubmed"
    assert rs.auto_sources("sparse attention kernels")[0] == "openalex"


def test_keys_are_stable_human_and_deduped() -> None:
    record = rs.blank_record("crossref")
    record.update({"title": "Deep Learning", "year": 2015, "authors": [rs._author("LeCun", "Yann")]})
    assert rs.make_key(record, set()) == "lecun2015deep"
    assert rs.make_key(record, {"lecun2015deep"}) == "lecun2015deepa"


def test_title_similarity_is_folded_not_exact() -> None:
    assert rs.jaccard("Deep learning", "deep, LEARNING!") == 1.0
    assert rs.jaccard("Deep learning", "Shallow gardening") == 0.0


def test_bibtex_round_trip_preserves_identifiers() -> None:
    record = rs.blank_record("crossref")
    record.update(
        {
            "title": "Cost & benefit of 50% coverage",
            "year": 2021,
            "doi": "10.1234/abc",
            "venue": "Nature",
            "authors": [rs._author("Smith", "Jane")],
            "type": "journal-article",
        }
    )
    text = rs.render_bibtex({"smith2021cost": {"record": record, "tags": [], "note": ""}})
    parsed = rs.parse_bibtex(text)
    assert len(parsed) == 1
    key, back = parsed[0]
    assert key == "smith2021cost"
    assert back["doi"] == "10.1234/abc"
    assert back["title"] == "Cost & benefit of 50% coverage"
    assert back["year"] == 2021
    assert back["authors"][0]["family"] == "Smith"


def test_module_never_imports_a_sidecar_excluded_package() -> None:
    source = Path(rs.__file__).read_text(encoding="utf-8")
    banned = ("pandas", "numpy", "scipy", "sklearn", "matplotlib", "torch", "transformers")
    for name in banned:
        assert f"import {name}" not in source
        assert f"from {name}" not in source


# --------------------------------------------------------------------- search


def test_lit_search_requires_a_query(rt: _Rt) -> None:
    assert "MISSING_QUERY" in run(rt, "lit_search", query="")


def test_lit_search_rejects_an_unknown_source(rt: _Rt) -> None:
    assert "BAD_SOURCE" in run(rt, "lit_search", query="x", source="scihub")


def test_lit_search_is_gated_on_the_existing_web_opt_in(rt: _Rt, monkeypatch) -> None:
    monkeypatch.setattr(rs, "web_tools_enabled", lambda runtime=None: False)
    out = run(rt, "lit_search", query="deep learning")
    assert "WEB_DISABLED" in out
    assert "web_tools_enabled=true" in out


def test_lit_search_arxiv_normalises_the_record(rt: _Rt, net: _Net) -> None:
    net.raw("export.arxiv.org", _fixture("arxiv_search.xml"))
    payload = run_json(rt, "lit_search", query="sparse attention", source="arxiv")
    assert payload["count"] == 2
    first = payload["records"][0]
    assert first["source"] == "arxiv"
    assert first["arxiv_id"] == "1706.03762"
    assert first["title"] == "Attention Is All You Need"
    assert first["year"] == 2017 and first["month"] == 6
    assert first["authors"][0] == {"family": "Vaswani", "given": "Ashish", "orcid": ""}
    assert first["pdf_url"].endswith("1706.03762v5")
    assert first["doi"] == ""  # absent, not guessed
    assert first["retrieved_utc"].endswith("Z")


def test_lit_search_crossref_normalises_and_strips_jats(rt: _Rt, net: _Net) -> None:
    net.json("api.crossref.org/works?", _fixture_json("crossref_search.json"))
    payload = run_json(rt, "lit_search", query="deep learning", source="crossref")
    rec = payload["records"][0]
    assert rec["doi"] == "10.1038/nature14539"
    assert rec["venue"] == "Nature"
    assert rec["year"] == 2015 and rec["month"] == 5
    assert rec["pages"] == "436-444"
    assert "<jats:" not in rec["abstract"]
    assert rec["abstract"].startswith("Abstract Deep learning")
    assert rec["cited_by_count"] == 60123
    assert rec["pdf_url"] == "https://example.org/nature14539.pdf"


def test_lit_search_openalex_rebuilds_the_inverted_abstract(rt: _Rt, net: _Net) -> None:
    net.json("api.openalex.org/works", _fixture_json("openalex_search.json"))
    payload = run_json(rt, "lit_search", query="deep learning", source="openalex")
    rec = payload["records"][0]
    assert rec["abstract"] == "Deep learning allows models"
    assert rec["openalex_id"] == "W2963403868"
    assert rec["pmid"] == "26017442"
    assert rec["pmcid"] == "PMC1234567"
    assert rec["oa_status"] == "green"
    assert rec["authors"][0]["orcid"].endswith("0000-0002-0000-0000")


def test_lit_search_pubmed_uses_esearch_then_esummary(rt: _Rt, net: _Net) -> None:
    net.json("esearch.fcgi", _fixture_json("pubmed_esearch.json"))
    net.json("esummary.fcgi", _fixture_json("pubmed_esummary.json"))
    payload = run_json(rt, "lit_search", query="deep learning in patients", source="pubmed")
    assert len(net.json_calls) == 2  # bounded: two requests, not one per record
    rec = payload["records"][0]
    assert rec["pmid"] == "26017442"
    assert rec["doi"] == "10.1038/nature14539"
    assert rec["authors"][0] == {"family": "LeCun", "given": "Y", "orcid": ""}
    assert rec["year"] == 2015 and rec["month"] == 5
    assert rec["abstract"] == ""  # esummary carries none — and we say so
    assert any("esummary carries no abstract" in n for n in payload["notes"])


def test_lit_search_semanticscholar_normalises(rt: _Rt, net: _Net) -> None:
    net.json("api.semanticscholar.org", _fixture_json("s2_search.json"))
    payload = run_json(rt, "lit_search", query="bert", source="semanticscholar")
    rec = payload["records"][0]
    assert rec["arxiv_id"] == "1810.04805"
    assert rec["doi"] == "10.18653/v1/n19-1423"
    assert rec["pdf_url"].endswith("N19-1423.pdf")
    assert any("anonymous tier" in n for n in payload["notes"])


def test_semanticscholar_backs_off_once_on_429(rt: _Rt, net: _Net, monkeypatch) -> None:
    monkeypatch.setattr(rs, "_S2_BACKOFF_S", 0.0)
    seen: list[str] = []

    def flaky(url: str, *, timeout: float = 30.0) -> Any:
        seen.append(url)
        if len(seen) == 1:
            raise _http(429, url)
        return _fixture_json("s2_search.json")

    monkeypatch.setattr(rs, "_fetch_json", flaky)
    payload = run_json(rt, "lit_search", query="bert", source="semanticscholar")
    assert len(seen) == 2  # exactly one retry, never a loop
    assert payload["count"] == 1
    assert any("429" in n for n in payload["notes"])


def test_lit_search_all_merges_across_sources_on_doi(rt: _Rt, net: _Net) -> None:
    net.raw("export.arxiv.org", _fixture("arxiv_search.xml"))
    net.json("api.crossref.org/works?", _fixture_json("crossref_search.json"))
    net.json("api.openalex.org/works", _fixture_json("openalex_search.json"))
    net.json("esearch.fcgi", _fixture_json("pubmed_esearch.json"))
    net.json("esummary.fcgi", _fixture_json("pubmed_esummary.json"))
    net.json("api.semanticscholar.org", _fixture_json("s2_search.json"))
    payload = run_json(rt, "lit_search", query="deep learning", source="all", max_results=20)
    assert payload["sources_tried"] == list(rs.SOURCES)
    merged = [r for r in payload["records"] if r["doi"] == "10.1038/nature14539"]
    assert len(merged) == 1, "the same DOI from three sources must collapse to one record"
    assert set(merged[0]["sources"]) == {"crossref", "openalex", "pubmed"}
    # Crossref had the abstract, OpenAlex the OpenAlex id: an exact-identifier
    # match is the only case where fields are allowed to cross over.
    assert merged[0]["openalex_id"] == "W2963403868"
    assert merged[0]["abstract"].startswith("Abstract Deep learning")


def test_lit_search_names_the_source_that_failed(rt: _Rt, net: _Net) -> None:
    net.json("api.openalex.org/works", URLError("dns went away"))
    net.json("api.crossref.org/works?", _fixture_json("crossref_search.json"))
    payload = run_json(rt, "lit_search", query="deep learning", source="auto")
    assert payload["count"] == 1
    assert payload["sources_failed"][0]["source"] == "openalex"
    assert payload["sources_failed"][0]["code"] == "NETWORK_ERROR"
    assert any("PARTIAL RESULT" in n for n in payload["notes"])


def test_lit_search_total_failure_is_not_an_empty_literature(rt: _Rt, net: _Net) -> None:
    net.json("api.openalex.org/works", _http(503))
    net.json("api.crossref.org/works?", _http(503))
    payload = run_json(rt, "lit_search", query="deep learning")
    assert payload["count"] == 0
    assert {f["code"] for f in payload["sources_failed"]} == {"HTTP_503"}
    assert any("not evidence the literature is empty" in n for n in payload["notes"])


def test_lit_search_fields_trims_the_payload(rt: _Rt, net: _Net) -> None:
    net.json("api.crossref.org/works?", _fixture_json("crossref_search.json"))
    payload = run_json(
        rt, "lit_search", query="deep learning", source="crossref", fields="title,doi,year"
    )
    rec = payload["records"][0]
    assert set(rec) == {"source", "sources", "id", "retrieved_utc", "title", "doi", "year"}


# ---------------------------------------------------------------------- fetch


def test_lit_fetch_needs_an_id_or_url(rt: _Rt) -> None:
    assert "MISSING_ID" in run(rt, "lit_fetch")


def test_lit_fetch_is_gated_on_web_tools(rt: _Rt, monkeypatch) -> None:
    monkeypatch.setattr(rs, "web_tools_enabled", lambda runtime=None: False)
    assert "WEB_DISABLED" in run(rt, "lit_fetch", id="10.1038/nature14539")


def test_lit_fetch_abstract_from_crossref(rt: _Rt, net: _Net) -> None:
    # DOI lookups hit /works/{doi} and unwrap payload["message"].
    net.json(
        "api.crossref.org/works/",
        {"message": _fixture_json("crossref_work.json")["message"]},
    )
    payload = run_json(rt, "lit_fetch", id="doi:10.1038/nature14539", want="abstract")
    assert payload["record"]["doi"] == "10.1038/nature14539"
    assert payload["extract_method"]
    assert payload["want"] == "abstract"


def test_lit_fetch_pdf_reports_its_extractor_and_lossiness(rt: _Rt, net: _Net) -> None:
    body = b"BT /F1 12 Tf 72 720 Td (Hello lossy world) Tj ET"
    stream = zlib.compress(body)
    pdf = (
        b"%PDF-1.4\n1 0 obj\n<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream\nendobj\n%%EOF\n"
    )
    net.raw("arxiv.org/pdf", pdf)
    net.raw("export.arxiv.org", _fixture("arxiv_search.xml"))
    payload = run_json(rt, "lit_fetch", id="arxiv:1706.03762", want="pdf")
    assert "Hello lossy world" in payload["text"]
    assert payload["extract_method"] not in ("", "none")
    # Whichever path ran, the payload has to say which one and how lossy it is.
    assert isinstance(payload["lossy"], bool)


def test_pdf_stdlib_fallback_is_flagged_lossy() -> None:
    stream = zlib.compress(b"BT (fallback text) Tj ET")
    pdf = b"%PDF-1.4\nstream\n" + stream + b"\nendstream\n%%EOF\n"
    assert "fallback text" in rs.pdf_text_stdlib(pdf)
    text, method, lossy, warning = rs.pdf_to_text(b"not a pdf at all")
    assert text == "" and method == "none" and lossy is True
    assert "not a claim the PDF is empty" in warning


def test_lit_fetch_never_passes_an_abstract_off_as_full_text(rt: _Rt, net: _Net) -> None:
    net.json("api.crossref.org/works/", {"message": _fixture_json("crossref_search.json")["message"]["items"][0]})
    net.raw("nature.com", URLError("paywall"))
    net.raw("dx.doi.org", URLError("paywall"))
    payload = run_json(rt, "lit_fetch", id="doi:10.1038/nature14539", want="fulltext")
    assert "ABSTRACT ONLY" in payload["extract_method"]
    assert any("Do not treat it as the paper" in n for n in payload["notes"])


def test_lit_fetch_save_path_is_write_jailed(rt: _Rt, net: _Net, tmp_path: Path) -> None:
    net.json("api.crossref.org/works/", {"message": _fixture_json("crossref_work.json")["message"]})
    out = run(
        rt,
        "lit_fetch",
        id="doi:10.1038/nature14539",
        want="metadata",
        save_path=str(tmp_path / "outside" / "leak.txt"),
    )
    assert "WRITE_JAIL" in out
    assert not (tmp_path / "outside" / "leak.txt").exists()


# -------------------------------------------------------------------- library


def _lecun_record() -> dict[str, Any]:
    record = rs.blank_record("crossref")
    record.update(
        {
            "doi": "10.1038/nature14539",
            "title": "Deep learning",
            "year": 2015,
            "venue": "Nature",
            "volume": "521",
            "pages": "436-444",
            "type": "journal-article",
            "authors": [
                rs._author("LeCun", "Yann"),
                rs._author("Bengio", "Yoshua"),
                rs._author("Hinton", "Geoffrey"),
            ],
        }
    )
    return record


def test_cite_add_needs_a_record_or_an_id(rt: _Rt) -> None:
    assert "MISSING_INPUT" in run(rt, "cite_add")


def test_cite_add_by_id_needs_the_web_gate(rt: _Rt, monkeypatch) -> None:
    monkeypatch.setattr(rs, "web_tools_enabled", lambda runtime=None: False)
    assert "WEB_DISABLED" in run(rt, "cite_add", id="10.1038/nature14539")


def test_cite_add_round_trip_and_idempotency(rt: _Rt, project: Path) -> None:
    payload = run_json(rt, "cite_add", record_json=json.dumps(_lecun_record()), tags="ml,review")
    assert payload["key"] == "lecun2015deep"
    assert payload["added"] is True and payload["updated"] is False

    library = project / ".remedy-research"
    assert Path(payload["library_dir"]) == library
    bib = (library / "refs.bib").read_text(encoding="utf-8")
    assert "@article{lecun2015deep," in bib
    assert "doi = {10.1038/nature14539}" in bib
    csl = json.loads((library / "refs.csl.json").read_text(encoding="utf-8"))
    assert csl[0]["id"] == "lecun2015deep"
    assert csl[0]["issued"]["date-parts"] == [[2015]]
    index = json.loads((library / "library.json").read_text(encoding="utf-8"))
    assert index["entries"]["lecun2015deep"]["provenance"]["verified_utc"] == ""

    # Same DOI arriving from another source updates, never duplicates.
    again = _lecun_record()
    again["source"] = "openalex"
    again["openalex_id"] = "W2963403868"
    second = run_json(rt, "cite_add", record_json=json.dumps(again))
    assert second["key"] == "lecun2015deep"
    assert second["added"] is False and second["updated"] is True
    index = json.loads((library / "library.json").read_text(encoding="utf-8"))
    assert len(index["entries"]) == 1
    assert index["entries"]["lecun2015deep"]["record"]["openalex_id"] == "W2963403868"


def test_cite_add_rejects_junk_record_json(rt: _Rt) -> None:
    assert "BAD_RECORD" in run(rt, "cite_add", record_json="{not json")


def test_cite_add_by_id_resolves_online(rt: _Rt, net: _Net) -> None:
    net.json("api.crossref.org/works/", _fixture_json("crossref_work.json"))
    payload = run_json(rt, "cite_add", id="10.1038/nature14539")
    assert payload["key"] == "lecun2015deep"
    assert payload["entry"]["record"]["title"] == "Deep learning"


def test_cite_import_bibtex(rt: _Rt, project: Path) -> None:
    src = project / "existing.bib"
    src.write_text(
        "@article{smith2021cost,\n"
        "  author = {Smith, Jane and Doe, John},\n"
        "  title = {A study of {DNA} repair},\n"
        "  journal = {Cell},\n"
        "  year = {2021},\n"
        "  doi = {10.1016/j.cell.2021.01.001}\n"
        "}\n",
        encoding="utf-8",
    )
    payload = run_json(rt, "cite_import", path="existing.bib")
    assert payload["format"] == "bibtex"
    assert payload["imported"] == 1
    entry = json.loads((project / ".remedy-research" / "library.json").read_text(encoding="utf-8"))
    record = entry["entries"]["smith2021cost"]["record"]
    assert record["doi"] == "10.1016/j.cell.2021.01.001"
    assert record["venue"] == "Cell"
    assert [a["family"] for a in record["authors"]] == ["Smith", "Doe"]
    # Re-import with merge=keep leaves the existing entry alone.
    again = run_json(rt, "cite_import", path="existing.bib", merge="keep")
    assert again["imported"] == 0 and again["skipped"] == 1


def test_cite_import_ris(rt: _Rt, project: Path) -> None:
    src = project / "export.ris"
    src.write_text(
        "TY  - JOUR\n"
        "AU  - LeCun, Yann\n"
        "TI  - Deep learning\n"
        "JO  - Nature\n"
        "PY  - 2015\n"
        "DO  - 10.1038/nature14539\n"
        "ER  - \n",
        encoding="utf-8",
    )
    payload = run_json(rt, "cite_import", path="export.ris")
    assert payload["format"] == "ris" and payload["imported"] == 1
    listed = run_json(rt, "cite_list", format="keys")
    assert listed["keys"] == ["lecun2015deep"]


def test_cite_import_reports_an_unreadable_path(rt: _Rt) -> None:
    assert "NOT_FOUND" in run(rt, "cite_import", path="nope.bib")


def test_cite_list_formats(rt: _Rt) -> None:
    run(rt, "cite_add", record_json=json.dumps(_lecun_record()), tags="ml")
    summary = run_json(rt, "cite_list")
    assert summary["entries"][0]["first_author"] == "LeCun"
    assert summary["entries"][0]["verified_utc"] == ""
    assert run_json(rt, "cite_list", query="bengio")["matched"] == 1
    assert run_json(rt, "cite_list", query="nothing here")["matched"] == 0
    assert run_json(rt, "cite_list", tags="ml")["matched"] == 1
    assert "@article{lecun2015deep," in run(rt, "cite_list", format="bibtex")


def test_cite_export_only_cited_in(rt: _Rt, project: Path) -> None:
    run(rt, "cite_add", record_json=json.dumps(_lecun_record()))
    other = rs.blank_record("crossref")
    other.update({"doi": "10.1016/j.cell.2021.01.001", "title": "Unrelated work",
                  "year": 2021, "authors": [rs._author("Smith", "Jane")]})
    run(rt, "cite_add", record_json=json.dumps(other))
    (project / "paper.md").write_text(
        "We build on [@lecun2015deep] throughout.\n", encoding="utf-8"
    )
    payload = run_json(
        rt, "cite_export", only_cited_in="paper.md", out_path="build/refs.bib"
    )
    assert payload["keys"] == ["lecun2015deep"]
    body = (project / "build" / "refs.bib").read_text(encoding="utf-8")
    assert "lecun2015deep" in body and "smith2021unrelated" not in body


def test_cite_export_refuses_to_write_outside_the_jail(rt: _Rt, tmp_path: Path) -> None:
    run(rt, "cite_add", record_json=json.dumps(_lecun_record()))
    out = run(rt, "cite_export", out_path=str(tmp_path / "elsewhere" / "refs.bib"))
    assert "WRITE_JAIL" in out


def test_library_lands_under_remedy_home_when_no_project_is_bound(
    tmp_path: Path, net: _Net
) -> None:
    from remedy.home import default_home

    root = tmp_path / "loose"
    root.mkdir()
    runtime = _Rt(root, bound=False)
    rs.register_research_tools(runtime)
    payload = run_json(runtime, "cite_add", record_json=json.dumps(_lecun_record()))
    library = Path(payload["library_dir"])
    assert library.is_relative_to(default_home() / "research")
    assert not (root / ".remedy-research").exists()


# ------------------------------------------------------------------ cite_check


MANUSCRIPT = r"""
\documentclass{article}
\begin{document}
% a comment citing \cite{commented2020out} must be ignored
Representation learning changed everything \cite{lecun2015deep}.
An assertion with no source in the library \cite{ghost2020nothing}.
A fabricated reference: 10.9999/fabricated.123 supposedly proves it.
\end{document}
"""


@pytest.fixture
def checked(rt: _Rt, project: Path, net: _Net):
    run(rt, "cite_add", record_json=json.dumps(_lecun_record()))
    (project / "paper.tex").write_text(MANUSCRIPT, encoding="utf-8")
    net.json("nature14539", _fixture_json("crossref_work.json"))
    net.json("fabricated", _http(404))
    net.raw("doi.org/10.9999", _http(404))
    return rt


def test_cite_check_catches_the_three_failure_modes(checked: _Rt, project: Path) -> None:
    payload = run_json(checked, "cite_check", manuscript="paper.tex")
    assert payload["verdict"] == "FAIL"
    assert payload["network_used"] is True
    by_key = {(r["kind"], r["key"] or r["identifier"]): r for r in payload["rows"]}

    good = by_key[("key", "lecun2015deep")]
    assert good["status"] == "OK"
    assert good["similarity"] == 1.0
    assert "resolved this run" in good["evidence"]

    missing = by_key[("key", "ghost2020nothing")]
    assert missing["status"] == "KEY_MISSING"
    assert missing["line"] == 6

    fabricated = by_key[("doi", "doi:10.9999/fabricated.123")]
    assert fabricated["status"] == "DOI_UNRESOLVED"
    assert "fabricated" in fabricated["evidence"]

    # A commented-out \cite is not a citation.
    assert not any(r["key"] == "commented2020out" for r in payload["rows"])
    assert set(payload["unresolved"]) == {"ghost2020nothing", "doi:10.9999/fabricated.123"}
    assert payload["rerun_command"].startswith("cite_check(")


def test_cite_check_writes_back_verified_utc_only_for_what_it_resolved(
    checked: _Rt, project: Path
) -> None:
    run_json(checked, "cite_check", manuscript="paper.tex")
    index = json.loads(
        (project / ".remedy-research" / "library.json").read_text(encoding="utf-8")
    )
    provenance = index["entries"]["lecun2015deep"]["provenance"]
    assert provenance["verified_utc"].endswith("Z")
    assert provenance["verified_identifier"] == "doi:10.1038/nature14539"


def test_cite_check_relabels_an_earlier_verification_as_cached(
    checked: _Rt, project: Path, monkeypatch
) -> None:
    run_json(checked, "cite_check", manuscript="paper.tex")
    monkeypatch.setattr(rs, "web_tools_enabled", lambda runtime=None: False)
    payload = run_json(checked, "cite_check", manuscript="paper.tex")
    row = next(r for r in payload["rows"] if r["key"] == "lecun2015deep")
    assert row["status"] == "OK_CACHED"
    assert "NOT re-checked" in row["evidence"]


def test_cite_check_reports_a_registry_mismatch_without_editing_the_entry(
    rt: _Rt, project: Path, net: _Net
) -> None:
    wrong = _lecun_record()
    wrong["title"] = "A completely different paper about gardening"
    wrong["year"] = 1998
    run(rt, "cite_add", record_json=json.dumps(wrong))
    (project / "paper.tex").write_text(r"Text \cite{lecun1998completely}.", encoding="utf-8")
    net.json("nature14539", _fixture_json("crossref_work.json"))
    payload = run_json(rt, "cite_check", manuscript="paper.tex")
    row = next(r for r in payload["rows"] if r["kind"] == "key")
    assert row["status"] == "MISMATCH"
    assert row["resolved_title"] == "Deep learning"
    assert row["library_title"].startswith("A completely different")
    assert "NOT changed" in row["evidence"]
    index = json.loads((project / ".remedy-research" / "library.json").read_text(encoding="utf-8"))
    stored = index["entries"]["lecun1998completely"]["record"]
    assert stored["title"] == "A completely different paper about gardening"
    assert index["entries"]["lecun1998completely"]["provenance"]["verified_utc"] == ""


def test_cite_check_flags_an_entry_with_no_identifier(rt: _Rt, project: Path) -> None:
    naked = rs.blank_record("supplied")
    naked.update({"title": "Something someone said once", "year": 2019,
                  "authors": [rs._author("Nobody", "N")]})
    run(rt, "cite_add", record_json=json.dumps(naked))
    (project / "paper.tex").write_text(r"As shown \cite{nobody2019something}.", encoding="utf-8")
    payload = run_json(rt, "cite_check", manuscript="paper.tex")
    row = next(r for r in payload["rows"] if r["kind"] == "key")
    assert row["status"] == "NO_IDENTIFIER"
    assert "UNVERIFIABLE" in row["evidence"]
    assert payload["verdict"] == "PASS"
    strict = run_json(rt, "cite_check", manuscript="paper.tex", strict=True)
    assert strict["verdict"] == "FAIL"


def test_cite_check_offline_touches_no_network(rt: _Rt, project: Path, net: _Net) -> None:
    run(rt, "cite_add", record_json=json.dumps(_lecun_record()))
    (project / "paper.md").write_text("Grounded in [@lecun2015deep].\n", encoding="utf-8")
    payload = run_json(rt, "cite_check", manuscript="paper.md", resolve=False)
    assert net.json_calls == [] and net.byte_calls == []
    row = next(r for r in payload["rows"] if r["kind"] == "key")
    assert row["status"] == "UNVERIFIED"
    assert payload["verdict"] == "PASS"
    assert payload["network_used"] is False
    assert any("structural check only" in n for n in payload["notes"])
    assert "resolve=true" in payload["rerun_command"]


def test_cite_check_says_so_when_the_web_gate_is_off(
    rt: _Rt, project: Path, monkeypatch
) -> None:
    run(rt, "cite_add", record_json=json.dumps(_lecun_record()))
    (project / "paper.md").write_text("Grounded in [@lecun2015deep].\n", encoding="utf-8")
    monkeypatch.setattr(rs, "web_tools_enabled", lambda runtime=None: False)
    payload = run_json(rt, "cite_check", manuscript="paper.md", resolve=True)
    assert payload["network_used"] is False
    assert any("web tools are OFF" in n for n in payload["notes"])


def test_cite_check_lists_uncited_library_entries_without_failing(
    rt: _Rt, project: Path
) -> None:
    run(rt, "cite_add", record_json=json.dumps(_lecun_record()))
    (project / "paper.md").write_text("No citations at all here.\n", encoding="utf-8")
    payload = run_json(rt, "cite_check", manuscript="paper.md", resolve=False)
    row = next(r for r in payload["rows"] if r["kind"] == "unused")
    assert row["status"] == "KEY_UNUSED"
    assert "not an error" in row["evidence"]
    assert payload["verdict"] == "PASS"


def test_cite_check_flags_a_dead_url(rt: _Rt, project: Path, net: _Net) -> None:
    (project / "paper.md").write_text(
        "Data from https://example.org/gone/dataset.csv underpins this.\n", encoding="utf-8"
    )
    net.raw("example.org/gone", _http(404))
    payload = run_json(rt, "cite_check", manuscript="paper.md")
    row = next(r for r in payload["rows"] if r["kind"] == "url")
    assert row["status"] == "URL_DEAD"
    assert payload["verdict"] == "FAIL"


def test_cite_check_needs_a_manuscript(rt: _Rt) -> None:
    assert "MISSING_MANUSCRIPT" in run(rt, "cite_check")
    assert "NOT_FOUND" in run(rt, "cite_check", manuscript="ghost.tex")


def test_extract_citations_covers_the_syntaxes_it_claims() -> None:
    tex = (
        r"\citep{a} \citet{b} \autocite[see][12]{c} \parencite{d} \nocite{e} "
        r"\citeauthor{f} \cite{g,h}"
    )
    found = rs.extract_citations(tex, suffix=".tex")
    assert {row["key"] for row in found["keys"]} == set("abcdefgh")
    md = "Both [@one; @two] and -@three and @four.\n"
    found_md = rs.extract_citations(md, suffix=".qmd")
    assert {row["key"] for row in found_md["keys"]} == {"one", "two", "three", "four"}
    # % is only a comment in TeX — a percent-encoded URL must survive elsewhere.
    kept = rs.extract_citations("see https://ex.org/a%20b/10.1/x", suffix=".md")
    assert kept["urls"][0]["url"] == "https://ex.org/a%20b/10.1/x"


def test_registered_in_workspace_tools() -> None:
    import inspect

    from remedy.core import agent_workspace_tools

    assert "register_research_tools" in inspect.getsource(agent_workspace_tools)


def test_timeouts_resolve(rt: _Rt) -> None:
    from remedy.core.tool_timeouts import tool_timeout_for

    reg = rt.tool_registry
    assert tool_timeout_for("lit_search", reg) == 180.0
    assert tool_timeout_for("lit_fetch", reg) == 300.0
    assert tool_timeout_for("cite_add", reg) == 60.0
    assert tool_timeout_for("cite_list", reg) == 30.0
    assert tool_timeout_for("cite_check", reg) == 300.0
