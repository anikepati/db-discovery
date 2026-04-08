# YAML Decision Tree & DAG Engine — Functional Requirements Document

**Version:** 2.0  
**Author:** Sunil — Principal Engineer, Enterprise AI Architecture  
**Status:** Active Development  
**Last Updated:** April 2026  
**Classification:** Internal — Engineering

---

## 1. Executive Summary

This document specifies a config-driven decision tree and DAG execution engine that evaluates business rules defined in YAML against Excel row data. The engine targets enterprise compliance, loan asset management (LAM), and data validation workflows where business analysts own the rules in YAML and the engine executes them without any code changes.

The core design philosophy is **zero code changes after deployment**. Adding, modifying, or removing validation rules requires only YAML edits. The engine supports arbitrarily nested decision trees, compound boolean logic, 14 built-in operators, pluggable action handlers, and a DAG orchestrator for dependency-ordered parallel execution.

### 1.1 Key Metrics (Proven)

| Metric | Result |
|--------|--------|
| Test rows processed | 20 rows × 10 trees = 243 evaluations |
| LAM conversion paths | 15 rows × 16 trees = 178 evaluations |
| DAG execution | 5 rows × 16 trees, 2 layers, 89 evaluations |
| Error rate | 0 across all test runs |
| Execution time | < 1 second for all test configurations |
| Schema validation | Pydantic fail-fast catches 100% of config errors at startup |

---

## 2. Problem Statement

Enterprise validation workflows (LAM Conversion, Compliance QA, Actimize EDR/RRO) are typically implemented as hardcoded if-else chains in application code. This creates the following problems.

**Change velocity bottleneck.** Every new rule or modification requires a code change, code review, testing, and redeployment. A single rule addition that takes a business analyst 5 minutes to define in a flowchart takes 2-5 days to reach production through the development pipeline.

**Business-IT disconnect.** Business analysts define rules in flowcharts and Word documents. Developers manually translate them into code, introducing interpretation errors. There is no single source of truth that both parties can read and validate.

**Audit trail gaps.** Hardcoded logic lacks structured per-row, per-rule audit trails required by compliance teams. When an exception is raised, there is no automated record of which condition evaluated to what value for that specific row.

**No parallelism.** Independent validation sections (e.g., Section 5: Hard Cost and Section 9: Borrowing Base) execute sequentially even when they have no data dependencies between them.

**Fragile maintenance.** Deeply nested if-else trees become unreadable and error-prone over time. A single misplaced bracket or inverted condition can silently corrupt validation logic for thousands of rows.

---

## 3. Goals and Non-Goals

### 3.1 Goals

| ID | Goal | Priority |
|----|------|----------|
| G1 | Zero code changes for rule additions, modifications, or deletions | P0 |
| G2 | YAML-driven decision trees with arbitrary nesting depth | P0 |
| G3 | Pydantic schema validation at config load time — fail fast, not at row 10,000 | P0 |
| G4 | Column pre-validation against Excel before processing row 1 | P0 |
| G5 | Pluggable action dispatcher supporting API calls, DB updates, logging, and custom handlers | P0 |
| G6 | Per-row, per-node audit trail with full decision path and action results | P0 |
| G7 | DAG orchestration with topological sort and parallel-ready execution layers | P1 |
| G8 | Compound boolean conditions (AND/OR) with arbitrary nesting | P1 |
| G9 | Dry-run mode for compliance audit without side effects | P1 |
| G10 | Extensible operator registry — add operators without engine changes | P2 |
| G11 | Chunked Excel reading for large files with configurable batch size | P2 |
| G12 | Flowchart-to-YAML mapping methodology for business analyst collaboration | P2 |

### 3.2 Non-Goals

| ID | Non-Goal | Rationale |
|----|----------|-----------|
| NG1 | Real-time streaming execution | Batch processing is sufficient for current use cases |
| NG2 | Visual YAML editor UI | Handled separately by the visual multi-agent workflow builder project |
| NG3 | Multi-file Excel processing in a single run | Can be orchestrated by an external scheduler or shell script |
| NG4 | User authentication and authorization | Handled by the deployment layer (Kubernetes, API gateway) |
| NG5 | Rule versioning and rollback | Handled by git version control of YAML config files |
| NG6 | Real-time async parallel execution | DAG layers are parallel-ready but current scope is sequential; async upgrade is a future enhancement |

---

## 4. Stakeholders

| Role | Responsibility | Interaction with System |
|------|---------------|----------------------|
| Business Analyst | Defines validation rules in flowcharts and YAML | Edits YAML config files, reviews audit trail reports |
| Compliance Officer | Approves validation logic, reviews exceptions | Consumes per-row audit trail and exception logs |
| Platform Engineer | Deploys and operates the engine | CLI execution, monitoring, CI/CD integration |
| Data Engineer | Provides Excel data feeds | Ensures column naming conventions match YAML config |
| API Service Owner | Maintains downstream update and exception endpoints | Receives API calls from the action dispatcher |

---

## 5. System Architecture

### 5.1 High-Level Data Flow

