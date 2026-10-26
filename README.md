# Ultra-Low Resource Monitoring Stack 🚀 (v3)

A complete monitoring and load-testing stack for **local laptop development** and small clusters — a FastAPI demo app, a dedicated async load generator, Prometheus, Grafana and kube-state-metrics in **~260 MB of RAM**.

It doubles as a **hands-on performance-testing lab**: one-click scenarios demonstrate Little's Law, Fixed Users vs Fixed Rate load, application bottlenecks and load-generator overload — and the load generator explains, live, which one you're looking at.

Version 3 makes the stack **portable** (tested on kind and k3d in CI and on OrbStack; no distro-specific hand-tuning), **secure by default** (no anonymous admin, non-root, least-privilege RBAC, Pod Security `restricted`), and adds a full **Kubernetes overview dashboard**.

---

## ⚡ Quick Start

```bash
helm repo add lightweight-monitoring https://hrushikeshkuwlekar.github.io/lightweight-monitoring-stack/
helm repo update
```

Pick the install that matches what you want:

```bash
# 1) Laptop demo — no login, LoadBalancer services, CPU/memory node metrics
helm install demo lightweight-monitoring/lightweight-monitoring-stack --set profile=laptop

# 2) Secure defaults — ClusterIP services, Grafana login with a generated password
helm install demo lightweight-monitoring/lightweight-monitoring-stack

# 3) Full Kubernetes visibility — adds a cAdvisor DaemonSet (container CPU, memory, network, throttling)
helm install demo lightweight-monitoring/lightweight-monitoring-stack --set nodeMetrics.mode=daemonset

# Recommended on OrbStack: laptop demo + full container metrics
helm install demo lightweight-monitoring/lightweight-monitoring-stack --set profile=laptop --set nodeMetrics.mode=daemonset
```

The chart creates two namespaces: `app` (demo-app, load-generator) and `monitoring` (Prometheus, Grafana, kube-state-metrics). The install notes print the exact URLs and login for your configuration.

### Grafana login

With `profile=laptop`, Grafana allows anonymous access — no login. Otherwise log in as `admin` with the generated password:

```bash
kubectl get secret grafana-admin -n monitoring -o jsonpath='{.data.admin-password}' | base64 -d; echo
```

The password is generated once and kept across `helm upgrade`. Set your own with `--set grafana.adminPassword=...`.

---

## 🌐 Accessing the Services

**OrbStack** routes cluster service names straight to your Mac — no port-forward, and the URLs keep working through pod restarts and upgrades:

| Service | URL |
| :--- | :--- |
| Grafana | http://grafana-svc.monitoring.svc.cluster.local:3000 |
| Prometheus | http://prometheus-svc.monitoring.svc.cluster.local:9090 |
| Demo App | http://demo-app-svc.app.svc.cluster.local:8000 |
| Load Generator UI | http://load-generator-svc.app.svc.cluster.local:8089 |

**Any cluster** — port-forward to `localhost`:

```bash
make port-forward        # starts all four, records their PIDs
make port-forward-stop   # stops them
```

> [!NOTE]
> `kubectl port-forward svc/...` attaches to **one pod** when it starts. After a `helm upgrade` or pod restart that pod is gone and the forward silently times out — re-run `make port-forward-stop; make port-forward`.

| Service | localhost URL | What you get |
| :--- | :--- | :--- |
| **Load Generator UI** | http://localhost:8089 | 🎛️ Fixed Users / Fixed Rate tests, live diagnosis and Little's Law, 11 one-click scenarios |
| **Grafana** | http://localhost:3000 | 📊 Four pre-provisioned dashboards (below) |
| **Demo App** | http://localhost:8000 | ⚡ Landing page to fire requests manually; metrics at `/metrics` |
| **Prometheus** | http://localhost:9090 | 🔥 Targets, PromQL explorer, alert rules |

---

## 📊 Dashboards

| Dashboard | Path | Shows |
| :--- | :--- | :--- |
| **App Performance & Health** | `/d/demo-app-performance` | Server-side RED metrics, p95 latency, in-flight requests, payload sizes, order revenue |
| **Load Generator & TPS Analytics** | `/d/loadgen-analytics` | Actual vs target TPS, delivery %, client p50–p99 latency, errors by type |
| **Performance Concepts** | `/d/perf-concepts` | Diagnosis over time, Little's Law check, users needed vs allowed, client vs server response time, app queue, generator CPU |
| **Kubernetes / Overview** | `/d/k8s-overview` | Node CPU/memory/disk ratios, namespace & workload counts, per-pod and per-container resources, network bandwidth |

