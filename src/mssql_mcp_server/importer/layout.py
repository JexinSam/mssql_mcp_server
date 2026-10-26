"""Locate the table inside a sheet and derive table and column names.

Pure functions over rows already buffered in memory; no I/O.
"""
import re
from collections.abc import Sequence
from dataclasses import dataclass

from .errors import ExcelImportError
from .inference import to_text
from .sources import RawCell

MAX_IDENTIFIER_LENGTH = 128
_INVALID_NAME_CHARS = re.compile(r"[^A-Za-z0-9_]")


@dataclass(frozen=True)
class Layout:
    """Where the table sits. Rows are Excel 1-based numbers, ``first_col`` is 0-based."""

    header_row: int | None
    first_data_row: int
    first_col: int


def filled_values(cells: Sequence[RawCell | None]) -> dict[int, object]:
    """Column index -> value for the cells that hold a value; missing/empty cells are skipped."""
    return {i: c.value for i, c in enumerate(cells) if c is not None and c.value is not None}


def sanitize_table_name(sheet_name: str) -> str:
    """Turn a sheet name into a safe SQL identifier."""
    name = _INVALID_NAME_CHARS.sub("_", sheet_name)[:MAX_IDENTIFIER_LENGTH]
    if not name or name[0].isdigit():
        name = ("t_" + name)[:MAX_IDENTIFIER_LENGTH]
    return name


def cell_ref(row: int, col: int) -> str:
    """A1-style reference for a 1-based row and 0-based column, e.g. (3, 1) -> 'B3'."""
    letters = ""
    n = col + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return f"{letters}{row}"


def _looks_like_header(
    candidate: list[RawCell | None], below: list[list[RawCell | None]]
) -> bool:
    header = filled_values(candidate)
    if not all(isinstance(v, str) for v in header.values()):
        return False
    if not below:  # header-only sheet
        return True
    below_values = [filled_values(cells) for cells in below]
    if any(not isinstance(v, str) for values in below_values for v in values.values()):
        return True
    # Text everywhere: a header has distinct names and none of them repeats below it.
    if len(set(header.values())) != len(header):
        return False
    return not any(values.get(i) == name for values in below_values for i, name in header.items())


def locate_table(
    rows: list[tuple[int, list[RawCell | None]]],
    has_header: bool | None,
    header_row: int | None,
) -> Layout:
    """Find the header and first data row in the first non-empty rows of a sheet."""
    if not rows:
        raise ExcelImportError("The sheet is empty")

    if header_row is not None:
        numbers = [number for number, _ in rows]
        if header_row not in numbers:
            raise ExcelImportError(
                f"Row {header_row} is empty or does not exist, so it cannot be the header row."
            )
        table_start = numbers.index(header_row)
        has_header = True
    else:
        table_start = next(
            (i for i, (_, cells) in enumerate(rows) if len(filled_values(cells)) >= 2), 0
        )
    # Rows above the table (titles, notes) must not influence where the table's columns start.
    table_rows = rows[table_start:]
    first_col = min(min(filled_values(cells)) for _, cells in table_rows)
    first_row, first_cells = table_rows[0]
    if has_header is None:
        has_header = _looks_like_header(first_cells, [cells for _, cells in table_rows[1:]])
    if has_header:
        return Layout(first_row, first_row + 1, first_col)
    return Layout(None, first_row, first_col)


def column_names(header_cells: list[RawCell | None], width: int) -> list[str]:
    """Unique, non-empty column names for ``width`` columns, from header cells if any."""
    names: list[str] = []
    seen: set[str] = set()
    for i in range(width):
        cell = header_cells[i] if i < len(header_cells) else None
        # Collapse whitespace runs (incl. Alt+Enter line breaks) and trim the ends.
        text = ""
        if cell is not None and cell.value is not None:
            text = " ".join(to_text(cell).split())
        name = (text or f"Column{i + 1}")[:MAX_IDENTIFIER_LENGTH]
        if name.lower() in seen:
            n = 2
            while True:
                suffix = f"_{n}"
                candidate = name[: MAX_IDENTIFIER_LENGTH - len(suffix)] + suffix
                if candidate.lower() not in seen:
                    break
                n += 1
            name = candidate
        seen.add(name.lower())
        names.append(name)
    return names
