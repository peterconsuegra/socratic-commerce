# app/routes/mcp.py
"""
/mcp: an MCP (Model Context Protocol) server, so a Claude agent can look up
the time windows the platform answers for and the total sales in them
(services/sales_periods.py).

Transport: Streamable HTTP, stateless. Every message is one JSON-RPC POST to
/mcp, answered with one JSON body (no SSE stream, no session id); a
notification is answered 202. GET and DELETE answer 405, which the spec
allows for a server that opens no stream.

Auth: the same API tokens as /api/* (Admin → API tokens, or
`flask create-api-token`), sent as "Authorization: Bearer <token>".

Connect Claude Code:
    claude mcp add --transport http second-order https://secondorder.saveaplaya.org/mcp \
        --header "Authorization: Bearer <token>"
"""
import json
import logging

from flask import current_app, jsonify, request

from app.services.periods import PERIODS, PeriodError
from app.services.sales_periods import BREAKDOWNS, compare_periods, describe_periods, sales_by_period

from . import main
from .common import API_ORDERS_MAX_AGE_SECONDS, api_access_required, refresh_all_orders_if_needed

logger = logging.getLogger(__name__)

SERVER_INFO = {"name": "second-order", "title": "Second Order", "version": "1.0.0"}
# Newest first; a client asking for one of these gets it back, any other gets the newest.
PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]

INSTRUCTIONS = (
    "Second Order is the sales analytics platform of the Save a Playa store (Colombia). "
    "Amounts are in COP; days are Bogotá calendar days. Call list_periods first to see "
    "today's date and the exact days each preset covers. Presets (today, last_7d, "
    "last_30d, last_90d, last_180d) run through now, so they include today so far; for "
    "whole days or months use period=custom with since and until (YYYY-MM-DD, both "
    "included, at most 366 days). Always tell the user which dates the figures cover "
    "and the orders_as_of time."
)

_DAY = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

TOOLS = [
    {
        "name": "list_periods",
        "title": "List time periods",
        "description": (
            "The time periods the sales tools answer for: today's date in Bogotá, each "
            "preset with its first and last day, the rules for a custom range, the "
            "breakdowns, and the first and last order dates in the history."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": _READ_ONLY,
    },
    {
        "name": "get_sales_by_period",
        "title": "Total sales by period",
        "description": (
            "Total sales, orders, average order value, first-time and repurchase sales "
            "for every preset (period=all), one preset, or a custom range (period=custom "
            "with since and until). breakdown=day|week|month also splits the period into "
            "rows (weeks start Monday; empty days/weeks/months included)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "enum": ["all", *PERIODS, "custom"], "default": "all",
                           "description": "A preset, all presets, or custom."},
                "since": {**_DAY, "description": "With period=custom: first day, YYYY-MM-DD."},
                "until": {**_DAY, "description": "With period=custom: last day, YYYY-MM-DD, at most today."},
                "breakdown": {"type": "string", "enum": list(BREAKDOWNS), "default": "none"},
            },
            "additionalProperties": False,
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "compare_periods",
        "title": "Compare two periods",
        "description": (
            "Sales of one custom range against another (compare_since/compare_until) or, "
            "without them, against the same number of days right before it. Returns both "
            "totals and the difference and percentage change of each figure."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "since": {**_DAY, "description": "First day of the period, YYYY-MM-DD."},
                "until": {**_DAY, "description": "Last day of the period, YYYY-MM-DD."},
                "compare_since": {**_DAY, "description": "Optional: first day of the period to compare with."},
                "compare_until": {**_DAY, "description": "Optional: last day of the period to compare with."},
            },
            "required": ["since", "until"],
            "additionalProperties": False,
        },
        "annotations": _READ_ONLY,
    },
]
_TOOL_ARGS = {tool["name"]: set(tool["inputSchema"]["properties"]) for tool in TOOLS}


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _result(msg_id, result):
    return jsonify({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _error(msg_id, code: int, message: str, status: int = 200):
    return jsonify({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}), status


def _initialize(params: dict) -> dict:
    asked = params.get("protocolVersion")
    return {
        "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": SERVER_INFO,
        "instructions": INSTRUCTIONS,
    }


def _run_tool(name: str, args: dict) -> dict:
    """A tool's answer as a dict; PeriodError for arguments it cannot answer."""
    unknown = set(args) - _TOOL_ARGS[name]
    if unknown:
        raise PeriodError(f"Unknown arguments for {name}: {sorted(unknown)}")
    refresh_all_orders_if_needed(max_age_seconds=API_ORDERS_MAX_AGE_SECONDS)
    path = current_app.config["ALL_ORDERS_CSV"]
    if name == "list_periods":
        return describe_periods(path)
    if name == "get_sales_by_period":
        return sales_by_period(path, args.get("period", "all"), args.get("since"),
                               args.get("until"), args.get("breakdown", "none"))
    if not args.get("since") or not args.get("until"):
        raise PeriodError("compare_periods needs since and until, as YYYY-MM-DD.")
    return compare_periods(path, args["since"], args["until"],
                           args.get("compare_since"), args.get("compare_until"))


def _call_tool(params: dict) -> dict:
    name = params.get("name")
    if name not in _TOOL_ARGS:
        raise RpcError(-32602, f"Unknown tool: {name}")
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        raise RpcError(-32602, "arguments must be an object")
    try:
        data = _run_tool(name, args)
    except PeriodError as e:
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    except Exception:
        logger.exception("MCP tool %s failed", name)
        return {"content": [{"type": "text", "text": "The tool failed on the server; try again in a minute."}],
                "isError": True}
    return {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
        "structuredContent": data,
    }


@main.route("/mcp", methods=["POST"])
@api_access_required
def mcp_endpoint():
    msg = request.get_json(silent=True)
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Expected one JSON-RPC 2.0 message (batches are not supported).", 400)

    method, msg_id = msg.get("method"), msg.get("id")
    if "id" not in msg:  # a notification (or a response) - nothing to answer
        return "", 202
    params = msg.get("params") or {}
    try:
        if method == "initialize":
            return _result(msg_id, _initialize(params))
        if method == "ping":
            return _result(msg_id, {})
        if method == "tools/list":
            return _result(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            return _result(msg_id, _call_tool(params))
        raise RpcError(-32601, f"Method not found: {method}")
    except RpcError as e:
        return _error(msg_id, e.code, str(e))


@main.route("/mcp", methods=["GET", "DELETE"])
def mcp_no_stream():
    """No server-initiated stream and no sessions to end."""
    return jsonify({"error": "Use POST for MCP messages."}), 405, {"Allow": "POST"}