The Kubernetes dashboard is adapted from [starsliao's K8S Dashboard](https://grafana.com/grafana/dashboards/15661) (grafana.com 15661). Its container panels need node metrics — see the next section.

---

## 🔭 Node Metrics Modes

Container-level metrics (`container_cpu_usage_seconds_total`, `container_memory_working_set_bytes`, network, throttling) are opt-in so the default install stays tiny:

| `nodeMetrics.mode` | Source | Gives you | Cost |
| :--- | :--- | :--- | :--- |
| `off` *(default)* | — | App + kube-state-metrics only | — |
| `resource` *(laptop profile)* | kubelet `/metrics/resource` | Container CPU + memory | ~50 series |
| `apiserver` | kubelet + cAdvisor via API-server proxy | Full container metrics | ~35k series on k3s |
| `direct` | kubelet `:10250` directly | Full container metrics | needs node network access |
| `daemonset` | cAdvisor DaemonSet | Full container metrics, network, throttling | +~30 MB; privileged pod |

**OrbStack** (and other clusters whose runtime is Docker 25+): use `daemonset`. Docker's containerd image store can't be read by the kubelet's built-in cAdvisor, so it only reports `machine_*` metrics. The chart ships cAdvisor v0.55 and auto-detects the containerd socket (plain containerd, k3s, or Docker's bundled one); override with `cadvisor.containerdSocket`.

In `daemonset` mode the `monitoring` namespace's Pod Security level is relaxed to `privileged` automatically (cAdvisor needs host-path mounts); the `app` namespace stays `restricted`.

---

## ⚙️ Configuration

The most useful values (full list with comments in [`values.yaml`](helm/lightweight-monitoring-stack/values.yaml)):

| Value | Default | Purpose |
| :--- | :--- | :--- |
| `profile` | `""` | `laptop` = anonymous Grafana, LoadBalancer services, `small` size, and `resource` node metrics unless you choose another mode |
| `size` | `""` | Resource preset for every component: `nano`, `small`, `standard` |
| `expose.type` | `""` | Force one Service type for everything: `ClusterIP`, `NodePort`, `LoadBalancer` |
| `nodeMetrics.mode` | `off` | See *Node Metrics Modes* above |
| `grafana.anonymousAuth` | `false` | Anonymous Admin access (on with `profile=laptop`) |
| `grafana.adminPassword` | `""` | Empty = keep existing / generate random |
| `podSecurity.enforce` / `.level` | `true` / `restricted` | Pod Security Admission labels on the created namespaces |
| `openshift` | `false` | Omit fixed UIDs so OpenShift's restricted SCC can assign them |
| `prometheus.retention.time` / `.size` | `2h` / `100MB` | TSDB retention |
| `prometheus.scrapeInterval` / `.infraScrapeInterval` | `5s` / `15s` | App targets vs. infra targets (Prometheus, KSM, node metrics) |
| `prometheus.externalTarget` | `""` | Extra `host:port` to scrape, e.g. a node_exporter outside the cluster |
| `kubeStateMetrics.collectors` | 11 resource types | What KSM watches (RBAC is trimmed to match) |
| `namespaces.app` / `.monitoring` | `app` / `monitoring` | Namespace names |

Example — a small production-ish install with full container metrics:

```bash
helm install demo lightweight-monitoring/lightweight-monitoring-stack \
  --set size=standard \
  --set nodeMetrics.mode=daemonset \
  --set prometheus.retention.time=24h --set prometheus.retention.size=1GB
```

---

## 🔒 Security Defaults

| Area | v3 behaviour |
| :--- | :--- |
| Grafana | Anonymous access **off**; admin password generated into the `grafana-admin` Secret and preserved across upgrades |
| Services | `ClusterIP` — nothing exposed outside the cluster unless you ask (`expose.type` or `profile=laptop`) |
| Pods | `runAsNonRoot`, read-only root filesystem, all capabilities dropped, `RuntimeDefault` seccomp, no privilege escalation |
| Images | App images run as UID 10001; all third-party images pinned to exact versions |
| Namespaces | Pod Security Admission `restricted` enforced |
| RBAC | kube-state-metrics can list only the 11 resource types it reports — **no access to Secrets or ConfigMaps**; cluster-scoped names are prefixed with the release name |

---

## 🎓 Learn Performance Testing

Open the **Load Generator UI** (`:8089`). It shows, live:

- a **diagnosis banner** — *Healthy*, *Not enough users*, *App saturated*, *Load generator overloaded* or *Requests failing* — with the numbers behind it and what to do;
- a **Little's Law card** that does the maths with measured values, e.g. *"Users needed = 30 TPS × 460 ms = 13.8, allowed: 6 → the most it can do is 13 TPS"*;
- clearly separated **intentional errors** (`/api/error`, on purpose) and **failures** (timeouts, refused connections — something is wrong);
- the generator's **own CPU**, because the test tool can be the bottleneck too.

