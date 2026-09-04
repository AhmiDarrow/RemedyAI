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

const voiceTestToken = "tok-voice-test-not-a-secret"

type fixtureVoiceWorker struct {
	speakWAV []byte
	text     string
	started  bool
}

func (f *fixtureVoiceWorker) Speak(context.Context, string, VoiceSpeakRequest, string) (*VoiceSpeakResult, error) {
	if len(f.speakWAV) == 0 {
		return nil, nil
	}
	return &VoiceSpeakResult{WAV: f.speakWAV, SampleRate: 24000}, nil
}

func (f *fixtureVoiceWorker) Transcribe(context.Context, string, []byte, string, string) (*VoiceTranscribeResult, error) {
	if f.text == "" {
		return nil, nil
	}
	return &VoiceTranscribeResult{Text: f.text, Language: "en"}, nil
}

func (f *fixtureVoiceWorker) Install(context.Context, string, string) (bool, error) {
	f.started = true
	return true, nil
}

func newVoiceTestServer(t *testing.T, worker VoiceWorker) (*Server, string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	s, err := New(Config{
		HomeDir:     home,
		Token:       voiceTestToken,
		DBPath:      filepath.Join(home, "memory.db"),
		VoiceWorker: worker,
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s, home
}

func doVoiceJSON(t *testing.T, s *Server, method, path string, body any) (int, map[string]any, string, http.Header) {
	t.Helper()
	var rdr *bytes.Reader
	if body != nil {
		raw, _ := json.Marshal(body)
		rdr = bytes.NewReader(raw)
	} else {
		rdr = bytes.NewReader(nil)
	}
	req := httptest.NewRequest(method, path, rdr)
	req.Header.Set("Authorization", "Bearer "+voiceTestToken)
	req.Header.Set("Content-Type", "application/json")
	req.Host = "127.0.0.1:7400"
	req.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	var payload map[string]any
	_ = json.Unmarshal(rr.Body.Bytes(), &payload)
	return rr.Code, payload, rr.Body.String(), rr.Header()
}

func TestVoiceStatusShape(t *testing.T) {
	s, home := newVoiceTestServer(t, nil)
	ttsDir := filepath.Join(home, "voice", "tts")
	_ = os.MkdirAll(ttsDir, 0o700)
	_ = os.WriteFile(filepath.Join(ttsDir, "kokoro-v1.0.onnx"), []byte("x"), 0o600)
	_ = os.WriteFile(filepath.Join(ttsDir, "voices-v1.0.bin"), []byte("y"), 0o600)

	code, body, text, _ := doVoiceJSON(t, s, http.MethodGet, "/api/voice/status", nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	tts, _ := body["tts"].(map[string]any)
	if tts["available"] != true || tts["engine"] != "kokoro-82m" {
		t.Fatalf("tts=%v", tts)
	}
	settings, _ := body["settings"].(map[string]any)
	if settings["tts_enabled"] != true {
		t.Fatalf("settings=%v", settings)
	}
	if _, ok := body["smart_turn"].(map[string]any); !ok {
		t.Fatalf("missing smart_turn: %s", text)
	}
}

func TestVoiceSettingsRoundTrip(t *testing.T) {
	s, _ := newVoiceTestServer(t, nil)
	code, body, text, _ := doVoiceJSON(t, s, http.MethodPost, "/api/voice/settings", map[string]any{
		"speak_replies": true, "speed": 1.25, "voice_override": "af_sky",
	})
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["speak_replies"] != true || body["voice_override"] != "af_sky" {
		t.Fatalf("body=%v", body)
	}
	code, status, text, _ := doVoiceJSON(t, s, http.MethodGet, "/api/voice/status", nil)
	if code != http.StatusOK {
		t.Fatalf("status get=%d %s", code, text)
	}
	settings, _ := status["settings"].(map[string]any)
	if settings["speak_replies"] != true {
		t.Fatalf("persisted settings=%v", settings)
	}
}

func TestVoiceSpeakFallbackWithoutWorker(t *testing.T) {
	s, _ := newVoiceTestServer(t, nil)
	code, body, text, _ := doVoiceJSON(t, s, http.MethodPost, "/api/voice/speak", map[string]any{
		"text": "hello",
	})
	if code != http.StatusServiceUnavailable {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["fallback"] != "browser" {
		t.Fatalf("body=%v", body)
	}
}

func TestVoiceSpeakWithWorker(t *testing.T) {
	wav := []byte("RIFF....WAVEfmt ")
	s, _ := newVoiceTestServer(t, &fixtureVoiceWorker{speakWAV: wav})
	code, _, text, hdr := doVoiceJSON(t, s, http.MethodPost, "/api/voice/speak", map[string]any{
		"text": "hello",
	})
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if !strings.Contains(hdr.Get("Content-Type"), "audio/wav") {
		t.Fatalf("content-type=%s", hdr.Get("Content-Type"))
	}
	if hdr.Get("X-Sample-Rate") != "24000" {
		t.Fatalf("sample-rate=%s", hdr.Get("X-Sample-Rate"))
	}
}

func TestVoiceClientLogAndInstall(t *testing.T) {
	w := &fixtureVoiceWorker{}
	s, _ := newVoiceTestServer(t, w)
	code, body, text, _ := doVoiceJSON(t, s, http.MethodPost, "/api/voice/client-log", map[string]any{
		"message": "playback failed",
	})
	if code != http.StatusOK || body["ok"] != true {
		t.Fatalf("client-log %d %s", code, text)
	}
	code, body, text, _ = doVoiceJSON(t, s, http.MethodPost, "/api/voice/install", map[string]any{
		"component": "tts",
	})
	if code != http.StatusOK || body["ok"] != true || body["started"] != true {
		t.Fatalf("install %d %s", code, text)
	}
	if !w.started {
		t.Fatal("worker Install not called")
	}
}

func TestVoiceTranscribeWithWorker(t *testing.T) {
	s, _ := newVoiceTestServer(t, &fixtureVoiceWorker{text: "hello world"})
	req := httptest.NewRequest(http.MethodPost, "/api/voice/transcribe", bytes.NewReader([]byte("fake-audio")))
	req.Header.Set("Authorization", "Bearer "+voiceTestToken)
	req.Header.Set("Content-Type", "audio/webm")
	req.Host = "127.0.0.1:7400"
	req.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	if rr.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", rr.Code, rr.Body.String())
	}
	var body map[string]any
	_ = json.Unmarshal(rr.Body.Bytes(), &body)
	if body["text"] != "hello world" {
		t.Fatalf("body=%v", body)
	}
}
