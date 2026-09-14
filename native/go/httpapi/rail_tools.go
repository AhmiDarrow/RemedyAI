package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"regexp"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// HostBridgeProvider supplies the live HostBridge for rail Tool ABI tools.
type HostBridgeProvider func() *HostBridge

var siteAliases = map[string]string{
	"gmail": "https://mail.google.com", "google": "https://www.google.com",
	"youtube": "https://www.youtube.com", "yt": "https://www.youtube.com",
	"github": "https://github.com", "gh": "https://github.com",
	"reddit": "https://www.reddit.com", "twitter": "https://x.com", "x": "https://x.com",
	"facebook": "https://www.facebook.com", "fb": "https://www.facebook.com",
	"instagram": "https://www.instagram.com", "linkedin": "https://www.linkedin.com",
	"maps": "https://maps.google.com", "drive": "https://drive.google.com",
	"docs": "https://docs.google.com", "calendar": "https://calendar.google.com",
	"wikipedia": "https://en.wikipedia.org", "wiki": "https://en.wikipedia.org",
	"amazon": "https://www.amazon.com", "walmart": "https://www.walmart.com",
	"netflix": "https://www.netflix.com",
}

var (
	bareDomainRE  = regexp.MustCompile(`(?i)^[a-z0-9.-]+\.[a-z]{2,}(/.*)?$`)
	metadataHosts = map[string]struct{}{
		"metadata.google.internal": {}, "metadata.goog": {}, "metadata": {},
		"instance-data": {}, "kubernetes.default.svc": {},
	}
)

// RegisterRailTools installs Browser-rail Tool ABI tools (HostBridge-backed).
func RegisterRailTools(registry *tools.Registry, bridge HostBridgeProvider) error {
	if registry == nil {
		return fmt.Errorf("%w: nil registry", tools.ErrInvalidDescriptor)
	}
	if bridge == nil {
		return errors.New("host bridge provider is required")
	}
	if _, err := registry.Latest("computer.navigate"); err != nil {
		if err := registry.Register(tools.Descriptor{
			ID:           "computer.navigate",
			Version:      1,
			Description:  "Open a URL in the in-app Browser rail (not the system browser)",
			Runtime:      tools.RuntimeGo,
			Risk:         tools.RiskMutation,
			Capabilities: []string{"computer.browser"},
			InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["url"],
			"properties":{
				"url":{"type":"string","minLength":1},
				"hint":{"type":"string"},
				"session_id":{"type":"string"},
				"timeout_s":{"type":"number","minimum":0.05,"maximum":120}
			},
			"additionalProperties":false
		}`),
			OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["ok","url"],
			"properties":{
				"ok":{"type":"boolean"},
				"url":{"type":"string"},
				"job_id":{"type":"string"},
				"status":{"type":"string"},
				"message":{"type":"string"},
				"error":{"type":"string"}
			},
			"additionalProperties":false
		}`),
		}, tools.ExecutorFunc(func(ctx context.Context, req tools.Request) (tools.Result, error) {
			return executeComputerNavigate(ctx, bridge, req)
		})); err != nil {
			return err
		}
	}
	return wrapComputerSnapshot(registry, bridge)
}

// snapshotRouter sends hwnd-less snapshots to the Browser rail when Desktop
// is connected, and otherwise keeps the Zig UIA/AT-SPI executor.
type snapshotRouter struct {
	inner  tools.Executor
	bridge HostBridgeProvider
}

func wrapComputerSnapshot(registry *tools.Registry, bridge HostBridgeProvider) error {
	if registry == nil || bridge == nil {
		return nil
	}
	if _, err := registry.Latest("computer.snapshot"); err != nil {
		return nil
	}
	if err := registry.WrapExecutor("computer.snapshot", 0, func(inner tools.Executor) tools.Executor {
		if existing, ok := inner.(*snapshotRouter); ok {
			existing.bridge = bridge
			return existing
		}
		return &snapshotRouter{inner: inner, bridge: bridge}
	}); err != nil {
		return err
	}
	return nil
}

func (s *snapshotRouter) Execute(ctx context.Context, req tools.Request) (tools.Result, error) {
	if s == nil || s.inner == nil {
		return tools.Result{}, tools.ErrToolNotFound
	}
	var body struct {
		HWND      *uint64 `json:"hwnd"`
		SessionID string  `json:"session_id"`
		TimeoutS  float64 `json:"timeout_s"`
	}
	if len(req.Input) > 0 {
		if err := json.Unmarshal(req.Input, &body); err != nil {
			return tools.Result{}, tools.ErrInvalidInput
		}
	}
	if body.HWND != nil && *body.HWND > 0 {
		return s.inner.Execute(ctx, req)
	}
	b := s.bridge()
	if b == nil || !b.hostConnected() {
		return s.inner.Execute(ctx, req)
	}
	return executeComputerSnapshotRail(ctx, b, req, body.SessionID, body.TimeoutS)
}

