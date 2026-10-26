"""Preview and run an Excel import: analyse the sheet, check the target, create and load.

Both entry points are synchronous and blocking; the MCP tools run them in a worker
thread. ``get_connection`` is called once per request and the connection is always
closed here, because ``create_and_load`` leaves it open.
"""
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pyodbc import Connection, Cursor

from .analysis import SheetAnalysis, analyze, iter_converted_rows
from .errors import ExcelImportError
from .inference import ColumnSpec
from .layout import MAX_IDENTIFIER_LENGTH, sanitize_table_name
from .sources import ProgressCallback, open_source
from .writer import create_and_load, schema_exists, table_exists

logger = logging.getLogger("mssql_mcp_server")


@dataclass(frozen=True)
class PreviewResult:
    file_path: str
    schema: str
    table: str
    analysis: SheetAnalysis
    available: bool
    unavailable_reason: str | None


@dataclass(frozen=True)
class ImportResult:
    schema: str
    table: str
    rows: int
    seconds: float
    columns: list[ColumnSpec]


def _validate_identifier(kind: str, value: str) -> None:
    """User-given names are quoted, not rewritten, but must be a plausible identifier."""
    if not 1 <= len(value) <= MAX_IDENTIFIER_LENGTH:
        raise ExcelImportError(
            f"Invalid {kind} name: it must be between 1 and {MAX_IDENTIFIER_LENGTH} characters long."
        )
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ExcelImportError(f"Invalid {kind} name: control characters are not allowed.")
    if kind == "table" and value.startswith("#"):
        raise ExcelImportError(
            "Table names starting with '#' create temporary tables and are not allowed."
        )


def _unavailable_reason(conn: Connection, schema: str, table: str) -> str | None:
    cursor: Cursor = conn.cursor()
    try:
        if not schema_exists(cursor, schema):
            return f"Schema '{schema}' does not exist."
        if table_exists(cursor, schema, table):
            return (
                f"Table '{schema}.{table}' already exists. "
                "Tell the user this name is taken and ask for another name."
            )
        return None
    finally:
        cursor.close()


def preview_import(
    get_connection: Callable[[], Connection],
    file_path: str,
    table_name: str | None = None,
    schema: str = "dbo",
    sheet: str | None = None,
    has_header: bool | None = None,
    header_row: int | None = None,
    progress: ProgressCallback | None = None,
) -> PreviewResult:
    """Analyse the sheet and check whether the target table can be created. Writes nothing."""
    _validate_identifier("schema", schema)
    if table_name:
        _validate_identifier("table", table_name)
    with open_source(Path(file_path)) as source:
        analysis = analyze(source, sheet, has_header, header_row, progress)
    table = table_name if table_name else sanitize_table_name(analysis.sheet)

    conn = get_connection()
    try:
        reason = _unavailable_reason(conn, schema, table)
    finally:
        conn.close()
    return PreviewResult(
        file_path=file_path,
        schema=schema,
        table=table,
        analysis=analysis,
        available=reason is None,
        unavailable_reason=reason,
    )


def _load_progress(progress: ProgressCallback | None, row_count: int) -> ProgressCallback | None:
    """Progress for the load phase, offset past the analysis phase.

    Progress must never go down within one tool call: the analysis phase reports
    ``(rows_read, None)``, so the load phase reports ``(row_count + inserted, 2 * row_count)``.
    """
    if progress is None:
        return None

    def report(inserted: float, _total: float | None, message: str) -> None:
        progress(row_count + inserted, 2 * row_count, message)

    return report


def run_import(
    get_connection: Callable[[], Connection],
    file_path: str,
    table_name: str | None = None,
    schema: str = "dbo",
    sheet: str | None = None,
    has_header: bool | None = None,
    header_row: int | None = None,
    progress: ProgressCallback | None = None,
) -> ImportResult:
    """Create the target table and load the sheet into it in one transaction."""
    started = time.monotonic()
    _validate_identifier("schema", schema)
    if table_name:
        _validate_identifier("table", table_name)
    with open_source(Path(file_path)) as source:
        analysis = analyze(source, sheet, has_header, header_row, progress)
        table = table_name if table_name else sanitize_table_name(analysis.sheet)

        conn = get_connection()
        try:
            reason = _unavailable_reason(conn, schema, table)
            if reason is not None:
                raise ExcelImportError(reason)
            rows = create_and_load(
                conn,
                schema,
                table,
                analysis.columns,
                iter_converted_rows(source, analysis),
                total=analysis.row_count,
                progress=_load_progress(progress, analysis.row_count),
            )
        finally:
            conn.close()

    seconds = time.monotonic() - started
    logger.info(f"Imported {rows} rows into {schema}.{table} in {seconds:.1f}s")
    return ImportResult(
        schema=schema, table=table, rows=rows, seconds=seconds, columns=analysis.columns
    )
