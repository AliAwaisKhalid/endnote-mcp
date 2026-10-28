"""Read-only MCP server for EndNote 2025 libraries.

Each library is read from a snapshot copy of its .enl file and its .Data\\sdb files, never from the
original. The copy is refreshed when the source changes. Item keys look like "<library name>:<record number>".
Configure libraries with ENDNOTE_LIBRARIES (semicolon-separated .enl paths) or libraries.json next to this file.
"""
import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import threading
import uuid
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

HERE = Path(__file__).resolve().parent
LOCAL = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
CACHE = LOCAL / "endnote-mcp" / "cache"
WORK = LOCAL / "endnote-mcp" / "work"
STYLES = Path(os.environ.get("ZOTERO_STYLES_DIR") or (Path.home() / "Zotero" / "styles"))

mcp = FastMCP("endnote")
_lock = threading.RLock()
_indexes = {}
_libs_cache = None

REF_TYPES = {
    31: "Generic", 13: "Artwork", 12: "Audiovisual Material", 17: "Bill", 1: "Book", 7: "Book Section",
    18: "Case", 26: "Chart or Table", 35: "Classical Work", 6: "Computer Program", 33: "Conference Paper",
    3: "Conference Proceedings", 9: "Edited Book", 29: "Electronic Book", 28: "Electronic Article",
    16: "Web Page", 27: "Equation", 25: "Figure", 21: "Film or Broadcast", 32: "Government Document",
    19: "Hearing", 0: "Journal Article", 36: "Legal Rule or Regulation", 8: "Magazine Article",
    20: "Manuscript", 11: "Map", 5: "Newspaper Article", 30: "Online Database", 34: "Online Multimedia",
    15: "Patent", 4: "Personal Communication", 10: "Report", 22: "Statute", 2: "Thesis",
    37: "Unpublished Work", 38: "Ancient Text", 39: "Dictionary", 40: "Encyclopedia", 41: "Grant",
    42: "Aggregated Database", 43: "Blog", 44: "Catalog", 45: "Pamphlet", 46: "Serial", 47: "Standard",
    48: "Dataset", 49: "Electronic Book Section", 50: "Music", 51: "Podcast", 52: "Press Release",
    53: "Interview", 54: "Discussion Forum", 55: "Social Media", 56: "Television Episode",
    57: "Multimedia Application",
}
RIS_TYPE = {0: "JOUR", 1: "BOOK", 9: "EDBOOK", 7: "CHAP", 3: "CONF", 33: "CPAPER", 2: "THES", 10: "RPRT", 16: "ELEC", 5: "NEWS"}
CSL_TYPE = {0: "article-journal", 1: "book", 9: "book", 7: "chapter", 3: "paper-conference", 33: "paper-conference",
            2: "thesis", 10: "report", 16: "webpage", 5: "article-newspaper", 8: "article-magazine"}
BIB_TYPE = {0: "article", 5: "article", 1: "book", 9: "book", 7: "incollection", 3: "proceedings", 33: "inproceedings",
            2: "phdthesis", 10: "techreport", 16: "misc"}
RIS_NAME = {"JOUR": 0, "BOOK": 1, "EDBOOK": 9, "CHAP": 7, "CONF": 3, "CPAPER": 33, "THES": 2, "RPRT": 10, "ELEC": 16, "NEWS": 5}

FIELDS = [
    ("author", "Author"), ("year", "Year"), ("title", "Title"), ("secondary_title", "Journal or book title"),
    ("secondary_author", "Secondary author"), ("tertiary_title", "Series title"), ("tertiary_author", "Series editor"),
    ("volume", "Volume"), ("number", "Issue"), ("number_of_volumes", "Number of volumes"), ("pages", "Pages"),
    ("place_published", "Place published"), ("publisher", "Publisher"), ("edition", "Edition"), ("date", "Date"),
    ("type_of_work", "Type of work"), ("keywords", "Keywords"), ("abstract", "Abstract"), ("notes", "Notes"),
    ("research_notes", "Research notes"), ("label", "Label"), ("url", "URL"), ("isbn", "ISBN or ISSN"),
    ("electronic_resource_number", "DOI or electronic resource number"), ("custom_1", "Custom 1"),
    ("custom_2", "Custom 2"), ("custom_3", "Custom 3"), ("custom_4", "Custom 4"), ("custom_5", "Custom 5"),
    ("custom_6", "Custom 6"), ("custom_7", "Custom 7"), ("alternate_title", "Alternate title"),
    ("short_title", "Short title"), ("accession_number", "Accession number"), ("call_number", "Call number"),
    ("section", "Section"), ("access_date", "Access date"), ("language", "Language"), ("author_address", "Author address"),
    ("translated_title", "Translated title"), ("translated_author", "Translated author"),
    ("name_of_database", "Name of database"), ("database_provider", "Database provider"), ("caption", "Caption"),
]
CUSTOM = ["custom_1", "custom_2", "custom_3", "custom_4", "custom_5", "custom_6", "custom_7"]


from errors import LibError  # noqa: E402
import online  # noqa: E402


# ---------- configuration and snapshots ----------

class Lib:
    def __init__(self, name, enl):
        self.name = name
        self.enl = Path(enl)
        self.safe = re.sub(r'[<>:"/\\|?*]', "_", name)


def _libs():
    global _libs_cache
    if _libs_cache is None:
        env = os.environ.get("ENDNOTE_LIBRARIES", "").strip()
        if env:
            items = [{"path": p.strip()} for p in env.split(";") if p.strip()]
        else:
            cfg = HERE / "libraries.json"
            if not cfg.is_file():
                raise LibError("No libraries configured. Set ENDNOTE_LIBRARIES to semicolon-separated .enl paths, "
                               "or create libraries.json next to server.py.")
            raw = json.loads(cfg.read_text(encoding="utf-8"))
            items = [{"path": x} if isinstance(x, str) else x for x in raw]
        libs = []
        for it in items:
            lib = Lib(it.get("name") or Path(it["path"]).stem, it["path"])
            if any(x.name.lower() == lib.name.lower() for x in libs):
                raise LibError(f"Two libraries are named '{lib.name}'. Give one a different name in libraries.json.")
            libs.append(lib)
        _libs_cache = libs
    return _libs_cache


def _pick(library=""):
    libs = _libs()
    want = (library or "").strip()
    if not want:
        return libs[0]
    for lib in libs:
        if want.lower() == lib.name.lower() or os.path.normcase(str(lib.enl)) == os.path.normcase(str(Path(want))):
            return lib
    hint = ""
    if want.lower().endswith(".enlx"):
        hint = " A .enlx is an export package, not a library: open it in EndNote, then use the .enl it creates."
    elif "\\" in want or "/" in want:
        hint = " Give a library name, not a file path, unless the path is exactly one that list_libraries shows."
    raise LibError(f"Unknown library '{library}'.{hint} Configured libraries: {', '.join(x.name for x in libs)}. "
                   "Use list_libraries to see their paths.")


def _sources(lib):
    """The .enl file plus the .eni files (and any journal or WAL file) in its .Data\\sdb folder."""
    files = [lib.enl]
    sdb = lib.enl.with_suffix(".Data") / "sdb"
    if sdb.is_dir():
        files += [p for p in sorted(sdb.iterdir()) if p.is_file() and (p.suffix == ".eni" or p.name.endswith(("-journal", "-wal")))]
    return files


def _sig(files):
    return [[p.name, p.stat().st_size, p.stat().st_mtime_ns] for p in files]


def _copy_reason(e, name):
    if isinstance(e, PermissionError) or getattr(e, "winerror", None) == 32:
        return (f"EndNote has {name} open, so it cannot be copied. Close the library in EndNote (or quit EndNote), "
                "or use File > Save a Copy and point the server at the copy.")
    return f"copying {name} failed: {e}"


def _snapshot(lib):
    """Copy the library into the cache when the source has changed. Returns (folder, meta)."""
    if not lib.enl.is_file():
        raise LibError(f"Library file not found: {lib.enl}. Check its path in libraries.json or ENDNOTE_LIBRARIES.")
    if not (lib.enl.with_suffix(".Data") / "sdb" / "sdb.eni").is_file():
        raise LibError(f"{lib.enl.name} has no Data\\sdb\\sdb.eni file, so it is an older EndNote format. "
                       "Open it in EndNote 2025 with File > Open > Open Library, then point the server at the converted library.")
    files = _sources(lib)
    try:
        sig = _sig(files)
    except OSError as e:
        raise LibError(f"Cannot read {lib.enl.name} or its Data folder: {e.strerror}.")
    dest = CACHE / lib.safe
    meta_path = dest / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    if meta.get("sig") == sig and (dest / "sdb.eni").is_file():
        return dest, meta
    tmp = dest.with_name(dest.name + ".tmp")
    current = lib.enl.name
    try:
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        for p in files:
            current = p.name
            shutil.copy2(p, tmp / p.name)
        if _sig(files) != sig:
            raise OSError("the library changed while it was being copied")
    except OSError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        why = _copy_reason(e, current)
        if meta.get("sig") and (dest / "sdb.eni").is_file():
            return dest, dict(meta, warning=f"Serving the snapshot from {meta.get('copied_at')}. "
                                            f"The library could not be refreshed: {why}")
        raise LibError(why)
    dest.mkdir(parents=True, exist_ok=True)
    names = {p.name for p in files}
    for old in dest.iterdir():
        if old.name not in names and old.name != "meta.json":
            old.unlink()
    for p in tmp.iterdir():
        os.replace(p, dest / p.name)
    tmp.rmdir()
    meta = {"sig": sig, "copied_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return dest, meta


def _collate(a, b):
    a, b = a.casefold(), b.casefold()
    return (a > b) - (a < b)


def _connect(path):
    """Read-only connection. ENCI_Base and ENCIN_Base are EndNote's collations for text columns. Stand-ins are registered."""
    con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    for name in ("ENCI_Base", "ENCIN_Base"):
        con.create_collation(name, _collate)
    return con


def _s(v):
    if v is None:
        return ""
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).decode("utf-8", "replace")
    return str(v)


