# Lightweight Monitoring Stack — Portability Implementation Plan

> Living doc. Original at: https://claude.ai/artifact/NGXbcPehbmhutroNqsiiFV

## Guiding principle

The demo must keep working at every commit. Every change is additive and gated behind a flag until defaults flip in Phase 2 (a separate branch). A `laptop` profile preserves today's exact demo experience through all phases.

---

## Phase 0 — Safety net (v2.1.1)

**Goal:** stop the crash, add guardrails, no visible behaviour change.

| Step | What | Files |
|------|------|-------|
| 0.0 | **Hotfix** — drop kubelet/cAdvisor scrape job; set Prometheus memory limit to 256 Mi | `values.yaml`, `k8s/02-prometheus.yaml`, `Chart.yaml` |
| 0.1 | `scripts/smoke.sh` — 9-point demo contract (pods Running, Prometheus up, kube_pod_info present, app /metrics reachable, Grafana up) | `scripts/smoke.sh` |
| 0.2 | Baseline recording — `kubectl top` snapshot in CI before any change | CI only |
| 0.3 | CI matrix — kind (amd64) + k3d (arm64) + Compose; gate merges on smoke | `.github/workflows/` |
| 0.4 | Profile plumbing — `--set profile=laptop` short-circuits to safe defaults via `coalesce` helper | `values.yaml`, `_helpers.tpl` |
| 0.5 | Dashboard files — move inline JSON out of the ConfigMap template into `helm/…/files/dashboards/app/` loaded with `.Files.Get` | `templates/grafana.yaml`, `files/dashboards/` |
| 0.6 | Datasource UID — replace `${DS_PROMETHEUS}` / hardcoded uid with `prometheus` everywhere | dashboard JSONs |

### 0.0 Demo contract (checked by smoke.sh)

1. All pods in `app` and `monitoring` namespaces reach `Running/Completed`.
2. `GET /metrics` on demo-app returns HTTP 200 and includes `http_requests_total`.
3. `GET /health` on load-generator returns HTTP 200.
4. Prometheus `/api/v1/query?query=up` returns at least 3 targets with `state="up"`.
5. `kube_pod_info` returns ≥ 1 series in Prometheus.
6. Grafana `GET /api/health` returns `{"database":"ok"}`.
7. Grafana datasource named "Prometheus" exists and its health check passes.
8. At least one Grafana dashboard is provisioned.
9. No pod has `restartCount > 1` after a 60 s soak.

---

## Phase 1 — Portability (v2.2.0)

**Goal:** run correctly on any Kubernetes cluster without manual tuning.

| Step | What |
|------|------|
| 1.0 | cAdvisor DaemonSet spike — test whether deploying cAdvisor as a DaemonSet gives `container_*` metrics on OrbStack/k3s |
| 1.1 | `nodeMetrics.mode` — values: `apiserver` (default), `direct`, `resource` (laptop), `daemonset`, `off`; only one mode active |
| 1.2 | `expose.type` — `LoadBalancer` (default), `NodePort`, `ClusterIP` |
| 1.3 | Ship dashboards 15760 and 24097 into a "Kubernetes" folder; strip `__inputs`; replace `${DS_PROMETHEUS}`; change `image!=""` filters to `container!=""` |
| 1.4 | Pin image versions: Prometheus 3.15.1, Grafana 13.0.2, KSM v2.13.0 |
| 1.5 | `k8s/` raw manifests — point at GHCR images; `make dev-images` for local builds |
| 1.6 | Smoke test additions for node metrics modes |

---

## Phase 2 — Security (v3.0.0) — separate branch

**Goal:** safe to leave running; no anonymous admin; least-privilege.

