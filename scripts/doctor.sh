#!/usr/bin/env bash
# doctor.sh — one-shot health check of a running stack.
# Usage: ./scripts/doctor.sh [namespace-app] [namespace-monitoring]
# Exits 0 if everything looks healthy, non-zero if a critical check fails.
set -euo pipefail

NS_APP="${1:-app}"
NS_MON="${2:-monitoring}"
PROM="${PROM_URL:-http://localhost:9090}"
GRAF="${GRAFANA_URL:-http://localhost:3000}"

RED=$(printf '\033[31m'); GRN=$(printf '\033[32m'); YEL=$(printf '\033[33m'); RST=$(printf '\033[0m')
pass() { echo "  ${GRN}✓${RST} $1"; }
warn() { echo "  ${YEL}!${RST} $1"; }
fail() { echo "  ${RED}✗${RST} $1"; FAILS=$((FAILS + 1)); }

FAILS=0

echo "== pod state =="
for ns in "$NS_APP" "$NS_MON"; do
  BAD=$(kubectl get pods -n "$ns" --no-headers 2>/dev/null | awk '$3 != "Running" && $3 != "Completed"' | wc -l | tr -d ' ')
  if [[ "$BAD" -eq 0 ]]; then
    pass "all pods in $ns are Running/Completed"
  else
    fail "$BAD pod(s) in $ns are not Running/Completed"
    kubectl get pods -n "$ns" --no-headers 2>/dev/null | awk '$3 != "Running" && $3 != "Completed" {print "    " $0}'
  fi
done

echo "== restart counts =="
HOT=$(kubectl get pods -n "$NS_APP" -n "$NS_MON" --no-headers 2>/dev/null | awk '{print $4}' | sort -n | tail -1)
if [[ "${HOT:-0}" -le 2 ]]; then
  pass "no pod has more than ${HOT:-0} restart(s)"
else
  warn "a pod has $HOT restart(s) — check its logs"
fi

echo "== prometheus =="
if curl -sf "$PROM/-/healthy" >/dev/null 2>&1; then
  pass "$PROM reachable"
  UP=$(curl -sf "$PROM/api/v1/query?query=up" 2>/dev/null | python3 -c "import json,sys; r=json.load(sys.stdin).get('data',{}).get('result',[]); print(sum(1 for x in r if x.get('value',['','0'])[1]=='1'))" 2>/dev/null || echo 0)
  if [[ "$UP" -ge 2 ]]; then
    pass "$UP target(s) up"
  else
    fail "only $UP target(s) up (expected ≥2)"
  fi
else
  warn "$PROM unreachable — run 'make port-forward' first"
fi

echo "== grafana =="
if curl -sf "$GRAF/api/health" >/dev/null 2>&1; then
  pass "$GRAF reachable"
  DBOK=$(curl -sf "$GRAF/api/health" 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('database','?'))" 2>/dev/null || echo "?")
  if [[ "$DBOK" == "ok" ]]; then
    pass "grafana database=ok"
  else
    fail "grafana database=$DBOK"
  fi
else
  warn "$GRAF unreachable — run 'make port-forward' first"
fi

echo "== recent warning events =="
EVENTS=$(kubectl get events -A --field-selector type=Warning --sort-by='.lastTimestamp' -o 'custom-columns=TIME:.lastTimestamp,NS:.involvedObject.namespace,REASON:.reason,MSG:.message' 2>/dev/null | tail -5)
if [[ -z "$EVENTS" ]] || [[ "$EVENTS" == "TIME NS REASON MSG" ]]; then
  pass "no warning events"
else
  warn "recent warnings (last 5):"
  echo "$EVENTS" | sed 's/^/    /'
fi

echo ""
if [[ "$FAILS" -eq 0 ]]; then
  echo "${GRN}healthy${RST}"
  exit 0
else
  echo "${RED}$FAILS critical check(s) failed${RST}"
  exit 1
fi
