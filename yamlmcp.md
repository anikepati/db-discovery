Great idea! MCP (Model Context Protocol) is a perfect fit here — you define everything in YAML and expose it as an MCP server, then connect it to your ADK agent as an `MCPToolset`.

## Project Structure

```
project/
├── .env
├── functions.yaml
├── yaml_runtime.py        # execution engine (http, sql, python)
├── mcp_server.py          # MCP server exposing YAML tools
└── agent.py               # ADK agent consuming MCP toolset
```

---

## `functions.yaml`

```yaml
functions:
  # ── API Tools ─────────────────────────────────────────────────────────────
  - id: get_user_orders
    description: "Fetch orders for a given user from REST API"
    type: http_api
    inputs:
      user_id:
        type: string
        required: true
      status:
        type: string
        required: false
        default: "active"
    config:
      method: GET
      url: "https://api.example.com/orders"
      params:
        user_id: "{{ inputs.user_id }}"
        status: "{{ inputs.status }}"
      headers:
        Authorization: "Bearer {{ secrets.API_TOKEN }}"

  - id: create_order
    description: "Create a new order via POST API"
    type: http_api
    inputs:
      user_id:
        type: string
        required: true
      product_id:
        type: string
        required: true
      quantity:
        type: integer
        required: true
    config:
      method: POST
      url: "https://api.example.com/orders"
      headers:
        Authorization: "Bearer {{ secrets.API_TOKEN }}"
        Content-Type: "application/json"
      body:
        user_id: "{{ inputs.user_id }}"
        product_id: "{{ inputs.product_id }}"
        quantity: "{{ inputs.quantity }}"

  # ── DB Tools ───────────────────────────────────────────────────────────────
  - id: get_active_users
    description: "Query active users from the database"
    type: sql
    inputs:
      status:
        type: string
        required: false
        default: "active"
      limit:
        type: integer
        required: false
        default: 100
    config:
      connection: "{{ secrets.DB_CONN }}"
      query: >
        SELECT id, name, email, created_at
        FROM users
        WHERE status = :status
        LIMIT :limit
      params:
        status: "{{ inputs.status }}"
        limit: "{{ inputs.limit }}"

  - id: get_order_summary
    description: "Get order summary grouped by user"
    type: sql
    inputs:
      from_date:
        type: string
        required: true
    config:
      connection: "{{ secrets.DB_CONN }}"
      query: >
        SELECT user_id, COUNT(*) as order_count, SUM(amount) as total
        FROM orders
        WHERE created_at >= :from_date
        GROUP BY user_id
        ORDER BY total DESC
      params:
        from_date: "{{ inputs.from_date }}"

  # ── Python Tools ───────────────────────────────────────────────────────────
  - id: calculate_metrics
    description: "Calculate summary metrics from a list of numbers"
    type: python
    inputs:
      numbers:
        type: array
        required: true
      round_to:
        type: integer
        required: false
        default: 2
    python:
      code: |
        import statistics
        nums = inputs["numbers"]
        r = inputs.get("round_to", 2)
        result = {
            "count": len(nums),
            "mean": round(statistics.mean(nums), r),
            "median": round(statistics.median(nums), r),
            "stdev": round(statistics.stdev(nums), r) if len(nums) > 1 else 0,
            "min": min(nums),
            "max": max(nums),
        }

  - id: parse_and_filter_json
    description: "Parse JSON string and filter records by a field value"
    type: python
    inputs:
      json_string:
        type: string
        required: true
      field:
        type: string
        required: true
      value:
        type: string
        required: true
    python:
      code: |
        import json
        data = json.loads(inputs["json_string"])
        records = data if isinstance(data, list) else data.get("data", [])
        result = [r for r in records if str(r.get(inputs["field"])) == inputs["value"]]
```

---

## `yaml_runtime.py`