### The three ideas

**1. Little's Law.** Users, throughput and response time are locked together:

```
TPS = users ÷ (response time + think time)        in flight = TPS × response time
```

**2. Two ways to drive load** — pick one in the UI:

| Mode | You set | The outcome | Behaves like |
| :--- | :--- | :--- | :--- |
| 👥 **Fixed Users** (closed model) | Virtual users + think time | TPS | JMeter thread groups, LoadRunner vusers |
| 🎯 **Fixed Rate** (open model) | Target TPS + max users | Users in flight | k6 `constant-arrival-rate`, real internet traffic |

In Fixed Users, a slower app silently produces less TPS. In Fixed Rate, a slower app piles up requests in flight; if that exceeds *max users* the generator has to skip requests.

**3. Is it the app or the generator?** Generating load costs CPU. An overloaded generator sends late and measures late, so a healthy app *looks* slow. Compare client and server response time, and always check generator CPU.

| State | Load generator | TPS | Response time | What to do | Scenario |
| :--- | :--- | :--- | :--- | :--- | :--- |
| ✅ Healthy scalability | More users | Rises in proportion | Flat, low | Keep going | 📈 `scale-users` |
| 🧮 Not enough users | Max users reached, requests skipped | Below target | Normal | Allow more users (≈ TPS × response time) | 🧮 `too-few-users` |
| 🧱 App bottleneck | More users | Flattens | Climbs steeply; app queue > 0 | Optimise the app or add capacity | 🧱 `app-saturation` |
| 🥵 Generator choke | CPU ≈ 100%, event loop lagging | Drops / jumpy | Client ≫ server (false) | Use more or bigger generators | 🥵 `generator-overload` |

### Scenarios

**Learn the concepts** — each one demonstrates one idea and tells you what to watch:

| ID | What happens |
| :--- | :--- |
| `steady-state` | 10 TPS the app handles easily — Little's Law holding live |
| `scale-users` | Fixed Users 1 → 12: TPS climbs in a straight line, response time flat |
| `think-time` | 10 users pausing 1 s produce only ≈ 7 TPS |
| `too-few-users` | 30 TPS needs ≈ 14 users in flight, only 6 allowed → TPS stalls at ≈ 13, requests skipped |
| `app-saturation` | App limited to 4 worker slots: TPS flattens at ≈ 11 while response time climbs 0.35 s → 2 s |
| `generator-overload` | 10 ms of test-script CPU per request overloads the 0.2-core generator; client p95 ≈ 0.7 s while the app answers in 40 ms |

**Classic tests:** `ramp-up` (0 → 50 TPS), `spike-test` (100 TPS burst), `error-storm` (trips `HighErrorRate`), `latency-soak` (trips p95 `LatencySpike`), `chaos` (random load).

```bash
make simulate    # steady-state
make spike       # 100 TPS spike
make stop-load   # stop the active test
# or directly:
curl -X POST http://localhost:8089/api/scenarios/too-few-users/run
```

### App capacity

The demo app processes at most **worker slots** requests at once, like a web server's thread pool; extra requests wait in a queue. Set it from either UI or the API (`0` = unlimited):

```bash
curl -X POST http://localhost:8000/api/admin/capacity -H 'Content-Type: application/json' -d '{"worker_slots": 4}'
```

Max TPS ≈ worker slots ÷ time per request — e.g. 4 ÷ 0.35 s ≈ 11 TPS.

---

## 📊 Resource Footprint

Measured on OrbStack with the load generator driving ~18 TPS:

| Pod | RAM (working set) |
| :--- | :--- |
| Grafana | ~91 MB |
| Prometheus | ~75 MB |
| Load Generator | ~44 MB |
| Demo App | ~40 MB |
| kube-state-metrics | ~11 MB |
| **Total** | **~262 MB**, ~0.12 CPU cores |
| + cAdvisor DaemonSet (`daemonset` mode) | +31 MB |

For comparison, the OpenTelemetry Astronomy Demo runs ~34 pods in 12–16 GB of RAM and 4–8 cores.

---

## 🚀 Other Ways to Run It

### Raw Kubernetes manifests (no Helm)

`k8s/generated.yaml` is rendered from the chart (laptop profile) and drift-checked in CI:

```bash
make up      # kubectl apply -f k8s/generated.yaml
make status
make down
```

The Grafana admin password in these manifests is the placeholder `change-me` (anonymous access is on).

