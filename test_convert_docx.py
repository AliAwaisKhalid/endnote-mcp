"""Tests for convert_docx_citations: synthetic .docx and a fake index. The library is never opened."""
import re
import zipfile
from types import SimpleNamespace

import pytest

import server
from server import LibError

NSW = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def rec(i, authors, year, title="T", journal="J", trash=False):
    return {"id": i, "trash": trash, "type": 0, "author": authors, "year": year, "title": title, "journal": journal,
            "volume": "1", "issue": "", "pages": "1-2", "doi": ["10.1/x"], "url": [], "row": {}}


@pytest.fixture
def fake(monkeypatch):
    recs = {1: rec(1, ["Smith, A.", "Lee, B."], "2019"), 2: rec(2, ["Wu, C.", "Ab, B.", "Dd, E."], "2021"),
            3: rec(3, ["Park, D."], "2020"), 4: rec(4, ["Park, D."], "2020"), 5: rec(5, ["Old, E."], "2018", trash=True)}
    idx = SimpleNamespace(db_id="DBX", lib=SimpleNamespace(name="Fake"), order=list(recs), recs=recs, warning=None)
    monkeypatch.setattr(server, "_pick", lambda library="": "Fake")
    monkeypatch.setattr(server, "_index", lambda lib: idx)
    return idx


def make(tmp_path, *paras):
    p = tmp_path / "in.docx"
    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{t}</w:t></w:r></w:p>' for t in paras)
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0" encoding="UTF-8"?><w:document {NSW}><w:body>{body}</w:body></w:document>')
    return p


def test_converts_and_reports(fake, tmp_path):
    p = make(tmp_path, "Prior work (Smith &amp; Lee, 2019; Wu et al., 2021) and Smith and Lee (2019) agree. See (Park, 2020) and (Nobody, 2000).",
             "Park, D. (2020). A reference entry.")
    r = server.convert_docx_citations(str(p))
    assert r["citations_converted"] == 2
    assert r["records_cited"] == 2
    assert r["unmatched"] == ["Nobody, 2000"]
    assert r["ambiguous"] and r["ambiguous"][0].startswith("Park, 2020")
    out = tmp_path / "in_endnote_fields.docx"
    assert out.exists()
    d = server._docx_citations(out)
    assert d["fields"] == 2 and {c["rec"] for c in d["cites"]} == {1, 2}
    xml = zipfile.ZipFile(out).read("word/document.xml").decode()
    assert 'ExcludeAuth' not in xml.split("Smith and Lee")[0]     # parenthetical group is not author-suppressed
    assert "Smith and Lee " in xml and "ExcludeAuth=" in xml        # narrative one is
    assert "Park, D. (2020)." in xml                                # reference entry untouched


def test_original_never_changed_and_no_overwrite(fake, tmp_path):
    p = make(tmp_path, "See (Smith &amp; Lee, 2019).")
    before = p.read_bytes()
    server.convert_docx_citations(str(p))
    assert p.read_bytes() == before
    with pytest.raises(LibError, match="already exists"):
        server.convert_docx_citations(str(p))


def test_dry_run_writes_nothing(fake, tmp_path):
    p = make(tmp_path, "See (Smith &amp; Lee, 2019).")
    r = server.convert_docx_citations(str(p), dry_run=True)
    assert r["citations_converted"] == 1 and not (tmp_path / "in_endnote_fields.docx").exists()


def test_trashed_and_wrong_author_count_not_matched(fake, tmp_path):
    p = make(tmp_path, "(Old, 2018) (Smith et al., 2019) (Wu &amp; Ab, 2021)")
    r = server.convert_docx_citations(str(p), dry_run=True)
    assert r["citations_converted"] == 0 and len(r["unmatched"]) == 3


def test_bad_inputs(fake, tmp_path):
    with pytest.raises(LibError, match="only .docx"):
        server.convert_docx_citations(str(tmp_path / "x.txt"))
    with pytest.raises(LibError, match="not found"):
        server.convert_docx_citations(str(tmp_path / "x.docx"))
