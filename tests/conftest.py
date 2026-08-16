# tests/conftest.py
import pytest
import os
from pyodbc import connect, Error


@pytest.fixture(scope="session")
def db_config():
    """Return database configuration for tests."""
    return {
        "driver": os.getenv("MSSQL_DRIVER", "ODBC Driver 17 for SQL Server"),
        "server": os.getenv("MSSQL_HOST") or os.getenv("MSSQL_SERVER") or "localhost",
        "user": os.getenv("MSSQL_USER", "sa"),
        "password": os.getenv("MSSQL_PASSWORD", "testpassword"),
        "database": os.getenv("MSSQL_DATABASE", "master"),
    }


@pytest.fixture(scope="session")
def connection_string(db_config):
    """Build a connection string from db_config."""
    return (
        f"Driver={{{db_config['driver']}}};"
        f"Server={db_config['server']};"
        f"UID={db_config['user']};"
        f"PWD={db_config['password']};"
        f"Database={db_config['database']};"
        f"TrustServerCertificate=yes;"
    )


@pytest.fixture(scope="session")
def mssql_connection(connection_string):
    """Create a test database connection."""
    try:
        conn = connect(connection_string)
        cursor = conn.cursor()

        # Create a test table using MSSQL-compatible syntax
        cursor.execute("""
            IF NOT EXISTS (
                SELECT * FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_NAME = 'test_table'
            )
            BEGIN
                CREATE TABLE test_table (
                    id INT IDENTITY(1,1) PRIMARY KEY,
                    name NVARCHAR(255),
                    value INT
                )
            END
        """)
        conn.commit()

        yield conn

        # Cleanup
        cursor.execute("DROP TABLE IF EXISTS test_table")
        conn.commit()
        cursor.close()
        conn.close()

    except Error as e:
        pytest.skip(f"MSSQL connection not available: {e}")


@pytest.fixture(scope="session")
def mssql_cursor(mssql_connection):
    """Create a test cursor."""
    cursor = mssql_connection.cursor()
    yield cursor
    cursor.close()