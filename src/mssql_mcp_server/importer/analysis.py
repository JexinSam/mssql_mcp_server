"""Analyse a sheet in a streaming pass and re-read it to produce typed rows.

Pass 1 (``analyze``) finds the table and infers a type per column. Pass 2
(``iter_converted_rows``) streams the same rows again, converted for insert.
"""
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from itertools import chain

from .errors import ExcelImportError
from .inference import ColumnSpec, ColumnStats, convert
from .layout import Layout, cell_ref, column_names, filled_values, locate_table
from .sources import ProgressCallback, RawCell, TabularSource

BUFFERED_ROWS = 20
PREVIEW_ROWS = 5
PROGRESS_EVERY = 10_000

Row = tuple[int, list[RawCell]]


@dataclass(frozen=True)
class SheetAnalysis:
    sheet: str
    sheets: list[str]
    layout: Layout
    start_cell: str
    columns: list[ColumnSpec]
    row_count: int
    preview_rows: list[tuple]
    warnings: list[str]


def _non_empty_rows(rows: Iterable[Row]) -> Iterator[Row]:
    return (row for row in rows if filled_values(row[1]))


def _width(cells: Sequence[RawCell | None]) -> int:
    """Number of columns a row spans, ignoring trailing empty cells."""
    filled = filled_values(cells)
    return max(filled) + 1 if filled else 0


def _in_table(cells: Sequence[RawCell | None], first_col: int) -> bool:
    """Whether a row has any content in the table's columns (from ``first_col`` on)."""
    return bool(filled_values(cells[first_col:]))


def _convert_row(cells: list[RawCell], columns: list[ColumnSpec], first_col: int) -> tuple:
    table_cells = cells[first_col:]
    return tuple(
        convert(table_cells[i] if i < len(table_cells) else None, column.sql_type)
        for i, column in enumerate(columns)
    )


def analyze(
    source: TabularSource,
    sheet: str | None = None,
    has_header: bool | None = None,
    header_row: int | None = None,
    progress: ProgressCallback | None = None,
) -> SheetAnalysis:
    sheets = source.sheet_names()
    if not sheets:
        raise ExcelImportError("The workbook has no worksheets.")
    if sheet is None:
        visible = source.visible_sheet_names()
        sheet = visible[0] if visible else sheets[0]
    elif sheet not in sheets:
        raise ExcelImportError(
            f"Sheet '{sheet}' not found. Available sheets: {', '.join(sheets)}."
        )

    rows = _non_empty_rows(source.iter_rows(sheet))
    buffered: list[Row] = []
    table_rows_buffered = 0
    for row in rows:
        buffered.append(row)
        # Rows above a forced header row are buffered too, so that it can be found; the
        # table itself is sampled over BUFFERED_ROWS rows starting at the header.
        if header_row is None or row[0] >= header_row:
            table_rows_buffered += 1
        if table_rows_buffered >= BUFFERED_ROWS:
            break
    layout = locate_table(buffered, has_header, header_row)
    first_col = layout.first_col

    header_cells: list[RawCell | None] = []
    if layout.header_row is not None:
        header_cells = next(cells for number, cells in buffered if number == layout.header_row)
        header_cells = header_cells[first_col:]
    stats = [ColumnStats() for _ in range(_width(header_cells))]

    row_count = 0
    left_rows = 0  # data rows with content left of the table, whether or not also in it
    preview_cells: list[list[RawCell]] = []
    for number, cells in chain(buffered, rows):
        if number < layout.first_data_row:
            continue
        if filled_values(cells[:first_col]):
            left_rows += 1
        if not _in_table(cells, first_col):
            continue
        table_cells = cells[first_col:]
        while len(stats) < _width(table_cells):
            late = ColumnStats()
            if row_count:
                late.observe(None)  # the rows already seen had nothing in this column
            stats.append(late)
        for i, column_stats in enumerate(stats):
            column_stats.observe(table_cells[i] if i < len(table_cells) else None)
        row_count += 1
        if len(preview_cells) < PREVIEW_ROWS:
            preview_cells.append(cells)
        if progress is not None and row_count % PROGRESS_EVERY == 0:
            progress(row_count, None, f"Read {row_count} rows")

    names = column_names(header_cells, len(stats))
    columns = [
        ColumnSpec(name, column_stats.sql_type(), column_stats.nullable)
        for name, column_stats in zip(names, stats)
    ]

    start_row = layout.header_row if layout.header_row is not None else layout.first_data_row
    warnings = []
    above_rows = sum(1 for number, _ in buffered if number < start_row)
    if above_rows:
        warnings.append(
            f"{above_rows} row(s) above the table (before row {start_row}) were skipped."
        )
    if left_rows:
        column_letter = cell_ref(1, first_col).rstrip("0123456789")
        warnings.append(
            f"{left_rows} row(s) have content to the left of the table "
            f"(before column {column_letter}) that will not be imported."
        )
    unresolved = source.unresolved_formula_count(sheet)
    if unresolved > 0:
        warnings.append(
            f"{unresolved} formula cell(s) in the first 1000 rows have no saved result "
            "and will be imported as NULL. Open and save the file in Excel to fix this."
        )

    return SheetAnalysis(
        sheet=sheet,
        sheets=sheets,
        layout=layout,
        start_cell=cell_ref(start_row, first_col),
        columns=columns,
        row_count=row_count,
        preview_rows=[_convert_row(cells, columns, first_col) for cells in preview_cells],
        warnings=warnings,
    )


def iter_converted_rows(source: TabularSource, analysis: SheetAnalysis) -> Iterator[tuple]:
    """Stream the data rows again, each converted to its column's SQL type."""
    first_col = analysis.layout.first_col
    for number, cells in _non_empty_rows(source.iter_rows(analysis.sheet)):
        if number >= analysis.layout.first_data_row and _in_table(cells, first_col):
            yield _convert_row(cells, analysis.columns, first_col)
