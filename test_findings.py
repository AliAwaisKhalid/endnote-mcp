"""Tests for the simulation findings (SIMULATION_FINDINGS.md, items 1-7). Uses the real Sports library, read-only."""
import pytest

import server
from server import LibError

needs_sports = pytest.mark.skipif(not any(l.name == "Sports" for l in server._libs()), reason="Sports library not configured")
pytestmark = needs_sports


def _is_message(rows, text):
    return isinstance(rows, list) and len(rows) == 1 and list(rows[0]) == ["message"] and text.lower() in rows[0]["message"].lower()


def test_1_unknown_doi_is_an_answer_not_an_error():
    rows = server.find_by_identifier("10.9999/not.here", "Sports")
    assert _is_message(rows, "no record with doi")


def test_2_unknown_title_is_an_answer_not_an_error():
    rows = server.find_by_identifier("quantum chromodynamics lattice gauge", "Sports")
    assert _is_message(rows, "no record matches")


def test_3_search_with_no_hits_says_so():
    assert _is_message(server.search_items("zzqqxx nothing", library="Sports"), "no records matched")


def test_3_list_collections_empty_says_so(monkeypatch):
    idx = server._index(server._pick("Sports"))
    monkeypatch.setattr(idx, "groups", [])
    assert _is_message(server.list_collections("Sports"), "no groups")


def test_4_check_records_without_inputs_asks_for_them():
    with pytest.raises(LibError, match="Give item keys, or the name of a group"):
        server.check_records(library="Sports")


@pytest.mark.parametrize("bad", [0, -5])
@pytest.mark.parametrize("call", [
    lambda n: server.search_items("carbon", limit=n, library="Sports"),
    lambda n: server.recent_items(limit=n, library="Sports"),
    lambda n: server.list_tags(limit=n, library="Sports"),
    lambda n: server.collection_items("x", limit=n, library="Sports"),
])
def test_5_bad_limit_is_rejected(call, bad):
    with pytest.raises(LibError, match="limit must be 1 or more"):
        call(bad)


def test_6_text_that_is_not_ris_says_why():
    with pytest.raises(LibError, match="No RIS records found"):
        server.preview_import(ris_text="hello world", library="Sports")


def test_7_enlx_path_gets_a_hint():
    with pytest.raises(LibError, match=r"\.enlx is an export package"):
        server._pick(r"G:\nowhere\Sports.enlx")


def test_7_other_path_gets_a_hint():
    with pytest.raises(LibError, match="name, not a file path"):
        server._pick(r"G:\nowhere\Other.enl")


def test_7_plain_wrong_name_still_lists_libraries():
    with pytest.raises(LibError, match="Configured libraries"):
        server._pick("Sport")