```
                    ┌─────────────────┐
                    │   YAML Config   │
                    │   (rules)       │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  Config Loader  │
                    │  + Pydantic     │
                    │  Validation     │
                    └────────┬────────┘
                             │ fail-fast on schema error
                    ┌────────▼────────┐     ┌──────────────┐
                    │  Column         │────►│  Excel File  │
                    │  Extractor +    │     │  (data)      │
                    │  Pre-Validator  │     └──────────────┘
                    └────────┬────────┘
                             │ fail-fast on missing columns
                    ┌────────▼────────┐
                    │  Mode Selector  │
                    │  (auto-detect)  │
                    └───┬─────────┬───┘
                        │         │
              ┌─────────▼──┐  ┌──▼──────────┐
              │ FLAT MODE   │  │  DAG MODE   │
              │ Sequential  │  │  Topo-sort  │
              │ tree list   │  │  Layered    │
              └─────────┬──┘  └──┬──────────┘
                        │         │
                    ┌───▼─────────▼───┐
                    │  Tree Walker    │
                    │  (recursive)    │
                    └───┬─────────┬───┘
                        │         │
              ┌─────────▼──┐  ┌──▼──────────┐
              │ Condition   │  │  Action     │
              │ Evaluator   │  │  Dispatcher │
              │ (14 ops)    │  │  (pluggable)│
              └────────────┘  └──┬───┬───┬──┘
                                 │   │   │
                        ┌────────┘   │   └────────┐
                        ▼            ▼            ▼
                   ┌─────────┐ ┌─────────┐ ┌──────────┐
                   │  API    │ │  DB     │ │  JSON    │
                   │  (httpx)│ │  (SQL)  │ │  Report  │
                   └─────────┘ └─────────┘ └──────────┘
```

### 5.2 Component Inventory

| Component | File | Lines | Responsibility |
|-----------|------|-------|---------------|
| CLI Entry Point | `main.py` | ~170 | Argument parsing, mode detection, orchestration loop, summary report |
| Config Loader | `engine/loader.py` | ~25 | YAML file loading, Pydantic model instantiation |
| Pydantic Schemas | `models/schemas.py` | ~105 | Type-safe config models with recursive forward references |
| Condition Evaluator | `engine/evaluator.py` | ~160 | Operator registry, simple/compound condition evaluation |
| Tree Walker | `engine/tree_walker.py` | ~85 | Recursive tree traversal, audit trail generation |
| Action Dispatcher | `engine/action_dispatcher.py` | ~80 | Action routing to pluggable handler functions |
| Excel Reader | `engine/excel_reader.py` | ~45 | Chunked pandas reader with column validation |
| Column Extractor | `engine/column_extractor.py` | ~40 | Recursive YAML scan to extract all referenced column names |
| DAG Executor | `engine/dag_executor.py` | ~245 | Kahn's algorithm topological sort, layer grouping, dependency tracking |
| API Action Handler | `actions/api_action.py` | ~60 | httpx/requests HTTP calls with configurable retry logic |
| DB Action Handler | `actions/db_action.py` | ~75 | SQLAlchemy upsert with sqlite3 fallback |

### 5.3 Technology Stack

| Layer | Technology | Version | Purpose |
|-------|-----------|---------|---------|
| Language | Python | 3.10+ | Core engine |
| Config parsing | PyYAML | 6.0+ | YAML to dict |
| Schema validation | Pydantic | 2.0+ | Type-safe config models, fail-fast validation |
| Data loading | pandas + openpyxl | 2.0+ / 3.1+ | Excel reading with chunked iteration |
| HTTP client | httpx | 0.24+ | API calls with retry (requests as fallback) |
| Database | SQLAlchemy | 2.0+ | DB-agnostic upsert (sqlite3 fallback) |
| CLI | argparse | stdlib | Command-line interface |

---

## 6. Functional Requirements

### 6.1 YAML Configuration Schema

#### FR-6.1.1: Settings Section

The YAML config shall contain a `settings` section with the following fields.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `excel_file` | string | Yes | — | Path to the input Excel file |
| `sheet_name` | string | No | `"Sheet1"` | Excel sheet name to read |
| `row_id_column` | string | No | `"ID"` | Column used as unique row identifier |
| `api_base_url` | string | No | `""` | Base URL prepended to all API action endpoints |
| `db_connection_string` | string | No | `"sqlite:///results.db"` | SQLAlchemy-compatible connection string |
| `db_table` | string | No | `"validation_results"` | Default table name for db_update actions |
| `log_level` | string | No | `"INFO"` | Python logging level (DEBUG, INFO, WARNING, ERROR) |
| `batch_size` | integer | No | `100` | Number of rows per processing chunk |
| `dry_run` | boolean | No | `false` | When true, log all actions without executing them |

#### FR-6.1.2: Decision Tree Section

The YAML config shall contain a `decision_trees` section as a list of decision node objects. Each decision node shall have the following structure.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `id` | string | Yes | — | Unique identifier for this node |
| `name` | string | Yes | — | Human-readable name for logging and audit |
| `description` | string | No | null | Optional description of what this node validates |
| `condition` | Condition | Yes | — | The condition to evaluate (see FR-6.2) |
| `on_true` | ActionBranch | No | `action: "none"` | What to do when condition is true (see FR-6.3) |
| `on_false` | ActionBranch | No | `action: "none"` | What to do when condition is false (see FR-6.3) |

