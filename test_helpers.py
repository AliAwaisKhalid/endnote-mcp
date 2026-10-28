"""Unit tests for the pure helpers in server.py. No EndNote library is opened."""
import struct
from types import SimpleNamespace

import pytest

import server
from server import LibError


def _blob(version, ids, count=None):
    n = len(ids) if count is None else count
    return struct.pack("<II", version, n) + struct.pack("<%dI" % len(ids), *ids)


# ---------- _members ----------

def test_members_reads_ids():
    assert server._members(_blob(1, [3, 7, 9])) == {3, 7, 9}


def test_members_empty_and_none():
    assert server._members(None) == set()
    assert server._members(b"") == set()
    assert server._members(_blob(1, [])) == set()


def test_members_short_header():
    assert server._members(b"\x01\x00\x00") == set()


def test_members_count_larger_than_data():
    # header claims 5 ids but only 2 are present: read the 2, do not crash
    assert server._members(_blob(1, [4, 5], count=5)) == {4, 5}


def test_members_trailing_partial_id():
    assert server._members(_blob(1, [4]) + b"\x01\x02") == {4}


# ---------- _record ----------

def _row(**kw):
    base = {"id": 1, "reference_type": 0, "author": "Smith, J\rLee, K", "year": "2020a", "title": "A Title"}
    base.update(kw)
    return base


def test_record_splits_multivalue_on_cr():
    rec = server._record(_row(keywords="a\rb\r\rc"))
    assert rec["author"] == ["Smith, J", "Lee, K"]
    assert rec["keywords"] == ["a", "b", "c"]


def test_record_year_truncated_to_four():
    assert server._record(_row())["year"] == "2020"


def test_record_doi_cleaned_and_filtered():
    rec = server._record(_row(electronic_resource_number="https://doi.org/10.1000/ABC\rnot a doi"))
    assert rec["doi"] == ["10.1000/abc"]


def test_record_bytes_and_none_fields():
    rec = server._record(_row(title=b"Caf\xc3\xa9", abstract=None))
    assert rec["title"] == "Café"
    assert rec["abstract"] == ""


def test_record_trash_flag():
    assert server._record(_row(trash_state=1))["trash"] is True
    assert server._record(_row(trash_state=0))["trash"] is False


def test_utc_is_utc():
    assert server._utc(0) == ""
    assert server._utc(86400) == "1970-01-02 00:00:00 UTC"


# ---------- _copy_reason ----------

def test_copy_reason_permission_error():
    msg = server._copy_reason(PermissionError("denied"), "x.enl")
    assert "EndNote has x.enl open" in msg


def test_copy_reason_winerror_32():
    e = OSError()
    e.winerror = 32
    assert "EndNote has" in server._copy_reason(e, "x.enl")


def test_copy_reason_other_error_passes_through():
    msg = server._copy_reason(OSError("disk full"), "x.enl")
    assert msg == "copying x.enl failed: disk full"


# ---------- check_records ----------

ESSEX = ["University of Essex, Institute for Social", "Economic, Research"]


def _rec(rid, author, year="2020", title="T", trash=False):
    return {"id": rid, "author": author, "year": year, "title": title, "trash": trash}


@pytest.fixture
def patched(monkeypatch):
    lib = SimpleNamespace(name="L")
    idx = SimpleNamespace(lib=lib)
    recs = {}

    def fake_resolve(key, library=""):
        return idx, recs[int(str(key).split(":")[-1])]

    monkeypatch.setattr(server, "_resolve", fake_resolve)
    return recs


def _check(patched, *recs):
    for r in recs:
        patched[r["id"]] = r
    return server.check_records(keys=[f"L:{r['id']}" for r in recs])


def test_check_flags_split_organisation(patched):
    out = _check(patched, _rec(1, ["University of Essex, Institute for Social", "and Economic Research"]))
    assert out["with_issues"] == 1
    assert "organisation split" in out["records"][0]["issues"][0]


def test_check_flags_split_when_fragment_looks_like_a_name(patched):
    out = _check(patched, _rec(6, ESSEX))
    assert out["with_issues"] == 1


def test_check_org_with_comma_followed_by_initials_author_is_not_flagged(patched):
    full = "University of Essex, Institute for Social and Economic Research"
    assert _check(patched, _rec(9, [full, "Smith, J"]))["with_issues"] == 0
    assert _check(patched, _rec(10, [full, "Smith, J. K."]))["with_issues"] == 0


