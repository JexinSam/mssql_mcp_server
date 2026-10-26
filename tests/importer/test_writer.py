import os
import uuid
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
import pyodbc
from pyodbc import Error

from mssql_mcp_server.importer.errors import ExcelImportError
from mssql_mcp_server.importer.inference import ColumnSpec
from mssql_mcp_server.importer.writer import (
    create_and_load,
    quote_ident,
    schema_exists,
    table_exists,
)

COLS = [ColumnSpec("id", "int", False), ColumnSpec("note", "nvarchar(max)", True)]


def _conn(exists: bool = False):
    conn = MagicMock()
    cur = conn.cursor.return_value
    cur.fetchone.return_value = (1,) if exists else None
    return conn, cur


def _sqls(cur):
    return [c.args[0] for c in cur.execute.call_args_list]


def test_quote_ident():
    assert quote_ident("a]b") == "[a]]b]"
    assert quote_ident("plain") == "[plain]"


def test_schema_exists_uses_parameter():
    conn, cur = _conn(exists=True)
    assert schema_exists(cur, "dbo") is True
    sql, *params = cur.execute.call_args.args
    assert "sys.schemas" in sql and "?" in sql
    assert params == ["dbo"]
    cur.fetchone.return_value = None
    assert schema_exists(cur, "nope") is False


def test_table_exists_uses_parameters():
    conn, cur = _conn(exists=True)
    assert table_exists(cur, "dbo", "t") is True
    sql, *params = cur.execute.call_args.args
    assert "sys.objects" in sql
    assert params == ["dbo", "t"]
    cur.fetchone.return_value = None
    assert table_exists(cur, "dbo", "x") is False


def test_create_sql_and_batches():
    conn, cur = _conn()
    n = create_and_load(
        conn, "dbo", "t", COLS, iter([(1, "a"), (2, None), (3, "c")]), total=3, batch_size=2
    )
    assert n == 3
    assert any(
        "CREATE TABLE [dbo].[t] ([id] int NOT NULL, [note] nvarchar(max) NULL)" in s
        for s in _sqls(cur)
    )
    assert cur.executemany.call_count == 2
    assert cur.executemany.call_args_list[0].args == (
        "INSERT INTO [dbo].[t] ([id], [note]) VALUES (?, ?)",
        [(1, "a"), (2, None)],
    )
    assert cur.fast_executemany is True
    conn.commit.assert_called_once()
    conn.rollback.assert_not_called()


def test_nvarchar_max_input_sizes():
    conn, cur = _conn()
    create_and_load(conn, "dbo", "t", COLS, [(1, "a")], total=1)
    cur.setinputsizes.assert_called_with([None, (pyodbc.SQL_WVARCHAR, 0, 0)])


def test_identifiers_are_quoted():
    conn, cur = _conn()
    cols = [ColumnSpec("we]ird", "int", True)]
    create_and_load(conn, "s]x", "t]y", cols, [(1,)], total=1)
    assert any("CREATE TABLE [s]]x].[t]]y] ([we]]ird] int NULL)" in s for s in _sqls(cur))


def test_autocommit_forced_off_before_any_statement():
    conn, cur = _conn()
    conn.autocommit = True
    seen = []
    cur.execute.side_effect = lambda *a, **k: seen.append(conn.autocommit)
    create_and_load(conn, "dbo", "t", COLS, [(1, "a")], total=1)
    assert conn.autocommit is False
    assert seen and all(a is False for a in seen)


def test_no_columns_rejected_before_touching_db():
    conn, cur = _conn()
    with pytest.raises(ExcelImportError, match="No columns"):
        create_and_load(conn, "dbo", "t", [], [], total=0)
    conn.cursor.assert_not_called()
    conn.rollback.assert_not_called()
    conn.commit.assert_not_called()


def test_cursor_closed():
    conn, cur = _conn()
    create_and_load(conn, "dbo", "t", COLS, [(1, "a")], total=1)
    cur.close.assert_called_once()
    conn, cur = _conn(exists=True)
    with pytest.raises(ExcelImportError):
        create_and_load(conn, "dbo", "t", COLS, [(1, "a")], total=1)
    cur.close.assert_called_once()


def test_existing_table_rolls_back():
    conn, cur = _conn(exists=True)
    with pytest.raises(ExcelImportError, match="already exists"):
        create_and_load(conn, "dbo", "t", COLS, [(1, "a")], total=1)
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()
    assert not any("CREATE TABLE" in s for s in _sqls(cur))
    cur.executemany.assert_not_called()


def test_insert_error_rolls_back():
    conn, cur = _conn()
    cur.executemany.side_effect = Error("23000", "[23000] NULL not allowed")
    with pytest.raises(RuntimeError, match="Database error"):
        create_and_load(conn, "dbo", "t", COLS, [(1, "a")], total=1)
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()