func executeComputerSnapshotRail(ctx context.Context, b *HostBridge, req tools.Request, sessionID string, timeoutS float64) (tools.Result, error) {
	_ = req
	sid := strings.TrimSpace(sessionID)
	job := b.Enqueue("snapshot", map[string]any{
		"ui":         map[string]any{"open_browser": true},
		"session_id": sid,
	}, sid)
	timeout := timeoutS
	if timeout <= 0 {
		timeout = 22
	}
	unclaimed := 8.0
	done := b.Wait(job.ID, WaitOptions{
		TimeoutS:          timeout,
		UnclaimedTimeoutS: &unclaimed,
		AbortCheck: func() bool {
			select {
			case <-ctx.Done():
				return true
			default:
				return false
			}
		},
	})
	if done == nil {
		return snapshotUnavailable("browser"), nil
	}
	if done.Status != "done" {
		return snapshotUnavailable("browser"), nil
	}
	controls := railResultControls(done.Result)
	out, err := json.Marshal(map[string]any{
		"source":    "browser",
		"available": true,
		"controls":  controls,
		"total":     len(controls),
	})
	return tools.Result{Output: out}, err
}

func snapshotUnavailable(source string) tools.Result {
	out, _ := json.Marshal(map[string]any{
		"source": source, "available": false, "controls": []any{}, "total": 0,
	})
	return tools.Result{Output: out}
}

func railResultControls(result map[string]any) []map[string]any {
	if result == nil {
		return []map[string]any{}
	}
	raw, ok := result["elements"]
	if !ok {
		raw = result["controls"]
	}
	list, ok := raw.([]any)
	if !ok {
		if typed, ok := raw.([]map[string]any); ok {
			out := make([]map[string]any, 0, len(typed))
			for _, el := range typed {
				if c := railElementToControl(el); c != nil {
					out = append(out, c)
				}
			}
			return out
		}
		return []map[string]any{}
	}
	out := make([]map[string]any, 0, len(list))
	for _, item := range list {
		el, ok := item.(map[string]any)
		if !ok {
			continue
		}
		if c := railElementToControl(el); c != nil {
			out = append(out, c)
		}
	}
	return out
}

func railElementToControl(el map[string]any) map[string]any {
	if el == nil {
		return nil
	}
	name := strOr(el["name"], "")
	if name == "" {
		name = strOr(el["text"], "")
	}
	if name == "" {
		name = strOr(el["label"], "")
	}
	c := map[string]any{"name": name, "role": strOr(el["role"], "")}
	if id := strOr(el["ref"], ""); id == "" {
		if id = strOr(el["id"], ""); id != "" {
			c["id"] = id
		}
	} else {
		c["id"] = id
	}
	if x, ok := jsonNumber(el["x"]); ok {
		c["x"] = x
	}
	if y, ok := jsonNumber(el["y"]); ok {
		c["y"] = y
	}
	return c
}

func jsonNumber(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case json.Number:
		f, err := n.Float64()
		return f, err == nil
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	default:
		return 0, false
	}
}

func executeComputerNavigate(ctx context.Context, bridge HostBridgeProvider, req tools.Request) (tools.Result, error) {
	var body struct {
		URL       string  `json:"url"`
		Hint      string  `json:"hint"`
		SessionID string  `json:"session_id"`
		TimeoutS  float64 `json:"timeout_s"`
	}
	if err := json.Unmarshal(req.Input, &body); err != nil {
		return tools.Result{}, tools.ErrInvalidInput
	}
	_ = body.Hint
	normalized := normalizeNavigateURL(body.URL)
	if normalized == "" {
		out, _ := json.Marshal(map[string]any{
			"ok": false, "url": "", "error": "invalid or blocked URL",
		})
		return tools.Result{Output: out}, nil
	}
	b := bridge()
	if b == nil {
		out, _ := json.Marshal(map[string]any{
			"ok": false, "url": normalized, "error": "host bridge unavailable",
		})
		return tools.Result{Output: out}, nil
	}
	if !b.hostConnected() {
		out, _ := json.Marshal(map[string]any{
			"ok": false, "url": normalized, "error": "Desktop host not connected — open Remedy Desktop so the Browser rail can load pages",
		})
		return tools.Result{Output: out}, nil
	}
	timeout := body.TimeoutS
	if timeout <= 0 {
		timeout = 30
	}
	sid := strings.TrimSpace(body.SessionID)
	job := b.Enqueue("navigate", map[string]any{
		"url":        normalized,
		"target":     "browser",
		"ui":         map[string]any{"open_browser": true},
		"session_id": sid,
	}, sid)

	select {
	case <-ctx.Done():
		b.cancel(job.ID)
		out, _ := json.Marshal(map[string]any{
			"ok": false, "url": normalized, "job_id": job.ID, "status": "cancelled", "error": "cancelled",
		})
		return tools.Result{Output: out}, nil
	default:
	}

	done := b.Wait(job.ID, WaitOptions{
		TimeoutS: timeout,
		// Navigate waits for Desktop complete; do not fail-fast on unclaimed.
		UnclaimedTimeoutS: nil,
		AbortCheck: func() bool {
			select {
			case <-ctx.Done():
				return true
			default:
				return false
			}
		},
	})
	if done == nil {
		out, _ := json.Marshal(map[string]any{
			"ok": false, "url": normalized, "job_id": job.ID, "error": "job missing after wait",
		})
		return tools.Result{Output: out}, nil
	}
	ok := done.Status == "done"
	msg := ""
	errMsg := ""
	if done.Error != nil {
		errMsg = *done.Error
	}
	if done.Result != nil {
		msg = strOr(done.Result["message"], "")
		if u := strings.TrimSpace(strOr(done.Result["url"], "")); u != "" {
			normalized = u
		}
	}
	if !ok && errMsg == "" {
		errMsg = "navigate failed"
	}
	out, _ := json.Marshal(map[string]any{
		"ok": ok, "url": normalized, "job_id": done.ID, "status": done.Status,
		"message": msg, "error": errMsg,
	})
	return tools.Result{Output: out}, nil
}

