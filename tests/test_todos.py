import importlib
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TODOS_FILE", str(tmp_path / "todos.json"))
    import main
    importlib.reload(main)
    return main, TestClient(main.app), tmp_path / "todos.json"


def add(c, text):
    return c.post("/api/todos", json={"text": text}).json()["id"]


def deps(c, i, d):
    return c.put(f"/api/todos/{i}/dependencies", json=d)


def toggle(c, i):
    return c.post(f"/api/todos/{i}/toggle")


def state(c):
    return {t["id"]: t for t in c.get("/api/todos").json()}


def test_cannot_complete_with_unfinished_dependency(env):
    _, c, _ = env
    a, b = add(c, "a"), add(c, "b")
    deps(c, b, [a])
    assert toggle(c, b).status_code == 409
    assert not state(c)[b]["done"]
    assert toggle(c, a).status_code == 200
    assert toggle(c, b).status_code == 200


def test_adding_unfinished_dep_to_done_todo_reopens_it(env):
    _, c, _ = env
    a, b = add(c, "a"), add(c, "b")
    toggle(c, b)
    deps(c, b, [a])
    assert not state(c)[b]["done"]


def test_uncompleting_dependency_reopens_dependents_transitively(env):
    _, c, _ = env
    a, b, d = add(c, "a"), add(c, "b"), add(c, "d")
    deps(c, b, [a]); deps(c, d, [b])
    for i in (a, b, d):
        assert toggle(c, i).status_code == 200
    toggle(c, a)
    assert not any(t["done"] for t in state(c).values())


def test_cycles_rejected(env):
    _, c, _ = env
    a, b = add(c, "a"), add(c, "b")
    deps(c, b, [a])
    assert deps(c, a, [b]).status_code == 400
    assert deps(c, a, [a]).status_code == 400


def test_unknown_ids(env):
    _, c, _ = env
    a = add(c, "a")
    assert deps(c, a, [99]).status_code == 404
    assert toggle(c, 99).status_code == 404


def test_persistence_across_restart(env):
    main, c, path = env
    a, b = add(c, "a"), add(c, "b")
    deps(c, b, [a]); toggle(c, a)
    importlib.reload(main)  # simulates a server restart
    c2 = TestClient(main.app)
    s = state(c2)
    assert s[a]["done"] and not s[b]["done"] and s[b]["depends_on"] == [a]
    assert add(c2, "c") == 3  # ids keep increasing


def test_ids_not_reused_after_delete(env):
    _, c, _ = env
    a = add(c, "a")
    c.delete(f"/api/todos/{a}")
    assert add(c, "b") != a


def test_delete_removes_dependency_links(env):
    _, c, _ = env
    a, b = add(c, "a"), add(c, "b")
    deps(c, b, [a])
    c.delete(f"/api/todos/{a}")
    assert state(c)[b]["depends_on"] == []


def test_invalid_data_file_is_repaired_on_load(env):
    main, _, path = env
    path.write_text(json.dumps({"next_id": 3, "todos": [
        {"id": 1, "text": "a", "done": False, "depends_on": []},
        {"id": 2, "text": "b", "done": True, "depends_on": [1, 77]},
    ]}))
    importlib.reload(main)
    s = state(TestClient(main.app))
    assert s[2]["done"] is False and s[2]["depends_on"] == [1]
