# Ultra-Low Resource Monitoring Stack 🚀

A complete, production-grade monitoring and load testing stack designed specifically for **local laptop development** with an ultra-lean memory footprint (~220 MB total RAM across all 4 services).

Now featuring a **dedicated asynchronous Load Generator module** with live TPS (Transactions Per Second) measurement, concurrency control, 7 pre-built traffic scenarios, and pre-provisioned Grafana dashboards.

---

## 📊 Resource Consumption Comparison

| Component | OpenTelemetry Astronomy Demo | **This Lightweight Stack** |
| :--- | :--- | :--- |
| **Number of Pods** | 34 pods (Java, Kafka, OpenSearch, etc.) | **4 pods** (App + LoadGen + Prometheus + Grafana) |
| **Total Memory (RAM)** | ~12,000 MB - 16,000 MB | **~220 MB total** (98% reduction!) |
| **CPU Consumption** | 4 - 8 CPU Cores | **< 0.08 CPU Cores** |
| **Local Laptop Heat / Battery** | Extreme fan noise & battery drain | Silent, zero laptop battery impact |

### Live Measured Container Stats:
- **Grafana OSS**: ~56 MB RAM (0.06% CPU)
- **Load Generator (async)**: ~45 MB RAM (0.35% CPU under 50 RPS load)
- **FastAPI Demo App**: ~38 MB RAM (0.28% CPU)
- **Prometheus**: ~30 MB RAM (0.96% CPU)

---

## ⚡ One-Line Deploy (Helm)

Anyone with a Kubernetes cluster can deploy the entire stack in one command:

```bash
# Add the Helm repo
helm repo add lightweight-monitoring https://hrushikeshkuwlekar.github.io/lightweight-monitoring-stack/
helm repo update

# Install the full stack (including Load Generator)
helm install demo lightweight-monitoring/lightweight-monitoring-stack -n monitoring --create-namespace
```

Then port-forward to access the services:
```bash
kubectl port-forward svc/demo-app-svc       -n monitoring 8000:8000 &
kubectl port-forward svc/load-generator-svc  -n monitoring 8089:8089 &
kubectl port-forward svc/grafana-svc         -n monitoring 3000:3000 &
kubectl port-forward svc/prometheus-svc      -n monitoring 9090:9090 &
```

Or run `make port-forward`!

### Customize with values:
```bash
helm install demo lightweight-monitoring/lightweight-monitoring-stack \
  --set demoApp.replicaCount=2 \
  --set loadGenerator.enabled=true \
  --set grafana.enabled=true \
  -n monitoring --create-namespace
```

### Uninstall:
```bash
helm uninstall demo -n monitoring
kubectl delete namespace monitoring
```

---

## 🐳 Docker Images

Pre-built multi-arch images (amd64 + arm64) are available on GitHub Container Registry:

```bash
# FastAPI Demo App
docker pull ghcr.io/hrushikeshkuwlekar/lightweight-monitoring-stack:latest

# Dedicated Load Generator
docker pull ghcr.io/hrushikeshkuwlekar/lightweight-monitoring-stack-loadgen:latest
```

---

## 🌐 Quick Access URLs

