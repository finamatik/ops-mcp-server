"""Compatibility shim: the data layer now lives in finamatik_ops_mcp/backend.py."""
from finamatik_ops_mcp.backend import DB_PATH, SCHEMA, STAGES, OpsBackend, seed  # noqa: F401
