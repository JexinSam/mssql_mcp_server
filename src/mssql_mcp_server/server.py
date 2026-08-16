import csv
import io
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


def _escape_conn_str_value(value: str | None) -> str:
    """Safely brace connection string values to prevent injection."""
    if not value:
        return ""
    # Double any closing braces and wrap in braces
    safe_val = str(value).replace("}", "}}")
    return f"{{{safe_val}}}"


def _format_csv(columns: list[str], rows: list[tuple]) -> str:
    """Format columns and rows as a valid CSV string."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(columns)
    writer.writerows(rows)
    return output.getvalue().strip()


def get_db_config():
    """Get database configuration from environment variables."""
    config = {
        "driver": os.getenv("MSSQL_DRIVER", "SQL Server"),
        "server": os.getenv("MSSQL_HOST") or os.getenv("MSSQL_SERVER") or "localhost",
        "user": os.getenv("MSSQL_USER"),
        "password": os.getenv("MSSQL_PASSWORD"),
        "database": os.getenv("MSSQL_DATABASE"),
        "trusted_server_certificate": os.getenv("TrustServerCertificate", "no"),
        "trusted_connection": os.getenv("Trusted_Connection", "no")
    }

    if not is_valid_config_present(config):
        logger.error("Missing required database configuration. Please check environment variables:")
        logger.error("MSSQL_DATABASE and either MSSQL_USER and MSSQL_PASSWORD, or Trusted_Connection=yes is required")
        raise ValueError("Missing required database configuration")

    # Build connection string safely
    base_conn_str = (
        f"Driver={_escape_conn_str_value(config['driver'])};"
        f"Server={_escape_conn_str_value(config['server'])};"
        f"Database={_escape_conn_str_value(config['database'])};"
        f"TrustServerCertificate={_escape_conn_str_value(config['trusted_server_certificate'])};"
    )

    if config["trusted_connection"].lower() == "yes":
        connection_string = base_conn_str + "Trusted_Connection=yes;"
    else:
        connection_string = (
            base_conn_str +
            f"UID={_escape_conn_str_value(config['user'])};"
            f"PWD={_escape_conn_str_value(config['password'])};"
            f"Trusted_Connection={_escape_conn_str_value(config['trusted_connection'])};"
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
    """Fetch the set of valid table names from the database as a set of (schema, table) tuples."""
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

@mcp.resource("mssql://{schema}/{table}/data")
def read_table_data(schema: str, table: str) -> str:
    """Read the first 100 rows from a table.

    Returns CSV-formatted data with column headers.
    """
    logger.info(f"Reading resource for table: {schema}.{table}")

    valid_tables = _get_valid_tables()
    if (schema, table) not in valid_tables:
        raise ValueError(f"Table not found: {schema}.{table}")

    # Safely quote the schema and table name
    safe_schema = f"[{schema}]"
    safe_table = f"[{table}]"

    try:
        with _get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT TOP 100 * FROM {safe_schema}.{safe_table}")
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                return _format_csv(columns, rows)

    except Error as e:
        logger.error(f"Database error reading table {schema}.{table}: {str(e)}")
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

    # Only allow SELECT, WITH, SHOW queries
    if not re.match(r"^\s*(SELECT|WITH|SHOW)\b", query, re.IGNORECASE):
        raise ValueError(
            "query_sql only supports SELECT/WITH/SHOW queries. "
            "Use execute_sql for INSERT, UPDATE, DELETE, or other statements."
        )

    # Handle SHOW TABLES as a MySQL compatibility shim
    if query.strip().upper() == "SHOW TABLES":
        return list_tables()

    try:
        with _get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query)
                if cursor.description is None:
                    raise ValueError("Query did not return a result set.")
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                return _format_csv(columns, rows)

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

                # If query returns a result set
                if cursor.description is not None:
                    columns = [desc[0] for desc in cursor.description]
                    rows = cursor.fetchall()
                    return _format_csv(columns, rows)
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
    if transport.lower() == "http":
        transport = "streamable-http"
    
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
