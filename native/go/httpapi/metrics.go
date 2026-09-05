package httpapi

import (
	"fmt"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
)

// MetricsRegistry is a process-local counter/gauge/histogram store for /api/metrics.
type MetricsRegistry struct {
	mu         sync.Mutex
	counters   map[string]*metricCounter
	gauges     map[string]*metricGauge
	histograms map[string]*metricHistogram
}

type metricCounter struct {
	mu     sync.Mutex
	name   string
	labels map[string]string
	value  float64
}

type metricGauge struct {
	mu     sync.Mutex
	name   string
	labels map[string]string
	value  float64
}

type metricHistogram struct {
	mu      sync.Mutex
	name    string
	labels  map[string]string
	buckets []float64
	counts  []int
	sum     float64
}

var defaultMetricBuckets = []float64{0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0}

// DefaultMetrics is the process-wide registry used by /api/metrics.
var DefaultMetrics = NewMetricsRegistry()

func NewMetricsRegistry() *MetricsRegistry {
	return &MetricsRegistry{
		counters:   map[string]*metricCounter{},
		gauges:     map[string]*metricGauge{},
		histograms: map[string]*metricHistogram{},
	}
}

func metricKey(name string, labels map[string]string) string {
	if len(labels) == 0 {
		return name
	}
	keys := make([]string, 0, len(labels))
	for k := range labels {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, k+"="+labels[k])
	}
	return name + "{" + strings.Join(parts, ",") + "}"
}

func sanitizeMetricLabels(labels map[string]string) map[string]string {
	if len(labels) == 0 {
		return map[string]string{}
	}
	out := make(map[string]string, len(labels))
	for k, v := range labels {
		key := k
		if len(key) > 32 {
			key = key[:32]
		}
		out[key] = sanitizeMetricLabelValue(v)
	}
	return out
}

func sanitizeMetricLabelValue(v string) string {
	const max = 64
	s := v
	if len(s) > max {
		s = s[:max-1] + "…"
	}
	if looksLikeSecretLabel(s) {
		return "[redacted]"
	}
	return s
}

func looksLikeSecretLabel(s string) bool {
	if strings.HasPrefix(s, "sk-") && len(s) >= 20 {
		return true
	}
	if len(s) >= 24 {
		hasDigit, hasAlpha := false, false
		for _, r := range s {
			if r >= '0' && r <= '9' {
				hasDigit = true
			}
			if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') {
				hasAlpha = true
			}
		}
		if hasDigit && hasAlpha && !strings.Contains(s, " ") {
			return true
		}
	}
	return false
}

func (r *MetricsRegistry) Counter(name string, labels map[string]string) *metricCounter {
	safe := sanitizeMetricLabels(labels)
	key := metricKey(name, safe)
	r.mu.Lock()
	defer r.mu.Unlock()
	if c, ok := r.counters[key]; ok {
		return c
	}
	c := &metricCounter{name: name, labels: safe}
	r.counters[key] = c
	return c
}

func (c *metricCounter) Inc(amount float64) {
	if amount == 0 {
		amount = 1
	}
	c.mu.Lock()
	c.value += amount
	c.mu.Unlock()
}

func (r *MetricsRegistry) Gauge(name string, labels map[string]string) *metricGauge {
	safe := sanitizeMetricLabels(labels)
	key := metricKey(name, safe)
	r.mu.Lock()
	defer r.mu.Unlock()
	if g, ok := r.gauges[key]; ok {
		return g
	}
	g := &metricGauge{name: name, labels: safe}
	r.gauges[key] = g
	return g
}

func (g *metricGauge) Set(v float64) {
	g.mu.Lock()
	g.value = v
	g.mu.Unlock()
}

func (r *MetricsRegistry) Histogram(name string, labels map[string]string) *metricHistogram {
	safe := sanitizeMetricLabels(labels)
	key := metricKey(name, safe)
	r.mu.Lock()
	defer r.mu.Unlock()
	if h, ok := r.histograms[key]; ok {
		return h
	}
	h := &metricHistogram{
		name:    name,
		labels:  safe,
		buckets: append([]float64{}, defaultMetricBuckets...),
		counts:  make([]int, len(defaultMetricBuckets)+1),
	}
	r.histograms[key] = h
	return h
}

func (h *metricHistogram) Observe(v float64) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.sum += v
	for i, b := range h.buckets {
		if v <= b {
			h.counts[i]++
			return
		}
	}
	h.counts[len(h.counts)-1]++
}

