"""Load test for the Task Executor through the api-gateway.

Each simulated user submits small Python batches (``POST /api/v1/ssh/batches``)
and polls their tasks (``GET /api/v1/ssh/batches/{id}/tasks``) until the batch
finishes.

Run against a gateway that already has at least one online node registered:

    LOAD_TEST_TOKEN=$(python generate_token.py) \\
        locust -f tests/load/locustfile.py --host http://localhost:8000

Environment variables:
    LOAD_TEST_TOKEN     Bearer token (omit only when ENVIRONMENT=development).
    LOAD_TEST_NODE_IDS  Comma-separated node ids to target. Default: every
                        online node returned by GET /api/v1/ssh/nodes.
    LOAD_TEST_INPUTS    Tasks (inputs) per batch. Default: 4.
"""
import os
import random

from locust import HttpUser, between, task

BATCHES = "/api/v1/ssh/batches"
FINISHED = {"completed", "completed_with_errors", "failed", "cancelled"}

CODE = (
    "import sys\n"
    "n = int(sys.stdin.read().strip() or 0)\n"
    "print(sum(i * i for i in range(n)))\n"
)


class TaskExecutorUser(HttpUser):
    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        token = os.environ.get("LOAD_TEST_TOKEN")
        if token:
            self.client.headers["Authorization"] = f"Bearer {token}"
        self.inputs_per_batch = int(os.environ.get("LOAD_TEST_INPUTS", "4"))
        self.batch_ids: list[str] = []

        configured = os.environ.get("LOAD_TEST_NODE_IDS", "")
        self.node_ids = [n.strip() for n in configured.split(",") if n.strip()]
        if not self.node_ids:
            with self.client.get(
                "/api/v1/ssh/nodes", name="/api/v1/ssh/nodes", catch_response=True
            ) as resp:
                if resp.status_code != 200:
                    resp.failure(f"listing nodes failed: {resp.status_code}")
                else:
                    self.node_ids = [
                        n["id"] for n in resp.json().get("items", [])
                        if n.get("status") == "online"
                    ]
        if not self.node_ids:
            raise RuntimeError(
                "No online nodes: register a node or set LOAD_TEST_NODE_IDS"
            )

    @task(1)
    def submit_batch(self) -> None:
        payload = {
            "code": CODE,
            "runtime": "python",
            "inputs": [str(random.randint(1_000, 100_000)) for _ in range(self.inputs_per_batch)],
            "node_ids": self.node_ids,
        }
        with self.client.post(BATCHES, json=payload, name=BATCHES, catch_response=True) as resp:
            if resp.status_code != 201:
                resp.failure(f"submit failed: {resp.status_code} {resp.text[:200]}")
                return
            self.batch_ids.append(resp.json()["id"])

    @task(4)
    def poll_tasks(self) -> None:
        if not self.batch_ids:
            return
        batch_id = random.choice(self.batch_ids)
        with self.client.get(
            f"{BATCHES}/{batch_id}/tasks",
            name=f"{BATCHES}/[id]/tasks",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"poll failed: {resp.status_code}")
                return
            if resp.json().get("batch", {}).get("status") in FINISHED:
                self.batch_ids.remove(batch_id)
