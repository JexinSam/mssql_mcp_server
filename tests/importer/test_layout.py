"""Unit tests for locating the table inside a sheet (pure functions, no files needed)."""
import pytest

from mssql_mcp_server.importer.errors import ExcelImportError
from mssql_mcp_server.importer.layout import (
    Layout,
    cell_ref,
    column_names,
    locate_table,
    sanitize_table_name,
)
from mssql_mcp_server.importer.sources import RawCell


def rows(*numbered):
    """Build buffered rows from (excel_row_number, [values]) pairs; None stays None."""
    return [(n, [None if v is None else RawCell(v) for v in vals]) for n, vals in numbered]


def test_sanitize_table_name():
    assert sanitize_table_name("Vendite 2024") == "Vendite_2024"
    assert sanitize_table_name("2024") == "t_2024"
    assert sanitize_table_name("***") == "___"
    assert sanitize_table_name("") == "t_"
    assert len(sanitize_table_name("x" * 200)) == 128
    assert len(sanitize_table_name("1" * 200)) == 128


def test_column_names_dedup_and_blanks():
    cells = [RawCell("Name"), RawCell("name"), None, RawCell(" Città ")]
    assert column_names(cells, 5) == ["Name", "name_2", "Column3", "Città", "Column5"]


def test_column_names_non_text_blank_value_and_truncation():
    cells = [RawCell(2024), RawCell(None), RawCell("x" * 200), RawCell("x" * 200)]
    names = column_names(cells, 4)
    assert names[0] == "2024"
    assert names[1] == "Column2"
    assert names[2] == "x" * 128
    assert names[3] == "x" * 126 + "_2"
    assert all(len(n) <= 128 for n in names)


def test_column_names_collapse_internal_whitespace():
    cells = [RawCell("Amount\n(EUR)"), RawCell("  Unit \t\r\n price  "), RawCell("a  b")]
    assert column_names(cells, 3) == ["Amount (EUR)", "Unit price", "a b"]


def test_column_names_without_header():
    assert column_names([], 3) == ["Column1", "Column2", "Column3"]


def test_cell_ref():
    assert cell_ref(3, 1) == "B3"
    assert cell_ref(1, 0) == "A1"
    assert cell_ref(10, 26) == "AA10"


def test_header_detected():
    layout = locate_table(rows((1, ["id", "name"]), (2, [1, "a"]), (3, [2, "b"])), None, None)
    assert layout == Layout(header_row=1, first_data_row=2, first_col=0)


def test_no_header_all_numbers():
    layout = locate_table(rows((1, [1, 2]), (2, [3, 4])), None, None)
    assert layout == Layout(header_row=None, first_data_row=1, first_col=0)


def test_title_row_skipped():
    layout = locate_table(
        rows((1, ["Sales report"]), (3, ["id", "name"]), (4, [1, "a"])), None, None
    )
    assert layout.header_row == 3
    assert layout.first_data_row == 4


def test_leading_empty_columns():
    layout = locate_table(
        rows((2, [None, None, "id", "name"]), (3, [None, None, 1, "a"])), None, None
    )
    assert layout.first_col == 2
    assert layout.header_row == 2


def test_empty_cell_objects_count_as_empty():
    buffered = [
        (1, [RawCell(None), RawCell("id"), RawCell("name")]),
        (2, [RawCell(None), RawCell(1), RawCell("a")]),
    ]
    assert locate_table(buffered, None, None).first_col == 1


def test_text_only_distinct_header():
    layout = locate_table(
        rows((1, ["city", "country"]), (2, ["Rome", "Italy"]), (3, ["Milan", "Italy"])),
        None,
        None,
    )
    assert layout.header_row == 1


def test_text_only_repeating_first_row():
    layout = locate_table(rows((1, ["a", "b"]), (2, ["a", "c"])), None, None)
    assert layout.header_row is None
    assert layout.first_data_row == 1


def test_header_only_sheet_is_a_header():
    layout = locate_table(rows((1, ["id", "name"])), None, None)
    assert layout == Layout(header_row=1, first_data_row=2, first_col=0)


def test_forced_header_row():
    layout = locate_table(rows((1, [1, 2]), (2, ["x", "y"]), (3, [3, 4])), None, 2)
    assert layout.header_row == 2
    assert layout.first_data_row == 3


def test_forced_has_header_true_on_numbers():
    layout = locate_table(rows((1, [1, 2]), (2, [3, 4])), True, None)
    assert layout.header_row == 1
    assert layout.first_data_row == 2


def test_forced_no_header():
    layout = locate_table(rows((1, ["id", "name"]), (2, [1, "a"])), False, None)
    assert layout.header_row is None
    assert layout.first_data_row == 1


def test_header_row_not_found():
    with pytest.raises(ExcelImportError, match="99"):
        locate_table(rows((1, ["id", "name"]), (2, [1, "a"])), None, 99)


def test_no_rows_is_an_empty_sheet():
    with pytest.raises(ExcelImportError, match="The sheet is empty"):
        locate_table([], None, None)


def test_title_in_an_earlier_column_does_not_move_the_table():
    layout = locate_table(
        rows((1, ["Report"]), (2, [None, "id", "name"]), (3, [None, 1, "a"])), None, None
    )
    assert layout == Layout(header_row=2, first_data_row=3, first_col=1)