def test_non_database_error_rolls_back_and_propagates():
    conn, cur = _conn()

    def rows():
        yield (1, "a")
        raise KeyError("boom")

    with pytest.raises(KeyError):
        create_and_load(conn, "dbo", "t", COLS, rows(), total=2, batch_size=1)
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()


def test_zero_rows_creates_table():
    conn, cur = _conn()
    assert create_and_load(conn, "dbo", "t", COLS, [], total=0) == 0
    assert any("CREATE TABLE [dbo].[t]" in s for s in _sqls(cur))
    cur.executemany.assert_not_called()
    conn.commit.assert_called_once()


def test_progress_per_batch():
    conn, cur = _conn()
    progress = MagicMock()
    create_and_load(
        conn, "dbo", "t", COLS, [(1, "a"), (2, "b"), (3, "c")], total=3, progress=progress, batch_size=2
    )
    assert progress.call_args_list[0].args == (2, 3, "Inserted 2 of 3 rows")
    assert progress.call_args_list[1].args == (3, 3, "Inserted 3 of 3 rows")
    assert progress.call_count == 2


# ─── Integration Tests (require DB) ─────────────────────────────────────────

class TestWithDatabase:
    """Tests that require a live MSSQL connection; skipped when none is configured."""

    @pytest.fixture(autouse=True)
    def _setup_env(self):
        if not os.getenv("MSSQL_DATABASE"):
            pytest.skip("Database configuration not available")

    @staticmethod
    def _drop(name: str) -> None:
        from mssql_mcp_server.server import _get_connection

        conn = _get_connection()
        try:
            conn.cursor().execute(f"DROP TABLE IF EXISTS [dbo].[{name}]")
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _count(name: str) -> int | None:
        """Row count, or None when the table does not exist."""
        from mssql_mcp_server.server import _get_connection

        conn = _get_connection()
        try:
            cur = conn.cursor()
            if not table_exists(cur, "dbo", name):
                return None
            cur.execute(f"SELECT COUNT(*) FROM [dbo].[{name}]")
            return cur.fetchone()[0]
        finally:
            conn.close()

    def test_create_load_and_refuse_existing(self):
        from mssql_mcp_server.server import _get_connection

        name = f"excel_import_test_{uuid.uuid4().hex[:8]}"
        try:
            conn = _get_connection()
            try:
                n = create_and_load(
                    conn, "dbo", name, COLS, [(1, "a"), (2, None), (3, "c")], total=3
                )
            finally:
                conn.close()
            assert n == 3
            assert self._count(name) == 3

            conn = _get_connection()
            try:
                with pytest.raises(ExcelImportError):
                    create_and_load(conn, "dbo", name, COLS, [(9, "z")], total=1)
            finally:
                conn.close()
            assert self._count(name) == 3
        finally:
            self._drop(name)

    def test_mixed_types_round_trip(self):
        from mssql_mcp_server.server import _get_connection

        name = f"excel_import_test_{uuid.uuid4().hex[:8]}"
        cols = [
            ColumnSpec("b", "bit", True),
            ColumnSpec("big", "bigint", True),
            ColumnSpec("dc", "decimal(12,4)", True),
            ColumnSpec("d", "date", True),
            ColumnSpec("dt", "datetime2", True),
            ColumnSpec("s", "nvarchar(20)", True),
            ColumnSpec("mx", "nvarchar(max)", True),
        ]
        rows = [
            (True, 2**40, Decimal("1.5"), date(2024, 2, 29),
             datetime(2024, 1, 2, 3, 4, 5, 123456), "h\u00e9llo", "x" * 5000),
            (False, -(2**40), Decimal("-1234.5678"), date(1999, 12, 31),
             datetime(2000, 1, 1, 0, 0, 0, 1), "", "y"),
            (None, None, Decimal("0.0001"), None, None, None, None),
        ]
        try:
            conn = _get_connection()
            try:
                create_and_load(conn, "dbo", name, cols, rows, total=3)
            finally:
                conn.close()
            conn = _get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT b, big, dc, d, dt, s, mx FROM [dbo].[" + name + "] ORDER BY big"
                )
                got = [tuple(r) for r in cur.fetchall()]
            finally:
                conn.close()
            # ORDER BY big puts the NULL row first (NULLs sort lowest).
            assert got[0] == rows[2]
            assert got[1] == rows[1]
            assert got[2] == rows[0]
            assert got[2][2] == Decimal("1.5000")
            assert got[2][4].microsecond == 123456
            assert got[1][4].microsecond == 1
        finally:
            self._drop(name)

    def test_failed_load_leaves_no_table(self):
        from mssql_mcp_server.server import _get_connection

        name = f"excel_import_test_{uuid.uuid4().hex[:8]}"
        try:
            conn = _get_connection()
            try:
                with pytest.raises(RuntimeError, match="Database error"):
                    # second row violates NOT NULL on "id"
                    create_and_load(conn, "dbo", name, COLS, [(1, "a"), (None, "b")], total=2)
            finally:
                conn.close()
            assert self._count(name) is None
        finally:
            self._drop(name)
