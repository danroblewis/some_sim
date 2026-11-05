# some_sim

A small FastAPI app. Python serves a JSON API under `/api` and plain HTML pages from `static/`
(the page uses React from a CDN, so there's no build step).

```sh
pip install -r requirements.txt
uvicorn main:app --reload --port 8000   # http://localhost:8000
```

Todos are saved to `data/todos.json` (override with the `TODOS_FILE` env var), so they survive restarts.
The server enforces the dependency rules itself: a todo can't be completed while a dependency is unfinished.

Tests: `pip install -r requirements-dev.txt && pytest`
