"""Integration tests for the TradingAgents MCP HTTP endpoint."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import httpx
import pytest
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from mcp_server import support
from mcp_server.server import build_mcp
from tradingagents.reporting import write_report_tree

if TYPE_CHECKING:
    from collections.abc import Iterator


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(host: str, port: int, timeout: float = 10.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError(f"Timed out waiting for {host}:{port}")


@pytest.fixture
def mcp_server(monkeypatch, tmp_path) -> Iterator[str]:
    port = _free_port()
    logs = tmp_path / "logs"
    memory = tmp_path / "memory"
    cache = tmp_path / "cache"
    for path in (logs, memory, cache):
        path.mkdir(parents=True)

    monkeypatch.setenv("TRADINGAGENTS_MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("TRADINGAGENTS_MCP_PORT", str(port))
    monkeypatch.setenv("TRADINGAGENTS_RESULTS_DIR", str(logs))
    monkeypatch.setenv("TRADINGAGENTS_CACHE_DIR", str(cache))
    monkeypatch.setenv("TRADINGAGENTS_MEMORY_LOG_PATH", str(memory / "trading_memory.md"))
    support.reset_runtime_state()
    support.set_analysis_runner(None)

    app = build_mcp().streamable_http_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    def _serve() -> None:
        asyncio.run(server.serve())

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    _wait_for_port("127.0.0.1", port)

    yield f"http://127.0.0.1:{port}/mcp"

    server.should_exit = True
    thread.join(timeout=5)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_endpoint(mcp_server: str) -> None:
    base = mcp_server.removesuffix("/mcp")
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{base}/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mcp_list_tools(mcp_server: str) -> None:
    async with streamablehttp_client(mcp_server, timeout=10) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()

    names = {tool.name for tool in tools.tools}
    expected = {
        "analyze_stock",
        "validate_ticker",
        "list_reports",
        "list_report_files",
        "get_report",
        "list_decision_history",
        "get_checkpoint_status",
        "get_server_info",
        "get_analysis_status",
    }
    assert expected.issubset(names)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_validate_ticker_tool(mcp_server: str) -> None:
    async with streamablehttp_client(mcp_server, timeout=10) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("validate_ticker", {"ticker": "aapl"})

    payload = _tool_json(result)
    assert payload["canonical_ticker"] == "AAPL"
    assert payload["asset_type"] == "stock"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_report_roundtrip_without_llm(mcp_server: str, tmp_path) -> None:
    logs = tmp_path / "logs"
    report_dir = logs / "reports" / "NVDA_demo"
    write_report_tree(
        {
            "market_report": "MARKET BODY",
            "risk_debate_state": {"judge_decision": "Hold for now"},
        },
        "NVDA",
        report_dir,
    )

    async with streamablehttp_client(mcp_server, timeout=10) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.call_tool("list_reports", {"ticker": "NVDA", "limit": 5})
            files = await session.call_tool(
                "list_report_files",
                {"report_dir": str(report_dir)},
            )
            report = await session.call_tool(
                "get_report",
                {"report_path": str(report_dir), "section": "market"},
            )

    list_payload = _tool_json(listed)
    assert list_payload[0]["name"] == "NVDA_demo"

    file_payload = _tool_json(files)
    assert any(item["section"] == "market" for item in file_payload)

    report_payload = _tool_json(report)
    assert "MARKET BODY" in report_payload["content"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_analyze_stock_with_stub_runner(mcp_server: str) -> None:
    def _stub(_graph, ticker, trade_date, asset_type):
        return (
            {
                "final_trade_decision": "**Rating**: Hold\nReason: stub",
                "market_report": "stub market",
                "trader_investment_plan": "stub trader",
                "investment_debate_state": {"judge_decision": "stub research"},
                "risk_debate_state": {"judge_decision": "stub portfolio"},
            },
            "Hold",
        )

    support.set_analysis_runner(_stub)

    async with streamablehttp_client(mcp_server, timeout=30) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "analyze_stock",
                {
                    "ticker": "NVDA",
                    "analysis_date": "2026-07-05",
                    "analysts": ["market", "news"],
                    "include_section_summaries": True,
                },
            )

    payload = _tool_json(result)
    assert payload["rating"] == "Hold"
    assert payload["analysts"] == ["market", "news"]
    assert payload["sections"]["market_report"] == "stub market"
    assert Path(payload["complete_report_path"]).is_file()


def _tool_json(result) -> dict | list:
    if result.isError:
        raise AssertionError(result.content)
    if result.structuredContent is not None:
        payload = result.structuredContent
        if isinstance(payload, dict) and set(payload.keys()) == {"result"}:
            return payload["result"]
        return payload
    if not result.content:
        return []
    block = result.content[0]
    return json.loads(block.text)
