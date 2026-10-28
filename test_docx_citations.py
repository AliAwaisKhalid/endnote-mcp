"""Tests for check_docx_citations. Synthetic .docx files plus the real v6 file against Commodities."""
import html
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import server
from server import LibError

LIB = "Commodities-Converted Copy"
V6 = Path(r"J:\Claude\Green Finance_RP\GF_Proposal_Update\RIF_Proposal_UK_Green_Finance_endnote_v6.docx")


def cite(num, author="Smith", year="2020", db="DB1"):
    inner = (f"<EndNote><Cite><Author>{author}</Author><Year>{year}</Year><RecNum>{num}</RecNum>"
             f"<record><rec-number>{num}</rec-number><foreign-keys><key app=\"EN\" db-id=\"{db}\">{num}</key></foreign-keys></record></Cite></EndNote>")
    return field(" ADDIN EN.CITE " + inner)


def field(instr):
    return (f'<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve">{html.escape(instr, quote=False)}</w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>(x)</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>')


def make_docx(tmp_path, body, name="t.docx"):
    p = tmp_path / name
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body><w:p>{body}</w:p></w:body></w:document>")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("word/document.xml", xml)
    return p


# ---------- parser ----------

def test_parser_counts_and_fields(tmp_path):
    p = make_docx(tmp_path, cite(1) + cite(1) + cite(2, "Lee", "2019") + field(" ADDIN EN.REFLIST ") + field(" ADDIN EN.CITE.DATA "))
    d = server._docx_citations(p)
    assert d["fields"] == 3
    assert [c["rec"] for c in d["cites"]] == [1, 1, 2]
    assert d["cites"][2]["author"] == "Lee" and d["cites"][2]["year"] == "2019"
    assert d["db_ids"] == {"DB1"}
    assert d["has_reflist"] is True
    assert d["data_placeholders"] == 1


def test_parser_instruction_split_over_runs(tmp_path):
    whole = " ADDIN EN.CITE <EndNote><Cite><Author>Smith</Author><Year>2020</Year><RecNum>7</RecNum></Cite></EndNote>"
    a, b = whole[:40], whole[40:]
    body = ('<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            f'<w:r><w:instrText>{html.escape(a)}</w:instrText></w:r><w:r><w:instrText>{html.escape(b)}</w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>')
    d = server._docx_citations(make_docx(tmp_path, body))
    assert [c["rec"] for c in d["cites"]] == [7]


def test_parser_cite_without_recnum_is_recorded(tmp_path):
    p = make_docx(tmp_path, field(" ADDIN EN.CITE <EndNote><Cite><Author>X</Author></Cite></EndNote>"))
    assert server._docx_citations(p)["cites"][0]["rec"] is None


def test_parser_no_endnote_fields(tmp_path):
    d = server._docx_citations(make_docx(tmp_path, "<w:r><w:t>plain</w:t></w:r>"))
    assert d["fields"] == 0 and d["cites"] == [] and d["has_reflist"] is False


def test_parser_rejects_non_docx(tmp_path):
    p = tmp_path / "x.docx"
    p.write_text("not a zip")
    with pytest.raises(LibError, match="not a valid .docx"):
        server._docx_citations(p)


def test_parser_xml_entities_not_expanded(tmp_path):
    bomb = ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "AAAA"><!ENTITY b "&a;&a;&a;&a;">]>'
            "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:body>&b;</w:body></w:document>")
    p = tmp_path / "bomb.docx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("word/document.xml", bomb)
    # the parser reads text with regex, never an XML entity expander
    assert server._docx_citations(p)["fields"] == 0


# ---------- tool, with a fake library ----------

@pytest.fixture
def fake(monkeypatch):
    recs = {
        1: {"id": 1, "author": ["Smith, J"], "year": "2020", "title": "A", "trash": False},
        2: {"id": 2, "author": ["Lee, K"], "year": "2019", "title": "B", "trash": False},
        3: {"id": 3, "author": ["Old, T"], "year": "2001", "title": "C", "trash": True},
    }
    idx = SimpleNamespace(recs=recs, lib=SimpleNamespace(name="L"), warning=None)
    monkeypatch.setattr(server, "_pick", lambda library="": idx.lib)
    monkeypatch.setattr(server, "_index", lambda lib: idx)
    return idx


