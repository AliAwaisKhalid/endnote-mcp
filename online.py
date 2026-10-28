"""Crossref and OpenAlex lookups for endnote-mcp. Sends only DOIs and search words, to two fixed hosts over https.
Set ENDNOTE_MCP_OFFLINE=1 to turn every online call off. Set ENDNOTE_MCP_MAILTO to identify yourself to the
providers' polite pools (optional)."""
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

from errors import LibError

ALLOWED_HOSTS = {"api.crossref.org", "api.openalex.org"}
MAX_BYTES = 8_000_000
DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
CROSSREF_FIELDS = ("DOI,title,subtitle,author,issued,published-print,published-online,container-title,volume,issue,page,type,"
                   "publisher,ISSN,URL,abstract,is-referenced-by-count,references-count,subject")
RIS_TYPE = {"journal-article": "JOUR", "book": "BOOK", "book-chapter": "CHAP", "proceedings-article": "CONF",
            "report": "RPRT", "dissertation": "THES", "posted-content": "UNPB"}
OPENALEX_TYPE = {"article": "journal-article", "review": "journal-article", "book": "book", "book-chapter": "book-chapter",
                 "dissertation": "dissertation", "preprint": "posted-content", "report": "report"}


def _require_online():
    if os.environ.get("ENDNOTE_MCP_OFFLINE", "").strip() not in ("", "0"):
        raise LibError("Online lookups are switched off (ENDNOTE_MCP_OFFLINE is set). Unset it to use Crossref or OpenAlex.")


def check_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or p.hostname not in ALLOWED_HOSTS or p.username or p.password or p.port not in (None, 443):
        raise LibError(f"'{url[:80]}' is not an allowed address. Only Crossref and OpenAlex are contacted.")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _open(url, timeout):
    mail = os.environ.get("ENDNOTE_MCP_MAILTO", "").strip()
    agent = "endnote-mcp/1.0" + (f" (mailto:{mail})" if mail else "")
    req = urllib.request.Request(url, headers={"User-Agent": agent, "Accept": "application/json"})
    return urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout)


def get_json(url, timeout=20):
    _require_online()
    check_url(url)
    host = urllib.parse.urlsplit(url).hostname
    try:
        with _open(url, timeout) as r:
            raw = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise LibError("HTTP 404: not found")
        if e.code == 429:
            raise LibError(f"{host} says too many requests (HTTP 429). Wait a minute and try again.")
        raise LibError(f"{host} returned HTTP {e.code}.")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LibError(f"Cannot reach {host}: {getattr(e, 'reason', e)}. Check the internet connection.")
    if len(raw) > MAX_BYTES:
        raise LibError(f"The answer from {host} was too large to read.")
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise LibError(f"{host} sent an answer that is not JSON.")


fetch = get_json  # replaced in tests


def clean_doi(s):
    s = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", (s or "").strip(), flags=re.I)
    return re.sub(r"^doi:\s*", "", s, flags=re.I).strip().lower()


def valid_doi(s):
    d = clean_doi(s)
    if not DOI_RE.match(d):
        raise LibError(f"'{str(s)[:60]}' is not a valid DOI. A DOI looks like 10.1016/j.eneco.2021.105118.")
    return d


