import pytest
from unittest.mock import patch, MagicMock
from pyodbc import Error
from mssql_mcp_server.server import (
    mcp,
    get_db_config,
    is_valid_config_present,
    list_tables,
    query_sql,
    execute_sql,
    _get_connection,
)


# ─── Unit Tests (no DB required) ────────────────────────────────────────────

class TestServerInitialization:
    def test_server_name(self):
        """Test that the server initializes with correct name."""
        assert mcp.name == "mssql_mcp_server"




class TestConfigValidation:
    def test_valid_config_with_credentials(self):
        config = {
            "database": "testdb",
            "user": "user",
            "password": "pass",
            "trusted_connection": "no",
        }
        assert is_valid_config_present(config) is True

    def test_valid_config_with_trusted_connection(self):
        config = {
            "database": "testdb",
            "user": None,
            "password": None,
            "trusted_connection": "yes",
        }
        assert is_valid_config_present(config) is True

    def test_invalid_config_no_database(self):
        config = {
            "database": None,
            "user": "user",
            "password": "pass",
            "trusted_connection": "no",
        }
        assert is_valid_config_present(config) is False

    def test_invalid_config_no_credentials_or_trusted(self):
        config = {
            "database": "testdb",
            "user": None,
            "password": None,
            "trusted_connection": "no",
        }
        assert is_valid_config_present(config) is False

    @patch.dict("os.environ", {
        "MSSQL_DATABASE": "testdb",
        "MSSQL_USER": "user",
        "MSSQL_PASSWORD": "pass",
    }, clear=True)
    def test_get_db_config_with_env(self):
        config, conn_str = get_db_config()
        assert config["database"] == "testdb"
        assert config["user"] == "user"
        assert "UID={user}" in conn_str
        assert "PWD={pass}" in conn_str

    @patch.dict("os.environ", {
        "MSSQL_DATABASE": "testdb",
        "Trusted_Connection": "yes",
    }, clear=True)
    def test_get_db_config_trusted_connection(self):
        config, conn_str = get_db_config()
        assert "Trusted_Connection=yes" in conn_str
        assert "UID=" not in conn_str
        assert "PWD=" not in conn_str

    @patch.dict("os.environ", {
        "MSSQL_SERVER": "remotehost",
        "MSSQL_DATABASE": "testdb",
        "MSSQL_USER": "user",
        "MSSQL_PASSWORD": "pass",
    }, clear=True)
    def test_get_db_config_server_fallback(self):
        config, _ = get_db_config()
        assert config["server"] == "remotehost"

    @patch.dict("os.environ", {}, clear=True)
    def test_get_db_config_missing_raises(self):
        with pytest.raises(ValueError, match="Missing required database configuration"):
            get_db_config()


class TestQuerySqlValidation:
    @patch("mssql_mcp_server.server._get_connection")
    def test_rejects_non_select(self, mock_conn):
        with pytest.raises(ValueError, match="query_sql only supports SELECT"):
            query_sql("INSERT INTO users VALUES (1, 'test')")

    @patch("mssql_mcp_server.server._get_connection")
    def test_rejects_delete(self, mock_conn):
        with pytest.raises(ValueError, match="query_sql only supports SELECT"):
            query_sql("DELETE FROM users")

    @patch("mssql_mcp_server.server._get_connection")
    def test_rejects_update(self, mock_conn):
        with pytest.raises(ValueError, match="query_sql only supports SELECT"):
            query_sql("UPDATE users SET name='test'")


class TestCertificateErrorHelp:
    """TrustServerCertificate flipped from 'yes' to 'no' in v1.0.0, so a cert
    failure needs to point users at the setting that changed."""

    ENV = {
        "MSSQL_HOST": "dbhost",
        "MSSQL_DATABASE": "testdb",
        "MSSQL_USER": "user",
        "MSSQL_PASSWORD": "pass",
    }

    CERT_ERROR = Error(
        "08001",
        "[08001] [Microsoft][ODBC Driver 18 for SQL Server]SSL Provider: "
        "The certificate chain was issued by an authority that is not trusted.",
    )

    @patch.dict("os.environ", ENV, clear=True)
    @patch("mssql_mcp_server.server.connect")
    def test_cert_failure_explains_setting(self, mock_connect):
        mock_connect.side_effect = self.CERT_ERROR
        with pytest.raises(RuntimeError, match="TrustServerCertificate") as exc:
            _get_connection()
        assert "v1.0.0" in str(exc.value)
        assert "dbhost" in str(exc.value)
        # Original driver error is preserved, not swallowed
        assert isinstance(exc.value.__cause__, Error)

    @patch.dict("os.environ", {**ENV, "TrustServerCertificate": "yes"}, clear=True)
    @patch("mssql_mcp_server.server.connect")
    def test_no_hint_when_already_trusting(self, mock_connect):
        """Already opted in, so the cert hint would be misleading."""
        mock_connect.side_effect = self.CERT_ERROR
        with pytest.raises(Error):
            _get_connection()

    @patch.dict("os.environ", ENV, clear=True)
    @patch("mssql_mcp_server.server.connect")
    def test_unrelated_errors_propagate(self, mock_connect):
        mock_connect.side_effect = Error("28000", "[28000] Login failed for user 'user'.")
        with pytest.raises(Error):
            _get_connection()


# ─── Integration Tests (require DB) ─────────────────────────────────────────

@pytest.mark.skipif(
    not all([
        pytest.importorskip("pyodbc"),
    ]),
    reason="pyodbc not available"
)
class TestWithDatabase:
    """Tests that require a live MSSQL connection.

    These are skipped when no database is configured.
    """

    @pytest.fixture(autouse=True)
    def _setup_env(self):
        """Skip if database is not configured."""
        import os
        if not os.getenv("MSSQL_DATABASE"):
            pytest.skip("Database configuration not available")

    def test_list_tables(self):
        result = list_tables()
        assert isinstance(result, str)
        assert "Tables in" in result

    def test_query_sql_select(self):
        result = query_sql("SELECT 1 AS test_col")
        assert "test_col" in result

    def test_execute_sql_select(self):
        result = execute_sql("SELECT 1 AS test_col")
        assert "test_col" in result