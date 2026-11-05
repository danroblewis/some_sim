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
  completed todo, and deleting a todo that has dependencies (or that others depend on).

The page contains no rules; it renders server state and shows the server's error messages.

## API

| Method | Path | Body |
| --- | --- | --- |
| GET | `/api/todos` | |
| POST | `/api/todos` | `{"text": "...", "depends_on": [ids]}` |
| PUT | `/api/todos/{id}/done` | `{"done": true}` |
| PUT | `/api/todos/{id}/dependencies` | `[ids]` |
| DELETE | `/api/todos/{id}` | |

Tests: `pip install -r requirements-dev.txt && pytest`
