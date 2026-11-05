import pytest
from fastapi.testclient import TestClient

from main import create_app


@pytest.fixture
def db(tmp_path):
    return tmp_path / "todos.db"


@pytest.fixture
def c(db):
    return TestClient(create_app(db))


def add(c, text, depends_on=()):
    r = c.post("/api/todos", json={"text": text, "depends_on": list(depends_on)})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def done(c, i, value=True):
    return c.put(f"/api/todos/{i}/done", json={"done": value})


def deps(c, i, ds):
    return c.put(f"/api/todos/{i}/dependencies", json=list(ds))


def state(c):
    return {t["id"]: t for t in c.get("/api/todos").json()}


# --- the core rule ---

def test_cannot_complete_until_dependencies_done(c):
    a, b = add(c, "a"), add(c, "b")
    deps(c, b, [a])
    r = done(c, b)
    assert r.status_code == 409 and "a" in r.json()["detail"]
    assert state(c)[b]["done"] is False
    assert done(c, a).status_code == 200
    assert done(c, b).status_code == 200


def test_dependencies_given_at_creation_are_enforced(c):
    a = add(c, "a")
    b = add(c, "b", [a])
    assert done(c, b).status_code == 409


def test_all_dependencies_must_be_done(c):
    a, b = add(c, "a"), add(c, "b")
    p = add(c, "parent", [a, b])
    done(c, a)
    assert done(c, p).status_code == 409
    done(c, b)
    assert done(c, p).status_code == 200


def test_transitive_chain(c):
    a = add(c, "a"); b = add(c, "b", [a]); d = add(c, "d", [b])
    assert done(c, d).status_code == 409
    assert done(c, b).status_code == 409
    done(c, a); done(c, b)
    assert done(c, d).status_code == 200


def test_computed_fields(c):
    a = add(c, "a"); b = add(c, "b", [a])
    s = state(c)
    assert s[a]["can_complete"] is True and s[a]["blocked_by"] == []
    assert s[b]["can_complete"] is False and s[b]["blocked_by"] == [a]
    done(c, a)
    assert state(c)[b]["can_complete"] is True


# --- nothing may leave a completed todo with an unfinished dependency ---

def test_cannot_reopen_while_completed_todo_depends_on_it(c):
    a = add(c, "a"); b = add(c, "b", [a])
    done(c, a); done(c, b)
    assert done(c, a, False).status_code == 409
    assert state(c)[a]["done"] is True
    done(c, b, False)
    assert done(c, a, False).status_code == 200


def test_cannot_add_unfinished_dependency_to_completed_todo(c):
    a, b = add(c, "a"), add(c, "b")
    done(c, b)
    assert deps(c, b, [a]).status_code == 409
    assert state(c)[b]["depends_on"] == []
    done(c, a)
    assert deps(c, b, [a]).status_code == 200  # fine if the dependency is already complete


def test_todo_with_no_dependencies_can_be_deleted_even_if_others_depend_on_it(c):
    orig = add(c, "orig")
    depped = add(c, "depped", [orig])
    assert c.delete(f"/api/todos/{orig}").status_code == 204
    s = state(c)
    assert list(s) == [depped]
    assert s[depped]["depends_on"] == []  # the link to the deleted todo is gone


def test_delete_leaf_in_a_chain(c):
    a = add(c, "a"); b = add(c, "b", [a]); d = add(c, "d", [b])
    assert c.delete(f"/api/todos/{b}").status_code == 409  # b has a dependency
    assert c.delete(f"/api/todos/{d}").status_code == 409  # d has a dependency
    assert c.delete(f"/api/todos/{a}").status_code == 204  # a has none
    assert state(c)[b]["depends_on"] == [] and state(c)[d]["depends_on"] == [b]


def test_cannot_delete_todo_that_has_dependencies(c):
    a = add(c, "a"); b = add(c, "b", [a])
    r = c.delete(f"/api/todos/{b}")
    assert r.status_code == 409 and "dependencies" in r.json()["detail"]
    assert set(state(c)) == {a, b}
    deps(c, b, [])  # once its dependencies are removed it can be deleted
    assert c.delete(f"/api/todos/{b}").status_code == 204


def test_cannot_delete_even_when_everything_is_complete(c):
    a = add(c, "a"); b = add(c, "b", [a])
    done(c, a); done(c, b)
    assert c.delete(f"/api/todos/{b}").status_code == 409  # b has a dependency
    assert c.delete(f"/api/todos/{a}").status_code == 204  # a has none


def test_removing_a_dependency_unblocks(c):
    a = add(c, "a"); b = add(c, "b", [a])
    deps(c, b, [])
    assert done(c, b).status_code == 200


# --- validation ---

def test_cycles_rejected(c):
    a = add(c, "a"); b = add(c, "b", [a]); d = add(c, "d", [b])
    assert deps(c, a, [d]).status_code == 400
    assert deps(c, a, [b]).status_code == 400
    assert deps(c, a, [a]).status_code == 400


def test_unknown_ids(c):
    a = add(c, "a")
    assert deps(c, a, [99]).status_code == 404
    assert done(c, 99).status_code == 404
    assert c.delete("/api/todos/99").status_code == 404
    assert c.post("/api/todos", json={"text": "x", "depends_on": [99]}).status_code == 404
    assert len(state(c)) == 1  # the failed create left nothing behind


def test_blank_text_rejected(c):
    assert c.post("/api/todos", json={"text": "   "}).status_code == 422


