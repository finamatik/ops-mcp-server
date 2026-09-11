"""Ops MCP server, lets Claude (Desktop / Code / API) read and act on a company's operations data
through a small set of typed, audited tools.

  finamatik-ops-mcp                          # stdio transport (Claude Desktop / Claude Code / MCP Inspector)
  OPS_MCP_READ_ONLY=1 finamatik-ops-mcp      # read-only: write tools are not even registered
  OPS_MCP_TRANSPORT=streamable-http finamatik-ops-mcp   # HTTP transport on :8000/mcp for remote use

Data lives in $OPS_MCP_HOME (default ~/.finamatik-ops-mcp): ops.db, seeded with sample data on first start, and audit.jsonl.
Override either file with OPS_MCP_DB and OPS_MCP_AUDIT.

Sample project on sample data. The backend (backend.py) is SQLite; the same tool contract sits on top of
any CRM/ERP/PM API (HubSpot, Procore, SAP B1, Odoo...) by swapping that one class.
"""
import functools
import json
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from .backend import DB_PATH, STAGES, OpsBackend

READ_ONLY = os.environ.get("OPS_MCP_READ_ONLY", "0") in ("1", "true", "yes")
HOME = Path(os.environ.get("OPS_MCP_HOME", Path.home() / ".finamatik-ops-mcp"))
AUDIT = Path(os.environ.get("OPS_MCP_AUDIT", HOME / "audit.jsonl"))
db = OpsBackend(DB_PATH)

mcp = FastMCP(
    "ops-mcp",
    instructions=("Operations data for a field-services company: contacts, deals, tasks, notes, inventory and orders. "
                  "Use search_contacts before get_contact. Write tools are audited; create_order previews by default (dry_run=true), "
                  "confirm with the user before calling it with dry_run=false." + (" THIS SESSION IS READ-ONLY." if READ_ONLY else "")),
)

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


def audited(fn):
    """Every tool call then one JSON line: who/what/args/outcome/duration. Ship these to your SIEM or a Sheet."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        t0 = time.time()
        rec = {"ts": datetime.utcnow().isoformat(timespec="milliseconds") + "Z", "tool": fn.__name__, "args": kw or list(a), "read_only_mode": READ_ONLY}
        try:
            out = fn(*a, **kw)
            rec.update(ok=True, ms=round((time.time() - t0) * 1000, 1))
            return out
        except ToolError as e:
            rec.update(ok=False, error=str(e), ms=round((time.time() - t0) * 1000, 1))
            raise
        except ValueError as e:
            rec.update(ok=False, error=str(e), ms=round((time.time() - t0) * 1000, 1))
            raise ToolError(str(e)) from e
        finally:
            AUDIT.parent.mkdir(parents=True, exist_ok=True)
            with open(AUDIT, "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
    return wrapper


def _iso_date(s: str, field: str) -> str:
    try:
        return date.fromisoformat(s).isoformat()
    except Exception:
        raise ToolError(f"{field} must be an ISO date (YYYY-MM-DD), got {s!r}")


def _limit(n: int, cap: int = 50) -> int:
    if n < 1:
        raise ToolError("limit must be >= 1")
    return min(n, cap)


# ------------------------------------------------------------------ read tools
@mcp.tool(annotations=READ)
@audited
def search_contacts(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Find contacts by name, company, email, phone or city (case-insensitive substring). Returns up to `limit` (max 50)."""
    if len(query.strip()) < 2:
        raise ToolError("query must be at least 2 characters")
    return db.search_contacts(query, _limit(limit))


@mcp.tool(annotations=READ)
@audited
def get_contact(contact_id: str) -> dict[str, Any]:
    """Full view of one contact: profile, deals, open tasks, last 5 notes, last 5 orders. Use the id from search_contacts (e.g. C-1007)."""
    c = db.get_contact(contact_id.strip().upper())
    if not c:
        raise ToolError(f"contact {contact_id} not found, use search_contacts first")
    return c


@mcp.tool(annotations=READ)
@audited
def list_tasks(status: Literal["open", "done", "all"] = "open", assignee: Optional[str] = None, due_before: Optional[str] = None, limit: int = 25) -> list[dict[str, Any]]:
    """List tasks, default open ones, optionally filtered by assignee (hamza|sana|ali) and due date (YYYY-MM-DD). Sorted by due date."""
    return db.list_tasks(status, assignee, _iso_date(due_before, "due_before") if due_before else None, _limit(limit))


