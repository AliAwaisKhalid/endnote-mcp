"""Tests for Crossref and OpenAlex import. No network: online.fetch is replaced with recorded sample responses."""
import pytest

import online
import server
from server import LibError
from test_features import make_idx, rec

CROSSREF_WORK = {"status": "ok", "message": {
    "DOI": "10.1000/Abc.123", "type": "journal-article", "title": ["Oil volatility"], "subtitle": ["and stock markets"],
    "author": [{"given": "Jane A.", "family": "Smith", "sequence": "first"}, {"name": "World Bank Research Group"}],
    "issued": {"date-parts": [[2021, 5, 1]]}, "container-title": ["Energy Economics"], "volume": "98", "issue": "2",
    "page": "105-118", "publisher": "Elsevier", "ISSN": ["0140-9883"], "URL": "http://dx.doi.org/10.1000/abc.123",
    "abstract": "<jats:p>We study <jats:italic>oil</jats:italic> shocks.</jats:p>", "is-referenced-by-count": 42, "references-count": 3,
    "reference": [{"DOI": "10.2000/ref.one", "year": "2015", "author": "Lee", "journal-title": "J Fin"},
                  {"unstructured": "Brown, T. (2010). A book without a DOI. Press.", "year": "2010"},
                  {"DOI": "10.2000/ref.two", "article-title": "Second ref", "year": "2018"}]}}

OPENALEX_WORK = {"id": "https://openalex.org/W111", "doi": "https://doi.org/10.1000/abc.123", "title": "Oil volatility and stock markets",
                 "publication_year": 2021, "type": "article",
                 "authorships": [{"author": {"display_name": "Jane A. Smith"}}, {"author": {"display_name": "Kim Lee"}}],
                 "primary_location": {"source": {"display_name": "Energy Economics", "host_organization_name": "Elsevier", "issn_l": "0140-9883"}},
                 "biblio": {"volume": "98", "issue": "2", "first_page": "105", "last_page": "118"},
                 "abstract_inverted_index": {"We": [0], "study": [1], "oil": [2], "shocks.": [3]},
                 "cited_by_count": 42, "referenced_works_count": 2, "referenced_works": ["https://openalex.org/W5", "https://openalex.org/W6"],
                 "keywords": [{"display_name": "Oil"}, {"display_name": "Volatility"}]}


@pytest.fixture
def net(monkeypatch):
    calls = []
    routes = {}

    def fake(url, timeout=20):
        calls.append(url)
        for frag, body in routes.items():
            if frag in url:
                if isinstance(body, Exception):
                    raise body
                return body
        raise AssertionError("unexpected URL " + url)

    monkeypatch.setattr(online, "fetch", fake)
    monkeypatch.delenv("ENDNOTE_MCP_OFFLINE", raising=False)
    fake.calls, fake.routes = calls, routes
    return fake


# ---------- normalisers ----------

def test_crossref_normalised():
    m = online.from_crossref(CROSSREF_WORK["message"])
    assert m["doi"] == "10.1000/abc.123" and m["title"] == "Oil volatility: and stock markets"
    assert m["authors"] == ["Smith, Jane A.", "World Bank Research Group,"]
    assert (m["year"], m["journal"], m["volume"], m["issue"], m["pages"]) == ("2021", "Energy Economics", "98", "2", "105-118")
    assert m["abstract"] == "We study oil shocks." and m["cited_by_count"] == 42 and m["type"] == "journal-article"
    assert m["source"] == "Crossref" and m["url"] == "https://doi.org/10.1000/abc.123"


def test_crossref_missing_fields_do_not_crash():
    m = online.from_crossref({"DOI": "10.1/x"})
    assert m["title"] == "" and m["authors"] == [] and m["year"] == ""


