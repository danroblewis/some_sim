"""Prometheus metrics for the todo app (served at GET /metrics).

Each app gets its own CollectorRegistry, so creating several apps (tests, restarts) never collides.

Three kinds of series:
  * HTTP:     http_requests_total, http_request_duration_seconds, http_requests_in_flight
  * Rules:    todo_rule_rejections_total{rule} - how often the server refused a request, and which
              rule did it. todo_events_total{event} - state changes that actually happened.
  * Snapshot: todos_current{state}, todo_dependency_links, todo_longest_dependency_chain,
              simulator_* - computed from the live data at scrape time, so they can never drift.
"""
import time

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

__all__ = ["Metrics", "CONTENT_TYPE_LATEST"]

# Latency buckets tuned for a small local API (5ms .. 2.5s)
BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)


class _TodoCollector:
    """Gauges computed from the store each time Prometheus scrapes."""

    def __init__(self, store):
        self.store = store

    def collect(self):
        todos = {t["id"]: t for t in self.store.list_all()}

        states = GaugeMetricFamily("todos_current", "Todos right now, by state", labels=["state"])
        counts = {"done": 0, "ready": 0, "blocked": 0}
        for t in todos.values():
            counts["done" if t["done"] else ("blocked" if t["blocked_by"] else "ready")] += 1
        for state, n in counts.items():
            states.add_metric([state], n)
        yield states

        yield GaugeMetricFamily("todo_dependency_links", "Dependency links between todos right now",
                                value=sum(len(t["depends_on"]) for t in todos.values()))

        # Longest chain of dependencies (a -> b -> c is 2). Memoised DFS; the graph is acyclic.
        depth: dict[int, int] = {}

        def d(i: int) -> int:
            if i not in depth:
                depth[i] = max((1 + d(x) for x in todos[i]["depends_on"]), default=0)
            return depth[i]

        yield GaugeMetricFamily("todo_longest_dependency_chain", "Length of the longest dependency chain",
                                value=max((d(i) for i in todos), default=0))


class _SimulatorCollector:
    def __init__(self, sim):
        self.sim = sim

    def collect(self):
        status = self.sim.status()
        yield GaugeMetricFamily("simulator_running", "1 while the traffic simulator is running",
                                value=1 if status["running"] else 0)
        c = CounterMetricFamily("simulator_requests", "Requests sent by the traffic simulator "
                                "(resets when the simulator is restarted)", labels=["action", "outcome"])
        for action, o in status["by_action"].items():
            c.add_metric([action, "ok"], o["ok"])
            c.add_metric([action, "rejected"], o["rejected"])
            c.add_metric([action, "error"], o["errors"])
        yield c


class Metrics:
    def __init__(self, store, sim, rules: tuple[str, ...], events: tuple[str, ...]):
        self.registry = r = CollectorRegistry()
        self.http_requests = Counter("http_requests_total", "HTTP requests handled",
                                     ["method", "route", "status"], registry=r)
        self.http_duration = Histogram("http_request_duration_seconds", "HTTP request latency",
                                       ["method", "route"], buckets=BUCKETS, registry=r)
        self.in_flight = Gauge("http_requests_in_flight", "HTTP requests being handled right now", registry=r)
        self.rule_rejections = Counter("todo_rule_rejections", "Requests the server refused, by the rule that refused them",
                                       ["rule"], registry=r)
        self.events = Counter("todo_events", "Todo state changes that happened", ["event"], registry=r)
        Gauge("todoapp_start_time_seconds", "Unix time the app started", registry=r).set(time.time())
        # Export every known series at 0 from the start, so rate() and alerts work before the first event.
        for rule in rules:
            self.rule_rejections.labels(rule)
        for event in events:
            self.events.labels(event)
        r.register(_TodoCollector(store))
        r.register(_SimulatorCollector(sim))

    def render(self) -> bytes:
        return generate_latest(self.registry)

    @staticmethod
    def route_label(path: str, route, status: int) -> str:
        """Low-cardinality label: the route template, never the raw path (no todo ids, no scanner junk)."""
        template = getattr(route, "path", None)
        if template:
            return template
        return "static" if status < 400 and not path.startswith("/api") else "unmatched"
