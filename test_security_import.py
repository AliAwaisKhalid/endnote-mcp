"""Security tests for preview_import: file type and size limits. Uses the real Sports library, read-only."""
import pytest

import server
from server import LibError

RIS = "TY  - JOUR\nAU  - Nobody, Nemo\nPY  - 2031\nTI  - A paper that is surely not in the library\nER  - \n"

pytestmark = pytest.mark.skipif(not any(l.name == "Sports" for l in server._libs()), reason="Sports library not configured")


@pytest.mark.parametrize("ext", [".ris", ".txt", ".RIS"])
def test_ris_and_txt_files_allowed(tmp_path, ext):
    p = tmp_path / ("refs" + ext)
    p.write_text(RIS)
    assert server.preview_import(path=str(p), library="Sports")["new"] == 1


@pytest.mark.parametrize("name", ["secret.docx", "win.ini", "notes.json", "noext", "refs.ris.exe"])
def test_other_file_types_refused(tmp_path, name):
    p = tmp_path / name
    p.write_text(RIS)
    with pytest.raises(LibError, match=r"\.ris or \.txt"):
        server.preview_import(path=str(p), library="Sports")


def test_refusal_does_not_read_the_file(tmp_path, monkeypatch):
    p = tmp_path / "x.ini"
    p.write_text("TI  - leaked title\nER  - \n")
    with pytest.raises(LibError) as e:
        server.preview_import(path=str(p), library="Sports")
    assert "leaked" not in str(e.value)


def test_big_file_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MAX_IMPORT_BYTES", 100)
    p = tmp_path / "big.ris"
    p.write_text(RIS * 20)
    with pytest.raises(LibError, match="too large"):
        server.preview_import(path=str(p), library="Sports")


def test_big_ris_text_refused(monkeypatch):
    monkeypatch.setattr(server, "MAX_IMPORT_BYTES", 100)
    with pytest.raises(LibError, match="too large"):
        server.preview_import(ris_text=RIS * 20, library="Sports")


def test_small_ris_text_still_works():
    assert server.preview_import(ris_text=RIS, library="Sports")["records"] == 1
