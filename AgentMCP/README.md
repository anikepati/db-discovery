# YAML MCP Agent

Secure, concurrent-safe multi-agent system using Google ADK where all tool logic
is defined in YAML files — no arbitrary Python execution, no subprocesses, no ports.

## Architecture

```
tools/crm_tools.yaml      ─┐
tools/finance_tools.yaml   ├─► YAMLFunctionRuntime
tools/hr_tools.yaml       ─┘         │
                                      ▼
                               mcp_factory.py
                            create_mcp_server(yaml)
                                      │
                               InMemoryTransport pair
                            (one per user per yaml file)
                                      │
                               MCPToolset (ADK)
                                      │
                        ┌─────────────┼─────────────┐
                        │             │             │
                   crm_agent   finance_agent    hr_agent
                        └─────────────┼─────────────┘
                                      │
                                 orchestrator
                              (AgentTool routing)
```

**Concurrency model:** Each `handle_request()` call creates its own `AsyncExitStack`
with fresh in-memory transport pairs. Concurrent users share zero state.

## Project Structure

```
├── .env                       # secrets (never committed)
├── requirements.txt
├── db_setup.py                # creates demo SQLite DB
├── yaml_runtime.py            # http / sql / python executors
├── mcp_factory.py             # MCP Server factory (in-memory)
├── tool_registry.py           # central list of yaml toolsets
├── agent.py                   # agent builder + request handler
├── main.py                    # entry point
└── tools/
    ├── crm_tools.yaml
    ├── finance_tools.yaml
    └── hr_tools.yaml
```

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure `.env`

```env
GEMINI_API_KEY=your_gemini_api_key_here
DB_CONN=sqlite:///./demo.db
CRM_API_TOKEN=your_token
FINANCE_API_TOKEN=your_token
HR_API_TOKEN=your_token
```

### 3. Create demo database

```bash
python db_setup.py
```

### 4. Run

```bash
# Single query
python main.py

# Concurrent multi-user simulation
python main.py --concurrent
```

## Adding a New Toolset

1. Create `tools/myservice_tools.yaml`
2. Add one entry to `TOOL_REGISTRY` in `tool_registry.py`
3. That's it — no other files change

## YAML Tool Types

### `http_api` — REST API calls

```yaml
- id: get_customers
  description: "Fetch customers"
  type: http_api
  inputs:
    status:
      type: string
      required: false
      default: "active"
  config:
    method: GET
    url: "https://api.example.com/customers"
    params:
      status: "{{ inputs.status }}"
    headers:
      Authorization: "Bearer {{ secrets.CRM_API_TOKEN }}"
```

### `sql` — Parameterized DB queries

```yaml
- id: get_overdue_invoices
  description: "Fetch overdue invoices"
  type: sql
  inputs:
    days:
      type: integer
      required: false
      default: 30
  config:
    connection: "{{ secrets.DB_CONN }}"
    query: >
      SELECT * FROM invoices
      WHERE status = 'overdue'
      AND due_date <= date('now', '-' || :days || ' days')
    params:
      days: "{{ inputs.days }}"
```

### `python` — Sandboxed Python

```yaml
- id: calculate_metrics
  description: "Compute summary stats"
  type: python
  inputs:
    numbers:
      type: array
      required: true
  python:
    code: |
      import statistics
      nums = inputs["numbers"]
      result = {
          "mean": statistics.mean(nums),
          "median": statistics.median(nums),
      }
```

**Python tool constraints:**
- Must assign output to `result` variable
- Allowed imports: `json`, `statistics`, `math`, `datetime`, `re`, `collections`, `itertools`, `functools`
- No file system, network, or subprocess access
- Executed via RestrictedPython sandbox

## Security Properties

| Threat | Mitigation |
|--------|------------|
| Arbitrary code execution | RestrictedPython sandbox + import allowlist |
| SQL injection | SQLAlchemy parameterized queries (`:param` binding) |
| Template injection | Jinja2 `SandboxedEnvironment` |
| Secret leakage | Secrets from `.env` only, never in YAML or MCP messages |
| Concurrent race conditions | Per-request `AsyncExitStack` with isolated transport pairs |
| Unknown tool types | Allowlist: only `http_api`, `sql`, `python` |
