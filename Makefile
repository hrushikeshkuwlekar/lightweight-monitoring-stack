.PHONY: up down status stats logs simulate

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

# Stream logs of all monitoring pods
logs:
	kubectl logs -n monitoring -l app=demo-app --tail=50 -f

# Toggle background traffic generator
simulate:
	curl -X POST http://localhost:8000/api/simulator/toggle
	@echo ""
