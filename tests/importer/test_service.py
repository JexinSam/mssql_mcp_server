from unittest.mock import MagicMock

import pytest

from mssql_mcp_server.importer import service
from mssql_mcp_server.importer.errors import ExcelImportError
from mssql_mcp_server.importer.service import ImportResult, preview_import, run_import

ROWS = [["id", "name"], [1, "a"], [2, "b"], [3, "c"]]


def _connect(*fetchone_results):
    """A get_connection factory; successive cursor.fetchone() calls return the given values."""
    conn = MagicMock()
    conn.cursor.return_value.fetchone.side_effect = list(fetchone_results)
    return conn, lambda: conn


def test_default_table_name_is_sheet(make_xlsx):
    path = make_xlsx({"Vendite 2024": ROWS})
    conn, get_connection = _connect((1,), None)  # schema exists, table does not
    result = preview_import(get_connection, str(path))
    assert result.table == "Vendite_2024"
    assert result.schema == "dbo"
    assert result.available is True
    assert result.unavailable_reason is None
    assert result.analysis.row_count == 3
    conn.close.assert_called_once()


def test_given_table_name_is_used_as_is(make_xlsx):
    path = make_xlsx({"S": ROWS})
    _, get_connection = _connect((1,), None)
    assert preview_import(get_connection, str(path), table_name="My Table").table == "My Table"


def test_table_taken(make_xlsx):
    path = make_xlsx({"Sales": ROWS})
    _, get_connection = _connect((1,), (1,))
    result = preview_import(get_connection, str(path))
    assert result.available is False
    assert "already exists" in result.unavailable_reason
    assert "ask for another name" in result.unavailable_reason
    assert "'dbo.Sales'" in result.unavailable_reason


def test_schema_missing(make_xlsx):
    path = make_xlsx({"Sales": ROWS})
    _, get_connection = _connect(None)
    result = preview_import(get_connection, str(path), schema="x")
    assert result.available is False
    assert result.unavailable_reason == "Schema 'x' does not exist."


def test_invalid_table_name(make_xlsx):
    path = make_xlsx({"Sales": ROWS})
    _, get_connection = _connect()
    with pytest.raises(ExcelImportError):
        preview_import(get_connection, str(path), table_name="x" * 129)


@pytest.mark.parametrize("bad", ["a\x00b", "a\nb", "a\tb"])
def test_control_characters_rejected(make_xlsx, bad):
    path = make_xlsx({"Sales": ROWS})
    _, get_connection = _connect()
    with pytest.raises(ExcelImportError):
        preview_import(get_connection, str(path), table_name=bad)
    with pytest.raises(ExcelImportError):
        preview_import(get_connection, str(path), schema=bad)


def test_empty_schema_rejected(make_xlsx):
    path = make_xlsx({"Sales": ROWS})
    _, get_connection = _connect()
    with pytest.raises(ExcelImportError):
        preview_import(get_connection, str(path), schema="")


def test_run_import_refuses_unavailable(make_xlsx, monkeypatch):
    path = make_xlsx({"Sales": ROWS})
    create = MagicMock()
    monkeypatch.setattr(service, "create_and_load", create)
    conn, get_connection = _connect((1,), (1,))
    with pytest.raises(ExcelImportError, match="already exists"):
        run_import(get_connection, str(path))
    create.assert_not_called()
    conn.close.assert_called_once()


def test_run_import_loads(make_xlsx, monkeypatch):
    path = make_xlsx({"Sales": ROWS})
    seen = {}

    def fake_create(conn, schema, table, columns, rows, total, progress=None):
        seen.update(schema=schema, table=table, columns=columns, rows=list(rows), total=total)
        return 3

    monkeypatch.setattr(service, "create_and_load", fake_create)
    conn, get_connection = _connect((1,), None)
    result = run_import(get_connection, str(path), table_name="T", schema="dbo")
    assert isinstance(result, ImportResult)
    assert result.rows == 3
    assert (result.schema, result.table) == ("dbo", "T")
    assert [c.name for c in result.columns] == ["id", "name"]
    assert result.seconds >= 0
    assert seen["rows"] == [(1, "a"), (2, "b"), (3, "c")]
    assert seen["total"] == 3
    assert (seen["schema"], seen["table"]) == ("dbo", "T")
    conn.close.assert_called_once()


def test_run_import_closes_connection_on_error(make_xlsx, monkeypatch):
    path = make_xlsx({"Sales": ROWS})

    def boom(*args, **kwargs):
        raise RuntimeError("Database error: x")

    monkeypatch.setattr(service, "create_and_load", boom)
    conn, get_connection = _connect((1,), None)
    with pytest.raises(RuntimeError):
        run_import(get_connection, str(path))
    conn.close.assert_called_once()


@pytest.mark.parametrize("name", ["#tmp", "##global"])
def test_temp_table_names_rejected(make_xlsx, monkeypatch, name):
    path = make_xlsx({"Sales": ROWS})
    create = MagicMock()
    monkeypatch.setattr(service, "create_and_load", create)
    _, get_connection = _connect((1,), None)
    message = "Table names starting with '#' create temporary tables and are not allowed."
    with pytest.raises(ExcelImportError) as exc:
        preview_import(get_connection, str(path), table_name=name)
    assert str(exc.value) == message
    with pytest.raises(ExcelImportError) as exc:
        run_import(get_connection, str(path), table_name=name)
    assert str(exc.value) == message
    create.assert_not_called()


def test_run_import_progress_is_monotonic(make_xlsx, monkeypatch):
    from mssql_mcp_server.importer import analysis

    monkeypatch.setattr(analysis, "PROGRESS_EVERY", 2)
    path = make_xlsx({"Sales": [["id"]] + [[i] for i in range(1, 8)]})  # 7 data rows

    def fake_create(conn, schema, table, columns, rows, total, progress=None):
        rows = list(rows)
        for inserted in (3, 6, 7):  # batches of 3
            progress(inserted, total, f"Inserted {inserted} of {total} rows")
        return len(rows)

    monkeypatch.setattr(service, "create_and_load", fake_create)
    _, get_connection = _connect((1,), None)
    calls = []
    run_import(get_connection, str(path), progress=lambda *call: calls.append(call))
    assert calls == [
        (2, None, "Read 2 rows"),
        (4, None, "Read 4 rows"),
        (6, None, "Read 6 rows"),
        (10, 14, "Inserted 3 of 7 rows"),
        (13, 14, "Inserted 6 of 7 rows"),
        (14, 14, "Inserted 7 of 7 rows"),
    ]
    done = [call[0] for call in calls]
    assert all(a < b for a, b in zip(done, done[1:]))
