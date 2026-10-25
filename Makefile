.PHONY: up down status stats logs simulate spike stop-load compose-up compose-down port-forward dev-images smoke

# Apply Kubernetes manifests (deploys app and monitoring namespaces)
up:
	kubectl apply -f k8s/

# Tear down both namespaces
down:
	kubectl delete -f k8s/

# Check status of pods and services across both namespaces
status:
	@echo "=== Application Namespace (app) ==="
	@kubectl get pods,svc -n app 2>/dev/null || true
	@echo ""
	@echo "=== Monitoring Namespace (monitoring) ==="
	@kubectl get pods,svc -n monitoring 2>/dev/null || true

# Show live memory & CPU consumption
stats:
	docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"

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

# Port forward all services across both namespaces locally
port-forward:
	kubectl port-forward svc/demo-app-svc -n app 8000:8000 & \
	kubectl port-forward svc/load-generator-svc -n app 8089:8089 & \
	kubectl port-forward svc/grafana-svc -n monitoring 3000:3000 & \
	kubectl port-forward svc/prometheus-svc -n monitoring 9090:9090 &
