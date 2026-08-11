import os

import pytest

from mssql_mcp_server.server import get_db_config


def test_get_db_config_brace_wraps_password_with_semicolon(monkeypatch):
    monkeypatch.setenv("MSSQL_DRIVER", "ODBC Driver 18 for SQL Server")
    monkeypatch.setenv("MSSQL_HOST", "db.example.com")
    monkeypatch.setenv("MSSQL_PORT", "1433")
    monkeypatch.setenv("MSSQL_USER", "alice")
    monkeypatch.setenv("MSSQL_PASSWORD", 'p@ss;word')
    monkeypatch.setenv("MSSQL_DATABASE", "AppDB")
    monkeypatch.setenv("TrustServerCertificate", "yes")
    monkeypatch.setenv("Trusted_Connection", "no")

    config, conn = get_db_config()
    assert config["server"] == "db.example.com,1433"
    assert "PWD={p@ss;word};" in conn
    assert "Encrypt=yes;" in conn
    assert "Driver={ODBC Driver 18 for SQL Server};" in conn


def test_get_db_config_requires_credentials(monkeypatch):
    monkeypatch.delenv("MSSQL_USER", raising=False)
    monkeypatch.delenv("MSSQL_PASSWORD", raising=False)
    monkeypatch.delenv("MSSQL_DATABASE", raising=False)
    with pytest.raises(ValueError):
        get_db_config()