**Nesting.** Any `on_true` or `on_false` branch may contain a `next_decision` field pointing to another DecisionNode. This creates arbitrarily deep nested decision trees. The engine shall recurse into `next_decision` after dispatching the branch's action.

#### FR-6.1.3: DAG Section (Optional)

When present, the YAML config may contain a `dag` section that defines execution dependencies between groups of decision trees.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `nodes` | list | Yes | List of DAG node definitions |
| `on_dependency_failure` | string | No | `"skip"`, `"continue"`, or `"abort"`. Default: `"skip"` |

Each DAG node shall have the following structure.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Unique DAG node identifier |
| `tree_id` | string | Conditional | Single decision tree ID to execute (use `tree_id` or `tree_ids`, not both) |
| `tree_ids` | list of strings | Conditional | Multiple decision tree IDs to execute in this node |
| `depends_on` | list of strings | Yes | List of DAG node IDs that must complete before this node executes. Empty list means no dependencies |

**Auto-detection.** The engine shall automatically detect DAG mode when the `dag` key exists in the YAML config. When absent, the engine shall fall back to flat sequential execution of all trees.

---

### 6.2 Condition Evaluation

#### FR-6.2.1: Simple Conditions

A simple condition shall evaluate a single comparison between a column value and a reference value (another column or a literal).

| Field | Type | Required For | Description |
|-------|------|-------------|-------------|
| `operator` | string | All | One of the 14 supported operators |
| `left_column` | string | All | Excel column name to read the left-hand value from |
| `right_column` | string | Binary comparison | Excel column name for the right-hand value |
| `right_value` | any | Binary comparison | Literal value for the right-hand side |
| `pattern` | string | `regex` | Regular expression pattern |
| `low` | number | `between` | Lower bound (inclusive) |
| `high` | number | `between` | Upper bound (inclusive) |
| `values` | list | `in`, `not_in` | List of values for set membership |

#### FR-6.2.2: Operator Registry

The engine shall support the following 14 operators organized into 6 categories.

**Comparison Operators (binary: left vs right)**

| Operator | Description | Null Behavior |
|----------|-------------|---------------|
| `gt` | Left greater than right | Returns false if either is null |
| `gte` | Left greater than or equal to right | Returns false if either is null |
| `lt` | Left less than right | Returns false if either is null |
| `lte` | Left less than or equal to right | Returns false if either is null |
| `eq` | Left equals right (numeric-first, string fallback) | Returns true only if both are null |
| `neq` | Left not equal to right | Returns false only if both are null |

Type coercion: the engine shall attempt numeric comparison first (`float(left)` vs `float(right)`). If either value cannot be converted to float, the engine shall fall back to string comparison (`str(left).strip()` vs `str(right).strip()`).

**String Operators (binary: left vs right_value)**

| Operator | Description | Null Behavior |
|----------|-------------|---------------|
| `contains` | right_value is a substring of left | Returns false if left is null |
| `starts_with` | left starts with right_value | Returns false if left is null |
| `ends_with` | left ends with right_value | Returns false if left is null |
| `regex` | left matches the regex `pattern` (uses `re.match`) | Returns false if left is null |
| `is_empty` | left is null, None, or whitespace-only string | Returns true if null |

**Null Operators (unary: left only)**

| Operator | Description |
|----------|-------------|
| `is_null` | Returns true if left is None or `float('nan')` |
| `is_not_null` | Returns true if left is not None and not `float('nan')` |

**Range Operator**

| Operator | Description | Fields |
|----------|-------------|--------|
| `between` | Returns true if `low <= left <= high` (inclusive) | `low`, `high` |

**Set Operators**

| Operator | Description | Fields |
|----------|-------------|--------|
| `in` | Returns true if left is in the values list | `values` |
| `not_in` | Returns true if left is not in the values list | `values` |

**Compound Operators**

| Operator | Description | Fields |
|----------|-------------|--------|
| `and` | Returns true if ALL child conditions are true | `conditions` (list) |
| `or` | Returns true if ANY child condition is true | `conditions` (list) |

Compound conditions shall nest arbitrarily deep. A compound condition's `conditions` list may contain both simple conditions and other compound conditions.

#### FR-6.2.3: Operator Extensibility

The engine shall support registering custom operators at runtime without modifying the evaluator source code.

```python
registry = OperatorRegistry()
registry.register("custom_op", lambda left, right, **kwargs: custom_logic(left, right))
```

The YAML config shall reference the custom operator by name, and the evaluator shall look it up from the registry at evaluation time.

#### FR-6.2.4: Pydantic Validation of Conditions

The Pydantic schema shall enforce operand requirements at config load time.

