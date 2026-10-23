import time
import random
import threading
from fastapi import FastAPI, Response, Request
from fastapi.responses import HTMLResponse, JSONResponse
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST
)

app = FastAPI(title="Lightweight Demo App with Monitoring")

# Prometheus Metrics
REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests received",
    ["method", "endpoint", "status_code"]
)

REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0]
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

# Initialize Gauges
ACTIVE_USERS.set(12)
MEMORY_USAGE_GAUGE.set(38.5)

# Background traffic simulator state
simulator_running = False
simulator_stats = {"total_sent": 0, "errors_sent": 0, "slow_sent": 0}

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

    try:
        response = await call_next(request)
        duration = time.time() - start_time
        REQUESTS_TOTAL.labels(
            method=request.method,
            endpoint=endpoint_label,
            status_code=str(response.status_code)
        ).inc()
        REQUEST_DURATION_SECONDS.labels(endpoint=endpoint_label).observe(duration)
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

@app.get("/metrics")
def get_metrics():
    """Prometheus scrape endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/healthz")
def healthz():
    return {"status": "ok", "timestamp": time.time()}

@app.get("/api/order")
def create_order(tier: str = "standard"):
    """Simulates a fast, successful order placement (200 OK)."""
    ORDERS_COUNTER.labels(tier=tier).inc()
    # Random slight fluctuation in active users
    ACTIVE_USERS.set(max(5, min(50, 20 + random.randint(-4, 6))))
    return {
        "success": True,
        "order_id": f"ORD-{random.randint(10000, 99999)}",
        "tier": tier,
        "message": "Order processed successfully in <20ms"
    }

@app.get("/api/slow")
def slow_query(delay: float = 0.8):
    """Simulates a slow database query or external API call."""
    actual_delay = min(max(delay, 0.2), 3.0) + random.uniform(0.05, 0.3)
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

# Background traffic simulator loop
def traffic_worker():
    import urllib.request
    global simulator_running
    while simulator_running:
        try:
            choice = random.random()
            if choice < 0.75:
                # 75% standard fast order
                urllib.request.urlopen("http://127.0.0.1:8000/api/order?tier=gold", timeout=2)
            elif choice < 0.88:
                # 13% slow query
                urllib.request.urlopen("http://127.0.0.1:8000/api/slow?delay=0.6", timeout=3)
                simulator_stats["slow_sent"] += 1
            elif choice < 0.95:
                # 7% simulated error
                try:
                    urllib.request.urlopen("http://127.0.0.1:8000/api/error", timeout=2)
                except Exception:
                    simulator_stats["errors_sent"] += 1
            else:
                # 5% standard fast order
                urllib.request.urlopen("http://127.0.0.1:8000/api/order?tier=silver", timeout=2)

            simulator_stats["total_sent"] += 1
        except Exception:
            pass
        time.sleep(random.uniform(0.3, 0.8))

@app.post("/api/simulator/toggle")
def toggle_simulator():
    global simulator_running
    simulator_running = not simulator_running
    if simulator_running:
        t = threading.Thread(target=traffic_worker, daemon=True)
        t.start()
    return {"running": simulator_running, "stats": simulator_stats}

@app.get("/api/simulator/status")
def simulator_status():
    return {"running": simulator_running, "stats": simulator_stats}

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
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #090d16;
      --card-bg: rgba(18, 24, 38, 0.7);
      --border: rgba(255, 255, 255, 0.08);
      --primary: #3b82f6;
      --primary-glow: rgba(59, 130, 246, 0.35);
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --purple: #8b5cf6;
      --text: #f3f4f6;
      --text-muted: #9ca3af;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: 'Plus Jakarta Sans', sans-serif;
      background: radial-gradient(circle at 50% 0%, #151e36 0%, var(--bg) 60%);
      color: var(--text);
      min-height: 100vh;
      padding: 2rem 1rem;
      display: flex;
      flex-direction: column;
      align-items: center;
    }

    .container {
      width: 100%;
      max-width: 960px;
      display: flex;
      flex-direction: column;
      gap: 1.5rem;
    }

    header {
      text-align: center;
      padding: 1rem 0;
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
      font-size: 2.25rem;
      font-weight: 800;
      letter-spacing: -0.03em;
      background: linear-gradient(135deg, #ffffff 30%, #93c5fd 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      margin-bottom: 0.5rem;
    }

    p.subtitle {
      color: var(--text-muted);
      font-size: 1rem;
    }

    .grid-2 {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 1rem;
    }

    .card {
      background: var(--card-bg);
      backdrop-filter: blur(12px);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 1.5rem;
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.25);
      transition: transform 0.2s, border-color 0.2s;
    }
    .card:hover {
      border-color: rgba(255, 255, 255, 0.15);
    }

    .card-title {
      font-size: 1.05rem;
      font-weight: 700;
      margin-bottom: 0.25rem;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }

    .card-desc {
      font-size: 0.85rem;
      color: var(--text-muted);
      margin-bottom: 1.25rem;
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
      padding: 0.65rem 1.15rem;
      border-radius: 10px;
      border: 1px solid transparent;
      cursor: pointer;
      transition: all 0.2s ease;
      text-decoration: none;
    }

    button:active, .link-btn:active {
      transform: scale(0.98);
    }

    .btn-primary {
      background: #2563eb;
      color: #ffffff;
      box-shadow: 0 4px 14px var(--primary-glow);
    }
    .btn-primary:hover {
      background: #1d4ed8;
      box-shadow: 0 6px 20px var(--primary-glow);
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

    .btn-simulator {
      background: linear-gradient(135deg, #8b5cf6, #ec4899);
      color: #ffffff;
      width: 100%;
      padding: 0.85rem;
      font-size: 0.95rem;
      box-shadow: 0 4px 16px rgba(139, 92, 246, 0.35);
    }
    .btn-simulator.active {
      background: linear-gradient(135deg, #ef4444, #dc2626);
      box-shadow: 0 4px 16px rgba(239, 68, 68, 0.35);
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

    .stats-row {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 0.5rem;
      margin-top: 0.75rem;
      text-align: center;
    }
    .stat-card {
      background: rgba(255, 255, 255, 0.03);
      padding: 0.5rem;
      border-radius: 8px;
      border: 1px solid var(--border);
    }
    .stat-val {
      font-size: 1.15rem;
      font-weight: 700;
      color: #ffffff;
    }
    .stat-lbl {
      font-size: 0.7rem;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }

    .links-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 1rem;
    }

    .link-tile {
      display: flex;
      align-items: center;
      gap: 1rem;
      background: rgba(255, 255, 255, 0.02);
      border: 1px solid var(--border);
      padding: 1rem;
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
      font-size: 1.75rem;
    }
    .link-tile .title {
      font-weight: 700;
      font-size: 0.95rem;
      color: #ffffff;
    }
    .link-tile .url {
      font-size: 0.75rem;
      color: var(--text-muted);
      font-family: 'JetBrains Mono', monospace;
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="badge-bar">
        <span class="pulse"></span> Ultra-Low Resource Profile: ~180MB RAM Total
      </div>
      <h1>Monitoring Demo Application</h1>
      <p class="subtitle">FastAPI + Prometheus Engine + Pre-Provisioned Grafana</p>
    </header>

    <!-- Top Navigation Links -->
    <div class="card">
      <div class="card-title">🚀 Monitoring Stack Endpoints</div>
      <div class="card-desc">Click below to jump directly into the pre-configured monitoring UIs:</div>
      <div class="links-grid">
        <a href="http://localhost:3000" target="_blank" class="link-tile" id="grafana-link">
          <div class="icon">📊</div>
          <div>
            <div class="title">Grafana Dashboard</div>
            <div class="url">http://localhost:3000</div>
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
          <div class="icon">📈</div>
          <div>
            <div class="title">Raw App Metrics</div>
            <div class="url">/metrics</div>
          </div>
        </a>
      </div>
    </div>

    <!-- Live Traffic & Alert Trigger Controls -->
    <div class="grid-2">
      <!-- Manual Trigger Card -->
      <div class="card">
        <div class="card-title">⚡ Interactive Request Triggers</div>
        <div class="card-desc">Fire custom traffic to see Prometheus metrics & Grafana charts react in real time:</div>
        
        <div class="btn-group">
          <button class="btn-success" onclick="fireReq('/api/order', 'GET')">
            <span>🛒</span> 200 OK Order
          </button>
          <button class="btn-warning" onclick="fireReq('/api/slow', 'GET')">
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

      <!-- Automated Simulator Card -->
      <div class="card">
        <div class="card-title">🤖 Auto-Traffic Generator</div>
        <div class="card-desc">Generates realistic undulating traffic in the background so Grafana graphs stay active:</div>
        
        <button id="simBtn" class="btn-simulator" onclick="toggleSim()">
          ▶ Start Traffic Simulator
        </button>

        <div class="stats-row">
          <div class="stat-card">
            <div class="stat-val" id="statTotal">0</div>
            <div class="stat-lbl">Total Sent</div>
          </div>
          <div class="stat-card">
            <div class="stat-val" id="statSlow" style="color: #fbbf24;">0</div>
            <div class="stat-lbl">Slow Queries</div>
          </div>
          <div class="stat-card">
            <div class="stat-val" id="statErrors" style="color: #f87171;">0</div>
            <div class="stat-lbl">Errors</div>
          </div>
        </div>
      </div>
    </div>

    <!-- Resource Comparison Banner -->
    <div class="card" style="border-color: rgba(16, 185, 129, 0.3); background: rgba(16, 185, 129, 0.04);">
      <div class="card-title" style="color: #34d399;">💡 Resource Efficiency Breakdown</div>
      <div style="font-size: 0.85rem; color: #d1d5db; line-height: 1.6; margin-top: 0.5rem;">
        • <strong>This Lightweight Stack:</strong> 3 pods (~180 MB RAM total, &lt; 0.05 CPU cores idle). Designed specifically to be featherweight on laptops.<br>
        • <strong>OpenTelemetry Demo:</strong> 34 pods (&gt; 12,000 MB RAM, Java/Kafka/OpenSearch, 4+ CPU cores).
      </div>
    </div>
  </div>

  <script>
    const logBox = document.getElementById('logBox');
    const simBtn = document.getElementById('simBtn');

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

    async function toggleSim() {
      try {
        const res = await fetch('/api/simulator/toggle', { method: 'POST' });
        const data = await res.json();
        updateSimState(data.running);
      } catch (e) {
        console.error(e);
      }
    }

    function updateSimState(running) {
      if (running) {
        simBtn.innerText = '⏹ Stop Traffic Simulator';
        simBtn.className = 'btn-simulator active';
        log('<span style="color: #a78bfa;">▶ Background traffic generator started</span>');
      } else {
        simBtn.innerText = '▶ Start Traffic Simulator';
        simBtn.className = 'btn-simulator';
        log('<span style="color: #9ca3af;">⏹ Background traffic generator stopped</span>');
      }
    }

    // Poll simulator stats every 1.5s
    setInterval(async () => {
      try {
        const res = await fetch('/api/simulator/status');
        const data = await res.json();
        document.getElementById('statTotal').innerText = data.stats.total_sent;
        document.getElementById('statSlow').innerText = data.stats.slow_sent;
        document.getElementById('statErrors').innerText = data.stats.errors_sent;
        if (data.running && simBtn.innerText.includes('Start')) {
          updateSimState(true);
        }
      } catch (e) {}
    }, 1500);
  </script>
</body>
</html>
"""