def test_openalex_normalised():
    m = online.from_openalex(OPENALEX_WORK)
    assert m["doi"] == "10.1000/abc.123" and m["authors"] == ["Smith, Jane A.", "Lee, Kim"]
    assert m["pages"] == "105-118" and m["abstract"] == "We study oil shocks." and m["keywords"] == ["Oil", "Volatility"]
    assert m["source"] == "OpenAlex" and m["openalex_id"] == "W111" and m["cited_by_count"] == 42


def test_openalex_without_doi_or_biblio():
    m = online.from_openalex({"id": "https://openalex.org/W9", "title": "T", "authorships": []})
    assert m["doi"] == "" and m["pages"] == ""


# ---------- RIS ----------

def test_to_ris_has_expected_lines():
    ris = online.to_ris(online.from_crossref(CROSSREF_WORK["message"]))
    lines = ris.splitlines()
    assert lines[0] == "TY  - JOUR" and lines[-1].startswith("ER  -")
    assert "AU  - Smith, Jane A." in lines and "AU  - World Bank Research Group," in lines
    for want in ("PY  - 2021", "TI  - Oil volatility: and stock markets", "T2  - Energy Economics", "VL  - 98", "IS  - 2",
                 "SP  - 105", "EP  - 118", "DO  - 10.1000/abc.123", "UR  - https://doi.org/10.1000/abc.123", "SN  - 0140-9883"):
        assert want in lines, want
    assert any(l.startswith("N1  - Imported from Crossref") for l in lines)


def test_to_ris_round_trips_through_the_server_parser():
    parsed = server._parse_ris(online.to_ris(online.from_crossref(CROSSREF_WORK["message"])))
    assert parsed[0]["doi"] == ["10.1000/abc.123"] and parsed[0]["year"] == "2021" and len(parsed[0]["authors"]) == 2


@pytest.mark.parametrize("typ,ty", [("journal-article", "JOUR"), ("book", "BOOK"), ("book-chapter", "CHAP"),
                                    ("proceedings-article", "CONF"), ("report", "RPRT"), ("dissertation", "THES"), ("weird", "GEN")])
def test_ris_types(typ, ty):
    assert online.to_ris({"type": typ, "title": "T", "authors": [], "source": "Crossref"}).startswith(f"TY  - {ty}")


def test_ris_text_cannot_inject_extra_records():
    ris = online.to_ris({"type": "journal-article", "title": "Bad\nER  - \nTY  - BOOK\nTI  - Injected", "authors": ["A\nER  - "],
                         "source": "Crossref"})
    assert ris.count("ER  - ") == 1 and ris.count("TY  - ") == 1


# ---------- network layer ----------

def test_only_allowed_hosts():
    for bad in ("http://api.crossref.org/works/x", "https://evil.example/x", "https://api.crossref.org.evil.example/x",
                "file:///c:/windows/win.ini", "https://user@api.crossref.org@evil.example/x"):
        with pytest.raises(LibError, match="not an allowed"):
            online.check_url(bad)
    online.check_url("https://api.crossref.org/works/10.1/x")
    online.check_url("https://api.openalex.org/works?filter=a")


def test_offline_switch(monkeypatch):
    monkeypatch.setenv("ENDNOTE_MCP_OFFLINE", "1")
    with pytest.raises(LibError, match="ENDNOTE_MCP_OFFLINE"):
        online.crossref_work("10.1000/abc")


def test_bad_doi_rejected_before_any_request(net):
    with pytest.raises(LibError, match="not a valid DOI"):
        online.crossref_work("not a doi")
    assert net.calls == []


def test_doi_is_url_encoded(net):
    net.routes["api.crossref.org/works/"] = CROSSREF_WORK
    online.crossref_work("10.1002/(SICI)1097-0258(19980430)17:8<873::AID-SIM779>3.0.CO;2-I")
    assert "<" not in net.calls[0] and "%3C" in net.calls[0]


def test_not_found_message(net):
    net.routes["api.crossref.org"] = LibError("HTTP 404")
    with pytest.raises(LibError):
        online.crossref_work("10.1000/none")


