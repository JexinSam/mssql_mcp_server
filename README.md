![Tests](https://github.com/JexinSam/mssql_mcp_server/actions/workflows/test.yml/badge.svg)
[![smithery badge](https://smithery.ai/badge/@JexinSam/mssql_mcp_server)](https://smithery.ai/server/@JexinSam/mssql_mcp_server)

# MSSQL MCP Server

MSSQL MCP Server is a **Model Context Protocol (MCP) server** that enables secure and structured interaction with **Microsoft SQL Server (MSSQL)** databases. It allows AI assistants to:
- List available tables
- Read table contents
- Execute SQL queries with controlled access

This ensures safer database exploration, strict permission enforcement, and logging of database interactions.

## Features

- **Secure MSSQL Database Access** through environment variables
- **Controlled Query Execution** with error handling
- **Table Listing & Data Retrieval**
- **Comprehensive Logging** for monitoring queries and operations

## Installation

### Installing via Smithery

To install MSSQL MCP Server for Claude Desktop automatically via [Smithery](https://smithery.ai/server/@JexinSam/mssql_mcp_server):

```bash
npx -y @smithery/cli install @JexinSam/mssql_mcp_server --client claude
```

### Manual Installation
```bash
pip install mssql-mcp-server
```

## Configuration

Set the following environment variables to configure database access:

```bash
MSSQL_DRIVER=mssql_driver
MSSQL_HOST=localhost
MSSQL_USER=your_username
MSSQL_PASSWORD=your_password
MSSQL_DATABASE=your_database
#optional
TrustServerCertificate=yes
Trusted_Connection=no
```

## Usage

### With Claude Desktop

To integrate with **Claude Desktop**, add this configuration to `claude_desktop_config.json`:

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
        "MSSQL_DRIVER": "mssql_driver",
        "MSSQL_HOST": "localhost",
        "MSSQL_USER": "your_username",
        "MSSQL_PASSWORD": "your_password",
        "MSSQL_DATABASE": "your_database"
      }
    }
  }
}
```

### Running as a Standalone Server

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server
python -m mssql_mcp_server
```

## Development

```bash
# Clone the repository
git clone https://github.com/yourusername/mssql_mcp_server.git
cd mssql_mcp_server

# Set up a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install development dependencies
pip install -r requirements-dev.txt

# Run tests
pytest
```

## Security Considerations

- **Use a dedicated MSSQL user** with minimal privileges.
- **Never use root credentials** or full administrative accounts.
- **Restrict database access** to only necessary operations.
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

