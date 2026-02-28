Let me show you the complete implementation with `.env` for secrets and Google ADK tool integration.

## Project Structure

```
project/
├── .env
├── functions.yaml
├── yaml_runtime.py
├── tools.py
└── agent.py
```

---

## `.env`

```env
API_TOKEN=your_api_token_here
DB_CONN=postgresql://user:password@localhost:5432/mydb
GEMINI_API_KEY=your_gemini_key_here
```

---

## `functions.yaml`

```yaml
functions:
  - id: get_user_orders
    description: "Fetch orders for a given user from REST API"
    type: http_api
    config:
      method: GET
      url: "https://api.example.com/orders"
      params:
        user_id: "{{ inputs.user_id }}"
        status: "{{ inputs.status | default('active') }}"
      headers:
        Authorization: "Bearer {{ secrets.API_TOKEN }}"

  - id: get_db_users
    description: "Query users from database by status"
    type: sql
    config:
      connection: "{{ secrets.DB_CONN }}"
      query: >
        SELECT id, name, email
        FROM users
        WHERE status = :status
    params:
      status: "{{ inputs.status }}"
```

---

## `yaml_runtime.py`

```python
import os
import yaml
import httpx
import sqlalchemy
from jinja2 import sandbox
from dotenv import load_dotenv
from typing import Any

load_dotenv()

class YAMLFunctionRuntime:
    def __init__(self, yaml_path: str):
        with open(yaml_path) as f:
            self.registry = {
                fn["id"]: fn
                for fn in yaml.safe_load(f)["functions"]
            }
        # Load secrets from .env at runtime — never stored in YAML
        self.secrets = {
            key: os.getenv(key)
            for key in os.environ
        }
        self.jinja = sandbox.SandboxedEnvironment()

    def resolve(self, template: str, context: dict) -> str:
        return self.jinja.from_string(str(template)).render(
            inputs=context.get("inputs", {}),
            secrets=self.secrets,
            item=context.get("item", {})
        )

    def execute(self, function_id: str, inputs: dict) -> Any:
        fn = self.registry.get(function_id)
        if not fn:
            raise ValueError(f"Unknown function: {function_id}")

        ctx = {"inputs": inputs}
        fn_type = fn["type"]

        if fn_type == "http_api":
            return self._run_http(fn["config"], ctx)
        elif fn_type == "sql":
            return self._run_sql(fn["config"], ctx)
        else:
            raise ValueError(f"Unsupported function type: {fn_type}")

    def _run_http(self, config: dict, ctx: dict) -> dict:
        url = self.resolve(config["url"], ctx)
        params = {
            k: self.resolve(v, ctx)
            for k, v in config.get("params", {}).items()
        }
        headers = {
            k: self.resolve(v, ctx)
            for k, v in config.get("headers", {}).items()
        }
        method = config.get("method", "GET").upper()

        with httpx.Client() as client:
            resp = client.request(method, url, params=params, headers=headers)
            resp.raise_for_status()
            return resp.json()

    def _run_sql(self, config: dict, ctx: dict) -> list:
        conn_str = self.resolve(config["connection"], ctx)
        engine = sqlalchemy.create_engine(conn_str)

        bound_params = {
            k: self.resolve(v, ctx)
            for k, v in config.get("params", {}).items()
        }
        with engine.connect() as conn:
            result = conn.execute(sqlalchemy.text(config["query"]), bound_params)
            return [dict(row) for row in result]
```

---

## `tools.py` — ADK Tool Registration

This is the key part — each YAML function becomes a proper typed ADK tool dynamically.

```python
import json
from typing import Callable
from google.adk.tools import FunctionTool
from yaml_runtime import YAMLFunctionRuntime
import yaml

runtime = YAMLFunctionRuntime("functions.yaml")

def _make_tool_fn(function_id: str) -> Callable:
    """
    Dynamically creates a typed Python function for each YAML function.
    ADK uses the docstring + type hints for the LLM tool description.
    """
    def tool_fn(**kwargs) -> str:
        try:
            result = runtime.execute(function_id, inputs=kwargs)
            return json.dumps(result, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})

    # ADK reads __name__ and __doc__ to build the tool schema
    tool_fn.__name__ = function_id
    tool_fn.__doc__ = _get_description(function_id)

    return tool_fn


def _get_description(function_id: str) -> str:
    with open("functions.yaml") as f:
        functions = yaml.safe_load(f)["functions"]
    for fn in functions:
        if fn["id"] == function_id:
            return fn.get("description", function_id)
    return function_id


def load_yaml_tools() -> list[FunctionTool]:
    """
    Reads all functions from YAML and returns a list of ADK FunctionTools.
    Call this once at agent startup.
    """
    with open("functions.yaml") as f:
        functions = yaml.safe_load(f)["functions"]

    tools = []
    for fn in functions:
        tool_fn = _make_tool_fn(fn["id"])
        tools.append(FunctionTool(func=tool_fn))

    return tools
```

---

## `agent.py` — ADK Agent with Dynamic Tools

```python
import os
from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from tools import load_yaml_tools

load_dotenv()

# Dynamically load all tools from YAML at startup
yaml_tools = load_yaml_tools()

agent = LlmAgent(
    name="data_agent",
    model="gemini-2.0-flash",
    description="An agent that fetches data using pre-defined secure YAML functions",
    instruction="""
        You are a data assistant. When the user asks for data, 
        use the available tools to fetch it. 
        Always summarize the results clearly.
        Never ask the user for secrets or credentials.
    """,
    tools=yaml_tools,
)

async def run_agent(user_query: str):
    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="yaml_agent",
        user_id="user_1",
        session_id="session_1"
    )

    runner = Runner(
        agent=agent,
        app_name="yaml_agent",
        session_service=session_service
    )

    message = types.Content(
        role="user",
        parts=[types.Part(text=user_query)]
    )

    print(f"\nUser: {user_query}\n")

    async for event in runner.run_async(
        user_id="user_1",
        session_id="session_1",
        new_message=message
    ):
        if event.is_final_response():
            print(f"Agent: {event.response.text}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(run_agent("Get me all active users from the database"))
```

---

## How It All Connects

```
.env (secrets)
     │
     ▼
YAMLFunctionRuntime          functions.yaml (logic)
  - loads secrets                  │
  - loads registry   ◄─────────────┘
  - executes safely
     │
     ▼
_make_tool_fn()              ← wraps each YAML fn as a Python callable
     │
     ▼
FunctionTool(func=tool_fn)   ← ADK wraps it with schema for Gemini
     │
     ▼
LlmAgent(tools=yaml_tools)  ← Gemini decides which tool to call
```

**Key points to note:**

- `load_dotenv()` runs before anything else — secrets come from `.env`, never from YAML
- `_make_tool_fn()` creates isolated closures per function ID, so each tool is independent
- `SandboxedEnvironment` in Jinja2 blocks any template from accessing `os`, `subprocess`, or file system
- SQL params use SQLAlchemy's `:param` binding — never string-interpolated into queries
- Adding a new function is just adding a block in `functions.yaml` — no Python changes needed
