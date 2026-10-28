"""Turn plain-text author-year citations in a .docx into EndNote (EN.CITE) fields.

Pure functions: the server passes in a lookup into the library. Nothing here touches a library, and the input
file is never changed; the result is written to a new file.
"""
import copy
import re
import zipfile
from xml.sax.saxutils import escape

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XMLNS = "http://www.w3.org/XML/1998/namespace"
NS = {"w": W}

# EndNote's XML ref-type codes (not the library's internal codes)
XML_TYPE = {0: ("Journal Article", 17), 1: ("Book", 6), 7: ("Book Section", 5), 3: ("Conference Proceedings", 10),
            10: ("Report", 27), 16: ("Web Page", 12), 2: ("Thesis", 32), 48: ("Dataset", 59), 8: ("Magazine Article", 19),
            5: ("Newspaper Article", 23), 9: ("Edited Book", 28), 31: ("Generic", 13)}

WORD = r"[A-Z][\w'’\-]+"
PHRASE = rf"{WORD}(?: {WORD})?(?: et al\.| (?:&|and) {WORD}(?: {WORD})?)?"
UNIT = re.compile(rf"(?P<name>{PHRASE}),\s*(?P<year>\d{{4}})(?![\d\w])")
REF_ENTRY = re.compile(r"^[A-Z][^()]{1,250}\(\d{4}[a-z]?\)\.")


def q(tag):
    return "{%s}%s" % (W, tag)


def surname(a):
    return a.split(",")[0].strip()


def phrase(rec, joiner="&"):
    a = rec["author"]
    if not a:
        return "Anon."
    if len(a) == 1:
        return surname(a[0])
    if len(a) == 2:
        return f"{surname(a[0])} {joiner} {surname(a[1])}"
    return f"{surname(a[0])} et al."


def display(rec, exclude_author=False):
    y = rec["year"] or "n.d."
    return y if exclude_author else f"{phrase(rec)}, {y}"


def record_xml(rec, db_id, ts):
    name, code = XML_TYPE.get(rec["type"], ("Generic", 13))
    row = rec.get("row", {})
    x = [f'<record><rec-number>{rec["id"]}</rec-number><foreign-keys><key app="EN" db-id="{escape(db_id or "")}" '
         f'timestamp="{ts}">{rec["id"]}</key></foreign-keys><ref-type name="{name}">{code}</ref-type>'
         "<contributors><authors>"]
    x += [f"<author>{escape(a)}</author>" for a in rec["author"]]
    x.append(f'</authors></contributors><titles><title>{escape(rec["title"])}</title>')
    if rec["journal"]:
        x.append(f'<secondary-title>{escape(rec["journal"])}</secondary-title>')
    x.append("</titles>")
    if rec["journal"]:
        x.append(f'<periodical><full-title>{escape(rec["journal"])}</full-title></periodical>')
    for tag, val in (("pages", rec["pages"]), ("volume", rec["volume"]), ("number", rec["issue"])):
        if val:
            x.append(f"<{tag}>{escape(val)}</{tag}>")
    if rec["year"]:
        x.append(f'<dates><year>{rec["year"]}</year></dates>')
    if row.get("publisher"):
        x.append(f'<publisher>{escape(row["publisher"])}</publisher>')
    x.append("<urls>")
    if rec["url"]:
        x.append(f'<related-urls><url>{escape(rec["url"][0])}</url></related-urls>')
    x.append("</urls>")
    if rec["doi"]:
        x.append(f'<electronic-resource-num>{escape(rec["doi"][0])}</electronic-resource-num>')
    x.append("</record>")
    return "".join(x)


def cite_xml(recs, db_id, exclude_author=False, ts="0"):
    recs = sorted(recs, key=lambda r: (surname(r["author"][0]).lower() if r["author"] else "", r["year"]))
    disp = f"({display(recs[0], True)})" if exclude_author else "(" + "; ".join(display(r) for r in recs) + ")"
    out = ["<EndNote>"]
    for k, r in enumerate(recs):
        attr = ' ExcludeAuth="1"' if exclude_author else ""
        yr = f'<Year>{r["year"]}</Year>' if r["year"] else ""
        dt = f"<DisplayText>{escape(disp)}</DisplayText>" if k == 0 else ""
        au = surname(r["author"][0]) if r["author"] else ""
        out.append(f"<Cite{attr}><Author>{escape(au)}</Author>{yr}<RecNum>{r['id']}</RecNum>{dt}{record_xml(r, db_id, ts)}</Cite>")
    out.append("</EndNote>")
    return "".join(out), disp


def _mk_run(rpr, kind, text=None, noproof=False):
    r = etree.Element(q("r"))
    if rpr is not None:
        r.append(copy.deepcopy(rpr))
    if noproof:
        rp = r.find("w:rPr", NS)
        if rp is None:
            rp = etree.Element(q("rPr"))
            r.insert(0, rp)
        later = {"snapToGrid", "vanish", "webHidden", "color", "spacing", "w", "kern", "position", "sz", "szCs",
                 "highlight", "u", "effect", "bdr", "shd", "fitText", "vertAlign", "rtl", "cs", "em", "lang"}
        pos = next((i for i, k in enumerate(rp) if etree.QName(k).localname in later), len(rp))
        rp.insert(pos, etree.Element(q("noProof")))
    if kind in ("begin", "separate", "end"):
        etree.SubElement(r, q("fldChar")).set(q("fldCharType"), kind)
    elif kind == "instr":
        e = etree.SubElement(r, q("instrText"))
        e.set("{%s}space" % XMLNS, "preserve")
        e.text = text
    else:
        e = etree.SubElement(r, q("t"))
        e.set("{%s}space" % XMLNS, "preserve")
        e.text = text
    return r