@mcp.tool(annotations=READ)
@audited
def check_inventory(sku: Optional[str] = None, low_stock_only: bool = False) -> list[dict[str, Any]]:
    """Stock levels. Pass a SKU for one item, or low_stock_only=true for items at/below their reorder level."""
    return db.inventory(sku.strip().upper() if sku else None, low_stock_only)


@mcp.tool(annotations=READ)
@audited
def pipeline_summary() -> dict[str, Any]:
    """Deals by stage (count + AED value), open pipeline total, and open deals expected to close in the next 30 days."""
    return db.pipeline_summary()


# ------------------------------------------------------------------ write tools (not registered in read-only mode)
if not READ_ONLY:
    @mcp.tool(annotations=WRITE)
    @audited
    def add_note(contact_id: str, text: str) -> dict[str, Any]:
        """Append a timestamped note to a contact's timeline (max 2,000 characters)."""
        if not text.strip() or len(text) > 2000:
            raise ToolError("text must be 1 to 2000 characters")
        return db.add_note(contact_id.strip().upper(), text.strip())

    @mcp.tool(annotations=WRITE)
    @audited
    def create_task(contact_id: str, title: str, due_date: str, assignee: Literal["hamza", "sana", "ali"]) -> dict[str, Any]:
        """Create an open task for a contact. due_date is YYYY-MM-DD and may not be in the past."""
        d = _iso_date(due_date, "due_date")
        if d < date.today().isoformat():
            raise ToolError(f"due_date {d} is in the past")
        if not (3 <= len(title.strip()) <= 200):
            raise ToolError("title must be 3 to 200 characters")
        return db.create_task(contact_id.strip().upper(), title.strip(), d, assignee)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    @audited
    def complete_task(task_id: str) -> dict[str, Any]:
        """Mark a task done (idempotent)."""
        return db.complete_task(task_id.strip().upper())

    @mcp.tool(annotations=WRITE)
    @audited
    def update_deal_stage(deal_id: str, stage: Literal["new", "qualified", "proposal", "negotiation", "won", "lost"]) -> dict[str, Any]:
        """Move a deal to a new pipeline stage."""
        return db.update_deal_stage(deal_id.strip().upper(), stage)

    @mcp.tool(annotations=WRITE)
    @audited
    def create_order(contact_id: str, sku: str, qty: int, dry_run: bool = True) -> dict[str, Any]:
        """Place a sales order for a contact. Checks stock first. dry_run=true (default) only returns a priced preview ,
        confirm with the user, then call again with dry_run=false to commit and decrement inventory."""
        return db.create_order(contact_id.strip().upper(), sku.strip().upper(), int(qty), dry_run)


# ------------------------------------------------------------------ resources + prompt
@mcp.resource("ops://schema")
def schema() -> str:
    """Data dictionary so the model knows what the ids and fields mean."""
    return json.dumps({"contacts": "C-####, name, company, email, phone, city, owner (hamza|sana|ali), tags",
                       "deals": f"D-####, stage in {STAGES}, value_aed, expected_close",
                       "tasks": "T-####, status open|done, due_date, assignee", "notes": "N-####, free text on a contact",
                       "inventory": "SKU, qty, reorder_level (low stock when qty <= reorder_level), unit_price_aed, supplier",
                       "orders": "SO-####, sku, qty, total_aed, status confirmed|shipped|delivered",
                       "mode": "read-only" if READ_ONLY else "read-write (writes audited)"}, indent=1)


@mcp.resource("ops://inventory/low-stock")
def low_stock() -> str:
    return json.dumps(db.inventory(low_stock_only=True), indent=1)


@mcp.resource("ops://pipeline/summary")
def pipeline_resource() -> str:
    return json.dumps(db.pipeline_summary(), indent=1)


@mcp.prompt()
def daily_ops_briefing(owner: str = "hamza") -> str:
    """Morning briefing: overdue tasks, deals closing soon, low stock, with suggested next actions."""
    return (f"You are the operations assistant. Using the ops-mcp tools, prepare a short briefing for {owner} for {date.today().isoformat()}: "
            f"1) list_tasks(status='open', assignee='{owner}', due_before='{date.today().isoformat()}') then overdue items; "
            "2) pipeline_summary() then deals closing in 30 days and total open value; 3) check_inventory(low_stock_only=true) then what to reorder. "
            "Finish with three concrete next actions. Do not create tasks or orders unless asked.")


def main() -> None:
    """Console entry point (finamatik-ops-mcp)."""
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    transport = os.environ.get("OPS_MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
