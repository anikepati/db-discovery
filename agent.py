"""
Data Mapper Agent — Google ADK + GraphRAG
==========================================
Pipeline:
  1. Discovery Agent → builds graph, provides schema context
  2. Mapping Agent   → uses GraphRAG retrieval (3 signals: embedding + graph + value)
  3. Code Gen Agent  → generates tools.py with correct SQL

The LLM reasons over graph-retrieved candidates — including FK context, 
related columns, concept matches, and value pattern analysis — to produce
accurate mappings that pure string matching cannot achieve.
"""

import json
import os
from google.adk.agents import Agent, SequentialAgent
from google.adk.tools import FunctionTool

from typing import Optional

from .graph_rag import SchemaGraphRAG

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MODEL_ID = "gemini-2.0-flash"
DB_PATH = os.environ.get("DB_PATH", "sample.db")
INPUT_JSON = os.environ.get("INPUT_JSON", "data_attributes.json")

# Lazy-initialized GraphRAG singleton (avoid heavy work on module import)
_rag: Optional[SchemaGraphRAG] = None


def _get_rag() -> SchemaGraphRAG:
    """Get or initialize the shared GraphRAG instance."""
    global _rag
    if _rag is None:
        _rag = SchemaGraphRAG(DB_PATH).build()
    return _rag


# ===========================================================================
# TOOL FUNCTIONS — Graph-backed retrieval for the agent
# ===========================================================================

def load_data_attributes() -> dict:
    """Load the data attributes JSON file.

    Returns:
        dict with attributes list (name, value, context per attribute).
    """
    try:
        with open(INPUT_JSON) as f:
            data = json.load(f)
        attrs = data.get("attributes", data if isinstance(data, list) else [])
        return {"status": "success", "count": len(attrs), "attributes": attrs}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def get_graph_stats() -> dict:
    """Get knowledge graph statistics — nodes, edges, types.

    Returns:
        dict with graph size, node type counts, edge type counts.
    """
    return {"status": "success", "stats": _get_rag().get_graph_stats()}


def get_schema_overview() -> dict:
    """Get complete database schema derived from the knowledge graph.

    Includes tables, columns, types, PKs, FKs, concepts, value patterns, and sample data.

    Returns:
        dict with full schema + human-readable summary.
    """
    return {
        "status": "success",
        "schema": _get_rag().get_schema_summary(),
        "readable": _get_rag().get_schema_context_text(),
    }


def get_table_details(table_name: str) -> dict:
    """Get detailed info for a specific table from the graph.

    Args:
        table_name: Exact table name.

    Returns:
        dict with columns, types, PKs, FKs, concepts, samples.
    """
    summary = _get_rag().get_schema_summary()
    if table_name in summary:
        return {"status": "success", "table": table_name, "details": summary[table_name]}
    return {"status": "error", "message": f"Table '{table_name}' not found",
            "available": list(summary.keys())}


def search_columns(attribute_name: str, attribute_value: str = "", attribute_context: str = "") -> dict:
    """Search for matching columns using GraphRAG retrieval.

    Uses THREE signals combined:
      1. EMBEDDING score — TF-IDF similarity between attribute and column descriptions
      2. GRAPH score — Graph traversal via concept nodes, pattern nodes, FK edges
      3. VALUE score — Pattern matching + sample value comparison

    Also returns graph context: FK relationships, related columns via 
    SAME_CONCEPT and SIMILAR_NAME edges.

    Args:
        attribute_name: Data attribute name (e.g., "customer email", "product stock level")
        attribute_value: The actual value (e.g., "charlie@example.com", "147")
        attribute_context: Natural language context about this attribute.

    Returns:
        dict with ranked matches and per-signal scores.
    """
    results = _get_rag().retrieve(attribute_name, value=attribute_value,
                                  context=attribute_context, top_k=8)
    return {
        "status": "success",
        "query": {"name": attribute_name, "value": str(attribute_value), "context": attribute_context},
        "matches": results,
    }


