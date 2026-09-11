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

## Install and run

```
pip install finamatik-ops-mcp
finamatik-ops-mcp                        # stdio, what Claude Desktop uses; seeds sample data on first start
OPS_MCP_READ_ONLY=1 finamatik-ops-mcp    # read only: the write tools are not registered at all
```

Data lives in `~/.finamatik-ops-mcp/` (`ops.db` and `audit.jsonl`). Move it with `OPS_MCP_HOME`, or point at specific files with `OPS_MCP_DB` and `OPS_MCP_AUDIT`.

Claude Desktop, `claude_desktop_config.json`:

```json
{ "mcpServers": { "ops-mcp": { "command": "finamatik-ops-mcp", "env": { "OPS_MCP_READ_ONLY": "0" } } } }
```

Claude Code: `claude mcp add ops-mcp -- finamatik-ops-mcp`

Remote: `OPS_MCP_TRANSPORT=streamable-http finamatik-ops-mcp` serves `/mcp` on port 8000; put authentication in front of it.

From source:

```
git clone https://github.com/finamatik/ops-mcp-server && cd ops-mcp-server
pip install -e ".[test]"
python3 server.py                       # same server, run from the checkout
python3 -m pytest tests -q              # 6 end to end tests through a real MCP client
npx @modelcontextprotocol/inspector --config inspector.config.json --server ops-mcp
```

Requires Python 3.10 or later and the official `mcp` SDK 1.27 or later (pinned below 2.0; the 2.x SDK renamed FastMCP).

## Files

`finamatik_ops_mcp/server.py` tools, resources, prompt and the `main()` entry point. `finamatik_ops_mcp/backend.py` data layer and sample seed (40 contacts, 25 deals, 30 tasks, 10 SKUs, 20 orders). `server.py` and `backend.py` at the root are thin shims so `python3 server.py` still works from a checkout. `tests/test_server.py`. `inspector.config.json`. `server.json` is the MCP registry manifest. `.github/workflows/publish.yml` publishes a GitHub release to PyPI with trusted publishing.

<!-- mcp-name: io.github.finamatik/ops-mcp-server -->

MIT licence, copyright Finamatik Business Solutions FZE LLC. Questions and production use: info@finamatik.com.
