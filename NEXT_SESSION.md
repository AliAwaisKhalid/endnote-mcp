# EndNote MCP: status and next steps

Updated 2026-10-28. 27 tools. `python -m pytest`: 203 pass. `test_mcp_client.py`: 52 of 52 pass, library files unchanged.

## Done on 2026-10-28

- Unit, integration and security tests (`test_helpers.py`, `test_integration.py`, `test_findings.py`, `test_security.py`, `test_security_import.py`).
- 31 user simulations (`simulate_users.py`). Seven unclear messages fixed. See `SIMULATION_FINDINGS.md`.
- Security review. Two fixes, two accepted. See `SECURITY_FINDINGS.md`.
- New tools, each test-first: `check_docx_citations`, `missing_pdfs`, `export_annotations`, `compare_libraries`, `watch_library`.
- `check_records` rules added: split organisation names that look like names, no title, no journal, bad DOI, shared DOI, reversed pages, all-capital title.
- Open items closed: `check_docx_citations` now compares the document's database id with the library's (`misc` table, code 14); `check_records` reads an entry ending in an organisation word as complete; `find_by_identifier` tolerates small spelling mistakes in titles.
- Online tools, test-first (`online.py`, `test_online.py`): `get_online_record`, `search_online`, `online_citations`, `import_online`, using Crossref and OpenAlex. Fetched records go through the duplicate check into a RIS file; the library is never written.
- README rewritten for 27 tools. Handout rebuilt (`make_handout.py`).

## To do

1. Export `EndNote_MCP_Handout.pdf` from the `.docx` in Word, and check it still fits two pages. If not, trim the "Tested and reviewed" section.
2. Record 10593 in Commodities is typed Journal Article with no journal name and no year. Change its type or fill the fields in EndNote.
3. Optional: read-only check of the v6 `.docx` through Word COM (counts fields). Launches Word, so ask first.
4. Online tools:
   - Scopus is off: it needs an Elsevier API key. To add it, set `ELSEVIER_API_KEY` and add a `scopus` source in `online.py` (Scopus Search and Abstract Retrieval APIs; institutional access gives references).
   - Google Scholar is not used: no official API, and automated queries break its terms.
   - Possible additions: PubMed/Semantic Scholar sources; import the whole `NEW` list of `online_citations` in one call.
5. Open choices:
   - `check_records`: an organisation whose last word is not in `ORG_ENDINGS`, followed by a person with a spelled-out first name, can still be flagged "may be split". Add words to `ORG_ENDINGS` in server.py if it happens.
   - Accepted security items: style path in `get_bibliography`, local paths in error messages.