def _split(v):
    return [x.strip() for x in _s(v).split("\r") if x.strip()]


def _norm(s):
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).split())


def _clean_doi(s):
    s = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", s.strip(), flags=re.I)
    return re.sub(r"^doi:\s*", "", s, flags=re.I).strip().lower()


DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")


def _utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if ts else ""


def _members(blob):
    """Record IDs in a group's members blob: 4 bytes version, 4-byte count (little-endian), then one little-endian ID per member."""
    b = bytes(blob or b"")
    if len(b) < 8:
        return set()
    n = min(struct.unpack_from("<I", b, 4)[0], (len(b) - 8) // 4)
    return set(struct.unpack_from("<%dI" % n, b, 8)) if n else set()


def _name_of(spec):
    m = re.search(r"<name>(.*?)</name>", _s(spec), re.S)
    return html.unescape(m.group(1)) if m else ""


def _record(r):
    author = _split(r.get("author"))
    doi = [_clean_doi(x) for x in _split(r.get("electronic_resource_number")) if DOI_RE.match(_clean_doi(x))]
    rec = {
        "id": r["id"], "trash": (r.get("trash_state") or 0) != 0, "type": r.get("reference_type") or 0,
        "author": author, "year": _s(r.get("year"))[:4], "title": _s(r.get("title")),
        "journal": _s(r.get("secondary_title")), "volume": _s(r.get("volume")), "issue": _s(r.get("number")),
        "pages": _s(r.get("pages")), "doi": doi, "keywords": _split(r.get("keywords")),
        "abstract": _s(r.get("abstract")), "notes": _s(r.get("notes")), "research_notes": _s(r.get("research_notes")),
        "label": _s(r.get("label")), "custom": [_s(r.get(c)) for c in CUSTOM], "url": _split(r.get("url")),
        "added": r.get("added_to_library") or 0, "updated": r.get("record_last_updated") or 0,
        "row": {k: _s(v) for k, v in r.items()},
    }
    rec["ntitle"] = _norm(rec["title"])
    rec["hay"] = " ".join([" ".join(author), rec["title"], rec["year"], " ".join(rec["keywords"]), rec["abstract"]]).lower()
    return rec


class Index:
    """One library snapshot, read into memory."""

    def __init__(self, lib, folder, meta):
        self.lib = lib
        self.folder = folder
        self.copied_at = meta.get("copied_at")
        self.warning = meta.get("warning")
        self.sig = meta.get("sig")
        self.source_mtime = (lib.enl.with_suffix(".Data") / "sdb" / "sdb.eni").stat().st_mtime
        self.pdb = folder / "pdb.eni"
        self._pdf_text = None
        con = _connect(folder / "sdb.eni")
        tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        self.db_id = None  # the id Word stores as db-id in every EndNote citation (misc table, code 14)
        if "misc" in tables:
            row = con.execute("select value from misc where code = 14 and subcode = 0").fetchone()
            self.db_id = _s(row[0]).strip() or None if row else None
        self._vocab = None
        cur = con.execute("select * from refs")
        cols = [d[0] for d in cur.description]
        self.recs = {}
        for row in cur:
            rec = _record(dict(zip(cols, row)))
            self.recs[rec["id"]] = rec
        self.order = sorted(self.recs)
        self.groups = []
        if "groups" in tables:
            for gid, spec, members in con.execute("select group_id, spec, members from groups order by group_id"):
                xml = _s(spec)
                gtype = int(re.search(r"TYPE;(\d+)", xml).group(1)) if re.search(r"TYPE;(\d+)", xml) else 0
                smart = bool(re.search(r"<rule>SEARCH;", xml))
                combined = "SET_SEARCH;" in xml
                kind = ("Combined group" if combined else "Smart group (cached members)" if smart
                        else "Online search group" if gtype == 6 else "Group" if gtype == 3 else f"Group (type {gtype})")
                self.groups.append({"id": gid, "name": _name_of(spec) or f"group {gid}", "kind": kind,
                                    "members": _members(members)})
        self.tag_names = {}
        if "tag_groups" in tables:
            for gid, spec in con.execute("select group_id, spec from tag_groups"):
                colour = re.search(r"COLOR;([0-9a-fA-F]{6})", _s(spec))
                self.tag_names[gid] = (_name_of(spec), colour.group(1) if colour else "")
        self.tag_members = {}
        if "tag_members" in tables:
            for rid, ids in con.execute("select rowid, tag_ids from tag_members"):
                s = {int(x) for x in _s(ids).split() if x.isdigit()}
                if s:
                    self.tag_members[rid] = s
        self.files = {}
        if "file_res" in tables:
            for rid, path in con.execute("select refs_id, file_path from file_res order by refs_id, file_pos"):
                self.files.setdefault(rid, []).append(path)
        con.close()
        self.groups_of = {}
        for g in self.groups:
            for rid in g["members"]:
                self.groups_of.setdefault(rid, []).append(g["name"])
        self.doi_map = {}
        self.title_year = {}
        for rec in self.recs.values():
            for d in rec["doi"]:
                self.doi_map.setdefault(d, []).append(rec["id"])
            if rec["ntitle"]:
                self.title_year.setdefault((rec["ntitle"], rec["year"]), []).append(rec["id"])

    def _pdf_candidates(self, rid):
        """(path, inside) for each attachment. The stored path comes from the library file, so an absolute path or ..
        must not lead out of the library's PDF folder."""
        base = (self.lib.enl.with_suffix(".Data") / "PDF").resolve()
        out = []
        for p in self.files.get(rid, []):
            full = base / p.replace("/", os.sep)
            out.append((full, full.resolve().is_relative_to(base)))
        return out

    def vocab(self):
        """Every distinct word of 4+ letters in active titles, for approximate title matching."""
        if getattr(self, "_vocab", None) is None:
            self._vocab = {w for r in self.recs.values() if not r["trash"] for w in r["ntitle"].split() if len(w) >= 4}
        return self._vocab

    def pdf_path(self, rid):
        return [p for p, inside in self._pdf_candidates(rid) if inside]

    def has_escaping_pdf(self, rid):
        return any(not inside for _, inside in self._pdf_candidates(rid))

    def pdb_text(self):
        """EndNote's own PDF text index (pdb.eni, table pdf_index), loaded on first use."""
        if self._pdf_text is None:
            self._pdf_text = {}
            if self.pdb.is_file():
                con = _connect(self.pdb)
                for rid, contents in con.execute("select refs_id, contents from pdf_index"):
                    self._pdf_text[rid] = _s(contents).lower()
                con.close()
        return self._pdf_text


def _index(lib):
    with _lock:
        folder, meta = _snapshot(lib)
        cached = _indexes.get(lib.name)
        if cached and cached.sig == meta.get("sig") and cached.copied_at == meta.get("copied_at"):
            cached.warning = meta.get("warning")
            return cached
        idx = Index(lib, folder, meta)
        _indexes[lib.name] = idx
        return idx


def _wrap(idx, rows, empty=""):
    """Adds the stale-snapshot warning. A tool that finds nothing says so, so the user never gets a blank reply."""
    if empty and not rows:
        rows = [{"message": empty}]
    return {"warning": idx.warning, "results": rows} if idx.warning else rows


def _limit(n):
    if n < 1:
        raise LibError(f"limit must be 1 or more (you gave {n}).")
    return n


def _resolve(key, library=""):
    k = str(key).strip()
    m = re.match(r"^(.+):(\d+)$", k)
    if m:
        lib, rid = _pick(m.group(1)), int(m.group(2))
    elif k.isdigit():
        lib, rid = _pick(library), int(k)
    else:
        raise LibError(f"Unknown item key '{key}'. Keys look like Sports:123 (library name, colon, record number). "
                       "Use find_by_identifier or search_items to find one.")
    idx = _index(lib)
    rec = idx.recs.get(rid)
    if rec is None:
        raise LibError(f"No record {rid} in {lib.name}. Use search_items to find a valid key.")
    return idx, rec


def _key(idx, rec):
    return f"{idx.lib.name}:{rec['id']}"


def _short_authors(authors):
    return "; ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")


def _brief(idx, rec):
    return {"key": _key(idx, rec), "type": REF_TYPES.get(rec["type"], str(rec["type"])),
            "authors": _short_authors(rec["author"]), "year": rec["year"], "title": rec["title"],
            "journal": rec["journal"], "doi": rec["doi"][0] if rec["doi"] else ""}


