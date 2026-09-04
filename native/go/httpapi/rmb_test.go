package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

const rmbTestToken = "tok-rmb-test-not-a-secret"

type fixtureRmbController struct {
	started bool
	stopped bool
}

func (f *fixtureRmbController) Start(context.Context, string) (map[string]any, error) {
	f.started = true
	return map[string]any{"ok": true, "started": true}, nil
}

func (f *fixtureRmbController) Stop(context.Context, string) (map[string]any, error) {
	f.stopped = true
	return map[string]any{"ok": true, "stopped": true}, nil
}

func (f *fixtureRmbController) ApplyLive(context.Context, string, map[string]any) (map[string]any, error) {
	return map[string]any{
		"live_apply": map[string]any{"live": true, "restarted": true},
	}, nil
}

func newRmbTestServer(t *testing.T, ctrl RmbController) (*Server, string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	s, err := New(Config{
		HomeDir:       home,
		Token:         rmbTestToken,
		DBPath:        filepath.Join(home, "memory.db"),
		RmbController: ctrl,
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s, home
}

func doRmbJSON(t *testing.T, s *Server, method, path string, body any) (int, map[string]any, string) {
	t.Helper()
	var rdr *bytes.Reader
	if body != nil {
		raw, _ := json.Marshal(body)
		rdr = bytes.NewReader(raw)
	} else {
		rdr = bytes.NewReader(nil)
	}
	req := httptest.NewRequest(method, path, rdr)
	req.Header.Set("Authorization", "Bearer "+rmbTestToken)
	req.Header.Set("Content-Type", "application/json")
	req.Host = "127.0.0.1:7400"
	req.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	var payload map[string]any
	_ = json.Unmarshal(rr.Body.Bytes(), &payload)
	return rr.Code, payload, rr.Body.String()
}

func TestRmbCatalogShape(t *testing.T) {
	s, _ := newRmbTestServer(t, nil)
	code, body, text := doRmbJSON(t, s, http.MethodGet, "/api/rmb/catalog", nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["default_model_id"] != defaultRMBModelID {
		t.Fatalf("default=%v", body["default_model_id"])
	}
	models, ok := body["models"].([]any)
	if !ok || len(models) < 3 {
		t.Fatalf("models=%v", body["models"])
	}
	profiles, ok := body["profiles"].(map[string]any)
	if !ok || profiles["autofit"] == nil {
		t.Fatalf("profiles=%v", body["profiles"])
	}
}

func TestRmbStatusFromDisk(t *testing.T) {
	s, home := newRmbTestServer(t, nil)
	models := filepath.Join(home, "rmb", "models")
	_ = os.MkdirAll(models, 0o700)
	gguf := filepath.Join(models, "Qwen3.5-9B-Q6_K.gguf")
	_ = os.WriteFile(gguf, []byte("gguf-bytes"), 0o600)
	_ = saveRmbJSON(home, mergeRmbState(map[string]any{
		"enabled": true, "model_id": "qwen35-9b", "model_path": gguf,
	}))

	code, body, text := doRmbJSON(t, s, http.MethodGet, "/api/rmb/status", nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["ok"] != true || body["brand"] != "RMB" {
		t.Fatalf("body=%s", text)
	}
	if body["model_present"] != true {
		t.Fatalf("model_present=%v", body["model_present"])
	}
	if body["chat_model"] != "Qwen3.5-9B-Q6_K" {
		t.Fatalf("chat_model=%v", body["chat_model"])
	}
	engine, _ := body["engine"].(map[string]any)
	if engine["thinking"] != "on" {
		t.Fatalf("engine=%v", engine)
	}
}

func TestRmbSettingsPersist(t *testing.T) {
	s, home := newRmbTestServer(t, nil)
	code, body, text := doRmbJSON(t, s, http.MethodPost, "/api/rmb/settings", map[string]any{
		"enabled": true, "ctx_size": 16384, "profile": "quality",
	})
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["ctx_size"] != float64(16384) {
		t.Fatalf("ctx_size=%v body=%s", body["ctx_size"], text)
	}
	disk := mergeRmbState(loadRmbJSON(home))
	if anyInt(disk["ctx_size"], 0) != 16384 {
		t.Fatalf("disk ctx=%v", disk["ctx_size"])
	}
	if anyString(disk["profile"]) != "quality" {
		t.Fatalf("disk profile=%v", disk["profile"])
	}
}

func TestRmbStartRequiresController(t *testing.T) {
	s, _ := newRmbTestServer(t, nil)
	code, body, text := doRmbJSON(t, s, http.MethodPost, "/api/rmb/start", nil)
	if code != http.StatusServiceUnavailable {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["ok"] != false {
		t.Fatalf("body=%v", body)
	}
}

func TestRmbStartWithController(t *testing.T) {
	ctrl := &fixtureRmbController{}
	s, _ := newRmbTestServer(t, ctrl)
	code, body, text := doRmbJSON(t, s, http.MethodPost, "/api/rmb/start", nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["ok"] != true || !ctrl.started {
		t.Fatalf("body=%v started=%v", body, ctrl.started)
	}
}

func TestRmbStopMarksDisk(t *testing.T) {
	ctrl := &fixtureRmbController{}
	s, home := newRmbTestServer(t, ctrl)
	_ = saveRmbJSON(home, mergeRmbState(map[string]any{"enabled": true, "auto_start": true}))
	code, body, text := doRmbJSON(t, s, http.MethodPost, "/api/rmb/stop", nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if !ctrl.stopped || body["ok"] != true {
		t.Fatalf("body=%v stopped=%v", body, ctrl.stopped)
	}
	disk := loadRmbJSON(home)
	if !anyBoolDef(disk["user_stopped"], false) {
		t.Fatalf("user_stopped not set: %v", disk)
	}
}

func TestRmbUseBindsProvider(t *testing.T) {
	ctrl := &fixtureRmbController{}
	s, home := newRmbTestServer(t, ctrl)
	code, body, text := doRmbJSON(t, s, http.MethodPost, "/api/rmb/use", nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if !ctrl.started {
		t.Fatalf("start not called body=%s", text)
	}
	cfg := LoadConfig(home)
	if cfgString(cfg, "llm_provider", "") != "rmb" {
		t.Fatalf("provider=%v body=%s", cfg["llm_provider"], text)
	}
	_ = body
}

func TestRmbHfProgressIdle(t *testing.T) {
	s, _ := newRmbTestServer(t, nil)
	code, body, text := doRmbJSON(t, s, http.MethodGet, "/api/rmb/hf/progress", nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	prog, _ := body["progress"].(map[string]any)
	if prog["phase"] != "idle" {
		t.Fatalf("progress=%v", prog)
	}
}

func TestRmbHfCancelIdle(t *testing.T) {
	s, _ := newRmbTestServer(t, nil)
	code, body, text := doRmbJSON(t, s, http.MethodPost, "/api/rmb/hf/cancel", nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["ok"] != false {
		t.Fatalf("body=%v", body)
	}
}

func TestRmbHfParseHint(t *testing.T) {
	h, err := parseHFHint("unsloth/Qwen3.5-9B-GGUF")
	if err != nil {
		t.Fatal(err)
	}
	if h.Kind != "repo" || h.Repo != "unsloth/Qwen3.5-9B-GGUF" {
		t.Fatalf("hint=%+v", h)
	}
	h, err = parseHFHint("https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q6_K.gguf")
	if err != nil {
		t.Fatal(err)
	}
	if h.Kind != "url" || h.Filename == "" || h.URL == "" {
		t.Fatalf("url hint=%+v", h)
	}
}

func TestRmbSettingsRefusesWhileStreaming(t *testing.T) {
	s, _ := newRmbTestServer(t, nil)
	if _, _, ok := s.claims.TryClaim("sid-rmb-busy"); !ok {
		t.Fatal("claim")
	}
	t.Cleanup(func() { s.claims.Release("sid-rmb-busy", nil) })
	code, _, text := doRmbJSON(t, s, http.MethodPost, "/api/rmb/settings", map[string]any{
		"ctx_size": 32768,
	})
	if code != http.StatusConflict {
		t.Fatalf("status=%d body=%s", code, text)
	}
}