```python
import os
import yaml
import json
import httpx
import sqlalchemy
import RestrictedPython
from jinja2 import sandbox
from dotenv import load_dotenv
from typing import Any

load_dotenv()

# Safe builtins allowed in python-type tools
SAFE_BUILTINS = RestrictedPython.safe_builtins.copy()
SAFE_BUILTINS.update({
    "len": len, "range": range, "enumerate": enumerate,
    "zip": zip, "map": map, "filter": filter,
    "sum": sum, "min": min, "max": max, "round": round,
    "list": list, "dict": dict, "set": set, "tuple": tuple,
    "str": str, "int": int, "float": float, "bool": bool,
    "sorted": sorted, "reversed": reversed,
    "isinstance": isinstance, "type": type,
    "print": print,
})

ALLOWED_IMPORTS = {"json", "statistics", "math", "datetime", "re", "collections"}


class YAMLFunctionRuntime:
    def __init__(self, yaml_path: str):
        with open(yaml_path) as f:
            self.registry = {
                fn["id"]: fn
                for fn in yaml.safe_load(f)["functions"]
            }
        self.secrets = dict(os.environ)
        self.jinja = sandbox.SandboxedEnvironment()

    def resolve(self, template: Any, context: dict) -> str:
        return self.jinja.from_string(str(template)).render(
            inputs=context.get("inputs", {}),
            secrets=self.secrets,
        )

    def resolve_inputs(self, fn_def: dict, raw_inputs: dict) -> dict:
        """Apply defaults and validate required inputs."""
        resolved = {}
        for name, schema in fn_def.get("inputs", {}).items():
            if name in raw_inputs:
                resolved[name] = raw_inputs[name]
            elif "default" in schema:
                resolved[name] = schema["default"]
            elif schema.get("required", False):
                raise ValueError(f"Missing required input: '{name}'")
        return resolved

    def execute(self, function_id: str, inputs: dict) -> Any:
        fn = self.registry.get(function_id)
        if not fn:
            raise ValueError(f"Unknown function: '{function_id}'")

        inputs = self.resolve_inputs(fn, inputs)
        ctx = {"inputs": inputs}
        fn_type = fn["type"]

        if fn_type == "http_api":
            return self._run_http(fn["config"], ctx)
        elif fn_type == "sql":
            return self._run_sql(fn["config"], ctx)
        elif fn_type == "python":
            return self._run_python(fn["python"], inputs)
        else:
            raise ValueError(f"Unsupported type: '{fn_type}'")

    def _run_http(self, config: dict, ctx: dict) -> dict:
        url = self.resolve(config["url"], ctx)
        method = config.get("method", "GET").upper()
        params = {k: self.resolve(v, ctx) for k, v in config.get("params", {}).items()}
        headers = {k: self.resolve(v, ctx) for k, v in config.get("headers", {}).items()}
        body = {k: self.resolve(v, ctx) for k, v in config.get("body", {}).items()} or None

        with httpx.Client(timeout=30) as client:
            resp = client.request(method, url, params=params, headers=headers, json=body)
            resp.raise_for_status()
            return resp.json()

    def _run_sql(self, config: dict, ctx: dict) -> list:
        conn_str = self.resolve(config["connection"], ctx)
        engine = sqlalchemy.create_engine(conn_str)
        bound_params = {k: self.resolve(v, ctx) for k, v in config.get("params", {}).items()}

        with engine.connect() as conn:
            result = conn.execute(sqlalchemy.text(config["query"]), bound_params)
            return [dict(row) for row in result]

    def _run_python(self, python_def: dict, inputs: dict) -> Any:
        """
        Execute python code safely using RestrictedPython.
        Only whitelisted builtins and imports are allowed.
        Result must be assigned to `result` variable in the code.
        """
        code = python_def["code"]

        # Compile with RestrictedPython
        compiled = RestrictedPython.compile_restricted(code, "<yaml_tool>", "exec")

        # Custom import guard
        def safe_import(name, *args, **kwargs):
            if name not in ALLOWED_IMPORTS:
                raise ImportError(f"Import '{name}' is not allowed in YAML tools")
            return __import__(name, *args, **kwargs)

        local_ns = {"inputs": inputs, "result": None}
        global_ns = {
            "__builtins__": SAFE_BUILTINS,
            "__import__": safe_import,
            "_print_": RestrictedPython.PrintCollector,
            "_getiter_": iter,
            "_getattr_": getattr,
        }

        exec(compiled, global_ns, local_ns)

        if "result" not in local_ns or local_ns["result"] is None:
            raise ValueError("Python tool must assign output to `result` variable")

        return local_ns["result"]
```

---

## `mcp_server.py`

