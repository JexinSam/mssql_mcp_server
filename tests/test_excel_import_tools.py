import os
import uuid
from unittest.mock import AsyncMock, MagicMock

import pyodbc
import pytest

from mcp.server.mcpserver.exceptions import ToolError

from mssql_mcp_server import server
from mssql_mcp_server.importer import service
from mssql_mcp_server.server import import_excel, mcp, preview_excel_import, query_sql

SALES = [
    ["Region", "Amount", "Code"],
    ["North", 10.5, "007"],
    ["South", 20, "010"],
    ["East", 30, "011"],
    ["West", 40, "012"],
    ["Centre", 50, "013"],
    ["Islands", 60, "014"],
    ["Coast", 70, "015"],
]


def _fake_connection(*fetchone_results):
    conn = MagicMock()
    conn.cursor.return_value.fetchone.side_effect = list(fetchone_results)
    return conn


@pytest.mark.anyio
async def test_preview_output(make_xlsx, monkeypatch):
    path = make_xlsx({"Sales": SALES})
    # nothing exists except schema dbo
    monkeypatch.setattr(server, "_get_connection", lambda: _fake_connection((1,), None))
    out = await preview_excel_import(file_path=str(path))
    assert "Header detected: yes" in out
    assert "Target table: dbo.Sales — available" in out
    assert "First 5 rows:" in out and out.count("\n") >= 10
    assert "Data rows: 7" in out
    assert "Warnings:" not in out
    assert "  Region  nvarchar(" in out
    assert "North,10.5,007" in out
    assert "Islands" not in out  # only 5 preview rows


@pytest.mark.anyio
async def test_preview_reports_unavailable_and_warnings(make_xlsx, monkeypatch):
    path = make_xlsx({"Sales": [["a", "b"], [1, "=1+1"], [2, 3]]})
    monkeypatch.setattr(server, "_get_connection", lambda: _fake_connection((1,), (1,)))
    out = await preview_excel_import(file_path=str(path))
    assert "NOT available: Table 'dbo.Sales' already exists." in out
    assert "Warnings:" in out
    assert "formula" in out


@pytest.mark.anyio
async def test_progress_relayed(make_xlsx, monkeypatch):
    rows = [["id", "v"]] + [[i, i] for i in range(10_001)]
    path = make_xlsx({"Big": rows})
    monkeypatch.setattr(server, "_get_connection", lambda: _fake_connection((1,), None))
    ctx = MagicMock()
    ctx.report_progress = AsyncMock()
    await preview_excel_import(file_path=str(path), ctx=ctx)
    assert ctx.report_progress.await_count >= 1


@pytest.mark.anyio
async def test_import_refuses_taken_table(make_xlsx, monkeypatch):
    path = make_xlsx({"Sales": SALES})
    monkeypatch.setattr(server, "_get_connection", lambda: _fake_connection((1,), (1,)))
    with pytest.raises(ToolError, match="already exists"):
        await import_excel(file_path=str(path))


@pytest.mark.anyio
async def test_import_relays_loading_progress(make_xlsx, monkeypatch):
    path = make_xlsx({"Sales": SALES})
    monkeypatch.setattr(server, "_get_connection", lambda: _fake_connection((1,), None))

    def fake_create(conn, schema, table, columns, rows, total, progress=None):
        progress(7, total, "Inserted 7 of 7 rows")
        return 7

    monkeypatch.setattr(service, "create_and_load", fake_create)
    ctx = MagicMock()
    ctx.report_progress = AsyncMock()
    await import_excel(file_path=str(path), ctx=ctx)
    # The load phase is offset past the analysis phase, so progress never goes down.
    ctx.report_progress.assert_any_await(14, 14, "Inserted 7 of 7 rows")


@pytest.mark.anyio
async def test_taken_table_message_reaches_client(make_xlsx, monkeypatch):
    path = make_xlsx({"Sales": SALES})
    monkeypatch.setattr(server, "_get_connection", lambda: _fake_connection((1,), (1,)))
    with pytest.raises(ToolError) as excinfo:
        await mcp.call_tool("import_excel", {"file_path": str(path)})
    message = str(excinfo.value)
    assert "already exists" in message
    assert "ask for another name" in message


