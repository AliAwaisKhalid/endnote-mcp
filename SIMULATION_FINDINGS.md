---
tags: [endnote-mcp, testing]
date: 2026-10-28
---
# User simulation findings

Run with `python simulate_users.py` (31 requests through the MCP stdio client). No tracebacks, no library changes.
Errors reach the user as `Error executing tool <name>: <message>`.

## Messages that work
- Wrong library name: lists the configured names and points to `list_libraries`.
- Record number as text, negative, or huge: says what a key looks like (`Sports:123`).
- Missing style: names the folder searched and returns CSL-JSON instead.
- Unknown group, unknown key, unknown export format, empty import, empty key list: each says what to give instead.
- Duplicate import: reports the matching record and "same DOI".

## Unclear or wrong (items 1-7 fixed 2026-10-28, tests in test_findings.py; item 2 still has no fuzzy matching, it now says so)
1. `find_by_identifier` with a DOI that is not in the library returns an error. "Not in my library" is a normal answer, so it should return an empty result with a message.
2. Misspelt title: `find_by_identifier` errors and `search_items` returns a blank reply. No fuzzy match, no "0 results" text.
3. `search_items` and `list_collections` return a blank reply when there is nothing to list. Say "0 results".
4. `check_records` with no keys and no group says "No group named ''". It should say "Give item keys or a group name".
5. `limit` of 0 or -5 silently returns 1 result. It should reject the value or use the default.
6. `preview_import` with text that is not RIS reports 0 records and gives no reason. It should say no RIS records were found.
7. A path ending in `.enlx` (or any path) gets the generic "Unknown library" message. Add a hint that libraries are named in `list_libraries`.
8. `get_annotations` on Sports:1 and Sports:2 both say no PDF is attached; the with-PDF case is covered by `test_mcp_client.py`.

## Not a bug
"MenÆs" in Sports:118 is a console display artefact. The stored bytes are valid UTF-8 (E2 80 99).
