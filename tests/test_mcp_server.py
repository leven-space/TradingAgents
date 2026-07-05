"""Unit tests for MCP server helpers."""

from pathlib import Path

import pytest

from mcp_server import support
from tradingagents.reporting import write_report_tree


@pytest.fixture(autouse=True)
def _reset_mcp_runtime():
    support.reset_runtime_state()
    support.set_analysis_runner(None)
    yield
    support.reset_runtime_state()
    support.set_analysis_runner(None)


def test_validate_ticker_input_normalizes_crypto():
    info = support.validate_ticker_input("btc-usd")
    assert info["canonical_ticker"] == "BTC-USD"
    assert info["asset_type"] == "crypto"
    assert "fundamentals" not in info["default_analysts"]


def test_normalize_analysts_rejects_unknown():
    with pytest.raises(ValueError, match="Unknown analysts"):
        support.normalize_analysts(["market", "invalid"], "stock")


def test_resolve_report_path_blocks_traversal(tmp_path, monkeypatch):
    root = tmp_path / "logs"
    root.mkdir()
    monkeypatch.setenv("TRADINGAGENTS_RESULTS_DIR", str(root))
    support.reset_runtime_state()

    allowed = root / "reports" / "AAPL_20260101" / "complete_report.md"
    allowed.parent.mkdir(parents=True)
    allowed.write_text("# ok", encoding="utf-8")

    resolved = support.resolve_report_path(str(allowed.relative_to(root)))
    assert resolved == allowed.resolve()

    with pytest.raises(ValueError, match="must be under"):
        support.resolve_report_path("/etc/passwd")


def test_list_report_entries_sorted_by_mtime(tmp_path, monkeypatch):
    root = tmp_path / "logs"
    reports = root / "reports"
    older = reports / "AAPL_20260101"
    newer = reports / "AAPL_20260102"
    for path in (older, newer):
        path.mkdir(parents=True)
        (path / "complete_report.md").write_text("# x", encoding="utf-8")

    import os
    import time

    os.utime(older, (time.time() - 100, time.time() - 100))
    os.utime(newer, (time.time(), time.time()))

    monkeypatch.setenv("TRADINGAGENTS_RESULTS_DIR", str(root))
    support.reset_runtime_state()

    entries = support.list_report_entries(limit=10)
    assert [entry["name"] for entry in entries] == ["AAPL_20260102", "AAPL_20260101"]


def test_list_report_file_entries(tmp_path, monkeypatch):
    root = tmp_path / "logs"
    monkeypatch.setenv("TRADINGAGENTS_RESULTS_DIR", str(root))
    support.reset_runtime_state()

    report_dir = root / "reports" / "AAPL_test"
    write_report_tree(
        {
            "market_report": "MKT",
            "trader_investment_plan": "TRADE",
            "risk_debate_state": {"judge_decision": "PM"},
        },
        "AAPL",
        report_dir,
    )

    files = support.list_report_file_entries(str(report_dir))
    sections = {item["section"] for item in files if item["section"]}
    assert {"market", "trader", "portfolio", "complete"}.issubset(sections)


def test_get_report_section_resolution(tmp_path, monkeypatch):
    root = tmp_path / "logs"
    monkeypatch.setenv("TRADINGAGENTS_RESULTS_DIR", str(root))
    support.reset_runtime_state()

    report_dir = root / "reports" / "AAPL_test"
    write_report_tree({"market_report": "MKT"}, "AAPL", report_dir)

    path = support.resolve_section_path(str(report_dir), "market")
    assert path.read_text(encoding="utf-8") == "MKT"


def test_server_info_includes_tools():
    info = support.server_info()
    assert "analyze_stock" in info["tools"]
    assert info["transport"] == "streamable-http"
