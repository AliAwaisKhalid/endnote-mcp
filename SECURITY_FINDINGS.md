---
tags: [endnote-mcp, security]
date: 2026-10-28
---
# Security review

Scope: every tool in server.py. Tests: test_security.py, test_security_import.py (81 tests pass in total; live client test 39 of 39).

| # | Finding | Decision |
|---|---------|----------|
| 1 | A library's stored PDF path (absolute or `..`) could make get_fulltext, get_annotations or fulltext search read a file outside the library. PyMuPDF opens .txt files. | Fixed. Paths must resolve inside `.Data\PDF`; escaping paths are refused. |
| 2 | preview_import read any file path and had no size limit. | Fixed. Only .ris and .txt, 10 MB limit (`MAX_IMPORT_BYTES`). |
| 3 | get_bibliography builds the style path from the `style` value, so `..\..\x` or an absolute path loads any .csl file. No pandoc options can be injected. | Accepted. Revisit if the style value can come from anyone but the library owner. |
| 4 | Error messages show local paths and up to 500 characters of pandoc stderr. | Accepted. Local tool; the paths help fix problems. |

## Checked, no problem
- SQL: every query is a fixed string; no user input reaches SQL.
- Read-only open: holds for paths containing `#` and `%` (`?` cannot be in a Windows path).
- pandoc: argument list, no shell; citation keys are letters only, so no markdown injection.
- Regexes: 200,000-character input runs in under a second.
- Writes: only `work`, `cache` and `cache\pdftext`; none of the write paths come from user input.
- Library selection: a path is accepted only if it exactly matches a configured library.