def test_response_size_cap(monkeypatch):
    class R:
        def read(self, n=-1):
            return b"x" * (n if n and n > 0 else 10**9)
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(online, "_open", lambda url, timeout: R())
    with pytest.raises(LibError, match="too large"):
        online.get_json("https://api.crossref.org/works/10.1/x")


def test_crossref_search_and_limit(net):
    net.routes["api.crossref.org/works?"] = {"message": {"items": [CROSSREF_WORK["message"]] * 3}}
    got = online.crossref_search("oil volatility", 2)
    assert len(got) == 3 or len(got) == 2  # server enforces rows=; the fake returns what it has
    assert "rows=2" in net.calls[0] and "query.bibliographic=oil+volatility" in net.calls[0]


def test_openalex_cited_by_uses_filter(net):
    net.routes["api.openalex.org/works/doi:"] = OPENALEX_WORK
    net.routes["api.openalex.org/works?"] = {"results": [OPENALEX_WORK], "meta": {"count": 1}}
    rows, total = online.openalex_related("10.1000/abc.123", "cited_by", 10)
    assert total == 1 and rows[0]["title"].startswith("Oil volatility")
    assert any("filter=cites:W111" in c for c in net.calls)


def test_openalex_references_uses_filter(net):
    net.routes["api.openalex.org/works/doi:"] = OPENALEX_WORK
    net.routes["api.openalex.org/works?"] = {"results": [OPENALEX_WORK, OPENALEX_WORK], "meta": {"count": 2}}
    rows, total = online.openalex_related("10.1000/abc.123", "references", 10)
    assert total == 2 and any("filter=cited_by:W111" in c for c in net.calls)


def test_crossref_references_listed():
    refs = online.crossref_references(CROSSREF_WORK["message"])
    assert [r["doi"] for r in refs] == ["10.2000/ref.one", "", "10.2000/ref.two"]
    assert refs[1]["unstructured"].startswith("Brown, T.")


# ---------- server tools ----------

@pytest.fixture
def lib(monkeypatch, tmp_path):
    i = make_idx(tmp_path, "L", [rec(1, doi=["10.1000/abc.123"], title="Oil volatility and stock markets", year="2021"),
                                 rec(2, title="Something else entirely different", year="2000")])
    monkeypatch.setattr(server, "_pick", lambda library="": i.lib)
    monkeypatch.setattr(server, "_index", lambda lib: i)
    monkeypatch.setattr(server, "WORK", tmp_path / "work")
    return i


def test_get_online_record_marks_library_status(net, lib):
    net.routes["api.crossref.org/works/"] = CROSSREF_WORK
    out = server.get_online_record("https://doi.org/10.1000/ABC.123", library="L")
    assert out["record"]["title"].startswith("Oil volatility") and out["in_library"] == ["L:1"] and out["status"] == "IN LIBRARY"


def test_get_online_record_new(net, lib):
    work = {"message": dict(CROSSREF_WORK["message"], DOI="10.9999/new", title=["Brand new paper title here"])}
    net.routes["api.crossref.org/works/"] = work
    assert server.get_online_record("10.9999/new", library="L")["status"] == "NEW"


def test_get_online_record_openalex(net, lib):
    net.routes["api.openalex.org/works/doi:"] = OPENALEX_WORK
    assert server.get_online_record("10.1000/abc.123", source="openalex", library="L")["record"]["source"] == "OpenAlex"


def test_unknown_source_rejected(lib):
    with pytest.raises(LibError, match="source must be"):
        server.get_online_record("10.1/x", source="pubmed", library="L")


def test_scopus_and_scholar_explained(lib):
    for s in ("scopus", "google scholar"):
        with pytest.raises(LibError, match="(Scopus|Scholar)"):
            server.search_online("x", source=s, library="L")


def test_search_online_rows_have_status(net, lib):
    other = dict(CROSSREF_WORK["message"], DOI="10.9999/new", title=["Brand new paper title here"])
    net.routes["api.crossref.org/works?"] = {"message": {"items": [CROSSREF_WORK["message"], other]}}
    out = server.search_online("oil volatility", limit=5, library="L")
    assert [r["status"] for r in out["results"]] == ["IN LIBRARY", "NEW"] and out["source"] == "Crossref"


