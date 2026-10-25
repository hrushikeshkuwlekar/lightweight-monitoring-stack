#!/usr/bin/env bash
# smoke.sh — 9-point demo contract for lightweight-monitoring-stack
# Usage: ./scripts/smoke.sh [namespace-app] [namespace-monitoring]
# Defaults: app=app, monitoring=monitoring
set -euo pipefail

NS_APP="${1:-app}"
NS_MON="${2:-monitoring}"
PROM_PORT="${PROM_PORT:-9090}"
GRAFANA_PORT="${GRAFANA_PORT:-3000}"

PASS=0
FAIL=0
ERRORS=()

pass() { echo "  [PASS] $1"; ((PASS++)); }
fail() { echo "  [FAIL] $1"; ERRORS+=("$1"); ((FAIL++)); }

wait_pods() {
  local ns="$1"
  local timeout=120
  echo "  Waiting up to ${timeout}s for pods in namespace '${ns}'..."
  if kubectl wait pod --all -n "$ns" --for=condition=Ready --timeout="${timeout}s" 2>/dev/null; then
    return 0
  else
    return 1
  fi
}

prom_query() {
  # Run a PromQL instant query; returns the raw JSON result
  kubectl exec -n "$NS_MON" deploy/prometheus -- \
    wget -qO- "http://localhost:${PROM_PORT}/api/v1/query?query=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$1")" 2>/dev/null
}

# ── Setup: forward Prometheus and Grafana if not already accessible ───────────
PROM_PF_PID=""
GRAFANA_PF_PID=""
cleanup() {
  [[ -n "$PROM_PF_PID" ]]    && kill "$PROM_PF_PID"    2>/dev/null || true
  [[ -n "$GRAFANA_PF_PID" ]] && kill "$GRAFANA_PF_PID" 2>/dev/null || true
}
trap cleanup EXIT

if ! curl -sf "http://localhost:${PROM_PORT}/-/healthy" >/dev/null 2>&1; then
  kubectl port-forward -n "$NS_MON" svc/prometheus-svc "${PROM_PORT}:${PROM_PORT}" >/dev/null 2>&1 &
  PROM_PF_PID=$!
  sleep 3
fi

if ! curl -sf "http://localhost:${GRAFANA_PORT}/api/health" >/dev/null 2>&1; then
  kubectl port-forward -n "$NS_MON" svc/grafana-svc "${GRAFANA_PORT}:${GRAFANA_PORT}" >/dev/null 2>&1 &
  GRAFANA_PF_PID=$!
  sleep 3
fi

echo ""
echo "═══════════════════════════════════════════════════"
echo " Lightweight Monitoring Stack — Smoke Test"
echo "═══════════════════════════════════════════════════"
echo ""

# ── 1. All pods Running ───────────────────────────────────────────────────────
echo "1. Pods ready"
if wait_pods "$NS_APP" && wait_pods "$NS_MON"; then
  CRASHED=$(kubectl get pods -n "$NS_APP" -n "$NS_MON" --no-headers 2>/dev/null | awk '{print $3}' | grep -vE 'Running|Completed' | wc -l | tr -d ' ')
  if [[ "$CRASHED" -eq 0 ]]; then
    pass "All pods Running/Completed in $NS_APP and $NS_MON"
  else
    fail "Some pods not Running/Completed (count=$CRASHED)"
  fi
else
  fail "Pods did not become Ready within 120s"
fi