| Operator | Required Fields | Validation Error If Missing |
|----------|----------------|---------------------------|
| `gt`, `gte`, `lt`, `lte`, `eq`, `neq` | `right_column` or `right_value` | "Operator 'X' requires right_column or right_value" |
| `contains`, `starts_with`, `ends_with` | `right_column` or `right_value` | Same as above |
| `regex` | `pattern` | "Operator 'regex' requires 'pattern'" |
| `between` | `low` and `high` | "Operator 'between' requires 'low' and 'high'" |
| `in`, `not_in` | `values` (non-empty list) | "Operator 'X' requires 'values' list" |

---

### 6.3 Action Dispatching

#### FR-6.3.1: Action Branch Structure

Each `on_true` and `on_false` branch shall support the following fields.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `action` | string | `"none"` | Action type: `api_call`, `db_update`, `log_only`, `none` |
| `method` | string | `"POST"` | HTTP method for `api_call` |
| `endpoint` | string | null | API endpoint path (appended to `settings.api_base_url`) |
| `payload` | dict | null | JSON payload for `api_call` |
| `set` | dict | null | Key-value pairs for `db_update` |
| `table` | string | null | Override table name for `db_update` (defaults to `settings.db_table`) |
| `next_decision` | DecisionNode | null | Recursive child decision tree to evaluate after this action |

#### FR-6.3.2: API Call Action

When `action` is `"api_call"`, the engine shall execute an HTTP request with the following behavior.

| Behavior | Specification |
|----------|--------------|
| URL construction | `settings.api_base_url` + `endpoint` |
| HTTP method | Value of `method` field (default: POST) |
| Request body | JSON-serialized `payload` dict with `row_id` injected |
| Timeout | 30 seconds per request |
| Retry policy | 3 attempts with immediate retry on failure |
| Success codes | 200, 201, 202, 204 |
| HTTP client | httpx (primary), requests (fallback if httpx not installed) |
| Content-Type | `application/json` |

#### FR-6.3.3: Database Update Action

When `action` is `"db_update"`, the engine shall perform an upsert operation.

| Behavior | Specification |
|----------|--------------|
| Connection | SQLAlchemy engine from `settings.db_connection_string` |
| Connection pooling | `pool_pre_ping=True`, cached engines per connection string |
| Table | `table` field if specified, otherwise `settings.db_table` |
| Upsert logic | Check if row exists by `row_id_column`, UPDATE if yes, INSERT if no |
| Values | All key-value pairs from `set` dict, plus `row_id` |
| Fallback | If SQLAlchemy is not installed, use raw sqlite3 INSERT |
| Supported databases | SQLite, PostgreSQL, MySQL (via connection string dialect) |

#### FR-6.3.4: Log Only Action

When `action` is `"log_only"`, the engine shall log the `set` or `payload` content to stdout via Python logging at INFO level. No side effects shall be produced.

#### FR-6.3.5: None Action

When `action` is `"none"`, the engine shall skip action execution. This is used for branches that only contain a `next_decision` without a direct action.

#### FR-6.3.6: Action Extensibility

The engine shall support registering custom action handlers at runtime.

```python
dispatcher = ActionDispatcher()
dispatcher.register_handler("slack_notify", my_slack_handler)
```

The YAML config shall reference the custom action by name. The handler function shall receive `(branch, row, row_id, context)` and return a result dict.

#### FR-6.3.7: Dry-Run Mode

When `settings.dry_run` is `true` or the `--dry-run` CLI flag is set, the engine shall log all actions that would be executed (including URL, method, payload, and set values) but shall not make any HTTP calls or database writes. The audit trail shall record `status: "dry_run"` for all actions.

---

### 6.4 Decision Tree Execution

#### FR-6.4.1: Flat Mode Execution

When no `dag` section is present in the YAML config, the engine shall execute all decision trees sequentially in the order they appear in the `decision_trees` list. For each row, every tree shall be evaluated regardless of the results of other trees.

#### FR-6.4.2: Recursive Tree Walking

For each decision node, the engine shall perform the following steps in order.

1. **Evaluate** the node's `condition` against the current row data using the operator registry.
2. **Select** the `on_true` or `on_false` branch based on the condition result.
3. **Dispatch** the selected branch's action using the action dispatcher.
4. **Record** the evaluation result, branch taken, and action result in the audit trail.
5. **Recurse** into `next_decision` if the selected branch has one, incrementing the depth counter.

There shall be no hard limit on recursion depth. The depth is bounded by the YAML config structure.

#### FR-6.4.3: Row Processing Loop

The engine shall process rows in the following order.

1. Load the Excel file using chunked reading with `settings.batch_size` rows per chunk.
2. For each row in each chunk, extract the `row_id` from `settings.row_id_column`.
3. Walk all decision trees (flat mode) or execute the DAG (DAG mode) for the row.
4. Generate a per-row audit report containing all node evaluation results.
5. Accumulate row reports into the overall summary.

---

### 6.5 DAG Execution

#### FR-6.5.1: Topological Sort

The DAG executor shall use Kahn's algorithm to sort DAG nodes into execution layers.

1. Compute the in-degree (number of dependencies) for each node.
2. Initialize a queue with all nodes having in-degree 0 — these form Layer 0.
3. For each node in the current layer, decrement the in-degree of all downstream nodes.
4. Any downstream node reaching in-degree 0 is added to the next layer.
5. Repeat until all nodes are processed.
6. If any nodes remain unprocessed, the DAG contains a cycle and the engine shall raise a `ValueError`.

