"""
yaml_runtime.py
---------------
Secure execution engine for YAML-defined tools.
Supports three tool types:
  - http_api  : REST API calls via httpx
  - sql       : Parameterized DB queries via SQLAlchemy
  - python    : Sandboxed Python via RestrictedPython
"""

import os
import yaml
import json
import httpx
import sqlalchemy
import asyncio
from typing import Any
from jinja2 import sandbox
from dotenv import load_dotenv

load_dotenv()

# ── RestrictedPython setup ────────────────────────────────────────────────────
try:
    import RestrictedPython
    RESTRICTED_AVAILABLE = True

    SAFE_BUILTINS = RestrictedPython.safe_builtins.copy()
    SAFE_BUILTINS.update({
        "len": len, "range": range, "enumerate": enumerate,
        "zip": zip, "map": map, "filter": filter,
        "sum": sum, "min": min, "max": max, "round": round,
        "list": list, "dict": dict, "set": set, "tuple": tuple,
        "str": str, "int": int, "float": float, "bool": bool,
        "sorted": sorted, "reversed": reversed,
        "isinstance": isinstance, "type": type,
        "abs": abs, "any": any, "all": all,
        "print": print,
    })
except ImportError:
    RESTRICTED_AVAILABLE = False
    print("[WARN] RestrictedPython not installed — python tools will be disabled")

# Only these stdlib modules may be imported inside python-type tools
ALLOWED_IMPORTS = {
    "json", "statistics", "math", "datetime",
    "re", "collections", "itertools", "functools",
}


class YAMLFunctionRuntime:
    """
    Loads a YAML tool definition file and executes tools securely.
    One instance per YAML file — safe to share across concurrent calls
    since all state is per-invocation (no shared mutable state).
    """

    def __init__(self, yaml_path: str):
        with open(yaml_path) as f:
            raw = yaml.safe_load(f)

        self.registry: dict[str, dict] = {
            fn["id"]: fn for fn in raw["functions"]
        }

        # Secrets come from environment only — never from YAML
        self.secrets: dict[str, str] = {k: v for k, v in os.environ.items() if v}

        # Sandboxed Jinja2 — blocks os, subprocess, file access in templates
        self.jinja = sandbox.SandboxedEnvironment()

    # ── Input resolution ──────────────────────────────────────────────────────

    def _resolve(self, template: Any, ctx: dict) -> str:
        """Render a Jinja2 template string with inputs + secrets."""
        return self.jinja.from_string(str(template)).render(
            inputs=ctx.get("inputs", {}),
            secrets=self.secrets,
        )

    def _resolve_inputs(self, fn_def: dict, raw_inputs: dict) -> dict:
        """Validate required fields and apply defaults."""
        resolved = {}
        for name, schema in fn_def.get("inputs", {}).items():
            if name in raw_inputs:
                resolved[name] = raw_inputs[name]
            elif "default" in schema:
                resolved[name] = schema["default"]
            elif schema.get("required", False):
                raise ValueError(
                    f"[{fn_def['id']}] Missing required input: '{name}'"
                )
        return resolved

    # ── Public execute ────────────────────────────────────────────────────────

    def execute(self, function_id: str, inputs: dict) -> Any:
        """Execute a YAML-defined tool by ID. Thread-safe."""
        fn = self.registry.get(function_id)
        if not fn:
            raise ValueError(f"Unknown function: '{function_id}'")

        inputs = self._resolve_inputs(fn, inputs)
        ctx = {"inputs": inputs}
        fn_type = fn["type"]

        if fn_type == "http_api":
            return self._run_http(fn["config"], ctx)
        elif fn_type == "sql":
            return self._run_sql(fn["config"], ctx)
        elif fn_type == "python":
            return self._run_python(fn["python"], inputs)
        else:
            raise ValueError(f"Unsupported tool type: '{fn_type}'")

    # ── HTTP executor ─────────────────────────────────────────────────────────

    def _run_http(self, config: dict, ctx: dict) -> Any:
        url     = self._resolve(config["url"], ctx)
        method  = config.get("method", "GET").upper()
        params  = {k: self._resolve(v, ctx) for k, v in config.get("params",  {}).items()}
        headers = {k: self._resolve(v, ctx) for k, v in config.get("headers", {}).items()}
        body    = {k: self._resolve(v, ctx) for k, v in config.get("body",    {}).items()} or None

        with httpx.Client(timeout=30) as client:
            resp = client.request(method, url, params=params, headers=headers, json=body)
            resp.raise_for_status()
            return resp.json()

    # ── SQL executor ──────────────────────────────────────────────────────────

    def _run_sql(self, config: dict, ctx: dict) -> list[dict]:
        conn_str = self._resolve(config["connection"], ctx)
        query    = config["query"]

        # Params are bound — NOT string-interpolated → SQL injection safe
        bound = {k: self._resolve(v, ctx) for k, v in config.get("params", {}).items()}

        engine = sqlalchemy.create_engine(conn_str)
        with engine.connect() as conn:
            result = conn.execute(sqlalchemy.text(query), bound)
            return [dict(row._mapping) for row in result]

    # ── Python executor ───────────────────────────────────────────────────────

    def _run_python(self, python_def: dict, inputs: dict) -> Any:
        """
        Executes user-provided Python code safely via RestrictedPython.
        Code must assign its output to a variable named `result`.
        Only whitelisted builtins and ALLOWED_IMPORTS are accessible.
        """
        if not RESTRICTED_AVAILABLE:
            raise RuntimeError(
                "RestrictedPython is not installed. "
                "Install it with: pip install RestrictedPython"
            )

        code = python_def["code"]

        # Compile through RestrictedPython — blocks dangerous AST nodes
        compiled = RestrictedPython.compile_restricted(code, "<yaml_tool>", "exec")

        def _safe_import(name, *args, **kwargs):
            if name not in ALLOWED_IMPORTS:
                raise ImportError(
                    f"Import '{name}' is not allowed in YAML tools. "
                    f"Allowed: {sorted(ALLOWED_IMPORTS)}"
                )
            return __import__(name, *args, **kwargs)

        local_ns = {"inputs": inputs, "result": None}
        global_ns = {
            "__builtins__":  SAFE_BUILTINS,
            "__import__":    _safe_import,
            "_print_":       RestrictedPython.PrintCollector,
            "_getiter_":     iter,
            "_getattr_":     getattr,
            "_inplacevar_":  RestrictedPython.Guards.guarded_inplacevar,
        }

        exec(compiled, global_ns, local_ns)  # noqa: S102

        if local_ns.get("result") is None:
            raise ValueError(
                f"Python tool did not set `result`. "
                "Assign your output to a variable named `result`."
            )

        return local_ns["result"]
