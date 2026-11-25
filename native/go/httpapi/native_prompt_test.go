package httpapi

import (
	"os"
	"path/filepath"
	"testing"
)

func TestNativePromptRouting(t *testing.T) {
	cases := []struct {
		name    string
		cfg     ConfigMap
		missing []string
		want    bool
	}{
		{"off by default", ConfigMap{}, nil, false},
		{"off explicitly", ConfigMap{"native_prompt": false}, nil, false},
		{"on but a section is not ported", ConfigMap{"native_prompt": true}, []string{"metabolism"}, false},
		{"on and complete", ConfigMap{"native_prompt": true}, nil, true},
	}
	for _, c := range cases {
		if got := useNativePrompt(c.cfg, c.missing); got != c.want {
			t.Errorf("%s: useNativePrompt = %v, want %v", c.name, got, c.want)
		}
	}

	// Through the runner: assemble still has unported sections, so it stays
	// on the worker; a chat-mode should_continue is fully ported.
	home := t.TempDir()
	proj := filepath.Join(t.TempDir(), "proj")
	if err := os.MkdirAll(proj, 0o755); err != nil {
		t.Fatal(err)
	}
	r := &CognitionTurnRunner{HomeDir: home}
	if _, ok := r.nativePrompt("prompt.should_continue", map[string]any{"chat_mode": true}); ok {
		t.Fatal("native_prompt unset: the Go port must not answer")
	}
	cfg := "native_prompt = true\nproject_path = '" + proj + "'\n"
	if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte(cfg), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, ok := r.nativePrompt("prompt.assemble", map[string]any{"message": "hello", "session_id": "s1"}); ok {
		t.Fatal("assemble has unported sections; it must stay on the Python worker")
	}
	out, ok := r.nativePrompt("prompt.should_continue", map[string]any{"chat_mode": true})
	if !ok || out["reason"] != "chat_mode" || out["continue"] != false {
		t.Fatalf("chat-mode should_continue: ok=%v out=%v", ok, out)
	}
}