#### FR-6.5.2: Layer Execution

Nodes within the same layer have no dependencies on each other and are candidates for parallel execution. The current implementation executes them sequentially within each layer, but the architecture shall preserve layer grouping for future parallelization.

#### FR-6.5.3: Dependency Failure Handling

When a DAG node's dependency has failed or been skipped, the engine shall handle it according to the `on_dependency_failure` setting.

| Mode | Behavior |
|------|----------|
| `skip` | Mark the dependent node as "skipped" in the audit trail. Do not execute its trees. Propagate skip to its own dependents. |
| `continue` | Execute the dependent node's trees regardless of the dependency's status. |
| `abort` | Raise a `RuntimeError` and halt processing for the current row. |

#### FR-6.5.4: DAG Node States

Each DAG node shall transition through the following states.

| State | Description |
|-------|-------------|
| `pending` | Initial state before execution |
| `running` | Currently executing its trees |
| `completed` | All trees executed successfully |
| `skipped` | Skipped due to dependency failure |
| `failed` | One or more trees raised an exception |

#### FR-6.5.5: Multi-Tree DAG Nodes

A single DAG node may reference multiple decision trees via `tree_ids`. All trees within the node shall execute sequentially. The entire node must complete before dependent nodes in the next layer can start.

#### FR-6.5.6: DAG Validation

At initialization, the DAG executor shall validate the following.

1. All `depends_on` references point to existing DAG node IDs.
2. The graph contains no cycles (verified by the topological sort).
3. All `tree_id` and `tree_ids` values reference existing decision trees in the `decision_trees` section.

---

### 6.6 Excel Data Loading

#### FR-6.6.1: File Reading

The engine shall read Excel files (`.xlsx` format) using pandas with the openpyxl engine. The engine shall support reading from any sheet specified by `settings.sheet_name`.

#### FR-6.6.2: Chunked Processing

The engine shall read rows in chunks of `settings.batch_size` using a Python generator to limit memory consumption for large files. Each chunk shall be converted to a list of dictionaries with column names as keys.

#### FR-6.6.3: Column Pre-Validation

Before processing any rows, the engine shall extract all column names referenced in the YAML config (from conditions, including nested `next_decision` nodes and compound conditions) and validate that every referenced column exists in the Excel file. If any columns are missing, the engine shall log the missing column names and exit with a non-zero status code.

---

### 6.7 Audit Trail and Reporting

#### FR-6.7.1: Per-Row Report

For each processed row, the engine shall generate a report containing the following fields.

| Field | Type | Description |
|-------|------|-------------|
| `row_id` | string | The row identifier value |
| `total_nodes_evaluated` | integer | Total decision nodes evaluated (including nested) |
| `true_count` | integer | Number of conditions that evaluated to true |
| `false_count` | integer | Number of conditions that evaluated to false |
| `error_count` | integer | Number of actions that returned error status |
| `errors` | list | List of node results where action status was "error" |
| `tree_results` | list | Full tree traversal results (see FR-6.7.2) |

#### FR-6.7.2: Per-Node Result

Each node result within `tree_results` shall contain the following fields.

| Field | Type | Description |
|-------|------|-------------|
| `node_id` | string | The decision node's `id` field |
| `node_name` | string | The decision node's `name` field |
| `condition_result` | boolean | Whether the condition evaluated to true or false |
| `branch_taken` | string | `"on_true"` or `"on_false"` |
| `action` | dict | The action result including `action` type and `status` |
| `children` | list | Results from any `next_decision` nodes (recursive) |

#### FR-6.7.3: DAG Report (DAG Mode Only)

When running in DAG mode, each row report shall include an additional `dag` field containing the following.

| Field | Type | Description |
|-------|------|-------------|
| `row_id` | string | The row identifier |
| `layers` | list | Per-layer execution report with node statuses |
| `node_results` | dict | Per-node status and tree count |
| `total_nodes_executed` | integer | Number of DAG nodes that executed |
| `total_skipped` | integer | Number of DAG nodes skipped due to dependency failures |

#### FR-6.7.4: Execution Summary

After all rows are processed, the engine shall log and optionally write to JSON a summary report containing the following.

| Field | Type | Description |
|-------|------|-------------|
| `mode` | string | `"flat"` or `"dag"` |
| `total_rows` | integer | Total rows processed |
| `total_trees` | integer | Number of decision trees in config |
| `total_evaluations` | integer | Sum of all node evaluations across all rows |
| `total_errors` | integer | Sum of all action errors across all rows |
| `elapsed_seconds` | float | Total execution time |
| `dry_run` | boolean | Whether dry-run mode was active |
| `dag_layers` | list | (DAG mode only) The execution layer plan |

---

### 6.8 Command-Line Interface

#### FR-6.8.1: CLI Arguments

| Argument | Short | Required | Description |
|----------|-------|----------|-------------|
| `--config` | `-c` | Yes | Path to YAML decision tree config file |
| `--dry-run` | `-d` | No | Run without executing actions (overrides `settings.dry_run`) |
| `--output` | `-o` | No | Save execution summary to JSON file |

