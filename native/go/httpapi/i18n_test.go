package httpapi

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"
)

const i18nTestToken = "tok-i18n-test-not-a-secret"

func newI18nTestServer(t *testing.T) (*Server, string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	s, err := New(Config{
		HomeDir: home,
		Token:   i18nTestToken,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s, home
}

func doI18nGET(t *testing.T, s *Server, path string) (int, map[string]any) {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, path, nil)
	req.Header.Set("Authorization", "Bearer "+i18nTestToken)
	req.Host = "127.0.0.1:7400"
	req.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	var payload map[string]any
	_ = json.Unmarshal(rr.Body.Bytes(), &payload)
	return rr.Code, payload
}

func TestI18nHintResolvesPortuguese(t *testing.T) {
	s, _ := newI18nTestServer(t)
	code, body := doI18nGET(t, s, "/api/i18n?lang=auto&hint=pt-BR")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%v", code, body)
	}
	if body["resolved"] != "pt" {
		t.Fatalf("resolved=%v want pt", body["resolved"])
	}
	if body["ui_language"] != "auto" {
		t.Fatalf("ui_language=%v", body["ui_language"])
	}
}

func TestI18nSpanishCatalogOverlay(t *testing.T) {
	s, _ := newI18nTestServer(t)
	code, body := doI18nGET(t, s, "/api/i18n?lang=es")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%v", code, body)
	}
	if body["resolved"] != "es" {
		t.Fatalf("resolved=%v", body["resolved"])
	}
	catalog, _ := body["catalog"].(map[string]any)
	if catalog["bar.help"] != "Ayuda" {
		t.Fatalf("bar.help=%v want Ayuda", catalog["bar.help"])
	}
	langs, _ := body["languages"].([]any)
	if len(langs) < 60 {
		t.Fatalf("languages=%d", len(langs))
	}
	var hasAuto, hasYo bool
	for _, row := range langs {
		m, _ := row.(map[string]any)
		switch m["id"] {
		case "auto":
			hasAuto = true
		case "yo":
			hasYo = true
		}
	}
	if !hasAuto || !hasYo {
		t.Fatalf("missing language rows auto=%v yo=%v", hasAuto, hasYo)
	}
}

func TestI18nJapaneseFromSettings(t *testing.T) {
	s, _ := newI18nTestServer(t)
	if _, err := s.applySettingsUpdate(map[string]any{"ui_language": "ja"}); err != nil {
		t.Fatal(err)
	}
	code, body := doI18nGET(t, s, "/api/i18n")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%v", code, body)
	}
	if body["resolved"] != "ja" {
		t.Fatalf("resolved=%v", body["resolved"])
	}
	catalog, _ := body["catalog"].(map[string]any)
	if catalog["settings.save"] != "保存" {
		t.Fatalf("settings.save=%v want 保存", catalog["settings.save"])
	}
}

func TestSettingsIncludesUILanguages(t *testing.T) {
	s, _ := newI18nTestServer(t)
	code, body := doI18nGET(t, s, "/api/settings")
	if code != http.StatusOK {
		t.Fatalf("status=%d", code)
	}
	langs, ok := body["ui_languages"].([]any)
	if !ok || len(langs) < 60 {
		t.Fatalf("ui_languages=%v", body["ui_languages"])
	}
}