| Step | What |
|------|------|
| 2.1 | Grafana login — generated Secret via `lookup`; `profile=laptop` keeps anonymous Admin |
| 2.2 | ClusterIP by default (was LoadBalancer) |
| 2.3 | `securityContext` — `runAsNonRoot`, `readOnlyRootFilesystem`, `USER 10001` in Dockerfiles, emptyDirs, `openshift` flag, PSA namespace labels |
| 2.4 | KSM `--resources` flag — trim RBAC to only the resource types actually used |
| 2.5 | `externalTarget` — scrape an external Prometheus from inside the cluster |
| 2.6 | `lms-` name prefixes on all ClusterRoles/Bindings |
| 2.7 | Smoke test additions for security posture |

---

## Phase 3 — Consistency (v3.1.0)

| Step | What |
|------|------|
| 3.1 | Drop `cluster.local` suffix — use `<svc>.<ns>.svc` |
| 3.2 | Generate `k8s/` with `helm template` + CI drift check |
| 3.3 | Scrape interval keep-list from dashboard queries; node/KSM at 15 s, app at 5 s |
| 3.4 | Size presets (`nano`, `small`, `standard`) |
| 3.5 | Makefile: `doctor`, `stats` via Prometheus, clean port-forward |
| 3.6 | CI matrix expansion |
| 3.7 | Cloud acceptance test |
| 3.8 | README overhaul |

---

## Defect traceability

| Defect | Severity | Phase | Step |
|--------|----------|-------|------|
| 1 — kubelet job OOM crash | P0 | 0 | 0.0 |
| 2 — no container_* metrics on OrbStack | P1 | 1 | 1.0–1.1 |
| 3 — no nodeMetrics fallback | P1 | 1 | 1.1 |
| 4 — no resource-mode scrape | P1 | 1 | 1.1 |
| 5 — expose.type hardcoded LoadBalancer | P1 | 1 | 1.2 |
| 6 — dashboards not shipped in chart | P1 | 0 | 0.5 |
| 7 — datasource uid mismatch | P1 | 0 | 0.6 |
| 8 — KSM reads secrets/configmaps | P2 | 2 | 2.4 |
| 9 — generic RBAC names (prometheus, kube-state-metrics) | P2 | 2 | 2.6 |
| 10 — anonymous admin on by default | P2 | 2 | 2.1 |
| 11 — image tags: latest unpinned | P1 | 1 | 1.4 |
| 12 — cluster.local hardcoded | P2 | 3 | 3.1 |
| 13 — k8s/ drifts from Helm chart | P2 | 3 | 3.2 |
| 14 — no CI / no smoke test | P1 | 0 | 0.1–0.3 |
| 15 — k8s/ uses local image tags | P1 | 1 | 1.5 |

---

## Release schedule

| Version | Branch | Defaults that change |
|---------|--------|----------------------|
| 2.1.1 | main | scrapeKubelet default → false; memory limit → 256 Mi |
| 2.2.0 | main | nodeMetrics.mode=apiserver; expose.type=LoadBalancer (same, but now a flag) |
| 3.0.0 | release/3.0 | anonymousAuth → false; serviceType → ClusterIP |
| 3.1.0 | main | cluster.local removed; k8s/ generated |

### Laptop profile

`helm install demo … --set profile=laptop` (or `kubectl apply -k k8s/overlays/laptop`) sets:
- `nodeMetrics.mode=off`
- `expose.type=LoadBalancer`
- `grafana.anonymousAuth=true`
- `prometheus.resources.limits.memory=256Mi`

---

## Decisions needed before each phase

### Before 0.x commit
- [ ] Confirm 256 Mi Prometheus limit is acceptable long-term

### Before 1.x
- [ ] Export dashboard 24097 JSON (needed for step 1.3c)
- [ ] Decide if cAdvisor DaemonSet mode is worth adding (step 1.0 spike)
- [ ] Confirm Grafana 13.0.2 pin
- [ ] Confirm k8s/ generation (step 3.2) replaces hand-maintained files

### Before 2.x (branch)
- [ ] Choose anonymous-viewer vs. login-required for the laptop profile
- [ ] Which cloud cluster to run acceptance test on (step 3.7)
