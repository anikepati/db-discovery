# ADK Kill Switch — Lightweight Implementation

## Design

```
User/Operator                Redis                 Background Poller        ADK Plugin (in-memory)
     │                         │                         │                         │
     │── POST /jobs/kill ─────▶│                         │                         │
     │                         │── SET KILLED ──────────▶│                         │
     │                         │                         │                         │
     │                         │         (polls every N sec)                       │
     │                         │                         │── read KILLED ─────────▶│
     │                         │                         │   set _killed_jobs      │
     │                         │                         │                         │
     │                         │                         │      (next callback)    │
     │                         │                         │         ┌───────────────│
     │                         │                         │         │ check in-mem  │
     │                         │                         │         │ dict — O(1)   │
     │                         │                         │         │ return Content│
     │                         │                         │         └───────────────│
```

Three moving parts:

1. **Kill API** — FastAPI endpoint writes `KILLED` to Redis. This is the only Redis write path.
2. **Background Poller** — Async task polls Redis every 2 seconds, syncs killed job IDs into a plain Python `set`.
3. **ADK Plugin** — `before_agent_callback` checks the in-memory `set`. Zero I/O. Zero latency.

---

## 1. Kill API (`kill_api.py`)

```python
"""
kill_api.py — Minimal REST API for kill switch.
Run: uvicorn kill_api:app --port 8900
"""
import redis.asyncio as redis
from fastapi import FastAPI

app = FastAPI(title="ADK Kill Switch")
r = redis.Redis(host="localhost", port=6379, decode_responses=True)

TTL = 86400  # 24h auto-cleanup


@app.post("/jobs/{job_id}/kill")
async def kill_job(job_id: str):
    await r.set(f"kill:{job_id}", "1", ex=TTL)
    return {"killed": job_id}


@app.delete("/jobs/{job_id}/kill")
async def revoke_kill(job_id: str):
    await r.delete(f"kill:{job_id}")
    return {"revoked": job_id}


@app.get("/jobs/{job_id}/status")
async def get_status(job_id: str):
    killed = await r.exists(f"kill:{job_id}")
    return {"job_id": job_id, "killed": bool(killed)}
```

---

## 2. Kill Switch Plugin (`kill_switch.py`)

Everything in one file. The plugin owns the poller and the in-memory state.

```python
"""
kill_switch.py — ADK Kill Switch Plugin with in-memory fast path.

Usage:
    plugin = KillSwitchPlugin(redis_host="localhost", poll_interval=2.0)
    app = AdkApp(root_agent=my_agent, plugins=[plugin])
"""
import asyncio
import logging
from typing import Optional

import redis.asyncio as redis
from google.adk.plugins import BasePlugin
from google.adk.agents import BaseAgent
from google.adk.callback_context import CallbackContext
from google.adk.types import Content, Part

logger = logging.getLogger("kill_switch")


class KillSwitchPlugin(BasePlugin):
    """
    Checks an in-memory set on every agent callback — O(1), no I/O.
    A background task polls Redis to sync killed job IDs into that set.
    """

    def __init__(
        self,
        redis_host: str = "localhost",
        redis_port: int = 6379,
        poll_interval: float = 2.0,
    ):
        super().__init__()
        self._redis_host = redis_host
        self._redis_port = redis_port
        self._poll_interval = poll_interval

        # ── The fast path: in-memory set of killed job IDs ──
        self._killed_jobs: set[str] = set()

        # ── Poller lifecycle ──
        self._poller_task: Optional[asyncio.Task] = None
        self._redis: Optional[redis.Redis] = None

    # ── Poller ───────────────────────────────────────────

    async def _start_poller(self):
        """Lazily start the background poller on first callback."""
        if self._poller_task is not None:
            return
        self._redis = redis.Redis(
            host=self._redis_host,
            port=self._redis_port,
            decode_responses=True,
        )
        self._poller_task = asyncio.create_task(self._poll_loop())
        logger.info(
            f"Kill switch poller started (interval={self._poll_interval}s)"
        )

    async def _poll_loop(self):
        """Sync Redis kill keys into the in-memory set."""
        while True:
            try:
                # SCAN for all kill:* keys
                cursor, keys = 0, []
                while True:
                    cursor, batch = await self._redis.scan(
                        cursor=cursor, match="kill:*", count=100
                    )
                    keys.extend(batch)
                    if cursor == 0:
                        break

                # Extract job IDs: "kill:job123" → "job123"
                active_kills = {k.split(":", 1)[1] for k in keys}

                # Atomic swap
                self._killed_jobs = active_kills

            except redis.ConnectionError:
                logger.warning("Redis connection lost — retaining last known state")
            except Exception:
                logger.exception("Poller error")

            await asyncio.sleep(self._poll_interval)

    # ── ADK Callbacks ────────────────────────────────────

    async def before_agent_callback(
        self,
        *,
        agent: BaseAgent,
        callback_context: CallbackContext,
    ) -> Optional[Content]:
        # Ensure poller is running
        await self._start_poller()

        job_id = self._extract_job_id(callback_context)
        if not job_id:
            return None

        # ── The fast path: pure in-memory check ──
        if job_id in self._killed_jobs:
            logger.warning(f"Kill switch triggered: job={job_id} agent={agent.name}")
            return Content(
                parts=[Part(text=f"TERMINATED: Job {job_id} was killed by user.")]
            )

        return None

    # ── Helpers ──────────────────────────────────────────

    @staticmethod
    def _extract_job_id(ctx: CallbackContext) -> Optional[str]:
        meta = ctx.invocation_context.session.metadata
        return meta.get("job_id") if meta else None
```

