Updated Design Overview
Given that your FastAPI backend is running in one container (e.g., as a web service) and the agent (long-running Python code from Google Agent Development Kit) is in a separate component/container (e.g., as a worker or job container), we need to handle inter-container communication for lifecycle management. Direct process termination via multiprocessing won’t work across containers, as they are isolated.
Key principles:
	•	Decoupling: Use a shared coordination mechanism (e.g., Redis or a database) for signaling start/stop states. This avoids tight coupling and allows scalability.
	•	Graceful Termination: The agent periodically polls the shared state (e.g., every 10-30 seconds) to check if it should stop. This enables clean shutdown without forceful kills.
	•	Identification: Still use UUID for agent IDs.
	•	Orchestration: Assume Docker/Kubernetes for containers. If using Docker Compose, add a Redis container. For production, use managed Redis (e.g., AWS ElastiCache).
	•	Starting the Agent: FastAPI triggers the agent launch (e.g., via Docker API, Kubernetes Job, or a message queue like Celery). For simplicity, we’ll assume you use subprocess in FastAPI to run docker run for a new agent container, but prefer a queue for robustness.
	•	Assumptions:
	◦	Containers share a network (e.g., via Docker Compose).
	◦	Agent container runs a Python script that loops indefinitely but checks stop signals.
	◦	One agent per ID; extend for multiples.
	◦	No persistence needed beyond Redis (it handles restarts).
	◦	Security: Use auth for API calls; secure Redis with password.
If your setup uses Kubernetes, we can adapt to Jobs/Signals. If the agent is already managed by Celery/RabbitMQ, use task revocation instead.
Step 1: Infrastructure Setup
Add a Redis container for shared state:
	•	Install Redis: In Docker Compose, add: services:
	•	  fastapi:
	•	    image: your-fastapi-image
	•	    ports:
	•	      - "8000:8000"
	•	    depends_on:
	•	      - redis
	•	
	•	  redis:
	•	    image: redis:alpine
	•	    ports:
	•	      - "6379:6379"
	•	
	•	  # Agent containers will be spawned dynamically or as separate services
	•	
	•	Python deps: pip install redis docker (for FastAPI to interact with Docker if needed).
Step 2: Backend Setup (FastAPI)
Shared State in Redis
Use Redis hashes for agent states (e.g., key: agent:{agent_id}, fields: status = ‘running’/‘stopped’).
In FastAPI main.py:
from fastapi import FastAPI
from pydantic import BaseModel
import uuid
import redis
import subprocess  # For docker run; replace with better orchestration if possible
import os

app = FastAPI()
redis_client = redis.Redis(host='redis', port=6379, db=0)  # Connect to Redis container

class AgentResponse(BaseModel):
    agent_id: str
    message: str

@app.post("/start_agent", response_model=AgentResponse)
def start_agent():
    agent_id = str(uuid.uuid4())
    
    # Set initial state in Redis
    redis_client.hset(f"agent:{agent_id}", "status", "running")
    
    # Launch agent container (example with docker run; assumes agent image exists)
    # Replace with Kubernetes Job create, Celery task, or HTTP call to agent service
    agent_image = "your-agent-image"  # Docker image with agent code
    subprocess.Popen([
        "docker", "run", "--name", f"agent-{agent_id}", "--network", "your-network",
        "-e", f"AGENT_ID={agent_id}", "-e", "REDIS_HOST=redis",
        agent_image
    ])
    
    return {"agent_id": agent_id, "message": "Agent started"}

@app.post("/kill_agent/{agent_id}", response_model=AgentResponse)
def kill_agent(agent_id: str):
    key = f"agent:{agent_id}"
    if not redis_client.exists(key):
        return {"agent_id": agent_id, "message": "Agent not found"}
    
    # Signal stop
    redis_client.hset(key, "status", "stopped")
    
    # Optional: Stop container via Docker (for immediate cleanup)
    # subprocess.run(["docker", "stop", f"agent-{agent_id}"])
    # But prefer agent self-terminates gracefully
    
    return {"agent_id": agent_id, "message": "Stop signal sent; agent will terminate shortly"}
	•	Start: Sets ‘running’ in Redis, launches a new container with env vars for ID and Redis host.
	•	Kill: Sets ‘stopped’ in Redis. The agent will detect and exit.
	•	Cleanup: Optionally, have a cron or background task in FastAPI to prune stopped containers/Redis keys.
	•	Alternatives for Launching:
	◦	Celery: If using, task = run_agent.delay(agent_id); then task.revoke(terminate=True).
	◦	Kubernetes: Create a Job resource via Kubernetes API client (kubernetes lib).
	◦	HTTP: If agent container has an API, POST to start it.
