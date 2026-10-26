import asyncio
import os
from pathlib import Path
import time
import random
from fastapi import FastAPI, Response, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST
)

START_TIME = time.time()

app = FastAPI(title="Lightweight Demo App with Monitoring")

# ---------------------------------------------------------
# Server-Side Prometheus Metrics
# ---------------------------------------------------------
REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests received",
    ["method", "endpoint", "status_code"]
)

REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0]  # identical to the load generator's, so client vs server p95 compare fairly
)

REQUESTS_IN_FLIGHT = Gauge(
    "http_requests_in_flight",
    "Number of HTTP requests currently being handled"
)

REQUEST_SIZE_BYTES = Histogram(
    "http_request_size_bytes",
    "HTTP request payload size in bytes",
    ["endpoint"],
    buckets=[64, 128, 256, 512, 1024, 2048, 4096, 8192]
)

RESPONSE_SIZE_BYTES = Histogram(
    "http_response_size_bytes",
    "HTTP response payload size in bytes",
    ["endpoint"],
    buckets=[64, 128, 256, 512, 1024, 2048, 4096, 8192]
)

ACTIVE_USERS = Gauge(
    "app_active_connections",
    "Number of simulated active client connections"
)

MEMORY_USAGE_GAUGE = Gauge(
    "app_simulated_memory_mb",
    "Simulated working set memory footprint in MB"
)

ORDERS_COUNTER = Counter(
    "app_business_orders_total",
    "Total successful business transactions completed",
    ["tier"]
)

ORDERS_VALUE_COUNTER = Counter(
    "app_orders_value_usd_total",
    "Simulated total revenue in USD from processed orders",
    ["tier"]
)

APP_UPTIME_SECONDS = Gauge(
    "app_uptime_seconds",
    "Seconds elapsed since demo application startup"
)

APP_INFO = Gauge(
    "app_info",
    "Application metadata information",
    ["version", "framework", "architecture"]
)

WORKER_SLOTS = Gauge(
    "app_worker_slots",
    "Requests the app processes at once (0 = unlimited)"
)

BUSY_WORKERS = Gauge(
    "app_busy_workers",
    "Worker slots currently processing a request"
)

QUEUED_REQUESTS = Gauge(
    "app_queued_requests",
    "Requests waiting for a free worker slot"
)

QUEUE_WAIT_SECONDS = Histogram(
    "app_queue_wait_seconds",
    "Time a request waited for a free worker slot",
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0]
)

# Initialize Gauges
ACTIVE_USERS.set(12)
MEMORY_USAGE_GAUGE.set(38.5)
REQUESTS_IN_FLIGHT.set(0)
APP_INFO.labels(version="3.0.0", framework="FastAPI", architecture="lightweight").set(1)


class WorkerPool:
    """A resizable pool of worker slots, like a web server's thread pool.

    When every slot is busy, new requests wait in a first-come-first-served
    queue. That waiting time is what makes an overloaded app slow: response
    time = queue wait + service time.
    """

    def __init__(self, slots: int):
        self.slots = slots          # 0 = unlimited
        self.busy = 0
        self.waiters: list = []

    def _has_room(self) -> bool:
        return self.slots == 0 or self.busy < self.slots

    async def acquire(self) -> float:
        start = time.perf_counter()
        if not self._has_room() or self.waiters:
            fut = asyncio.get_running_loop().create_future()
            self.waiters.append(fut)
            QUEUED_REQUESTS.set(len(self.waiters))
            try:
                await fut
            except asyncio.CancelledError:
                if fut in self.waiters:
                    self.waiters.remove(fut)
                    QUEUED_REQUESTS.set(len(self.waiters))
                elif fut.done() and not fut.cancelled():
                    self.release()      # slot was handed to us but we went away
                raise
        else:
            self.busy += 1
        BUSY_WORKERS.set(self.busy)
        waited = time.perf_counter() - start
        QUEUE_WAIT_SECONDS.observe(waited)
        return waited

    def release(self) -> None:
        self.busy -= 1
        self._wake()

    def _wake(self) -> None:
        while self.waiters and self._has_room():
            fut = self.waiters.pop(0)
            if not fut.done():
                self.busy += 1
                fut.set_result(None)
        QUEUED_REQUESTS.set(len(self.waiters))
        BUSY_WORKERS.set(self.busy)

    def resize(self, slots: int) -> None:
        self.slots = slots
        WORKER_SLOTS.set(slots)
        self._wake()


POOL = WorkerPool(int(os.getenv("APP_WORKER_SLOTS", "0")))
WORKER_SLOTS.set(POOL.slots)
LIMITED_PATHS = ("/api/order", "/api/slow", "/api/error")


