"""Todo app with dependencies.

ALL business rules live in `Store` below. The HTML page is a dumb view: it renders what the
server returns and shows the server's error messages. The rules:

  1. A todo can only be completed when every todo it depends on is completed.
  2. Dependencies can't form a cycle (and a todo can't depend on itself).
  3. The server never silently changes a todo you didn't ask about, so any action that would
     leave a completed todo with an unfinished dependency is refused with a 409:
       - re-opening a todo while a completed todo depends on it
       - adding an unfinished dependency to a completed todo
       - deleting a todo that has dependencies (a todo with none can always be deleted;
         links other todos had to it are removed along with it)
"""
import os
import sqlite3
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

DEFAULT_DB = Path(os.environ.get("TODOS_DB", Path(__file__).parent / "data" / "todos.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS todo (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT    NOT NULL CHECK (length(text) > 0),
    done INTEGER NOT NULL DEFAULT 0 CHECK (done IN (0, 1))
);
CREATE TABLE IF NOT EXISTS dependency (
    todo_id       INTEGER NOT NULL REFERENCES todo(id) ON DELETE CASCADE,
    depends_on_id INTEGER NOT NULL REFERENCES todo(id) ON DELETE CASCADE,
    PRIMARY KEY (todo_id, depends_on_id),
    CHECK (todo_id != depends_on_id)
);
"""


class NotFound(Exception): ...
class Invalid(Exception): ...   # bad request (400)
class Conflict(Exception): ...  # request is well-formed but violates a rule (409)


class Store:
    """The only place where the dependency rules are implemented."""

    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()  # one operation at a time

    # ---- reads ----

    def _snapshot(self) -> tuple[dict[int, dict], dict[int, list[int]]]:
        todos = {r["id"]: dict(r) for r in self.db.execute("SELECT id, text, done FROM todo ORDER BY id")}
        deps: dict[int, list[int]] = {i: [] for i in todos}
        for r in self.db.execute("SELECT todo_id, depends_on_id FROM dependency ORDER BY depends_on_id"):
            deps[r["todo_id"]].append(r["depends_on_id"])
        return todos, deps

    def _view(self, t: dict, deps: dict[int, list[int]], todos: dict[int, dict]) -> dict:
        blocked_by = [d for d in deps[t["id"]] if not todos[d]["done"]]
        return {
            "id": t["id"],
            "text": t["text"],
            "done": bool(t["done"]),
            "depends_on": deps[t["id"]],
            "blocked_by": blocked_by,          # unfinished dependencies
            "can_complete": not t["done"] and not blocked_by,
        }

    def list_all(self) -> list[dict]:
        with self.lock:
            todos, deps = self._snapshot()
            return [self._view(t, deps, todos) for t in todos.values()]

    def get(self, todo_id: int) -> dict:
        with self.lock:
            todos, deps = self._snapshot()
            if todo_id not in todos:
                raise NotFound(f"Todo {todo_id} not found")
            return self._view(todos[todo_id], deps, todos)

    # ---- writes ----

    def create(self, text: str, depends_on: list[int]) -> dict:
        with self.lock:
            todos, deps = self._snapshot()
            depends_on = list(dict.fromkeys(depends_on))
            for d in depends_on:
                if d not in todos:
                    raise NotFound(f"Dependency {d} not found")
            self.db.execute("BEGIN")
            try:
                new_id = self.db.execute("INSERT INTO todo (text) VALUES (?)", (text,)).lastrowid
                self.db.executemany(
                    "INSERT INTO dependency (todo_id, depends_on_id) VALUES (?, ?)",
                    [(new_id, d) for d in depends_on],
                )
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
            return self.get(new_id)

    def set_done(self, todo_id: int, done: bool) -> dict:
        with self.lock:
            todos, deps = self._snapshot()
            if todo_id not in todos:
                raise NotFound(f"Todo {todo_id} not found")
            if done:
                pending = [d for d in deps[todo_id] if not todos[d]["done"]]
                if pending:
                    raise Conflict(f"Can't complete: dependencies not done yet: {self._names(todos, pending)}")
            else:
                reliant = [t for t, ds in deps.items() if todo_id in ds and todos[t]["done"]]
                if reliant:
                    raise Conflict(
                        f"Can't re-open: completed todos depend on it: {self._names(todos, reliant)}. "
                        "Re-open those first."
                    )
            self.db.execute("UPDATE todo SET done = ? WHERE id = ?", (int(done), todo_id))
            return self.get(todo_id)

    def set_dependencies(self, todo_id: int, new_deps: list[int]) -> dict:
        with self.lock:
            todos, deps = self._snapshot()
            if todo_id not in todos:
                raise NotFound(f"Todo {todo_id} not found")
            new_deps = list(dict.fromkeys(new_deps))
            for d in new_deps:
                if d not in todos:
                    raise NotFound(f"Dependency {d} not found")
                if d == todo_id:
                    raise Invalid("A todo can't depend on itself")
                if self._reaches(deps, d, todo_id):
                    raise Invalid(f"Dependency on '{todos[d]['text']}' would create a cycle")
            if todos[todo_id]["done"]:
                pending = [d for d in new_deps if not todos[d]["done"]]
                if pending:
                    raise Conflict(
                        f"Can't add unfinished dependencies to a completed todo: {self._names(todos, pending)}. "
                        "Re-open it first."
                    )
            self.db.execute("BEGIN")
            try:
                self.db.execute("DELETE FROM dependency WHERE todo_id = ?", (todo_id,))
                self.db.executemany(
                    "INSERT INTO dependency (todo_id, depends_on_id) VALUES (?, ?)",
                    [(todo_id, d) for d in new_deps],
                )
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
            return self.get(todo_id)

    def delete(self, todo_id: int) -> None:
        with self.lock:
            todos, deps = self._snapshot()
            if todo_id not in todos:
                raise NotFound(f"Todo {todo_id} not found")
            if deps[todo_id]:
                raise Conflict(
                    f"Can't delete: it has dependencies: {self._names(todos, deps[todo_id])}. "
                    "Remove its dependencies first."
                )
            # A todo with no dependencies is always deletable. Links that other todos had
            # *to* it go away with it (it no longer exists, so it can't be an open dependency).
            self.db.execute("BEGIN")
            try:
                self.db.execute("DELETE FROM dependency WHERE depends_on_id = ?", (todo_id,))
                self.db.execute("DELETE FROM todo WHERE id = ?", (todo_id,))
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
            return

    # ---- helpers ----

    @staticmethod
    def _names(todos: dict[int, dict], ids: list[int]) -> str:
        return ", ".join(f"'{todos[i]['text']}' (#{i})" for i in ids)

    @staticmethod
    def _reaches(deps: dict[int, list[int]], start: int, target: int) -> bool:
        """Is `target` reachable from `start` by following dependencies?"""
        stack, seen = [start], set()
        while stack:
            cur = stack.pop()
            if cur == target:
                return True
            if cur not in seen:
                seen.add(cur)
                stack.extend(deps[cur])
        return False


# ---------- HTTP layer: no rules here, just translation ----------

class TodoView(BaseModel):
    id: int
    text: str
    done: bool
    depends_on: list[int]
    blocked_by: list[int]
    can_complete: bool


class NewTodo(BaseModel):
    text: str = Field(max_length=500)
    depends_on: list[int] = Field(default_factory=list)

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("text must not be blank")
        return v


class DoneBody(BaseModel):
    done: bool


def create_app(db_path: Path | str = DEFAULT_DB) -> FastAPI:
    store = Store(db_path)
    app = FastAPI(title="Todos with dependencies")
    app.state.store = store

    @app.exception_handler(NotFound)
    async def _nf(_, e): return _error(404, e)

    @app.exception_handler(Invalid)
    async def _inv(_, e): return _error(400, e)

    @app.exception_handler(Conflict)
    async def _conf(_, e): return _error(409, e)

    @app.get("/api/todos")
    def list_todos() -> list[TodoView]:
        return store.list_all()

    @app.post("/api/todos", status_code=201)
    def create_todo(body: NewTodo) -> TodoView:
        return store.create(body.text, body.depends_on)

    @app.put("/api/todos/{todo_id}/done")
    def set_done(todo_id: int, body: DoneBody) -> TodoView:
        return store.set_done(todo_id, body.done)

    @app.put("/api/todos/{todo_id}/dependencies")
    def set_dependencies(todo_id: int, depends_on: list[int]) -> TodoView:
        return store.set_dependencies(todo_id, depends_on)

    @app.delete("/api/todos/{todo_id}", status_code=204)
    def delete_todo(todo_id: int) -> None:
        store.delete(todo_id)

    app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
    return app


def _error(status: int, e: Exception):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=status, content={"detail": str(e)})


app = create_app()
