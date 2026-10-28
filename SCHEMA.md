# EndNote library schema (Step 1)

Checked 2026-10-28 on snapshot copies in `%LOCALAPPDATA%\endnote-mcp\cache\`. Every connection was read-only (`mode=ro`). No library, `.Data` folder, PDF or attachment was written. The one library I could not copy is listed under "Not checked".

## Summary

- **References are in `sdb.eni`**, table `refs`, one row per record. The `.enl` file is also SQLite. In FUNDLIQ it holds a full copy of the tables. In Sports it holds `enl_refs`, identical to `refs`. The server should read `sdb.eni`.
- **The layout depends on the EndNote version that last saved the library.** Three layouts were seen (table list below). Check for each table before using it.
- **EndNote 2025 has colour tags** (`tag_groups`, `tag_members`). The prompt assumed EndNote has none.
- **PDF annotations are not in the database.** They are stored in the PDF files and PyMuPDF reads them.
- **EndNote keeps its own full-text index** in `pdb.eni`, table `pdf_index`. The Funding Liquidity library has 32 rows, one per PDF.
- **Attachment paths resolve.** All 32 `file_res` rows in the Funding Liquidity library point to files that exist.
- **EndNote holds `Commodities-Converted Copy` open and locks its `sdb.eni`.** The snapshot copy failed with a sharing violation. See "Not checked".

## Files and what they hold

| File | Holds |
|---|---|
| `<Library>.enl` | SQLite. Sports: `enl_refs` (360 rows, identical to `refs`). FUNDLIQ: full copy of the tables. Funding Liquidity_Library: 4 bytes. References_CTS: binary header, not SQLite. |
| `<Library>.Data\sdb\sdb.eni` | SQLite. All records and most library data (see tables). **Read this file.** |
| `<Library>.Data\sdb\pdb.eni` | SQLite. `pdf_index`: EndNote's extracted PDF text, one row per attachment (32 rows in the Funding Liquidity library, 0 in the others). |
| `<Library>.Data\PDF\<folder>\<file>.pdf` | The attachments. Never copied. Opened in place for annotations and text. |

## Tables and row counts

| Table | Sports | FUNDLIQ | Funding Liquidity_Library (2021) | Vendor sample (`Sample_Library_2025`) |
|---|---|---|---|---|
| `refs` | 360 | 142 | 34 (1 in Trash) | 192 (2 in Trash) |
| `refs_ord` | 360 | 142 | 34 | 192 |
| `ret_watch` | 360 | 142 | absent | 192 |
| `ref_props` | absent | 142 | absent | 192 |
| `file_res` | 0 | 0 | 32 | 3 |
| `groups` | 0 | 0 | 4 | 16 |
| `tag_groups` | absent | 0 | absent | 5 |
| `tag_members` | absent | 142 | absent | 192 |
| `terms` | 674 | 326 | 126 | 1,038 |
| `jterms` | 150 | 99 | 17 | 68 |
| `misc` | 27 | 32 | 18 | 71 |
| `csort` | 1 | 1 | 1 | 3 |
| `pdb.eni` `pdf_index` | 0 | 0 | 32 | 0 |

The vendor sample is EndNote's own example, at `C:\Users\Public\Documents\EndNote\Examples\Sample_Library_2025.enlx`. I unpacked a copy in the scratchpad to read its layout. The server does not read `.enlx` files.

Layouts seen:
- **2025 with tags** (FUNDLIQ and the sample): `ref_props`, `tag_*` tables present.
- **Sports:** `ret_watch` present, but no `ref_props` or tag tables.
- **2021 (Funding Liquidity_Library):** no `ret_watch`, `ref_props` or tag tables.

## Field map (table `refs`)

| Item | Column | Format and notes |
|---|---|---|
| Record number | `id` | Integer, unique per library. Equal to `rowid` in the sample. Confirm against EndNote's display in Step 3. |
| Trash flag | `trash_state` | `0` active, `1` in Trash (inferred from the data; confirm in Step 3). |
| Reference type | `reference_type` | Integer code. Names come from the table in `EndNote.exe.mui`: 0 Journal Article, 1 Book, 2 Thesis, 3 Conference Proceedings, 7 Book Section, 9 Edited Book, 16 Web Page, 31 Generic. |
| Author | `author` | Several values separated by `char(13)` (carriage return). Form: `Last, First`. |
| Year | `year` | Text, four digits. |
| Title | `title` | Text. |
| Secondary title (journal) | `secondary_title` | Journal for a Journal Article. Book title for a Book Section. |
| Volume | `volume` | Text. |
| Number (issue) | `number` | Text. |
| Pages | `pages` | Text, for example `27-54`. |
| DOI | `electronic_resource_number` | The DOI is field 43 in Journal Article ("DOI") and "Electronic Resource Number" in Generic. Several values are separated by `char(13)`, and the DOI is not always the first line. Match any line. |
| Keywords | `keywords` | `char(13)` separated. |
| Abstract | `abstract` | Text. |
| Notes | `notes` | Text, `char(13)` separated (for example `Times Cited:44`). |
| Research notes | `research_notes` | Text. Column present, empty in all four test libraries. |
| Label | `label` | Text. Column present, empty in all test libraries. |
| Custom 1 to 7 | `custom_1` to `custom_7` | Text. |
| URL | `url` | `char(13)` separated. |
| Date added | `added_to_library` | Integer, Unix seconds, UTC. `1731661351` = 2024-11-15 09:02:31 UTC. |
| Date last updated | `record_last_updated` | Integer, Unix seconds, UTC. |
| Publication date | `date` | Text as entered. Not the same as `year`. |
| Access date | `access_date` | Text. |

Multi-value separator: `char(13)`. A few `notes` and `author_address` values also contain `;`. The server should split on `char(13)` only.

## Groups, group sets and smart groups (table `groups`)

Columns: `group_id`, `recs_stamp`, `spec` (XML), `members` (binary).

- `spec` is XML, possibly stored as a blob. The name is in `<name>`, and names are XML-escaped (`Corvids &amp; Parrots`). Rules are in `<rule>`. The meanings below are inferred from the examples, not from EndNote's documentation:
  - `TYPE;3`: standard group (members listed in `members`).
  - `TYPE;4` with `SEARCH;...`: smart group. `members` holds a cached result.
  - `TYPE;6`: appears to be an online search group (Library of Congress, PubMed, Web of Science, Jisc).
  - `TYPE;8` with `SET_SEARCH;...`: combined group made from other groups (`Corvids & Parrots`).
- `recs_stamp` is `0` for groups without a `SEARCH` rule, and non-zero for the `SEARCH` groups in the sample. Its meaning is not confirmed.
- **Group membership (`members`)**: 4-byte header, 4-byte count (little-endian), then one 4-byte little-endian record ID per member. Checked against all 16 groups in the sample: 373 memberships match the SQL below.
- **Group sets:** none found in the test libraries. The sample has no group-set rows, so this is unverified.
- **Colour tags** (EndNote 2025): `tag_groups.spec` has the name and `COLOR;hex`. `tag_members.tag_ids` is a space-separated list of tag IDs for each record (the rowid is the record ID).

## Attachments (table `file_res`)

Columns: `refs_id`, `file_path`, `file_type`, `file_pos`.

- `file_path` is relative to `<Library>.Data\PDF\`, with `/` between folder and file: `2145212090/Abbas-2021-Dynamics of Funding Liquidity and R.pdf`.
- Absolute path: `<Library>.Data\PDF\` + `file_path` with `/` replaced by `\`.
- `file_type` is `1` for every row seen, including `.xls` in the sample, so it does not mean "PDF". Meaning not confirmed.
- `file_pos` gives the order of attachments for one record.

## Full text (`pdb.eni`, table `pdf_index`)

Columns: `pdfi_id`, `version`, `refs_id`, `file_timestamp`, `subkey`, `contents`, `tag`.

- `contents` is extracted text. The `subkey` column holds a path from an earlier tool (a Citavi folder); ignore it.
- 32 of 32 attachments in the Funding Liquidity library are indexed. Sports has no PDFs.
- The full-text query below attaches `pdb.eni` as a second read-only database.

## PDF annotations (in the PDF files, read with PyMuPDF)

Read in place with `fitz.open`, no save. Size and modified time of every PDF checked before and after the scan: no change.

| Library | PDFs | PDFs with annotations | Annotations |
|---|---|---|---|
| References_CTS | 44 | 1 (`Systematic_Tail_Risk_COVAR.pdf`, page 4) | 1 Highlight |
| Commodities-Converted Copy | 112 | 1 (page 4) | 1 Highlight |
| Funding Liquidity_Library | 32 | 6 | 34 Highlight (author field `Aliaw` in the samples) |

- Sports and FUNDLIQ have no PDFs, so the prompt's test on them is not possible. Step 3 will use the Funding Liquidity library, or a highlight added in EndNote.
- Highlights carry the highlighted text through `page.get_textbox(annot.rect)`. Some text is garbled by the PDF's own text layer (for example `bank�s`). That is in the PDF, not the reader.
- The scan found only Highlight annotations. It did not check for other types beyond those three libraries.

## Collation (needed to read the database)

The schemas use two EndNote collations, `ENCI_Base` and `ENCIN_Base`, which are not built into SQLite. Without them, some queries fail with `no such collation sequence`. The server registers both on each connection. The stand-in compares case-insensitively. EndNote's real ordering is not copied, so text sort order may differ slightly. The examples sort by integer IDs only.

```python
for name in ("ENCI_Base", "ENCIN_Base"):
    con.create_collation(name, lambda a, b: (a.casefold() > b.casefold()) - (a.casefold() < b.casefold()))
