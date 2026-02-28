"""
main.py
-------
Entry point. Demonstrates concurrent multi-user requests,
each fully isolated via in-memory MCP sessions.

Usage:
    # First time only — create demo DB:
    python db_setup.py

    # Run single query:
    python main.py

    # Run concurrent simulation:
    python main.py --concurrent
"""

import asyncio
import argparse
from agent import handle_request


# ── Example queries per domain ─────────────────────────────────────────────────

SINGLE_QUERY = "Get all active customers and show a summary of their support tickets"

CONCURRENT_QUERIES = [
    {
        "user_id":    "user_1",
        "session_id": "s1",
        "query":      "How many customers do we have by status? Show the CRM summary.",
    },
    {
        "user_id":    "user_2",
        "session_id": "s2",
        "query":      "Which invoices are overdue by more than 30 days? Show top 5 by amount.",
    },
    {
        "user_id":    "user_3",
        "session_id": "s3",
        "query":      "Show payroll summary by department and calculate attrition rate "
                      "if 8 out of 100 employees left this year.",
    },
    {
        "user_id":    "user_4",
        "session_id": "s4",
        "query":      "Get open support tickets and fetch the first customer's details.",
    },
]


async def run_single():
    """Run a single query."""
    await handle_request(
        user_id="user_1",
        session_id="session_1",
        query=SINGLE_QUERY,
    )


async def run_concurrent():
    """
    Fire all queries at the same time.
    Each gets its own isolated in-memory MCP sessions — no race conditions.
    """
    print("\n🚀 Launching concurrent requests...\n")
    await asyncio.gather(*[
        handle_request(**q)
        for q in CONCURRENT_QUERIES
    ])
    print("\n✅ All concurrent requests completed.")


def main():
    parser = argparse.ArgumentParser(description="YAML MCP Agent")
    parser.add_argument(
        "--concurrent",
        action="store_true",
        help="Run multiple concurrent user requests simultaneously",
    )
    args = parser.parse_args()

    if args.concurrent:
        asyncio.run(run_concurrent())
    else:
        asyncio.run(run_single())


if __name__ == "__main__":
    main()
