Great idea — just **dynamically generate the MCP server as an in-memory Python module** per request. Clean and simple.

## Architecture

```
project/
├── .env
├── yaml_runtime.py          # unchanged
├── mcp_factory.py           # generates MCP server dynamically in memory
├── tools/
│   ├── crm_tools.yaml
│   ├── finance_tools.yaml
│   └── hr_tools.yaml
└── agent.py
```

---

## `mcp_factory.py` — Core of the approach

```python
import os
import json
import yaml
import types
import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types as mcp_types
from yaml_runtime import YAMLFunctionRuntime


def build_input_schema(fn_def: dict) -> dict:
    type_map = {
        "string": "string", "integer": "integer",
        "number": "number", "boolean": "boolean",
        "array": "array",   "object": "object",
    }
    properties, required = {}, []
    for name, schema in fn_def.get("inputs", {}).items():
        prop = {"type": type_map.get(schema.get("type", "string"), "string")}
        if "default" in schema:
            prop["default"] = schema["default"]
        properties[name] = prop
        if schema.get("required", False):
            required.append(name)
    return {"type": "object", "properties": properties, "required": required}


def create_mcp_server(yaml_path: str) -> Server:
    """
    Dynamically creates a fully wired MCP Server in memory.
    No subprocess, no ports, no files — just a Python object.
    """
    runtime = YAMLFunctionRuntime(yaml_path)

    with open(yaml_path) as f:
        functions = yaml.safe_load(f)["functions"]

    server_name = os.path.basename(yaml_path).replace(".yaml", "")
    server = Server(server_name)

    # --- Dynamically bind handlers to this server instance ---

    @server.list_tools()
    async def list_tools() -> list[mcp_types.Tool]:
        return [
            mcp_types.Tool(
                name=fn["id"],
                description=fn["description"],
                inputSchema=build_input_schema(fn),
            )
            for fn in functions
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[mcp_types.TextContent]:
        try:
            loop = asyncio.get_event_loop()
            # Blocking IO pushed to thread pool — concurrent safe
            result = await loop.run_in_executor(
                None, lambda: runtime.execute(name, inputs=arguments)
            )
            return [mcp_types.TextContent(
                type="text",
                text=json.dumps(result, default=str, indent=2)
            )]
        except Exception as e:
            return [mcp_types.TextContent(
                type="text",
                text=json.dumps({"error": str(e)})
            )]

    return server
```

---

## `agent.py` — In-memory MCP + ADK, concurrent safe

```python
import asyncio
import os
from contextlib import AsyncExitStack
from dotenv import load_dotenv

from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client
from mcp import ClientSession
from mcp.server.memory import InMemoryTransport

from mcp_factory import create_mcp_server

load_dotenv()

# --- Tool definitions — just yaml paths + agent instructions ---
TOOL_CONFIGS = [
    {
        "name": "crm_agent",
        "yaml": "tools/crm_tools.yaml",
        "description": "Handles CRM, customers and leads",
        "instruction": "You are a CRM specialist.",
    },
    {
        "name": "finance_agent",
        "yaml": "tools/finance_tools.yaml",
        "description": "Handles invoices, payments and finance",
        "instruction": "You are a finance specialist.",
    },
    {
        "name": "hr_agent",
        "yaml": "tools/hr_tools.yaml",
        "description": "Handles employees, payroll and HR",
        "instruction": "You are an HR specialist.",
    },
]


async def load_tools_from_yaml(yaml_path: str, exit_stack: AsyncExitStack):
    """
    Creates an in-memory MCP server and connects a client to it
    via InMemoryTransport — zero network, zero subprocess.
    Each call returns a fresh isolated session.
    """
    server = create_mcp_server(yaml_path)

    # In-memory bidirectional transport — like a pipe between server & client
    server_transport, client_transport = InMemoryTransport.create_pair()

    # Start the server in background using this transport
    exit_stack.enter_async_context(
        server.run(
            server_transport.read_stream,
            server_transport.write_stream,
            server.create_initialization_options(),
        )
    )

    # Connect ADK MCPToolset via the client side of the transport
    mcp_toolset = MCPToolset(client_session=ClientSession(
        client_transport.read_stream,
        client_transport.write_stream,
    ))

    tools = await exit_stack.enter_async_context(mcp_toolset)
    return tools


async def build_sub_agent(config: dict, exit_stack: AsyncExitStack) -> LlmAgent:
    tools = await load_tools_from_yaml(config["yaml"], exit_stack)
    return LlmAgent(
        name=config["name"],
        model="gemini-2.0-flash",
        description=config["description"],
        instruction=config["instruction"],
        tools=tools,
    )


async def handle_request(user_id: str, session_id: str, query: str):
    """
    Fully isolated per user request.
    Each request creates its own in-memory MCP sessions — no sharing.
    """
    async with AsyncExitStack() as exit_stack:
        # Each user gets fresh in-memory MCP sessions
        sub_agents = [
            await build_sub_agent(cfg, exit_stack)
            for cfg in TOOL_CONFIGS
        ]

        orchestrator = LlmAgent(
            name="orchestrator",
            model="gemini-2.0-flash",
            instruction="""
                Route tasks to the right specialist:
                - CRM/customer tasks   → crm_agent
                - Finance/invoice tasks → finance_agent
                - HR/employee tasks     → hr_agent
                Combine results when task spans multiple domains.
            """,
            tools=[AgentTool(agent=a) for a in sub_agents],
        )

        session_service = InMemorySessionService()
        await session_service.create_session(
            app_name="app", user_id=user_id, session_id=session_id
        )

        runner = Runner(
            agent=orchestrator,
            app_name="app",
            session_service=session_service,
        )

        print(f"\n[{user_id}] {query}")
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=types.Content(
                role="user",
                parts=[types.Part(text=query)]
            )
        ):
            if event.is_final_response():
                print(f"[{user_id}] → {event.response.text}")
        # AsyncExitStack auto-cleans all in-memory sessions here


# --- Concurrent user simulation ---
async def main():
    await asyncio.gather(
        handle_request("user_1", "s1", "Get all overdue invoices for Q1"),
        handle_request("user_2", "s2", "Show active employees in engineering"),
        handle_request("user_3", "s3", "Find customers with open support tickets"),
    )


if __name__ == "__main__":
    asyncio.run(main())
```

---

## How It Works

```
handle_request(user_1)          handle_request(user_2)
       │                                │
  AsyncExitStack                  AsyncExitStack
       │                                │
  create_mcp_server(crm.yaml)    create_mcp_server(crm.yaml)
       │                                │
  InMemoryTransport.create_pair() InMemoryTransport.create_pair()
  [server_pipe ←→ client_pipe]   [server_pipe ←→ client_pipe]
       │                                │
  MCPToolset(client_session)      MCPToolset(client_session)
       │                                │
  crm_agent (isolated)            crm_agent (isolated)
       │                                │
  request completes →             request completes →
  exit_stack cleans up            exit_stack cleans up
```

**What you get:**

- **No subprocesses** — everything is Python objects in memory
- **No ports/SSE** — `InMemoryTransport` is a direct async pipe
- **Concurrent safe** — each `handle_request` has its own transport pair and session
- **Auto cleanup** — `AsyncExitStack` tears down everything when request ends
- **Add new toolset** — one yaml file + one line in `TOOL_CONFIGS`
