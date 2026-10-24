# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-10-24

Fixes from [#1](https://github.com/brailateo/couchdb_sql/pull/1) and [#2](https://github.com/brailateo/couchdb_sql/pull/2).

### Fixed

- Quoted text can contain `AND`, `OR`, `WHERE`, and parentheses. `UPDATE` splits `SET` and `WHERE` outside quotes, and an unclosed quote is a parse error.
- `=`, `!=`, `IN`, `NOT IN`, and range comparisons no longer match JSON null or a missing field. `IS NULL` and `IS NOT NULL` are unchanged.
- JSON `true` and `false` stay distinct from `1` and `0` in comparisons, `IN`, and `COUNT(DISTINCT)`.
- A comparison between incompatible types no longer raises. It does not match, and the shell prints one warning. `ORDER BY ... DESC` orders strings directly, so prefixes and empty strings sort correctly, and nulls sort last.
- `SELECT`, `ORDER BY`, and `UPDATE SET` follow dotted paths. Assigning through a parent that is not an object fails before any write.
- `_all_docs` is read in pages. When `total_rows` is present and the fetched row count differs, the command stops before writing.
- Every HTTP request has a timeout (3s connect and 60s read by default, overridable with `COUCHDB_TIMEOUT`).
- Startup probes `GET /_all_dbs`. HTTP 401 is reported as an authentication failure.
- SQL export quotes identifiers. Spreadsheet export prefixes formula-like text and removes characters Excel rejects, including NUL. An empty export still creates the file and reports zero fields.
- CSV import accepts a UTF-8 BOM, scientific notation (`1e2` becomes `100`), `true`/`false` booleans, and leading zeros such as `012`. Bulk insert, update, delete, and import errors print the document id, error, and reason. Re-importing an existing `_id` still conflicts and does not overwrite the document.
- Database names may include `-` and `$()+`, and field names may include `-`. An invalid regular expression is an error. `SELECT COUNT` rejects `ORDER BY`, `LIMIT`, and `OFFSET`.
- `.env` is loaded from the directory that contains the program, not the process working directory.

### Added

- pytest suite with a fake CouchDB HTTP layer, and a GitHub Actions workflow that runs it.
- MIT license.
- `python main.py --version`, and the version on the startup banner.