@pytest.mark.anyio
async def test_missing_file_message_reaches_client(tmp_path):
    missing = tmp_path / "nope.xlsx"
    for tool in ("preview_excel_import", "import_excel"):
        with pytest.raises(ToolError, match="File not found"):
            await mcp.call_tool(tool, {"file_path": str(missing)})


@pytest.mark.anyio
async def test_database_error_message_reaches_client(make_xlsx, monkeypatch):
    path = make_xlsx({"Sales": SALES})
    monkeypatch.setattr(server, "_get_connection", lambda: _fake_connection((1,), None))

    def boom(*args, **kwargs):
        raise RuntimeError("Database error: disk full")

    monkeypatch.setattr(service, "create_and_load", boom)
    with pytest.raises(ToolError, match="Database error: disk full"):
        await mcp.call_tool("import_excel", {"file_path": str(path)})


@pytest.mark.anyio
async def test_missing_db_config_message_reaches_client(make_xlsx, monkeypatch):
    path = make_xlsx({"Sales": SALES})
    for var in ("MSSQL_DATABASE", "MSSQL_USER", "MSSQL_PASSWORD", "Trusted_Connection"):
        monkeypatch.delenv(var, raising=False)
    for tool in ("preview_excel_import", "import_excel"):
        with pytest.raises(ToolError, match="Missing required database configuration"):
            await mcp.call_tool(tool, {"file_path": str(path)})


@pytest.mark.anyio
async def test_driver_error_message_reaches_client(make_xlsx, monkeypatch):
    path = make_xlsx({"Sales": SALES})

    def refuse():
        raise pyodbc.Error("08001", "[08001] Login timeout expired")

    monkeypatch.setattr(server, "_get_connection", refuse)
    with pytest.raises(ToolError, match=r"Database error: .*Login timeout expired"):
        await mcp.call_tool("preview_excel_import", {"file_path": str(path)})


@pytest.mark.anyio
async def test_tools_registered():
    tools = {t.name: t for t in await mcp.list_tools()}
    preview = tools["preview_excel_import"].annotations
    assert (preview.read_only_hint, preview.destructive_hint, preview.idempotent_hint) == (
        True,
        False,
        True,
    )
    imp = tools["import_excel"].annotations
    assert (imp.read_only_hint, imp.destructive_hint, imp.idempotent_hint) == (
        False,
        False,
        False,
    )
    for tool in (tools["preview_excel_import"], tools["import_excel"]):
        assert "ctx" not in tool.input_schema["properties"]
        assert tool.input_schema["required"] == ["file_path"]


@pytest.mark.anyio
async def test_import_excel_documents_its_args():
    tools = {t.name: t for t in await mcp.list_tools()}

    def args_block(description):
        return description[description.index("Args:"):].strip()

    preview_args = args_block(tools["preview_excel_import"].description)
    assert "file_path:" in preview_args and "header_row:" in preview_args
    assert args_block(tools["import_excel"].description) == preview_args


class TestWithDatabase:
    """Tests that require a live MSSQL connection; skipped when none is configured."""

    @pytest.fixture(autouse=True)
    def _setup_env(self):
        if not os.getenv("MSSQL_DATABASE"):
            pytest.skip("Database configuration not available")

    @pytest.mark.anyio
    async def test_preview_import_then_duplicate_fails(self, make_xlsx):
        name = f"xlimp_{uuid.uuid4().hex[:12]}"
        path = make_xlsx({"Sales": SALES})
        try:
            preview = await preview_excel_import(file_path=str(path), table_name=name)
            assert f"Target table: dbo.{name} — available" in preview

            result = await import_excel(file_path=str(path), table_name=name)
            assert f"Imported 7 rows into dbo.{name}" in result

            counted = query_sql(f"SELECT COUNT(*) AS n FROM dbo.{name}")
            assert counted.splitlines()[1] == "7"

            with pytest.raises(ToolError):
                await import_excel(file_path=str(path), table_name=name)
        finally:
            conn = server._get_connection()
            try:
                conn.cursor().execute(f"DROP TABLE IF EXISTS [dbo].[{name}]")
                conn.commit()
            finally:
                conn.close()
