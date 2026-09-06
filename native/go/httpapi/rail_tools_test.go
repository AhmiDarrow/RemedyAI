package httpapi

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

func TestNormalizeNavigateURL(t *testing.T) {
	cases := []struct {
		in, want string
	}{
		{"gmail", "https://mail.google.com"},
		{"https://example.com/path", "https://example.com/path"},
		{"www.example.com", "https://www.example.com"},
		{"example.com", "https://example.com"},
		{"not a url at all", ""},
		{"https://user:pass@example.com/", ""},
		{"http://169.254.169.254/", ""},
		{"http://127.0.0.1:7400/api/settings", ""},
		{"about:blank", "about:blank"},
	}
	for _, c := range cases {
		if got := normalizeNavigateURL(c.in); got != c.want {
			t.Fatalf("normalize(%q)=%q want %q", c.in, got, c.want)
		}
	}
}

func TestComputerNavigateFailClosedHostOffline(t *testing.T) {
	home := t.TempDir()
	b := newHostBridge(home)
	reg := tools.NewRegistry(tools.AuthorizerFunc(runtimeLocalAuthorizer))
	if err := RegisterRailTools(reg, func() *HostBridge { return b }); err != nil {
		t.Fatal(err)
	}
	desc, err := reg.Latest("computer.navigate")
	if err != nil {
		t.Fatal(err)
	}
	if desc.Runtime != tools.RuntimeGo || desc.Risk != tools.RiskMutation {
		t.Fatalf("desc=%+v", desc)
	}
	out, err := reg.Execute(context.Background(), tools.Request{
		ToolID:          "computer.navigate",
		Version:         1,
		Input:           json.RawMessage(`{"url":"https://example.com"}`),
		CapabilityToken: RuntimeCapabilityToken(desc),
	})
	if err != nil {
		t.Fatal(err)
	}
	var body map[string]any
	if json.Unmarshal(out.Output, &body) != nil {
		t.Fatalf("out=%s", out.Output)
	}
	if body["ok"] != false {
		t.Fatalf("expected fail-closed offline: %s", out.Output)
	}
}

func TestComputerNavigateEnqueueAndComplete(t *testing.T) {
	home := t.TempDir()
	b := newHostBridge(home)
	b.markHostAlive(true, "rust")
	reg := tools.NewRegistry(tools.AuthorizerFunc(runtimeLocalAuthorizer))
	if err := RegisterRailTools(reg, func() *HostBridge { return b }); err != nil {
		t.Fatal(err)
	}
	desc, err := reg.Latest("computer.navigate")
	if err != nil {
		t.Fatal(err)
	}
	go func() {
		deadline := time.Now().Add(3 * time.Second)
		for time.Now().Before(deadline) {
			job := b.claimNext(nil, nil, "", 0.2)
			if job == nil {
				continue
			}
			b.complete(job.ID, true, map[string]any{"ok": true, "url": "https://example.com/"}, nil)
			return
		}
	}()
	out, err := reg.Execute(context.Background(), tools.Request{
		ToolID:          "computer.navigate",
		Version:         1,
		Input:           json.RawMessage(`{"url":"https://example.com","timeout_s":3}`),
		CapabilityToken: RuntimeCapabilityToken(desc),
	})
	if err != nil {
		t.Fatal(err)
	}
	var body map[string]any
	if json.Unmarshal(out.Output, &body) != nil {
		t.Fatalf("out=%s", out.Output)
	}
	if body["ok"] != true {
		t.Fatalf("navigate=%s", out.Output)
	}
}

func TestAttachRailToolsOnRunner(t *testing.T) {
	r := NewCognitionTurnRunner(&cognition.ScriptedModel{})
	home := t.TempDir()
	b := newHostBridge(home)
	if err := r.AttachRailTools(func() *HostBridge { return b }); err != nil {
		t.Fatal(err)
	}
	if _, err := r.Registry.Latest("computer.navigate"); err != nil {
		t.Fatal(err)
	}
	// Idempotent
	if err := r.AttachRailTools(func() *HostBridge { return b }); err != nil {
		t.Fatal(err)
	}
}

func TestLifeWallCleared(t *testing.T) {
	if lifeWallCleared("captcha", "Welcome home", "https://example.com/inbox", "https://example.com/challenge", true) != true {
		t.Fatal("expected cleared")
	}
	if lifeWallCleared("captcha", "please complete the captcha", "https://example.com/", "", true) {
		t.Fatal("captcha still present")
	}
	if lifeWallCleared("password", "", "https://accounts.google.com/signin", "", true) {
		t.Fatal("login url not cleared")
	}
	if lifeWallCleared("pay", "", "https://example.com", "", true) {
		t.Fatal("pay must not auto-clear")
	}
}