#### FR-6.8.2: Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Successful execution (all rows processed, zero or more action errors) |
| 1 | Configuration error (missing file, schema validation failure, missing columns) |
| 2 | Runtime error (DAG cycle detected, unrecoverable exception) |

---

## 7. Flowchart-to-YAML Mapping Methodology

### 7.1 Element Mapping

| Flowchart Element | YAML Equivalent |
|-------------------|----------------|
| Diamond (decision node) | `condition` block with `operator` |
| Rectangle (action box) | `action: "api_call"` or `action: "db_update"` |
| Arrow labeled YES | `on_true` branch |
| Arrow labeled NO | `on_false` branch |
| Sequential diamonds | Nested `next_decision` |
| Exception box | `action: "api_call"` to `/exceptions/log` with `exception_id` |
| Update box | `action: "api_call"` to `/lam/update` with `fields` list |
| Parallel sections | Separate DAG `nodes` with `depends_on: []` |
| Dependent sections | DAG `nodes` with `depends_on: ["parent_id"]` |

### 7.2 Guard/Filter Pattern

A chain of NO diamonds leading to a final check is a guard pattern. Instead of nesting each diamond as a `next_decision`, collapse them into a `not_in` compound condition.

Flowchart:
```
LAND→LUD? → NO → LUD→LOT? → NO → LOT→SPEC? → NO → Appraisal Change? → YES → Exception
```

YAML:
```yaml
condition:
  operator: "and"
  conditions:
    - operator: "not_in"
      left_column: "ConversionType"
      values: ["LAND_TO_LUD", "LUD_TO_LOT", "LOT_TO_SPEC"]
    - operator: "eq"
      left_column: "AppraisalValueChange"
      right_value: "Yes"
on_true:
  action: "api_call"
  endpoint: "/exceptions/log"
  payload:
    exception_id: 17
```

### 7.3 Deep Nesting Pattern

A chain of YES diamonds leading deeper is a happy-path validation chain. Each diamond becomes a `next_decision` under the previous `on_true`.

Flowchart:
```
Plan Name exists? → YES → Appraisal matched? → YES → BDR has Hard Cost? → YES → Update LAM
```

YAML:
```yaml
condition:
  operator: "eq"
  left_column: "PlanNameExistsInLAM"
  right_value: "Yes"
on_true:
  next_decision:
    id: "appraisal_check"
    condition:
      operator: "eq"
      left_column: "AppraisalValueMatched"
      right_value: "Yes"
    on_true:
      next_decision:
        id: "hard_cost_check"
        condition:
          operator: "eq"
          left_column: "CustomerBDRHasHardCost"
          right_value: "Yes"
        on_true:
          action: "api_call"
          endpoint: "/lam/update"
          payload:
            fields: ["CollateralType", "ConversionDate", "HardCost"]
        on_false:
          action: "api_call"
          endpoint: "/exceptions/log"
          payload:
            exception_id: 7
    on_false:
      action: "api_call"
      endpoint: "/exceptions/log"
      payload:
        exception_id: 6
on_false:
  action: "api_call"
  endpoint: "/exceptions/log"
  payload:
    exception_id: 5
```

---

## 8. Proven Domain Application: LAM Conversion

The engine has been validated against a real-world LAM (Loan Asset Management) Conversion and Percentage Update workflow spanning 10 sections and 19 exception types.

### 8.1 Section Inventory

| Section | Name | Trees | Max Depth | Exception IDs |
|---------|------|-------|-----------|--------------|
| 2 | Percentage Update | 1 | 2 | 1 |
| 3 | Conversion | 9 | 6 | 2-13 |
| 5 | Hard Cost | 1 | 1 | 14 |
| 6 | Lot Cost | 1 | 1 | 15 |
| 7 | Contract Price | 1 | 1 | 16 |
| 8 | Appraisal | 1 | 1 | 17 |
| 9 | Borrowing Base | 1 | 1 | 18 |
| 10 | Availability | 1 | 1 | 19 |

### 8.2 Conversion Types Covered

| From → To | Exception Path | Update Path |
|-----------|---------------|-------------|
| SPEC → PRESOLD | 2 (contract price issue) | Update CollateralType + ContractPrice |
| MODEL → PRESOLD | 2 (contract price issue) | Update CollateralType + ContractPrice |
| SPEC → MODEL | — | Update CollateralType |
| LAND → LUD | 3 (plan missing), 4 (appraisal mismatch) | Update CollateralType |
| LUD → LOT | 5 (plan/appraisal issue) | Update CollateralType + ConversionDate |
| LOT → SPEC | 5, 6, 7, 8 | Update CollateralType + ConversionDate + HardCost |
| LUD → SPEC | 5, 6, 7, 8 | Update CollateralType + ConversionDate + HardCost |
| LOT → MODEL | 5, 6, 7, 8 | Update CollateralType + ConversionDate + HardCost |
| LUD → MODEL | 5, 6, 7, 8 | Update CollateralType + ConversionDate + HardCost |
| LOT → PRESOLD | 8, 9, 10, 11 | Update CollateralType + ConversionDate + HardCost + ContractPrice |
| LUD → PRESOLD | 8, 9, 10, 11 | Update CollateralType + ConversionDate + HardCost + ContractPrice |
| PRESOLD → SPEC | 12 (custom collateral basis) | Update CollateralType, remove ContractPrice |
| PRESOLD → MODEL | 12 (custom collateral basis) | Update CollateralType, remove ContractPrice |
| Any other | 11 (unhandled) | — |
| Non-logical | 13 (illogical progression) | — |