func (r *MetricsRegistry) Snapshot() map[string]any {
	r.mu.Lock()
	counters := make([]*metricCounter, 0, len(r.counters))
	for _, c := range r.counters {
		counters = append(counters, c)
	}
	gauges := make([]*metricGauge, 0, len(r.gauges))
	for _, g := range r.gauges {
		gauges = append(gauges, g)
	}
	histograms := make([]*metricHistogram, 0, len(r.histograms))
	for _, h := range r.histograms {
		histograms = append(histograms, h)
	}
	r.mu.Unlock()

	counterOut := make([]map[string]any, 0, len(counters))
	for _, c := range counters {
		c.mu.Lock()
		counterOut = append(counterOut, map[string]any{
			"name": c.name, "value": c.value, "labels": cloneStringMap(c.labels),
		})
		c.mu.Unlock()
	}
	gaugeOut := make([]map[string]any, 0, len(gauges))
	for _, g := range gauges {
		g.mu.Lock()
		gaugeOut = append(gaugeOut, map[string]any{
			"name": g.name, "value": g.value, "labels": cloneStringMap(g.labels),
		})
		g.mu.Unlock()
	}
	histOut := make([]map[string]any, 0, len(histograms))
	for _, h := range histograms {
		h.mu.Lock()
		count := 0
		buckets := make([]map[string]any, 0, len(h.counts))
		for i, c := range h.counts {
			count += c
			le := "+Inf"
			if i < len(h.buckets) {
				le = fmt.Sprint(h.buckets[i])
			}
			buckets = append(buckets, map[string]any{"le": le, "count": c})
		}
		histOut = append(histOut, map[string]any{
			"name": h.name, "sum": h.sum, "count": count,
			"buckets": buckets, "labels": cloneStringMap(h.labels),
		})
		h.mu.Unlock()
	}
	return map[string]any{
		"counters":   counterOut,
		"gauges":     gaugeOut,
		"histograms": histOut,
	}
}

func (r *MetricsRegistry) Describe() []string {
	snap := r.Snapshot()
	out := make([]string, 0)
	for _, c := range snap["counters"].([]map[string]any) {
		out = append(out, fmt.Sprintf("counter %v = %v", c["name"], c["value"]))
	}
	for _, g := range snap["gauges"].([]map[string]any) {
		out = append(out, fmt.Sprintf("gauge %v = %v", g["name"], g["value"]))
	}
	for _, h := range snap["histograms"].([]map[string]any) {
		out = append(out, fmt.Sprintf("histogram %v count=%v sum=%.2f", h["name"], h["count"], h["sum"]))
	}
	return out
}