def _type_code(ref_type):
    t = str(ref_type).strip()
    if t.isdigit():
        return int(t)
    for code, name in REF_TYPES.items():
        if name.lower() == t.lower():
            return code
    raise LibError(f"Unknown reference type '{ref_type}'. Use a name such as Journal Article or a code such as 0.")


def _group(idx, group):
    g = str(group).strip()
    for x in idx.groups:
        if (g.isdigit() and x["id"] == int(g)) or x["name"].lower() == g.lower():
            return x
    raise LibError(f"No group named '{group}' in {idx.lib.name}. Use list_collections to see group names.")


def _text_cache_file(path):
    """Cache file for one PDF's text, keyed by path, size and modified time."""
    st = path.stat()
    key = hashlib.sha1(f"{path}|{st.st_size}|{st.st_mtime_ns}".encode("utf-8")).hexdigest()
    return CACHE / "pdftext" / (key + ".txt")


def _cached_pdf_text(path):
    """Text of one PDF, cached. The PDF itself is only read."""
    import fitz
    cp = _text_cache_file(path)
    if cp.is_file():
        return cp.read_text(encoding="utf-8")
    doc = fitz.open(str(path))
    try:
        text = "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(text, encoding="utf-8")
    return text


def _existing_pdfs(idx, rid, key):
    if idx.has_escaping_pdf(rid):
        raise LibError(f"{key} has an attachment path that points outside the library's PDF folder, so it was not opened. "
                       "A library file from someone else can contain such a path. Check the attachment in EndNote.")
    paths = idx.pdf_path(rid)
    if not paths:
        raise LibError(f"No PDF is attached to {key}. Use get_item to see its attachments.")
    for p in paths:
        if not p.is_file():
            raise LibError(f"PDF not found on disk: {p}. EndNote lists this attachment, but the file has moved or is missing "
                           f"from {idx.lib.enl.with_suffix('.Data')}\\PDF.")
    return paths


# ---------- MCP tools ----------

@mcp.tool()
def list_libraries() -> list[dict]:
    """Configured libraries with path, reference counts, snapshot time and any problem reading them."""
    out = []
    for lib in _libs():
        try:
            idx = _index(lib)
            active = sum(1 for r in idx.recs.values() if not r["trash"])
            out.append({"name": lib.name, "path": str(lib.enl), "status": "ok", "references": active,
                        "in_trash": len(idx.recs) - active, "database_id": idx.db_id, "snapshot_copied_at": idx.copied_at,
                        "source_modified": _utc(idx.source_mtime), "warning": idx.warning})
        except LibError as e:
            out.append({"name": lib.name, "path": str(lib.enl), "status": f"error: {e}"})
    return out


@mcp.tool()
def search_items(query: str = "", group: str = "", keyword: str = "", ref_type: str = "", limit: int = 25,
                 fulltext: bool = False, library: str = "") -> list[dict] | dict:
    """Search active references. Every word in query must appear in author, title, year, keywords or abstract.
    fulltext=True also searches PDF text (EndNote's index and any PDFs already read by get_fulltext).
    Optional filters: group (name), keyword (exact), ref_type (name or code)."""
    _limit(limit)
    idx = _index(_pick(library))
    words = [w for w in re.split(r"\s+", query.lower().strip()) if w]
    members = _group(idx, group)["members"] if group.strip() else None
    rtype = _type_code(ref_type) if ref_type.strip() else None
    kw = keyword.strip().lower()
    pdf = idx.pdb_text() if (fulltext and words) else {}
    hits = []
    for rid in idx.order:
        r = idx.recs[rid]
        if r["trash"] or (members is not None and rid not in members) or (rtype is not None and r["type"] != rtype):
            continue
        if kw and kw not in [k.lower() for k in r["keywords"]]:
            continue
        if words:
            hay = r["hay"]
            if fulltext:
                cached = [_text_cache_file(p) for p in idx.pdf_path(rid) if p.is_file()]
                extra = " ".join(cp.read_text(encoding="utf-8").lower() for cp in cached if cp.is_file())
                hay = hay + " " + pdf.get(rid, "") + " " + extra
            if not all(w in hay for w in words):
                continue
        hits.append(_brief(idx, r))
        if len(hits) >= limit:
            break
    return _wrap(idx, hits, "No records matched. Every word in query must appear in the author, title, year, keywords or abstract; "
                            "try fewer or shorter words. To find a title with a spelling mistake, use find_by_identifier.")


def _approximate_title(idx, words):
    """Active records whose title has, for every query word, the same word or a close spelling of it.
    Words under 4 letters must match exactly. Returns at most 25 rows."""
    import difflib
    vocab = idx.vocab()
    ok = {}
    for w in set(words):
        ok[w] = {w} | (set(difflib.get_close_matches(w, vocab, n=8, cutoff=0.8)) if len(w) >= 4 else set())
    rows = []
    for r in idx.recs.values():
        if r["trash"]:
            continue
        tw = set(r["ntitle"].split())
        if all(tw & ok[w] for w in words):
            rows.append(dict(_brief(idx, r), match="title words (approximate spelling)"))
            if len(rows) >= 25:
                break
    return rows


@mcp.tool()
def find_by_identifier(identifier: str, library: str = "") -> list[dict] | dict:
    """Find records from one identifier: a DOI (or doi.org link), a key such as Sports:12, a record number,
    or words from the title. Exact matches come first."""
    ident = identifier.strip()
    if re.match(r"^(.+):(\d+)$", ident) or ident.isdigit():
        idx, rec = _resolve(ident, library)
        return _wrap(idx, [dict(_brief(idx, rec), match="key or record number")])
    idx = _index(_pick(library))
    doi = _clean_doi(ident)
    if DOI_RE.match(doi):
        rows = [dict(_brief(idx, idx.recs[i]), match="DOI exact") for i in idx.doi_map.get(doi, []) if not idx.recs[i]["trash"]]
        if rows:
            return _wrap(idx, rows)
        return _wrap(idx, [{"message": f"No record with DOI {doi} in {idx.lib.name}. It is not in the library."}])
    nt = _norm(ident)
    if not nt:
        raise LibError("Give a DOI, a key such as Sports:12, a record number, or words from the title.")
    exact = [r for r in idx.recs.values() if not r["trash"] and r["ntitle"] == nt]
    rows = [dict(_brief(idx, r), match="title exact") for r in exact]
    if not rows:
        words = nt.split()
        loose = [r for r in idx.recs.values() if not r["trash"] and all(w in r["ntitle"] for w in words)]
        rows = [dict(_brief(idx, r), match="title words") for r in loose[:25]]
    if not rows:
        rows = _approximate_title(idx, words)
    if not rows:
        return _wrap(idx, [{"message": f"No record matches '{identifier}' in {idx.lib.name}, even allowing for small spelling "
                                       "mistakes. Try a DOI, a record number, or fewer words."}])
    return _wrap(idx, rows)


@mcp.tool()
def get_item(key: str, library: str = "") -> dict:
    """All non-empty fields of one record, its PDF paths (with whether each file exists) and the groups it belongs to.
    key looks like Sports:12."""
    idx, rec = _resolve(key, library)
    k = _key(idx, rec)
    fields = {}
    for col, label in FIELDS:
        v = rec["row"].get(col, "")
        if v:
            fields[label] = v
    paths = idx.pdf_path(rec["id"])
    fields = {label: v.replace("\r", "; ") for label, v in fields.items()}
    out = {"key": k, "record_number": rec["id"], "type": REF_TYPES.get(rec["type"], str(rec["type"])),
           "trashed": rec["trash"], "authors": rec["author"], "fields": fields,
           "pdf_files": [{"path": str(p), "exists": p.is_file()} for p in paths],
           "groups": idx.groups_of.get(rec["id"], []),
           "tags": [idx.tag_names[t][0] for t in sorted(idx.tag_members.get(rec["id"], set())) if t in idx.tag_names],
           "added": _utc(rec["added"]), "last_updated": _utc(rec["updated"])}
    return dict(out, warning=idx.warning) if idx.warning else out


@mcp.tool()
def get_notes(key: str = "", query: str = "", limit: int = 25, library: str = "") -> list[dict] | dict:
    """Notes and Research Notes. With key: those fields for one record. Without key: records whose notes
    contain every word in query, with a short snippet."""
    if key.strip():
        idx, rec = _resolve(key, library)
        return [{"key": _key(idx, rec), "field": f, "text": rec[c]} for f, c in
                (("Notes", "notes"), ("Research notes", "research_notes")) if rec[c]]
    idx = _index(_pick(library))
    words = [w for w in query.lower().split() if w]
    rows = []
    for rid in idx.order:
        r = idx.recs[rid]
        text = (r["notes"] + "\n" + r["research_notes"]).strip()
        if r["trash"] or not text or not all(w in text.lower() for w in words):
            continue
        pos = text.lower().find(words[0]) if words else 0
        rows.append({"key": _key(idx, r), "title": r["title"], "snippet": text[max(0, pos - 80):pos + 160]})
        if len(rows) >= limit:
            break
    return _wrap(idx, rows)


def _hex(colour):
    return "#" + "".join(f"{round(c * 255):02x}" for c in colour[:3]) if colour else ""


