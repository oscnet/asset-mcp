FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp \
    ASSET_MCP_CONFIG=/data/config.local.yaml \
    ASSET_MCP_DATABASE=/data/portfolio.db \
    ASSET_MCP_VAULT_FILE=/data/credentials.enc

WORKDIR /app

RUN groupadd --gid 10001 assetmcp \
    && useradd --uid 10001 --gid assetmcp --no-log-init --create-home assetmcp

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir ".[web]" \
    && mkdir -p /data/backups \
    && chown -R assetmcp:assetmcp /data

USER assetmcp

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=2).read() == b'ok'"

CMD ["sh", "-c", "asset-mcp init --path \"$ASSET_MCP_CONFIG\" && exec asset-mcp-web --server.address=0.0.0.0 --server.port=8501"]
