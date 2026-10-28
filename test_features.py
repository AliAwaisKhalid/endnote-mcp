"""Tests for missing_pdfs, export_annotations, compare_libraries and the extra check_records rules. Synthetic libraries."""
from types import SimpleNamespace

import fitz
import pytest

import server
from server import LibError


def rec(rid, title="A long enough title for matching", year="2020", doi=None, files=None, trash=False, author=None,
        journal="J Test", rtype=0, pages="", raw_doi=""):
    return {"id": rid, "title": title, "year": year, "doi": doi or [], "trash": trash, "author": author or ["Smith, J"],
            "journal": journal, "type": rtype, "pages": pages, "ntitle": server._norm(title),
            "row": {"electronic_resource_number": raw_doi}}


def make_idx(tmp_path, name, recs, files=None, groups=None):
    lib = server.Lib(name, tmp_path / f"{name}.enl")
    (tmp_path / f"{name}.Data" / "PDF").mkdir(parents=True, exist_ok=True)
    i = server.Index.__new__(server.Index)
    i.lib, i.files, i.warning = lib, files or {}, None
    i.recs = {r["id"]: r for r in recs}
    i.order = sorted(i.recs)
    i.groups = groups or []
    i.doi_map, i.title_year = {}, {}
    for r in recs:
        for d in r["doi"]:
            i.doi_map.setdefault(d, []).append(r["id"])
        i.title_year.setdefault((r["ntitle"], r["year"]), []).append(r["id"])
    return i


def pdf_with_highlight(path, text="important finding", comment="check this"):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), text)
    a = page.add_highlight_annot(page.search_for(text)[0])
    a.set_info(content=comment, title="Ali")
    a.update()
    doc.save(str(path))
    doc.close()


@pytest.fixture
def world(monkeypatch):
    state = {}

    def install(*idxs):
        by_name = {i.lib.name: i for i in idxs}
        monkeypatch.setattr(server, "_pick", lambda library="": (by_name[library].lib if library else idxs[0].lib))
        monkeypatch.setattr(server, "_index", lambda lib: by_name[lib.name])
        return by_name

    state["install"] = install
    return state


# ---------- missing_pdfs ----------

def test_missing_pdfs_classifies(tmp_path, world):
    pdfdir = tmp_path / "L.Data" / "PDF"
    i = make_idx(tmp_path, "L", [rec(1), rec(2), rec(3), rec(4), rec(5, trash=True), rec(6)],
                 files={1: ["a.pdf"], 2: ["gone.pdf"], 4: ["../../x.txt"]})
    (pdfdir / "a.pdf").write_bytes(b"%PDF")
    world["install"](i)
    out = server.missing_pdfs(library="L")
    assert out["checked"] == 5 and out["with_pdf"] == 1
    probs = {r["key"]: r["problem"] for r in out["records"]}
    assert probs == {"L:2": "file missing on disk", "L:3": "no PDF attached", "L:4": "attachment path is outside the library's PDF folder",
                     "L:6": "no PDF attached"}


def test_missing_pdfs_group_filter(tmp_path, world):
    i = make_idx(tmp_path, "L", [rec(1), rec(2)], groups=[{"id": 7, "name": "G", "kind": "Group", "members": {2}}])
    world["install"](i)
    out = server.missing_pdfs(group="G", library="L")
    assert out["checked"] == 1 and [r["key"] for r in out["records"]] == ["L:2"]


def test_missing_pdfs_limit_truncates(tmp_path, world):
    i = make_idx(tmp_path, "L", [rec(n) for n in range(1, 6)])
    world["install"](i)
    out = server.missing_pdfs(library="L", limit=2)
    assert len(out["records"]) == 2 and out["truncated"] is True and out["without_usable_pdf"] == 5


def test_missing_pdfs_all_present_says_so(tmp_path, world):
    i = make_idx(tmp_path, "L", [rec(1)], files={1: ["a.pdf"]})
    (tmp_path / "L.Data" / "PDF" / "a.pdf").write_bytes(b"%PDF")
    world["install"](i)
    out = server.missing_pdfs(library="L")
    assert out["records"] == [] and "every" in out["message"].lower()