Step 3: Agent Setup (Python in Separate Container)
The agent’s Dockerfile:
FROM python:3.10-slim
WORKDIR /app
COPY agent.py .
RUN pip install redis your-google-agent-kit-deps
CMD ["python", "agent.py"]
In agent.py (your long-running code):
import os
import time
import redis

agent_id = os.getenv("AGENT_ID")
redis_host = os.getenv("REDIS_HOST", "localhost")
redis_client = redis.Redis(host=redis_host, port=6379, db=0)

def run_agent_logic():
    # Your Google Agent Development Kit code here
    print(f"Agent {agent_id} performing work...")
    time.sleep(10)  # Simulate

if __name__ == "__main__":
    key = f"agent:{agent_id}"
    while True:
        status = redis_client.hget(key, "status")
        if status and status.decode() == "stopped":
            print(f"Agent {agent_id} stopping gracefully...")
            # Cleanup logic here
            redis_client.delete(key)  # Optional self-cleanup
            break
        
        run_agent_logic()
        
        time.sleep(5)  # Poll interval; adjust based on needs (e.g., 10-60s to avoid overload)
	•	The agent runs in a loop, checks Redis status frequently, and exits when ‘stopped’.
	•	Graceful: Add shutdown hooks for your agent kit (e.g., close connections).
	•	If poll interval is too high, termination delays; if too low, wastes CPU.
Step 4: UI Integration (Next.js)
Remains the same as before: Call /start_agent and /kill_agent/{id} via Axios. The UI doesn’t change since it’s API-driven.
Step 5: Testing & Deployment
	•	Local Test (Docker Compose):
	1	Build images for FastAPI and agent.
	2	Run docker-compose up.
	3	From UI/Postman, start agent → Check new container spins up, logs “running”.
	4	Kill → Redis updates, agent detects and exits; container stops (if you add docker stop).
	•	Edge Cases:
	◦	Agent container crashes: Redis state persists; clean manually or with timeouts (e.g., expire keys after 1h).
	◦	No Redis: Fallback to shared volume (e.g., write/stop file), but Redis is more reliable.
	◦	Scaling: For multiple agents, Redis handles concurrency.
	•	Security: Add Redis password; use VLANs for containers; auth on FastAPI.
	•	Enhancements:
	◦	Monitoring: Add endpoints to list active agents (query Redis keys).
	◦	Logging: Use ELK or CloudWatch; agent logs to stdout.
	◦	If no Docker control from FastAPI: Rely solely on Redis signal, and let orchestrator (K8s) handle container lifecycle.
This setup is container-friendly, scalable, and ensures reliable termination. If your agent launch mechanism differs (e.g., already using AWS Lambda or GCS), or if you prefer no polling (use Redis Pub/Sub for real-time signals), let me know for tweaks!
sequenceDiagram
    participant UI as Next.js UI
    participant API as FastAPI Backend
    participant Redis as Redis
    participant Agent as Agent Container

    Note over UI,Agent: Starting the Agent
    UI->>API: POST /start_agent
    activate API
    API->>Redis: Set agent:{id} status="running"
    API->>API: Launch agent container (docker run)
    API-->>UI: {agent_id, "Started"}
    deactivate API
    Agent->>Agent: Start running loop
    loop Every 5-10s
        Agent->>Redis: Get status
        Redis-->>Agent: "running"
        Agent->>Agent: Perform work
    end

    Note over UI,Agent: Killing the Agent
    UI->>API: POST /kill_agent/{id}
    activate API
    API->>Redis: Set status="stopped"
    API-->>UI: "Stop signal sent"
    deactivate API
    Agent->>Redis: Get status
    Redis-->>Agent: "stopped"
    Agent->>Agent: Graceful shutdown
    Agent->>Redis: Optional: Delete key
