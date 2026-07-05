"""Shared helpers for the TradingAgents MCP server."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from cli.models import AnalystType, AssetType
from cli.utils import detect_asset_type, filter_analysts_for_asset_type, normalize_ticker_symbol
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.checkpointer import checkpoint_step, has_checkpoint
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.llm_clients.api_key_env import get_api_key_env

VALID_ANALYSTS = frozenset(a.value for a in AnalystType)
VALID_ASSET_TYPES = frozenset(a.value for a in AssetType)
DEFAULT_ANALYSTS = ("market", "social", "news", "fundamentals")

REPORT_SECTIONS: dict[str, str] = {
    "complete": "complete_report.md",
    "market": "1_analysts/market.md",
    "sentiment": "1_analysts/sentiment.md",
    "news": "1_analysts/news.md",
    "fundamentals": "1_analysts/fundamentals.md",
    "research": "2_research/manager.md",
    "trader": "3_trading/trader.md",
    "risk_aggressive": "4_risk/aggressive.md",
    "risk_conservative": "4_risk/conservative.md",
    "risk_neutral": "4_risk/neutral.md",
    "portfolio": "5_portfolio/decision.md",
}

_analysis_runner: Callable[..., tuple[dict[str, Any], str]] | None = None
_graph_cache: dict[tuple[str, ...], TradingAgentsGraph] = {}
_results_root: Path | None = None


def set_analysis_runner(
    runner: Callable[..., tuple[dict[str, Any], str]] | None,
) -> None:
    """Test hook: replace propagate() with a stub."""
    global _analysis_runner
    _analysis_runner = runner


def reset_runtime_state() -> None:
    """Clear cached graph instances (used in tests)."""
    global _graph_cache, _results_root
    _graph_cache = {}
    _results_root = None


def load_config() -> dict[str, Any]:
    """Return a fresh config snapshot, honoring runtime env overrides."""
    config = DEFAULT_CONFIG.copy()
    env_path_keys = {
        "TRADINGAGENTS_RESULTS_DIR": "results_dir",
        "TRADINGAGENTS_CACHE_DIR": "data_cache_dir",
        "TRADINGAGENTS_MEMORY_LOG_PATH": "memory_log_path",
    }
    for env_var, key in env_path_keys.items():
        value = os.environ.get(env_var)
        if value:
            config[key] = value
    return config


def results_dir() -> Path:
    global _results_root
    root = Path(load_config()["results_dir"]).resolve()
    if _results_root != root:
        _results_root = root
        _graph_cache.clear()
    return root


def resolve_report_path(report_path: str) -> Path:
    """Resolve *report_path* under results_dir; reject path traversal."""
    root = results_dir()
    candidate = Path(report_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"report_path must be under {root}")
    return resolved


def resolve_report_dir(report_dir: str) -> Path:
    path = resolve_report_path(report_dir)
    if not path.is_dir():
        raise NotADirectoryError(f"Not a report directory: {path}")
    return path


def resolve_section_path(report_dir: str, section: str = "complete") -> Path:
    key = section.strip().lower()
    if key not in REPORT_SECTIONS:
        allowed = ", ".join(sorted(REPORT_SECTIONS))
        raise ValueError(f"Unknown section {section!r}; expected one of: {allowed}")
    base = resolve_report_dir(report_dir)
    return base / REPORT_SECTIONS[key]


def normalize_analysts(
    analysts: list[str] | None,
    asset_type: str,
) -> tuple[str, ...]:
    if analysts is None:
        selected = list(DEFAULT_ANALYSTS)
    else:
        selected = [item.strip().lower() for item in analysts if item.strip()]
        if not selected:
            raise ValueError("analysts must include at least one of: market, social, news, fundamentals")
        unknown = sorted(set(selected) - VALID_ANALYSTS)
        if unknown:
            raise ValueError(f"Unknown analysts: {unknown}")

    typed = [AnalystType(name) for name in selected]
    filtered = filter_analysts_for_asset_type(typed, AssetType(asset_type))
    if not filtered:
        raise ValueError(f"No analysts available for asset_type={asset_type!r}")
    return tuple(analyst.value for analyst in filtered)


def validate_asset_type(asset_type: str | None, ticker: str) -> str:
    if asset_type is None:
        return detect_asset_type(ticker).value
    normalized = asset_type.strip().lower()
    if normalized not in VALID_ASSET_TYPES:
        raise ValueError(f"asset_type must be one of: {', '.join(sorted(VALID_ASSET_TYPES))}")
    return normalized


def get_graph(analysts: tuple[str, ...]) -> TradingAgentsGraph:
    if analysts not in _graph_cache:
        config = load_config()
        _graph_cache[analysts] = TradingAgentsGraph(
            selected_analysts=analysts,
            debug=False,
            config=config,
        )
    return _graph_cache[analysts]


def excerpt(text: str | None, limit: int = 500) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def summarize_final_state(final_state: dict[str, Any]) -> dict[str, str]:
    risk = final_state.get("risk_debate_state") or {}
    research = final_state.get("investment_debate_state") or {}
    return {
        "market_report": excerpt(final_state.get("market_report")),
        "sentiment_report": excerpt(final_state.get("sentiment_report")),
        "news_report": excerpt(final_state.get("news_report")),
        "fundamentals_report": excerpt(final_state.get("fundamentals_report")),
        "research_manager_decision": excerpt(research.get("judge_decision")),
        "trader_plan": excerpt(final_state.get("trader_investment_plan")),
        "investment_plan": excerpt(final_state.get("investment_plan")),
        "portfolio_decision": excerpt(risk.get("judge_decision")),
    }


def run_analysis(
    ticker: str,
    trade_date: str,
    asset_type: str,
    analysts: tuple[str, ...],
) -> tuple[dict[str, Any], str, TradingAgentsGraph]:
    graph = get_graph(analysts)
    if _analysis_runner is not None:
        final_state, rating = _analysis_runner(graph, ticker, trade_date, asset_type)
    else:
        final_state, rating = graph.propagate(ticker, trade_date, asset_type)
    return final_state, rating, graph


def list_report_entries(ticker: str | None = None, limit: int = 10) -> list[dict[str, str]]:
    limit = max(1, min(limit, 50))
    reports_root = results_dir() / "reports"
    if not reports_root.is_dir():
        return []

    prefix = f"{normalize_ticker_symbol(ticker)}_" if ticker else None
    candidates = [
        path
        for path in reports_root.iterdir()
        if path.is_dir() and (prefix is None or path.name.startswith(prefix))
    ]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)

    entries: list[dict[str, str]] = []
    for path in candidates[:limit]:
        complete = path / "complete_report.md"
        modified = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
        entries.append(
            {
                "name": path.name,
                "report_dir": str(path),
                "complete_report_path": str(complete) if complete.is_file() else "",
                "modified_at": modified,
            }
        )
    return entries


def list_report_file_entries(report_dir: str) -> list[dict[str, str]]:
    base = resolve_report_dir(report_dir)
    files: list[dict[str, str]] = []
    for path in sorted(base.rglob("*.md")):
        rel = path.relative_to(base).as_posix()
        section = next((name for name, rel_path in REPORT_SECTIONS.items() if rel_path == rel), "")
        files.append(
            {
                "relative_path": rel,
                "section": section,
                "absolute_path": str(path),
                "size_bytes": str(path.stat().st_size),
            }
        )
    return files


def list_decision_entries(ticker: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 100))
    graph = get_graph(DEFAULT_ANALYSTS)
    entries = graph.memory_log.load_entries()
    if ticker:
        canonical = normalize_ticker_symbol(ticker)
        entries = [entry for entry in entries if entry.get("ticker") == canonical]
    entries = list(reversed(entries))[:limit]
    return [
        {
            "date": entry.get("date", ""),
            "ticker": entry.get("ticker", ""),
            "rating": entry.get("rating", ""),
            "pending": entry.get("pending", False),
            "raw_return": entry.get("raw"),
            "alpha_return": entry.get("alpha"),
            "holding_days": entry.get("holding"),
            "decision_excerpt": excerpt(entry.get("decision", ""), 400),
            "reflection_excerpt": excerpt(entry.get("reflection", ""), 400),
        }
        for entry in entries
    ]


def checkpoint_status(ticker: str, analysis_date: str) -> dict[str, Any]:
    config = load_config()
    cache_dir = config["data_cache_dir"]
    canonical = normalize_ticker_symbol(ticker)
    enabled = bool(config.get("checkpoint_enabled"))
    step = checkpoint_step(cache_dir, canonical, analysis_date) if enabled else None
    return {
        "ticker": canonical,
        "analysis_date": analysis_date,
        "checkpoint_enabled": enabled,
        "has_checkpoint": has_checkpoint(cache_dir, canonical, analysis_date),
        "resume_step": step,
    }


def server_info() -> dict[str, Any]:
    config = load_config()
    provider = config.get("llm_provider", "")
    api_env = get_api_key_env(provider) if provider else None
    api_configured = bool(api_env and os.environ.get(api_env))
    return {
        "service": "tradingagents-mcp",
        "version": "0.3.0",
        "transport": "streamable-http",
        "mcp_path": "/mcp",
        "health_path": "/health",
        "llm_provider": provider,
        "deep_think_llm": config.get("deep_think_llm"),
        "quick_think_llm": config.get("quick_think_llm"),
        "output_language": config.get("output_language"),
        "max_debate_rounds": config.get("max_debate_rounds"),
        "max_risk_discuss_rounds": config.get("max_risk_discuss_rounds"),
        "checkpoint_enabled": config.get("checkpoint_enabled"),
        "data_vendors": dict(config.get("data_vendors", {})),
        "api_key_configured": api_configured,
        "api_key_env_var": api_env,
        "results_dir": str(results_dir()),
        "cache_dir": config.get("data_cache_dir"),
        "memory_log_path": config.get("memory_log_path"),
        "default_analysts": list(DEFAULT_ANALYSTS),
        "report_sections": sorted(REPORT_SECTIONS),
        "tools": [
            "analyze_stock",
            "validate_ticker",
            "list_reports",
            "list_report_files",
            "get_report",
            "list_decision_history",
            "get_checkpoint_status",
            "get_server_info",
            "get_analysis_status",
        ],
    }


def validate_ticker_input(ticker: str) -> dict[str, str]:
    canonical = normalize_ticker_symbol(ticker)
    asset_type = detect_asset_type(canonical).value
    analysts = normalize_analysts(None, asset_type)
    return {
        "input": ticker,
        "canonical_ticker": canonical,
        "asset_type": asset_type,
        "default_analysts": list(analysts),
    }
