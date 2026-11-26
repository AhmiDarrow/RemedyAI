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

func TestNavigateRefusesLocalAPIInEverySpelling(t *testing.T) {
	for _, u := range []string{
		"http://127.0.0.1:7400/",
		"http://127.1:7400/",
		"http://0x7f.1:7400/",
		"http://0x7f000001:7400/",
		"http://2130706433:7400/",
		"http://0177.0.0.1:7400/",
		"http://127.0.0.2:7400/",
		"http://127.1:07400/",
		"http://127.0.0.1.:7400/",
		"http://0.0.0.0:7400/",
		"http://0:7400/",
		"http://[::1]:7400/",
		"http://[::ffff:127.0.0.1]:7400/",
		"http://[::ffff:7f00:1]:7400/",
		"http://LOCALHOST:7400/",
		"http://localhost.:7400/",
		"http://127.1:8080/api/settings",
	} {
		if isValidNavigateURL(u) {
			t.Errorf("%s must be refused", u)
		}
	}
	for _, u := range []string{
		"http://127.0.0.1:8080/",
		"http://localhost:3000/app",
		"https://example.com:7400/",
		"https://1.2.3.4:7400/",
		"https://0x7f.example.com:7400/",
	} {
		if !isValidNavigateURL(u) {
			t.Errorf("%s must stay allowed", u)
		}
	}
}

func TestWhatwgIPv4(t *testing.T) {
	cases := map[string]string{
		"127.1": "127.0.0.1", "0x7f.1": "127.0.0.1", "2130706433": "127.0.0.1",
		"0177.0.0.1": "127.0.0.1", "1.2.3.4": "1.2.3.4", "10.0x10.1": "10.16.0.1",
		"0x": "0.0.0.0",
	}
	for in, want := range cases {
		got, ok := whatwgIPv4(in)
		if !ok || got.String() != want {
			t.Errorf("whatwgIPv4(%q)=%v,%v want %s", in, got, ok, want)
		}
	}
	for _, in := range []string{"example.com", "1.2.3.4.5", "256.1.1.1", "1.2.65536", "08.1.1.1", "1..2", ""} {
		if _, ok := whatwgIPv4(in); ok {
			t.Errorf("whatwgIPv4(%q) should not parse", in)
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

func TestComputerSnapshotRailWhenHostConnected(t *testing.T) {
	home := t.TempDir()
	b := newHostBridge(home)
	b.markHostAlive(true, "rust")
	reg, err := NewDefaultToolRegistry(nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := RegisterRailTools(reg, func() *HostBridge { return b }); err != nil {
		t.Fatal(err)
	}
	if err := RegisterRailTools(reg, func() *HostBridge { return b }); err != nil {
		t.Fatal("wrap must be idempotent")
	}
	desc, err := reg.Latest("computer.snapshot")
	if err != nil {
		t.Fatal(err)
	}
	go func() {
		deadline := time.Now().Add(3 * time.Second)
		for time.Now().Before(deadline) {
			job := b.claimNext(nil, nil, "sess-1", 0.2)
			if job == nil {
				continue
			}
			if job.Action != "snapshot" {
				t.Errorf("claimed %s", job.Action)
				return
			}
			_ = b.completeA11yPush(job.ID, []map[string]any{
				{"ref": "e1", "name": "Place order", "role": "button", "x": 40, "y": 80},
			})
			return
		}
	}()
	out, err := reg.Execute(context.Background(), tools.Request{
		ToolID:          "computer.snapshot",
		Version:         desc.Version,
		Input:           json.RawMessage(`{"session_id":"sess-1","timeout_s":3}`),
		CapabilityToken: RuntimeCapabilityToken(desc),
	})
	if err != nil {
		t.Fatal(err)
	}
	var body map[string]any
	if json.Unmarshal(out.Output, &body) != nil {
		t.Fatalf("out=%s", out.Output)
	}
	if body["source"] != "browser" || body["available"] != true {
		t.Fatalf("snapshot=%s", out.Output)
	}
	controls, _ := body["controls"].([]any)
	if len(controls) != 1 {
		t.Fatalf("controls=%v", body["controls"])
	}
	el, _ := controls[0].(map[string]any)
	if el["name"] != "Place order" || el["id"] != "e1" {
		t.Fatalf("control=%v", el)
	}
}

func TestComputerNavigateRejectsTargetField(t *testing.T) {
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
	_, err = reg.Execute(context.Background(), tools.Request{
		ToolID:          "computer.navigate",
		Version:         1,
		Input:           json.RawMessage(`{"url":"https://example.com","target":"browser"}`),
		CapabilityToken: RuntimeCapabilityToken(desc),
	})
	if err == nil {
		t.Fatal("target= must fail schema validation")
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