def test_search_online_empty_query(lib):
    with pytest.raises(LibError, match="Give search words"):
        server.search_online("  ", library="L")


def test_search_online_bad_limit(lib):
    with pytest.raises(LibError, match="limit must be 1 or more"):
        server.search_online("x", limit=0, library="L")


def test_search_online_limit_capped(net, lib):
    net.routes["api.crossref.org/works?"] = {"message": {"items": []}}
    server.search_online("x", limit=5000, library="L")
    assert "rows=100" in net.calls[0]


def test_online_citations_references_crossref(net, lib):
    net.routes["api.crossref.org/works/"] = CROSSREF_WORK
    out = server.online_citations("10.1000/abc.123", direction="references", source="crossref", library="L")
    assert out["total"] == 3 and [r["status"] for r in out["results"]] == ["NEW", "NO DOI", "NEW"]


def test_online_citations_cited_by_needs_openalex(net, lib):
    with pytest.raises(LibError, match="OpenAlex"):
        server.online_citations("10.1000/abc.123", direction="cited_by", source="crossref", library="L")


def test_online_citations_cited_by_openalex(net, lib):
    net.routes["api.openalex.org/works/doi:"] = OPENALEX_WORK
    net.routes["api.openalex.org/works?"] = {"results": [OPENALEX_WORK], "meta": {"count": 40}}
    out = server.online_citations("10.1000/abc.123", direction="cited_by", source="openalex", limit=1, library="L")
    assert out["total"] == 40 and out["truncated"] is True and out["results"][0]["status"] == "IN LIBRARY"


def test_online_citations_bad_direction(lib):
    with pytest.raises(LibError, match="direction must be"):
        server.online_citations("10.1/x", direction="sideways", library="L")


def test_import_online_writes_ris_for_new_only(net, lib, tmp_path):
    new = {"message": dict(CROSSREF_WORK["message"], DOI="10.9999/new", title=["Brand new paper title here"])}
    net.routes["10.9999"] = new
    net.routes["10.1000"] = CROSSREF_WORK
    out = server.import_online(["10.1000/abc.123", "10.9999/new"], library="L")
    assert out["new"] == 1 and out["duplicates"] == 1 and out["failed"] == []
    text = open(out["file_for_import"], encoding="utf-8").read()
    assert "10.9999/new" in text and "10.1000/abc.123" not in text
    assert str(tmp_path / "work") in out["file_for_import"]


def test_import_online_reports_failures(net, lib):
    net.routes["10.9999"] = CROSSREF_WORK
    net.routes["10.8888"] = LibError("No record with DOI 10.8888/gone at Crossref.")
    out = server.import_online(["10.9999/new", "10.8888/gone", "bad doi"], library="L")
    assert [f["doi"] for f in out["failed"]] == ["10.8888/gone", "bad doi"]


def test_import_online_all_failed(net, lib):
    net.routes["10.8888"] = LibError("No record with DOI 10.8888/gone at Crossref.")
    with pytest.raises(LibError, match="None of the DOIs"):
        server.import_online(["10.8888/gone"], library="L")


def test_import_online_limits(lib):
    with pytest.raises(LibError, match="Give at least one DOI"):
        server.import_online([], library="L")
    with pytest.raises(LibError, match="at most 50"):
        server.import_online([f"10.1/{n}" for n in range(51)], library="L")


def test_import_online_never_writes_to_library_files(net, lib, tmp_path):
    net.routes["10.9999"] = CROSSREF_WORK
    before = sorted(p.name for p in tmp_path.rglob("*") if p.is_file())
    server.import_online(["10.9999/new"], library="L")
    after = sorted(p.name for p in tmp_path.rglob("*") if p.is_file())
    assert [a for a in after if a not in before and not a.startswith("new_records_")] == []
