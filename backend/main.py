from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="some_sim")


class Todo(BaseModel):
    id: int
    text: str
    done: bool = False


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


@app.post("/api/todos/{todo_id}/toggle")
def toggle_todo(todo_id: int) -> Todo:
    todos[todo_id].done = not todos[todo_id].done
    return todos[todo_id]


@app.delete("/api/todos/{todo_id}")
def delete_todo(todo_id: int) -> dict:
    todos.pop(todo_id, None)
    return {"ok": True}


# Serve the built React app (run `npm run build` in frontend/ first)
dist = Path(__file__).parent.parent / "frontend" / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=dist, html=True), name="static")
