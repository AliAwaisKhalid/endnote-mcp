# EndNote MCP (read-only)

Reads EndNote 2025 libraries through a snapshot copy. It never writes to a library, a `.Data` folder or an attachment. 28 tools. Schema notes: `SCHEMA.md`. Tests: `test_mcp_client.py` (live, over MCP) and the pytest files listed under Tests.

## Quick start for the group

For EndNote 2025 libraries on Windows. The server only reads copies of your libraries and never changes them.

1. Clone the repository and install the packages: `pip install -r requirements.txt`. Python 3.11 or newer. Install `pandoc` too if you want `get_bibliography`.
2. Copy `libraries.example.json` to `libraries.json` and put your own `.enl` paths in it (forward slashes). `libraries.json` stays on your machine and is not committed.
3. Register the server with Claude Code, using the path to your clone:

   ```
   claude mcp add endnote --scope user -- python C:/path/to/endnote-mcp/server.py
   ```

4. Start a new Claude Code session and run `claude mcp list`; `endnote` should show Connected. Ask Claude to run `list_libraries`.
5. Check the install: `python -m pytest`. Tests that need a particular library skip on your machine. `test_mcp_client.py` is the author's live test against the author's own libraries and will not run as is.
6. Optional: the online tools send DOIs and search words to Crossref and OpenAlex. Set `ENDNOTE_MCP_OFFLINE=1` to switch them off.

Report problems as issues in the repository, with the tool name, the arguments (without private titles if you prefer) and the message you saw.

## Setup

1. Install the packages the server needs: `mcp`, `PyMuPDF` (`fitz`), and `pandoc` on the PATH for `get_bibliography`.
2. Name the libraries. The server reads `libraries.json` next to `server.py`. This machine's file lists Sports, Funding_Liquidity, Funding Liquidity_Library and Commodities-Converted Copy. Each entry is a path, or an object with a name. Use forward slashes in paths:

   [
     {"name": "Sports", "path": "G:/Completed Projects/Sports_Carbon_Energy/References/Sports.enl"},
     {"name": "Funding_Liquidity", "path": "G:/Research/Funding Liquidity Risk/FUNDLIQ/References/Funding_Liquidity.enl"}
   ]

   `libraries.example.json` shows the format. `libraries.json` itself is not committed to git because it holds local paths.
   The first library is the default. A name defaults to the file name without `.enl`. An environment variable `ENDNOTE_LIBRARIES` (semicolon-separated `.enl` paths) overrides the file if it is set.
   Registered with Claude Code: `claude mcp add endnote --scope user -- python J:/Claude/endnote-mcp/server.py`. Check with `claude mcp list`, which shows `endnote` as Connected.
3. Run the client test: `python test_mcp_client.py`. It calls every tool and shows pass or fail with the time for each.