@mcp.tool()
def get_annotations(key: str, library: str = "") -> dict:
    """PDF highlights and comments on every attached PDF of one record, read in place with PyMuPDF (nothing is saved).
    Each row gives the page, type, highlighted text, comment, colour and author."""
    idx, rec = _resolve(key, library)
    k = _key(idx, rec)
    paths = _existing_pdfs(idx, rec["id"], k)
    rows = [row for p in paths for row in _pdf_annotations(p)]
    out = {"key": k, "pdfs_checked": [p.name for p in paths], "annotations": rows}
    return dict(out, warning=idx.warning) if idx.warning else out


def _pdf_annotations(p):
    """Annotation rows of one PDF, read in place."""
    import fitz
    rows = []
    doc = fitz.open(str(p))
    try:
        for page in doc:
            for a in page.annots() or []:
                kind = a.type[1]
                info = a.info
                rows.append({"pdf": p.name, "page": page.number + 1, "type": kind,
                             "highlighted_text": page.get_textbox(a.rect).strip() if kind in ("Highlight", "Underline", "StrikeOut", "Squiggly") else "",
                             "comment": info.get("content") or "", "colour": _hex(a.colors.get("stroke")),
                             "author": info.get("title") or ""})
    finally:
        doc.close()
    return rows


@mcp.tool()
def export_annotations(keys: list[str] = [], group: str = "", library: str = "") -> dict:
    """Writes the PDF highlights and comments of several records to one Markdown file in the work folder, and returns
    its text. Give keys, or a group name. Records with no PDF are listed under skipped. The PDFs are only read."""
    pairs = _records_for(keys, group, library)
    lines, skipped, total, with_ann = ["# PDF annotations", ""], [], 0, 0
    for idx, rec in pairs:
        k = _key(idx, rec)
        try:
            rows = [row for p in _existing_pdfs(idx, rec["id"], k) for row in _pdf_annotations(p)]
        except LibError as e:
            skipped.append({"key": k, "reason": str(e)})
            continue
        if not rows:
            continue
        with_ann += 1
        total += len(rows)
        lines += [f"## {k}: {_short_authors(rec['author']) or 'Anonymous'} ({rec['year'] or 'n.d.'}). {rec['title']}", ""]
        for r in rows:
            quote = f"“{r['highlighted_text']}”" if r["highlighted_text"] else f"[{r['type']}]"
            lines.append(f"- p. {r['page']} {quote}" + (f" — {r['comment']}" if r["comment"] else ""))
        lines.append("")
    text = "\n".join(lines)
    WORK.mkdir(parents=True, exist_ok=True)
    path = WORK / f"annotations_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    path.write_text(text, encoding="utf-8")
    out = {"file": str(path), "records": len(pairs), "with_annotations": with_ann, "annotations": total, "skipped": skipped,
           "text": text}
    if not total:
        out["message"] = "No annotations found in the PDFs of these records."
    if pairs and pairs[0][0].warning:
        out["warning"] = pairs[0][0].warning
    return out


@mcp.tool()
def missing_pdfs(group: str = "", limit: int = 100, library: str = "") -> dict:
    """Active records that have no usable PDF: nothing attached, the file is missing on disk, or the attachment path
    leaves the library's PDF folder. Optionally limited to one group. Read-only."""
    _limit(limit)
    idx = _index(_pick(library))
    members = _group(idx, group)["members"] if group.strip() else None
    rows, checked, ok = [], 0, 0
    for rid in idx.order:
        r = idx.recs[rid]
        if r["trash"] or (members is not None and rid not in members):
            continue
        checked += 1
        cands = idx._pdf_candidates(rid)
        if not cands:
            problem = "no PDF attached"
        elif any(not inside for _, inside in cands):
            problem = "attachment path is outside the library's PDF folder"
        elif any(not p.is_file() for p, _ in cands):
            problem = "file missing on disk"
        else:
            ok += 1
            continue
        rows.append(dict(_brief(idx, r), problem=problem))
    out = {"library": idx.lib.name, "checked": checked, "with_pdf": ok, "without_usable_pdf": len(rows),
           "truncated": len(rows) > limit, "records": rows[:limit]}
    if not rows:
        out["message"] = "Every checked record has its PDF on disk."
    return dict(out, warning=idx.warning) if idx.warning else out


def _watch_file(lib):
    """Baseline for watch_library. Not under cache/<library>, because _snapshot deletes unknown files there."""
    return CACHE / "watch" / f"{lib.safe}.json"


@mcp.tool()
def watch_library(limit: int = 50, update_baseline: bool = True, reset: bool = False, library: str = "") -> dict:
    """What changed in a library since the last call: records added, updated, moved to the trash, restored, or removed.
    The first call saves a baseline (record numbers and update times, in the cache folder) and reports no changes.
    Each later call compares the library with the baseline, then moves the baseline forward. update_baseline=False
    looks without moving it; reset=True starts again from now. Reads the same snapshot as every other tool."""
    _limit(limit)
    idx = _index(_pick(library))
    cur = {rid: [r["updated"], bool(r["trash"]), r["title"][:80]] for rid, r in idx.recs.items()}
    path = _watch_file(idx.lib)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def save():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"taken_at": now, "records": {str(k): v for k, v in cur.items()}}), encoding="utf-8")
        os.replace(tmp, path)

    base, note = None, ""
    if path.is_file() and not reset:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            base = {int(k): v for k, v in data["records"].items()}
            since = data["taken_at"]
        except (ValueError, KeyError, TypeError, OSError):
            note = "The saved baseline was unreadable, so it was replaced. "
    result = {"library": idx.lib.name, "snapshot_copied_at": idx.copied_at}
    if idx.warning:
        result["warning"] = idx.warning
    if base is None:
        save()
        why = ("Baseline reset. " if reset else note or "This is the first call for this library. ")
        return dict(result, baseline_saved=True, records=len(cur), taken_at=now,
                    message=f"{why}Saved a baseline of {len(cur)} records. Call watch_library again later to see what changed.")
    groups = {"added": [], "updated": [], "trashed": [], "restored": [], "removed": []}
    for rid, c in cur.items():
        b = base.get(rid)
        if b is None:
            groups["added"].append(rid)
        elif not b[1] and c[1]:
            groups["trashed"].append(rid)
        elif b[1] and not c[1]:
            groups["restored"].append(rid)
        elif b[0] != c[0] and not c[1]:
            groups["updated"].append(rid)
    groups["removed"] = [rid for rid in base if rid not in cur]
    rows = {k: [] for k in groups}
    for k, ids in groups.items():
        for rid in sorted(ids)[:limit]:
            if k == "removed":
                rows[k].append({"key": f"{idx.lib.name}:{rid}", "title": base[rid][2]})
            else:
                rows[k].append(dict(_brief(idx, idx.recs[rid]), last_updated=_utc(idx.recs[rid]["updated"])))
    counts = {k: len(v) for k, v in groups.items()}
    changed = sum(counts.values())
    out = dict(result, since=since, counts=counts, unchanged=len(cur) - sum(counts[k] for k in ("added", "updated", "trashed", "restored")),
               truncated=any(n > limit for n in counts.values()), baseline_moved=bool(update_baseline), **rows)
    out["message"] = (f"No changes since {since}." if not changed else
                      f"{changed} change(s) since {since}: " + ", ".join(f"{n} {k}" for k, n in counts.items() if n) + ".")
    if update_baseline:
        save()
    return out


@mcp.tool()
def compare_libraries(other: str, group: str = "", limit: int = 100, library: str = "") -> dict:
    """Records of one library that are not in another, matched by DOI or by title and year (titles under 16 characters
    are not matched). Use it to find what a project library lacks, or what two libraries share. Optionally limited to one
    group of the first library. Read-only."""
    _limit(limit)
    if not (other or "").strip():
        raise LibError("Name the library to compare with (other). Use list_libraries to see the names.")
    a, b = _index(_pick(library)), _index(_pick(other))
    if a.lib.name == b.lib.name:
        raise LibError("Choose two different libraries: library and other are the same.")
    members = _group(a, group)["members"] if group.strip() else None

    def in_b(r):
        if any(not b.recs.get(i, {}).get("trash") for d in r["doi"] for i in b.doi_map.get(d, [])):
            return True
        return len(r["ntitle"]) > 15 and any(not b.recs.get(i, {}).get("trash") for i in b.title_year.get((r["ntitle"], r["year"]), []))

    rows, checked, both = [], 0, 0
    for rid in a.order:
        r = a.recs[rid]
        if r["trash"] or (members is not None and rid not in members):
            continue
        checked += 1
        if in_b(r):
            both += 1
        else:
            rows.append(_brief(a, r))
    out = {"library": a.lib.name, "other": b.lib.name, "checked": checked, "in_both": both, "only_in_library": len(rows),
           "truncated": len(rows) > limit, "records": rows[:limit]}
    return dict(out, warning=a.warning) if a.warning else out


@mcp.tool()
def get_fulltext(key: str, max_chars: int = 30000, library: str = "") -> dict:
    """Text of the first attached PDF of one record, up to max_chars (default 30,000). The text is cached."""
    idx, rec = _resolve(key, library)
    k = _key(idx, rec)
    path = _existing_pdfs(idx, rec["id"], k)[0]
    text = _cached_pdf_text(path)
    out = {"key": k, "pdf": str(path), "total_chars": len(text), "truncated": len(text) > max_chars, "text": text[:max_chars]}
    return dict(out, warning=idx.warning) if idx.warning else out


