"""Tests for watch_library: changes since the last call, using a baseline stored in the cache folder."""
import json

import pytest

import server
from server import LibError
from test_features import make_idx, rec


def r(rid, updated=100, trash=False, title=None):
    return dict(rec(rid, title=title or f"Title number {rid} is long enough", trash=trash), updated=updated, added=updated)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CACHE", tmp_path / "cache")
    state = {"idx": None}

    def use(*recs):
        state["idx"] = make_idx(tmp_path, "L", list(recs))
        i = state["idx"]
        i.copied_at = "2026-10-28T10:00:00+00:00"
        monkeypatch.setattr(server, "_pick", lambda library="": i.lib)
        monkeypatch.setattr(server, "_index", lambda lib: i)
        return i

    state["use"] = use
    return state


def test_first_call_saves_baseline(env, tmp_path):
    env["use"](r(1), r(2))
    out = server.watch_library(library="L")
    assert out["baseline_saved"] is True and out["records"] == 2
    assert "first call" in out["message"].lower()
    saved = json.loads((tmp_path / "cache" / "watch" / "L.json").read_text(encoding="utf-8"))
    assert set(saved["records"]) == {"1", "2"}


def test_no_changes(env):
    env["use"](r(1), r(2))
    server.watch_library(library="L")
    out = server.watch_library(library="L")
    assert out["unchanged"] == 2 and out["added"] == [] and out["updated"] == []
    assert out["message"].startswith("No changes since")


def test_added_updated_trashed_restored_removed(env):
    env["use"](r(1), r(2), r(3), r(4, trash=True), r(5))
    server.watch_library(library="L")
    env["use"](r(1), r(2, updated=200), r(3, trash=True), r(4), r(6))
    out = server.watch_library(library="L")
    assert [x["key"] for x in out["added"]] == ["L:6"]
    assert [x["key"] for x in out["updated"]] == ["L:2"]
    assert [x["key"] for x in out["trashed"]] == ["L:3"]
    assert [x["key"] for x in out["restored"]] == ["L:4"]
    assert [x["key"] for x in out["removed"]] == ["L:5"] and "Title number 5" in out["removed"][0]["title"]
    assert out["unchanged"] == 1
    assert "last_updated" in out["updated"][0]


def test_baseline_advances_so_changes_are_reported_once(env):
    env["use"](r(1))
    server.watch_library(library="L")
    env["use"](r(1), r(2))
    assert len(server.watch_library(library="L")["added"]) == 1
    assert server.watch_library(library="L")["added"] == []


def test_update_baseline_false_keeps_old_baseline(env):
    env["use"](r(1))
    server.watch_library(library="L")
    env["use"](r(1), r(2))
    assert len(server.watch_library(library="L", update_baseline=False)["added"]) == 1
    assert len(server.watch_library(library="L")["added"]) == 1


def test_reset_rebaselines(env):
    env["use"](r(1))
    server.watch_library(library="L")
    env["use"](r(1), r(2))
    out = server.watch_library(library="L", reset=True)
    assert out["baseline_saved"] is True
    assert server.watch_library(library="L")["added"] == []


def test_corrupt_baseline_is_replaced_with_a_message(env, tmp_path):
    env["use"](r(1))
    d = tmp_path / "cache" / "watch"
    d.mkdir(parents=True)
    (d / "L.json").write_text("{not json")
    out = server.watch_library(library="L")
    assert out["baseline_saved"] is True and "unreadable" in out["message"].lower()


def test_limit_caps_rows_but_not_counts(env):
    env["use"](r(1))
    server.watch_library(library="L")
    env["use"](r(1), *[r(n) for n in range(2, 8)])
    out = server.watch_library(library="L", limit=2)
    assert len(out["added"]) == 2 and out["counts"]["added"] == 6 and out["truncated"] is True


def test_bad_limit(env):
    env["use"](r(1))
    with pytest.raises(LibError, match="limit must be 1 or more"):
        server.watch_library(library="L", limit=0)


def test_reports_snapshot_time_and_stale_warning(env):
    i = env["use"](r(1))
    server.watch_library(library="L")
    i.warning = "Serving the snapshot from earlier"
    out = server.watch_library(library="L")
    assert out["snapshot_copied_at"] == "2026-10-28T10:00:00+00:00" and out["warning"] == "Serving the snapshot from earlier"


def test_baseline_file_survives_a_snapshot_refresh(env, tmp_path):
    """The snapshot code deletes unknown files in cache/<library>; the baseline must live elsewhere."""
    env["use"](r(1))
    server.watch_library(library="L")
    assert not (tmp_path / "cache" / "L").exists() or not list((tmp_path / "cache" / "L").glob("watch*"))


def test_library_name_with_unsafe_characters(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CACHE", tmp_path / "cache")
    lib = server.Lib('A:b/c*"d', tmp_path / "x.enl")
    p = server._watch_file(lib)
    assert p.parent == tmp_path / "cache" / "watch" and "/" not in p.name and ":" not in p.name