@app.middleware("http")
async def prometheus_middleware(request: Request, call_next):
    if request.url.path == "/metrics":
        return await call_next(request)
    if request.url.path.startswith(LIMITED_PATHS):
        # Measure from arrival, so queue wait counts toward response time.
        start = time.time()
        await POOL.acquire()
        try:
            return await _instrumented(request, call_next, start)
        finally:
            POOL.release()
    return await _instrumented(request, call_next, time.time())


async def _instrumented(request: Request, call_next, start_time: float):
    endpoint = request.url.path

    # Normalize endpoint name to prevent metric cardinality explosion
    if endpoint.startswith("/api/"):
        endpoint_label = endpoint
    else:
        endpoint_label = "/"

    REQUESTS_IN_FLIGHT.inc()
    APP_UPTIME_SECONDS.set(round(time.time() - START_TIME, 1))

    # Track approximate request size
    content_length = request.headers.get("content-length")
    req_size = int(content_length) if content_length else 120
    REQUEST_SIZE_BYTES.labels(endpoint=endpoint_label).observe(req_size)

    try:
        response = await call_next(request)
        duration = time.time() - start_time
        REQUESTS_TOTAL.labels(
            method=request.method,
            endpoint=endpoint_label,
            status_code=str(response.status_code)
        ).inc()
        REQUEST_DURATION_SECONDS.labels(endpoint=endpoint_label).observe(duration)
        
        # Track response size
        resp_len = response.headers.get("content-length")
        resp_size = int(resp_len) if resp_len else 250
        RESPONSE_SIZE_BYTES.labels(endpoint=endpoint_label).observe(resp_size)

        return response
    except Exception as exc:
        duration = time.time() - start_time
        REQUESTS_TOTAL.labels(
            method=request.method,
            endpoint=endpoint_label,
            status_code="500"
        ).inc()
        REQUEST_DURATION_SECONDS.labels(endpoint=endpoint_label).observe(duration)
        raise exc
    finally:
        REQUESTS_IN_FLIGHT.dec()


@app.get("/metrics")
def get_metrics():
    """Prometheus scrape endpoint."""
    APP_UPTIME_SECONDS.set(round(time.time() - START_TIME, 1))
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "uptime_seconds": round(time.time() - START_TIME, 1)}


class CapacityRequest(BaseModel):
    worker_slots: int = Field(ge=0, le=1000)


@app.get("/api/admin/capacity")
def get_capacity():
    """Current worker-slot limit and how busy the app is."""
    return {"worker_slots": POOL.slots, "busy": POOL.busy, "queued": len(POOL.waiters)}


@app.post("/api/admin/capacity")
def set_capacity(req: CapacityRequest):
    """Resize the worker pool live (0 = unlimited)."""
    POOL.resize(req.worker_slots)
    return get_capacity()


@app.get("/api/order")
async def create_order(tier: str = "standard"):
    """A fast, successful order: ~5–25 ms of simulated work (200 OK)."""
    await asyncio.sleep(random.uniform(0.005, 0.025))
    ORDERS_COUNTER.labels(tier=tier).inc()
    
    tier_values = {"gold": 150.0, "silver": 75.0, "bronze": 35.0, "standard": 20.0}
    ORDERS_VALUE_COUNTER.labels(tier=tier).inc(tier_values.get(tier, 20.0))

    # Slight dynamic jitter in simulated memory and active users
    ACTIVE_USERS.set(max(5, min(80, 24 + random.randint(-6, 8))))
    MEMORY_USAGE_GAUGE.set(round(36.0 + random.uniform(0.5, 4.0), 1))

    return {
        "success": True,
        "order_id": f"ORD-{random.randint(10000, 99999)}",
        "tier": tier,
        "revenue_usd": tier_values.get(tier, 20.0),
        "message": "Order processed in ~15 ms"
    }


@app.get("/api/slow")
async def slow_query(delay: float = 0.8):
    """A slow database query or external API call: `delay` + 50–250 ms."""
    actual_delay = min(max(delay, 0.2), 3.0) + random.uniform(0.05, 0.25)
    await asyncio.sleep(actual_delay)
    return {
        "success": True,
        "duration_seconds": round(actual_delay, 3),
        "message": "Latency spike simulated successfully"
    }


@app.get("/api/error")
async def simulate_error():
    """An intentional server error (500) for testing alerts."""
    await asyncio.sleep(random.uniform(0.002, 0.005))
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "SimulatedDatabaseConnectionTimeout",
            "message": "Simulated 500 server error triggered for monitoring alerts"
        }
    )


STATIC = Path(__file__).parent / "static"


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
