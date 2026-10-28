"""Tests for the three open items: library db-id check, organisation false positive, approximate title matching."""
import pytest

import server
from test_docx_citations import cite, field, make_docx, problems
from test_features import make_idx, rec

LIB = "Commodities-Converted Copy"
V6_DB = "fdfzrf2s4zzpv3ept28psrtrw0w0avv5vxdr"


# ---------- 1. database id ----------

def test_index_reads_database_id_from_real_library():
    if not any(l.name == LIB for l in server._libs()):
        pytest.skip("Commodities library not configured")
    assert server._index(server._pick(LIB)).db_id == V6_DB


def test_every_configured_library_has_its_own_id():
    ids = [server._index(l).db_id for l in server._libs() if l.enl.is_file()]
    assert all(ids) and len(set(ids)) == len(ids)


def test_list_libraries_shows_database_id():
    rows = server.list_libraries()
    assert all("database_id" in r for r in rows if r.get("status") == "ok")


@pytest.fixture
def fake(monkeypatch):
    def use(db_id):
        recs = {1: {"id": 1, "author": ["Smith, J"], "year": "2020", "title": "A", "trash": False}}
        idx = type("I", (), {})()
        idx.recs, idx.warning, idx.db_id = recs, None, db_id
        idx.lib = type("L", (), {"name": "L"})()
        monkeypatch.setattr(server, "_pick", lambda library="": idx.lib)
        monkeypatch.setattr(server, "_index", lambda lib: idx)
    return use


def test_docx_with_matching_database_id(tmp_path, fake):
    fake("DB1")
    out = server.check_docx_citations(str(make_docx(tmp_path, cite(1, db="DB1"))))
    assert out["database_id_matches"] is True and out["problems"] == []


def test_docx_with_other_database_id_is_flagged(tmp_path, fake):
    fake("DB2")
    out = server.check_docx_citations(str(make_docx(tmp_path, cite(1, db="DB1"))))
    assert out["database_id_matches"] is False
    assert any("different EndNote library" in p for p in problems(out))


def test_docx_unknown_library_id_is_not_a_problem(tmp_path, fake):
    fake(None)
    out = server.check_docx_citations(str(make_docx(tmp_path, cite(1, db="DB1"))))
    assert out["database_id_matches"] is None and out["problems"] == []


def test_docx_without_database_id_in_citations(tmp_path, fake):
    fake("DB1")
    body = field(" ADDIN EN.CITE <EndNote><Cite><Author>Smith</Author><Year>2020</Year><RecNum>1</RecNum></Cite></EndNote>")
    assert server.check_docx_citations(str(make_docx(tmp_path, body)))["database_id_matches"] is None


def test_real_v6_matches_commodities_by_database_id():
    from test_docx_citations import V6
    if not V6.is_file() or not any(l.name == LIB for l in server._libs()):
        pytest.skip("v6 docx or library missing")
    assert server.check_docx_citations(str(V6), library=LIB)["database_id_matches"] is True


def test_real_v6_against_wrong_library_is_flagged():
    from test_docx_citations import V6
    if not V6.is_file() or not any(l.name == "Sports" for l in server._libs()):
        pytest.skip("v6 docx or library missing")
    out = server.check_docx_citations(str(V6), library="Sports")
    assert out["database_id_matches"] is False


# ---------- 2. organisation false positive ----------

FULL = "University of Essex, Institute for Social and Economic Research"


@pytest.fixture
def check(monkeypatch, tmp_path):
    def run(authors):
        i = make_idx(tmp_path, "L", [dict(rec(1, author=authors))])
        monkeypatch.setattr(server, "_resolve", lambda key, library="": (i, i.recs[1]))
        return server.check_records(keys=["L:1"])
    return run


@pytest.mark.parametrize("person", ["Smith, John", "Lee, Kim Min", "Smith, J", "Smith, J. K."])
def test_complete_organisation_then_person_not_flagged(check, person):
    assert check([FULL, person])["with_issues"] == 0


def test_truncated_organisation_still_flagged(check):
    assert check(["University of Essex, Institute for Social", "Economic, Research"])["with_issues"] == 1


def test_truncated_organisation_before_a_person_name_shape_still_flagged(check):
    # last word 'Social' is not an organisation ending, so the entry looks cut off
    assert check(["University of Essex, Institute for Social", "Smith, John"])["with_issues"] == 1


def test_organisation_ending_with_connector_flagged(check):
    assert check(["University of Essex, Institute for Social and", "Economic Research"])["with_issues"] == 1


# ---------- 3. approximate title matching ----------

@pytest.fixture
def lib_with_titles(monkeypatch, tmp_path):
    recs = [rec(1, title="The Influence of ESG Performance on Corporate Value"),
            rec(2, title="Carbon Footprint of Sport Events"),
            rec(3, title="Influence of Weather on Football Attendance"),
            rec(4, title="Trashed Influence of ESG Performance on Corporate Value", trash=True)]
    i = make_idx(tmp_path, "L", recs)
    monkeypatch.setattr(server, "_pick", lambda library="": i.lib)
    monkeypatch.setattr(server, "_index", lambda lib: i)
    return i


def test_misspelt_title_finds_the_record(lib_with_titles):
    rows = server.find_by_identifier("Influense of ESG performanse on corporate valu", "L")
    assert [r["key"] for r in rows] == ["L:1"] and rows[0]["match"] == "title words (approximate spelling)"


def test_exact_and_word_matches_still_come_first(lib_with_titles):
    assert server.find_by_identifier("Carbon Footprint of Sport Events", "L")[0]["match"] == "title exact"
    assert server.find_by_identifier("carbon footprint", "L")[0]["match"] == "title words"


def test_unrelated_words_still_find_nothing(lib_with_titles):
    rows = server.find_by_identifier("quantum chromodynamics lattice", "L")
    assert list(rows[0]) == ["message"]


def test_short_words_are_not_matched_loosely(lib_with_titles):
    rows = server.find_by_identifier("Influence of ESG performance on corporate xx", "L")
    assert list(rows[0]) == ["message"]


def test_trashed_records_not_returned_by_approximate_match(lib_with_titles):
    rows = server.find_by_identifier("Trashd Influense ESG performanse corporate valu", "L")
    assert all(r.get("key") != "L:4" for r in rows)


def test_real_sports_misspelt_title():
    if not any(l.name == "Sports" for l in server._libs()):
        pytest.skip("Sports library not configured")
    rows = server.find_by_identifier("Influense of ESG performanse on corporate valu", "Sports")
    assert rows and rows[0].get("match") == "title words (approximate spelling)" and "ESG" in rows[0]["title"]
