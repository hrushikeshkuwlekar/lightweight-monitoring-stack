.PHONY: up down status stats stats-docker logs simulate spike stop-load compose-up compose-down port-forward port-forward-stop dev-images smoke doctor k8s-regen k8s-check

# Deploy without Helm: apply the manifests rendered from the chart (laptop profile)
up:
	kubectl apply -f k8s/generated.yaml

# Tear down everything `make up` created (both namespaces, cluster RBAC)
down:
	kubectl delete -f k8s/generated.yaml --ignore-not-found

# Check status of pods and services across both namespaces
status:
	@echo "=== Application Namespace (app) ==="
	@kubectl get pods,svc -n app 2>/dev/null || true
	@echo ""
	@echo "=== Monitoring Namespace (monitoring) ==="
	@kubectl get pods,svc -n monitoring 2>/dev/null || true

# Show live memory & CPU per container as reported by Prometheus (works
# whether the stack runs under Docker, kind, k3s, or a managed cluster).
# Requires Prometheus to be reachable at localhost:9090 (see make port-forward).
stats:
	@echo "== cpu (last 1m, cores) =="
	@curl -sf 'http://localhost:9090/api/v1/query?query=sum(rate(container_cpu_usage_seconds_total%7Bcontainer!%3D%22%22%7D%5B1m%5D))%20by%20(pod)' \
	  | python3 -c "import json,sys; r=json.load(sys.stdin).get('data',{}).get('result',[]); print('\n'.join(f\"  {m['metric'].get('pod','?'):<40} {float(m['value'][1]):.3f}\" for m in sorted(r, key=lambda x: -float(x['value'][1]))))" 2>/dev/null || echo "  (no series)"
	@echo "== memory (MiB) =="
	@curl -sf 'http://localhost:9090/api/v1/query?query=sum(container_memory_working_set_bytes%7Bcontainer!%3D%22%22%7D)%20by%20(pod)' \
	  | python3 -c "import json,sys; r=json.load(sys.stdin).get('data',{}).get('result',[]); print('\n'.join(f\"  {m['metric'].get('pod','?'):<40} {float(m['value'][1])/1024/1024:.1f}\" for m in sorted(r, key=lambda x: -float(x['value'][1]))))" 2>/dev/null || echo "  (no series)"

# Docker-only container stats; use when running under docker compose
stats-docker:
	docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"

# One-shot health check: pod state, Prometheus, Grafana, images, stuck events.
# Non-zero exit if any critical check fails.
doctor:
	@bash scripts/doctor.sh

# Stream logs of application and load-generator pods
logs:
	kubectl logs -n app -l 'app in (demo-app,load-generator)' --tail=50 -f

# Run steady-state load scenario (10 RPS, 5 VU)
simulate:
	curl -X POST http://localhost:8089/api/scenarios/steady-state/run
	@echo ""

# Run traffic spike scenario (100 RPS burst)
spike:
	curl -X POST http://localhost:8089/api/scenarios/spike-test/run
	@echo ""

# Stop active load test
stop-load:
	curl -X POST http://localhost:8089/api/load/stop
	@echo ""

# Start with Docker Compose
compose-up:
	docker compose up -d --build

# Stop Docker Compose
compose-down:
	docker compose down

# Build local Docker images and load them into the active kind/k3d cluster.
# Use this when iterating on app code without pushing to GHCR.
dev-images:
	docker build -t ghcr.io/hrushikeshkuwlekar/lightweight-monitoring-stack:dev ./app
	docker build -t ghcr.io/hrushikeshkuwlekar/lightweight-monitoring-stack-loadgen:dev ./load-generator
	@if kubectl config current-context 2>/dev/null | grep -q kind; then \
	  kind load docker-image ghcr.io/hrushikeshkuwlekar/lightweight-monitoring-stack:dev || true; \
	  kind load docker-image ghcr.io/hrushikeshkuwlekar/lightweight-monitoring-stack-loadgen:dev || true; \
	elif kubectl config current-context 2>/dev/null | grep -q k3d; then \
	  k3d image import ghcr.io/hrushikeshkuwlekar/lightweight-monitoring-stack:dev || true; \
	  k3d image import ghcr.io/hrushikeshkuwlekar/lightweight-monitoring-stack-loadgen:dev || true; \
	fi
	@echo "Images built. Install with: helm upgrade demo ./helm/lightweight-monitoring-stack --set demoApp.image.tag=dev --set loadGenerator.image.tag=dev --reuse-values"

# Run the 9-point smoke test against the active cluster
smoke:
	@bash scripts/smoke.sh

# Regenerate k8s/generated.yaml from the Helm chart (laptop profile).
k8s-regen:
	@bash scripts/generate-k8s.sh

# Fail if k8s/generated.yaml drifted from what the chart would render now.
k8s-check:
	@bash scripts/generate-k8s.sh --check

# Port-forward every service to localhost. PIDs are recorded in .pf.pids so
# `make port-forward-stop` can clean them up — no orphaned processes.
port-forward:
	@mkdir -p .make
	@: > .make/pf.pids
	@kubectl port-forward svc/demo-app-svc        -n app        8000:8000 >/dev/null 2>&1 & echo $$! >> .make/pf.pids
	@kubectl port-forward svc/load-generator-svc  -n app        8089:8089 >/dev/null 2>&1 & echo $$! >> .make/pf.pids
	@kubectl port-forward svc/grafana-svc         -n monitoring 3000:3000 >/dev/null 2>&1 & echo $$! >> .make/pf.pids
	@kubectl port-forward svc/prometheus-svc      -n monitoring 9090:9090 >/dev/null 2>&1 & echo $$! >> .make/pf.pids
	@sleep 2
	@echo "Forwards:"
	@echo "  demo-app        http://localhost:8000"
	@echo "  load-generator  http://localhost:8089"
	@echo "  grafana         http://localhost:3000"
	@echo "  prometheus      http://localhost:9090"
	@echo "Run 'make port-forward-stop' to clean up."

port-forward-stop:
	@if [ -f .make/pf.pids ]; then \
	  while read pid; do kill $$pid 2>/dev/null || true; done < .make/pf.pids; \
	  rm -f .make/pf.pids; \
	  echo "Stopped port-forwards."; \
	else \
	  echo "No .make/pf.pids file — nothing to stop."; \
	fi
