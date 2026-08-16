# Code Review — mssql_mcp_server

**Date:** 2026-08-16
**Reviewed at:** `7c5c97e` — *feat!: modernize to MCP SDK v2.0.0 (2026-07-28 spec)*
**Baseline:** `8d32897` (pre-migration)

Findings marked ✓ were verified by executing the code, not by reading alone. Evidence for each is in the [Appendix](#appendix-verification-evidence).

---

## Summary

The v2 migration commit is solid work. Of 23 findings from the pre-migration review, **11 are fully fixed**, including the critical SQL injection. The CI rewrite is the standout improvement — the previous workflow could never actually reach a database, and now it can.

Seven findings remain open, and the commit introduces three new issues plus two documentation errors. The single most serious item is now **connection-string injection**, which survived the rewrite unchanged.

A separate pass compared this server against three comparable implementations; the defensible suggestions from that are folded in below as a [hardening backlog](#hardening-backlog).

| Category | Count |
|---|---|
| Fixed | 11 |
| Still open | 7 |
| New in this commit | 5 |
| Hardening backlog | 7 |

---

## Fixed in this commit

| # | Finding | How |
|---|---|---|
| 1 | **SQL injection in resource read** | `INFORMATION_SCHEMA` whitelist *plus* bracket-quoting — belt and braces |
| 5 | `UID=None;PWD=None` under Trusted Connection | Connection string now branches; UID/PWD omitted entirely |
| 6 | Unreachable `SHOW TABLES` branch | Check moved above `cursor.execute` |
| 7 | Config loaded before argument validation | Validation now precedes connection |
| 11 | `conftest.py` was MySQL code | Real T-SQL with `IDENTITY(1,1)`; `pytest.skip` instead of `pytest.fail` |
| 12 | CI could not reach a database | Unit/integration split, `msodbcsql18` install, health check, policy-compliant SA password, 2022 image |
| 13 | Unused `httpx` dependency | Removed |
| 14 | `requirements.txt` / `pyproject.toml` drift | Both aligned on `mcp[cli]>=2.0.0` |
| 15 | Effectively no test coverage | 3 trivial assertions → 14 real unit tests |
| — | No Dockerfile (issue #4) | Added, with ODBC driver layer |
| — | Driver options undocumented (issue #6) | Documented in README troubleshooting |

---

## Open findings

### 1. Connection-string injection — *high* ✓

[`server.py:53-61`](src/mssql_mcp_server/server.py#L53-L61)

Config values are still interpolated into the ODBC connection string with no escaping:

```
Driver=SQL Server;Server=localhost;UID=app;PWD=pa;ss}word;Database=testdb;...
```

A `;` in a password terminates the `PWD` keyword. Ordinary passwords containing `;` break connections outright, and a value reaching this from an untrusted source can inject keywords such as `Trusted_Connection=yes`.

**Fix:** wrap every value in `{}` with any `}` doubled, or pass keywords to `pyodbc.connect()` as keyword arguments rather than building the string by hand.

Note that [`conftest.py:23`](tests/conftest.py#L23) already braces its driver value correctly — the test helper is more careful than the server.

### 2. Driver names with spaces are not braced — *high* ✓

[`server.py:46`](src/mssql_mcp_server/server.py#L46)

Both the Dockerfile and the CI integration job default `MSSQL_DRIVER` to `ODBC Driver 18 for SQL Server`, which renders unbraced:

```
Driver=ODBC Driver 18 for SQL Server;Server=localhost;...
```

The documented form is `Driver={ODBC Driver 18 for SQL Server};`. Same fix as finding 1 — worth resolving before trusting the new integration job.

### 3. `python -m mssql_mcp_server` fails — *medium* ✓

There is still no `__main__.py` in the package:

```
No module named mssql_mcp_server.__main__; 'mssql_mcp_server' is a package
and cannot be directly executed
```

It is now documented in three places — [README:149](README.md#L149), [README:152](README.md#L152), and [README:185](README.md#L185) — including as troubleshooting step 3 for the "Program Not Found" error, so the remedy offered to users is itself broken.

**Fix:** a two-line `__main__.py` calling `main()` resolves all three references.

### 4. `MCP_TRANSPORT=http` is not a valid transport — *medium* ✓

[README:152](README.md#L152) documents `MCP_TRANSPORT=http`. The installed SDK accepts only:

```python
transport: Literal['stdio', 'sse', 'streamable-http'] = 'stdio'
```

Should be `streamable-http`. (`sse` is the legacy HTTP+SSE path, deprecated in the 2026-07-28 spec.)

### 5. `TrustServerCertificate` defaults to `yes` — *medium*

[`server.py:34`](src/mssql_mcp_server/server.py#L34)

Certificate validation is disabled unless the operator opts back in, which leaves connections open to interception. [README:47](README.md#L47) now documents this as the default, which normalizes it rather than flagging it.

**Fix:** default to `no` and document how to opt in for self-signed development certificates.

### 6. Naive `SELECT` detection, now a regression — *medium*

[`server.py:186`](src/mssql_mcp_server/server.py#L186) and [`server.py:235`](src/mssql_mcp_server/server.py#L235)

String-prefix matching on `SELECT` misses `WITH ... SELECT` (CTEs), leading `--` or `/* */` comments, and `EXEC`. Splitting into two tools has made this worse rather than better: `query_sql` now *rejects* a CTE outright, so a common read-only query is refused by the read-only tool.

**Fix:** check `cursor.description is not None` after execute, rather than pattern-matching the query text.

### 7. No CSV escaping — *low*

[`server.py:125`](src/mssql_mcp_server/server.py#L125), [`server.py:202`](src/mssql_mcp_server/server.py#L202), [`server.py:238`](src/mssql_mcp_server/server.py#L238)

Three copies of the same hand-rolled join. Any value containing a comma, quote, or newline corrupts row alignment, and `NULL` renders as the string `None`.

**Fix:** one shared helper using `csv.writer` over a `StringIO`.

Now that the server is on SDK v2, the more idiomatic fix is `outputSchema` + structured content — returning `{columns, rows, row_count}` as typed data retires the escaping problem entirely and gives execution metadata somewhere clean to live (see [item 17](#17-logging-to-a-file--low)).

---

## New issues introduced by this commit

### 8. Schema is dropped during table validation — *medium*

[`_get_valid_tables()`](src/mssql_mcp_server/server.py#L86) returns `(schema, name)` tuples, but [`server.py:113`](src/mssql_mcp_server/server.py#L113) collapses them to bare names and [`server.py:122`](src/mssql_mcp_server/server.py#L122) queries unqualified:

```python
table_names = {name for _, name in valid_tables}   # schema discarded
...
cursor.execute(f"SELECT TOP 100 * FROM {safe_table}")
```

A table in schema `sales` passes validation and then fails at query time against a `dbo` default schema. Two same-named tables in different schemas are silently ambiguous.

**Fix:** keep the tuple and emit `[schema].[table]`.

### 9. `_validate_identifier` is redundant and over-restrictive — *low*

[`server.py:19-23`](src/mssql_mcp_server/server.py#L19-L23)

Once a name has been matched against `INFORMATION_SCHEMA` it is known-good. The regex's only remaining effect is to reject *real* tables whose names contain spaces, unicode, or a leading digit — all of which bracket-quoting already handles safely.

### 10. One env-dependent test — *low*

[`test_server.py:77`](tests/test_server.py#L77)

Uses `clear=False` where every other config test uses `clear=True`. A developer with `Trusted_Connection=yes` in their ambient environment takes the trusted branch, and the `"UID=user" in conn_str` assertion fails. This is the same class of problem as the pre-migration ordering bug.

### 11. CI health check — *verify on next run*

[`test.yml:52`](.github/workflows/test.yml#L52) shells out to `/opt/mssql-tools18/bin/sqlcmd` *inside* `mcr.microsoft.com/mssql/server:2022-latest`. Recent server images may no longer ship the command-line tools; if they don't, the service never reports healthy. Easy to confirm the first time the job runs.

### 12. Minor tidying

- `requirements-dev.txt` still pins `pytest-asyncio`, though `pytest.ini` dropped the asyncio config and nothing is async anymore.
- The Dockerfile installs dependencies twice — `pip install -r requirements.txt` then `pip install -e .`.

---

## Carried over, lower priority

- No row cap on result sets — `SELECT * FROM huge_table` pulls everything into memory and into the model's context. Superseded by [item 14](#14-cap-result-size--high), which gives this a concrete design.
- Full query text logged at INFO ([`server.py:183`](src/mssql_mcp_server/server.py#L183), [`server.py:227`](src/mssql_mcp_server/server.py#L227)) — will capture sensitive literals. See [item 17](#17-logging-to-a-file--low).
- Inconsistent env-var naming: prefixed `MSSQL_*` alongside bare `TrustServerCertificate` and `Trusted_Connection`, which are collision-prone as process-wide names.
- `publish.yml` uses a stored `PYPI_API_TOKEN` rather than PyPI trusted publishing (OIDC).
- Two connections opened per resource read — one in `_get_valid_tables()`, one for the query itself.

### A note on the read-only split

Splitting `execute_sql` into `query_sql` / `execute_sql` with annotations is a genuine improvement to the tool surface, but it is not enforcement: `execute_sql` still runs `DROP TABLE`. MCP's own guidance treats `readOnlyHint` and `destructiveHint` as a *risk vocabulary* for client UX, not a security boundary. The actual control remains whatever `GRANT`s the DBA has set — which is worth stating plainly in the README's security section. [Item 13](#13-make-destructive-execution-opt-in--high) is the concrete fix.

---

## Hardening backlog

Compared against three comparable MSSQL MCP servers — [aadversteeg/mssqlclient-mcp-server](https://github.com/aadversteeg/mssqlclient-mcp-server) (.NET), [bilims/mcp-sqlserver](https://github.com/bilims/mcp-sqlserver) (TypeScript), and [liliangshan/mcp-server-mssqlserver](https://github.com/liliangshan/mcp-server-mssqlserver) (Node). All three **default to deny**; this server defaults to allowing everything the SQL credentials permit. Items 13-17 close that gap; 18-19 are features rather than defects.

### 13. Make destructive execution opt-in — *high*

Every comparable gates writes by default. aadversteeg ships `DatabaseConfiguration__EnableExecuteQuery=false`; liliangshan has `ALLOW_DDL` / `ALLOW_DROP` / `ALLOW_DELETE`, all defaulting to false; bilims is read-only outright, permitting only `SELECT`, `WITH`, `SHOW`, `DESCRIBE`, and `EXPLAIN`.

Add `MSSQL_ENABLE_EXECUTE_SQL`, defaulting to false. When disabled, **don't register the tool at all** so the model never learns it exists. With module-scope `@mcp.tool()` decorators that means conditional registration — guard the decorator or call `mcp.add_tool()` behind the check — not a runtime error raised inside the tool body.

Worth noting that bilims explicitly permits `WITH`, which is the CTE case [finding 6](#6-naive-select-detection-now-a-regression--medium) currently rejects.

### 14. Cap result size — *high*

Two limits, not one:

- `MSSQL_MAX_ROWS` (bilims defaults to 1000) via `cursor.fetchmany(max_rows)`. bilims additionally injects a `TOP` clause so the cap is enforced server-side rather than after transfer.
- `MSSQL_MAX_CELL_LENGTH` (aadversteeg defaults to 40 characters). Row caps alone are insufficient — a single `NVARCHAR(MAX)` column will exhaust the context window at any row count.

### 15. Command timeout — *medium*

`MSSQL_TIMEOUT_SECONDS`, set on the pyodbc connection via `conn.timeout`. aadversteeg defaults to 30s per command with a 120s ceiling on the whole tool call. A cross-join mistake by the model currently runs until the database gives up on its own.

### 16. A `check_permissions` tool — *low, cheap*

liliangshan exposes one. Once item 13 lands, a tool that reports which operations are enabled lets the model stop guessing and retrying against a wall. Small, and it pairs directly with the gating.

### 17. Logging to a file — *low*

liliangshan writes to a configurable `MCP_LOG_DIR` / `MCP_LOG_FILE`.

One correction worth recording: this server already logs to **stderr**, not stdout ([`server.py:9`](src/mssql_mcp_server/server.py#L9)), and that must stay. Under stdio transport stdout carries the JSON-RPC stream, so writing logs there corrupts the protocol. The argument for `MSSQL_LOG_FILE` is that Claude Desktop discards stderr — not that stdout is the current sink.

A file sink also makes the sensitive-literal exposure noted above worse, so add redaction or a level toggle in the same change.

### 18. Schema discovery tools — *feature, not a defect*

The highest-value feature gap: models write substantially better SQL given types and relationships. bilims ships `describe_table`, `get_foreign_keys`, `list_views`, and `list_databases`.

- `describe_table(table)` — columns, types, nullability, primary key
- `get_foreign_keys(table)` — so `JOIN` clauses come out correct
- `list_views()` — many enterprise databases restrict direct table access

Larger than items 13-17; a release of its own.

### 19. Stored procedures — *feature, later*

Where direct table DML is forbidden, procs are the only access path. aadversteeg has the full shape — `list_stored_procedures`, parameter discovery, typed execution. Worth building only if users ask for it.

---

## Recommended next pass

Small, tightly scoped, and clears the two highest-severity items:

1. Brace and escape the connection string — closes findings 1 and 2 together.
2. Add `__main__.py` — closes finding 3 and its three README references.
3. Correct `MCP_TRANSPORT=http` → `streamable-http` — finding 4.
4. Qualify table names with their schema — finding 8.

Findings 5-7 are worth a follow-up commit; 9-12 are cleanup whenever convenient.

For the release after that, items 13-15 are the ones that change the server's safety posture, and they are all small. Item 13 in particular is a smaller diff than item 18 while doing more for deployability — it is what makes this server installable in environments that currently cannot run it at all.

---

## Appendix: verification evidence

Connection-string injection and unbraced driver (findings 1, 2):

```console
$ MSSQL_PASSWORD='pa;ss}word' python -c "from mssql_mcp_server.server import get_db_config; print(get_db_config()[1])"
Driver=SQL Server;Server=localhost;UID=app;PWD=pa;ss}word;Database=testdb;TrustServerCertificate=yes;Trusted_Connection=no;

$ MSSQL_DRIVER="ODBC Driver 18 for SQL Server" python -c "..."
Driver=ODBC Driver 18 for SQL Server;Server=localhost;UID=app;PWD=pw;...
```

Missing `__main__.py` (finding 3):

```console
$ python -m mssql_mcp_server
No module named mssql_mcp_server.__main__; 'mssql_mcp_server' is a package and cannot be directly executed
```

Valid transports (finding 4):

```console
$ python -c "import inspect; from mcp.server.mcpserver import MCPServer; print(inspect.signature(MCPServer.run))"
(self, transport: "Literal['stdio', 'sse', 'streamable-http']" = 'stdio', **kwargs: 'Any') -> 'None'
```

Test suite status:

```console
$ python -m pytest tests/ -q -k "not TestWithDatabase"
14 passed, 3 deselected in 0.90s
```
