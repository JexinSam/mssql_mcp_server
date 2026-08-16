import logging
import os
import re
from pyodbc import connect, Error

from mcp.server.mcpserver import MCPServer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("mssql_mcp_server")

# Valid SQL identifier pattern to prevent SQL injection
_VALID_IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_@#$]*$')


def _validate_identifier(name: str) -> str:
    """Validate and quote a SQL identifier to prevent injection."""
    if not name or not _VALID_IDENTIFIER.match(name):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return f"[{name}]"


def get_db_config():
    """Get database configuration from environment variables."""
    config = {
        "driver": os.getenv("MSSQL_DRIVER", "SQL Server"),
        "server": os.getenv("MSSQL_HOST") or os.getenv("MSSQL_SERVER") or "localhost",
        "user": os.getenv("MSSQL_USER"),
        "password": os.getenv("MSSQL_PASSWORD"),
        "database": os.getenv("MSSQL_DATABASE"),
        "trusted_server_certificate": os.getenv("TrustServerCertificate", "yes"),
        "trusted_connection": os.getenv("Trusted_Connection", "no")
    }

    if not is_valid_config_present(config):
        logger.error("Missing required database configuration. Please check environment variables:")
        logger.error("MSSQL_DATABASE and either MSSQL_USER and MSSQL_PASSWORD, or Trusted_Connection=yes is required")
        raise ValueError("Missing required database configuration")

    # Build connection string — omit UID/PWD for Trusted Connection
    if config["trusted_connection"].lower() == "yes":
        connection_string = (
            f"Driver={config['driver']};"
            f"Server={config['server']};"
            f"Database={config['database']};"
            f"TrustServerCertificate={config['trusted_server_certificate']};"
            f"Trusted_Connection=yes;"
        )
    else:
        connection_string = (
            f"Driver={config['driver']};"
            f"Server={config['server']};"
            f"UID={config['user']};"
            f"PWD={config['password']};"
            f"Database={config['database']};"
            f"TrustServerCertificate={config['trusted_server_certificate']};"
            f"Trusted_Connection={config['trusted_connection']};"
        )

    return config, connection_string


def is_valid_config_present(config):
    """Check if the required database configuration is present."""
    if not config["database"]:
        return False

    if config["user"] and config["password"]:
        return True

    if config["trusted_connection"].lower() == "yes":
        return True

    return False


def _get_connection():
    """Create and return a database connection."""
    _, connection_string = get_db_config()
    return connect(connection_string)


def _get_valid_tables():
    """Fetch the set of valid table names from the database."""
    with _get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_TYPE = 'BASE TABLE';"
            )
            return {(row[0], row[1]) for row in cursor.fetchall()}


# Initialize MCP server
mcp = MCPServer("mssql_mcp_server")


# ─── Resources ───────────────────────────────────────────────────────────────

@mcp.resource("mssql://{table}/data")
def read_table_data(table: str) -> str:
    """Read the first 100 rows from a table.

    Returns CSV-formatted data with column headers.
    """
    logger.info(f"Reading resource for table: {table}")

    # Validate table exists to prevent SQL injection
    valid_tables = _get_valid_tables()
    table_names = {name for _, name in valid_tables}
    if table not in table_names:
        raise ValueError(f"Table not found: {table!r}")

    safe_table = _validate_identifier(table)

    try:
        with _get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT TOP 100 * FROM {safe_table}")
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                result = [",".join(map(str, row)) for row in rows]
                return "\n".join([",".join(columns)] + result)

    except Error as e:
        logger.error(f"Database error reading table {table}: {str(e)}")
        raise RuntimeError(f"Database error: {str(e)}")


# ─── Tools ───────────────────────────────────────────────────────────────────

@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
    }
)
def list_tables() -> str:
    """List all available tables in the connected MSSQL database.

    Returns a list of table names with their schemas.
    """
    logger.info("Listing tables...")
    config, _ = get_db_config()

    try:
        with _get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                    "WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_SCHEMA, TABLE_NAME;"
                )
                tables = cursor.fetchall()
                header = f"Tables in {config['database']}"
                rows = [f"  {row[0]}.{row[1]}" for row in tables]
                return "\n".join([header, "-" * len(header)] + rows)

    except Error as e:
        logger.error(f"Error listing tables: {str(e)}")
        raise RuntimeError(f"Database error: {str(e)}")


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
    }
)
def query_sql(query: str) -> str:
    """Execute a read-only SQL SELECT query on the MSSQL server.

    Use this tool for SELECT statements and other read operations.
    For data modifications (INSERT, UPDATE, DELETE), use execute_sql instead.

    Args:
        query: The SQL SELECT query to execute.
    """
    logger.info(f"Executing read query: {query}")

    stripped = query.strip().upper()
    if not stripped.startswith("SELECT") and stripped != "SHOW TABLES":
        raise ValueError(
            "query_sql only supports SELECT queries. "
            "Use execute_sql for INSERT, UPDATE, DELETE, or other statements."
        )

    # Handle SHOW TABLES as a MySQL compatibility shim
    if stripped == "SHOW TABLES":
        return list_tables()

    try:
        with _get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query)
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                result = [",".join(map(str, row)) for row in rows]
                return "\n".join([",".join(columns)] + result)

    except Exception as e:
        logger.error(f"Error executing query '{query}': {e}")
        raise RuntimeError(f"Error executing query: {str(e)}")


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
    }
)
def execute_sql(query: str) -> str:
    """Execute any SQL statement on the MSSQL server.

    This tool can execute any SQL including INSERT, UPDATE, DELETE,
    CREATE, ALTER, DROP, and SELECT statements. Use query_sql for
    read-only operations when possible.

    Args:
        query: The SQL statement to execute.
    """
    logger.info(f"Executing SQL: {query}")

    try:
        with _get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query)

                # SELECT queries — return results
                if query.strip().upper().startswith("SELECT"):
                    columns = [desc[0] for desc in cursor.description]
                    rows = cursor.fetchall()
                    result = [",".join(map(str, row)) for row in rows]
                    return "\n".join([",".join(columns)] + result)

                # Non-SELECT queries — commit and report
                else:
                    conn.commit()
                    return f"Query executed successfully. Rows affected: {cursor.rowcount}"

    except Exception as e:
        logger.error(f"Error executing SQL '{query}': {e}")
        raise RuntimeError(f"Error executing query: {str(e)}")


# ─── Entry Point ─────────────────────────────────────────────────────────────

def main():
    """Main entry point to run the MCP server."""
    logger.info("Starting MSSQL MCP server...")
    try:
        config, _ = get_db_config()
        logger.info(f"Database config: {config['server']}/{config['database']}")
    except ValueError:
        logger.warning("Database config not set — server will start but tools will fail until configured")

    transport = os.getenv("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