def _field(recs, db_id, rpr, exclude_author, ts):
    xml, disp = cite_xml(recs, db_id, exclude_author, ts)
    return [_mk_run(rpr, "begin"), _mk_run(rpr, "instr", " ADDIN EN.CITE " + xml), _mk_run(rpr, "separate"),
            _mk_run(rpr, "text", disp, noproof=True), _mk_run(rpr, "end")]


def _text(run):
    return "".join(t.text or "" for t in run.findall("w:t", NS))


def _plain(run):
    kids = [k for k in run if k.tag != q("rPr")]
    return len(kids) == 1 and kids[0].tag == q("t")


def _trailing(out):
    s = ""
    for el in reversed(out):
        if el.tag == q("r") and _plain(el):
            s = _text(el) + s
        else:
            break
    return s


def _units(content):
    """[(name, year)] if the bracket holds only author-year units separated by ; or 'and', else None."""
    found = [(m.group("name"), m.group("year")) for m in UNIT.finditer(content)]
    rest = UNIT.sub("", content)
    if not found or not re.fullmatch(r"(\s|;|and)*", rest):
        return None
    return found


def convert(path_in, path_out, find, db_id, ts="0", dry_run=False):
    """find(name_phrase, year) -> list of library records. Returns a report; writes path_out unless dry_run."""
    with zipfile.ZipFile(path_in) as z:
        names = z.namelist()
        xml = z.read("word/document.xml")
    root = etree.fromstring(xml)
    body = root.find("w:body", NS)
    report = {"converted": 0, "records": {}, "unmatched": [], "ambiguous": []}
    seen = set()

    def resolve(name, year):
        hits = find(name, year)
        if len(hits) == 1:
            return hits[0]
        key = (name, year)
        if key not in seen:
            seen.add(key)
            (report["ambiguous"] if hits else report["unmatched"]).append(
                f"{name}, {year}" + (": " + ", ".join(f'{h["id"]}' for h in hits[:5]) if hits else ""))
        return None

    def name_before(prev, rec):
        return any(prev.endswith(phrase(rec, j) + " ") for j in ("&", "and"))

    for p in list(body.iter(q("p"))):
        st = p.find("w:pPr/w:pStyle", NS)
        if st is not None and st.get(q("val")) == "EndNoteBibliography":
            continue
        if REF_ENTRY.match("".join(p.itertext()).strip()):
            continue
        children, out, i, changed = list(p), [], 0, False
        while i < len(children):
            ch = children[i]
            fc = ch.find("w:fldChar", NS) if ch.tag == q("r") else None
            if fc is not None and fc.get(q("fldCharType")) == "begin":
                depth, j = 0, i
                while j < len(children):
                    c = children[j]
                    f = c.find("w:fldChar", NS) if c.tag == q("r") else None
                    if f is not None:
                        depth += {"begin": 1, "end": -1}.get(f.get(q("fldCharType")), 0)
                    j += 1
                    if depth == 0:
                        break
                out.extend(children[i:j])
                i = j
                continue
            if ch.tag == q("r") and _plain(ch):
                txt, rpr = _text(ch), ch.find("w:rPr", NS)
                pieces, pos, base = [], 0, _trailing(out)
                for m in re.finditer(r"\(([^()]*)\)", txt):
                    content, before = m.group(1), base + txt[:m.start()]
                    recs, excl = None, False
                    if re.fullmatch(r"\d{4}", content):
                        mm = re.search(rf"({PHRASE}) $", before)
                        if mm:
                            r = resolve(mm.group(1), content)
                            if r and name_before(before, r):
                                recs, excl = [r], True
                    else:
                        us = _units(content)
                        if us:
                            got = [resolve(n, y) for n, y in us]
                            if all(got):
                                recs = got
                                excl = len(got) == 1 and name_before(before, got[0])
                    if not recs:
                        continue
                    pieces += [("t", txt[pos:m.start()]), ("f", recs, excl)]
                    pos = m.end()
                    for r in recs:
                        report["records"][r["id"]] = report["records"].get(r["id"], 0) + 1
                    report["converted"] += 1
                if pieces:
                    pieces.append(("t", txt[pos:]))
                    for pc in pieces:
                        if pc[0] == "t":
                            if pc[1]:
                                out.append(_mk_run(rpr, "text", pc[1]))
                        else:
                            out.extend(_field(pc[1], db_id, rpr, pc[2], ts))
                    changed = True
                else:
                    out.append(ch)
                i += 1
                continue
            out.append(ch)
            i += 1
        if changed:
            for c in children:
                p.remove(c)
            for c in out:
                p.append(c)
    report["records"] = {str(k): v for k, v in sorted(report["records"].items())}
    if not dry_run and report["converted"]:
        data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        with zipfile.ZipFile(path_in) as zin, zipfile.ZipFile(path_out, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                zout.writestr(item, data if item.filename == "word/document.xml" else zin.read(item.filename))
    return report
