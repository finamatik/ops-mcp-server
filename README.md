# Ops MCP server

An MCP server (Python, official `mcp` SDK, FastMCP) that lets Claude Desktop, Claude Code or any MCP client work with a company's operations data through ten typed, audited tools over contacts, deals, tasks, notes, inventory and sales orders, plus three resources and a briefing prompt. Built and tested on sample data by [Finamatik](https://finamatik.com/work/ops-mcp-server). The SQLite backend is one class (`backend.py`) and is the only thing to swap for a real CRM, ERP or project system.

| Tool | Kind | Guardrail |
|---|---|---|
| `search_contacts(query, limit)` | read | query at least 2 characters, limit capped at 50 |
| `get_contact(contact_id)` | read | full view: deals, open tasks, last notes, last orders |
| `list_tasks(status, assignee, due_before, limit)` | read | ISO date validated |
| `check_inventory(sku, low_stock_only)` | read | |
| `pipeline_summary()` | read | deals by stage, open value, closing in 30 days |
| `add_note(contact_id, text)` | write | 1 to 2,000 characters, contact must exist |
| `create_task(contact_id, title, due_date, assignee)` | write | no past due dates, assignee enum |
| `complete_task(task_id)` | write | idempotent |
| `update_deal_stage(deal_id, stage)` | write | stage enum |
| `create_order(contact_id, sku, qty, dry_run=true)` | write | stock check; preview first, commit only with dry_run=false |

Safety model: tool annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`) so clients can ask before writes; `OPS_MCP_READ_ONLY=1` removes the write tools from the tool list entirely; every call is appended to `audit.jsonl` (timestamp, tool, arguments, ok or error, duration); validation errors come back as MCP tool errors the model can act on.

## Run

```
pip install "mcp>=1.27" pytest
python3 server.py                       # stdio, what Claude Desktop uses; seeds sample data on first start
python3 -m pytest tests -q              # 6 end to end tests through a real MCP client
npx @modelcontextprotocol/inspector --config inspector.config.json --server ops-mcp
```

Claude Desktop, `claude_desktop_config.json`:

```json
{ "mcpServers": { "ops-mcp": { "command": "python3", "args": ["/absolute/path/ops-mcp-server/server.py"], "env": { "OPS_MCP_READ_ONLY": "0" } } } }
```

Claude Code: `claude mcp add ops-mcp -- python3 /absolute/path/ops-mcp-server/server.py`

Remote: `OPS_MCP_TRANSPORT=streamable-http python3 server.py` serves `/mcp` on port 8000; put authentication in front of it.

## Files

`server.py` tools, resources and prompt. `backend.py` data layer and sample seed (40 contacts, 25 deals, 30 tasks, 10 SKUs, 20 orders). `tests/test_server.py`. `inspector.config.json`. `audit.jsonl` is created on the first call.

MIT licence, copyright Finamatik Business Solutions FZE LLC. Questions and production use: info@finamatik.com.
