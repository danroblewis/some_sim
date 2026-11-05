import json
import os
import tempfile
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

app = FastAPI(title="some_sim")

DATA_FILE = Path(os.environ.get("TODOS_FILE", Path(__file__).parent / "data" / "todos.json"))
lock = threading.RLock()  # endpoints run in a thread pool; serialize all state changes


class Todo(BaseModel):
    id: int
    text: str
    done: bool = False
    depends_on: list[int] = Field(default_factory=list)


class TodoIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


todos: dict[int, Todo] = {}
next_id = 1


# ---------- invariants (enforced by the server, never by the client) ----------

def enforce_invariants() -> None:
    """Drop dangling deps and un-complete any todo that is done while a dependency isn't.

    Runs after every change and on load, so no sequence of requests (or a hand-edited
    data file) can leave a completed todo with an unfinished dependency.
    """
    for t in todos.values():
        t.depends_on = [d for d in dict.fromkeys(t.depends_on) if d in todos and d != t.id]
    changed = True
    while changed:
        changed = False
        for t in todos.values():
            if t.done and any(not todos[d].done for d in t.depends_on):
                t.done = False
                changed = True


def reaches(start: int, target: int) -> bool:
    """True if `target` is reachable from `start` by following dependencies."""
    stack, seen = [start], set()
    while stack:
        cur = stack.pop()
        if cur == target:
            return True
        if cur not in seen:
            seen.add(cur)
            stack.extend(todos[cur].depends_on)
    return False


# ---------- persistence ----------

def save() -> None:
    enforce_invariants()
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"next_id": next_id, "todos": [t.model_dump() for t in todos.values()]}
    fd, tmp = tempfile.mkstemp(dir=DATA_FILE.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, DATA_FILE)  # atomic: a crash never leaves a half-written file


def load() -> None:
    global next_id
    todos.clear()
    next_id = 1
    if DATA_FILE.exists():
        data = json.loads(DATA_FILE.read_text())
        for raw in data["todos"]:
            todos[raw["id"]] = Todo(**raw)
        next_id = max(data.get("next_id", 1), max(todos, default=0) + 1)
    enforce_invariants()


load()


# ---------- API ----------

def get_todo(todo_id: int) -> Todo:
    if todo_id not in todos:
        raise HTTPException(404, f"Todo {todo_id} not found")
    return todos[todo_id]


@app.get("/api/todos")
def list_todos() -> list[Todo]:
    with lock:
        return list(todos.values())


@app.post("/api/todos")
def add_todo(body: TodoIn) -> Todo:
    global next_id
    with lock:
        todo = Todo(id=next_id, text=body.text.strip())
        todos[next_id] = todo
        next_id += 1
        save()
        return todo


@app.post("/api/todos/{todo_id}/toggle")
def toggle_todo(todo_id: int) -> Todo:
    with lock:
        todo = get_todo(todo_id)
        if not todo.done:
            pending = [d for d in todo.depends_on if not todos[d].done]
            if pending:
                raise HTTPException(409, f"Complete dependencies first: {pending}")
            todo.done = True
        else:
            todo.done = False  # dependents are un-completed by enforce_invariants()
        save()
        return todo


@app.put("/api/todos/{todo_id}/dependencies")
def set_dependencies(todo_id: int, deps: list[int]) -> Todo:
    with lock:
        todo = get_todo(todo_id)
        deps = list(dict.fromkeys(deps))
        for d in deps:
            get_todo(d)
            if reaches(d, todo_id):  # also catches d == todo_id
                raise HTTPException(400, f"Dependency {d} would create a cycle")
        todo.depends_on = deps
        save()  # un-completes this todo (and its dependents) if it now has an unfinished dep
        return todo


@app.delete("/api/todos/{todo_id}")
def delete_todo(todo_id: int) -> dict:
    with lock:
        todos.pop(todo_id, None)
        save()
        return {"ok": True}


# Plain HTML pages (React loaded from a CDN, no build step)
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