def check_record_exists(table_name: str, column_name: str, value: str) -> dict:
    """Check if a value exists in the live database for INSERT vs UPDATE decision.

    Args:
        table_name: Table to check.
        column_name: Column to match (PK or unique).
        value: Value to search for.

    Returns:
        dict with exists flag and current records if found.
    """
    return _get_rag().check_exists(table_name, column_name, value)


def find_fk_path(from_table: str, to_table: str) -> dict:
    """Find the foreign key path between two tables via graph traversal.

    Useful when data spans multiple tables and you need JOIN logic.

    Args:
        from_table: Source table.
        to_table: Target table.

    Returns:
        dict with the FK path steps or error if no path exists.
    """
    path = _get_rag().find_fk_path(from_table, to_table)
    if path is not None:
        return {"status": "success", "path": path}
    return {"status": "error", "message": f"No FK path from '{from_table}' to '{to_table}'"}


def write_tools_file(python_code: str, output_path: str = "tools.py") -> dict:
    """Write the generated tools.py file and validate its syntax.

    Args:
        python_code: Complete Python source code.
        output_path: Output file path.

    Returns:
        dict with status and syntax validation result.
    """
    try:
        compile(python_code, output_path, "exec")
        with open(output_path, "w") as f:
            f.write(python_code)
        return {"status": "success", "path": os.path.abspath(output_path), "syntax_valid": True}
    except SyntaxError as e:
        return {"status": "syntax_error", "line": e.lineno, "message": e.msg,
                "text": e.text, "hint": "Fix the error and call write_tools_file again."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ===========================================================================
# AGENTS
# ===========================================================================

schema_discovery_agent = Agent(
    name="schema_discovery_agent",
    model=MODEL_ID,
    instruction="""You are a Schema Discovery Agent backed by a Knowledge Graph.

Steps:
1. Call load_data_attributes() — get ALL input attributes.
2. Call get_graph_stats() — understand the graph size (nodes, edges, types).
3. Call get_schema_overview() — get full schema with columns, types, PKs, FKs, 
   concepts, value patterns, and sample data from the graph.
4. For important tables, call get_table_details() for deeper inspection.

Output a COMPLETE report:
- Every table: name, columns, types, PKs, FKs, row counts
- Concepts detected per column (email, phone, money, quantity, etc.)
- Value patterns observed (email format, phone format, code format, etc.)
- Sample data for each column
- The complete list of data attributes to be mapped

This report is the foundation for accurate mapping in the next step.""",
    tools=[
        FunctionTool(load_data_attributes),
        FunctionTool(get_graph_stats),
        FunctionTool(get_schema_overview),
        FunctionTool(get_table_details),
    ],
)

attribute_mapping_agent = Agent(
    name="attribute_mapping_agent",
    model=MODEL_ID,
    instruction="""You are a Data Attribute Mapping Agent powered by GraphRAG.

For EACH data attribute, determine the CORRECT target table and column.

For EACH attribute:
1. Call search_columns(name, value, context) — this returns candidates ranked by 
   three signals:
   - score_embedding: How similar is the attribute to the column's semantic profile
   - score_graph: Was this column reachable via concept/pattern/FK graph traversal
   - score_value: Does the attribute's value pattern match the column's sample patterns
   
2. CRITICALLY evaluate the results. DO NOT blindly pick the top result. Consider:
   - Do the SAMPLE VALUES look like the same kind of data as the attribute value?
     e.g., If value is "charlie@example.com" and samples are ["alice@example.com"] → strong match
     e.g., If value is "WM-001" and samples are ["HB-003", "KB-002", "WM-001"] → exact match!
   - Do the CONCEPTS align? "customer email" should match concept:email columns
   - Does the graph_context show FK relationships that make sense?
   - Could this be an UPDATE? Check context for words like "updated", "existing", "after"

3. For potential UPDATEs:
   - Call check_record_exists() to find the existing record
   - Determine the WHERE clause column and value
   - If context mentions a specific identifier (e.g., "for SKU WM-001"), use that

4. If the attribute doesn't match ANY table well (e.g., "loyalty points" with no loyalty table),
   mark it as UNMAPPED with reasoning.

5. If data spans multiple tables, call find_fk_path() to understand the join structure.

Output a COMPLETE JSON mapping:
{
  "mappings": [
    {
      "attribute_name": "...",
      "attribute_value": "...",
      "target_table": "...",
      "target_column": "...",
      "operation": "INSERT or UPDATE",
      "confidence": "HIGH/MEDIUM/LOW",
      "reasoning": "Why this mapping is correct",
      "where_column": "..." (for UPDATE only),
      "where_value": "..." (for UPDATE only)
    }
  ],
  "unmapped": [
    {"attribute_name": "...", "reason": "..."}
  ]
}""",
    tools=[
        FunctionTool(search_columns),
        FunctionTool(check_record_exists),
        FunctionTool(find_fk_path),
        FunctionTool(get_table_details),
    ],
)

code_generator_agent = Agent(
    name="code_generator_agent",
    model=MODEL_ID,
    instruction="""You are a Python Code Generator. Generate a tools.py file from the mappings.

STRICT RULES:
1. Group attributes for SAME TABLE + SAME OPERATION into ONE function.
2. Use PARAMETERIZED queries: VALUES (?, ?, ?) — NEVER f-strings or format().
3. INSERT functions: all mapped columns for that table.
4. UPDATE functions: SET columns + WHERE clause.
   WHERE params are named where_<column> (e.g., where_sku: str).
5. try/except sqlite3.IntegrityError, always conn.close() in finally.
6. Single-element tuples: (value,) with trailing comma.
7. String values in run_all() use double quotes. Integers are bare numbers.
8. UNMAPPED attributes are comments explaining why.
9. run_all() calls every function with actual values and prints results.
10. if __name__ == "__main__" block at bottom.

TEMPLATE:
```python
\"\"\"tools.py — Auto-generated SQL tools.\"\"\"
import sqlite3

DB_PATH = "sample.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def insert_customers(email: str, first_name: str) -> dict:
    \"\"\"Insert new customer.\"\"\"
    conn = get_connection()
    try:
        conn.execute(
            'INSERT INTO "customers" ("email", "first_name") VALUES (?, ?)',
            (email, first_name)
        )
        conn.commit()
        return {"status": "success", "operation": "INSERT", "table": "customers"}
    except sqlite3.IntegrityError as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

def update_products(stock_quantity: int, where_sku: str) -> dict:
    \"\"\"Update product stock.\"\"\"
    conn = get_connection()
    try:
        conn.execute(
            'UPDATE "products" SET "stock_quantity" = ? WHERE "sku" = ?',
            (stock_quantity, where_sku)
        )
        conn.commit()
        return {"status": "success", "operation": "UPDATE", "table": "products", "rows_affected": conn.total_changes}
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

def run_all():
    results = []
    r = insert_customers("charlie@example.com", "Charlie")
    print(f"  {r['status']:>7} | INSERT | customers")
    results.append(r)
    return results

if __name__ == "__main__":
    print("Running operations...")
    results = run_all()
    ok = sum(1 for r in results if r["status"] == "success")
    print(f"Done: {ok}/{len(results)} succeeded")
```

Call write_tools_file() with the complete code. If syntax validation fails, 
read the error, fix it, and call write_tools_file() again.""",
    tools=[
        FunctionTool(write_tools_file),
    ],
)

# ===========================================================================
# ORCHESTRATOR
# ===========================================================================

root_agent = SequentialAgent(
    name="data_mapper_orchestrator",
    description=(
        "Maps natural language data attributes to database columns using "
        "GraphRAG (knowledge graph + embeddings + value patterns) and "
        "generates a tools.py file with the correct SQL queries."
    ),
    sub_agents=[
        schema_discovery_agent,
        attribute_mapping_agent,
        code_generator_agent,
    ],
)
