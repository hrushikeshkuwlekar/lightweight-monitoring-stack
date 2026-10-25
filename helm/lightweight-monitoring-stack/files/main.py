import time
import random
from fastapi import FastAPI, Response, Request
from fastapi.responses import HTMLResponse, JSONResponse
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
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.8, 1.0, 2.0, 5.0, 10.0]
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

# Initialize Gauges
ACTIVE_USERS.set(12)
MEMORY_USAGE_GAUGE.set(38.5)
REQUESTS_IN_FLIGHT.set(0)
APP_INFO.labels(version="1.1.0", framework="FastAPI", architecture="lightweight").set(1)


@app.middleware("http")
async def prometheus_middleware(request: Request, call_next):
    if request.url.path == "/metrics":
        return await call_next(request)

    start_time = time.time()
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


@app.get("/api/order")
def create_order(tier: str = "standard"):
    """Simulates a fast, successful order placement (200 OK)."""
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
        "message": "Order processed successfully in <20ms"
    }


@app.get("/api/slow")
def slow_query(delay: float = 0.8):
    """Simulates a slow database query or external API call."""
    actual_delay = min(max(delay, 0.2), 3.0) + random.uniform(0.05, 0.25)
    time.sleep(actual_delay)
    return {
        "success": True,
        "duration_seconds": round(actual_delay, 3),
        "message": "Latency spike simulated successfully"
    }


@app.get("/api/error")
def simulate_error():
    """Simulates an internal server failure (500)."""
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "SimulatedDatabaseConnectionTimeout",
            "message": "Simulated 500 server error triggered for monitoring alerts"
        }
    )