### 8.3 DAG Execution Plan

```
Layer 0 (parallel):
  sec2_percentage        — 1 tree, no dependencies
  sec3_conversion        — 9 trees, no dependencies
  sec9_borrowing_base    — 1 tree, no dependencies
  sec10_availability     — 1 tree, no dependencies

Layer 1 (parallel, depends on sec3_conversion):
  sec5_hard_cost         — 1 tree
  sec6_lot_cost          — 1 tree
  sec7_contract_price    — 1 tree
  sec8_appraisal         — 1 tree
```

---

## 9. Error Handling

### 9.1 Fail-Fast Errors (Process Exits)

| Error | Stage | Behavior |
|-------|-------|----------|
| YAML file not found | Config loading | `FileNotFoundError`, exit code 1 |
| YAML syntax error | Config loading | PyYAML `ScannerError`, exit code 1 |
| Pydantic validation error | Config loading | `ValidationError` with field-level details, exit code 1 |
| Missing Excel file | Excel loading | `FileNotFoundError`, exit code 1 |
| Missing columns | Column pre-validation | Log missing column names, `sys.exit(1)` |
| DAG cycle detected | DAG initialization | `ValueError`, exit code 2 |
| DAG dependency not found | DAG initialization | `ValueError`, exit code 2 |

### 9.2 Per-Row Errors (Logged, Processing Continues)

| Error | Stage | Behavior |
|-------|-------|----------|
| API call failure (all retries exhausted) | Action dispatch | Record `status: "error"` in audit, continue to next tree |
| DB write failure | Action dispatch | Record `status: "error"` in audit, continue to next tree |
| Unknown operator | Condition evaluation | Raise `ValueError` (should be caught by Pydantic at load time) |
| Type coercion failure | Condition evaluation | Fall back to string comparison, log at DEBUG level |

---

## 10. Performance Characteristics

| Dimension | Specification |
|-----------|--------------|
| Startup time | < 1 second for configs with up to 100 trees |
| Per-row evaluation | < 1ms per tree node (in-memory operations only) |
| Excel loading | Chunked at `batch_size` rows; memory footprint proportional to chunk size, not file size |
| API call overhead | 30-second timeout per call, 3 retries, sequential within each action |
| DB write overhead | Connection pooled per connection string, upsert per row |
| Total throughput | ~100 rows/second with 16 trees per row (dry-run, measured) |

---

## 11. Security Considerations

| Concern | Mitigation |
|---------|-----------|
| YAML injection | Pydantic schema limits the `action` field to a fixed enum (`api_call`, `db_update`, `log_only`, `none`). No arbitrary code execution from YAML. |
| SQL injection | SQLAlchemy parameterized queries for all DB operations. No string interpolation in SQL. |
| API credential management | No credentials stored in YAML. API auth handled by the deployment environment (env vars, secrets manager). |
| Excel macro execution | openpyxl does not execute macros. `.xlsm` files are read for data only. |
| File path traversal | The `excel_file` path is validated for existence. No file write operations to user-specified paths. |

---

## 12. Testing Strategy

### 12.1 Test Data Requirements

Each test dataset shall cover the following dimensions.

| Dimension | Minimum Coverage |
|-----------|-----------------|
| Happy path per tree | At least 1 row that evaluates to `on_true` for each decision tree |
| Exception path per tree | At least 1 row that evaluates to `on_false` for each decision tree |
| Null handling | At least 1 row with null/NaN values in columns referenced by `is_null`/`is_not_null` |
| Deep nesting | At least 1 row that traverses the maximum nesting depth in the config |
| Compound conditions | At least 1 row that tests each AND/OR condition with both true and false outcomes |
| DAG dependency skip | At least 1 row where a Layer 0 node fails, causing Layer 1 nodes to be skipped |

### 12.2 Validation Approach

| Level | Method | What It Validates |
|-------|--------|------------------|
| Schema | Pydantic model instantiation | YAML structure, field types, operand requirements |
| Column | `column_extractor` + `validate_columns` | All YAML-referenced columns exist in Excel |
| Logic | Dry-run execution | Correct tree traversal, branch selection, action routing |
| Integration | Live execution against test API/DB | End-to-end action execution and response handling |
| DAG | Layer inspection + dependency skip test | Correct topological sort, parallel grouping, failure propagation |

---

## 13. Deployment

### 13.1 Prerequisites

| Dependency | Version | Installation |
|-----------|---------|-------------|
| Python | 3.10+ | System package |
| PyYAML | 6.0+ | `pip install pyyaml` |
| Pydantic | 2.0+ | `pip install pydantic` |
| pandas | 2.0+ | `pip install pandas` |
| openpyxl | 3.1+ | `pip install openpyxl` |
| httpx | 0.24+ | `pip install httpx` (optional, falls back to requests) |
| SQLAlchemy | 2.0+ | `pip install sqlalchemy` (optional, falls back to sqlite3) |

