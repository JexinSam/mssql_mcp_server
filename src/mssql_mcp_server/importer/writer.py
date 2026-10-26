"""Create a new SQL Server table and bulk-load rows into it in one transaction.

Never writes to an existing object: if ``schema.table`` already exists nothing is
created or loaded. SQL Server DDL is transactional, so a rollback removes the
CREATE TABLE together with any inserted rows.
"""
import logging
from collections.abc import Iterable, Iterator
from itertools import islice

import pyodbc

from .errors import ExcelImportError
from .inference import ColumnSpec
from .sources import ProgressCallback

logger = logging.getLogger("mssql_mcp_server")

DEFAULT_BATCH_SIZE = 10_000


def quote_ident(name: str) -> str:
    """Quote an identifier as ``[name]``, doubling any ``]``."""
    return "[" + name.replace("]", "]]") + "]"


def schema_exists(cursor, schema: str) -> bool:
    cursor.execute("SELECT 1 FROM sys.schemas WHERE name = ?", schema)
    return cursor.fetchone() is not None


def table_exists(cursor, schema: str, table: str) -> bool:
    """True if any object (table, view, ...) with this name exists in the schema."""
    cursor.execute(
        "SELECT 1 FROM sys.objects o JOIN sys.schemas s ON o.schema_id = s.schema_id "
        "WHERE s.name = ? AND o.name = ?",
        schema,
        table,
    )
    return cursor.fetchone() is not None


def _batches(rows: Iterable[tuple], size: int) -> Iterator[list[tuple]]:
    iterator = iter(rows)
    while batch := list(islice(iterator, size)):
        yield batch


def create_and_load(
    conn,
    schema: str,
    table: str,
    columns: list[ColumnSpec],
    rows: Iterable[tuple],
    total: int,
    progress: ProgressCallback | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Create ``schema.table`` and insert ``rows`` in a single transaction.

    ``conn.autocommit`` is forced to False. Any error rolls back (removing the new
    table too) and is re-raised; ``pyodbc.Error`` becomes ``RuntimeError``.
    Returns the number of rows inserted.
    """
    if not columns:
        raise ExcelImportError("No columns to import")
    target = f"{quote_ident(schema)}.{quote_ident(table)}"
    cursor = None
    try:
        # The one-transaction guarantee needs autocommit off; enforce it before any statement.
        conn.autocommit = False
        cursor = conn.cursor()
        if table_exists(cursor, schema, table):
            raise ExcelImportError(f"Table {schema}.{table} already exists")

        column_defs = ", ".join(
            f"{quote_ident(c.name)} {c.sql_type} {'NULL' if c.nullable else 'NOT NULL'}"
            for c in columns
        )
        cursor.execute(f"CREATE TABLE {target} ({column_defs})")

        insert_sql = (
            f"INSERT INTO {target} ({', '.join(quote_ident(c.name) for c in columns)}) "
            f"VALUES ({', '.join(['?'] * len(columns))})"
        )
        input_sizes = [
            (pyodbc.SQL_WVARCHAR, 0, 0) if c.sql_type == "nvarchar(max)" else None
            for c in columns
        ]
        cursor.fast_executemany = True

        inserted = 0
        for batch in _batches(rows, batch_size):
            cursor.setinputsizes(input_sizes)
            cursor.executemany(insert_sql, batch)
            inserted += len(batch)
            if progress is not None:
                progress(inserted, total, f"Inserted {inserted} of {total} rows")

        conn.commit()
        return inserted
    except BaseException as e:
        try:
            conn.rollback()
        except pyodbc.Error:
            logger.exception("Rollback failed after import error")
        if isinstance(e, pyodbc.Error):
            raise RuntimeError(f"Database error: {str(e)}") from e
        raise
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except pyodbc.Error:
                logger.exception("Closing cursor failed")
