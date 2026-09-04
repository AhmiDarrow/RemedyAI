package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const visionTestToken = "tok-vision-test-not-a-secret"

type fixtureVisionWorker struct {
	activated bool
	started   bool
	stopped   bool
	installed bool
	progress  map[string]any
}

func (f *fixtureVisionWorker) Activate(context.Context, string, bool) (map[string]any, error) {
	f.activated = true
	return map[string]any{"ok": true, "mode": "local_files", "message": "activated"}, nil
}

func (f *fixtureVisionWorker) Install(context.Context, string, string, string, bool) (map[string]any, error) {
	f.installed = true
	return map[string]any{"ok": true, "mode": "download", "started": true}, nil
}

func (f *fixtureVisionWorker) CancelInstall(context.Context, string) (map[string]any, error) {
	return map[string]any{"ok": true, "cancelled": true}, nil
}

func (f *fixtureVisionWorker) ReinstallRuntime(context.Context, string, bool) (map[string]any, error) {
	return map[string]any{"ok": true, "reinstall": true}, nil
}

func (f *fixtureVisionWorker) Uninstall(context.Context, string, bool) (map[string]any, error) {
	return map[string]any{"ok": true}, nil
}

func (f *fixtureVisionWorker) Start(context.Context, string) (map[string]any, error) {
	f.started = true
	return map[string]any{"ok": true, "started": true}, nil
}

func (f *fixtureVisionWorker) Stop(context.Context, string) (map[string]any, error) {
	f.stopped = true
	return map[string]any{"ok": true, "stopped": true}, nil
}

func (f *fixtureVisionWorker) Progress(context.Context, string) map[string]any {
	if f.progress != nil {
		return f.progress
	}
	return nil
}

func newVisionTestServer(t *testing.T, worker VisionWorker) (*Server, string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	s, err := New(Config{
		HomeDir:      home,
		Token:        visionTestToken,
		DBPath:       filepath.Join(home, "memory.db"),
		VisionWorker: worker,
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s, home
}

func doVisionJSON(t *testing.T, s *Server, method, path string, body any) (int, map[string]any, string) {
	t.Helper()
	var rdr *bytes.Reader
	if body != nil {
		raw, _ := json.Marshal(body)
		rdr = bytes.NewReader(raw)
	} else {
		rdr = bytes.NewReader(nil)
	}
	req := httptest.NewRequest(method, path, rdr)
	req.Header.Set("Authorization", "Bearer "+visionTestToken)
	req.Header.Set("Content-Type", "application/json")
	req.Host = "127.0.0.1:7400"
	req.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	var payload map[string]any
	_ = json.Unmarshal(rr.Body.Bytes(), &payload)
	return rr.Code, payload, rr.Body.String()
}

func TestVisionCatalogShape(t *testing.T) {
	s, _ := newVisionTestServer(t, nil)
	code, body, text := doVisionJSON(t, s, http.MethodGet, "/api/vision/catalog", nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["default_model_id"] != visionDefaultModelID {
		t.Fatalf("default=%v", body["default_model_id"])
	}
	models, ok := body["models"].([]any)
	if !ok || len(models) < 1 {
		t.Fatalf("models=%v", body["models"])
	}
	if body["llama_cpp_tag"] != visionLlamaCPPTag {
		t.Fatalf("tag=%v", body["llama_cpp_tag"])
	}
}

func TestVisionStatusFromDisk(t *testing.T) {
	s, home := newVisionTestServer(t, nil)
	models := filepath.Join(home, "vision", "models", visionDefaultModelID)
	_ = os.MkdirAll(models, 0o700)
	spec := visionModelByID(visionDefaultModelID)
	_ = os.WriteFile(filepath.Join(models, spec.ModelFile), []byte("gguf"), 0o600)
	_ = os.WriteFile(filepath.Join(models, spec.MMProjFile), []byte("mmproj"), 0o600)
	_ = os.MkdirAll(filepath.Join(home, "vision", "runtime"), 0o700)
	_ = os.WriteFile(filepath.Join(home, "vision", "runtime", "llama-server.exe"), []byte("x"), 0o600)
	_ = WriteConfig(filepath.Join(home, "config.toml"), ConfigMap{
		"vision": map[string]any{"enabled": true, "model_id": visionDefaultModelID},
	})
	InvalidateConfigCache()

	code, body, text := doVisionJSON(t, s, http.MethodGet, "/api/vision/status", nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["installed"] != true || body["enabled"] != true || body["ready"] != true {
		t.Fatalf("body=%s", text)
	}
	if body["model_id"] != visionDefaultModelID {
		t.Fatalf("model_id=%v", body["model_id"])
	}
	prog, _ := body["progress"].(map[string]any)
	if prog["phase"] != "idle" {
		t.Fatalf("progress=%v", prog)
	}
}

func TestVisionStatusFullIncludesCatalog(t *testing.T) {
	s, _ := newVisionTestServer(t, nil)
	code, body, text := doVisionJSON(t, s, http.MethodGet, "/api/vision/status?full=1", nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["catalog"] == nil {
		t.Fatalf("expected catalog: %s", text)
	}
	if body["health"] == nil {
		t.Fatalf("expected health: %s", text)
	}
}

func TestVisionMutateFailsClosedWithoutWorker(t *testing.T) {
	s, _ := newVisionTestServer(t, nil)
	for _, path := range []string{
		"/api/vision/activate",
		"/api/vision/install",
		"/api/vision/install/cancel",
		"/api/vision/reinstall-runtime",
		"/api/vision/uninstall",
		"/api/vision/start",
		"/api/vision/stop",
	} {
		code, body, text := doVisionJSON(t, s, http.MethodPost, path, map[string]any{})
		if code != http.StatusServiceUnavailable {
			t.Fatalf("%s status=%d body=%s", path, code, text)
		}
		if body["ok"] != false {
			t.Fatalf("%s body=%v", path, body)
		}
		errMsg, _ := body["error"].(string)
		if errMsg == "" || !strings.Contains(errMsg, "Vision worker") {
			t.Fatalf("%s error=%v", path, body["error"])
		}
	}
}

func TestVisionMutateWithWorker(t *testing.T) {
	w := &fixtureVisionWorker{}
	s, _ := newVisionTestServer(t, w)
	code, body, text := doVisionJSON(t, s, http.MethodPost, "/api/vision/activate", map[string]any{})
	if code != http.StatusOK || body["ok"] != true || !w.activated {
		t.Fatalf("activate %d %s activated=%v", code, text, w.activated)
	}
	code, body, text = doVisionJSON(t, s, http.MethodPost, "/api/vision/install", map[string]any{
		"prefer_cuda": true,
	})
	if code != http.StatusOK || body["ok"] != true || !w.installed {
		t.Fatalf("install %d %s", code, text)
	}
	code, body, text = doVisionJSON(t, s, http.MethodPost, "/api/vision/start", map[string]any{})
	if code != http.StatusOK || body["ok"] != true || !w.started {
		t.Fatalf("start %d %s", code, text)
	}
	code, body, text = doVisionJSON(t, s, http.MethodPost, "/api/vision/stop", map[string]any{})
	if code != http.StatusOK || body["ok"] != true || !w.stopped {
		t.Fatalf("stop %d %s", code, text)
	}
}