| Service | Local URL | Description |
| :--- | :--- | :--- |
| **Load Generator UI** | [http://localhost:8089](http://localhost:8089) | 🎛️ Dedicated UI for target RPS, concurrency, sliders, 7 test scenarios, live TPS sparkline |
| **Grafana: Load & TPS** | [http://localhost:3000/d/loadgen-analytics](http://localhost:3000/d/loadgen-analytics) | 📊 Dedicated TPS dashboard (Achieved vs Target RPS, Delivery %, Client p95/p99, Error types) |
| **Grafana: App Health** | [http://localhost:3000](http://localhost:3000) | 📈 Server Golden RED metrics (RPS, Error Rate %, In-Flight saturation, P95/P99 latency, USD Revenue) |
| **Interactive Demo App** | [http://localhost:8000](http://localhost:8000) | ⚡ Demo App UI to trigger ad-hoc requests, view stack links, and test endpoints |
| **Prometheus UI** | [http://localhost:9090](http://localhost:9090) | 🔥 Scrape targets, PromQL query explorer, and alert rules |
| **Client Metrics** | [http://localhost:8089/metrics](http://localhost:8089/metrics) | Prometheus scrape endpoint for load generator |
| **App Metrics** | [http://localhost:8000/metrics](http://localhost:8000/metrics) | Prometheus scrape endpoint for demo app |

---

## 🛠️ How It Works

### 1. Dedicated Load Generator (`load-generator/`)
Built with `asyncio` and `aiohttp` for maximum concurrency with negligible memory footprint (~45 MB RAM):
- **Dynamic Rate Limiting**: Inter-arrival scheduling to hit target RPS precisely.
- **Worker Concurrency Limit**: Simulated Virtual Users (VU) governed via `asyncio.Semaphore`.
- **Hot-Updating**: Change target RPS or concurrency mid-test without stopping (`POST /api/load/update`).
- **7 Pre-Built Scenarios**:
  - `steady-state`: Stable 10 RPS, 5 VU, balanced traffic mix.
  - `ramp-up`: Linear 0→50 RPS climb over 2m, hold 3m, ramp down.
  - `spike-test`: Immediate 100 RPS burst to test auto-scaling / alert triggers.
  - `error-storm`: 90% 500-errors at 20 RPS to trip Prometheus alerts.
  - `latency-soak`: High-latency queries to test client p95/p99 degradation.
  - `chaos`: Randomized RPS, endpoint mix, and traffic pauses.
  - `canary-compare`: 50/50 split between fast orders and slow queries.
- **Exported Client Metrics**:
  - `loadgen_requests_total{method, endpoint, status_code, scenario}`
  - `loadgen_request_duration_seconds` (histogram with p50, p90, p95, p99)
  - `loadgen_achieved_rps` (live sliding-window TPS)
  - `loadgen_target_rps` (configured target)
  - `loadgen_concurrent_users` (active workers)
  - `loadgen_requests_in_flight` (pending responses)
  - `loadgen_errors_total{error_type}` (timeout, connection_refused, 4xx, 5xx)
  - `loadgen_bytes_sent_total`, `loadgen_bytes_received_total`

### 2. Demo Application (`app/main.py`)
FastAPI application emitting server-side golden signals:
- `http_requests_total{method, endpoint, status_code}`
- `http_request_duration_seconds` (p50, p95, p99 percentiles)
- `http_requests_in_flight` (saturation gauge)
- `app_business_orders_total{tier}` & `app_orders_value_usd_total{tier}`
- `app_uptime_seconds` & `app_simulated_memory_mb`

### 3. Pre-Provisioned Grafana Dashboards
Zero-setup experience: Automatically connects to Prometheus and loads two dashboards:
1. **"App Performance & Health"** (`/var/lib/grafana/dashboards/demo-app-dashboard.json`)
2. **"Load Generator & TPS Analytics"** (`/var/lib/grafana/dashboards/loadgen-dashboard.json`)

---

## 📂 Project Structure

```
.
├── app/                          # FastAPI demo application
│   ├── Dockerfile                # Multi-stage Docker build
│   ├── main.py                   # Application source code & UI
│   └── requirements.txt          # Python dependencies
├── load-generator/               # ⭐ Standalone Load Generator module
│   ├── Dockerfile                # Container build (:8089)
│   ├── main.py                   # Async load engine, control API & UI
│   ├── scenarios.py              # Pre-built load profiles
│   └── requirements.txt          # aiohttp, fastapi, prometheus-client
├── grafana/                      # Grafana provisioning & dashboards
│   ├── dashboards/
│   │   ├── demo-app-dashboard.json
│   │   └── loadgen-dashboard.json
│   └── provisioning/
│       ├── dashboards/provider.yaml
│       └── datasources/prometheus.yaml
├── prometheus/                   # Prometheus configuration
│   ├── alerts.yml                # Alerting rules (errors, latency, load)
│   └── prometheus.yml            # Scrape targets (demo-app, load-generator)
├── helm/                         # ⭐ Helm chart (publishable)
│   └── lightweight-monitoring-stack/
│       ├── Chart.yaml            # Chart metadata (v0.2.0)
│       ├── values.yaml           # Configuration values
│       └── templates/            # Kubernetes manifest templates
├── k8s/                          # Raw Kubernetes manifests
│   ├── 00-namespace.yaml
│   ├── 01-app.yaml
│   ├── 02-prometheus.yaml
│   ├── 03-grafana.yaml
│   └── 04-load-generator.yaml    # Load generator deployment & service
├── .github/workflows/
│   └── release.yml               # CI/CD: Multi-arch Docker & Helm release
├── docker-compose.yml            # Docker Compose 4-container stack
└── Makefile                      # Convenience targets
```

---

## ⚡ Deployment Methods

### 1. Docker Compose (Quickest Local Run)
```bash
docker compose up -d --build
docker compose down
```

### 2. Raw Kubernetes Manifests
```bash
kubectl apply -f k8s/
make port-forward
```

### 3. Helm Chart
```bash
helm install demo helm/lightweight-monitoring-stack -n monitoring --create-namespace
make port-forward
```

### 4. Make Targets
```bash
make up            # Apply Kubernetes manifests
make down          # Tear down the stack
make status        # Check pod/service status
make stats         # Show live memory & CPU across containers
make simulate      # Trigger steady-state load test (10 RPS)
make spike         # Trigger 100 RPS traffic spike
make stop-load     # Stop active load test
make compose-up    # Run full 4-container stack via Docker Compose
make compose-down  # Stop Docker Compose
make port-forward  # Port-forward all 4 services to localhost
```

---

## 🔄 CI/CD Pipeline

On every push to `main` or tag creation (`v*`), GitHub Actions automatically:
1. **Builds and pushes** multi-arch Docker image for `demo-app`
2. **Builds and pushes** multi-arch Docker image for `load-generator`
3. **Packages** the Helm chart (v0.2.0)
4. **Publishes** the chart to GitHub Pages (`gh-pages` branch)

---

## 📜 License

MIT