@mcp.tool()
def list_collections(library: str = "") -> list[dict] | dict:
    """Groups with type and active member count. Smart groups show their cached members, which may be out of date."""
    idx = _index(_pick(library))
    return _wrap(idx, [{"id": g["id"], "name": g["name"], "kind": g["kind"],
                        "items": sum(1 for m in g["members"] if m in idx.recs and not idx.recs[m]["trash"])}
                       for g in idx.groups], "This library has no groups.")


@mcp.tool()
def collection_items(group: str, limit: int = 100, library: str = "") -> list[dict] | dict:
    """Active members of one group, by name or group id."""
    _limit(limit)
    idx = _index(_pick(library))
    g = _group(idx, group)
    rows = [_brief(idx, idx.recs[m]) for m in sorted(g["members"]) if m in idx.recs and not idx.recs[m]["trash"]]
    return _wrap(idx, rows[:limit])


@mcp.tool()
def list_tags(kind: str = "keywords", limit: int = 100, library: str = "") -> list[dict] | dict:
    """Keywords with record counts (kind='keywords', the default), or EndNote's colour tags with counts (kind='colour').
    EndNote has no free tags; keywords are the nearest match."""
    _limit(limit)
    idx = _index(_pick(library))
    if kind == "colour":
        counts = Counter()
        for rid, ids in idx.tag_members.items():
            if rid in idx.recs and not idx.recs[rid]["trash"]:
                counts.update(ids)
        rows = [{"tag": idx.tag_names[t][0], "colour": idx.tag_names[t][1], "count": n}
                for t, n in counts.most_common() if t in idx.tag_names]
    else:
        counts, shown = Counter(), {}
        for r in idx.recs.values():
            if r["trash"]:
                continue
            for k in set(r["keywords"]):
                counts[k.lower()] += 1
                shown.setdefault(k.lower(), k)
        rows = [{"tag": shown[k], "count": n} for k, n in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]
    return _wrap(idx, rows[:limit])


@mcp.tool()
def recent_items(limit: int = 20, sort: str = "added", library: str = "") -> list[dict] | dict:
    """Newest records by date added (sort='added', default) or last updated (sort='updated'). Times are UTC."""
    _limit(limit)
    idx = _index(_pick(library))
    field = "updated" if sort.lower() in ("updated", "datemodified", "last_updated") else "added"
    rows = sorted((r for r in idx.recs.values() if not r["trash"]), key=lambda r: r[field], reverse=True)[:limit]
    return _wrap(idx, [dict(_brief(idx, r), added=_utc(r["added"]), last_updated=_utc(r["updated"])) for r in rows])


@mcp.tool()
def find_duplicates(max_items: int = 1000, library: str = "") -> list[dict] | dict:
    """Groups of records that share a DOI, or an identical normalised title and year. Scans the newest max_items
    (0 means all)."""
    idx = _index(_pick(library))
    recs = sorted((r for r in idx.recs.values() if not r["trash"]), key=lambda r: r["added"], reverse=True)
    if max_items:
        recs = recs[:max_items]
    ids = {r["id"] for r in recs}
    groups = []
    seen = set()
    for d, members in idx.doi_map.items():
        m = [i for i in members if i in ids]
        if len(m) > 1:
            groups.append({"reason": f"same DOI {d}", "keys": [f"{idx.lib.name}:{i}" for i in m]})
            seen.update(m)
    for (nt, year), members in idx.title_year.items():
        m = [i for i in members if i in ids and i not in seen]
        if nt and len(m) > 1:
            groups.append({"reason": f"same title and year {year}", "keys": [f"{idx.lib.name}:{i}" for i in m]})
    return _wrap(idx, groups)


@mcp.tool()
def list_by_status(prefix: str = "replication:", library: str = "") -> dict:
    """Records whose keywords or Label start with a prefix such as replication: or verify:, grouped by the full term."""
    idx = _index(_pick(library))
    p = prefix.lower()
    groups = {}
    for rid in idx.order:
        r = idx.recs[rid]
        if r["trash"]:
            continue
        terms = {k for k in r["keywords"] if k.lower().startswith(p)}
        if r["label"].lower().startswith(p):
            terms.add(r["label"])
        for t in sorted(terms):
            groups.setdefault(t, []).append(_key(idx, r))
    return _wrap(idx, {"prefix": prefix, "count": sum(len(v) for v in groups.values()),
                       "statuses": {t: groups[t] for t in sorted(groups)}})


@mcp.tool()
def method_matrix(group: str = "", fields: str = "Method,Estimator,Sample,Data", custom: bool = False,
                  include_empty: bool = False, library: str = "") -> dict:
    """Table of 'Name: value' lines from Research Notes (default) or from the Custom fields (custom=True),
    for the active records of one group or of the whole library."""
    idx = _index(_pick(library))
    names = [f.strip() for f in fields.split(",") if f.strip()]
    members = _group(idx, group)["members"] if group.strip() else None
    rows = []
    for rid in idx.order:
        r = idx.recs[rid]
        if r["trash"] or (members is not None and rid not in members):
            continue
        text = "\n".join(r["custom"]) if custom else r["research_notes"]
        values = {}
        for n in names:
            m = re.search(rf"^\s*{re.escape(n)}:[ \t]*(.*)$", text, re.I | re.M)
            values[n] = m.group(1).strip() if m else ""
        if include_empty or any(values.values()):
            rows.append({"key": _key(idx, r), "title": r["title"], "year": r["year"], **values})
    return _wrap(idx, {"source": "custom fields" if custom else "research notes", "rows": rows})


MAX_IMPORT_BYTES = 10_000_000

# Last words that end a full organisation name. An entry ending in one of these is read as complete, not cut off.
ORG_ENDINGS = re.compile(r"(Research|Studies|Statistics|Sciences?|Economics|Policy|Development|Affairs|Finance|Analysis|"
                         r"Education|Health|Review|Survey|Programme|Program|Network|Group|Board|Fund|Union|Trust|Forum|"
                         r"Team|Unit|Division|Directorate|Laboratory|Observatory|Secretariat|Bureau|Branch)", re.I)

ORG_WORDS = re.compile(r"\b(University|Institute|Department|Council|Commission|Ministry|Agency|Office|Bank|Authority|"
                       r"Organi[sz]ation|Centre|Center|Society|Association|Foundation|Service)\b", re.I)


def _record_rules(idx, rec):
    """Checks on title, journal, DOI and pages. A key missing from rec counts as fine."""
    found = []
    title = rec.get("title", "x")
    if not title.strip():
        found.append("no title")
    elif len([c for c in title if c.isalpha()]) >= 12 and not any(c.islower() for c in title):
        found.append("title is all capitals; EndNote cannot fix this in a style, so retype it")
    if rec.get("type") == 0 and rec.get("journal", "x") == "":
        found.append("journal article with no journal name")
    raw = _s(rec.get("row", {}).get("electronic_resource_number")).strip()
    if rec.get("type") == 0 and raw and not rec.get("doi"):
        found.append(f"electronic resource number is not a valid DOI: '{raw[:60]}'")
    for d in rec.get("doi", []):
        others = [i for i in idx.doi_map.get(d, []) if i != rec["id"] and not idx.recs.get(i, {}).get("trash")]
        if others:
            found.append(f"DOI {d} is also on " + ", ".join(f"{idx.lib.name}:{i}" for i in others[:3]))
    m = re.fullmatch(r"\s*(\d+)\s*[-–—]\s*(\d+)\s*", rec.get("pages", "") or "")
    if m:
        start, end = m.groups()
        if len(end) < len(start):
            end = start[:len(start) - len(end)] + end
        if int(start) > int(end):
            found.append(f"page range runs backwards: '{rec['pages']}'")
    return found


@mcp.tool()
def check_records(group: str = "", keys: list[str] = [], library: str = "") -> dict:
    """Checks records before they are cited or exported. Flags an organisation name that EndNote has split across
    several author entries (for example 'University of Essex, Institute for Social' and 'Economic, Research'),
    records with no year, and records with no authors. Read-only. Give keys, or a group name."""
    if keys:
        pairs = [_resolve(k, library) for k in keys]
    else:
        if not group.strip():
            raise LibError("Give item keys, or the name of a group.")
        idx = _index(_pick(library))
        g = _group(idx, group)
        pairs = [(idx, idx.recs[m]) for m in sorted(g["members"]) if m in idx.recs and not idx.recs[m]["trash"]]
    issues = []
    for idx, rec in pairs:
        found = []
        auths = rec["author"]
        if not auths:
            found.append("no author")
        for i, a in enumerate(auths):
            head, _, tail = a.partition(",")
            last = (re.findall(r"[A-Za-z]+", tail) or [""])[-1]
            looks_complete = bool(ORG_WORDS.fullmatch(last) or ORG_ENDINGS.fullmatch(last))
            org_both_sides = bool(ORG_WORDS.search(head) and ORG_WORDS.search(tail)) and not looks_complete
            next_is_initials = i + 1 < len(auths) and re.fullmatch(r"[^,]+, ?(?:[A-Z]\.?\s?)+", auths[i + 1])
            if ORG_WORDS.search(a) and "," in a and i + 1 < len(auths) and (
                    not re.search(r"[A-Z][a-z]+, [A-Z]", auths[i + 1]) or (org_both_sides and not next_is_initials)):
                found.append(f"author {i + 1} looks like an organisation split over entries {i + 1} and {i + 2}: "
                             f"'{a}' / '{auths[i + 1]}'. Make it one entry in EndNote.")
            elif ORG_WORDS.search(a) and "," not in a and i + 1 < len(auths):
                found.append(f"author {i + 1} may be split: '{a}' / '{auths[i + 1]}'.")
        if not rec["year"]:
            found.append("no year")
        found += _record_rules(idx, rec)
        if found:
            issues.append({"key": _key(idx, rec), "title": rec["title"][:90], "issues": found})
    return {"checked": len(pairs), "with_issues": len(issues), "records": issues}


