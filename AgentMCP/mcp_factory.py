"""
mcp_factory.py
--------------
Creates fully wired in-memory MCP servers from YAML tool definitions.

Key design:
  - No subprocesses, no ports, no SSE servers
  - Each call to `create_mcp_server()` returns a fresh Server object
  - InMemoryTransport (from mcp library) connects server ↔ client
    via async pipes — zero network overhead
  - Concurrent users each get their own transport pair → fully isolated
"""

import os
import json
import yaml
import asyncio
from mcp.server import Server
from mcp import types as mcp_types
from yaml_runtime import YAMLFunctionRuntime


def _build_input_schema(fn_def: dict) -> dict:
    """Convert YAML inputs spec → JSON Schema for MCP tool registration."""
    type_map = {
        "string":  "string",
        "integer": "integer",
        "number":  "number",
        "boolean": "boolean",
        "array":   "array",
        "object":  "object",
    }
    properties: dict = {}
    required:   list = []

    for name, schema in fn_def.get("inputs", {}).items():
        prop: dict = {"type": type_map.get(schema.get("type", "string"), "string")}
        if "default" in schema:
            prop["default"] = schema["default"]
        if "description" in schema:
            prop["description"] = schema["description"]
        properties[name] = prop
        if schema.get("required", False):
            required.append(name)

    return {"type": "object", "properties": properties, "required": required}


def create_mcp_server(yaml_path: str) -> Server:
    """
    Factory function — reads a YAML tool definition file and returns
    a fully configured MCP Server instance ready to be connected via
    InMemoryTransport.

    Usage:
        server = create_mcp_server("tools/crm_tools.yaml")
        # Then wire it to an in-memory transport pair (see agent.py)
    """
    runtime = YAMLFunctionRuntime(yaml_path)

    with open(yaml_path) as f:
        functions: list[dict] = yaml.safe_load(f)["functions"]

    # Derive a clean server name from the filename
    server_name = os.path.basename(yaml_path).replace(".yaml", "")
    server = Server(server_name)

    # ── Register list_tools handler ───────────────────────────────────────────
    @server.list_tools()
    async def list_tools() -> list[mcp_types.Tool]:
        return [
            mcp_types.Tool(
                name=fn["id"],
                description=fn.get("description", fn["id"]),
                inputSchema=_build_input_schema(fn),
            )
            for fn in functions
        ]

    # ── Register call_tool handler ────────────────────────────────────────────
    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[mcp_types.TextContent]:
        try:
            # Offload blocking IO (HTTP/SQL/Python exec) to thread pool
            # so we don't block the async event loop
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: runtime.execute(name, inputs=arguments)
            )
            return [mcp_types.TextContent(
                type="text",
                text=json.dumps(result, default=str, indent=2),
            )]
        except Exception as e:
            return [mcp_types.TextContent(
                type="text",
                text=json.dumps({"error": type(e).__name__, "detail": str(e)}),
            )]

    return server