def test_check_live_essex_shape_is_single_entry_and_clean(patched):
    one = ["University of Essex, Institute for Social and Economic Research"]
    assert _check(patched, _rec(7, one))["with_issues"] == 0
    assert _check(patched, _rec(8, one, year=""))["records"][0]["issues"] == ["no year"]


def test_check_clean_record(patched):
    out = _check(patched, _rec(2, ["Smith, John", "Lee, Kim"]))
    assert out == {"checked": 1, "with_issues": 0, "records": []}


def test_check_missing_year_and_author(patched):
    out = _check(patched, _rec(3, [], year=""))
    assert out["records"][0]["issues"] == ["no author", "no year"]


def test_check_single_org_entry_not_flagged(patched):
    out = _check(patched, _rec(4, ["University of Essex"]))
    assert out["with_issues"] == 0


def test_check_org_without_comma_followed_by_author_is_maybe_split(patched):
    out = _check(patched, _rec(5, ["World Bank", "Smith, J"]))
    assert "may be split" in out["records"][0]["issues"][0]


def test_org_words_no_catastrophic_backtracking():
    import time
    t = time.perf_counter()
    server.ORG_WORDS.search("University " * 20000 + "!")
    assert time.perf_counter() - t < 1


# ---------- _resolve ----------

@pytest.fixture
def libs(monkeypatch):
    a, b = server.Lib("Alpha", "a.enl"), server.Lib("Beta", "b.enl")
    monkeypatch.setattr(server, "_libs_cache", [a, b])
    idx = SimpleNamespace(recs={5: {"id": 5}}, lib=a)
    monkeypatch.setattr(server, "_index", lambda lib: idx if lib is a else SimpleNamespace(recs={}, lib=lib))
    return a, b, idx


def test_resolve_prefixed_key(libs):
    _, rec = server._resolve("Alpha:5")
    assert rec["id"] == 5


def test_resolve_bare_number_uses_default_library(libs):
    _, rec = server._resolve("5")
    assert rec["id"] == 5


def test_resolve_bare_number_with_library(libs):
    with pytest.raises(LibError, match="No record 5 in Beta"):
        server._resolve("5", "Beta")


def test_resolve_library_name_is_case_insensitive(libs):
    assert server._resolve("alpha:5")[1]["id"] == 5


def test_resolve_unknown_library(libs):
    with pytest.raises(LibError, match="Unknown library 'Nope'"):
        server._resolve("Nope:5")


def test_resolve_unknown_key_format(libs):
    with pytest.raises(LibError, match="Unknown item key"):
        server._resolve("abc")


def test_resolve_missing_record(libs):
    with pytest.raises(LibError, match="No record 99 in Alpha"):
        server._resolve("Alpha:99")


def test_resolve_non_string_key(libs):
    assert server._resolve(5)[1]["id"] == 5


# ---------- _wrap ----------

def test_wrap_without_warning_returns_rows():
    assert server._wrap(SimpleNamespace(warning=None), [1]) == [1]


def test_wrap_with_warning_wraps_rows():
    idx = SimpleNamespace(warning="stale")
    assert server._wrap(idx, [1]) == {"warning": "stale", "results": [1]}


# ---------- small string helpers ----------

@pytest.mark.parametrize("raw,want", [
    ("https://doi.org/10.1000/X", "10.1000/x"),
    ("http://dx.doi.org/10.1000/X", "10.1000/x"),
    ("doi: 10.1000/X", "10.1000/x"),
    ("  10.1000/X ", "10.1000/x"),
])
def test_clean_doi(raw, want):
    assert server._clean_doi(raw) == want


def test_connect_is_read_only(tmp_path):
    import sqlite3
    p = tmp_path / "t.db"
    sqlite3.connect(p).executescript("create table t(a); insert into t values (1);")
    con = server._connect(p)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("insert into t values (2)")


def test_connect_path_with_hash_and_percent_stays_read_only(tmp_path):
    # '?' cannot be in a Windows file name; '#' and '%' can and are URI-special
    import sqlite3
    d = tmp_path / "a#mode=rw%3Fx"
    d.mkdir()
    p = d / "t.db"
    sqlite3.connect(p).executescript("create table t(a);")
    con = server._connect(p)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("insert into t values (2)")
