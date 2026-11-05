"""Traffic simulator: a background loop that exercises the todo API over real HTTP.

Each tick it reads the list, then does ONE of:
  - create a todo, depending on 0-3 randomly chosen existing todos (50% chance of any), or
  - try to complete a randomly chosen open todo, or
  - try to delete a randomly chosen completed todo (so finished todos don't pile up).

It has no knowledge of the rules: completing a blocked todo, or deleting a todo that still has
dependencies, is a normal request that the server rejects (409). Those rejections are counted,
not hidden.
"""
import asyncio
import random
import time
from collections import defaultdict

import httpx

VERBS = ["write", "review", "deploy", "test", "fix", "plan", "refactor", "document", "ship", "audit"]
NOUNS = ["parser", "login page", "database", "report", "cache", "queue", "API", "dashboard", "build", "schema"]


class Simulator:
    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.task: asyncio.Task | None = None
        self.interval = 1.0
        self.reset_stats()

    def reset_stats(self) -> None:
        self.requests = 0
        self.ok = 0
        self.rejected = 0   # 4xx: the server refused the request
        self.errors = 0     # 5xx or the connection failed
        self.by_action: dict[str, dict[str, int]] = defaultdict(lambda: {"ok": 0, "rejected": 0, "errors": 0})
        self.last_error: str | None = None
        self.started_at: float | None = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def status(self) -> dict:
        return {
            "running": self.running,
            "interval": self.interval,
            "uptime_seconds": round(time.time() - self.started_at, 1) if self.running and self.started_at else 0,
            "requests": self.requests,
            "ok": self.ok,
            "rejected": self.rejected,
            "errors": self.errors,
            "by_action": {k: dict(v) for k, v in self.by_action.items()},
            "last_error": self.last_error,
        }

    def start(self, base_url: str, interval: float = 1.0, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if self.running:
            return
        self.interval = max(0.05, interval)
        self.reset_stats()
        self.started_at = time.time()
        self.task = asyncio.create_task(self._run(base_url, transport))

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    async def _run(self, base_url: str, transport: httpx.AsyncBaseTransport | None) -> None:
        async with httpx.AsyncClient(base_url=base_url, timeout=5, transport=transport) as client:
            while True:
                await self.tick(client)
                await asyncio.sleep(self.interval * self.rng.uniform(0.5, 1.5))

    def _record(self, action: str, status: int | None, error: str | None = None) -> None:
        self.requests += 1
        bucket = self.by_action[action]
        if status is not None and status < 400:
            self.ok += 1; bucket["ok"] += 1
        elif status is not None and status < 500:
            self.rejected += 1; bucket["rejected"] += 1
        else:
            self.errors += 1; bucket["errors"] += 1
            self.last_error = error or f"HTTP {status}"

    async def _send(self, client: httpx.AsyncClient, action: str, method: str, url: str, **kw) -> httpx.Response | None:
        try:
            r = await client.request(method, url, **kw)
        except httpx.HTTPError as e:
            self._record(action, None, f"{type(e).__name__}: {e}")
            return None
        self._record(action, r.status_code)
        return r

    async def tick(self, client: httpx.AsyncClient) -> None:
        r = await self._send(client, "list", "GET", "/api/todos")
        if r is None or r.status_code != 200:
            return
        todos = r.json()
        open_todos = [t for t in todos if not t["done"]]
        done_todos = [t for t in todos if t["done"]]
        actions = ["create"] + (["complete"] if open_todos else []) + (["delete"] if done_todos else [])
        action = self.rng.choice(actions)
        if action == "create":
            deps: list[int] = []
            if todos and self.rng.random() < 0.5:
                deps = [t["id"] for t in self.rng.sample(todos, k=min(len(todos), self.rng.randint(1, 3)))]
            text = f"{self.rng.choice(VERBS)} {self.rng.choice(NOUNS)}"
            await self._send(client, "create", "POST", "/api/todos", json={"text": text, "depends_on": deps})
        elif action == "complete":
            target = self.rng.choice(open_todos)  # may still be blocked: the server decides
            await self._send(client, "complete", "PUT", f"/api/todos/{target['id']}/done", json={"done": True})
        else:
            target = self.rng.choice(done_todos)  # may still have dependencies: the server decides
            await self._send(client, "delete", "DELETE", f"/api/todos/{target['id']}")
