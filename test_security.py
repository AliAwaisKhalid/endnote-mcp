"""Security tests: PDF attachment paths must stay inside the library's .Data\\PDF folder."""
import pytest

import server
from server import LibError


@pytest.fixture
def idx(tmp_path):
    lib = server.Lib("T", tmp_path / "T.enl")
    pdfdir = tmp_path / "T.Data" / "PDF" / "sub"
    pdfdir.mkdir(parents=True)
    (pdfdir / "ok.pdf").write_bytes(b"x")
    outside = tmp_path / "secret.txt"
    outside.write_text("TOP SECRET")
    i = server.Index.__new__(server.Index)
    i.lib = lib
    i.files = {
        1: ["sub/ok.pdf"],
        2: [str(outside)],                     # absolute path outside
        3: ["../../secret.txt"],               # traversal
        4: ["sub/ok.pdf", "..\\..\\secret.txt"],  # one good, one bad
        5: ["sub/../sub/ok.pdf"],              # harmless dots that stay inside
        6: [],
    }
    i.outside = outside
    return i


def test_inside_path_allowed(idx):
    assert [p.name for p in idx.pdf_path(1)] == ["ok.pdf"]


def test_dots_that_stay_inside_allowed(idx):
    assert [p.name for p in idx.pdf_path(5)] == ["ok.pdf"]


def test_absolute_path_outside_is_dropped(idx):
    assert idx.pdf_path(2) == []


def test_traversal_is_dropped(idx):
    assert idx.pdf_path(3) == []


def test_mixed_keeps_only_the_safe_one(idx):
    assert [p.name for p in idx.pdf_path(4)] == ["ok.pdf"]


def test_existing_pdfs_refuses_escaping_path(idx):
    for rid in (2, 3, 4):
        with pytest.raises(LibError, match="outside the library's PDF folder"):
            server._existing_pdfs(idx, rid, f"T:{rid}")


def test_existing_pdfs_still_works_for_safe_record(idx):
    assert [p.name for p in server._existing_pdfs(idx, 1, "T:1")] == ["ok.pdf"]


def test_no_pdf_message_unchanged(idx):
    with pytest.raises(LibError, match="No PDF is attached"):
        server._existing_pdfs(idx, 6, "T:6")


def test_refusal_does_not_echo_the_target_path(idx):
    with pytest.raises(LibError) as e:
        server._existing_pdfs(idx, 2, "T:2")
    assert "secret.txt" not in str(e.value)