```

## Working SQL examples

All of these ran read-only against the snapshot copies. The parameters are `:doi`, `:gid`, `:rid`, `:tag` and `:term`.

Open the snapshot with:

```python
con = sqlite3.connect(Path(snapshot_path).resolve().as_uri() + "?mode=ro", uri=True)
```

### 1. List references (Sports: 20 of 360 active records)

```sql
SELECT id AS record_number, reference_type, author, year, title,
       secondary_title AS journal, electronic_resource_number AS doi
FROM refs
WHERE trash_state = 0
ORDER BY id
LIMIT 20
```

### 2. Find by DOI (Sports: `10.1186/s13102-024-00914-5` returns record 1; the upper-case version also matches; an unknown DOI returns nothing)

```sql
SELECT id AS record_number, title
FROM refs
WHERE trash_state = 0
  AND instr(char(13) || lower(electronic_resource_number) || char(13),
            char(13) || lower(:doi) || char(13)) > 0
```

### 3. List groups with stored member counts (sample: 16 groups)

```sql
SELECT group_id,
       substr(CAST(spec AS TEXT), instr(CAST(spec AS TEXT), '<name>') + 6,
              instr(CAST(spec AS TEXT), '</name>') - instr(CAST(spec AS TEXT), '<name>') - 6) AS group_name,
       length(members) / 4 - 2 AS stored_members