# ── 2. demo-app /metrics ──────────────────────────────────────────────────────
echo "2. demo-app /metrics"
DEMO_APP_POD=$(kubectl get pod -n "$NS_APP" -l app=demo-app -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [[ -n "$DEMO_APP_POD" ]]; then
  if kubectl exec -n "$NS_APP" "$DEMO_APP_POD" -- wget -qO- http://localhost:8000/metrics 2>/dev/null | grep -q 'http_requests_total'; then
    pass "demo-app /metrics reachable and includes http_requests_total"
  else
    fail "demo-app /metrics missing http_requests_total"
  fi
else
  fail "No demo-app pod found in namespace $NS_APP"
fi

# ── 3. load-generator /health ────────────────────────────────────────────────
echo "3. load-generator /health"
LG_POD=$(kubectl get pod -n "$NS_APP" -l app=load-generator -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [[ -n "$LG_POD" ]]; then
  if kubectl exec -n "$NS_APP" "$LG_POD" -- wget -qO- http://localhost:8089/health 2>/dev/null | grep -q '"status"'; then
    pass "load-generator /health returns OK"
  else
    fail "load-generator /health did not return expected JSON"
  fi
else
  fail "No load-generator pod found in namespace $NS_APP"
fi

# ── 4. Prometheus targets up ─────────────────────────────────────────────────
echo "4. Prometheus targets"
UP_COUNT=$(curl -sf "http://localhost:${PROM_PORT}/api/v1/query?query=up" 2>/dev/null \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(sum(1 for r in d.get('data',{}).get('result',[]) if r.get('value',['','0'])[1]=='1'))" 2>/dev/null || echo 0)
if [[ "$UP_COUNT" -ge 3 ]]; then
  pass "Prometheus has $UP_COUNT targets up (≥3 required)"
else
  fail "Prometheus has only $UP_COUNT targets up (need ≥3)"
fi

# ── 5. kube_pod_info in Prometheus ───────────────────────────────────────────
echo "5. kube_pod_info"
KPI_COUNT=$(curl -sf "http://localhost:${PROM_PORT}/api/v1/query?query=kube_pod_info" 2>/dev/null \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',{}).get('result',[])))" 2>/dev/null || echo 0)
if [[ "$KPI_COUNT" -ge 1 ]]; then
  pass "kube_pod_info has $KPI_COUNT series"
else
  fail "kube_pod_info has 0 series — kube-state-metrics may be down or not scraping"
fi

# ── 6. Grafana health ────────────────────────────────────────────────────────
echo "6. Grafana health"
GRAFANA_HEALTH=$(curl -sf "http://localhost:${GRAFANA_PORT}/api/health" 2>/dev/null || echo "{}")
if echo "$GRAFANA_HEALTH" | grep -q '"database":"ok"'; then
  pass "Grafana /api/health → database OK"
else
  fail "Grafana /api/health did not return database OK (got: $GRAFANA_HEALTH)"
fi

# ── 7. Grafana datasource health ─────────────────────────────────────────────
echo "7. Grafana datasource"
DS_LIST=$(curl -sf "http://admin:password@localhost:${GRAFANA_PORT}/api/datasources" 2>/dev/null || echo "[]")
DS_UID=$(echo "$DS_LIST" | python3 -c "import sys,json; ds=json.load(sys.stdin); matches=[d for d in ds if d.get('name')=='Prometheus']; print(matches[0].get('uid','') if matches else '')" 2>/dev/null || echo "")
if [[ -n "$DS_UID" ]]; then
  DS_HEALTH=$(curl -sf "http://admin:password@localhost:${GRAFANA_PORT}/api/datasources/uid/${DS_UID}/health" 2>/dev/null || echo "{}")
  if echo "$DS_HEALTH" | grep -q '"status":"OK"'; then
    pass "Grafana datasource 'Prometheus' (uid=$DS_UID) is healthy"
  else
    fail "Grafana datasource 'Prometheus' health check failed: $DS_HEALTH"
  fi
else
  fail "Grafana datasource named 'Prometheus' not found"
fi

# ── 8. At least one dashboard provisioned ────────────────────────────────────
echo "8. Grafana dashboards"
DASH_COUNT=$(curl -sf "http://admin:password@localhost:${GRAFANA_PORT}/api/search?type=dash-db" 2>/dev/null \
  | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
if [[ "$DASH_COUNT" -ge 1 ]]; then
  pass "Grafana has $DASH_COUNT dashboard(s) provisioned"
else
  fail "No Grafana dashboards found — provisioning may have failed"
fi

# ── 9. No crash-looping pods (30 s soak) ─────────────────────────────────────
echo "9. Pod stability (30 s soak)"
sleep 30
MAX_RESTARTS=$(kubectl get pods -n "$NS_APP" -n "$NS_MON" --no-headers 2>/dev/null \
  | awk '{print $4}' | sort -n | tail -1 || echo 0)
if [[ "${MAX_RESTARTS:-0}" -le 1 ]]; then
  pass "No pod has more than 1 restart after soak"
else
  fail "A pod has $MAX_RESTARTS restarts — possible crash loop"
fi

# ── 10. Security posture (optional — only runs when CHECK_SECURITY=1) ─────────
if [[ "${CHECK_SECURITY:-}" == "1" ]]; then
  echo "10. Security posture"

  # 10a. No pod running as root (UID 0)
  ROOT_PODS=$(kubectl get pods -n "$NS_APP" -n "$NS_MON" -o jsonpath='{range .items[*]}{.metadata.name}{"/"}{range .spec.containers[*]}{.securityContext.runAsUser}{" "}{end}{"\n"}{end}' 2>/dev/null \
    | grep -c "^[^/]*/0 " || true)
  if [[ "${ROOT_PODS:-0}" -eq 0 ]]; then
    pass "No pod container running as UID 0"
  else
    fail "$ROOT_PODS pod container(s) running as UID 0"
  fi

  # 10b. Grafana anonymous access disabled (if not laptop profile)
  if [[ "${ANONYMOUS_AUTH:-}" != "true" ]]; then
    ANON=$(curl -sf "http://localhost:${GRAFANA_PORT}/api/dashboards/home" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print('ok')" 2>/dev/null || echo "blocked")
    if [[ "$ANON" == "blocked" ]]; then
      pass "Grafana anonymous access is disabled (unauthenticated request blocked)"
    else
      fail "Grafana anonymous access is enabled — unauthenticated request succeeded"
    fi
  else
    pass "Grafana anonymous auth check skipped (ANONYMOUS_AUTH=true)"
  fi
fi

# ── 11. nodeMetrics mode (optional — only runs when NODE_METRICS_MODE is set) ─
if [[ -n "${NODE_METRICS_MODE:-}" ]]; then
  echo "11. nodeMetrics mode: $NODE_METRICS_MODE"
  case "$NODE_METRICS_MODE" in
    resource)
      COUNT=$(curl -sf "http://localhost:${PROM_PORT}/api/v1/query?query=container_cpu_usage_seconds_total" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',{}).get('result',[])))" 2>/dev/null || echo 0)
      if [[ "$COUNT" -ge 1 ]]; then
        pass "resource mode: container_cpu_usage_seconds_total has $COUNT series"
      else
        fail "resource mode: container_cpu_usage_seconds_total has 0 series"
      fi
      ;;
    apiserver|direct)
      COUNT=$(curl -sf "http://localhost:${PROM_PORT}/api/v1/query?query=kubelet_node_name" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',{}).get('result',[])))" 2>/dev/null || echo 0)
      if [[ "$COUNT" -ge 1 ]]; then
        pass "$NODE_METRICS_MODE mode: kubelet metrics present ($COUNT series)"
      else
        fail "$NODE_METRICS_MODE mode: kubelet_node_name has 0 series — check RBAC or network access"
      fi
      ;;
    daemonset)
      COUNT=$(curl -sf "http://localhost:${PROM_PORT}/api/v1/query?query=container_cpu_usage_seconds_total" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',{}).get('result',[])))" 2>/dev/null || echo 0)
      if [[ "$COUNT" -ge 1 ]]; then
        pass "daemonset mode: container_cpu_usage_seconds_total has $COUNT series"
      else
        fail "daemonset mode: container_cpu_usage_seconds_total has 0 series — check cAdvisor DaemonSet"
      fi
      ;;
    off)
      pass "nodeMetrics.mode=off: node scraping intentionally disabled"
      ;;
  esac
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "───────────────────────────────────────────────────"
echo " Results: $PASS passed, $FAIL failed"
if [[ "$FAIL" -gt 0 ]]; then
  echo " Failures:"
  for e in "${ERRORS[@]}"; do echo "   • $e"; done
  echo "───────────────────────────────────────────────────"
  exit 1
else
  echo " All checks passed."
  echo "───────────────────────────────────────────────────"
  exit 0
fi