### Docker Compose (no Kubernetes)

```bash
make compose-up      # builds and starts demo-app, load-generator, Prometheus, Grafana
make compose-down
```

Compose is a local-only demo: Grafana runs with anonymous Admin and `admin` / `password`.

---

## 🛠️ Make Targets

| Target | Does |
| :--- | :--- |
| `make port-forward` / `port-forward-stop` | Forward all four services to localhost / stop them |
| `make doctor` | One-shot health check: pod state, restarts, Prometheus & Grafana health, recent warning events |
| `make smoke` | 9-point smoke test (the same one CI runs) |
| `make stats` | Live CPU & memory per pod, from Prometheus |
| `make dev-images` | Build app images and load them into kind/k3d for local iteration |
| `make k8s-regen` / `k8s-check` | Regenerate / verify `k8s/generated.yaml` |
| `make simulate` / `spike` / `stop-load` | Drive the load generator |

---

## ⬆️ Upgrading from v2

v3 changes defaults; review these before `helm upgrade`:

- **Services are `ClusterIP`.** If you relied on LoadBalancer, add `--set profile=laptop` or `--set expose.type=LoadBalancer`.
- **Grafana requires a login.** Read the password from the `grafana-admin` Secret, or keep the old behaviour with `--set profile=laptop` (or `--set grafana.anonymousAuth=true`).
- **Pod Security `restricted`** is enforced on the created namespaces. Your own workloads in `app` must meet it, or set `podSecurity.level=baseline`.
- **kube-state-metrics** no longer reports Secrets/ConfigMaps.
- **Kubernetes dashboard** is now *Kubernetes / Overview* (`/d/k8s-overview`); container panels need a `nodeMetrics.mode`.
- `prometheus.scrapeKubelet` was replaced by `nodeMetrics.mode` (v2.2).
- **Load generator API:** `POST /api/load/start` now takes `mode` (`fixed_users` / `fixed_rate`), `target_tps`, `max_users`, `virtual_users`, `think_time_ms`; the old `target_rps` / `concurrency` fields are still accepted. Scenario `canary-compare` was removed; `steady-state`, `ramp-up`, `spike-test`, `error-storm`, `latency-soak` and `chaos` keep their IDs.

---

## 🧯 Troubleshooting

| Symptom | Cause / fix |
| :--- | :--- |
| `localhost:3000` times out after an upgrade | Stale port-forward pinned to a deleted pod — `make port-forward-stop; make port-forward` |
| `address already in use` when port-forwarding | An old forward still holds the port — `make port-forward-stop` (or `lsof -nP -iTCP:3000`) |
| Kubernetes dashboard container panels empty | Set `nodeMetrics.mode` (`daemonset` on OrbStack / Docker Desktop) |
| Pods stuck at 0/1 with "violates PodSecurity" events | A workload doesn't meet `restricted`; fix its securityContext or lower `podSecurity.level` |
| Grafana 401 | Wrong password — read it from the `grafana-admin` Secret |
| TPS below target, diagnosis "Not enough users" | Raise *max users* to at least target TPS × response time |
| Generator CPU ≈ 100% | The generator is the bottleneck; its response times are inflated. Lower *script work* or the load |

Run `make doctor` for a quick diagnosis.

---

## 📂 Project Structure

```
.
├── app/                         # FastAPI demo app (UID 10001): main.py + static/index.html
├── load-generator/              # Load generator (:8089)
│   ├── engine.py                # Fixed Users / Fixed Rate engine, Little's Law maths, diagnosis
│   ├── scenarios.py             # One-click scenarios with lessons
│   ├── main.py                  # Control API
│   └── static/index.html        # UI
├── helm/lightweight-monitoring-stack/
│   ├── values.yaml              # All configuration, documented inline
│   ├── files/dashboards/        # App, Load Generator, Kubernetes / Overview dashboards
│   └── templates/               # namespaces, demo-app, load-generator, prometheus (+rbac),
│                                # grafana, kube-state-metrics, cadvisor-daemonset, NOTES
├── k8s/generated.yaml           # Raw manifests rendered from the chart (make up)
├── grafana/ prometheus/         # Docker Compose configuration
├── scripts/                     # smoke.sh, doctor.sh, generate-k8s.sh
├── docs/portability-plan.md     # Design plan behind v3
├── .github/workflows/
│   ├── ci.yml                   # Smoke tests on kind, k3d and Compose + manifest drift check
│   └── release.yml              # Multi-arch images → GHCR, chart → GitHub Pages + GitHub Release
├── docker-compose.yml
└── Makefile
```

---

## 📜 License

MIT