### 13.2 Execution Commands

```bash
# Dry run (recommended first step)
python main.py --config config/lam_full_dag.yaml --dry-run

# Live execution
python main.py --config config/lam_full_dag.yaml

# With JSON output
python main.py --config config/lam_full_dag.yaml --output results/output.json

# Flat mode (no DAG)
python main.py --config config/lam_conversion.yaml --dry-run
```

### 13.3 CI/CD Integration

The engine shall be invocable as a standalone Python script with exit codes suitable for CI/CD pipelines. A non-zero exit code indicates a configuration or runtime error that prevents successful execution. Action-level errors (e.g., a single API call failure) are logged but do not cause a non-zero exit code.

---

## 14. Future Enhancements

| ID | Enhancement | Description | Priority |
|----|------------|-------------|----------|
| FE1 | Async parallel execution | Use `asyncio.gather()` within DAG layers for true parallel execution | P1 |
| FE2 | CSV and Parquet support | Extend `excel_reader.py` to support additional tabular formats | P2 |
| FE3 | Webhook action type | Add a `webhook` action that fires events to configurable endpoints | P2 |
| FE4 | Slack/Email notification action | Add `slack_notify` and `email_notify` action types | P3 |
| FE5 | YAML hot-reload | Watch the config file for changes and reload without restart | P3 |
| FE6 | Web dashboard | Real-time execution monitoring with per-row drill-down | P3 |
| FE7 | Rule conflict detection | Static analysis of YAML to detect overlapping or contradictory conditions | P2 |
| FE8 | Performance profiling | Per-tree timing metrics to identify slow conditions or actions | P2 |

---

## 15. Glossary

| Term | Definition |
|------|-----------|
| **Decision Tree** | A tree structure where each node contains a condition and two branches (on_true, on_false), each of which may contain an action and/or a child node |
| **DAG** | Directed Acyclic Graph — a graph where nodes have directed edges (dependencies) and no cycles exist |
| **Operator** | A named function that evaluates a condition against row data (e.g., `gt`, `eq`, `contains`) |
| **Action** | A side effect triggered by a branch (API call, DB update, log) |
| **Action Dispatcher** | The component that routes actions to handler functions based on the action type |
| **Operator Registry** | A lookup table mapping operator names to callable functions |
| **Tree Walker** | The component that recursively traverses decision nodes, evaluating conditions and dispatching actions |
| **Layer** | A group of DAG nodes with no dependencies on each other, eligible for parallel execution |
| **Fail-fast** | The principle that configuration errors should cause immediate termination at startup, not silent failures during processing |
| **Dry-run** | An execution mode where all actions are logged but not executed |
| **Upsert** | A database operation that inserts a row if it does not exist, or updates it if it does |
| **Topological Sort** | An ordering of graph nodes such that every node appears after all of its dependencies |

---

## Appendix A: Complete Project File Listing

```
yaml-decision-tree-engine/
├── main.py                              # CLI entry point, mode detection, orchestration
├── requirements.txt                     # Python dependencies
├── config/
│   ├── lam_conversion.yaml              # Sections 2-3 (flat mode)
│   ├── lam_sections_5_10.yaml           # Sections 5-10 standalone
│   └── lam_full_dag.yaml                # All sections with DAG orchestration
├── engine/
│   ├── __init__.py
│   ├── loader.py                        # YAML → Pydantic validation
│   ├── evaluator.py                     # Operator registry + condition evaluation
│   ├── tree_walker.py                   # Recursive tree traversal + audit trail
│   ├── action_dispatcher.py             # Pluggable action routing
│   ├── excel_reader.py                  # Chunked pandas reader
│   ├── column_extractor.py              # Column name extraction from YAML
│   └── dag_executor.py                  # Kahn's algorithm DAG orchestrator
├── actions/
│   ├── __init__.py
│   ├── api_action.py                    # httpx HTTP calls with retry
│   └── db_action.py                     # SQLAlchemy upsert with sqlite3 fallback
├── models/
│   ├── __init__.py
│   └── schemas.py                       # Pydantic v2 schemas with forward references
├── data/
│   └── lam_data.xlsx                    # Sample test data
└── results/
    └── lam_results.json                 # Sample execution output
```

---

## Appendix B: YAML Config Quick Reference

```yaml
# Minimal working config
settings:
  excel_file: "data/input.xlsx"
  row_id_column: "ID"

decision_trees:
  - id: "rule_1"
    name: "My first rule"
    condition:
      operator: "gt"
      left_column: "ColumnA"
      right_column: "ColumnB"
    on_true:
      action: "api_call"
      endpoint: "/update"
      payload:
        result: "Yes"
    on_false:
      action: "db_update"
      set:
        result: "No"

# Optional: add DAG for dependency ordering
dag:
  nodes:
    - id: "group_1"
      tree_id: "rule_1"
      depends_on: []
  on_dependency_failure: "skip"
```
