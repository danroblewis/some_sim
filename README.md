# Todos with dependencies

FastAPI + a single HTML page (React from a CDN, no build step). Todos are stored in SQLite
(`data/todos.db`, override with `TODOS_DB`) so they survive restarts.

```sh
pip install -r requirements.txt
uvicorn main:app --port 8000      # http://localhost:8000
```

## Rules (all enforced in `main.py`, in `Store`)

- A todo can't be completed until everything it depends on is complete (409 otherwise).
- Dependencies can't form cycles or point at the todo itself (400).
- The server never silently changes other todos, so these are refused with a 409:
  re-opening a todo that a completed todo depends on, adding an unfinished dependency to a
  completed todo, and deleting a todo that has dependencies. A todo with no dependencies can always be deleted;
  links other todos had to it are removed with it.

The page contains no rules; it renders server state and shows the server's error messages.

## API

| Method | Path | Body |
| --- | --- | --- |
| GET | `/api/todos` | |
| POST | `/api/todos` | `{"text": "...", "depends_on": [ids]}` |
| PUT | `/api/todos/{id}/done` | `{"done": true}` |
| PUT | `/api/todos/{id}/dependencies` | `[ids]` |
| DELETE | `/api/todos/{id}` | |

## Traffic simulator and monitor

- **Simulator** (`simulator.py`): a loop inside the server that sends real HTTP requests to this
  app's own API. Each tick it lists the todos, then either creates a todo (50% chance it depends on
  1-3 random existing todos) , tries to complete a random open todo, or tries to delete a random
  completed todo. Deletes are weighted above creates (`WEIGHTS` in `simulator.py`, create:complete:delete = 1:3:3)
  so the list stays small and gets cleared out now and then. It knows nothing about the rules, so completing a blocked todo or deleting one
  that still has dependencies is just a request the server rejects with 409 (counted as "rejected"). It's off until started: use the Start/Stop button in the page, or
  `POST /api/simulator/start?interval=1.0` / `POST /api/simulator/stop` / `GET /api/simulator`.
- **Monitor**: the page reloads the todo list and simulator stats every 2s (`POLL_MS` in
  `static/index.html`) and shows whether the server is reachable.

## Metrics (`GET /metrics`, Prometheus text format)

| Metric | What it tells you |
| --- | --- |
| `http_requests_total{method,route,status}` | Traffic. `route` is the template (`/api/todos/{todo_id}`), never a raw path. Non-API files are `static`, anything unknown is `unmatched`. |
| `http_request_duration_seconds` (histogram) | Latency per method and route. |
| `http_requests_in_flight` | Requests being handled right now. |
| `todo_rule_rejections_total{rule}` | How often the server refused a request, by the rule that refused it: `complete_blocked`, `delete_has_dependencies`, `reopen_with_completed_dependents`, `open_dependency_on_completed`, `dependency_cycle`, `self_dependency`. |
| `todo_events_total{event}` | State changes that really happened: `created`, `completed`, `reopened`, `deleted`, `dependencies_changed`. |
| `todos_current{state}` | Todos right now: `ready`, `blocked`, `done`. |
| `todo_dependency_links`, `todo_longest_dependency_chain` | Size and depth of the dependency graph. |
| `simulator_running`, `simulator_requests_total{action,outcome}` | The traffic simulator (outcome is `ok`, `rejected` for 4xx, or `error`). Resets when the simulator is restarted. |
| `todoapp_start_time_seconds` | Process start time (use it to detect restarts). |

Counters start at 0 on every server start; the `todos_*` gauges are computed from the database
at scrape time, so they are correct right after a restart. Scrapes of `/metrics` are not counted.

```yaml
# prometheus.yml
scrape_configs:
  - job_name: todos
    static_configs: [{targets: ["localhost:8000"]}]
```

Handy queries: `sum by (route, status) (rate(http_requests_total[1m]))`,
`histogram_quantile(0.95, sum by (le, route) (rate(http_request_duration_seconds_bucket[5m])))`,
`sum by (rule) (rate(todo_rule_rejections_total[5m]))`, `todos_current`.

Tests: `pip install -r requirements-dev.txt && pytest`