func (r *MetricsRegistry) PrometheusText() string {
	snap := r.Snapshot()
	lines := make([]string, 0)
	seen := map[string]struct{}{}

	labelStr := func(labels map[string]string) string {
		if len(labels) == 0 {
			return ""
		}
		keys := make([]string, 0, len(labels))
		for k := range labels {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		parts := make([]string, 0, len(keys))
		for _, k := range keys {
			parts = append(parts, fmt.Sprintf(`%s="%s"`, k, promEscape(labels[k])))
		}
		return "{" + strings.Join(parts, ",") + "}"
	}
	asLabels := func(v any) map[string]string {
		if m, ok := v.(map[string]string); ok {
			return m
		}
		return map[string]string{}
	}

	for _, c := range snap["counters"].([]map[string]any) {
		name := anyString(c["name"])
		if _, ok := seen[name]; !ok {
			lines = append(lines, "# HELP "+name+" Remedy counter")
			lines = append(lines, "# TYPE "+name+" counter")
			seen[name] = struct{}{}
		}
		lines = append(lines, fmt.Sprintf("%s%s %v", name, labelStr(asLabels(c["labels"])), c["value"]))
	}
	for _, g := range snap["gauges"].([]map[string]any) {
		name := anyString(g["name"])
		if _, ok := seen[name]; !ok {
			lines = append(lines, "# HELP "+name+" Remedy gauge")
			lines = append(lines, "# TYPE "+name+" gauge")
			seen[name] = struct{}{}
		}
		lines = append(lines, fmt.Sprintf("%s%s %v", name, labelStr(asLabels(g["labels"])), g["value"]))
	}
	for _, h := range snap["histograms"].([]map[string]any) {
		name := anyString(h["name"])
		if _, ok := seen[name]; !ok {
			lines = append(lines, "# HELP "+name+" Remedy histogram")
			lines = append(lines, "# TYPE "+name+" histogram")
			seen[name] = struct{}{}
		}
		base := asLabels(h["labels"])
		cum := 0
		buckets, _ := h["buckets"].([]map[string]any)
		for _, b := range buckets {
			cum += int(anyFloat(b["count"], 0))
			lbl := cloneStringMap(base)
			lbl["le"] = anyString(b["le"])
			lines = append(lines, fmt.Sprintf("%s_bucket%s %d", name, labelStr(lbl), cum))
		}
		lines = append(lines, fmt.Sprintf("%s_sum%s %v", name, labelStr(base), h["sum"]))
		lines = append(lines, fmt.Sprintf("%s_count%s %v", name, labelStr(base), h["count"]))
	}
	if len(lines) == 0 {
		return ""
	}
	return strings.Join(lines, "\n") + "\n"
}

func promEscape(v string) string {
	v = strings.ReplaceAll(v, `\`, `\\`)
	v = strings.ReplaceAll(v, "\n", `\n`)
	v = strings.ReplaceAll(v, `"`, `\"`)
	return v
}

func cloneStringMap(m map[string]string) map[string]string {
	if m == nil {
		return map[string]string{}
	}
	out := make(map[string]string, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

func agencyMetricsRollup(snap map[string]any) map[string]any {
	totals := map[string]float64{
		"tool_calls":            0,
		"tool_success":          0,
		"tool_soft_errors":      0,
		"tool_errors":           0,
		"tool_batch_errors":     0,
		"tool_batch_exceptions": 0,
		"tool_recovery_nudges":  0,
		"skill_activate_ok":     0,
		"skill_auto_suggest":    0,
		"skill_run_ok":          0,
		"skill_run_error":       0,
	}
	nameMap := map[string]string{
		"remedy_tool_calls_total":               "tool_calls",
		"remedy_tool_success_total":             "tool_success",
		"remedy_tool_soft_errors_total":         "tool_soft_errors",
		"remedy_tool_errors_total":              "tool_errors",
		"remedy_tool_batch_errors_total":        "tool_batch_errors",
		"remedy_tool_batch_exceptions_total":    "tool_batch_exceptions",
		"remedy_tool_recovery_nudge_total":      "tool_recovery_nudges",
		"remedy_skill_auto_suggest_inject_total": "skill_auto_suggest",
	}
	counters, _ := snap["counters"].([]map[string]any)
	for _, c := range counters {
		name := anyString(c["name"])
		val := anyFloat(c["value"], 0)
		if dest, ok := nameMap[name]; ok {
			totals[dest] += val
			continue
		}
		switch name {
		case "remedy_skill_activate_total":
			totals["skill_activate_ok"] += val
		case "remedy_skill_run_total":
			labels, _ := c["labels"].(map[string]string)
			if labels == nil {
				if raw, ok := c["labels"].(map[string]any); ok {
					labels = map[string]string{}
					for k, v := range raw {
						labels[k] = fmt.Sprint(v)
					}
				}
			}
			st := ""
			if labels != nil {
				st = labels["status"]
			}
			if st == "ok" {
				totals["skill_run_ok"] += val
			} else {
				totals["skill_run_error"] += val
			}
		}
	}
	out := make(map[string]any, len(totals))
	for k, v := range totals {
		out[k] = v
	}
	return out
}

func (s *Server) metricsHealth() map[string]any {
	uptime := time.Since(s.started).Seconds()
	if uptime < 0 {
		uptime = 0
	}
	return map[string]any{
		"status":         "ok",
		"uptime_seconds": uptime,
		"checks":         map[string]any{},
		"checked_at":     time.Now().UTC().Format(time.RFC3339Nano),
	}
}

func (s *Server) handleMetrics(w http.ResponseWriter, r *http.Request) {
	wantProm := false
	format := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("format")))
	switch format {
	case "prometheus", "prom", "text":
		wantProm = true
	}
	if !wantProm {
		accept := strings.ToLower(r.Header.Get("Accept"))
		if strings.Contains(accept, "text/plain") && !strings.Contains(accept, "application/json") {
			wantProm = true
		}
	}
	if wantProm {
		body := DefaultMetrics.PrometheusText()
		w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(body))
		return
	}
	snap := DefaultMetrics.Snapshot()
	writeJSON(w, http.StatusOK, map[string]any{
		"version": s.version,
		"metrics": snap,
		"agency":  agencyMetricsRollup(snap),
		"health":  s.metricsHealth(),
		"lines":   DefaultMetrics.Describe(),
	})
}
