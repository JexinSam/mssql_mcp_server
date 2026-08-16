![Tests](https://github.com/JexinSam/mssql_mcp_server/actions/workflows/test.yml/badge.svg)

# MSSQL MCP Server

MSSQL MCP Server is a **Model Context Protocol (MCP) server** that enables secure and structured interaction with **Microsoft SQL Server (MSSQL)** databases. It allows AI assistants to:
- List available tables
- Read table contents
- Execute SQL queries with controlled access

Built on **MCP SDK v2** (2026-07-28 spec) with support for both **stdio** and **Streamable HTTP** transports.

## Features

- **Secure MSSQL Database Access** through environment variables
- **SQL Injection Protection** with identifier validation
- **Read-Only & Write Tools** with appropriate MCP annotations
- **Windows Authentication** support via Trusted Connection
- **Dual Transport** — stdio (default) and HTTP
- **Docker Support** with pre-configured ODBC drivers
- **Comprehensive Logging** for monitoring queries and operations

## Installation

```bash
pip install mssql-mcp-server
```

## Configuration

Set the following environment variables to configure database access:

```bash
# Required
MSSQL_DATABASE=your_database

# Authentication (choose one):
# Option 1: SQL Server Authentication
MSSQL_USER=your_username
MSSQL_PASSWORD=your_password

# Option 2: Windows / Kerberos Authentication
Trusted_Connection=yes

# Optional
MSSQL_HOST=localhost           # or use MSSQL_SERVER
MSSQL_DRIVER=SQL Server        # default driver
TrustServerCertificate=no      # default: no (set to yes for self-signed certs)
MCP_TRANSPORT=stdio            # or "streamable-http" for Streamable HTTP
```

## Available Tools

| Tool | Description | Annotations |
|------|-------------|-------------|
| `list_tables` | List all tables in the database | Read-only, Idempotent |
| `query_sql` | Execute read-only SELECT queries | Read-only, Idempotent |
| `execute_sql` | Execute any SQL statement (SELECT, INSERT, UPDATE, DELETE, DDL) | Destructive |

## Usage

### With Claude Desktop

Add this configuration to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mssql": {
      "command": "uv",
      "args": [
        "--directory",
        "path/to/mssql_mcp_server",
        "run",
        "mssql_mcp_server"
      ],
      "env": {
        "MSSQL_HOST": "localhost",
        "MSSQL_USER": "your_username",
        "MSSQL_PASSWORD": "your_password",
        "MSSQL_DATABASE": "your_database"
      }
    }
  }
}
```

### With Cursor IDE

Add this to your `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "mssql": {
      "command": "uv",
      "args": [
        "--directory",
        "path/to/mssql_mcp_server",
        "run",
        "mssql_mcp_server"
      ],
      "env": {
        "MSSQL_HOST": "localhost",
        "MSSQL_USER": "your_username",
        "MSSQL_PASSWORD": "your_password",
        "MSSQL_DATABASE": "your_database"
      }
    }
  }
}
```

### With pip install (global)

```json
{
  "mcpServers": {
    "mssql": {
      "command": "mssql_mcp_server",
      "env": {
        "MSSQL_HOST": "localhost",
        "MSSQL_USER": "your_username",
        "MSSQL_PASSWORD": "your_password",
        "MSSQL_DATABASE": "your_database"
      }
    }
  }
}
```

### With Docker

```bash
docker build -t mssql-mcp-server .
docker run -e MSSQL_HOST=host.docker.internal \
           -e MSSQL_USER=your_username \
           -e MSSQL_PASSWORD=your_password \
           -e MSSQL_DATABASE=your_database \
           mssql-mcp-server
```

### Running as a Standalone Server

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server (stdio)
python -m mssql_mcp_server

# Run with HTTP transport
MCP_TRANSPORT=streamable-http python -m mssql_mcp_server
```

### Development & Testing

```bash
# Clone the repository
git clone https://github.com/JexinSam/mssql_mcp_server.git
cd mssql_mcp_server

# Set up a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install development dependencies
pip install -r requirements-dev.txt
pip install -e .

# Run tests
pytest -v

# Test with MCP Inspector
uv run mcp dev src/mssql_mcp_server/server.py
```

## Troubleshooting

### "Program Not Found" Error

This usually means the `mssql_mcp_server` command is not in your PATH. Solutions:

1. **Use `uv`** (recommended): Configure your MCP client to use `uv --directory path/to/mssql_mcp_server run mssql_mcp_server`
2. **Use full path**: Find the install location with `pip show mssql-mcp-server` and use the scripts directory
3. **Use `python -m`**: Run as `python -m mssql_mcp_server`

### MSSQL Driver Issues

The default driver is `SQL Server` (built into Windows). For Linux/macOS or newer features:

1. Install [Microsoft ODBC Driver 18](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server)
2. Set `MSSQL_DRIVER="ODBC Driver 18 for SQL Server"`

### Connection Timeouts

If using `MSSQL_SERVER` from other projects, this server supports both `MSSQL_HOST` and `MSSQL_SERVER` env vars (with `MSSQL_HOST` taking priority).

## Security Considerations

- **Use a dedicated MSSQL user** with minimal privileges.
- **Never use root credentials** or full administrative accounts.
- **Restrict database access** to only necessary operations (e.g. use `GRANT SELECT` only if the model should not modify data). The `readOnlyHint` and `destructiveHint` annotations are for client UX; security is enforced entirely by your database credentials.
- **Enable logging and auditing** for security monitoring.
- **Regularly review permissions** to ensure least privilege access.

## Security Best Practices

For a secure setup:

1. **Create a dedicated MSSQL user** with restricted permissions.
2. **Avoid hardcoding credentials**—use environment variables instead.
3. **Restrict access** to necessary tables and operations only.
4. **Enable SQL Server logging and monitoring** for auditing.
5. **Review database access regularly** to prevent unauthorized access.

For detailed instructions, refer to the **[MSSQL Security Configuration Guide](https://github.com/JexinSam/mssql_mcp_server/blob/main/SECURITY.md)**.

⚠️ **IMPORTANT:** Always follow the **Principle of Least Privilege** when configuring database access.

## License

This project is licensed under the **MIT License**. See the `LICENSE` file for details.

## Contributing

We welcome contributions! To contribute:

1. Fork the repository.
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Commit your changes: `git commit -m 'Add amazing feature'`
4. Push to the branch: `git push origin feature/amazing-feature`
5. Open a **Pull Request**.

---

### Need Help?
For any questions or issues, feel free to open a GitHub **[Issue](https://github.com/JexinSam/mssql_mcp_server/issues)** or reach out to the maintainers.
