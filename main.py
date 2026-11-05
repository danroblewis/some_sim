from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="some_sim")


class Todo(BaseModel):
    id: int
    text: str
    done: bool = False
    depends_on: list[int] = []


class TodoIn(BaseModel):
    text: str


todos: dict[int, Todo] = {}
next_id = 1


@app.get("/api/todos")
def list_todos() -> list[Todo]:
    return list(todos.values())


@app.post("/api/todos")
def add_todo(body: TodoIn) -> Todo:
    global next_id
    todo = Todo(id=next_id, text=body.text)
    todos[next_id] = todo
    next_id += 1
    return todo


def get_todo(todo_id: int) -> Todo:
    if todo_id not in todos:
        raise HTTPException(404, f"Todo {todo_id} not found")
    return todos[todo_id]


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


def uncomplete_dependents(todo_id: int) -> None:
    """If a todo becomes incomplete, everything that depends on it must too."""
    for t in todos.values():
        if todo_id in t.depends_on and t.done:
            t.done = False
            uncomplete_dependents(t.id)


@app.post("/api/todos/{todo_id}/toggle")
def toggle_todo(todo_id: int) -> Todo:
    todo = get_todo(todo_id)
    if not todo.done:
        pending = [d for d in todo.depends_on if not todos[d].done]
        if pending:
            raise HTTPException(409, f"Complete dependencies first: {pending}")
        todo.done = True
    else:
        todo.done = False
        uncomplete_dependents(todo_id)
    return todo


@app.put("/api/todos/{todo_id}/dependencies")
def set_dependencies(todo_id: int, deps: list[int]) -> Todo:
    todo = get_todo(todo_id)
    deps = list(dict.fromkeys(deps))
    for d in deps:
        get_todo(d)
        if reaches(d, todo_id):  # also catches d == todo_id
            raise HTTPException(400, f"Dependency {d} would create a cycle")
    todo.depends_on = deps
    if todo.done and any(not todos[d].done for d in deps):
        todo.done = False
        uncomplete_dependents(todo_id)
    return todo


@app.delete("/api/todos/{todo_id}")
def delete_todo(todo_id: int) -> dict:
    todos.pop(todo_id, None)
    for t in todos.values():
        if todo_id in t.depends_on:
            t.depends_on.remove(todo_id)
    return {"ok": True}


# Plain HTML pages (React loaded from a CDN, no build step)
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
