"""End-to-end tests: a real MCP client talks to the server over stdio (the same path Claude Desktop uses)."""
import asyncio
import json
import os
from datetime import date, timedelta
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def run(coro):
    return asyncio.run(coro)


async def session(tmp_path, read_only=False):
    env = {**os.environ, "OPS_MCP_DB": str(tmp_path / "ops.db"), "OPS_MCP_AUDIT": str(tmp_path / "audit.jsonl"), "OPS_MCP_READ_ONLY": "1" if read_only else "0"}
    return stdio_client(StdioServerParameters(command="python3", args=["server.py"], cwd=str(ROOT), env=env))


def test_tools_resources_prompts_listed(tmp_path):
    async def go():
        async with await session(tmp_path) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = {t.name: t for t in (await s.list_tools()).tools}
                assert {"search_contacts", "get_contact", "list_tasks", "check_inventory", "pipeline_summary", "add_note", "create_task", "complete_task", "update_deal_stage", "create_order"} <= set(tools)
                assert tools["search_contacts"].annotations.readOnlyHint is True and tools["create_order"].annotations.readOnlyHint is False
                assert {str(x.uri) for x in (await s.list_resources()).resources} == {"ops://schema", "ops://inventory/low-stock", "ops://pipeline/summary"}
                assert [p.name for p in (await s.list_prompts()).prompts] == ["daily_ops_briefing"]
    run(go())


def test_read_only_mode_hides_write_tools(tmp_path):
    async def go():
        async with await session(tmp_path, read_only=True) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = {t.name for t in (await s.list_tools()).tools}
                assert names == {"search_contacts", "get_contact", "list_tasks", "check_inventory", "pipeline_summary"}
    run(go())


def test_search_then_get_contact(tmp_path):
    async def go():
        async with await session(tmp_path) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                res = await s.call_tool("search_contacts", {"query": "khan", "limit": 3})
                assert not res.isError and 1 <= len(res.structuredContent["result"]) <= 3
                cid = res.structuredContent["result"][0]["id"]
                full = await s.call_tool("get_contact", {"contact_id": cid.lower()})
                assert full.structuredContent["id"] == cid and "open_tasks" in full.structuredContent
                missing = await s.call_tool("get_contact", {"contact_id": "C-9999"})
                assert missing.isError and "not found" in missing.content[0].text
                short = await s.call_tool("search_contacts", {"query": "k"})
                assert short.isError
    run(go())


def test_create_task_validation_and_completion(tmp_path):
    async def go():
        async with await session(tmp_path) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                past = await s.call_tool("create_task", {"contact_id": "C-1001", "title": "Send quote", "due_date": "2020-01-01", "assignee": "hamza"})
                assert past.isError and "in the past" in past.content[0].text
                bad_date = await s.call_tool("create_task", {"contact_id": "C-1001", "title": "Send quote", "due_date": "tomorrow", "assignee": "hamza"})
                assert bad_date.isError
                due = (date.today() + timedelta(days=2)).isoformat()
                ok = await s.call_tool("create_task", {"contact_id": "C-1001", "title": "Send quote", "due_date": due, "assignee": "hamza"})
                assert not ok.isError and ok.structuredContent["status"] == "open"
                tid = ok.structuredContent["id"]
                done = await s.call_tool("complete_task", {"task_id": tid})
                assert done.structuredContent["status"] == "done"
                again = await s.call_tool("complete_task", {"task_id": tid})
                assert again.structuredContent.get("already_done") is True
    run(go())


def test_order_preview_commit_and_stock_guard(tmp_path):
    async def go():
        async with await session(tmp_path) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                oos = await s.call_tool("create_order", {"contact_id": "C-1001", "sku": "CLEAN-KIT-PRO", "qty": 1})
                assert oos.isError and "insufficient stock" in oos.content[0].text
                before = (await s.call_tool("check_inventory", {"sku": "AC-SPLIT-18K"})).structuredContent["result"][0]["qty"]
                preview = await s.call_tool("create_order", {"contact_id": "C-1001", "sku": "ac-split-18k", "qty": 2})
                assert preview.structuredContent["dry_run"] is True and preview.structuredContent["preview"]["total_aed"] == 3700.0
                after_preview = (await s.call_tool("check_inventory", {"sku": "AC-SPLIT-18K"})).structuredContent["result"][0]["qty"]
                assert after_preview == before, "dry run must not touch stock"
                placed = await s.call_tool("create_order", {"contact_id": "C-1001", "sku": "AC-SPLIT-18K", "qty": 2, "dry_run": False})
                assert placed.structuredContent["order_id"].startswith("SO-")
                after = (await s.call_tool("check_inventory", {"sku": "AC-SPLIT-18K"})).structuredContent["result"][0]["qty"]
                assert after == before - 2
                neg = await s.call_tool("create_order", {"contact_id": "C-1001", "sku": "AC-SPLIT-18K", "qty": 0})
                assert neg.isError
    run(go())


def test_audit_log_written(tmp_path):
    async def go():
        async with await session(tmp_path) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                await s.call_tool("pipeline_summary", {})
                await s.call_tool("get_contact", {"contact_id": "C-0000"})
    run(go())
    rows = [json.loads(l) for l in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [r["tool"] for r in rows] == ["pipeline_summary", "get_contact"]
    assert rows[0]["ok"] is True and rows[1]["ok"] is False and "not found" in rows[1]["error"]
