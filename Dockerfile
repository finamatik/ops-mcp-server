FROM python:3.12-slim

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir .

# SQLite database and audit log live here; mount a volume to keep them.
ENV OPS_MCP_HOME=/data
RUN mkdir -p /data

# stdio transport: the MCP client starts this container and talks over stdin/stdout.
CMD ["finamatik-ops-mcp"]
