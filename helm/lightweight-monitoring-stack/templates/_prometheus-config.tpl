{{/*
Rendered body of the prometheus-config ConfigMap (prometheus.yml + alerts.yml).
Shared by the ConfigMap and the Deployment checksum so any change to the
rendered config — values or template — rolls the Prometheus pod.
*/}}
{{- define "lightweight-monitoring-stack.prometheusConfigData" -}}
prometheus.yml: |
  global:
    scrape_interval: {{ .Values.prometheus.scrapeInterval }}
    evaluation_interval: {{ .Values.prometheus.evaluationInterval }}

  rule_files:
    - /etc/prometheus/alerts.yml

  scrape_configs:
    - job_name: 'demo-app'
      metrics_path: '/metrics'
      static_configs:
        - targets: ['demo-app-svc.{{ .Values.namespaces.app | default .Release.Namespace }}.svc:{{ .Values.demoApp.port }}']
          labels:
            app: 'demo-app'
            environment: 'helm-deploy'
            namespace: 'app'

{{- if .Values.loadGenerator.enabled }}
    - job_name: 'load-generator'
      metrics_path: '/metrics'
      static_configs:
        - targets: ['load-generator-svc.{{ .Values.namespaces.app | default .Release.Namespace }}.svc:{{ .Values.loadGenerator.port }}']
          labels:
            app: 'load-generator'
            environment: 'helm-deploy'
            namespace: 'app'
{{- end }}

    - job_name: 'prometheus'
      scrape_interval: {{ .Values.prometheus.infraScrapeInterval }}
      static_configs:
        - targets: ['localhost:{{ .Values.prometheus.port }}']

{{- if .Values.kubeStateMetrics.enabled }}
    - job_name: 'kube-state-metrics'
      scrape_interval: {{ .Values.prometheus.infraScrapeInterval }}
      static_configs:
        - targets: ['kube-state-metrics-svc.{{ .Values.namespaces.monitoring | default .Release.Namespace }}.svc:8080']
{{- end }}

{{- $mode := include "lightweight-monitoring-stack.nodeMetricsMode" . }}

{{- if eq $mode "apiserver" }}
    - job_name: 'kubelet'
      scrape_interval: {{ .Values.prometheus.infraScrapeInterval }}
      scheme: https
      tls_config:
        ca_file: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
        insecure_skip_verify: true
      bearer_token_file: /var/run/secrets/kubernetes.io/serviceaccount/token
      kubernetes_sd_configs:
        - role: node
      relabel_configs:
        - action: labelmap
          regex: __meta_kubernetes_node_label_(.+)
        - target_label: __address__
          replacement: kubernetes.default.svc:443
        - source_labels: [__meta_kubernetes_node_name]
          regex: (.+)
          target_label: __metrics_path__
          replacement: /api/v1/nodes/${1}/proxy/metrics

    - job_name: 'cadvisor'
      scrape_interval: {{ .Values.prometheus.infraScrapeInterval }}
      scheme: https
      metrics_path: /metrics/cadvisor
      tls_config:
        ca_file: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
        insecure_skip_verify: true
      bearer_token_file: /var/run/secrets/kubernetes.io/serviceaccount/token
      kubernetes_sd_configs:
        - role: node
      relabel_configs:
        - action: labelmap
          regex: __meta_kubernetes_node_label_(.+)
        - target_label: __address__
          replacement: kubernetes.default.svc:443
        - source_labels: [__meta_kubernetes_node_name]
          regex: (.+)
          target_label: __metrics_path__
          replacement: /api/v1/nodes/${1}/proxy/metrics/cadvisor
{{- end }}

{{- if eq $mode "direct" }}
    - job_name: 'kubelet'
      scrape_interval: {{ .Values.prometheus.infraScrapeInterval }}
      scheme: https
      tls_config:
        ca_file: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
        insecure_skip_verify: true
      bearer_token_file: /var/run/secrets/kubernetes.io/serviceaccount/token
      kubernetes_sd_configs:
        - role: node
      relabel_configs:
        - action: labelmap
          regex: __meta_kubernetes_node_label_(.+)

    - job_name: 'cadvisor'
      scrape_interval: {{ .Values.prometheus.infraScrapeInterval }}
      scheme: https
      metrics_path: /metrics/cadvisor
      tls_config:
        ca_file: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
        insecure_skip_verify: true
      bearer_token_file: /var/run/secrets/kubernetes.io/serviceaccount/token
      kubernetes_sd_configs:
        - role: node
      relabel_configs:
        - action: labelmap
          regex: __meta_kubernetes_node_label_(.+)
{{- end }}

{{- if eq $mode "resource" }}
    - job_name: 'kubelet-resource'
      scrape_interval: {{ .Values.prometheus.infraScrapeInterval }}
      scheme: https
      metrics_path: /metrics/resource
      tls_config:
        ca_file: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
        insecure_skip_verify: true
      bearer_token_file: /var/run/secrets/kubernetes.io/serviceaccount/token
      kubernetes_sd_configs:
        - role: node
      relabel_configs:
        - action: labelmap
          regex: __meta_kubernetes_node_label_(.+)
{{- end }}

{{- if eq $mode "daemonset" }}
    - job_name: 'cadvisor-daemonset'
      scrape_interval: {{ .Values.prometheus.infraScrapeInterval }}
      kubernetes_sd_configs:
        - role: pod
          namespaces:
            names: ['{{ .Values.namespaces.monitoring | default .Release.Namespace }}']
      relabel_configs:
        - source_labels: [__meta_kubernetes_pod_label_app]
          action: keep
          regex: cadvisor
        - source_labels: [__meta_kubernetes_pod_node_name]
          target_label: node
        # The three k8s labels come through cAdvisor as
        # container_label_io_kubernetes_*. Rename them to pod/namespace/
        # container so the Grafana dashboards (which assume kubelet-style
        # labels) can match series.
      metric_relabel_configs:
        - source_labels: [container_label_io_kubernetes_pod_name]
          target_label: pod
        - source_labels: [container_label_io_kubernetes_pod_namespace]
          target_label: namespace
        - source_labels: [container_label_io_kubernetes_container_name]
          target_label: container
        - regex: container_label_io_kubernetes_(pod_name|pod_namespace|container_name)
          action: labeldrop
        # Pause/sandbox containers (container="POD") carry no useful signal.
        - source_labels: [container]
          regex: POD
          action: drop
{{- end }}

{{- if .Values.prometheus.externalTarget }}
    - job_name: 'external'
      static_configs:
        - targets: ['{{ .Values.prometheus.externalTarget }}']
          labels:
            source: 'external'
{{- end }}

alerts.yml: |
  groups:
    - name: demo-app-alerts
      rules:
        - alert: HighErrorRate
          expr: sum(rate(http_requests_total{status_code=~"5.*"}[1m])) / sum(rate(http_requests_total[1m])) > 0.1
          for: 15s
          labels:
            severity: warning
          annotations:
            summary: "High error rate detected (>10% 5xx errors)"
            description: "App error rate exceeded threshold."

        - alert: LatencySpike
          expr: histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[1m])) by (le)) > 0.8
          for: 15s
          labels:
            severity: warning
          annotations:
            summary: "p95 Latency spike (>800ms)"
            description: "App response time is degraded."

        - alert: LoadGenHighErrorRate
          expr: sum(rate(loadgen_errors_total[1m])) / sum(rate(loadgen_requests_total[1m])) > 0.15
          for: 20s
          labels:
            severity: warning
          annotations:
            summary: "Load Generator reports elevated failure rate (>15%)"
            description: "Client-side errors exceed threshold during load test."
{{- end }}