func normalizeNavigateURL(raw string) string {
	u := strings.TrimSpace(raw)
	if u == "" {
		return ""
	}
	lower := strings.ToLower(u)
	if alias, ok := siteAliases[lower]; ok {
		return alias
	}
	if strings.ContainsAny(u, " \n\t") && !strings.HasPrefix(lower, "about:") {
		first := strings.Fields(u)[0]
		first = strings.Trim(first, ",.;:")
		if alias, ok := siteAliases[strings.ToLower(first)]; ok {
			return alias
		}
		return ""
	}
	if strings.Contains(u, "@") && !strings.HasPrefix(lower, "http://") && !strings.HasPrefix(lower, "https://") {
		if alias, ok := siteAliases[lower]; ok {
			return alias
		}
		return ""
	}
	if strings.HasPrefix(lower, "about:") {
		if isValidNavigateURL(u) {
			return u
		}
		return ""
	}
	if strings.HasPrefix(lower, "http://") || strings.HasPrefix(lower, "https://") {
		if isValidNavigateURL(u) {
			return u
		}
		return ""
	}
	if strings.HasPrefix(lower, "www.") {
		cand := "https://" + u
		if isValidNavigateURL(cand) {
			return cand
		}
		return ""
	}
	if bareDomainRE.MatchString(u) {
		cand := "https://" + u
		if isValidNavigateURL(cand) {
			return cand
		}
	}
	return ""
}

func isValidNavigateURL(raw string) bool {
	u := strings.TrimSpace(raw)
	if u == "" {
		return false
	}
	hostPart := strings.SplitN(strings.SplitN(u, "?", 2)[0], "#", 2)[0]
	if strings.ContainsAny(hostPart, " \n\t,\"'") {
		return false
	}
	toParse := u
	if !strings.Contains(u, "://") && !strings.HasPrefix(strings.ToLower(u), "about:") {
		toParse = "https://" + u
	}
	p, err := url.Parse(toParse)
	if err != nil {
		return false
	}
	scheme := strings.ToLower(p.Scheme)
	if scheme != "http" && scheme != "https" && scheme != "about" {
		return false
	}
	if scheme == "about" {
		return true
	}
	if p.User != nil {
		return false
	}
	host := strings.ToLower(strings.TrimSpace(p.Hostname()))
	if host == "" || strings.Contains(host, " ") || strings.Contains(host, ",") {
		return false
	}
	if isBlockedMetadataHost(host) {
		return false
	}
	// Never load the local API into the rail.
	if (host == "127.0.0.1" || host == "localhost" || host == "::1") &&
		(p.Port() == "7400" || strings.HasPrefix(p.Path, "/api/")) {
		return false
	}
	return true
}

func isBlockedMetadataHost(host string) bool {
	h := strings.TrimSuffix(strings.ToLower(strings.TrimSpace(host)), ".")
	if h == "" {
		return false
	}
	if _, ok := metadataHosts[h]; ok {
		return true
	}
	if strings.HasSuffix(h, ".internal") || strings.HasSuffix(h, ".localhost") {
		return true
	}
	for _, label := range strings.Split(h, ".") {
		if label == "metadata" {
			return true
		}
	}
	for _, sfx := range []string{".nip.io", ".sslip.io", ".xip.io"} {
		if strings.HasSuffix(h, sfx) {
			return true
		}
	}
	return strings.HasPrefix(h, "169.254.")
}

// AttachRailTools registers Browser-rail tools on the cognition runner.
func (r *CognitionTurnRunner) AttachRailTools(bridge HostBridgeProvider) error {
	if r == nil || r.Registry == nil {
		return errors.New("cognition turn runner has no tool registry")
	}
	if err := RegisterRailTools(r.Registry, bridge); err != nil {
		return err
	}
	r.Tools = &RegistryToolExecutor{Registry: r.Registry, TokenFor: RuntimeCapabilityToken}
	r.Policy = &RegistryPolicy{Registry: r.Registry, Approvals: r.Approvals, LiveContext: r.LiveContext}
	r.syncModelToolSchemas()
	return nil
}
