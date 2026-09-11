"""Repo entry point kept for `python3 server.py`; the package lives in finamatik_ops_mcp/ (pip install finamatik-ops-mcp)."""
from finamatik_ops_mcp.server import main, mcp  # noqa: F401

if __name__ == "__main__":
    main()