def _strip_tags(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _year(date_parts):
    try:
        return str(date_parts["date-parts"][0][0])
    except (KeyError, IndexError, TypeError):
        return ""


def from_crossref(m):
    title = (m.get("title") or [""])[0]
    sub = (m.get("subtitle") or [""])[0]
    if sub and sub.lower() not in title.lower():
        title = f"{title}: {sub}"
    authors = []
    for a in m.get("author") or []:
        if a.get("family"):
            authors.append(f"{a['family']}, {a['given']}" if a.get("given") else a["family"])
        elif a.get("name"):
            authors.append(a["name"] + ",")
    doi = clean_doi(m.get("DOI", ""))
    year = next((y for y in (_year(m.get(k)) for k in ("issued", "published-print", "published-online", "published")) if y), "")
    return {"source": "Crossref", "doi": doi, "title": title, "authors": authors, "year": year,
            "journal": (m.get("container-title") or [""])[0], "volume": str(m.get("volume") or ""), "issue": str(m.get("issue") or ""),
            "pages": str(m.get("page") or ""), "type": m.get("type") or "", "publisher": m.get("publisher") or "",
            "issn": (m.get("ISSN") or [""])[0], "abstract": _strip_tags(m.get("abstract")), "keywords": list(m.get("subject") or [])[:8],
            "cited_by_count": m.get("is-referenced-by-count"), "references_count": m.get("references-count"),
            "url": f"https://doi.org/{doi}" if doi else (m.get("URL") or "")}


def _person(name):
    parts = (name or "").split()
    return f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else (name or "")


def from_openalex(w):
    doi = clean_doi(w.get("doi") or "")
    b = w.get("biblio") or {}
    pages = "-".join(x for x in (b.get("first_page"), b.get("last_page")) if x)
    src = ((w.get("primary_location") or {}).get("source")) or {}
    inv = w.get("abstract_inverted_index") or {}
    words = sorted((pos, word) for word, poss in inv.items() for pos in poss)
    wid = (w.get("id") or "").rsplit("/", 1)[-1]
    return {"source": "OpenAlex", "doi": doi, "title": w.get("title") or "",
            "authors": [_person(a.get("author", {}).get("display_name")) for a in w.get("authorships") or []
                        if a.get("author", {}).get("display_name")],
            "year": str(w.get("publication_year") or ""), "journal": src.get("display_name") or "",
            "volume": str(b.get("volume") or ""), "issue": str(b.get("issue") or ""), "pages": pages,
            "type": OPENALEX_TYPE.get(w.get("type") or "", w.get("type") or ""), "publisher": src.get("host_organization_name") or "",
            "issn": src.get("issn_l") or "", "abstract": " ".join(word for _, word in words),
            "keywords": [k["display_name"] for k in (w.get("keywords") or [])[:8] if k.get("display_name")],
            "cited_by_count": w.get("cited_by_count"), "references_count": w.get("referenced_works_count"),
            "url": f"https://doi.org/{doi}" if doi else (w.get("id") or ""), "openalex_id": wid}


def _line(tag, value):
    v = re.sub(r"\s+", " ", str(value)).strip()
    v = re.sub(r"\b([A-Z][A-Z0-9])  - ", r"\1 - ", v)  # a value can never start a new RIS tag
    return f"{tag}  - {v}" if v else None


def to_ris(m):
    rows = [f"TY  - {RIS_TYPE.get(m.get('type'), 'GEN')}"]
    rows += [_line("AU", a) for a in m.get("authors", [])]
    pages = [p for p in re.split(r"\s*[-–—]\s*", m.get("pages") or "") if p]
    rows += [_line("PY", m.get("year")), _line("TI", m.get("title")), _line("T2", m.get("journal")), _line("VL", m.get("volume")),
             _line("IS", m.get("issue")), _line("SP", pages[0] if pages else ""), _line("EP", pages[1] if len(pages) > 1 else ""),
             _line("DO", m.get("doi")), _line("UR", m.get("url")), _line("AB", m.get("abstract")), _line("PB", m.get("publisher")),
             _line("SN", m.get("issn"))]
    rows += [_line("KW", k) for k in m.get("keywords", [])]
    rows.append(_line("N1", f"Imported from {m.get('source', 'online')} on {date.today().isoformat()} via endnote-mcp"))
    return "\n".join([r for r in rows if r] + ["ER  - "]) + "\n"


def _doi_path(doi):
    return urllib.parse.quote(doi, safe="/")


def crossref_raw(doi):
    _require_online()
    d = valid_doi(doi)
    try:
        body = fetch(f"https://api.crossref.org/works/{_doi_path(d)}")
    except LibError as e:
        if "404" in str(e):
            raise LibError(f"No record with DOI {d} at Crossref.")
        raise
    if not isinstance(body, dict) or "message" not in body:
        raise LibError("Crossref sent an answer in an unexpected shape.")
    return body["message"]


def crossref_work(doi):
    return from_crossref(crossref_raw(doi))


def crossref_search(query, limit):
    _require_online()
    q = urllib.parse.urlencode({"query.bibliographic": query, "rows": limit, "select": CROSSREF_FIELDS})
    items = (fetch("https://api.crossref.org/works?" + q).get("message") or {}).get("items") or []
    return [from_crossref(i) for i in items[:limit]]


def crossref_references(message):
    out = []
    for r in message.get("reference") or []:
        d = clean_doi(r.get("DOI", ""))
        out.append({"doi": d if DOI_RE.match(d) else "", "title": r.get("article-title") or r.get("volume-title") or "",
                    "authors": r.get("author") or "", "year": str(r.get("year") or ""), "journal": r.get("journal-title") or "",
                    "unstructured": r.get("unstructured") or ""})
    return out


def openalex_raw(doi):
    _require_online()
    d = valid_doi(doi)
    try:
        return fetch(f"https://api.openalex.org/works/doi:{_doi_path(d)}")
    except LibError as e:
        if "404" in str(e):
            raise LibError(f"No record with DOI {d} at OpenAlex.")
        raise


def openalex_work(doi):
    return from_openalex(openalex_raw(doi))


def openalex_search(query, limit):
    _require_online()
    q = urllib.parse.urlencode({"search": query, "per-page": limit})
    return [from_openalex(w) for w in (fetch("https://api.openalex.org/works?" + q).get("results") or [])[:limit]]


def openalex_related(doi, kind, limit):
    """('cited_by' | 'references') works of a DOI, and how many OpenAlex holds in total."""
    wid = (openalex_raw(doi).get("id") or "").rsplit("/", 1)[-1]
    if not wid:
        raise LibError("OpenAlex has no record id for that DOI.")
    flt = f"cites:{wid}" if kind == "cited_by" else f"cited_by:{wid}"
    q = urllib.parse.urlencode({"filter": flt, "per-page": min(limit, 200)}, safe=":")
    body = fetch("https://api.openalex.org/works?" + q)
    return [from_openalex(w) for w in (body.get("results") or [])[:limit]], int((body.get("meta") or {}).get("count") or 0)
