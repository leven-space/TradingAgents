"""TradingAgents MCP server for OpenClaw and other MCP clients."""

from __future__ import annotations

import asyncio
import os
import re
from datetime import date
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_server import support

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_analysis_lock = asyncio.Lock()
_analysis_running = False


def _validate_date(value: str) -> str:
    if not _DATE_RE.match(value):
        raise ValueError("analysis_date must be YYYY-MM-DD")
    return value


def build_mcp() -> FastMCP:
    host = os.environ.get("TRADINGAGENTS_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("TRADINGAGENTS_MCP_PORT", "8080"))

    mcp = FastMCP(
        "TradingAgents",
        instructions=(
            "Multi-agent LLM financial analysis. Call validate_ticker before long "
            "runs. analyze_stock takes 5–20 minutes. Use list_reports / get_report "
            "for history; list_decision_history for memory log."
        ),
        host=host,
        port=port,
        json_response=True,
        stateless_http=True,
    )

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "tradingagents-mcp"})

    @mcp.tool()
    def get_server_info() -> dict[str, Any]:
        """Return MCP service metadata, active config, and available tools."""
        return support.server_info()

    @mcp.tool()
    def validate_ticker(ticker: str) -> dict[str, Any]:
        """Normalize a ticker and detect asset type before running analysis."""
        if not ticker.strip():
            raise ValueError("ticker is required")
        return support.validate_ticker_input(ticker)

    @mcp.tool()
    def get_analysis_status() -> dict[str, bool]:
        """Return whether a long-running analyze_stock call is in progress."""
        return {"running": _analysis_running}

    @mcp.tool()
    async def analyze_stock(
        ticker: str,
        analysis_date: str | None = None,
        asset_type: str | None = None,
        analysts: list[str] | None = None,
        include_full_report: bool = False,
        include_section_summaries: bool = True,
        save_report: bool = True,
    ) -> dict[str, Any]:
        """Run a full multi-agent trading analysis for *ticker*.

        Args:
            ticker: Yahoo symbol, e.g. AAPL, 0700.HK, BTC-USD.
            analysis_date: Trade date YYYY-MM-DD; defaults to today.
            asset_type: ``stock`` or ``crypto``; auto-detected when omitted.
            analysts: Optional subset of market/social/news/fundamentals.
            include_full_report: Embed complete_report.md (can be very large).
            include_section_summaries: Include short excerpts per pipeline stage.
            save_report: Write markdown report tree to results_dir.
        """
        global _analysis_running
        if not ticker.strip():
            raise ValueError("ticker is required")

        canonical = support.normalize_ticker_symbol(ticker)
        trade_date = _validate_date(analysis_date or date.today().isoformat())
        resolved_asset = support.validate_asset_type(asset_type, canonical)
        selected_analysts = support.normalize_analysts(analysts, resolved_asset)

        async with _analysis_lock:
            _analysis_running = True
            try:
                final_state, rating, graph = await asyncio.to_thread(
                    support.run_analysis,
                    canonical,
                    trade_date,
                    resolved_asset,
                    selected_analysts,
                )
                report_file = None
                report_dir = None
                if save_report:
                    report_file = await asyncio.to_thread(
                        graph.save_reports, final_state, canonical
                    )
                    report_dir = report_file.parent
            finally:
                _analysis_running = False

        payload: dict[str, Any] = {
            "ticker": canonical,
            "analysis_date": trade_date,
            "asset_type": resolved_asset,
            "analysts": list(selected_analysts),
            "rating": rating,
            "final_trade_decision": final_state.get("final_trade_decision", ""),
        }
        if report_dir is not None:
            payload["report_dir"] = str(report_dir)
        if report_file is not None:
            payload["complete_report_path"] = str(report_file)
        if include_section_summaries:
            payload["sections"] = support.summarize_final_state(final_state)
        if include_full_report and report_file is not None and report_file.is_file():
            payload["complete_report"] = report_file.read_text(encoding="utf-8")
        return payload

    @mcp.tool()
    def list_reports(ticker: str | None = None, limit: int = 10) -> list[dict[str, str]]:
        """List recent analysis report directories (newest first)."""
        return support.list_report_entries(ticker, limit)

    @mcp.tool()
    def list_report_files(report_dir: str) -> list[dict[str, str]]:
        """List markdown files inside a report directory returned by analyze_stock."""
        return support.list_report_file_entries(report_dir)

    @mcp.tool()
    def get_report(
        report_path: str,
        section: str = "complete",
        max_chars: int = 120_000,
    ) -> dict[str, Any]:
        """Read a report section from a report directory or markdown file path.

        Args:
            report_path: Report directory or markdown file under results_dir.
            section: One of complete/market/sentiment/news/fundamentals/research/
                trader/risk_aggressive/risk_conservative/risk_neutral/portfolio.
            max_chars: Truncate very long content.
        """
        candidate = support.resolve_report_path(report_path)
        if candidate.is_dir():
            path = support.resolve_section_path(str(candidate), section)
        else:
            if section != "complete":
                raise ValueError("section requires report_path to be a report directory")
            path = candidate

        if not path.is_file():
            raise FileNotFoundError(f"No report section at {path}")

        text = path.read_text(encoding="utf-8")
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars] + "\n\n… [truncated]"
        return {
            "path": str(path),
            "section": section,
            "truncated": truncated,
            "content": text,
        }

    @mcp.tool()
    def list_decision_history(ticker: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """List entries from the persistent trading memory log."""
        return support.list_decision_entries(ticker, limit)

    @mcp.tool()
    def get_checkpoint_status(ticker: str, analysis_date: str) -> dict[str, Any]:
        """Check whether a resumable checkpoint exists for ticker + date."""
        if not ticker.strip():
            raise ValueError("ticker is required")
        return support.checkpoint_status(ticker, _validate_date(analysis_date))

    return mcp


def main() -> None:
    build_mcp().run(transport="streamable-http")


if __name__ == "__main__":
    main()
