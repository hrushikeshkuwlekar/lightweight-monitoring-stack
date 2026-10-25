"""
Pre-built load test scenarios for the load generator.
Each scenario defines a load profile that can be run with one click.
"""

SCENARIOS = {
    "steady-state": {
        "name": "Steady State",
        "description": "Normal production traffic — stable RPS with realistic mix",
        "icon": "🟢",
        "config": {
            "duration_seconds": 0,  # 0 = indefinite
            "ramp_up_seconds": 10,
            "ramp_down_seconds": 5,
            "concurrency": 5,
            "target_rps": 10,
            "traffic_mix": {
                "GET /api/order?tier=gold": 50,
                "GET /api/order?tier=silver": 20,
                "GET /api/order?tier=bronze": 10,
                "GET /api/slow?delay=0.6": 12,
                "GET /api/error": 8,
            },
            "timeout_seconds": 5,
        },
    },
    "ramp-up": {
        "name": "Ramp-Up Stress Test",
        "description": "Gradual load increase from 0→50 RPS over 2min, hold 3min, ramp down",
        "icon": "📈",
        "config": {
            "duration_seconds": 360,
            "ramp_up_seconds": 120,
            "ramp_down_seconds": 60,
            "concurrency": 30,
            "target_rps": 50,
            "traffic_mix": {
                "GET /api/order?tier=gold": 55,
                "GET /api/order?tier=silver": 15,
                "GET /api/order?tier=bronze": 5,
                "GET /api/slow?delay=0.8": 15,
                "GET /api/error": 10,
            },
            "timeout_seconds": 5,
        },
    },
    "spike-test": {
        "name": "Traffic Spike",
        "description": "Sudden burst from 5→100 RPS for 30s then back to baseline",
        "icon": "⚡",
        "config": {
            "duration_seconds": 120,
            "ramp_up_seconds": 3,
            "ramp_down_seconds": 5,
            "concurrency": 50,
            "target_rps": 100,
            "traffic_mix": {
                "GET /api/order?tier=gold": 60,
                "GET /api/order?tier=silver": 20,
                "GET /api/slow?delay=0.3": 10,
                "GET /api/error": 10,
            },
            "timeout_seconds": 3,
        },
    },
    "error-storm": {
        "name": "Error Storm",
        "description": "90% error traffic at 20 RPS — triggers high error rate alerts",
        "icon": "🔴",
        "config": {
            "duration_seconds": 90,
            "ramp_up_seconds": 5,
            "ramp_down_seconds": 5,
            "concurrency": 15,
            "target_rps": 20,
            "traffic_mix": {
                "GET /api/error": 90,
                "GET /api/order?tier=gold": 10,
            },
            "timeout_seconds": 5,
        },
    },
    "latency-soak": {
        "name": "Latency Soak",
        "description": "70% slow queries at 15 RPS for 5min — triggers P95 latency alerts",
        "icon": "🐢",
        "config": {
            "duration_seconds": 300,
            "ramp_up_seconds": 10,
            "ramp_down_seconds": 10,
            "concurrency": 20,
            "target_rps": 15,
            "traffic_mix": {
                "GET /api/slow?delay=1.2": 40,
                "GET /api/slow?delay=0.8": 30,
                "GET /api/order?tier=gold": 20,
                "GET /api/error": 10,
            },
            "timeout_seconds": 10,
        },
    },
    "chaos": {
        "name": "Chaos Mode",
        "description": "Unpredictable — random RPS, random mix, random pauses",
        "icon": "🎲",
        "config": {
            "duration_seconds": 180,
            "ramp_up_seconds": 1,
            "ramp_down_seconds": 1,
            "concurrency": 40,
            "target_rps": 60,
            "traffic_mix": {
                "GET /api/order?tier=gold": 25,
                "GET /api/order?tier=silver": 15,
                "GET /api/order?tier=bronze": 10,
                "GET /api/slow?delay=0.5": 15,
                "GET /api/slow?delay=1.5": 10,
                "GET /api/error": 25,
            },
            "timeout_seconds": 5,
            "chaos_mode": True,
        },
    },
    "canary-compare": {
        "name": "Canary Compare",
        "description": "Even 50/50 split between fast orders and slow queries for A/B comparison",
        "icon": "🔀",
        "config": {
            "duration_seconds": 180,
            "ramp_up_seconds": 15,
            "ramp_down_seconds": 10,
            "concurrency": 10,
            "target_rps": 20,
            "traffic_mix": {
                "GET /api/order?tier=gold": 50,
                "GET /api/slow?delay=0.8": 50,
            },
            "timeout_seconds": 5,
        },
    },
}


def get_scenario(name: str) -> dict | None:
    """Get a scenario configuration by name."""
    return SCENARIOS.get(name)


def list_scenarios() -> list[dict]:
    """List all available scenarios with metadata."""
    result = []
    for key, scenario in SCENARIOS.items():
        result.append({
            "id": key,
            "name": scenario["name"],
            "description": scenario["description"],
            "icon": scenario["icon"],
            "config": scenario["config"],
        })
    return result