def test_duplicate_dependency_ids_collapsed(c):
    a = add(c, "a"); b = add(c, "b", [a, a])
    assert state(c)[b]["depends_on"] == [a]


# --- persistence ---

def test_survives_restart(db):
    c1 = TestClient(create_app(db))
    a = add(c1, "a"); b = add(c1, "b", [a]); done(c1, a)
    c2 = TestClient(create_app(db))  # new app + new connection = restart
    s = state(c2)
    assert s[a]["done"] and not s[b]["done"] and s[b]["depends_on"] == [a]
    assert done(c2, b).status_code == 200
    assert add(c2, "c") == 3


def test_ids_never_reused(c):
    a = add(c, "a")
    c.delete(f"/api/todos/{a}")
    assert add(c, "b") != a


def test_database_itself_refuses_bad_links(db):
    """Defense in depth: FK + CHECK constraints back up the Python rules."""
    import sqlite3
    store = create_app(db).state.store
    a = add(TestClient(create_app(db)), "a")
    with pytest.raises(sqlite3.IntegrityError):
        store.db.execute("INSERT INTO dependency VALUES (?, ?)", (a, a))
    with pytest.raises(sqlite3.IntegrityError):
        store.db.execute("INSERT INTO dependency VALUES (?, ?)", (a, 999))


# --- the positive side: when completion IS allowed ---

def test_todo_with_no_dependencies_can_be_completed(c):
    a = add(c, "a")
    r = done(c, a)
    assert r.status_code == 200 and r.json()["done"] is True
    assert state(c)[a]["done"] is True


def test_todo_can_be_completed_once_all_its_dependencies_are_complete(c):
    a, b = add(c, "a"), add(c, "b")
    p = add(c, "parent", [a, b])
    done(c, a); done(c, b)
    s = state(c)[p]
    assert s["blocked_by"] == [] and s["can_complete"] is True
    r = done(c, p)
    assert r.status_code == 200 and r.json()["done"] is True
    assert state(c)[p]["done"] is True


def test_dependency_completed_before_the_link_is_added(c):
    a = add(c, "a"); done(c, a)
    b = add(c, "b", [a])  # dependency is already complete at creation
    assert state(c)[b]["can_complete"] is True
    assert done(c, b).status_code == 200


def test_completing_is_idempotent(c):
    a = add(c, "a")
    assert done(c, a).status_code == 200
    assert done(c, a).status_code == 200
    assert state(c)[a]["done"] is True


# --- traffic simulator ---

def run_sim(db, seed, ticks):
    """Run the simulator's ticks against the app in-process, over the real ASGI stack.

    After every tick the server's state is checked against the rules; returns the simulator,
    a client, and the list size after each tick.
    """
    import asyncio, random
    import httpx
    from simulator import Simulator

    app = create_app(db)
    sim = Simulator(random.Random(seed))
    sizes, saw_dependency = [], []

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for _ in range(ticks):
                await sim.tick(client)
                s = {t["id"]: t for t in app.state.store.list_all()}
                for t in s.values():
                    if t["done"]:
                        assert all(d in s and s[d]["done"] for d in t["depends_on"])  # the rule always holds
                    assert t["id"] not in t["depends_on"]
                    assert all(d in s for d in t["depends_on"])                     # no dangling links
                sizes.append(len(s))
                saw_dependency.append(any(t["depends_on"] for t in s.values()))

    asyncio.run(go())
    return sim, TestClient(app), sizes, any(saw_dependency)


def test_simulator_traffic_never_breaks_the_rules(db):
    sim, c, sizes, saw_dependency = run_sim(db, seed=1, ticks=500)
    a = sim.by_action
    assert sim.errors == 0
    assert sim.requests == sim.ok + sim.rejected + sim.errors
    assert a["create"]["ok"] > 20 and saw_dependency          # it creates todos, some with dependencies
    assert a["complete"]["ok"] > 20                            # it completes todos
    assert a["complete"]["rejected"] > 0                       # ...and the server refuses blocked ones
    assert a["delete"]["ok"] > 20                              # it deletes completed todos


def test_simulator_deletes_more_often_than_it_creates_and_clears_the_list(db):
    sim, c, sizes, _ = run_sim(db, seed=1, ticks=500)
    assert max(sizes) < 60                                     # the list stays bounded
    assert 0 in sizes[30:]                                     # and gets cleared out now and then
    assert sim.by_action["delete"]["ok"] >= 0.8 * sim.by_action["create"]["ok"]


def test_simulator_deleting_a_todo_with_dependencies_is_refused_by_the_server(db):
    sim, c, sizes, _ = run_sim(db, seed=1, ticks=500)
    assert sim.by_action["delete"]["rejected"] > 0


def test_simulator_is_reproducible_with_a_seed(tmp_path):
    a, ca, _, _ = run_sim(tmp_path / "a.db", seed=7, ticks=60)
    b, cb, _, _ = run_sim(tmp_path / "b.db", seed=7, ticks=60)
    assert state(ca) == state(cb)


def test_simulator_endpoints(db):
    with TestClient(create_app(db)) as c:
        assert c.get("/api/simulator").json()["running"] is False
        assert c.post("/api/simulator/start?interval=0.05").json()["running"] is True
        assert c.post("/api/simulator/start").json()["running"] is True  # starting twice is harmless
        assert c.post("/api/simulator/stop").json()["running"] is False
        assert c.post("/api/simulator/stop").json()["running"] is False
