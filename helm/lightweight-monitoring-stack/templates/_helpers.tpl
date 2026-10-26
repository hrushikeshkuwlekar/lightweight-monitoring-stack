{{/*
Chart name
*/}}
{{- define "lightweight-monitoring-stack.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fullname
*/}}
{{- define "lightweight-monitoring-stack.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "lightweight-monitoring-stack.labels" -}}
helm.sh/chart: {{ include "lightweight-monitoring-stack.name" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}

{{/*
Demo app image tag — defaults to Chart.AppVersion
*/}}
{{- define "lightweight-monitoring-stack.appImageTag" -}}
{{- .Values.demoApp.image.tag | default .Chart.AppVersion }}
{{- end }}

{{/*
Effective service type for a given component.
Pass the per-component serviceType as the argument; expose.type overrides it when set.
Usage: {{ include "lightweight-monitoring-stack.svcType" (list . .Values.prometheus.serviceType) }}
*/}}
{{- define "lightweight-monitoring-stack.svcType" -}}
{{- $ctx := index . 0 -}}
{{- $perSvc := index . 1 -}}
{{- if eq $ctx.Values.profile "laptop" -}}
{{- coalesce $ctx.Values.expose.type "LoadBalancer" }}
{{- else -}}
{{- coalesce $ctx.Values.expose.type $perSvc | default "ClusterIP" }}
{{- end -}}
{{- end }}

{{/*
Effective nodeMetrics mode. profile=laptop upgrades the default "off" to
"resource" (CPU + memory, ~50 series) but respects any mode you choose,
so laptop + daemonset works.
*/}}
{{- define "lightweight-monitoring-stack.nodeMetricsMode" -}}
{{- $mode := .Values.nodeMetrics.mode | default "off" -}}
{{- if and (eq .Values.profile "laptop") (eq $mode "off") -}}
resource
{{- else -}}
{{- $mode }}
{{- end -}}
{{- end }}

{{/*
Effective Grafana anonymousAuth — forced true when profile=laptop
*/}}
{{- define "lightweight-monitoring-stack.grafanaAnonymousAuth" -}}
{{- if eq .Values.profile "laptop" -}}
true
{{- else -}}
{{- .Values.grafana.anonymousAuth }}
{{- end -}}
{{- end }}

{{/*
Effective size preset — profile=laptop implies "small" when size is unset.
*/}}
{{- define "lightweight-monitoring-stack.size" -}}
{{- if .Values.size -}}
{{- .Values.size }}
{{- else if eq .Values.profile "laptop" -}}
small
{{- else -}}

{{- end -}}
{{- end }}

{{/*
Render a resources: block for a component honoring the active size preset.
Falls back to the per-component resources map when size is unset.
Usage:
  resources:
    {{- include "lightweight-monitoring-stack.resources" (dict "ctx" . "component" "prometheus") | nindent 10 }}
*/}}
{{- define "lightweight-monitoring-stack.resources" -}}
{{- $ctx := .ctx -}}
{{- $component := .component -}}
{{- $size := include "lightweight-monitoring-stack.size" $ctx -}}
{{- $presets := dict
    "nano" (dict
      "demoApp"           (dict "req" (dict "cpu" "10m" "memory" "25Mi") "lim" (dict "cpu" "150m" "memory" "60Mi"))
      "loadGenerator"     (dict "req" (dict "cpu" "10m" "memory" "25Mi") "lim" (dict "cpu" "100m" "memory" "60Mi"))
      "prometheus"        (dict "req" (dict "cpu" "20m" "memory" "40Mi") "lim" (dict "cpu" "150m" "memory" "180Mi"))
      "grafana"           (dict "req" (dict "cpu" "30m" "memory" "70Mi") "lim" (dict "cpu" "300m" "memory" "260Mi"))
      "kubeStateMetrics"  (dict "req" (dict "cpu" "10m" "memory" "20Mi") "lim" (dict "cpu" "80m"  "memory" "48Mi")))
    "small" (dict
      "demoApp"           (dict "req" (dict "cpu" "20m" "memory" "40Mi") "lim" (dict "cpu" "250m" "memory" "80Mi"))
      "loadGenerator"     (dict "req" (dict "cpu" "20m" "memory" "40Mi") "lim" (dict "cpu" "200m" "memory" "100Mi"))
      "prometheus"        (dict "req" (dict "cpu" "30m" "memory" "60Mi") "lim" (dict "cpu" "200m" "memory" "256Mi"))
      "grafana"           (dict "req" (dict "cpu" "50m" "memory" "100Mi") "lim" (dict "cpu" "500m" "memory" "400Mi"))
      "kubeStateMetrics"  (dict "req" (dict "cpu" "10m" "memory" "32Mi") "lim" (dict "cpu" "100m" "memory" "64Mi")))
    "standard" (dict
      "demoApp"           (dict "req" (dict "cpu" "50m"  "memory" "80Mi")  "lim" (dict "cpu" "500m" "memory" "200Mi"))
      "loadGenerator"     (dict "req" (dict "cpu" "50m"  "memory" "80Mi")  "lim" (dict "cpu" "500m" "memory" "200Mi"))
      "prometheus"        (dict "req" (dict "cpu" "100m" "memory" "256Mi") "lim" (dict "cpu" "1"    "memory" "1Gi"))
      "grafana"           (dict "req" (dict "cpu" "100m" "memory" "200Mi") "lim" (dict "cpu" "1"    "memory" "600Mi"))
      "kubeStateMetrics"  (dict "req" (dict "cpu" "25m"  "memory" "64Mi")  "lim" (dict "cpu" "200m" "memory" "128Mi")))
}}
{{- if hasKey $presets $size -}}
{{- $preset := index (index $presets $size) $component -}}
requests:
  cpu: {{ $preset.req.cpu }}
  memory: {{ $preset.req.memory }}
limits:
  cpu: {{ $preset.lim.cpu }}
  memory: {{ $preset.lim.memory }}
{{- else -}}
{{- $r := index $ctx.Values $component "resources" -}}
requests:
  cpu: {{ $r.requests.cpu }}
  memory: {{ $r.requests.memory }}
limits:
  cpu: {{ $r.limits.cpu }}
  memory: {{ $r.limits.memory }}
{{- end -}}
{{- end }}
