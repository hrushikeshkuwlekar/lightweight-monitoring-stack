"""One-click scenarios.

"learn" scenarios each demonstrate one performance-testing concept and say what
to watch; "classic" scenarios are the usual test shapes. Response-time notes
assume the demo app's endpoints: /api/order ≈ 15 ms, /api/slow?delay=D ≈ D + 0.15 s.
"""

_SLOW = {"GET /api/slow?delay=0.2": 100}   # ≈ 0.35 s per request: keeps TPS laptop-friendly
_MIXED = {
    "GET /api/order?tier=gold": 50,
    "GET /api/order?tier=silver": 20,
    "GET /api/order?tier=bronze": 10,
    "GET /api/slow?delay=0.6": 12,
    "GET /api/error": 8,
}

SCENARIOS = {
    # ------------------------------------------------------------------ learn
    "steady-state": {
        "group": "learn", "icon": "🟢", "name": "Healthy baseline",
        "concept": "Healthy system",
        "lesson": "A steady 10 TPS the app handles easily: TPS matches the target and response time stays flat.",
        "watch": "In-flight ≈ TPS × response time — Little's Law holding live.",
        "config": {"mode": "fixed_rate", "target_tps": 10, "max_users": 30, "duration_seconds": 0,
                   "ramp_up_seconds": 5, "traffic_mix": _MIXED, "app_worker_slots": 0},
    },
    "scale-users": {
        "group": "learn", "icon": "📈", "name": "TPS scales with users",
        "concept": "Healthy scalability",
        "lesson": "Fixed Users mode, adding users from 1 to 12 over a minute. While the app has spare capacity, every user adds the same TPS.",
        "watch": "TPS climbs in a straight line while response time stays flat (TPS = users ÷ response time).",
        "config": {"mode": "fixed_users", "virtual_users": 12, "think_time_ms": 0, "duration_seconds": 90,
                   "ramp_up_seconds": 60, "traffic_mix": _SLOW, "app_worker_slots": 0},
    },
    "think-time": {
        "group": "learn", "icon": "☕", "name": "Think time",
        "concept": "Workload modelling",
        "lesson": "10 users who each pause 1 s between requests, like real people reading a page. They produce only ≈ 7 TPS.",
        "watch": "TPS ≈ 10 ÷ (0.35 s response + 1 s think). Lower the think time live and watch TPS jump.",
        "config": {"mode": "fixed_users", "virtual_users": 10, "think_time_ms": 1000, "duration_seconds": 90,
                   "ramp_up_seconds": 5, "traffic_mix": _SLOW, "app_worker_slots": 0},
    },
    "too-few-users": {
        "group": "learn", "icon": "🧮", "name": "Not enough users",
        "concept": "Little's Law",
        "lesson": "Fixed Rate asks for 30 TPS of ≈ 0.45 s requests but allows only 6 users. Little's Law needs 30 × 0.45 ≈ 14 in flight.",
        "watch": "TPS stalls near 6 ÷ 0.45 ≈ 13 and skipped requests climb. Raise max users above 14 live to fix it.",
        "config": {"mode": "fixed_rate", "target_tps": 30, "max_users": 6, "duration_seconds": 90,
                   "ramp_up_seconds": 5, "traffic_mix": {"GET /api/slow?delay=0.3": 100}, "app_worker_slots": 0},
    },
    "app-saturation": {
        "group": "learn", "icon": "🧱", "name": "App bottleneck",
        "concept": "Application bottleneck",
        "lesson": "The app is limited to 4 worker slots (≈ 11 TPS max). Users ramp from 1 to 24: beyond 4 users requests just queue.",
        "watch": "TPS flattens at ≈ 11 while response time climbs — more users add waiting, not throughput.",
        "config": {"mode": "fixed_users", "virtual_users": 24, "think_time_ms": 0, "duration_seconds": 90,
                   "ramp_up_seconds": 60, "traffic_mix": _SLOW, "app_worker_slots": 4},
    },
    "generator-overload": {
        "group": "learn", "icon": "🥵", "name": "Load generator overload",
        "concept": "Load generator choke",
        "lesson": "30 TPS of fast requests, but the test script does 10 ms of heavy work per response (parsing, checks). "
                  "30 × 10 ms = 0.3 CPU cores — more than the generator's 0.2-core limit in Kubernetes. The app stays nearly idle.",
        "watch": "Generator CPU hits ~100% and runs late: TPS falls short and response times look worse than they really are. "
                 "Set script work to 0 live and everything recovers — the app was never the problem.",
        "config": {"mode": "fixed_rate", "target_tps": 30, "max_users": 100, "duration_seconds": 90,
                   "ramp_up_seconds": 10, "script_cpu_ms": 10, "traffic_mix": {"GET /api/order?tier=gold": 100},
                   "app_worker_slots": 0},
    },
    # ---------------------------------------------------------------- classic
    "ramp-up": {
        "group": "classic", "icon": "📶", "name": "Ramp-up stress test",
        "concept": "Find the limit",
        "lesson": "0 → 50 TPS over 2 minutes, hold 3 minutes, ramp down 1 minute.",
        "watch": "The point where response time starts rising is the system's knee.",
        "config": {"mode": "fixed_rate", "target_tps": 50, "max_users": 60, "duration_seconds": 360,
                   "ramp_up_seconds": 120, "ramp_down_seconds": 60, "traffic_mix": _MIXED},
    },
    "spike-test": {
        "group": "classic", "icon": "⚡", "name": "Traffic spike",
        "concept": "Sudden burst",
        "lesson": "Jumps to 100 TPS in 3 seconds and holds for 2 minutes.",
        "watch": "How fast response time recovers, and whether the generator itself keeps up.",
        "config": {"mode": "fixed_rate", "target_tps": 100, "max_users": 100, "duration_seconds": 120,
                   "ramp_up_seconds": 3, "ramp_down_seconds": 5, "traffic_mix": _MIXED},
    },
    "error-storm": {
        "group": "classic", "icon": "🔴", "name": "Error storm",
        "concept": "Alerting",
        "lesson": "90% of requests hit /api/error (intentional 500s) at 20 TPS.",
        "watch": "The HighErrorRate alert fires in Prometheus. These are intentional errors, not failures.",
        "config": {"mode": "fixed_rate", "target_tps": 20, "max_users": 20, "duration_seconds": 90,
                   "ramp_up_seconds": 5, "traffic_mix": {"GET /api/error": 90, "GET /api/order?tier=gold": 10}},
    },
    "latency-soak": {
        "group": "classic", "icon": "🐢", "name": "Latency soak",
        "concept": "Alerting",
        "lesson": "70% slow requests at 15 TPS for 5 minutes.",
        "watch": "The p95 LatencySpike alert fires; in-flight rises to ≈ 15 × response time.",
        "config": {"mode": "fixed_rate", "target_tps": 15, "max_users": 40, "duration_seconds": 300,
                   "ramp_up_seconds": 10, "ramp_down_seconds": 10, "timeout_seconds": 10,
                   "traffic_mix": {"GET /api/slow?delay=1.2": 40, "GET /api/slow?delay=0.8": 30,
                                   "GET /api/order?tier=gold": 20, "GET /api/error": 10}},
    },
    "chaos": {
        "group": "classic", "icon": "🎲", "name": "Chaos",
        "concept": "Unpredictable load",
        "lesson": "Target TPS jumps to a random 20–100% of 60 every 5 seconds, with a noisy mix.",
        "watch": "How dashboards and alerts behave under irregular traffic.",
        "config": {"mode": "fixed_rate", "target_tps": 60, "max_users": 60, "duration_seconds": 180,
                   "ramp_up_seconds": 1, "chaos": True,
                   "traffic_mix": {"GET /api/order?tier=gold": 25, "GET /api/order?tier=silver": 15,
                                   "GET /api/slow?delay=0.5": 15, "GET /api/slow?delay=1.5": 10,
                                   "GET /api/error": 25}},
    },
}


def get_scenario(name: str):
    return SCENARIOS.get(name)


def list_scenarios() -> list:
    return [{"id": key, **{k: v for k, v in s.items()}} for key, s in SCENARIOS.items()]