MAX_DOCX_BYTES = 100_000_000
MAX_DOCX_XML_BYTES = 300_000_000
_FIELD_TOKENS = re.compile(r'<w:fldChar\b[^>]*w:fldCharType="(begin|separate|end)"[^>]*/?>|<w:instrText\b[^>]*>(.*?)</w:instrText>'
                           r'|<w:fldSimple\b[^>]*w:instr="([^"]*)"', re.S)


def _docx_citations(path):
    """EndNote fields in a .docx, read from word/document.xml as text. Nothing is run and nothing is changed."""
    try:
        with zipfile.ZipFile(path) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise LibError("The document body is too large to check.")
            xml = z.read(info).decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError):
        raise LibError(f"{Path(path).name} is not a valid .docx file (no word/document.xml inside).")
    instrs, stack = [], []
    for m in _FIELD_TOKENS.finditer(xml):
        kind, text, simple = m.groups()
        if simple is not None:
            instrs.append(html.unescape(simple))
        elif text is not None:
            if stack:
                stack[-1].append(text)
        elif kind == "begin":
            stack.append([])
        elif kind == "end" and stack:
            instrs.append(html.unescape("".join(stack.pop())))
    out = {"fields": 0, "cites": [], "db_ids": set(), "has_reflist": False, "data_placeholders": 0}
    for ins in instrs:
        s = ins.strip()
        if s.startswith("ADDIN EN.CITE.DATA"):
            out["data_placeholders"] += 1
        elif s.startswith("ADDIN EN.CITE"):
            out["fields"] += 1
            out["db_ids"].update(re.findall(r'db-id="([^"]+)"', s))
            for c in re.findall(r"<Cite[ >].*?</Cite>", s, re.S):
                num = re.search(r"<RecNum>(\d+)</RecNum>", c) or re.search(r"<rec-number>(\d+)</rec-number>", c)
                au = re.search(r"<Author>(.*?)</Author>", c, re.S)
                yr = re.search(r"<Year>(.*?)</Year>", c, re.S)
                out["cites"].append({"rec": int(num.group(1)) if num else None,
                                     "author": html.unescape(au.group(1)).strip() if au else "",
                                     "year": html.unescape(yr.group(1)).strip() if yr else ""})
        elif s.startswith("ADDIN EN.REFLIST"):
            out["has_reflist"] = True
    return out


@mcp.tool()
def check_docx_citations(path: str, library: str = "") -> dict:
    """Checks the EndNote citations in a Word .docx against a library, without opening Word or changing the file.
    Reports citations whose record is missing or in the trash, whose author or year differs from the library record,
    that have no record number, or that come from more than one EndNote library. Also reports whether the document has
    a reference list. Give the library the document was written with."""
    p = Path(path.strip())
    if p.suffix.lower() != ".docx":
        raise LibError("check_docx_citations reads only .docx files. In Word, use File > Save As > Word Document (.docx).")
    if not p.is_file():
        raise LibError(f"Word file not found: {p}")
    if p.stat().st_size > MAX_DOCX_BYTES:
        raise LibError(f"That file is too large (limit {MAX_DOCX_BYTES // 1_000_000} MB).")
    doc = _docx_citations(p)
    cites = doc["cites"]
    base = {"file": p.name, "citation_fields": doc["fields"], "citations": len(cites),
            "has_reference_list": doc["has_reflist"], "data_placeholders": doc["data_placeholders"]}
    if not cites:
        return dict(base, distinct_records=0, problems=[], message="No EndNote citations found in this document. "
                    "Either it has none, or they were converted to plain text.")
    idx = _index(_pick(library))
    found = {}
    lib_id = getattr(idx, "db_id", None)
    id_matches = (lib_id in doc["db_ids"] and len(doc["db_ids"]) == 1) if lib_id and doc["db_ids"] else None

    def note(rec, problem, detail=""):
        key = (rec, problem, detail)
        found[key] = found.get(key, 0) + 1

    distinct = {c["rec"] for c in cites if c["rec"] is not None}
    missing = set()
    for c in cites:
        rid = c["rec"]
        if rid is None:
            note(None, "citation has no record number", c["author"])
            continue
        rec = idx.recs.get(rid)
        if rec is None:
            missing.add(rid)
            note(rid, "record not in library")
            continue
        key = _key(idx, rec)
        if rec["trash"]:
            note(key, "record is in the trash")
        if c["year"] and c["year"][:4] != rec["year"]:
            note(key, "year differs", f"document {c['year']}, library {rec['year'] or 'none'}")
        if c["author"] and rec["author"] and c["author"].lower() not in " ".join(rec["author"]).lower():
            note(key, "author differs", f"document {c['author']}, library {rec['author'][0]}")
    problems = [{"record": k[0] if k[0] is None or isinstance(k[0], str) else f"{idx.lib.name}:{k[0]}", "problem": k[1],
                 **({"detail": k[2]} if k[2] else {}), "times": n} for k, n in found.items()]
    if len(doc["db_ids"]) > 1:
        problems.append({"record": None, "problem": "citations come from more than one EndNote library",
                         "detail": ", ".join(sorted(doc["db_ids"]))})
    if distinct and len(missing) * 2 > len(distinct):
        problems.append({"record": None, "problem": f"most cited records are not in {idx.lib.name}; the document may have been "
                         "written with a different library", "detail": f"{len(missing)} of {len(distinct)} missing"})
    if id_matches is False and lib_id not in doc["db_ids"]:
        problems.append({"record": None, "problem": f"the citations come from a different EndNote library than {idx.lib.name}",
                         "detail": f"document database id {', '.join(sorted(doc['db_ids']))}; {idx.lib.name} is {lib_id}"})
    out = dict(base, library=idx.lib.name, database_id_matches=id_matches, distinct_records=len(distinct), problems=problems)
    return dict(out, warning=idx.warning) if idx.warning else out


def _fold(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s.casefold()) if not unicodedata.combining(c)).strip()


@mcp.tool()
def convert_docx_citations(path: str, library: str = "", output: str = "", dry_run: bool = False) -> dict:
    """Turns plain-text author-year citations in a Word .docx into live EndNote fields, matched to records in a library.
    Handles '(Smith et al., 2020)', '(Smith & Lee, 2019; Wu, 2021)' and 'Smith et al. (2020)'. A citation is converted only
    when exactly one active record matches the first author, the year and the number of authors; the rest are listed under
    unmatched or ambiguous. The original file is never changed: the result goes to a new file (default
    <name>_endnote_fields.docx beside the original, never overwriting). The library is not changed either. Afterwards, in Word,
    use EndNote > Update Citations and Bibliography to format the citations and build the reference list.
    dry_run=True only reports what would be converted."""
    p = Path(path.strip())
    if p.suffix.lower() != ".docx":
        raise LibError("convert_docx_citations reads only .docx files. In Word, use File > Save As > Word Document (.docx).")
    if not p.is_file():
        raise LibError(f"Word file not found: {p}")
    if p.stat().st_size > MAX_DOCX_BYTES:
        raise LibError(f"That file is too large (limit {MAX_DOCX_BYTES // 1_000_000} MB).")
    out_path = Path(output.strip()) if output.strip() else p.with_name(p.stem + "_endnote_fields.docx")
    if out_path.suffix.lower() != ".docx":
        raise LibError("The output file must end in .docx.")
    if out_path.resolve() == p.resolve():
        raise LibError("The output file must be a different file from the original.")
    if out_path.exists() and not dry_run:
        raise LibError(f"{out_path.name} already exists. Give another output name, or move that file.")
    idx = _index(_pick(library))
    if not idx.db_id:
        raise LibError(f"{idx.lib.name} has no database id, so Word could not link the citations to it.")
    by_surname = {}
    for rid in idx.order:
        r = idx.recs[rid]
        if not r["trash"] and r["author"]:
            by_surname.setdefault(_fold(r["author"][0].split(",")[0]), []).append(r)

    def find(name, year):
        parts = re.split(r" et al\.| & | and ", name)
        first = _fold(parts[0])
        hits = [r for r in by_surname.get(first, []) if r["year"] == year]
        if " et al." in name:
            hits = [r for r in hits if len(r["author"]) >= 3]
        elif len(parts) > 1:
            hits = [r for r in hits if len(r["author"]) == 2 and _fold(r["author"][1].split(",")[0]) == _fold(parts[1])]
        else:
            hits = [r for r in hits if len(r["author"]) == 1]
        return hits

    import docx_cites
    out_tmp = out_path
    rep = docx_cites.convert(str(p), str(out_tmp), find, idx.db_id, ts=str(int(datetime.now().timestamp())), dry_run=dry_run)
    result = {"file": p.name, "library": idx.lib.name, "dry_run": dry_run, "citations_converted": rep["converted"],
              "records_cited": len(rep["records"]), "per_record": {f"{idx.lib.name}:{k}": v for k, v in rep["records"].items()},
              "unmatched": rep["unmatched"], "ambiguous": rep["ambiguous"]}
    if rep["converted"] and not dry_run:
        result["output"] = str(out_path)
        result["next_step"] = "Open the new file in Word and use EndNote > Update Citations and Bibliography."
    elif not rep["converted"]:
        result["message"] = "No plain-text citations matched a record, so no file was written."
    return dict(result, warning=idx.warning) if idx.warning else result