def problems(out):
    return [p["problem"] for p in out["problems"]]


def test_tool_clean_document(tmp_path, fake):
    p = make_docx(tmp_path, cite(1) + cite(2, "Lee", "2019") + field(" ADDIN EN.REFLIST "))
    out = server.check_docx_citations(str(p))
    assert out["citations"] == 2 and out["distinct_records"] == 2
    assert out["problems"] == [] and out["has_reference_list"] is True


def test_tool_missing_record(tmp_path, fake):
    out = server.check_docx_citations(str(make_docx(tmp_path, cite(99))))
    assert "record not in library" in problems(out)


def test_tool_trashed_record(tmp_path, fake):
    out = server.check_docx_citations(str(make_docx(tmp_path, cite(3, "Old", "2001"))))
    assert problems(out) == ["record is in the trash"]


def test_tool_author_and_year_mismatch(tmp_path, fake):
    out = server.check_docx_citations(str(make_docx(tmp_path, cite(1, "Jones", "2018"))))
    assert sorted(problems(out)) == ["author differs", "year differs"]


def test_tool_year_suffix_is_not_a_mismatch(tmp_path, fake):
    out = server.check_docx_citations(str(make_docx(tmp_path, cite(1, "Smith", "2020a"))))
    assert out["problems"] == []


def test_tool_two_libraries_in_one_document(tmp_path, fake):
    out = server.check_docx_citations(str(make_docx(tmp_path, cite(1, db="DB1") + cite(2, "Lee", "2019", db="DB2"))))
    assert "citations come from more than one EndNote library" in problems(out)


def test_tool_most_missing_suggests_other_library(tmp_path, fake):
    out = server.check_docx_citations(str(make_docx(tmp_path, cite(90) + cite(91) + cite(1))))
    assert any("different library" in p["problem"] for p in out["problems"])


def test_tool_cite_without_record_number(tmp_path, fake):
    p = make_docx(tmp_path, field(" ADDIN EN.CITE <EndNote><Cite><Author>X</Author></Cite></EndNote>"))
    assert "citation has no record number" in problems(server.check_docx_citations(str(p)))


def test_tool_no_endnote_fields_is_a_message(tmp_path, fake):
    out = server.check_docx_citations(str(make_docx(tmp_path, "<w:r><w:t>plain</w:t></w:r>")))
    assert out["citations"] == 0 and "no endnote citations" in out["message"].lower()


def test_tool_only_docx(tmp_path, fake):
    p = tmp_path / "a.doc"
    p.write_bytes(b"x")
    with pytest.raises(LibError, match=r"\.docx"):
        server.check_docx_citations(str(p))


def test_tool_missing_file(tmp_path, fake):
    with pytest.raises(LibError, match="not found"):
        server.check_docx_citations(str(tmp_path / "nope.docx"))


def test_tool_size_limit(tmp_path, fake, monkeypatch):
    p = make_docx(tmp_path, cite(1))
    monkeypatch.setattr(server, "MAX_DOCX_BYTES", 10)
    with pytest.raises(LibError, match="too large"):
        server.check_docx_citations(str(p))


def test_tool_is_read_only(tmp_path, fake):
    p = make_docx(tmp_path, cite(1))
    before = (p.stat().st_size, p.stat().st_mtime_ns)
    server.check_docx_citations(str(p))
    assert (p.stat().st_size, p.stat().st_mtime_ns) == before


# ---------- real file ----------

@pytest.mark.skipif(not V6.is_file() or not any(l.name == LIB for l in server._libs()), reason="v6 docx or library missing")
def test_real_v6_against_commodities():
    out = server.check_docx_citations(str(V6), library=LIB)
    assert out["citations"] == 106 and out["distinct_records"] == 36
    assert out["has_reference_list"] is True and out["data_placeholders"] == 3
    assert out["problems"] == []
