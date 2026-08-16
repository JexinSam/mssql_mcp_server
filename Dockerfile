FROM python:3.12-slim

# Install ODBC driver dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        gnupg2 \
        apt-transport-https \
        unixodbc-dev && \
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg && \
    curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list > /etc/apt/sources.list.d/mssql-release.list && \
    apt-get update && \
    ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy application
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

# Default to ODBC Driver 18 in Docker
ENV MSSQL_DRIVER="ODBC Driver 18 for SQL Server"

# Default to stdio transport
ENV MCP_TRANSPORT="stdio"

ENTRYPOINT ["mssql_mcp_server"]