FROM groups
ORDER BY group_id
```

### 4. List a group's members (sample, group 1 "Corvids": 24 records)

Decodes the `members` blob in SQL, using hex and a recursive CTE. Returns only active records. It matches the Python decode for all 16 groups.

```sql
WITH RECURSIVE
g AS (SELECT hex(members) AS h FROM groups WHERE group_id = :gid),
pos(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM pos, g WHERE n < length(g.h)),
dig AS (SELECT pos.n AS n, instr('0123456789ABCDEF', substr(g.h, pos.n, 1)) - 1 AS v FROM pos, g),
byt AS (SELECT (n - 1) / 2 AS b, sum(v * (CASE WHEN (n - 1) % 2 = 0 THEN 16 ELSE 1 END)) AS val
        FROM dig GROUP BY b),
cnt AS (SELECT sum(val << (8 * (b - 4))) AS c FROM byt WHERE b BETWEEN 4 AND 7),
ids AS (SELECT (b - 8) / 4 AS k, sum(val << (8 * ((b - 8) % 4))) AS id
        FROM byt WHERE b >= 8 GROUP BY k)
SELECT r.id AS record_number, r.title
FROM ids JOIN refs r ON r.id = ids.id, cnt
WHERE ids.k < cnt.c AND r.trash_state = 0
ORDER BY r.id
```

### 5. Find a reference's PDF path (Funding Liquidity library, record 1)

```sql
SELECT f.refs_id AS record_number, f.file_pos, f.file_path AS path_under_pdf_folder
FROM file_res f
WHERE f.refs_id = :rid
ORDER BY f.file_pos
```

Result: `2145212090/Abbas-2021-Dynamics of Funding Liquidity and R.pdf`. Prefix it with `<Library>.Data\PDF\` and replace `/` with `\`. The sample's record 2467 (`Hominid Evolution.xls`) works the same way.

### 6. List colour tags with record counts (sample)

```sql
SELECT tg.group_id AS tag_id,
       substr(CAST(tg.spec AS TEXT), instr(CAST(tg.spec AS TEXT), '<name>') + 6,
              instr(CAST(tg.spec AS TEXT), '</name>') - instr(CAST(tg.spec AS TEXT), '<name>') - 6) AS tag_name,
       (SELECT count(*) FROM tag_members tm
        WHERE instr(' ' || trim(tm.tag_ids) || ' ', ' ' || tg.group_id || ' ') > 0) AS records
