"""
agent.py
--------
Core agent builder using in-memory MCP servers.

Architecture per user request:
  ┌─────────────────────────────────────────────────────┐
  │  handle_request(user_id, session_id, query)         │
  │                                                     │
  │  AsyncExitStack (owns all resources for this req)   │
  │  │                                                  │
  │  ├─ crm_agent                                       │
  │  │    └─ InMemoryTransport pair                     │
  │  │         └─ MCP Server (crm_tools.yaml)           │
  │  │                                                  │
  │  ├─ finance_agent                                   │
  │  │    └─ InMemoryTransport pair                     │
  │  │         └─ MCP Server (finance_tools.yaml)       │
  │  │                                                  │
  │  ├─ hr_agent                                        │
  │  │    └─ InMemoryTransport pair                     │
  │  │         └─ MCP Server (hr_tools.yaml)            │
  │  │                                                  │
  │  └─ orchestrator  (routes via AgentTool)            │
  │                                                     │
  │  On exit: AsyncExitStack cleans up all transports   │
  └─────────────────────────────────────────────────────┘

Each concurrent user gets their own isolated stack — zero sharing.
"""

import asyncio
from contextlib import AsyncExitStack
from dotenv import load_dotenv

from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from mcp.client.session import ClientSession
from mcp.shared.memory import (
    create_connected_server_and_client_session as create_in_memory_pair,
)

from mcp_factory import create_mcp_server
from tool_registry import TOOL_REGISTRY, ToolConfig

load_dotenv()


# ── In-memory MCP session builder ─────────────────────────────────────────────

async def _build_in_memory_tools(
    config: ToolConfig,
    exit_stack: AsyncExitStack,
) -> list:
    """
    Creates an in-memory MCP server from a YAML file and connects
    an ADK MCPToolset to it via an async memory transport.

    The exit_stack owns the server task lifetime — when the stack
    closes, the server and transport are torn down automatically.
    """
    server = create_mcp_server(config.yaml_path)

    # create_in_memory_pair() from the mcp library wires a ClientSession
    # directly to a Server via in-memory streams — no network involved
    client_session = await exit_stack.enter_async_context(
        create_in_memory_pair(server)
    )

    # Wrap the raw ClientSession in an ADK MCPToolset
    mcp_toolset = MCPToolset(client_session=client_session)
    tools = await exit_stack.enter_async_context(mcp_toolset)
    return tools


# ── Sub-agent builder ──────────────────────────────────────────────────────────

async def _build_sub_agent(
    config: ToolConfig,
    exit_stack: AsyncExitStack,
) -> LlmAgent:
    """Build one specialist LlmAgent backed by an in-memory MCP server."""
    tools = await _build_in_memory_tools(config, exit_stack)
    return LlmAgent(
        name=config.name,
        model="gemini-2.0-flash",
        description=config.description,
        instruction=config.instruction,
        tools=tools,
    )


# ── Orchestrator builder ───────────────────────────────────────────────────────

async def _build_orchestrator(
    sub_agents: list[LlmAgent],
) -> LlmAgent:
    """
    Build the orchestrator agent that routes user requests to specialist
    sub-agents using AgentTool.
    """
    agent_descriptions = "\n".join(
        f"  - {cfg.name}: {cfg.description}"
        for cfg in TOOL_REGISTRY
    )

    return LlmAgent(
        name="orchestrator",
        model="gemini-2.0-flash",
        description="Routes enterprise requests to the correct specialist agent",
        instruction=f"""
You are an enterprise operations orchestrator.

Available specialist agents:
{agent_descriptions}

Rules:
- Delegate each task to the most relevant specialist agent.
- If a task spans multiple domains, call multiple agents and combine results.
- Always present results in a clear, structured format.
- Never fabricate data — only report what the tools return.
""",
        tools=[AgentTool(agent=a) for a in sub_agents],
    )


# ── Main request handler ───────────────────────────────────────────────────────

async def handle_request(
    user_id:    str,
    session_id: str,
    query:      str,
    verbose:    bool = True,
) -> str:
    """
    Handle one user request end-to-end.

    Fully isolated — each call creates its own:
      - in-memory MCP servers (one per yaml file)
      - transport pairs
      - ADK session
      - runner

    Concurrent calls never share any state.

    Returns the agent's final response text.
    """
    async with AsyncExitStack() as stack:
        # 1. Build all sub-agents with isolated in-memory MCP sessions
        sub_agents = [
            await _build_sub_agent(cfg, stack)
            for cfg in TOOL_REGISTRY
        ]

        # 2. Build orchestrator on top of sub-agents
        orchestrator = await _build_orchestrator(sub_agents)

        # 3. Create ADK session and runner
        session_service = InMemorySessionService()
        await session_service.create_session(
            app_name="enterprise_agent",
            user_id=user_id,
            session_id=session_id,
        )

        runner = Runner(
            agent=orchestrator,
            app_name="enterprise_agent",
            session_service=session_service,
        )

        # 4. Run the agent
        if verbose:
            print(f"\n{'─'*60}")
            print(f"[{user_id}] Query: {query}")
            print(f"{'─'*60}")

        response_text = ""
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=types.Content(
                role="user",
                parts=[types.Part(text=query)],
            ),
        ):
            if event.is_final_response():
                response_text = event.response.text
                if verbose:
                    print(f"[{user_id}] Response:\n{response_text}")

        # AsyncExitStack auto-cleans all MCP sessions on exit
        return response_text