def _authors_csl(authors):
    out = []
    for a in authors:
        fam, _, given = a.partition(",")
        out.append({"family": fam.strip(), "given": given.strip()} if given.strip() else {"literal": a.strip()})
    return out


def _citekeys(recs):
    used, keys = set(), {}
    for rec in recs:
        fam = re.sub(r"[^A-Za-z]", "", rec["author"][0].split(",")[0]) if rec["author"] else ""
        word = next((w.lower() for w in re.findall(r"[A-Za-z]+", rec["title"]) if len(w) > 3), "")
        base = f"{fam or 'anon'}{rec['year'] or 'nd'}{word}"
        k, i = base, 1
        while k in used:
            k = base + ("abcdefghijklmnopqrstuvwxyz"[i - 1] if i <= 26 else str(i))
            i += 1
        used.add(k)
        keys[rec["id"]] = k
    return keys


def _csl(rec, cid):
    out = {"id": cid, "type": CSL_TYPE.get(rec["type"], "document"), "title": rec["title"]}
    if rec["author"]:
        out["author"] = _authors_csl(rec["author"])
    if rec["year"]:
        out["issued"] = {"date-parts": [[int(rec["year"])]]}
    for key, val in (("container-title", rec["journal"]), ("volume", rec["volume"]), ("issue", rec["issue"]),
                     ("page", rec["pages"]), ("DOI", rec["doi"][0] if rec["doi"] else ""),
                     ("URL", rec["url"][0] if rec["url"] else ""), ("abstract", rec["abstract"]),
                     ("keyword", "; ".join(rec["keywords"]))):
        if val:
            out[key] = val
    return out


def _ris(rec):
    ty = RIS_TYPE.get(rec["type"], "GEN")
    lines = [f"TY  - {ty}"] + [f"AU  - {a}" for a in rec["author"]]
    if rec["title"]:
        lines.append(f"TI  - {rec['title']}")
    if rec["journal"]:
        lines.append(f"T2  - {rec['journal']}")
    if rec["year"]:
        lines.append(f"PY  - {rec['year']}")
    if rec["volume"]:
        lines.append(f"VL  - {rec['volume']}")
    if rec["issue"]:
        lines.append(f"IS  - {rec['issue']}")
    if rec["pages"]:
        sp, _, ep = rec["pages"].partition("-")
        lines.append(f"SP  - {sp.strip()}")
        if ep:
            lines.append(f"EP  - {ep.strip()}")
    if rec["doi"]:
        lines.append(f"DO  - {rec['doi'][0]}")
    lines += [f"UR  - {u}" for u in rec["url"]]
    if rec["abstract"]:
        lines.append(f"AB  - {rec['abstract']}")
    lines += [f"KW  - {k}" for k in rec["keywords"]]
    lines.append("ER  - ")
    return "\n".join(lines) + "\n"


def _bibtex(rec, key):
    def esc(v):
        return v.replace("{", "\\{").replace("}", "\\}")
    typ = BIB_TYPE.get(rec["type"], "misc")
    fields = [("author", " and ".join(rec["author"])), ("title", rec["title"]),
              ("journal" if typ == "article" else "booktitle", rec["journal"]), ("year", rec["year"]),
              ("volume", rec["volume"]), ("number", rec["issue"]), ("pages", rec["pages"].replace("-", "--")),
              ("doi", rec["doi"][0] if rec["doi"] else ""), ("url", rec["url"][0] if rec["url"] else ""),
              ("keywords", ", ".join(rec["keywords"])), ("abstract", rec["abstract"])]
    body = ",\n".join(f"  {k} = {{{esc(v)}}}" for k, v in fields if v)
    return f"@{typ}{{{key},\n{body}\n}}\n"


def _records_for(keys, group, library):
    """Records for keys (any library) or for one group of the library. Returns (idx, rec) pairs."""
    if keys:
        return [_resolve(k, library) for k in keys]
    if not group.strip():
        raise LibError("Give item keys, or the name of a group.")
    idx = _index(_pick(library))
    g = _group(idx, group)
    return [(idx, idx.recs[m]) for m in sorted(g["members"]) if m in idx.recs and not idx.recs[m]["trash"]]


@mcp.tool()
def get_bibliography(keys: list[str], style: str = "apa", library: str = "") -> str:
    """Formatted bibliography for item keys with pandoc and citeproc, using a CSL file from the Zotero styles folder
    (for example apa). If the style is not found, returns the CSL-JSON instead."""
    pairs = _records_for(keys, "", library)
    recs = [r for _, r in pairs]
    ids = _citekeys(recs)
    csl_json = [_csl(r, ids[r["id"]]) for r in recs]
    csl = STYLES / f"{style}.csl"
    if not csl.is_file():
        return (f"Style '{style}' was not found in {STYLES}, so here is the CSL-JSON for these records:\n"
                + json.dumps(csl_json, indent=1, ensure_ascii=False))
    pandoc = shutil.which("pandoc")
    if not pandoc:
        return "pandoc is not on PATH, so no formatted bibliography can be made. CSL-JSON:\n" + json.dumps(csl_json, indent=1, ensure_ascii=False)
    WORK.mkdir(parents=True, exist_ok=True)
    bib = WORK / f"bib_{uuid.uuid4().hex}.json"
    bib.write_text(json.dumps(csl_json, ensure_ascii=False), encoding="utf-8")
    md = "---\nnocite: |\n  " + " ".join("@" + ids[r["id"]] for r in recs) + "\n---\n\n::: {#refs}\n:::\n"
    try:
        res = subprocess.run([pandoc, "--citeproc", "--csl", str(csl), "--bibliography", str(bib), "-f", "markdown",
                              "-t", "plain", "--wrap=none"], input=md.encode("utf-8"), capture_output=True, timeout=120)
    finally:
        bib.unlink(missing_ok=True)
    if res.returncode != 0:
        raise LibError("pandoc failed: " + res.stderr.decode("utf-8", "replace")[:500])
    return res.stdout.decode("utf-8", "replace").replace("\r\n", "\n").strip()


@mcp.tool()
def export_items(keys: list[str] = [], group: str = "", fmt: str = "ris", library: str = "") -> dict:
    """Export records as ris, bibtex or csljson. Give keys, or a group name. The file is written to the work folder
    and its text is returned."""
    fmt = fmt.lower()
    if fmt not in ("ris", "bibtex", "csljson"):
        raise LibError("fmt must be ris, bibtex or csljson.")
    pairs = _records_for(keys, group, library)
    recs = [r for _, r in pairs]
    ids = _citekeys(recs)
    if fmt == "ris":
        text = "\n".join(_ris(r) for r in recs)
    elif fmt == "bibtex":
        text = "\n".join(_bibtex(r, ids[r["id"]]) for r in recs)
    else:
        text = json.dumps([_csl(r, ids[r["id"]]) for r in recs], indent=1, ensure_ascii=False)
    WORK.mkdir(parents=True, exist_ok=True)
    ext = {"ris": "ris", "bibtex": "bib", "csljson": "json"}[fmt]
    path = WORK / f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
    path.write_text(text, encoding="utf-8")
    out = {"file": str(path), "records": len(recs), "text": text}
    if pairs and pairs[0][0].warning:
        out["warning"] = pairs[0][0].warning
    return out


def _parse_ris(text):
    """Records from RIS text, each with the raw lines of its block so new records can be written back unchanged."""
    recs, blocks, cur, block = [], [], {}, []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        m = re.match(r"^([A-Z][A-Z0-9])  - ?(.*)$", line)
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        block.append(line)
        if tag == "ER":
            recs.append(cur)
            blocks.append("\n".join(block) + "\n")
            cur, block = {}, []
        else:
            cur.setdefault(tag, []).append(val)
    if cur:
        recs.append(cur)
        blocks.append("\n".join(block) + "\n")
    out = []
    for r, raw in zip(recs, blocks):
        title = (r.get("TI") or r.get("T1") or [""])[0]
        year = next((re.search(r"\d{4}", v).group(0) for t in ("PY", "Y1", "DA") for v in r.get(t, []) if re.search(r"\d{4}", v)), "")
        dois = [_clean_doi(v) for v in r.get("DO", [])]
        dois += [_clean_doi(v) for v in r.get("N1", []) + r.get("UR", []) if DOI_RE.match(_clean_doi(v))]
        out.append({"title": title, "year": year, "ntitle": _norm(title),
                    "authors": r.get("AU", []) + r.get("A1", []), "journal": (r.get("T2") or r.get("JO") or [""])[0],
                    "doi": list(dict.fromkeys(d for d in dois if DOI_RE.match(d))), "raw": raw})
    return out


