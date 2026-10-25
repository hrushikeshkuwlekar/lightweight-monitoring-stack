.PHONY: up down status stats logs simulate spike stop-load compose-up compose-down port-forward

# Apply Kubernetes manifests
up:
	kubectl apply -f k8s/

# Tear down the Kubernetes stack
down:
	kubectl delete -f k8s/

# Check status of pods and services
status:
	kubectl get pods,svc -n monitoring

# Show live memory & CPU consumption
stats:
	docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"

# Stream logs of monitoring and load-generator pods
logs:
	kubectl logs -n monitoring -l 'app in (demo-app,load-generator)' --tail=50 -f

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

# Port forward all services locally
port-forward:
	kubectl port-forward svc/demo-app-svc -n monitoring 8000:8000 & \
	kubectl port-forward svc/load-generator-svc -n monitoring 8089:8089 & \
	kubectl port-forward svc/grafana-svc -n monitoring 3000:3000 & \
	kubectl port-forward svc/prometheus-svc -n monitoring 9090:9090 &
