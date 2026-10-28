"""Integration tests against the real Commodities library and the v6 Word file. Read-only; Word is not launched."""
import html
import re
import zipfile
from pathlib import Path

import pytest

import server

LIB = "Commodities-Converted Copy"
DOCX = Path(r"J:\Claude\Green Finance_RP\GF_Proposal_Update\RIF_Proposal_UK_Green_Finance_endnote_v6.docx")

needs_lib = pytest.mark.skipif(not any(l.name == LIB for l in server._libs()), reason="Commodities library not configured")
needs_docx = pytest.mark.skipif(not DOCX.is_file(), reason="v6 docx not found")


@needs_lib
def test_check_records_essex_pair():
    out = server.check_records(keys=[f"{LIB}:10587", f"{LIB}:10593"])
    assert out["checked"] == 2
    flagged = {r["key"]: r["issues"] for r in out["records"]}
    assert f"{LIB}:10587" not in flagged            # organisation fixed, year present
    # 10593 is typed Journal Article but has no journal name, and no year
    assert flagged == {f"{LIB}:10593": ["no year", "journal article with no journal name"]}


@pytest.fixture(scope="module")
def instr():
    xml = zipfile.ZipFile(DOCX).read("word/document.xml").decode("utf8")
    return html.unescape("".join(re.findall(r"<w:instrText[^>]*>(.*?)</w:instrText>", xml, re.S)))


@needs_docx
def test_docx_field_inventory(instr):
    assert len(re.findall(r"ADDIN EN\.CITE(?!\.DATA)", instr)) == 106
    assert instr.count("EN.CITE.DATA") == 3
    assert instr.count("EN.REFLIST") == 1


@needs_docx
def test_docx_uses_one_library(instr):
    assert len(set(re.findall(r'db-id="([^"]+)"', instr))) == 1


@needs_docx
@needs_lib
def test_docx_citations_point_at_matching_commodities_records(instr):
    cites = re.findall(r"<Cite[ >].*?</Cite>", instr, re.S)
    assert len(cites) == 106
    for c in cites:
        n = int(re.search(r"<RecNum>(\d+)", c).group(1))
        _, rec = server._resolve(f"{LIB}:{n}")
        year = re.search(r"<Year>(.*?)</Year>", c)
        author = re.search(r"<Author>(.*?)</Author>", c)
        if year:
            assert year.group(1)[:4] == rec["year"], n
        if author:
            assert author.group(1).split(",")[0].lower() in " ".join(rec["author"]).lower(), n
