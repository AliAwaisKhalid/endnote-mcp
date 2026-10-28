"""Runs common user requests through a real MCP stdio client and prints the exact text a user would see.
Run: python simulate_users.py   (read-only)"""
import asyncio
import json
import logging
import sys
from pathlib import Path

logging.disable(logging.CRITICAL)
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

SERVER = str(Path(__file__).resolve().parent / "server.py")
COM = "Commodities-Converted Copy"
RIS_NEW = "TY  - JOUR\nAU  - Nobody, Nemo\nPY  - 2031\nTI  - A paper that is surely not in the library\nER  - \n"
RIS_DUP = "TY  - JOUR\nAU  - Someone, A\nPY  - 2021\nTI  - dup\nDO  - 10.1080/17430437.2021.1984426\nER  - \n"

CASES = [
    ("Already in my library? (DOI, known)", "find_by_identifier", {"identifier": "10.1080/17430437.2021.1984426", "library": "Sports"}),
    ("Already in my library? (DOI as URL)", "find_by_identifier", {"identifier": "https://doi.org/10.1080/17430437.2021.1984426", "library": "Sports"}),
    ("Already in my library? (DOI, unknown)", "find_by_identifier", {"identifier": "10.9999/not.here", "library": "Sports"}),
    ("Already in my library? (misspelt title)", "find_by_identifier", {"identifier": "Influense of ESG performanse on corporate valu", "library": "Sports"}),
    ("Already in my library? (misspelt title via search)", "search_items", {"query": "Influense ESG performanse corporate value", "library": "Sports", "limit": 3}),
    ("Highlights, record 1", "get_annotations", {"key": "Sports:1"}),
    ("Highlights, record without PDF", "get_annotations", {"key": "Sports:2"}),
    ("Check import: new", "preview_import", {"ris_text": RIS_NEW, "library": "Sports"}),
    ("Check import: duplicate", "preview_import", {"ris_text": RIS_DUP, "library": "Sports"}),
    ("Check import: empty", "preview_import", {"ris_text": "", "library": "Sports"}),
    ("Check import: not RIS", "preview_import", {"ris_text": "hello world", "library": "Sports"}),
    ("Methods table: group list", "list_collections", {"library": "Sports"}),
    ("Methods table: no Method lines", "method_matrix", {"library": "Funding Liquidity_Library"}),
    ("Methods table: unknown group", "method_matrix", {"group": "Nope", "library": "Sports"}),
    ("Bibliography APA", "get_bibliography", {"keys": ["Sports:1"], "style": "apa"}),
    ("Bibliography missing style", "get_bibliography", {"keys": ["Sports:1"], "style": "nonexistent-style"}),
    ("Bibliography key does not exist", "get_bibliography", {"keys": ["Sports:999999"], "style": "apa"}),
    ("Bibliography empty keys", "get_bibliography", {"keys": [], "style": "apa"}),
    ("Check before citing: Essex pair", "check_records", {"keys": [f"{COM}:10587", f"{COM}:10593"]}),
    ("Check before citing: no group, no keys", "check_records", {"library": "Sports"}),
    ("Wrong library name", "search_items", {"query": "x", "library": "Sport"}),
    ("Wrong library name (path to .enlx)", "search_items", {"query": "x", "library": r"G:\nowhere\Sports.enlx"}),
    ("Record number as text", "get_item", {"key": "one", "library": "Sports"}),
    ("Record number with spaces", "get_item", {"key": " 1 ", "library": "Sports"}),
    ("Record number negative", "get_item", {"key": "-1", "library": "Sports"}),
    ("Record number huge", "get_item", {"key": "99999999999999999999", "library": "Sports"}),
    ("Full text of record without PDF", "get_fulltext", {"key": "Sports:2"}),
    ("Limit zero", "search_items", {"query": "carbon", "limit": 0, "library": "Sports"}),
    ("Limit negative", "search_items", {"query": "carbon", "limit": -5, "library": "Sports"}),
    ("Export unknown format", "export_items", {"keys": ["Sports:1"], "fmt": "docx"}),
    ("Export nothing", "export_items", {}),
]


def clip(t, n=420):
    t = t.replace("\n", " ")
    return t if len(t) <= n else t[:n] + " ...[cut]"


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for label, tool, args in CASES:
                try:
                    res = await asyncio.wait_for(s.call_tool(tool, args), 120)
                    txt = " ".join(c.text for c in res.content if getattr(c, "text", None))
                    tag = "ERROR" if res.isError else "ok"
                except Exception as e:  # noqa: BLE001
                    txt, tag = f"{type(e).__name__}: {e}", "EXC"
                print(f"[{tag}] {label}\n    {tool} {json.dumps(args)[:110]}\n    -> {clip(txt)}\n")


asyncio.run(main())