---

## 3. Runner Integration (`main.py`)

```python
"""
main.py — Wire the kill switch into your ADK pipeline.
"""
import asyncio
from google.adk.apps import AdkApp
from google.adk.runners import Runner
from google.adk.agents import SequentialAgent, Agent

from kill_switch import KillSwitchPlugin


async def main():
    # ── 1. Plugin — one line ──
    plugin = KillSwitchPlugin(redis_host="localhost", poll_interval=2.0)

    # ── 2. Agent pipeline ──
    pipeline = SequentialAgent(
        name="compliance_pipeline",
        sub_agents=[
            Agent(name="ingest", model="gemini-2.0-flash",
                  instruction="Parse incoming compliance data."),
            Agent(name="decision", model="gemini-2.0-flash",
                  instruction="Apply compliance rules."),
            Agent(name="report", model="gemini-2.0-flash",
                  instruction="Generate exception report."),
        ],
    )

    # ── 3. App with plugin ──
    app = AdkApp(root_agent=pipeline, plugins=[plugin])
    runner = Runner(app=app)

    # ── 4. Run — job_id in metadata ──
    async for event in runner.run_async(
        "Process Q1 2026 LAM Conversion exceptions.",
        metadata={"job_id": "run_20260419_001"},
    ):
        print(f"[{event.agent}] {event.content}")


if __name__ == "__main__":
    asyncio.run(main())
```

---

## How It Works

**On kill request:** User hits `POST /jobs/run_20260419_001/kill`. Redis gets a key `kill:run_20260419_001` with 24h TTL.

**On next poll cycle (≤2s):** The background poller does a `SCAN kill:*`, extracts `run_20260419_001`, adds it to `self._killed_jobs` (a Python `set`).

**On next agent callback:** `before_agent_callback` checks `job_id in self._killed_jobs` — a hash lookup, nanoseconds. Returns `Content` to halt the Runner.

**On revoke:** User hits `DELETE /jobs/run_20260419_001/kill`. Redis key is removed. Next poll cycle clears it from the in-memory set. Pipeline resumes on next run.

---

## Latency Comparison

| Approach | Per-callback cost | Notes |
|----------|-------------------|-------|
| Redis on every callback | ~1-3ms | Network round-trip per agent step |
| **In-memory + poller** | **~50ns** | Hash set lookup, polling amortized |

The tradeoff is up to `poll_interval` seconds of delay between the kill request and the plugin seeing it. For a 2s interval, that's acceptable — the agent was going to spend 5-30s on an LLM call anyway.

---

## File Structure

```
adk-kill-switch/
├── kill_api.py          # FastAPI — 3 endpoints
├── kill_switch.py       # ADK plugin + poller — single file
├── main.py              # Runner wiring
└── requirements.txt
```

```
# requirements.txt
google-adk>=0.5.0
redis[hiredis]>=5.0.0
fastapi>=0.110.0
uvicorn>=0.27.0
```