Snapshots and caches are kept in `%LOCALAPPDATA%\endnote-mcp\`. `cache\<library>\` holds the copies, `cache\pdftext\` the extracted PDF text, `work\` the exports and RIS files written by `export_items` and `preview_import`.

## Online lookups (Crossref and OpenAlex)

Four tools fetch records from the internet: `get_online_record`, `search_online`, `online_citations` and `import_online`. They are the only part of the server that goes online.

- **What is sent:** a DOI or the search words, over https, to `api.crossref.org` or `api.openalex.org`. No library content, record, note or file is sent. Nothing else is contacted: other hosts, redirects and non-https addresses are refused. Answers over 8 MB are refused.
- **Switch off:** set `ENDNOTE_MCP_OFFLINE=1` and every online tool refuses. Optional: `ENDNOTE_MCP_MAILTO=you@example.org` identifies you to the providers' polite pools, which are faster.
- **Not used:** Google Scholar has no official API and forbids automated queries; `source='google scholar'` returns an explanation. Scopus needs an Elsevier API key that this server does not have; `source='scopus'` returns an explanation. OpenAlex gives cited-by and reference lists in place of Scholar.
- `get_online_record(doi, source)`: full metadata (title, authors, year, journal, volume, issue, pages, DOI, abstract, publisher, ISSN, keywords, citation count) and whether it is already in the library. `source='crossref'` (default, publisher data) or `'openalex'` (adds keywords).
- `search_online(query, source, limit)`: up to 100 results by title, author or topic words, each marked `IN LIBRARY` or `NEW`.
- `online_citations(doi, direction, source, limit)`: `direction='references'` (works it cites) or `'cited_by'` (works that cite it, OpenAlex only), each marked `IN LIBRARY` or `NEW`. Crossref's reference list is what the publisher deposited; many entries have no DOI and show `NO DOI` with the citation text.
- `import_online(dois, source)`: up to 50 DOIs. Marks each NEW or DUPLICATE against the library (same rules as `preview_import`) and writes only the new ones to `work\new_records_<time>.ris`. You import that file with File > Import, filter RIS. The server never writes to the library. DOIs that cannot be fetched are listed under `failed`. Each record has a note line saying where and when it was fetched.
- Typical use: `online_citations` on a key paper, pick the `NEW` DOIs, `import_online` with them, then File > Import in EndNote.
- Online data can be wrong or incomplete (a missing journal volume, an abstract from a different version). Run `check_records` after importing.

## Item keys

A key is `<library name>:<record number>`, for example `Sports:12`. Record numbers are unique only within one library. Every tool takes an optional `library` argument (name or path) for the other keys.

## Tools

Find and read: `search_items`, `find_by_identifier`, `get_item`, `get_notes`, `get_annotations`, `get_fulltext`

Browse and organise: `list_libraries`, `list_collections`, `collection_items`, `list_tags`, `recent_items`, `find_duplicates`, `list_by_status`

Output: `method_matrix`, `get_bibliography`, `export_items`, `preview_import`

Online: `get_online_record`, `search_online`, `online_citations`, `import_online` (see Online lookups below)

Check: `check_records`, `check_docx_citations`, `convert_docx_citations`, `missing_pdfs`, `compare_libraries`, `watch_library`

- `watch_library(limit, update_baseline, reset)`: what changed since the last call: records added, updated, moved to the trash, restored, or removed. The first call saves a baseline (record numbers, update times and short titles) in `%LOCALAPPDATA%\endnote-mcp\cache\watch\<library>.json` and reports no changes. Each later call compares the library with the baseline and then moves it forward, so a change is reported once. `update_baseline=False` looks without moving it; `reset=True` starts again from now. It sees changes only after EndNote has saved and the snapshot refreshed; a removed record shows only its number and title. The baseline is the only thing this server writes besides exports, the snapshot copies and the PDF text cache.

Annotations: `get_annotations` (one record), `export_annotations` (several records to one Markdown file).

- `missing_pdfs(group, limit)`: active records with no usable PDF: nothing attached, the file is missing on disk, or the attachment path leaves the library's PDF folder. Gives counts and the first `limit` records.
- `compare_libraries(other, group, limit, library)`: records of `library` that are not in `other`, matched by DOI or by title and year (titles under 16 characters are not matched). Gives how many are in both and the first `limit` that are only in `library`.
- `export_annotations(keys or group)`: writes highlights and comments to `work\annotations_<time>.md` and returns the text. Records with no PDF are listed under `skipped`.
- `check_records` also flags: no title; a journal article with no journal name; an electronic resource number that is not a valid DOI (journal articles); a DOI shared with another active record; a page range that runs backwards (`54-27`; `1234-56` is read as 1234-1256); a title in all capitals.
- `check_records(keys or group)`: flags an organisation split across author entries (including a fragment that looks like a name, such as `University of Essex, Institute for Social` followed by `Economic, Research`), a missing year, or no author. Run it on a group before citing it in Word. An entry that ends in a word such as Research, Studies, Institute or Council is read as a complete organisation name and is not flagged, even when a person with a spelled-out first name follows. An organisation whose last word is not in that list, followed by a person, may still be flagged as "may be split"; check it by eye.
- `check_docx_citations(path, library)`: reads the EndNote fields in a `.docx` without opening Word and compares each citation with the library. Reports a record that is missing or in the trash, an author or year that differs, a citation with no record number, citations from more than one EndNote library, and a document whose records mostly are not in the chosen library. Also reports whether the document has a reference list. Also compares the database id Word stored in the citations with the library's own id (`database_id_matches`: true, false, or null when either id is unknown). A false means the document was written with a different library, and is reported as a problem. A copy made with File > Save a Copy keeps the id, so a match is strong evidence, not proof. Only `.docx`, up to 100 MB.
- `convert_docx_citations(path, library, output, dry_run)`: turns plain-text author-year citations in a `.docx` into live EndNote fields (`(Smith et al., 2020)`, `(Smith & Lee, 2019; Wu, 2021)`, `Smith et al. (2020)`). A citation is converted only when exactly one active record matches the first author, the year and the number of authors; the rest are listed as `unmatched` or `ambiguous`. Reference-list entries are left alone. The original and the library are never changed: the result goes to a new file (default `<name>_endnote_fields.docx`, never overwriting). Then use EndNote > Update Citations and Bibliography in Word. Needs `lxml`. `dry_run=True` only reports.

- `search_items(query, group, keyword, ref_type, limit, fulltext)`: every word must appear in author, title, year, keywords or abstract. `fulltext=True` also searches EndNote's PDF index and text already read by `get_fulltext`.
- `find_by_identifier(identifier)`: DOI (doi.org link accepted), key, record number, or title words. Exact matches first, then records containing all the words, then (only if nothing matched) records whose title has each word or a close spelling of it, marked `title words (approximate spelling)`. Words under 4 letters must match exactly. `search_items` stays exact.
- `list_libraries` also shows each library's `database_id`, the id Word stores in every citation.
- `get_annotations(key)`: highlights and comments in the attached PDFs. Read in place. Nothing is saved.
- `get_fulltext(key, max_chars)`: first attached PDF, 30,000 characters by default. Text is cached by file path, size and modified time.
- `list_tags(kind)`: keywords with counts (default). `kind="colour"` lists EndNote's colour tags.
- `preview_import(ris_text or path)`: marks each RIS record as NEW or DUPLICATE (same DOI, or same title and year). Writes only the new records to a fresh RIS file. You import that file with File > Import in EndNote. A path must end in `.ris` or `.txt`. Input over 10 MB is refused. Text with no RIS records gives an error that shows the expected format.
- `export_items(keys or group, fmt)`: `ris`, `bibtex` or `csljson`. The file path is returned with the text.

## What a tool says when it finds nothing

A search that finds nothing is an answer, not an error. `search_items`, `find_by_identifier` and `list_collections` return one row, `{"message": "..."}`, that says what was not found and what to try. Code that reads results should expect that shape. Real problems (unknown library, bad key, bad `limit`) are errors with a message that says what to give instead. `limit` must be 1 or more in `search_items`, `recent_items`, `list_tags` and `collection_items`. Title search in `find_by_identifier` tolerates small spelling mistakes; `search_items` does not.

## Tests

- `python test_mcp_client.py`: every tool over a real MCP client against the live libraries, and a check that no library file changed (52 calls; 61 with `ENDNOTE_TEST_ONLINE=1`, which makes real requests to Crossref and OpenAlex for one public DOI).
- `python -m pytest`: 203 tests, including `test_online.py` (no network: recorded sample responses), including `test_features.py` (missing_pdfs, export_annotations, compare_libraries, the extra check_records rules), `test_watch.py` (watch_library) and `test_open_items.py` (database id, organisation names, approximate titles). `test_helpers.py` (parsing and check rules, no library opened), `test_integration.py` (Essex records and the v6 Word file), `test_findings.py` (messages and limits), `test_security.py` and `test_security_import.py` (path and size limits), `test_docx_citations.py`.
- `python simulate_users.py`: 31 common requests with the exact messages a user would see. Results: `SIMULATION_FINDINGS.md`.
- Security review: `SECURITY_FINDINGS.md`.

## How the snapshot works

- Each call checks the size and modified time of the library's `.enl` and `Data\sdb` files. If they changed, the server copies them again and reads the copy. Otherwise it reuses the copy it has.
- A result can lag EndNote by the time it takes to notice the change. EndNote saves to disk as you work, so a change shows on the next call after the save.
- If EndNote has a library open, Windows may refuse the copy. The server then serves the last snapshot with a warning that gives its time. If there is no earlier snapshot, the call fails with a message that says what to do: close the library in EndNote, or use File > Save a Copy and point the server at the copy.
- Copies are checked before they are used. If the source changes during the copy, the copy is discarded.
- The `.enl` file is copied with the others, but the server does not read its contents. Records come from `sdb.eni`.

## Limits

- **Read only.** No tool writes to a library. Adding records or tags needs EndNote itself.
- **PDF paths stay inside the library.** An attachment path that is absolute or climbs out of `<library>.Data\PDF` is refused by `get_fulltext` and `get_annotations` and skipped by full-text search and `get_item`. A library file from someone else can contain such a path.
- **Style names.** `get_bibliography` builds the style file path from the `style` value, so only pass a plain style name such as `apa`. A path loads any `.csl` file. Accepted in the security review because the value comes from you.
- **Error messages** include local paths and up to 500 characters of pandoc's error text. Accepted.
- **Layouts differ by version.** Some tables exist only in newer libraries. The server checks which tables are present.
- **Older libraries.** A library with no `Data\sdb\sdb.eni` file is an older format. Convert it in EndNote 2025 first (File > Open > Open Library).
- **`.enlx` files are not read.** These are compressed libraries. Export or save them as `.enl` libraries first.
- **Group sets** were not found in the test libraries. Combined groups are listed as combined.
- **Smart groups** show the members EndNote last cached. Those may be out of date.
- **Text sort order** uses a stand-in for EndNote's `ENCI_Base` and `ENCIN_Base` collations. Sorting of text may differ slightly from EndNote.
- **Annotations** are read with PyMuPDF. Text in a PDF's own text layer can be garbled, and EndNote's annotation text may differ from what PyMuPDF extracts.
- **Speed.** On the 7,499-record Commodities library, the first search takes about 0.4 s, including loading the library. A full-text search on the same library takes 0.14 s on the first call. Later calls take milliseconds.
