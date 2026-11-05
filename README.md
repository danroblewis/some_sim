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
  completed todo. It knows nothing about the rules, so completing a blocked todo or deleting one
  that still has dependencies is just a request the server rejects with 409 (counted as "rejected"). It's off until started: use the Start/Stop button in the page, or
  `POST /api/simulator/start?interval=1.0` / `POST /api/simulator/stop` / `GET /api/simulator`.
- **Monitor**: the page reloads the todo list and simulator stats every 2s (`POLL_MS` in
  `static/index.html`) and shows whether the server is reachable.

Tests: `pip install -r requirements-dev.txt && pytest`