```python
import json
import yaml
import asyncio
from typing import Any
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types as mcp_types
from yaml_runtime import YAMLFunctionRuntime

# Load runtime once
runtime = YAMLFunctionRuntime("functions.yaml")

# Load YAML metadata for tool schema generation
with open("functions.yaml") as f:
    FUNCTIONS = yaml.safe_load(f)["functions"]


def build_input_schema(fn_def: dict) -> dict:
    """Convert YAML inputs definition → JSON Schema for MCP."""
    properties = {}
    required = []

    type_map = {
        "string": "string",
        "integer": "integer",
        "number": "number",
        "boolean": "boolean",
        "array": "array",
        "object": "object",
    }

    for name, schema in fn_def.get("inputs", {}).items():
        prop = {"type": type_map.get(schema.get("type", "string"), "string")}
        if "default" in schema:
            prop["default"] = schema["default"]
        if "description" in schema:
            prop["description"] = schema["description"]
        properties[name] = prop

        if schema.get("required", False):
            required.append(name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


# Initialize MCP server
app = Server("yaml-toolset")


@app.list_tools()
async def list_tools() -> list[mcp_types.Tool]:
    """Expose all YAML functions as MCP tools."""
    tools = []
    for fn in FUNCTIONS:
        tools.append(
            mcp_types.Tool(
                name=fn["id"],
                description=fn["description"],
                inputSchema=build_input_schema(fn),
            )
        )
    return tools


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[mcp_types.TextContent]:
    """Route MCP tool calls to YAML runtime."""
    try:
        result = runtime.execute(name, inputs=arguments)
        return [
            mcp_types.TextContent(
                type="text",
                text=json.dumps(result, default=str, indent=2),
            )
        ]
    except Exception as e:
        return [
            mcp_types.TextContent(
                type="text",
                text=json.dumps({"error": str(e)}),
            )
        ]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
```

---

## `agent.py`

```python
import asyncio
import os
from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StdioServerParameters
from google.genai import types

load_dotenv()


async def create_agent():
    """Create ADK agent with MCP YAML toolset."""

    # Connect to your MCP server — ADK spawns it as subprocess
    mcp_toolset = MCPToolset(
        connection_params=StdioServerParameters(
            command="python",
            args=["mcp_server.py"],
            env={
                **os.environ,  # Pass .env vars through to MCP server process
            }
        )
    )

    # Fetch tools from MCP server
    tools, exit_stack = await mcp_toolset.load_tools()

    agent = LlmAgent(
        name="yaml_mcp_agent",
        model="gemini-2.0-flash",
        description="Agent powered by secure YAML-defined MCP tools",
        instruction="""
            You are a data operations assistant. Use the available tools to:
            - Fetch data from APIs and databases
            - Perform calculations and data transformations
            
            Always use tools to get real data. Summarize results clearly.
            Never ask users for credentials — they are handled securely.
        """,
        tools=tools,
    )

    return agent, exit_stack


async def run_agent(user_query: str):
    agent, exit_stack = await create_agent()

    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name="yaml_mcp_agent",
        user_id="user_1",
        session_id="session_1",
    )

    runner = Runner(
        agent=agent,
        app_name="yaml_mcp_agent",
        session_service=session_service,
    )

    message = types.Content(
        role="user",
        parts=[types.Part(text=user_query)]
    )

    print(f"\nUser: {user_query}\n")

    async for event in runner.run_async(
        user_id="user_1",
        session_id="session_1",
        new_message=message,
    ):
        if event.is_final_response():
            print(f"Agent: {event.response.text}")

    # Clean up MCP connection
    await exit_stack.aclose()


if __name__ == "__main__":
    asyncio.run(run_agent("Get all active users and calculate their order metrics"))
```

---

## How It All Flows

```
.env
 │  secrets loaded by YAMLFunctionRuntime at startup
 ▼
functions.yaml
 │  defines all tools (api / sql / python)
 ▼
yaml_runtime.py
 │  executes tools safely (RestrictedPython, parameterized SQL, sandboxed Jinja2)
 ▼
mcp_server.py  ←──── stdio MCP server
 │  list_tools()  → exposes YAML tools as MCP tool schema
 │  call_tool()   → routes calls to runtime
 ▼
ADK MCPToolset (StdioServerParameters)
 │  spawns mcp_server.py as subprocess
 │  fetches tool list automatically
 ▼
LlmAgent(tools=tools)
 │  Gemini sees all tools with proper schemas
 │  decides which to call based on user query
```

## Install Dependencies

```bash
pip install google-adk mcp httpx sqlalchemy jinja2 \
            python-dotenv pyyaml RestrictedPython
```

**Key benefits of this setup:**

- **Zero Python exposure** — users only edit `functions.yaml`, never touch runtime code
- **MCP protocol** — tools are language-agnostic and reusable across any MCP-compatible agent
- **RestrictedPython** — python-type tools run in a sandbox with whitelisted imports only
- **Secrets isolation** — `.env` is loaded by the runtime process, never passed through YAML or MCP messages
- **Hot-swappable** — add/modify tools by editing YAML, restart the MCP server, agent picks them up automatically
