import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from main import create_app


@pytest.fixture
def c(tmp_path):
    return TestClient(create_app(tmp_path / "todos.db"))


def scrape(c):
    r = c.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    samples = {}
    for fam in text_string_to_metric_families(r.text):
        for s in fam.samples:
            samples[(s.name, tuple(sorted(s.labels.items())))] = s.value
    return samples


def val(c, name, **labels):
    return scrape(c).get((name, tuple(sorted(labels.items()))))


def add(c, text, deps=()):
    return c.post("/api/todos", json={"text": text, "depends_on": list(deps)}).json()["id"]


def done(c, i, v=True):
    return c.put(f"/api/todos/{i}/done", json={"done": v})


def test_every_series_is_exported_at_zero_from_the_start(c):
    m = scrape(c)
    for rule in ("complete_blocked", "reopen_with_completed_dependents", "open_dependency_on_completed",
                 "delete_has_dependencies", "dependency_cycle", "self_dependency"):
        assert m[("todo_rule_rejections_total", (("rule", rule),))] == 0
    for event in ("created", "completed", "reopened", "deleted", "dependencies_changed"):
        assert m[("todo_events_total", (("event", event),))] == 0
    assert m[("todos_current", (("state", "ready"),))] == 0
    assert m[("simulator_running", ())] == 0


def test_http_metrics_use_route_templates_not_raw_paths(c):
    a = add(c, "a")
    done(c, a)
    done(c, 9999)  # 404
    c.get("/")  # static page
    c.get("/api/nope/123")  # unmatched
    m = scrape(c)
    assert m[("http_requests_total", (("method", "POST"), ("route", "/api/todos"), ("status", "201")))] == 1
    assert m[("http_requests_total", (("method", "PUT"), ("route", "/api/todos/{todo_id}/done"), ("status", "200")))] == 1
    assert m[("http_requests_total", (("method", "PUT"), ("route", "/api/todos/{todo_id}/done"), ("status", "404")))] == 1
    assert m[("http_requests_total", (("method", "GET"), ("route", "static"), ("status", "200")))] == 1
    assert m[("http_requests_total", (("method", "GET"), ("route", "unmatched"), ("status", "404")))] == 1
    assert not any("9999" in str(k) or "/123" in str(k) for k in m)  # no ids leaked into labels
    # latency histogram recorded the same requests
    assert m[("http_request_duration_seconds_count", (("method", "POST"), ("route", "/api/todos")))] == 1
    assert m[("http_requests_in_flight", ())] == 0


def test_scrapes_do_not_count_as_app_traffic(c):
    scrape(c); scrape(c); scrape(c)
    routes = {dict(labels).get("route") for (name, labels) in scrape(c) if name == "http_requests_total"}
    assert routes == set()  # nothing but scrapes happened, and scrapes aren't counted


def test_each_rule_rejection_is_counted_under_its_rule(c):
    a = add(c, "a"); b = add(c, "b", [a])
    assert done(c, b).status_code == 409                                              # complete_blocked
    assert c.delete(f"/api/todos/{b}").status_code == 409                             # delete_has_dependencies
    assert c.put(f"/api/todos/{a}/dependencies", json=[b]).status_code == 400         # dependency_cycle
    assert c.put(f"/api/todos/{a}/dependencies", json=[a]).status_code == 400         # self_dependency
    done(c, a); done(c, b)
    assert done(c, a, False).status_code == 409                                       # reopen_with_completed_dependents
    x = add(c, "x"); done(c, x)
    assert c.put(f"/api/todos/{x}/dependencies", json=[add(c, "y")]).status_code == 409  # open_dependency_on_completed
    for rule in ("complete_blocked", "delete_has_dependencies", "dependency_cycle", "self_dependency",
                 "reopen_with_completed_dependents", "open_dependency_on_completed"):
        assert val(c, "todo_rule_rejections_total", rule=rule) == 1, rule


def test_404s_and_validation_errors_are_not_rule_rejections(c):
    done(c, 99)
    c.post("/api/todos", json={"text": "   "})
    m = scrape(c)
    assert sum(v for (n, _), v in m.items() if n == "todo_rule_rejections_total") == 0


def test_events_count_real_state_changes_only(c):
    a = add(c, "a"); b = add(c, "b")
    done(c, a); done(c, a)                 # second is a no-op: must not count twice
    done(c, a, False); done(c, a, False)   # likewise
    c.put(f"/api/todos/{b}/dependencies", json=[a])
    done(c, b)                             # rejected: must not count
    c.put(f"/api/todos/{b}/dependencies", json=[])
    c.delete(f"/api/todos/{a}")
    m = scrape(c)
    assert m[("todo_events_total", (("event", "created"),))] == 2
    assert m[("todo_events_total", (("event", "completed"),))] == 1
    assert m[("todo_events_total", (("event", "reopened"),))] == 1
    assert m[("todo_events_total", (("event", "dependencies_changed"),))] == 2
    assert m[("todo_events_total", (("event", "deleted"),))] == 1


def test_snapshot_gauges_reflect_live_state(c):
    a = add(c, "a"); b = add(c, "b", [a]); d = add(c, "d", [b]); e = add(c, "e")
    assert val(c, "todos_current", state="ready") == 2      # a, e
    assert val(c, "todos_current", state="blocked") == 2    # b, d
    assert val(c, "todos_current", state="done") == 0
    assert val(c, "todo_dependency_links") == 2
    assert val(c, "todo_longest_dependency_chain") == 2     # d -> b -> a
    done(c, a); done(c, e)
    assert val(c, "todos_current", state="done") == 2
    assert val(c, "todos_current", state="ready") == 1      # b is unblocked now
    assert val(c, "todos_current", state="blocked") == 1    # d still waits on b
    c.delete(f"/api/todos/{e}")
    assert val(c, "todos_current", state="done") == 1


def test_snapshot_gauges_survive_restart_but_counters_start_over(tmp_path):
    db = tmp_path / "t.db"
    c1 = TestClient(create_app(db))
    a = add(c1, "a"); add(c1, "b", [a])
    c2 = TestClient(create_app(db))  # restart
    assert val(c2, "todos_current", state="blocked") == 1
    assert val(c2, "todo_events_total", event="created") == 0


def test_simulator_metrics(c):
    import asyncio, random
    import httpx
    from simulator import Simulator
    app = c.app
    sim = app.state.sim
    sim.rng = random.Random(1)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            for _ in range(100):
                await sim.tick(client)
    asyncio.run(go())
    m = scrape(c)
    assert m[("simulator_requests_total", (("action", "create"), ("outcome", "ok")))] == sim.by_action["create"]["ok"] > 0
    assert m[("simulator_requests_total", (("action", "complete"), ("outcome", "rejected")))] == sim.by_action["complete"]["rejected"] > 0
    # the simulator's traffic is visible in the same registry as ordinary HTTP + rule metrics
    assert m[("todo_rule_rejections_total", (("rule", "complete_blocked"),))] == sim.by_action["complete"]["rejected"]
