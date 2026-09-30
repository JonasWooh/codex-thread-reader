from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

# Import sibling module without requiring package installation.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import (  # noqa: E402
    ThreadReaderError,
    list_thread_summaries,
    read_thread as read_thread_core,
    search_thread_summaries,
    status as status_core,
)

NAME = "codex-thread-reader"
VERSION = "0.2.0"
MODERN_VERSION = "2026-07-28"
LEGACY_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
INSTRUCTIONS = (
    "Read-only access to local Codex rollout files. When the user supplies a "
    "codex://threads/... deeplink or asks to recover a prior Codex conversation, "
    "use read_thread. Hidden reasoning is intentionally excluded."
)
SERVER_INFO = {"name": NAME, "version": VERSION}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "read_thread",
        "description": "Read visible context from a local Codex thread by codex:// deeplink or UUID. Returns the newest chunk within max_chars and supports paging backward with end_item.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "thread": {"type": "string", "description": "codex://threads/<uuid> or bare thread UUID"},
                "include_tools": {"type": "boolean", "default": False},
                "include_instructions": {"type": "boolean", "default": False},
                "max_chars": {"type": "integer", "minimum": 1000, "maximum": 500000, "default": 120000},
                "end_item": {"type": "integer", "minimum": 0, "description": "Exclusive transcript item index. Omit for the newest chunk; use next_older_end_item to page backward."}
            },
            "required": ["thread"],
            "additionalProperties": False
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
    },
    {
        "name": "list_threads",
        "description": "List recent local Codex threads, newest first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 30},
                "include_archived": {"type": "boolean", "default": True}
            },
            "additionalProperties": False
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
    },
    {
        "name": "search_threads",
        "description": "Search visible user/assistant text across recent local Codex threads.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "include_archived": {"type": "boolean", "default": True}
            },
            "required": ["query"],
            "additionalProperties": False
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
    },
    {
        "name": "thread_reader_status",
        "description": "Report CODEX_HOME, rollout counts, Python version, and Zstandard decoding support.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
    }
]


def _is_modern(message: dict[str, Any]) -> bool:
    if message.get("method") == "server/discover":
        return True
    params = message.get("params")
    if not isinstance(params, dict):
        return False
    meta = params.get("_meta")
    return isinstance(meta, dict) and meta.get("io.modelcontextprotocol/protocolVersion") == MODERN_VERSION


def _modernize(result: dict[str, Any], *, cacheable: bool = False, cache_scope: str = "private") -> dict[str, Any]:
    out = dict(result)
    out.setdefault("resultType", "complete")
    out.setdefault("_meta", {"io.modelcontextprotocol/serverInfo": SERVER_INFO})
    if cacheable:
        out.setdefault("ttlMs", 0)
        out.setdefault("cacheScope", cache_scope)
    return out


def _send(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _result(request_id: Any, result: dict[str, Any]) -> None:
    _send({"jsonrpc": "2.0", "id": request_id, "result": result})


def _error(request_id: Any, code: int, message: str, data: Any = None) -> None:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    _send({"jsonrpc": "2.0", "id": request_id, "error": err})


def _tool_read(args: dict[str, Any]) -> dict[str, Any]:
    return read_thread_core(
        args.get("thread", ""),
        include_tools=bool(args.get("include_tools", False)),
        include_instructions=bool(args.get("include_instructions", False)),
        max_chars=int(args.get("max_chars", 120000)),
        end_item=args.get("end_item"),
    )


def _tool_list(args: dict[str, Any]) -> dict[str, Any]:
    items = list_thread_summaries(
        limit=int(args.get("limit", 30)),
        include_archived=bool(args.get("include_archived", True)),
    )
    return {"threads": [item.to_dict() for item in items], "count": len(items)}


def _tool_search(args: dict[str, Any]) -> dict[str, Any]:
    matches = search_thread_summaries(
        str(args.get("query", "")),
        limit=int(args.get("limit", 20)),
        include_archived=bool(args.get("include_archived", True)),
    )
    return {"query": args.get("query", ""), "matches": matches, "count": len(matches)}


HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "read_thread": _tool_read,
    "list_threads": _tool_list,
    "search_threads": _tool_search,
    "thread_reader_status": lambda _args: status_core(),
}


def _call_tool(params: dict[str, Any], modern: bool) -> dict[str, Any]:
    name = params.get("name")
    args = params.get("arguments", {})
    if not isinstance(name, str) or name not in HANDLERS:
        content = [{"type": "text", "text": f"Unknown tool: {name!r}"}]
        result = {"content": content, "isError": True}
        return _modernize(result) if modern else result
    if not isinstance(args, dict):
        content = [{"type": "text", "text": "Tool arguments must be a JSON object."}]
        result = {"content": content, "isError": True}
        return _modernize(result) if modern else result
    try:
        data = HANDLERS[name](args)
        text = json.dumps(data, ensure_ascii=False, indent=2)
        result = {"content": [{"type": "text", "text": text}], "structuredContent": data, "isError": False}
    except (ThreadReaderError, ValueError, TypeError) as exc:
        data = {"error": str(exc)}
        result = {"content": [{"type": "text", "text": str(exc)}], "structuredContent": data, "isError": True}
    except Exception as exc:  # avoid killing the MCP process on one malformed rollout
        data = {"error": f"Unexpected reader failure: {type(exc).__name__}: {exc}"}
        result = {"content": [{"type": "text", "text": data["error"]}], "structuredContent": data, "isError": True}
    return _modernize(result) if modern else result


def handle(message: dict[str, Any]) -> None:
    method = message.get("method")
    request_id = message.get("id")

    # Notifications have no id and require no response.
    if request_id is None:
        return

    if method == "server/discover":
        _result(request_id, _modernize({
            "supportedVersions": [MODERN_VERSION],
            "capabilities": {"tools": {"listChanged": False}},
            "instructions": INSTRUCTIONS,
        }, cacheable=True, cache_scope="public"))
        return

    if method == "initialize":
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        requested = params.get("protocolVersion")
        selected = requested if requested in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
        _result(request_id, {
            "protocolVersion": selected,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        })
        return

    modern = _is_modern(message)
    if modern:
        params = message.get("params")
        meta = params.get("_meta") if isinstance(params, dict) else None
        requested = meta.get("io.modelcontextprotocol/protocolVersion") if isinstance(meta, dict) else None
        if requested not in (None, MODERN_VERSION):
            _error(request_id, -32022, "Unsupported protocol version", {"supported": [MODERN_VERSION], "requested": requested})
            return

    if method == "tools/list":
        result = {"tools": TOOLS}
        _result(request_id, _modernize(result, cacheable=True) if modern else result)
        return

    if method == "tools/call":
        params = message.get("params")
        if not isinstance(params, dict):
            _error(request_id, -32602, "Invalid params")
            return
        _result(request_id, _call_tool(params, modern))
        return

    if method == "ping":
        _result(request_id, _modernize({}) if modern else {})
        return

    _error(request_id, -32601, f"Method not found: {method}")


def main() -> None:
    for raw in sys.stdin:
        if not raw.strip():
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError as exc:
            _error(None, -32700, "Parse error", str(exc))
            continue
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            _error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid Request")
            continue
        handle(message)


if __name__ == "__main__":
    main()