def test_missing_pdfs_bad_limit(tmp_path, world):
    world["install"](make_idx(tmp_path, "L", [rec(1)]))
    with pytest.raises(LibError, match="limit must be 1 or more"):
        server.missing_pdfs(library="L", limit=0)


# ---------- export_annotations ----------

def test_export_annotations_writes_markdown(tmp_path, world, monkeypatch):
    i = make_idx(tmp_path, "L", [rec(1, title="Paper One", author=["Smith, J", "Lee, K"]), rec(2, title="No pdf")],
                 files={1: ["p.pdf"]})
    pdf_with_highlight(tmp_path / "L.Data" / "PDF" / "p.pdf")
    world["install"](i)
    monkeypatch.setattr(server, "WORK", tmp_path / "work")
    out = server.export_annotations(keys=["L:1", "L:2"], library="L")
    assert out["records"] == 2 and out["with_annotations"] == 1 and out["annotations"] == 1
    assert len(out["skipped"]) == 1 and out["skipped"][0]["key"] == "L:2"
    assert out["skipped"][0]["reason"].startswith("No PDF is attached to L:2.")
    text = (tmp_path / "work").joinpath(out["file"].split("\\")[-1]).read_text(encoding="utf-8")
    assert "important finding" in text and "check this" in text and "Paper One" in text and "p. 1" in text
    assert out["file"].endswith(".md") and str(tmp_path / "work") in out["file"]


def test_export_annotations_pdf_without_annotations(tmp_path, world, monkeypatch):
    i = make_idx(tmp_path, "L", [rec(1)], files={1: ["p.pdf"]})
    doc = fitz.open()
    doc.new_page()
    doc.save(str(tmp_path / "L.Data" / "PDF" / "p.pdf"))
    world["install"](i)
    monkeypatch.setattr(server, "WORK", tmp_path / "work")
    out = server.export_annotations(keys=["L:1"], library="L")
    assert out["annotations"] == 0 and "no annotations" in out["message"].lower()


def test_export_annotations_needs_keys_or_group(tmp_path, world):
    world["install"](make_idx(tmp_path, "L", [rec(1)]))
    with pytest.raises(LibError, match="Give item keys, or the name of a group"):
        server.export_annotations(library="L")


def test_get_annotations_still_works(tmp_path, world, monkeypatch):
    i = make_idx(tmp_path, "L", [rec(1)], files={1: ["p.pdf"]})
    pdf_with_highlight(tmp_path / "L.Data" / "PDF" / "p.pdf")
    world["install"](i)
    monkeypatch.setattr(server, "_resolve", lambda key, library="": (i, i.recs[1]))
    out = server.get_annotations("L:1")
    assert out["annotations"][0]["highlighted_text"] == "important finding"


# ---------- compare_libraries ----------

def test_compare_libraries_by_doi_and_title(tmp_path, world):
    a = make_idx(tmp_path, "A", [rec(1, doi=["10.1/x"]), rec(2, title="Same title across libraries", year="2019"),
                                 rec(3, title="Only in A unique"), rec(4, trash=True, title="Trashed one")])
    b = make_idx(tmp_path, "B", [rec(10, doi=["10.1/x"], title="Different title"), rec(11, title="Same title across libraries", year="2019")])
    world["install"](a, b)
    out = server.compare_libraries(library="A", other="B")
    assert out["library"] == "A" and out["other"] == "B"
    assert out["checked"] == 3 and out["in_both"] == 2 and out["only_in_library"] == 1
    assert [r["key"] for r in out["records"]] == ["A:3"]


