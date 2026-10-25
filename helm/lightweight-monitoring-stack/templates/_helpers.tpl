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
Effective nodeMetrics mode.
profile=laptop forces resource mode (light: CPU+memory only, ~50 series).
*/}}
{{- define "lightweight-monitoring-stack.nodeMetricsMode" -}}
{{- if eq .Values.profile "laptop" -}}
resource
{{- else -}}
{{- .Values.nodeMetrics.mode | default "off" }}
{{- end -}}
{{- end }}

{{/*
Effective Prometheus memory limit — capped at 256Mi when profile=laptop
*/}}
{{- define "lightweight-monitoring-stack.prometheusMemLimit" -}}
{{- if eq .Values.profile "laptop" -}}
256Mi
{{- else -}}
{{- .Values.prometheus.resources.limits.memory }}
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