@app.get("/", response_class=HTMLResponse)
def index():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Lightweight App & Monitoring Stack</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #090d16;
      --card-bg: rgba(18, 24, 38, 0.75);
      --border: rgba(255, 255, 255, 0.08);
      --primary: #3b82f6;
      --primary-glow: rgba(59, 130, 246, 0.35);
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --purple: #8b5cf6;
      --cyan: #06b6d4;
      --text: #f3f4f6;
      --text-muted: #9ca3af;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: 'Plus Jakarta Sans', sans-serif;
      background: radial-gradient(circle at 50% 0%, #151e36 0%, var(--bg) 65%);
      color: var(--text);
      min-height: 100vh;
      padding: 2rem 1rem;
      display: flex;
      flex-direction: column;
      align-items: center;
    }

    .container {
      width: 100%;
      max-width: 1020px;
      display: flex;
      flex-direction: column;
      gap: 1.5rem;
    }

    header {
      text-align: center;
      padding: 0.75rem 0;
    }

    .badge-bar {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      background: rgba(59, 130, 246, 0.12);
      border: 1px solid rgba(59, 130, 246, 0.3);
      padding: 0.35rem 0.85rem;
      border-radius: 9999px;
      font-size: 0.8rem;
      color: #93c5fd;
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
    @keyframes pulseAnim {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.4; transform: scale(0.85); }
    }

    h1 {
      font-size: 2.3rem;
      font-weight: 800;
      letter-spacing: -0.03em;
      background: linear-gradient(135deg, #ffffff 30%, #93c5fd 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      margin-bottom: 0.4rem;
    }

    p.subtitle {
      color: var(--text-muted);
      font-size: 1rem;
    }

    .grid-2 {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 1.25rem;
    }

    .card {
      background: var(--card-bg);
      backdrop-filter: blur(14px);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 1.5rem;
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
      transition: transform 0.2s, border-color 0.2s;
    }
    .card:hover {
      border-color: rgba(255, 255, 255, 0.15);
    }

    .card-title {
      font-size: 1.1rem;
      font-weight: 700;
      margin-bottom: 0.3rem;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }

    .card-desc {
      font-size: 0.85rem;
      color: var(--text-muted);
      margin-bottom: 1.25rem;
      line-height: 1.4;
    }

    .btn-group {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
    }

    button, .link-btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 0.5rem;
      font-family: inherit;
      font-weight: 600;
      font-size: 0.875rem;
      padding: 0.7rem 1.15rem;
      border-radius: 10px;
      border: 1px solid transparent;
      cursor: pointer;
      transition: all 0.2s ease;
      text-decoration: none;
    }

    button:active, .link-btn:active {
      transform: scale(0.98);
    }

    .btn-success {
      background: rgba(16, 185, 129, 0.15);
      border-color: rgba(16, 185, 129, 0.4);
      color: #34d399;
    }
    .btn-success:hover {
      background: rgba(16, 185, 129, 0.25);
    }

    .btn-warning {
      background: rgba(245, 158, 11, 0.15);
      border-color: rgba(245, 158, 11, 0.4);
      color: #fbbf24;
    }
    .btn-warning:hover {
      background: rgba(245, 158, 11, 0.25);
    }

    .btn-danger {
      background: rgba(239, 68, 68, 0.15);
      border-color: rgba(239, 68, 68, 0.4);
      color: #f87171;
    }
    .btn-danger:hover {
      background: rgba(239, 68, 68, 0.25);
    }

    .log-box {
      background: #050811;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 0.75rem 1rem;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.78rem;
      color: #a5b4fc;
      max-height: 140px;
      overflow-y: auto;
      margin-top: 1rem;
    }

    .links-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 0.85rem;
    }

    .link-tile {
      display: flex;
      align-items: center;
      gap: 0.85rem;
      background: rgba(255, 255, 255, 0.02);
      border: 1px solid var(--border);
      padding: 0.9rem 1rem;
      border-radius: 12px;
      text-decoration: none;
      color: inherit;
      transition: all 0.2s;
    }
    .link-tile:hover {
      background: rgba(255, 255, 255, 0.05);
      border-color: rgba(59, 130, 246, 0.5);
      transform: translateY(-2px);
    }
    .link-tile .icon {
      font-size: 1.6rem;
    }
    .link-tile .title {
      font-weight: 700;
      font-size: 0.88rem;
      color: #ffffff;
    }
    .link-tile .url {
      font-size: 0.72rem;
      color: var(--text-muted);
      font-family: 'JetBrains Mono', monospace;
    }

    .loadgen-cta {
      background: linear-gradient(135deg, rgba(99, 102, 241, 0.15), rgba(139, 92, 246, 0.15));
      border: 1px solid rgba(99, 102, 241, 0.35);
      border-radius: 14px;
      padding: 1.25rem;
      display: flex;
      flex-direction: column;
      gap: 0.85rem;
    }
    .btn-launch-loadgen {
      background: linear-gradient(135deg, #6366f1, #8b5cf6);
      color: #ffffff;
      text-decoration: none;
      font-weight: 700;
      padding: 0.85rem 1.25rem;
      border-radius: 10px;
      text-align: center;
      box-shadow: 0 4px 15px rgba(99, 102, 241, 0.4);
      transition: all 0.2s;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 0.5rem;
    }
    .btn-launch-loadgen:hover {
      background: linear-gradient(135deg, #4f46e5, #7c3aed);
      transform: translateY(-1px);
    }

    .mini-scenarios {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 0.5rem;
    }
    .mini-sc-btn {
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.6rem 0.4rem;
      text-align: center;
      cursor: pointer;
      font-size: 0.75rem;
      font-weight: 600;
      color: #e2e8f0;
      transition: all 0.2s;
      text-decoration: none;
      display: block;
    }
    .mini-sc-btn:hover {
      background: rgba(99, 102, 241, 0.2);
      border-color: rgba(99, 102, 241, 0.5);
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="badge-bar">
        <span class="pulse"></span> Production-Grade Monitoring & Load Testing Stack
      </div>
      <h1>Lightweight Monitoring Demo App</h1>
      <p class="subtitle">FastAPI Core + Prometheus Engine + Grafana TPS Telemetry + Async Load Generator</p>
    </header>

    <!-- Top Navigation Links -->
    <div class="card">
      <div class="card-title">🚀 Monitoring Stack Endpoints</div>
      <div class="card-desc">Click below to jump directly into the pre-configured monitoring and testing UIs:</div>
      <div class="links-grid">
        <a href="http://localhost:3000" target="_blank" class="link-tile" id="grafana-app-link">
          <div class="icon">📈</div>
          <div>
            <div class="title">App Performance</div>
            <div class="url">Grafana Dashboard</div>
          </div>
        </a>
        <a href="http://localhost:3000/d/loadgen-analytics" target="_blank" class="link-tile" id="grafana-tps-link">
          <div class="icon">📊</div>
          <div>
            <div class="title">Load & TPS Analytics</div>
            <div class="url">Grafana Dashboard</div>
          </div>
        </a>
        <a href="http://localhost:8089" target="_blank" class="link-tile" id="loadgen-ui-link">
          <div class="icon">🎛️</div>
          <div>
            <div class="title">Load Generator UI</div>
            <div class="url">http://localhost:8089</div>
          </div>
        </a>
        <a href="http://localhost:9090" target="_blank" class="link-tile" id="prom-link">
          <div class="icon">🔥</div>
          <div>
            <div class="title">Prometheus UI</div>
            <div class="url">http://localhost:9090</div>
          </div>
        </a>
        <a href="/metrics" target="_blank" class="link-tile" id="metrics-link">
          <div class="icon">📋</div>
          <div>
            <div class="title">App Metrics</div>
            <div class="url">/metrics (:8000)</div>
          </div>
        </a>
      </div>
    </div>

    <!-- Live Traffic & Alert Trigger Controls -->
    <div class="grid-2">
      <!-- Manual Trigger Card -->
      <div class="card">
        <div class="card-title">⚡ Interactive Request Triggers</div>
        <div class="card-desc">Fire custom traffic directly to inspect Prometheus metrics & Grafana charts in real time:</div>
        
        <div class="btn-group">
          <button class="btn-success" onclick="fireReq('/api/order?tier=gold', 'GET')">
            <span>🛒</span> 200 OK Order
          </button>
          <button class="btn-warning" onclick="fireReq('/api/slow?delay=0.8', 'GET')">
            <span>⏳</span> Slow Latency (1s)
          </button>
          <button class="btn-danger" onclick="fireReq('/api/error', 'GET')">
            <span>💥</span> 500 Error Spike
          </button>
        </div>

        <div class="log-box" id="logBox">
          [System Ready] Click a button above to send requests...
        </div>
      </div>

      <!-- Dedicated Load Generator Card -->
      <div class="card">
        <div class="card-title">🤖 Dedicated Load Generator Module</div>
        <div class="card-desc">
          High-performance, async traffic generator running in an isolated container to keep demo metrics unpolluted:
        </div>

        <div class="loadgen-cta">
          <a href="http://localhost:8089" target="_blank" class="btn-launch-loadgen">
            <span>🎛️</span> Open Load Generator Control Panel
          </a>
          <div style="font-size: 0.78rem; color: #cbd5e1; text-align: center;">
            Control target RPS (1–250), Concurrency (1–50 VU), ramp curves, and traffic mix.
          </div>
          <div class="mini-scenarios">
            <a href="http://localhost:8089" target="_blank" class="mini-sc-btn">🟢 Steady State</a>
            <a href="http://localhost:8089" target="_blank" class="mini-sc-btn">📈 Ramp-Up</a>
            <a href="http://localhost:8089" target="_blank" class="mini-sc-btn">⚡ Spike Test</a>
          </div>
        </div>
      </div>
    </div>

    <!-- Resource Comparison Banner -->
    <div class="card" style="border-color: rgba(16, 185, 129, 0.3); background: rgba(16, 185, 129, 0.04);">
      <div class="card-title" style="color: #34d399;">💡 Resource Efficiency Breakdown</div>
      <div style="font-size: 0.85rem; color: #d1d5db; line-height: 1.6; margin-top: 0.5rem;">
        • <strong>This Lightweight Stack:</strong> 4 pods (~220 MB RAM total, &lt; 0.08 CPU cores idle). Designed specifically to be featherweight on laptops.<br>
        • <strong>OpenTelemetry Demo:</strong> 34 pods (&gt; 12,000 MB RAM, Java/Kafka/OpenSearch, 4+ CPU cores).
      </div>
    </div>
  </div>

  <script>
    const logBox = document.getElementById('logBox');

    function log(msg) {
      const now = new Date().toLocaleTimeString();
      logBox.innerHTML = `[${now}] ${msg}<br>` + logBox.innerHTML;
    }

    async function fireReq(endpoint, method) {
      try {
        const start = performance.now();
        const res = await fetch(endpoint, { method });
        const dur = (performance.now() - start).toFixed(0);
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          log(`<span style="color: #34d399;">✔ ${res.status} OK</span> ${endpoint} (${dur}ms)`);
        } else {
          log(`<span style="color: #f87171;">✖ ${res.status} Error</span> ${endpoint} (${dur}ms) - ${data.error || 'Server Error'}`);
        }
      } catch (err) {
        log(`<span style="color: #f87171;">Network Error:</span> ${err.message}`);
      }
    }
  </script>
</body>
</html>
"""