def test_compare_libraries_group_filter(tmp_path, world):
    a = make_idx(tmp_path, "A", [rec(1, title="First unique"), rec(2, title="Second unique")],
                 groups=[{"id": 1, "name": "G", "kind": "Group", "members": {2}}])
    b = make_idx(tmp_path, "B", [rec(10, title="Other thing entirely")])
    world["install"](a, b)
    out = server.compare_libraries(library="A", other="B", group="G")
    assert out["checked"] == 1 and out["records"][0]["key"] == "A:2"


def test_compare_libraries_same_library_refused(tmp_path, world):
    world["install"](make_idx(tmp_path, "A", [rec(1)]), make_idx(tmp_path, "B", [rec(2)]))
    with pytest.raises(LibError, match="two different libraries"):
        server.compare_libraries(library="A", other="A")


def test_compare_libraries_other_required(tmp_path, world):
    world["install"](make_idx(tmp_path, "A", [rec(1)]))
    with pytest.raises(LibError, match="Name the library to compare with"):
        server.compare_libraries(library="A", other="")


def test_compare_libraries_short_titles_do_not_match(tmp_path, world):
    a = make_idx(tmp_path, "A", [rec(1, title="Intro")])
    b = make_idx(tmp_path, "B", [rec(2, title="Intro")])
    world["install"](a, b)
    assert server.compare_libraries(library="A", other="B")["only_in_library"] == 1


# ---------- extra check_records rules ----------

@pytest.fixture
def checked(monkeypatch, tmp_path):
    def run(*recs, doi_map=None):
        i = make_idx(tmp_path, "L", list(recs))
        if doi_map is not None:
            i.doi_map = doi_map
        monkeypatch.setattr(server, "_resolve", lambda key, library="": (i, i.recs[int(str(key).split(":")[-1])]))
        return server.check_records(keys=[f"L:{r['id']}" for r in recs])
    return run


def issues_of(out):
    return [x for r in out["records"] for x in r["issues"]]


def test_rule_clean_record_has_no_issues(checked):
    assert checked(rec(1))["with_issues"] == 0


def test_rule_no_title(checked):
    assert "no title" in issues_of(checked(rec(1, title="")))


def test_rule_journal_article_without_journal(checked):
    assert "journal article with no journal name" in issues_of(checked(rec(1, journal="")))


def test_rule_book_without_journal_is_fine(checked):
    assert checked(rec(1, journal="", rtype=1))["with_issues"] == 0


def test_rule_invalid_doi(checked):
    out = issues_of(checked(rec(1, raw_doi="doi not recorded")))
    assert any("not a valid DOI" in x for x in out)


def test_rule_valid_doi_ok(checked):
    assert checked(rec(1, doi=["10.1/abc"], raw_doi="10.1/abc"))["with_issues"] == 0


def test_rule_doi_shared_with_other_record(checked):
    out = issues_of(checked(rec(1, doi=["10.1/abc"], raw_doi="10.1/abc"), doi_map={"10.1/abc": [1, 2]}))
    assert any("also on L:2" in x for x in out)


def test_rule_trashed_duplicate_doi_ignored(checked, tmp_path):
    r1, r2 = rec(1, doi=["10.1/abc"], raw_doi="10.1/abc"), rec(2, doi=["10.1/abc"], trash=True)
    out = checked(r1, r2, doi_map={"10.1/abc": [1, 2]})
    assert [r["key"] for r in out["records"] if r["key"] == "L:1"] == []


def test_rule_reversed_page_range(checked):
    assert any("page range" in x for x in issues_of(checked(rec(1, pages="54-27"))))


def test_rule_normal_page_ranges_ok(checked):
    for pages in ("27-54", "27", "e1234", "S27-S54", "1234-56"):
        assert checked(rec(1, pages=pages))["with_issues"] == 0, pages


def test_rule_all_caps_title(checked):
    assert any("capitals" in x for x in issues_of(checked(rec(1, title="THE EFFECT OF OIL PRICES ON STOCK RETURNS"))))


def test_rule_acronym_titles_ok(checked):
    assert checked(rec(1, title="GARCH and VAR models of CO2 and OPEC prices"))["with_issues"] == 0
