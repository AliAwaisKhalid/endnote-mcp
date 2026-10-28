"""Calls every endnote tool through a real MCP stdio client and reports pass or fail with the time per call.

Run: python test_mcp_client.py
Libraries come from ENDNOTE_LIBRARIES below. The test also records the size and modified time of each
library file before and after the run, so you can see that nothing changed.
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

logging.disable(logging.CRITICAL)
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

HERE = Path(__file__).resolve().parent
SERVER = str(HERE / "server.py")
SCRATCH = Path(os.environ.get("ENDNOTE_TEST_SCRATCH", r"C:\Users\aliaw\AppData\Local\Temp\claude\J--Claude\7130a134-3833-491f-b7d4-34ab225d2daa\scratchpad"))  # holds the vendor-sample test library

LIBRARIES = [
    r"G:\Completed Projects\Sports_Carbon_Energy\References\Sports.enl",
    r"G:\Research\Funding Liquidity Risk\FUNDLIQ\References\Funding_Liquidity.enl",
    r"G:\Research\Funding Liquidity Risk\References\Funding Liquidity_Library.enl",
    r"G:\Completed Projects\Wavelet_Leen\References\Commodities-Converted Copy.enl",
    r"G:\Completed Projects\Banks\European_Banks\Articles\References_CTS.enl",
]
SAMPLE = SCRATCH / "test_libs" / "Sample_Library_2025.enl"
if SAMPLE.is_file():
    LIBRARIES.append(str(SAMPLE))

WATCHED = [
    r"G:\Completed Projects\Sports_Carbon_Energy\References\Sports.enl",
    r"G:\Completed Projects\Sports_Carbon_Energy\References\Sports.Data\sdb\sdb.eni",
    r"G:\Research\Funding Liquidity Risk\FUNDLIQ\References\Funding_Liquidity.enl",
    r"G:\Research\Funding Liquidity Risk\FUNDLIQ\References\Funding_Liquidity.Data\sdb\sdb.eni",
    r"G:\Research\Funding Liquidity Risk\References\Funding Liquidity_Library.Data\sdb\sdb.eni",
    r"G:\Completed Projects\Wavelet_Leen\References\Commodities-Converted Copy.Data\sdb\sdb.eni",
]


def stats():
    out = {}
    for p in WATCHED:
        try:
            s = os.stat(p)
            out[p] = (s.st_size, datetime.fromtimestamp(s.st_mtime).strftime("%Y-%m-%d %H:%M:%S"))
        except OSError as e:
            out[p] = ("missing", str(e)[:60])
    return out


def parse(txt):
    try:
        return json.loads(txt)
    except ValueError:
        return txt


results = []


async def run(s, name, args, check, expect_error=False):
    t = time.time()
    try:
        res = await asyncio.wait_for(s.call_tool(name, args), timeout=300)
        blocks = [c.text for c in res.content if getattr(c, "text", None)]
        is_err = bool(res.isError)
    except Exception as e:  # transport-level failure
        blocks, is_err = [repr(e)], True
    dt = time.time() - t
    # FastMCP sends one text block per list element, and no block for an empty list.
    txt = "\n".join(blocks)
    if len(blocks) == 0:
        data = []
    elif len(blocks) == 1:
        data = parse(blocks[0])
    else:
        data = [parse(b) for b in blocks]
    if expect_error:
        ok = is_err and check(txt)
    else:
        ok = (not is_err) and check(data)
    results.append((name, ok, dt))
    short = txt.replace("\n", " ")[:150]
    print(("PASS" if ok else "FAIL"), f"{name:<20} {dt:6.2f}s  {json.dumps(args, ensure_ascii=False)[:60]:<60} | {short}", flush=True)


async def main():
    before = stats()
    env = dict(os.environ, ENDNOTE_LIBRARIES=";".join(LIBRARIES))
    params = StdioServerParameters(command=sys.executable, args=[SERVER], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = sorted(t.name for t in (await s.list_tools()).tools)
            expected = sorted(["list_libraries", "search_items", "find_by_identifier", "get_item", "get_notes", "get_annotations",
                               "get_fulltext", "list_collections", "collection_items", "list_tags", "recent_items",
                               "find_duplicates", "list_by_status", "method_matrix", "get_bibliography", "export_items",
                               "preview_import", "check_records", "check_docx_citations", "missing_pdfs",
                               "export_annotations", "compare_libraries", "watch_library", "get_online_record", "search_online",
                               "online_citations", "import_online"])
            print(len(names), "tools over MCP. All 27 expected present:", names == expected, flush=True)

            def has(*words):
                return lambda d: all(w.lower() in json.dumps(d, ensure_ascii=False).lower() for w in words)

            def is_list_min(n, key=None):
                def f(d):
                    if isinstance(d, dict) and "results" in d:
                        d = d["results"]
                    if isinstance(d, dict) and key:
                        d = d.get(key, [])
                    if isinstance(d, dict) and "statuses" not in d and "rows" not in d:
                        d = [d]  # a single record comes back as one object
                    return isinstance(d, list) and len(d) >= n
                return f

            await run(s, "list_libraries", {}, lambda d: isinstance(d, list) and len(d) >= 5
                      and sum(1 for x in d if x.get("status") == "ok") >= 4)
            await run(s, "search_items", {"query": "Yun", "library": "Sports", "limit": 3}, is_list_min(1))
            await run(s, "search_items", {"query": "liquidity", "library": "Funding Liquidity_Library", "fulltext": True, "limit": 5},
                      is_list_min(1))
            await run(s, "search_items", {"query": "cross", "library": "Commodities-Converted Copy", "limit": 5}, is_list_min(1))
            await run(s, "find_by_identifier", {"identifier": "10.1186/s13102-024-00914-5", "library": "Sports"},
                      has("Sports:1", "DOI exact"))
            await run(s, "find_by_identifier", {"identifier": "Another Market Segment: Sport Event Tourism by Disabled Athletes",
                                                "library": "Sports"}, has("Sports:1", "title exact"))
            await run(s, "find_by_identifier", {"identifier": "Sports:1"}, has("Sports:1"))
            await run(s, "get_item", {"key": "Sports:1"}, has("Sports:1", "Yun"))
            await run(s, "get_item", {"key": "Sports:999999"}, lambda t: "No record" in t, expect_error=True)
            await run(s, "get_item", {"key": "ZZZZZZZZ"}, lambda t: "Unknown item key" in t, expect_error=True)
            if SAMPLE.is_file():
                await run(s, "get_notes", {"query": "Times Cited", "library": "Sample_Library_2025", "limit": 2}, is_list_min(1))
                await run(s, "get_annotations", {"key": "Funding Liquidity_Library:1"}, has("Highlight", "highlighted_text"))
                await run(s, "list_tags", {"kind": "colour", "library": "Sample_Library_2025"}, is_list_min(5))
                await run(s, "collection_items", {"group": "Corvids", "library": "Sample_Library_2025", "limit": 100}, is_list_min(24))
                await run(s, "list_by_status", {"prefix": "son", "library": "Sample_Library_2025"}, has("sonar"))
                await run(s, "export_items", {"group": "Corvids", "fmt": "bibtex", "library": "Sample_Library_2025"}, has("@", "Corvids"))
            await run(s, "get_notes", {"key": "Sports:1"}, lambda d: isinstance(d, list))
            await run(s, "get_fulltext", {"key": "Funding Liquidity_Library:1", "max_chars": 500}, has("Journal"))
            await run(s, "get_fulltext", {"key": "Sports:1"}, lambda t: "No PDF is attached" in t, expect_error=True)
            await run(s, "list_collections", {"library": "Funding Liquidity_Library"}, is_list_min(4))
            await run(s, "list_collections", {"library": "Commodities-Converted Copy"}, is_list_min(1))
            await run(s, "collection_items", {"group": "PubMed (NLM)", "library": "Funding Liquidity_Library"}, lambda d: isinstance(d, list) or isinstance(d, dict))
            await run(s, "list_tags", {"library": "Funding Liquidity_Library", "limit": 5}, lambda d: isinstance(d, list))
            await run(s, "recent_items", {"limit": 3, "library": "Sports"}, is_list_min(3))
            await run(s, "recent_items", {"limit": 3, "sort": "updated", "library": "Commodities-Converted Copy"}, is_list_min(3))
            await run(s, "find_duplicates", {"max_items": 0, "library": "Sports"}, lambda d: isinstance(d, list) and len(d) > 0)
            await run(s, "find_duplicates", {"max_items": 1000, "library": "Commodities-Converted Copy"}, lambda d: isinstance(d, list))
            await run(s, "list_by_status", {"prefix": "replication:", "library": "Sports"}, has("count"))
            await run(s, "method_matrix", {"library": "Funding Liquidity_Library"}, lambda d: isinstance(d, (dict, list)))
            await run(s, "get_bibliography", {"keys": ["Sports:1", "Sports:2"], "style": "apa"}, has("Yun", "Yi"))
            await run(s, "export_items", {"keys": ["Sports:1"], "fmt": "ris"}, has("TY  - JOUR", "ER  -"))
            await run(s, "export_items", {"keys": ["Sports:1"], "fmt": "csljson"}, has("article-journal"))
            ris = ("TY  - JOUR\nAU  - Yun, Seokmin\nTI  - Another Market Segment: Sport Event Tourism by Disabled Athletes\n"
                   "PY  - 2024\nDO  - 10.1186/s13102-024-00914-5\nER  - \n\n"
                   "TY  - JOUR\nAU  - Charlier, Dorothee\nAU  - Legendre, Berangere\nTI  - A Multidimensional Approach to Measuring Fuel Poverty\n"
                   "PY  - 2019\nVL  - 40\nIS  - 2\nSP  - 27\nEP  - 54\nDO  - 10.5547/01956574.40.2.bleg\nER  - \n")
            await run(s, "preview_import", {"ris_text": ris, "library": "Sports"}, lambda d: isinstance(d, dict) and d.get("new") == 1
                      and d.get("duplicates") == 1 and d.get("file_for_import"))
            await run(s, "preview_import", {"ris_text": "", "library": "Sports"}, lambda t: "Give ris_text" in t, expect_error=True)
            await run(s, "check_records", {"keys": ["Commodities-Converted Copy:10587", "Commodities-Converted Copy:10593"]}, has("with_issues"))
            v6 = r"J:\Claude\Green Finance_RP\GF_Proposal_Update\RIF_Proposal_UK_Green_Finance_endnote_v6.docx"
            if os.path.isfile(v6):
                await run(s, "check_docx_citations", {"path": v6, "library": "Commodities-Converted Copy"},
                          lambda d: isinstance(d, dict) and d.get("citations") == 106 and d.get("problems") == []
                          and d.get("database_id_matches") is True)
                await run(s, "check_docx_citations", {"path": v6, "library": "Sports"},
                          lambda d: isinstance(d, dict) and d.get("database_id_matches") is False
                          and any("different EndNote library" in p["problem"] for p in d["problems"]))
            await run(s, "find_by_identifier", {"identifier": "Influense of ESG performanse on corporate valu", "library": "Sports"},
                      has("approximate spelling", "ESG"))
            await run(s, "search_online", {"query": "x", "source": "scopus", "library": "Sports"}, lambda t: "Elsevier API key" in t, expect_error=True)
            await run(s, "search_online", {"query": "x", "source": "google scholar", "library": "Sports"}, lambda t: "no official API" in t,
                      expect_error=True)
            await run(s, "get_online_record", {"doi": "not a doi", "library": "Sports"}, lambda t: "not a valid DOI" in t, expect_error=True)
            await run(s, "import_online", {"dois": [], "library": "Sports"}, lambda t: "at least one DOI" in t, expect_error=True)
            if os.environ.get("ENDNOTE_TEST_ONLINE") == "1":  # real requests to Crossref and OpenAlex; sends one public DOI
                doi = "10.1186/s13102-024-00914-5"
                await run(s, "get_online_record", {"doi": doi, "library": "Sports"}, lambda d: d.get("status") == "IN LIBRARY")
                await run(s, "online_citations", {"doi": doi, "direction": "references", "source": "crossref", "limit": 3, "library": "Sports"},
                          lambda d: d.get("total", 0) > 0)
                await run(s, "online_citations", {"doi": doi, "direction": "references", "source": "openalex", "limit": 3, "library": "Sports"},
                          lambda d: d.get("total", 0) > 0)
                await run(s, "search_online", {"query": "sport event tourism disabled athletes", "limit": 3, "library": "Sports"},
                          lambda d: len(d.get("results", [])) > 0)
                await run(s, "import_online", {"dois": [doi], "library": "Sports"}, lambda d: d.get("duplicates") == 1)
            await run(s, "missing_pdfs", {"library": "Sports", "limit": 5},
                      lambda d: isinstance(d, dict) and d.get("checked", 0) > 100 and "without_usable_pdf" in d)
            await run(s, "missing_pdfs", {"library": "Sports", "limit": 0}, lambda t: "limit must be 1 or more" in t, expect_error=True)
            await run(s, "export_annotations", {"keys": ["Sports:1", "Sports:2"], "library": "Sports"},
                      lambda d: isinstance(d, dict) and d.get("records") == 2 and str(d.get("file", "")).endswith(".md"))
            await run(s, "export_annotations", {"library": "Sports"}, lambda t: "Give item keys" in t, expect_error=True)
            await run(s, "compare_libraries", {"other": "Funding_Liquidity", "library": "Funding Liquidity_Library", "limit": 3},
                      lambda d: isinstance(d, dict) and d.get("checked", 0) > 0 and d["in_both"] + d["only_in_library"] == d["checked"])
            await run(s, "watch_library", {"library": "Sports", "reset": True},
                      lambda d: isinstance(d, dict) and d.get("baseline_saved") is True and d.get("records", 0) > 100)
            await run(s, "watch_library", {"library": "Sports"},
                      lambda d: isinstance(d, dict) and d.get("message", "").startswith("No changes since") and d.get("unchanged", 0) > 100)
            await run(s, "watch_library", {"library": "Sports", "limit": 0}, lambda t: "limit must be 1 or more" in t, expect_error=True)
            await run(s, "compare_libraries", {"other": "Sports", "library": "Sports"}, lambda t: "two different libraries" in t,
                      expect_error=True)
            await run(s, "check_docx_citations", {"path": r"C:\nowhere\a.doc"}, lambda t: ".docx" in t, expect_error=True)
            await run(s, "list_libraries", {}, lambda d: isinstance(d, list))
            await run(s, "get_item", {"key": "1", "library": "No Such Library"}, lambda t: "Unknown library" in t, expect_error=True)
            await run(s, "search_items", {"query": "x", "group": "No such group", "library": "Sports"}, lambda t: "No group named" in t,
                      expect_error=True)
            await run(s, "get_bibliography", {"keys": ["Sports:1"], "style": "nonexistent-style"}, lambda t: "was not found" in t)

    after = stats()
    passed = sum(1 for _, ok, _ in results if ok)
    print()
    print(f"{passed} of {len(results)} calls passed. Slowest: " + ", ".join(f"{n} {d:.1f}s" for n, _, d in sorted(results, key=lambda x: -x[2])[:4]))
    print("Library files unchanged during the run (size and modified time):")
    for p in WATCHED:
        print(f"  {'same' if before[p] == after[p] else 'CHANGED'}  {Path(p).name:<22} {before[p]}  -> {after[p]}")


if __name__ == "__main__":
    asyncio.run(main())
