import asyncio
import os
import time
import random
import logging
from collections import deque
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field

import aiohttp
from fastapi import FastAPI, Response, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST
)

from scenarios import SCENARIOS, list_scenarios, get_scenario

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("loadgen")

# Configuration
DEFAULT_TARGET_URL = os.getenv("TARGET_URL", "http://demo-app:8000").rstrip("/")

app = FastAPI(title="Professional Load Generator & Traffic Simulator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------
# Prometheus Client-Side Metrics
# ---------------------------------------------------------
LOADGEN_REQUESTS_TOTAL = Counter(
    "loadgen_requests_total",
    "Total HTTP requests initiated by the load generator",
    ["method", "endpoint", "status_code", "scenario"]
)

LOADGEN_REQUEST_DURATION = Histogram(
    "loadgen_request_duration_seconds",
    "Client-observed HTTP request duration in seconds",
    ["endpoint", "status_code"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0]
)

LOADGEN_REQUESTS_IN_FLIGHT = Gauge(
    "loadgen_requests_in_flight",
    "Number of client HTTP requests currently pending a response"
)

LOADGEN_TARGET_RPS = Gauge(
    "loadgen_target_rps",
    "Configured target requests per second"
)

LOADGEN_ACHIEVED_RPS = Gauge(
    "loadgen_achieved_rps",
    "Current measured achieved requests per second (TPS)"
)

LOADGEN_ERRORS_TOTAL = Counter(
    "loadgen_errors_total",
    "Total request errors categorized by failure type",
    ["error_type"]
)

LOADGEN_CONCURRENT_USERS = Gauge(
    "loadgen_concurrent_users",
    "Number of simulated concurrent worker coroutines active"
)

LOADGEN_SCENARIO_ACTIVE = Gauge(
    "loadgen_scenario_active",
    "Indicates which scenario is actively running (1 for active, 0 otherwise)",
    ["scenario_name"]
)

LOADGEN_BYTES_SENT = Counter(
    "loadgen_bytes_sent_total",
    "Total simulated HTTP request bytes transmitted"
)

LOADGEN_BYTES_RECEIVED = Counter(
    "loadgen_bytes_received_total",
    "Total simulated HTTP response bytes received"
)

LOADGEN_POOL_SIZE = Gauge(
    "loadgen_connection_pool_size",
    "Active HTTP connection pool size"
)

# Initial gauge states
LOADGEN_TARGET_RPS.set(0)
LOADGEN_ACHIEVED_RPS.set(0)
LOADGEN_CONCURRENT_USERS.set(0)
LOADGEN_REQUESTS_IN_FLIGHT.set(0)
LOADGEN_POOL_SIZE.set(0)
for s_name in SCENARIOS.keys():
    LOADGEN_SCENARIO_ACTIVE.labels(scenario_name=s_name).set(0)
LOADGEN_SCENARIO_ACTIVE.labels(scenario_name="custom").set(0)


# ---------------------------------------------------------
# Load Configuration Schema
# ---------------------------------------------------------
class LoadConfigRequest(BaseModel):
    target_url: Optional[str] = None
    target_rps: float = Field(default=10.0, ge=0.5, le=500.0)
    concurrency: int = Field(default=5, ge=1, le=100)
    duration_seconds: int = Field(default=0, ge=0, le=7200)  # 0 = indefinite
    ramp_up_seconds: int = Field(default=5, ge=0, le=600)
    ramp_down_seconds: int = Field(default=5, ge=0, le=600)
    timeout_seconds: float = Field(default=5.0, ge=0.1, le=30.0)
    traffic_mix: Dict[str, float] = Field(
        default_factory=lambda: {
            "GET /api/order?tier=gold": 50,
            "GET /api/order?tier=silver": 20,
            "GET /api/order?tier=bronze": 10,
            "GET /api/slow?delay=0.6": 12,
            "GET /api/error": 8,
        }
    )
    scenario_name: str = "custom"


class LoadUpdateRequest(BaseModel):
    target_rps: Optional[float] = Field(None, ge=0.5, le=500.0)
    concurrency: Optional[int] = Field(None, ge=1, le=100)


# ---------------------------------------------------------
# High-Performance Async Load Engine
# ---------------------------------------------------------
class LoadEngine:
    def __init__(self):
        self.is_running: bool = False
        self.config: Optional[LoadConfigRequest] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self.active_tasks: List[asyncio.Task] = []
        self.manager_task: Optional[asyncio.Task] = None
        
        # Concurrency & rate limiting
        self.semaphore: Optional[asyncio.Semaphore] = None
        self.current_target_rps: float = 0.0
        self.current_concurrency: int = 0
        
        # Sliding-window metrics for live TPS & latencies
        self.recent_completions: deque = deque(maxlen=2000)  # timestamps
        self.recent_latencies: deque = deque(maxlen=1000)    # seconds
        
        # Cumulative stats for current run
        self.stats = {
            "total_sent": 0,
            "status_2xx": 0,
            "status_4xx": 0,
            "status_5xx": 0,
            "errors": 0,
            "start_time": 0.0,
            "scenario": "idle",
            "state": "IDLE",  # IDLE, RAMP_UP, RUNNING, RAMP_DOWN, STOPPING
        }
        self.run_history: List[Dict[str, Any]] = []

    def normalize_traffic_mix(self, mix: Dict[str, float]) -> List[tuple[str, str, float]]:
        """Parses traffic mix dict into [(method, path, cumulative_weight)]"""
        items = []
        total_weight = sum(mix.values())
        if total_weight <= 0:
            return [("GET", "/api/order?tier=gold", 1.0)]

        cum = 0.0
        for entry, weight in mix.items():
            if weight <= 0:
                continue
            parts = entry.strip().split(" ", 1)
            if len(parts) == 2:
                method, path = parts[0].upper(), parts[1]
            else:
                method, path = "GET", parts[0]
            cum += (weight / total_weight)
            items.append((method, path, cum))
        return items

    def pick_endpoint(self, items: List[tuple[str, str, float]]) -> tuple[str, str]:
        r = random.random()
        for method, path, cum in items:
            if r <= cum:
                return method, path
        return items[-1][0], items[-1][1]

    async def _worker_loop(self, worker_id: int, traffic_items: List[tuple[str, str, float]]):
        """Worker loop maintaining concurrency limit and request dispatching."""
        target_url = (self.config.target_url or DEFAULT_TARGET_URL).rstrip("/")
        scenario = self.config.scenario_name

        while self.is_running:
            # Check concurrency boundary dynamically
            if worker_id >= self.current_concurrency:
                await asyncio.sleep(0.1)
                continue

            async with self.semaphore:
                # Rate-limiting inter-arrival delay based on current target RPS
                rps = max(self.current_target_rps, 0.1)
                target_interval = (1.0 / rps) * self.current_concurrency
                # Add natural jitter (Poisson-like spacing)
                interval = random.expovariate(1.0 / target_interval) if target_interval > 0 else 0.05
                await asyncio.sleep(min(interval, 2.0))

                if not self.is_running:
                    break

                method, path = self.pick_endpoint(traffic_items)
                full_url = f"{target_url}{path}"
                clean_endpoint = path.split("?")[0]

                LOADGEN_REQUESTS_IN_FLIGHT.inc()
                t0 = time.perf_counter()
                status_str = "error"

                try:
                    LOADGEN_BYTES_SENT.inc(120)  # estimate HTTP request headers
                    timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
                    async with self.session.request(method, full_url, timeout=timeout) as resp:
                        body = await resp.read()
                        duration = time.perf_counter() - t0
                        status_str = str(resp.status)
                        LOADGEN_BYTES_RECEIVED.inc(len(body) + 150)

                        self.stats["total_sent"] += 1
                        if 200 <= resp.status < 300:
                            self.stats["status_2xx"] += 1
                        elif 400 <= resp.status < 500:
                            self.stats["status_4xx"] += 1
                            LOADGEN_ERRORS_TOTAL.labels(error_type="http_4xx").inc()
                        elif resp.status >= 500:
                            self.stats["status_5xx"] += 1
                            LOADGEN_ERRORS_TOTAL.labels(error_type="http_5xx").inc()

                        self.recent_latencies.append(duration)
                        self.recent_completions.append(time.time())

                        LOADGEN_REQUESTS_TOTAL.labels(
                            method=method,
                            endpoint=clean_endpoint,
                            status_code=status_str,
                            scenario=scenario
                        ).inc()
                        LOADGEN_REQUEST_DURATION.labels(
                            endpoint=clean_endpoint,
                            status_code=status_str
                        ).observe(duration)

                except asyncio.TimeoutError:
                    duration = time.perf_counter() - t0
                    self.stats["errors"] += 1
                    LOADGEN_ERRORS_TOTAL.labels(error_type="timeout").inc()
                    LOADGEN_REQUESTS_TOTAL.labels(
                        method=method,
                        endpoint=clean_endpoint,
                        status_code="timeout",
                        scenario=scenario
                    ).inc()
                except aiohttp.ClientConnectorError:
                    duration = time.perf_counter() - t0
                    self.stats["errors"] += 1
                    LOADGEN_ERRORS_TOTAL.labels(error_type="connection_refused").inc()
                    LOADGEN_REQUESTS_TOTAL.labels(
                        method=method,
                        endpoint=clean_endpoint,
                        status_code="conn_refused",
                        scenario=scenario
                    ).inc()
                except Exception as ex:
                    duration = time.perf_counter() - t0
                    self.stats["errors"] += 1
                    LOADGEN_ERRORS_TOTAL.labels(error_type="other").inc()
                    LOADGEN_REQUESTS_TOTAL.labels(
                        method=method,
                        endpoint=clean_endpoint,
                        status_code="exception",
                        scenario=scenario
                    ).inc()
                finally:
                    LOADGEN_REQUESTS_IN_FLIGHT.dec()

    async def _manager_loop(self):
        """Manager loop handling TPS calculation, ramp-up, ramp-down, and duration."""
        start_t = time.time()
        self.stats["start_time"] = start_t
        cfg = self.config

        ramp_up_sec = cfg.ramp_up_seconds
        ramp_down_sec = cfg.ramp_down_seconds
        duration_sec = cfg.duration_seconds
        target_rps = cfg.target_rps
        target_conc = cfg.concurrency

        while self.is_running:
            now = time.time()
            elapsed = now - start_t

            # Calculate actual measured TPS over last 1.0 second
            cutoff_1s = now - 1.0
            cutoff_5s = now - 5.0
            
            # Prune old timestamps
            while self.recent_completions and self.recent_completions[0] < cutoff_5s:
                self.recent_completions.popleft()

            count_1s = sum(1 for t in self.recent_completions if t >= cutoff_1s)
            achieved_tps = float(count_1s)
            LOADGEN_ACHIEVED_RPS.set(achieved_tps)

            # Ramp-Up Phase
            if ramp_up_sec > 0 and elapsed < ramp_up_sec:
                self.stats["state"] = "RAMP_UP"
                progress = elapsed / ramp_up_sec
                self.current_target_rps = max(1.0, round(progress * target_rps, 1))
                self.current_concurrency = max(1, int(progress * target_conc))
            # Ramp-Down Phase (if duration is finite)
            elif duration_sec > 0 and elapsed >= (duration_sec - ramp_down_sec):
                self.stats["state"] = "RAMP_DOWN"
                remaining = max(0.0, duration_sec - elapsed)
                progress = remaining / max(ramp_down_sec, 1)
                self.current_target_rps = max(1.0, round(progress * target_rps, 1))
                self.current_concurrency = max(1, int(progress * target_conc))
            else:
                self.stats["state"] = "RUNNING"
                self.current_target_rps = target_rps
                self.current_concurrency = target_conc

            LOADGEN_TARGET_RPS.set(self.current_target_rps)
            LOADGEN_CONCURRENT_USERS.set(self.current_concurrency)

            # Check if duration completed
            if duration_sec > 0 and elapsed >= duration_sec:
                logger.info(f"Load test scenario '{cfg.scenario_name}' reached duration ({duration_sec}s). Stopping.")
                asyncio.create_task(self.stop())
                break

            await asyncio.sleep(0.5)

    async def start(self, config: LoadConfigRequest):
        if self.is_running:
            await self.stop()

        self.config = config
        self.is_running = True
        self.current_target_rps = 1.0 if config.ramp_up_seconds > 0 else config.target_rps
        self.current_concurrency = 1 if config.ramp_up_seconds > 0 else config.concurrency
        
        self.stats = {
            "total_sent": 0,
            "status_2xx": 0,
            "status_4xx": 0,
            "status_5xx": 0,
            "errors": 0,
            "start_time": time.time(),
            "scenario": config.scenario_name,
            "state": "INITIALIZING",
        }
        self.recent_completions.clear()
        self.recent_latencies.clear()

        # Reset active scenario labels
        for s_name in SCENARIOS.keys():
            LOADGEN_SCENARIO_ACTIVE.labels(scenario_name=s_name).set(1 if s_name == config.scenario_name else 0)
        LOADGEN_SCENARIO_ACTIVE.labels(scenario_name="custom").set(1 if config.scenario_name == "custom" else 0)

        # Semaphore for max workers
        self.semaphore = asyncio.Semaphore(100)
        
        # Async HTTP session with high limit
        conn = aiohttp.TCPConnector(limit=500, limit_per_host=200, ttl_dns_cache=300)
        self.session = aiohttp.ClientSession(connector=conn)
        LOADGEN_POOL_SIZE.set(500)

        traffic_items = self.normalize_traffic_mix(config.traffic_mix)

        # Launch worker pool (up to 100 coroutines)
        max_workers = min(100, max(config.concurrency * 2, 20))
        self.active_tasks = [
            asyncio.create_task(self._worker_loop(i, traffic_items))
            for i in range(max_workers)
        ]

        # Launch manager
        self.manager_task = asyncio.create_task(self._manager_loop())
        logger.info(f"Load test started: {config.scenario_name} (Target RPS: {config.target_rps}, Concurrency: {config.concurrency})")

    async def stop(self):
        if not self.is_running:
            return

        self.stats["state"] = "STOPPING"
        self.is_running = False

        if self.manager_task and not self.manager_task.done():
            self.manager_task.cancel()

        for t in self.active_tasks:
            t.cancel()

        if self.session and not self.session.closed:
            await self.session.close()

        # Reset gauges
        LOADGEN_TARGET_RPS.set(0)
        LOADGEN_ACHIEVED_RPS.set(0)
        LOADGEN_CONCURRENT_USERS.set(0)
        LOADGEN_REQUESTS_IN_FLIGHT.set(0)
        LOADGEN_POOL_SIZE.set(0)
        for s_name in SCENARIOS.keys():
            LOADGEN_SCENARIO_ACTIVE.labels(scenario_name=s_name).set(0)
        LOADGEN_SCENARIO_ACTIVE.labels(scenario_name="custom").set(0)

        # Save run history
        elapsed = time.time() - self.stats["start_time"]
        avg_tps = round(self.stats["total_sent"] / max(elapsed, 0.1), 1)
        lat_list = sorted(list(self.recent_latencies))
        p95 = round(lat_list[int(len(lat_list) * 0.95)] * 1000, 1) if lat_list else 0.0

        history_record = {
            "scenario": self.stats["scenario"],
            "duration": round(elapsed, 1),
            "total_sent": self.stats["total_sent"],
            "avg_tps": avg_tps,
            "errors": self.stats["errors"],
            "status_2xx": self.stats["status_2xx"],
            "status_5xx": self.stats["status_5xx"],
            "p95_ms": p95,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.run_history.insert(0, history_record)
        if len(self.run_history) > 15:
            self.run_history.pop()

        self.stats["state"] = "IDLE"
        logger.info(f"Load test stopped. Total requests sent: {self.stats['total_sent']}")

    def update_load(self, target_rps: Optional[float] = None, concurrency: Optional[int] = None):
        if not self.is_running or not self.config:
            raise HTTPException(status_code=400, detail="Load test is not currently active")
        
        if target_rps is not None:
            self.config.target_rps = target_rps
            self.current_target_rps = target_rps
            LOADGEN_TARGET_RPS.set(target_rps)

        if concurrency is not None:
            self.config.concurrency = concurrency
            self.current_concurrency = concurrency
            LOADGEN_CONCURRENT_USERS.set(concurrency)

    def get_status(self) -> Dict[str, Any]:
        elapsed = (time.time() - self.stats["start_time"]) if self.is_running else 0.0
        
        # Calculate percentiles from recent latencies
        lat_list = sorted(list(self.recent_latencies))
        n = len(lat_list)
        p50 = round(lat_list[int(n * 0.50)] * 1000, 1) if n > 0 else 0.0
        p95 = round(lat_list[int(n * 0.95)] * 1000, 1) if n > 0 else 0.0
        p99 = round(lat_list[int(n * 0.99)] * 1000, 1) if n > 0 else 0.0

        # Calculate current achieved TPS
        now = time.time()
        cutoff_1s = now - 1.0
        achieved_tps = sum(1 for t in self.recent_completions if t >= cutoff_1s)

        error_rate = 0.0
        if self.stats["total_sent"] > 0:
            error_rate = round((self.stats["errors"] + self.stats["status_5xx"]) / self.stats["total_sent"] * 100, 2)

        return {
            "running": self.is_running,
            "state": self.stats["state"],
            "scenario": self.stats["scenario"],
            "elapsed_seconds": round(elapsed, 1),
            "target_rps": self.current_target_rps if self.is_running else 0.0,
            "achieved_tps": achieved_tps,
            "concurrency": self.current_concurrency if self.is_running else 0,
            "total_sent": self.stats["total_sent"],
            "status_2xx": self.stats["status_2xx"],
            "status_4xx": self.stats["status_4xx"],
            "status_5xx": self.stats["status_5xx"],
            "errors": self.stats["errors"],
            "error_rate_pct": error_rate,
            "latency_p50_ms": p50,
            "latency_p95_ms": p95,
            "latency_p99_ms": p99,
            "target_url": (self.config.target_url or DEFAULT_TARGET_URL) if self.config else DEFAULT_TARGET_URL
        }


engine = LoadEngine()


# ---------------------------------------------------------
# Control-Plane API Routes
# ---------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "service": "load-generator"}


@app.get("/metrics")
def get_metrics():
    """Prometheus scrape endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/scenarios")
def get_scenarios():
    return {"scenarios": list_scenarios()}


@app.post("/api/load/start")
async def start_load(config: LoadConfigRequest):
    await engine.start(config)
    return {"status": "started", "config": config}


@app.post("/api/scenarios/{scenario_id}/run")
async def run_named_scenario(scenario_id: str, target_url: Optional[str] = None):
    scenario = get_scenario(scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found")
    
    cfg_data = scenario["config"].copy()
    cfg_data["scenario_name"] = scenario_id
    if target_url:
        cfg_data["target_url"] = target_url
    
    load_config = LoadConfigRequest(**cfg_data)
    await engine.start(load_config)
    return {"status": "started", "scenario": scenario_id, "name": scenario["name"]}


@app.post("/api/load/stop")
async def stop_load():
    await engine.stop()
    return {"status": "stopped", "stats": engine.stats}


@app.post("/api/load/update")
def update_load(update: LoadUpdateRequest):
    engine.update_load(target_rps=update.target_rps, concurrency=update.concurrency)
    return {
        "status": "updated",
        "target_rps": engine.current_target_rps,
        "concurrency": engine.current_concurrency
    }


@app.get("/api/load/status")
def get_status():
    return engine.get_status()


@app.get("/api/history")
def get_history():
    return {"history": engine.run_history}


# ---------------------------------------------------------
# Embedded Web UI
# ---------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Load Generator & TPS Controller</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #070a12;
      --card-bg: rgba(15, 21, 37, 0.75);
      --border: rgba(255, 255, 255, 0.08);
      --primary: #6366f1;
      --primary-hover: #4f46e5;
      --primary-glow: rgba(99, 102, 241, 0.4);
      --accent: #8b5cf6;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --cyan: #06b6d4;
      --text: #f3f4f6;
      --text-muted: #94a3b8;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: 'Plus Jakarta Sans', sans-serif;
      background: radial-gradient(circle at 50% 0%, #151b36 0%, var(--bg) 70%);
      color: var(--text);
      min-height: 100vh;
      padding: 1.5rem 1rem 3rem 1rem;
      display: flex;
      flex-direction: column;
      align-items: center;
    }

    .container {
      width: 100%;
      max-width: 1100px;
      display: flex;
      flex-direction: column;
      gap: 1.25rem;
    }

    header {
      text-align: center;
      padding: 0.75rem 0;
    }

    .badge-bar {
      display: inline-flex;
      align-items: center;
      gap: 0.6rem;
      background: rgba(99, 102, 241, 0.12);
      border: 1px solid rgba(99, 102, 241, 0.3);
      padding: 0.35rem 0.9rem;
      border-radius: 9999px;
      font-size: 0.8rem;
      color: #c7d2fe;
      font-weight: 600;
      margin-bottom: 0.75rem;
    }

    .pulse {
      width: 8px;
      height: 8px;
      background: var(--success);
      border-radius: 50%;
      box-shadow: 0 0 10px var(--success);
      animation: pulseAnim 2s infinite;
    }
    .pulse.idle {
      background: var(--text-muted);
      box-shadow: none;
      animation: none;
    }
    @keyframes pulseAnim {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.3; transform: scale(0.85); }
    }

    h1 {
      font-size: 2.2rem;
      font-weight: 800;
      letter-spacing: -0.03em;
      background: linear-gradient(135deg, #ffffff 20%, #a5b4fc 70%, #818cf8 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      margin-bottom: 0.4rem;
    }

    p.subtitle {
      color: var(--text-muted);
      font-size: 0.95rem;
    }

    .top-links {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 0.75rem;
    }
    .top-link {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      background: rgba(255, 255, 255, 0.02);
      border: 1px solid var(--border);
      padding: 0.75rem 1rem;
      border-radius: 10px;
      text-decoration: none;
      color: var(--text);
      transition: all 0.2s;
    }
    .top-link:hover {
      background: rgba(99, 102, 241, 0.1);
      border-color: rgba(99, 102, 241, 0.4);
      transform: translateY(-2px);
    }
    .top-link .icon { font-size: 1.3rem; }
    .top-link .info .title { font-size: 0.82rem; font-weight: 700; }
    .top-link .info .sub { font-size: 0.7rem; color: var(--text-muted); font-family: 'JetBrains Mono', monospace; }

    .card {
      background: var(--card-bg);
      backdrop-filter: blur(14px);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 1.25rem 1.5rem;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
    }

    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1rem;
    }
    .card-title {
      font-size: 1.05rem;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }

    /* Live Stat Cards Grid */
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 0.75rem;
    }
    .kpi-box {
      background: rgba(255, 255, 255, 0.025);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1rem 0.75rem;
      text-align: center;
      position: relative;
      overflow: hidden;
    }
    .kpi-box::after {
      content: '';
      position: absolute;
      top: 0; left: 0; right: 0; height: 2px;
      background: linear-gradient(90deg, transparent, var(--border), transparent);
    }
    .kpi-box.active-glow::after {
      background: linear-gradient(90deg, transparent, var(--cyan), transparent);
    }
    .kpi-val {
      font-size: 1.7rem;
      font-weight: 800;
      font-family: 'JetBrains Mono', monospace;
      color: #fff;
      line-height: 1.2;
    }
    .kpi-label {
      font-size: 0.7rem;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-top: 0.3rem;
    }

    /* Scenarios Grid */
    .scenarios-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 0.65rem;
    }
    .scenario-btn {
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 0.75rem 0.5rem;
      text-align: center;
      cursor: pointer;
      color: var(--text);
      font-family: inherit;
      transition: all 0.2s;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 0.35rem;
    }
    .scenario-btn:hover {
      background: rgba(99, 102, 241, 0.15);
      border-color: rgba(99, 102, 241, 0.5);
      transform: translateY(-2px);
    }
    .scenario-btn.active {
      background: rgba(99, 102, 241, 0.25);
      border-color: var(--primary);
      box-shadow: 0 0 15px rgba(99, 102, 241, 0.4);
    }
    .scenario-btn .icon { font-size: 1.5rem; }
    .scenario-btn .name { font-size: 0.8rem; font-weight: 700; }
    .scenario-btn .desc { font-size: 0.65rem; color: var(--text-muted); line-height: 1.2; }

    /* Controls Grid */
    .control-row {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 1.25rem;
      margin-bottom: 1.25rem;
    }
    .form-group {
      display: flex;
      flex-direction: column;
      gap: 0.4rem;
    }
    .form-label {
      display: flex;
      justify-content: space-between;
      font-size: 0.8rem;
      font-weight: 600;
      color: #e2e8f0;
    }
    .form-val-display {
      font-family: 'JetBrains Mono', monospace;
      color: #a5b4fc;
    }
    input[type=range] {
      -webkit-appearance: none;
      width: 100%;
      height: 6px;
      background: rgba(255, 255, 255, 0.1);
      border-radius: 3px;
      outline: none;
    }
    input[type=range]::-webkit-slider-thumb {
      -webkit-appearance: none;
      width: 18px;
      height: 18px;
      background: var(--primary);
      border-radius: 50%;
      cursor: pointer;
      box-shadow: 0 0 10px var(--primary-glow);
    }

    .traffic-mix-box {
      background: rgba(0, 0, 0, 0.2);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 0.75rem 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }
    .mix-item {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      font-size: 0.78rem;
    }
    .mix-name {
      flex: 1;
      font-family: 'JetBrains Mono', monospace;
      color: #cbd5e1;
    }

    /* Actions Bar */
    .actions-bar {
      display: flex;
      gap: 0.75rem;
      flex-wrap: wrap;
      margin-top: 1rem;
    }
    button.btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 0.5rem;
      font-family: inherit;
      font-weight: 700;
      font-size: 0.9rem;
      padding: 0.75rem 1.4rem;
      border-radius: 10px;
      border: none;
      cursor: pointer;
      transition: all 0.2s ease;
    }
    button.btn:active { transform: scale(0.98); }
    .btn-start {
      background: linear-gradient(135deg, #10b981, #059669);
      color: #fff;
      box-shadow: 0 4px 15px rgba(16, 185, 129, 0.35);
      flex: 2;
      min-width: 160px;
    }
    .btn-start:hover {
      background: linear-gradient(135deg, #34d399, #10b981);
    }
    .btn-stop {
      background: linear-gradient(135deg, #ef4444, #dc2626);
      color: #fff;
      box-shadow: 0 4px 15px rgba(239, 68, 68, 0.35);
      flex: 2;
      min-width: 160px;
    }
    .btn-update {
      background: rgba(99, 102, 241, 0.2);
      border: 1px solid rgba(99, 102, 241, 0.5);
      color: #c7d2fe;
      flex: 1;
      min-width: 130px;
    }
    .btn-update:hover {
      background: rgba(99, 102, 241, 0.35);
    }

    /* Sparkline Chart Canvas */
    .chart-container {
      margin-top: 1rem;
      background: rgba(0, 0, 0, 0.3);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 0.75rem;
      height: 120px;
      position: relative;
    }
    canvas#tpsChart {
      width: 100%;
      height: 100%;
    }

    /* Log Box */
    .log-box {
      background: #04060c;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 0.75rem 1rem;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.78rem;
      color: #a5b4fc;
      max-height: 130px;
      overflow-y: auto;
    }

    /* History Table */
    .history-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.78rem;
      font-family: 'JetBrains Mono', monospace;
    }
    .history-table th, .history-table td {
      padding: 0.5rem 0.6rem;
      text-align: left;
      border-bottom: 1px solid rgba(255, 255, 255, 0.05);
    }
    .history-table th {
      color: var(--text-muted);
      font-weight: 600;
      text-transform: uppercase;
      font-size: 0.68rem;
    }
    .status-badge {
      display: inline-block;
      padding: 0.2rem 0.55rem;
      border-radius: 9999px;
      font-size: 0.7rem;
      font-weight: 700;
    }
    .badge-running { background: rgba(16, 185, 129, 0.2); color: #34d399; }
    .badge-idle { background: rgba(148, 163, 184, 0.2); color: #94a3b8; }
    .badge-ramp { background: rgba(245, 158, 11, 0.2); color: #fbbf24; }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="badge-bar">
        <span class="pulse" id="pulseIndicator"></span>
        <span id="stackStatus">Load Generator Ready</span> &bull; Target: <span id="targetUrlDisplay">http://demo-app:8000</span>
      </div>
      <h1>TPS & Load Generator Controller</h1>
      <p class="subtitle">High-concurrency async load engine with real-time Grafana TPS telemetry</p>
    </header>

    <!-- Top Links -->
    <div class="top-links">
      <a href="http://localhost:3000" target="_blank" class="top-link" id="grafanaTpsLink">
        <div class="icon">📊</div>
        <div class="info">
          <div class="title">Grafana: Load & TPS</div>
          <div class="sub">TPS, P95, Saturation</div>
        </div>
      </a>
      <a href="http://localhost:3000" target="_blank" class="top-link" id="grafanaAppLink">
        <div class="icon">📈</div>
        <div class="info">
          <div class="title">Grafana: App Health</div>
          <div class="sub">Server Golden Signals</div>
        </div>
      </a>
      <a href="http://localhost:8000" target="_blank" class="top-link">
        <div class="icon">⚡</div>
        <div class="info">
          <div class="title">Demo App UI</div>
          <div class="sub">http://localhost:8000</div>
        </div>
      </a>
      <a href="/metrics" target="_blank" class="top-link">
        <div class="icon">🔥</div>
        <div class="info">
          <div class="title">Client Metrics</div>
          <div class="sub">/metrics (:8089)</div>
        </div>
      </a>
    </div>

    <!-- Live Telemetry KPI Dashboard -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">⚡ Real-Time TPS & Performance Telemetry</div>
        <span class="status-badge badge-idle" id="stateBadge">IDLE</span>
      </div>
      <div class="kpi-grid">
        <div class="kpi-box active-glow">
          <div class="kpi-val" id="kpiAchievedTps" style="color: #38bdf8;">0</div>
          <div class="kpi-label">Achieved TPS</div>
        </div>
        <div class="kpi-box">
          <div class="kpi-val" id="kpiTargetRps" style="color: #a5b4fc;">0</div>
          <div class="kpi-label">Target RPS</div>
        </div>
        <div class="kpi-box">
          <div class="kpi-val" id="kpiConcurrency" style="color: #c084fc;">0</div>
          <div class="kpi-label">Active Users</div>
        </div>
        <div class="kpi-box">
          <div class="kpi-val" id="kpiP95" style="color: #fbbf24;">0<span style="font-size:0.9rem;">ms</span></div>
          <div class="kpi-label">P95 Latency</div>
        </div>
        <div class="kpi-box">
          <div class="kpi-val" id="kpiErrorRate" style="color: #f87171;">0%</div>
          <div class="kpi-label">Error Rate</div>
        </div>
        <div class="kpi-box">
          <div class="kpi-val" id="kpiTotalSent">0</div>
          <div class="kpi-label">Total Requests</div>
        </div>
      </div>

      <!-- Real-time Sparkline Graph -->
      <div class="chart-container">
        <canvas id="tpsChart"></canvas>
      </div>
    </div>

    <!-- 1-Click Preset Scenarios -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">🎯 Pre-Built Test Scenarios (1-Click Run)</div>
        <span style="font-size:0.75rem; color:var(--text-muted);">One-click production-grade patterns</span>
      </div>
      <div class="scenarios-grid" id="scenariosContainer">
        <!-- Injected via JavaScript -->
      </div>
    </div>

    <!-- Dynamic Sliders & Custom Control Panel -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">🎛️ Custom Load Parameters & Live Tuning</div>
        <span style="font-size:0.75rem; color:var(--text-muted);">Hot-update works while running</span>
      </div>

      <div class="control-row">
        <!-- Target RPS -->
        <div class="form-group">
          <div class="form-label">
            <span>Target Throughput</span>
            <span class="form-val-display" id="valRps">25 RPS</span>
          </div>
          <input type="range" id="inputRps" min="1" max="250" value="25" oninput="updateLabel('valRps', this.value + ' RPS')">
        </div>

        <!-- Concurrency -->
        <div class="form-group">
          <div class="form-label">
            <span>Concurrency (Virtual Users)</span>
            <span class="form-val-display" id="valConcurrency">10 Users</span>
          </div>
          <input type="range" id="inputConcurrency" min="1" max="50" value="10" oninput="updateLabel('valConcurrency', this.value + ' Users')">
        </div>

        <!-- Duration -->
        <div class="form-group">
          <div class="form-label">
            <span>Test Duration (0 = Indefinite)</span>
            <span class="form-val-display" id="valDuration">Indefinite</span>
          </div>
          <input type="range" id="inputDuration" min="0" max="600" step="30" value="0" oninput="updateLabel('valDuration', this.value == 0 ? 'Indefinite' : this.value + 's')">
        </div>

        <!-- Ramp-Up Time -->
        <div class="form-group">
          <div class="form-label">
            <span>Ramp-Up Duration</span>
            <span class="form-val-display" id="valRampUp">5s</span>
          </div>
          <input type="range" id="inputRampUp" min="0" max="60" step="5" value="5" oninput="updateLabel('valRampUp', this.value + 's')">
        </div>
      </div>

      <!-- Traffic Mix Config -->
      <div class="form-label" style="margin-bottom: 0.5rem;">Traffic Endpoint Distribution</div>
      <div class="traffic-mix-box">
        <div class="mix-item">
          <span class="mix-name">🛒 GET /api/order?tier=gold (Fast 200 OK)</span>
          <input type="range" id="mixGold" min="0" max="100" value="50" style="width: 140px;" oninput="updateLabel('lblMixGold', this.value + '%')">
          <span class="form-val-display" id="lblMixGold" style="width: 40px; text-align:right;">50%</span>
        </div>
        <div class="mix-item">
          <span class="mix-name">🥈 GET /api/order?tier=silver</span>
          <input type="range" id="mixSilver" min="0" max="100" value="20" style="width: 140px;" oninput="updateLabel('lblMixSilver', this.value + '%')">
          <span class="form-val-display" id="lblMixSilver" style="width: 40px; text-align:right;">20%</span>
        </div>
        <div class="mix-item">
          <span class="mix-name">⏳ GET /api/slow?delay=0.8 (Latency Spike)</span>
          <input type="range" id="mixSlow" min="0" max="100" value="15" style="width: 140px;" oninput="updateLabel('lblMixSlow', this.value + '%')">
          <span class="form-val-display" id="lblMixSlow" style="width: 40px; text-align:right;">15%</span>
        </div>
        <div class="mix-item">
          <span class="mix-name">💥 GET /api/error (Simulated 500 Spike)</span>
          <input type="range" id="mixError" min="0" max="100" value="15" style="width: 140px;" oninput="updateLabel('lblMixError', this.value + '%')">
          <span class="form-val-display" id="lblMixError" style="width: 40px; text-align:right;">15%</span>
        </div>
      </div>

      <!-- Action Buttons -->
      <div class="actions-bar">
        <button class="btn btn-start" id="btnStart" onclick="startCustomLoad()">▶ Start Custom Load Test</button>
        <button class="btn btn-stop" id="btnStop" onclick="stopLoad()" style="display:none;">⏹ Stop Load Test</button>
        <button class="btn btn-update" id="btnUpdate" onclick="updateLiveLoad()">⚡ Apply Live Changes</button>
      </div>
    </div>

    <!-- Execution Activity Log -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">📜 Event Activity Log</div>
        <span style="font-size:0.75rem; color:var(--text-muted); cursor:pointer;" onclick="clearLog()">Clear</span>
      </div>
      <div class="log-box" id="logBox">
        [Ready] Standalone load engine initialized. Select a scenario or click Start...
      </div>
    </div>

    <!-- Run History -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">🕒 Recent Test Runs History</div>
      </div>
      <div style="overflow-x: auto;">
        <table class="history-table">
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>Scenario</th>
              <th>Duration</th>
              <th>Total Reqs</th>
              <th>Avg TPS</th>
              <th>Errors</th>
              <th>P95</th>
            </tr>
          </thead>
          <tbody id="historyBody">
            <tr><td colspan="7" style="color:var(--text-muted); text-align:center;">No completed runs yet.</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <script>
    const logBox = document.getElementById('logBox');
    const stateBadge = document.getElementById('stateBadge');
    const pulse = document.getElementById('pulseIndicator');
    const btnStart = document.getElementById('btnStart');
    const btnStop = document.getElementById('btnStop');

    let tpsHistory = new Array(60).fill(0);
    let targetHistory = new Array(60).fill(0);

    function log(msg) {
      const now = new Date().toLocaleTimeString();
      logBox.innerHTML = `[${now}] ${msg}<br>` + logBox.innerHTML;
    }

    function clearLog() {
      logBox.innerHTML = '';
    }

    function updateLabel(id, text) {
      document.getElementById(id).innerText = text;
    }

    // Load scenarios on start
    async function loadScenarios() {
      try {
        const res = await fetch('/api/scenarios');
        const data = await res.json();
        const container = document.getElementById('scenariosContainer');
        container.innerHTML = '';
        data.scenarios.forEach(sc => {
          const btn = document.createElement('div');
          btn.className = 'scenario-btn';
          btn.id = 'sc_' + sc.id;
          btn.onclick = () => runScenario(sc.id, sc.name);
          btn.innerHTML = `
            <div class="icon">${sc.icon}</div>
            <div class="name">${sc.name}</div>
            <div class="desc">${sc.config.target_rps} RPS &bull; ${sc.config.concurrency} VU</div>
          `;
          container.appendChild(btn);
        });
      } catch (err) {
        console.error(err);
      }
    }

    async function runScenario(scenarioId, scenarioName) {
      try {
        log(`<span style="color:#a5b4fc;">▶ Launching scenario: <strong>${scenarioName}</strong>...</span>`);
        const res = await fetch(`/api/scenarios/${scenarioId}/run`, { method: 'POST' });
        if (res.ok) {
          log(`<span style="color:#34d399;">✔ Scenario '${scenarioName}' active!</span>`);
        }
      } catch (err) {
        log(`<span style="color:#f87171;">✖ Failed to start scenario: ${err.message}</span>`);
      }
    }

    async function startCustomLoad() {
      const rps = parseFloat(document.getElementById('inputRps').value);
      const concurrency = parseInt(document.getElementById('inputConcurrency').value);
      const duration = parseInt(document.getElementById('inputDuration').value);
      const rampUp = parseInt(document.getElementById('inputRampUp').value);

      const mix = {
        "GET /api/order?tier=gold": parseFloat(document.getElementById('mixGold').value),
        "GET /api/order?tier=silver": parseFloat(document.getElementById('mixSilver').value),
        "GET /api/slow?delay=0.8": parseFloat(document.getElementById('mixSlow').value),
        "GET /api/error": parseFloat(document.getElementById('mixError').value),
      };

      const payload = {
        target_rps: rps,
        concurrency: concurrency,
        duration_seconds: duration,
        ramp_up_seconds: rampUp,
        ramp_down_seconds: 5,
        traffic_mix: mix,
        scenario_name: "custom"
      };

      try {
        log(`<span style="color:#a5b4fc;">▶ Starting custom test (${rps} RPS, ${concurrency} users)...</span>`);
        const res = await fetch('/api/load/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (res.ok) {
          log(`<span style="color:#34d399;">✔ Custom load test running!</span>`);
        }
      } catch (err) {
        log(`<span style="color:#f87171;">✖ Failed to start: ${err.message}</span>`);
      }
    }

    async function stopLoad() {
      try {
        log(`<span style="color:#fbbf24;">⏹ Stopping load test...</span>`);
        const res = await fetch('/api/load/stop', { method: 'POST' });
        if (res.ok) {
          log(`<span style="color:#94a3b8;">⏹ Load engine stopped.</span>`);
          refreshHistory();
        }
      } catch (err) {
        log(`<span style="color:#f87171;">✖ Error stopping: ${err.message}</span>`);
      }
    }

    async function updateLiveLoad() {
      const rps = parseFloat(document.getElementById('inputRps').value);
      const concurrency = parseInt(document.getElementById('inputConcurrency').value);

      try {
        const res = await fetch('/api/load/update', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target_rps: rps, concurrency: concurrency })
        });
        if (res.ok) {
          log(`<span style="color:#38bdf8;">⚡ Hot-updated load: ${rps} RPS &bull; ${concurrency} users</span>`);
        } else {
          const err = await res.json();
          log(`<span style="color:#f87171;">✖ Update failed: ${err.detail || 'Test not active'}</span>`);
        }
      } catch (err) {
        log(`<span style="color:#f87171;">✖ Error: ${err.message}</span>`);
      }
    }

    async function refreshHistory() {
      try {
        const res = await fetch('/api/history');
        const data = await res.json();
        const tbody = document.getElementById('historyBody');
        if (!data.history || data.history.length === 0) {
          tbody.innerHTML = '<tr><td colspan="7" style="color:var(--text-muted); text-align:center;">No completed runs yet.</td></tr>';
          return;
        }
        tbody.innerHTML = data.history.map(h => `
          <tr>
            <td style="color:var(--text-muted);">${h.timestamp}</td>
            <td><strong>${h.scenario}</strong></td>
            <td>${h.duration}s</td>
            <td>${h.total_sent}</td>
            <td style="color:#38bdf8;">${h.avg_tps}</td>
            <td style="color:${h.errors > 0 ? '#f87171' : '#34d399'};">${h.errors}</td>
            <td style="color:#fbbf24;">${h.p95_ms}ms</td>
          </tr>
        `).join('');
      } catch (e) {}
    }

    // Canvas Sparkline Chart
    const canvas = document.getElementById('tpsChart');
    const ctx = canvas.getContext('2d');

    function resizeCanvas() {
      canvas.width = canvas.parentElement.clientWidth - 24;
      canvas.height = canvas.parentElement.clientHeight - 24;
    }
    window.addEventListener('resize', resizeCanvas);
    resizeCanvas();

    function drawChart() {
      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);

      const maxVal = Math.max(10, ...tpsHistory, ...targetHistory);
      const step = w / (tpsHistory.length - 1);

      // Draw grid lines
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
      ctx.lineWidth = 1;
      for (let i = 1; i <= 3; i++) {
        const y = h - (h / 4) * i;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
      }

      // Draw Target line (dashed purple)
      ctx.strokeStyle = 'rgba(165, 180, 252, 0.4)';
      ctx.setLineDash([4, 4]);
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      targetHistory.forEach((v, idx) => {
        const x = idx * step;
        const y = h - (v / maxVal) * (h - 10) - 5;
        if (idx === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.setLineDash([]);

      // Draw Achieved TPS line (cyan)
      ctx.strokeStyle = '#38bdf8';
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      tpsHistory.forEach((v, idx) => {
        const x = idx * step;
        const y = h - (v / maxVal) * (h - 10) - 5;
        if (idx === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();

      // Area fill
      ctx.lineTo(w, h);
      ctx.lineTo(0, h);
      ctx.closePath();
      const grad = ctx.createLinearGradient(0, 0, 0, h);
      grad.addColorStop(0, 'rgba(56, 189, 248, 0.25)');
      grad.addColorStop(1, 'rgba(56, 189, 248, 0.0)');
      ctx.fillStyle = grad;
      ctx.fill();

      // Legend
      ctx.font = '10px JetBrains Mono';
      ctx.fillStyle = '#38bdf8';
      ctx.fillText('Achieved TPS', 10, 15);
      ctx.fillStyle = '#a5b4fc';
      ctx.fillText('-- Target RPS', 110, 15);
    }

    // Telemetry Poller (every 1 second)
    setInterval(async () => {
      try {
        const res = await fetch('/api/load/status');
        const st = await res.json();

        // Update KPIs
        document.getElementById('kpiAchievedTps').innerText = st.achieved_tps;
        document.getElementById('kpiTargetRps').innerText = st.target_rps;
        document.getElementById('kpiConcurrency').innerText = st.concurrency;
        document.getElementById('kpiP95').innerHTML = st.latency_p95_ms + '<span style="font-size:0.9rem;">ms</span>';
        document.getElementById('kpiErrorRate').innerText = st.error_rate_pct + '%';
        document.getElementById('kpiTotalSent').innerText = st.total_sent;
        document.getElementById('targetUrlDisplay').innerText = st.target_url;

        // Update state badges & buttons
        stateBadge.innerText = st.state;
        if (st.running) {
          stateBadge.className = 'status-badge badge-running';
          pulse.className = 'pulse';
          document.getElementById('stackStatus').innerText = `Active [${st.scenario}] (${st.elapsed_seconds}s)`;
          btnStart.style.display = 'none';
          btnStop.style.display = 'inline-flex';
        } else {
          stateBadge.className = 'status-badge badge-idle';
          pulse.className = 'pulse idle';
          document.getElementById('stackStatus').innerText = 'Load Engine Idle';
          btnStart.style.display = 'inline-flex';
          btnStop.style.display = 'none';
        }

        // Highlight active scenario card
        document.querySelectorAll('.scenario-btn').forEach(el => el.classList.remove('active'));
        if (st.running && st.scenario !== 'custom') {
          const act = document.getElementById('sc_' + st.scenario);
          if (act) act.classList.add('active');
        }

        // Update sparkline data
        tpsHistory.shift();
        tpsHistory.push(st.achieved_tps);
        targetHistory.shift();
        targetHistory.push(st.target_rps);
        drawChart();

      } catch (err) {}
    }, 1000);

    // Initial load
    loadScenarios();
    refreshHistory();
  </script>
</body>
</html>
"""
