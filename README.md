# Ultra-Low Resource Monitoring Stack 🚀

A complete, production-grade monitoring stack designed specifically for **local laptop development** with an ultra-lean memory footprint (~120 MB total RAM).

---

## 📊 Resource Consumption Comparison

| Component | OpenTelemetry Astronomy Demo | **This Lightweight Stack** |
| :--- | :--- | :--- |
| **Number of Pods** | 34 pods (Java, Kafka, OpenSearch, etc.) | **3 pods** (App + Prometheus + Grafana) |
| **Total Memory (RAM)** | ~12,000 MB - 16,000 MB | **~120 MB total** (99% reduction!) |
| **CPU Consumption** | 4 - 8 CPU Cores | **< 0.05 CPU Cores** |
| **Local Laptop Heat / Battery** | Extreme fan noise & battery drain | Silent, zero laptop battery impact |

### Live Measured Container Stats:
- **Grafana OSS**: ~56 MB RAM (0.06% CPU)
- **FastAPI App**: ~36 MB RAM (0.26% CPU)
- **Prometheus**: ~28 MB RAM (0.96% CPU)

---

## ⚡ One-Line Deploy (Helm)

Anyone with a Kubernetes cluster can deploy the entire stack in one command:

```bash
# Add the Helm repo
helm repo add lightweight-monitoring https://hrushikeshkuwlekar.github.io/lightweight-monitoring-stack/
helm repo update

# Install the full stack
helm install demo lightweight-monitoring/lightweight-monitoring-stack -n monitoring --create-namespace
```

Then port-forward to access the services:
```bash
kubectl port-forward svc/demo-app-svc    -n monitoring 8000:8000 &
kubectl port-forward svc/grafana-svc      -n monitoring 3000:3000 &
kubectl port-forward svc/prometheus-svc   -n monitoring 9090:9090 &
```

### Customize with values:
```bash
helm install demo lightweight-monitoring/lightweight-monitoring-stack \
  --set demoApp.replicaCount=2 \
  --set grafana.enabled=false \
  -n monitoring --create-namespace
```

### Uninstall:
```bash
helm uninstall demo -n monitoring
kubectl delete namespace monitoring
```

---

## 🐳 Docker Image

Pre-built multi-arch images (amd64 + arm64) are available on GitHub Container Registry:

```bash
docker pull ghcr.io/hrushikeshkuwlekar/lightweight-monitoring-stack:latest
```

---

## 🌐 Quick Access URLs

| Service | Local URL | Description |
| :--- | :--- | :--- |
| **Interactive Demo App** | [http://localhost:8000](http://localhost:8000) | Web UI to trigger traffic, errors, slow queries, and auto-traffic |
| **Grafana Dashboard** | [http://localhost:3000](http://localhost:3000) | Live RED metrics (Anonymous Admin access enabled, no password needed) |
| **Prometheus UI** | [http://localhost:9090](http://localhost:9090) | Scrape targets, PromQL query explorer, and alert rules |
| **Metrics Endpoint** | [http://localhost:8000/metrics](http://localhost:8000/metrics) | Standard Prometheus scrape target |

---

## 🛠️ How It Works

1. **Demo Application (`app/main.py`)**:
   - Built on FastAPI with `prometheus-client`.
   - Emits golden RED metrics:
     - `http_requests_total{method, endpoint, status_code}`
     - `http_request_duration_seconds` (histogram with p50, p95 percentiles)
     - `app_active_connections` (gauge)
     - `app_simulated_memory_mb` (gauge)
     - `app_business_orders_total` (counter)
   - Contains a background traffic generator that automatically produces realistic wave traffic.

2. **Ultra-Low Memory Prometheus (`k8s/02-prometheus.yaml`)**:
   - Configured with low-retention flags:
     - `--storage.tsdb.retention.time=2h`
     - `--storage.tsdb.retention.size=100MB`
     - `--storage.tsdb.min-block-duration=2h`
     - `--storage.tsdb.max-block-duration=2h`
     - Scrape interval: 5 seconds.
   - Includes alerts for high error rate (>10%) and latency spikes (>800ms).

3. **Pre-Provisioned Grafana (`k8s/03-grafana.yaml`)**:
   - Zero-setup experience: Automatically connects to Prometheus.
   - Automatically provisions the **"App Performance & Health"** dashboard.
   - Configured with anonymous admin authentication (no login screen).

---

## 📂 Project Structure

```
.
├── app/                          # FastAPI demo application
│   ├── Dockerfile                # Multi-stage Docker build
│   ├── main.py                   # Application source code
│   └── requirements.txt          # Python dependencies
├── helm/                         # ⭐ Helm chart (publishable)
│   └── lightweight-monitoring-stack/
│       ├── Chart.yaml            # Chart metadata
│       ├── values.yaml           # Default configuration values
│       └── templates/            # Kubernetes manifest templates
├── k8s/                          # Raw Kubernetes manifests (alternative)
│   ├── 00-namespace.yaml
│   ├── 01-app.yaml
│   ├── 02-prometheus.yaml
│   └── 03-grafana.yaml
├── .github/workflows/
│   └── release.yml               # CI/CD: Docker → GHCR, Helm → GitHub Pages
├── docker-compose.yml            # Docker Compose alternative
└── Makefile                      # Convenience targets
```

---

## ⚡ Alternative Deployment Methods

### Raw Kubernetes Manifests
```bash
kubectl apply -f k8s/
```

### Docker Compose
```bash
docker compose up -d
docker compose down
```

### Make Targets
```bash
make up        # Apply Kubernetes manifests
make down      # Tear down the stack
make status    # Check pod/service status
make stats     # Show live memory & CPU
make simulate  # Toggle background traffic generator
make logs      # Stream pod logs
```

---

## 🔄 CI/CD Pipeline

On every push to `main` or tag creation (`v*`), GitHub Actions automatically:

1. **Builds** a multi-arch Docker image (amd64 + arm64)
2. **Pushes** to `ghcr.io/hrushikeshkuwlekar/lightweight-monitoring-stack`
3. **Packages** the Helm chart
4. **Publishes** the chart to GitHub Pages (`gh-pages` branch)

---

## 📜 License

MIT
