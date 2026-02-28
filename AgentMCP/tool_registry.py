"""
tool_registry.py
----------------
Single source of truth for all YAML-backed tool sets.

To add a new domain:
  1. Create tools/<domain>_tools.yaml
  2. Add an entry to TOOL_REGISTRY below — nothing else changes.
"""

from dataclasses import dataclass


@dataclass
class ToolConfig:
    name:        str   # agent name, e.g. "crm_agent"
    yaml_path:   str   # path to yaml tool definition
    description: str   # shown to orchestrator LLM for routing decisions
    instruction: str   # system prompt for this specialist agent


TOOL_REGISTRY: list[ToolConfig] = [
    ToolConfig(
        name="crm_agent",
        yaml_path="tools/crm_tools.yaml",
        description=(
            "Handles CRM operations: customers, leads, contacts, "
            "support tickets, and customer scoring."
        ),
        instruction=(
            "You are a CRM specialist agent. "
            "Use the available tools to retrieve and analyse customer data. "
            "Always return structured, clear results."
        ),
    ),
    ToolConfig(
        name="finance_agent",
        yaml_path="tools/finance_tools.yaml",
        description=(
            "Handles finance operations: invoices, payments, revenue reports, "
            "overdue tracking, and anomaly detection."
        ),
        instruction=(
            "You are a finance specialist agent. "
            "Use the available tools to retrieve financial data and compute metrics. "
            "Always include totals and highlight anomalies."
        ),
    ),
    ToolConfig(
        name="hr_agent",
        yaml_path="tools/hr_tools.yaml",
        description=(
            "Handles HR operations: employees, departments, payroll, "
            "job postings, and attrition analysis."
        ),
        instruction=(
            "You are an HR specialist agent. "
            "Use the available tools to retrieve and analyse employee data. "
            "Present results clearly with department breakdowns where relevant."
        ),
    ),
]