@mcp.tool()
def preview_import(ris_text: str = "", path: str = "", library: str = "") -> dict:
    """Read-only check of an RIS file against a library: records with the same DOI, or the same title and year,
    are marked as duplicates. New records are written to a fresh RIS file for File > Import in EndNote.
    Give ris_text, or path to a file. The library is never changed."""
    idx = _index(_pick(library))
    if path.strip():
        p = Path(path.strip())
        if p.suffix.lower() not in (".ris", ".txt"):
            raise LibError("preview_import reads only .ris or .txt files. Export the references from your source as RIS.")
        if not p.is_file():
            raise LibError(f"RIS file not found: {p}")
        if p.stat().st_size > MAX_IMPORT_BYTES:
            raise LibError(f"That file is too large (limit {MAX_IMPORT_BYTES // 1_000_000} MB). Split it into smaller RIS files.")
        ris_text = p.read_text(encoding="utf-8-sig", errors="replace")
    if len(ris_text.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise LibError(f"That RIS text is too large (limit {MAX_IMPORT_BYTES // 1_000_000} MB). Split it into smaller parts.")
    if not ris_text.strip():
        raise LibError("Give ris_text, or a path to an RIS file.")
    return _preview_ris(idx, ris_text)


def _preview_ris(idx, ris_text):
    parsed = _parse_ris(ris_text)
    if not parsed:
        raise LibError("No RIS records found. RIS lines look like 'TY  - JOUR' (two spaces before the dash) "
                       "and each record ends with 'ER  - '.")
    rows, new_blocks = [], []
    for n, rec in enumerate(parsed, 1):
        matches = []
        for d in rec["doi"]:
            matches += [(i, "same DOI") for i in idx.doi_map.get(d, []) if not idx.recs[i]["trash"]]
        if not matches and rec["ntitle"] and len(rec["ntitle"]) > 15:
            matches += [(i, "same title and year") for i in idx.title_year.get((rec["ntitle"], rec["year"]), []) if not idx.recs[i]["trash"]]
        first = rec["authors"][0].split(",")[0] if rec["authors"] else "?"
        row = {"n": n, "authors": first + (" et al." if len(rec["authors"]) > 2 else ""), "year": rec["year"],
               "title": rec["title"], "journal": rec["journal"], "doi": rec["doi"][0] if rec["doi"] else "",
               "status": "DUPLICATE" if matches else "NEW",
               "matches": [{"key": _key(idx, idx.recs[i]), "why": why, "title": idx.recs[i]["title"]} for i, why in matches[:3]]}
        rows.append(row)
        if not matches:
            new_blocks.append(rec["raw"])
    written = None
    if new_blocks:
        WORK.mkdir(parents=True, exist_ok=True)
        written = WORK / f"new_records_{datetime.now().strftime('%Y%m%d_%H%M%S')}.ris"
        written.write_text("\n".join(new_blocks), encoding="utf-8")
    out = {"library": idx.lib.name, "records": len(rows), "new": sum(1 for r in rows if r["status"] == "NEW"),
           "duplicates": sum(1 for r in rows if r["status"] != "NEW"), "rows": rows,
           "file_for_import": str(written) if written else None,
           "import_steps": "In EndNote: File > Import, choose the file above, and set the filter to RIS. The server never imports."}
    return _wrap(idx, out) if idx.warning else out


# ---------- online lookups (Crossref, OpenAlex) ----------

def _online_source(source):
    s = (source or "").strip().lower()
    if s in ("crossref", "openalex"):
        return s
    if s == "scopus":
        raise LibError("Scopus is not set up. Its API needs an Elsevier API key, which this server does not have. "
                       "Use source='crossref' or 'openalex'.")
    if "scholar" in s:
        raise LibError("Google Scholar has no official API and forbids automated queries, so it is not used. "
                       "source='openalex' gives cited-by and reference lists, and source='crossref' gives publisher metadata.")
    raise LibError("source must be crossref or openalex.")


def _library_status(idx, meta):
    """('IN LIBRARY' | 'NEW', keys) for one online record, by DOI and then by title and year."""
    ids = [i for d in ([meta["doi"]] if meta.get("doi") else []) for i in idx.doi_map.get(d, []) if not idx.recs[i]["trash"]]
    nt = _norm(meta.get("title", ""))
    if not ids and len(nt) > 15:
        ids = [i for i in idx.title_year.get((nt, meta.get("year", "")), []) if not idx.recs[i]["trash"]]
    return ("IN LIBRARY" if ids else "NEW"), [f"{idx.lib.name}:{i}" for i in ids]


def _online_row(idx, m):
    status, keys = _library_status(idx, m)
    return {"doi": m["doi"], "title": m["title"], "authors": _short_authors(m["authors"]), "year": m["year"], "journal": m["journal"],
            "cited_by_count": m["cited_by_count"], "status": status, "in_library": keys}


def _online_fetch(source, doi):
    return online.crossref_work(doi) if source == "crossref" else online.openalex_work(doi)


@mcp.tool()
def get_online_record(doi: str, source: str = "crossref", library: str = "") -> dict:
    """Full metadata for one DOI from Crossref (publisher data) or OpenAlex (also keywords and citation counts), and
    whether the record is already in the library. Sends only the DOI to that service. Nothing is saved."""
    src = _online_source(source)
    idx = _index(_pick(library))
    meta = _online_fetch(src, doi)
    status, keys = _library_status(idx, meta)
    return {"source": meta["source"], "record": meta, "status": status, "in_library": keys}


@mcp.tool()
def search_online(query: str, source: str = "crossref", limit: int = 10, library: str = "") -> dict:
    """Searches Crossref or OpenAlex by title, author or topic words and marks each result IN LIBRARY or NEW. Sends only
    the search words to that service. Use import_online with the DOIs you want."""
    src = _online_source(source)
    _limit(limit)
    q = (query or "").strip()
    if not q:
        raise LibError("Give search words, for example a title or an author and a topic.")
    if len(q) > 300:
        raise LibError("Search words are limited to 300 characters.")
    idx = _index(_pick(library))
    n = min(limit, 100)
    found = online.crossref_search(q, n) if src == "crossref" else online.openalex_search(q, n)
    return {"source": "Crossref" if src == "crossref" else "OpenAlex", "query": q, "results": [_online_row(idx, m) for m in found]}


@mcp.tool()
def online_citations(doi: str, direction: str = "references", source: str = "openalex", limit: int = 25, library: str = "") -> dict:
    """The works a paper cites (direction='references') or the works that cite it (direction='cited_by'), each marked
    IN LIBRARY or NEW. OpenAlex gives both, with full details. Crossref gives only the reference list the publisher
    deposited, and many entries have no DOI (status NO DOI) so they cannot be imported. Sends only the DOI."""
    if direction not in ("references", "cited_by"):
        raise LibError("direction must be references or cited_by.")
    src = _online_source(source)
    _limit(limit)
    if src == "crossref" and direction == "cited_by":
        raise LibError("Crossref does not list the works that cite a paper. Use OpenAlex (source='openalex') for cited_by.")
    idx = _index(_pick(library))
    n = min(limit, 200)
    if src == "crossref":
        refs = online.crossref_references(online.crossref_raw(doi))
        rows = []
        for r in refs[:n]:
            status, keys = _library_status(idx, r) if r["doi"] else ("NO DOI", [])
            rows.append(dict(r, status=status, in_library=keys))
        total = len(refs)
    else:
        found, total = online.openalex_related(doi, direction, n)
        rows = [_online_row(idx, m) for m in found]
    return {"doi": online.clean_doi(doi), "direction": direction, "source": "Crossref" if src == "crossref" else "OpenAlex",
            "total": total, "shown": len(rows), "truncated": total > len(rows), "results": rows}


@mcp.tool()
def import_online(dois: list[str], source: str = "crossref", library: str = "") -> dict:
    """Fetches the metadata for up to 50 DOIs from Crossref or OpenAlex, marks each NEW or DUPLICATE against the library,
    and writes only the new ones to a RIS file for File > Import in EndNote. The server never imports and never changes the
    library. DOIs that could not be fetched are listed under failed. Sends only the DOIs."""
    src = _online_source(source)
    todo = [str(d).strip() for d in dois if str(d).strip()]
    if not todo:
        raise LibError("Give at least one DOI.")
    if len(todo) > 50:
        raise LibError("Give at most 50 DOIs per call.")
    idx = _index(_pick(library))
    ris, failed = [], []
    for d in todo:
        try:
            ris.append(online.to_ris(_online_fetch(src, d)))
        except LibError as e:
            failed.append({"doi": d, "reason": str(e)})
    if not ris:
        raise LibError("None of the DOIs could be fetched: " + "; ".join(f"{f['doi']}: {f['reason']}" for f in failed[:5]))
    out = _preview_ris(idx, "\n".join(ris))
    out = out["results"] if "results" in out and "warning" in out else out
    return dict(out, source="Crossref" if src == "crossref" else "OpenAlex", failed=failed)


if __name__ == "__main__":
    mcp.run()