FROM tag_groups tg
ORDER BY tg.group_id
```

Result: Reading: High Priority 3, Need Abstract 30, Reading: Suggested 3, Contact Author 2, Need to Buy 7.

### 7. Records with a tag (sample, tag 2 "Need Abstract": 30 records)

```sql
SELECT r.id AS record_number, r.title
FROM tag_members tm JOIN refs r ON r.id = tm.rowid
WHERE instr(' ' || trim(tm.tag_ids) || ' ', ' ' || :tag || ' ') > 0
  AND r.trash_state = 0
ORDER BY r.id
```

### 8. Full-text search over EndNote's PDF index (Funding Liquidity library, `funding liquidity`: 22 records)

Attaches `pdb.eni` read-only.

```sql
SELECT p.refs_id AS record_number, r.title
FROM pdb.pdf_index p JOIN refs r ON r.id = p.refs_id
WHERE r.trash_state = 0 AND instr(lower(p.contents), lower(:term)) > 0
ORDER BY p.refs_id
```

Attach with: `ATTACH DATABASE ? AS pdb`, passing `Path(pdb_path).resolve().as_uri() + "?mode=ro"`.

## Not checked, and why

1. **`Commodities-Converted Copy` (112 PDFs): no snapshot.** EndNote holds `Commodities-Converted Copy.Data\sdb\sdb.eni` open and locked, so the copy failed with `WinError 32`. I did not try to get around the lock. Options are in the summary. Its layout is not confirmed.
2. **`References_CTS` (44 PDFs): not SQLite.** `References_CTS.enl` starts with a binary header, and there is no `.Data\sdb` folder, so this is an older EndNote format. Its schema was not read.
3. **Funding Liquidity_Library:** the `.enl` is 4 bytes, so the layout was read from `sdb.eni` only.
   The same applies to `References_CTS`, where only the folder list and PDFs were read.
4. **EndNote's display:** record numbers, the trash meaning and group types were inferred from the data. Step 3 checks them against EndNote.
5. **Group sets:** none in the test libraries. The server can list combined groups (`TYPE;8`) but cannot yet confirm EndNote's group-set type.
6. **Sort order:** the `ENCI_Base` stand-in may order text slightly differently from EndNote.

## Changes to the prompt's assumptions

- EndNote 2025 has colour tags. `list_tags` can report them, with `list_by_status` built on them.
- The DOI is in `electronic_resource_number`. The reference-type table and the data agree. Step 3 checks it in EndNote.
- Smart groups keep cached members in `groups.members`. Their freshness is not confirmed.
- EndNote keeps a text index in `pdb.eni`, so `get_fulltext` and `fulltext=True` can use it without parsing PDFs. The server still needs to open PDFs for annotations.
