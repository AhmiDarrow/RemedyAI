package connect

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestGetTailscaleStatusFailClosed(t *testing.T) {
	prev := zigTailscaleStatus
	t.Cleanup(func() { zigTailscaleStatus = prev })
	zigTailscaleStatus = func() ([]byte, error) {
		return nil, errors.New("core missing")
	}
	st := GetTailscaleStatus()
	if st.Installed || st.Running || st.LoggedIn {
		t.Fatalf("expected fail-closed zeros, got %+v", st)
	}
	if !strings.Contains(st.Error, "core missing") {
		t.Fatalf("error=%q", st.Error)
	}
}

func TestGetTailscaleStatusParsesCoreJSON(t *testing.T) {
	prev := zigTailscaleStatus
	t.Cleanup(func() { zigTailscaleStatus = prev })
	zigTailscaleStatus = func() ([]byte, error) {
		return []byte(`{"installed":true,"running":true,"logged_in":true,"tailnet_ipv4":"100.1.2.3","version":"1.2.3","error":""}`), nil
	}
	st := GetTailscaleStatus()
	if !st.Installed || !st.LoggedIn || st.TailnetIPv4 != "100.1.2.3" || st.Version != "1.2.3" {
		t.Fatalf("got %+v", st)
	}
}

func TestStartTailscaleLoginParsesURL(t *testing.T) {
	prev := zigTailscaleLogin
	t.Cleanup(func() { zigTailscaleLogin = prev })
	zigTailscaleLogin = func() ([]byte, error) {
		return []byte(`{"status":"needs_login","message":"Open the sign-in link","login_url":"https://login.tailscale.com/a/abc","msi_path":"","installer_url":""}`), nil
	}
	act := StartTailscaleLogin()
	if act.Status != "needs_login" || !strings.HasPrefix(act.LoginURL, "https://login.tailscale.com/") {
		t.Fatalf("got %+v", act)
	}
}

func TestEnsureTailscaleAlreadyInstalled(t *testing.T) {
	prevStatus := zigTailscaleStatus
	prevLaunch := zigTailscaleLaunch
	t.Cleanup(func() {
		zigTailscaleStatus = prevStatus
		zigTailscaleLaunch = prevLaunch
	})
	zigTailscaleStatus = func() ([]byte, error) {
		return []byte(`{"installed":true,"running":false,"logged_in":false,"tailnet_ipv4":"","version":"","error":"x"}`), nil
	}
	zigTailscaleLaunch = func(string) (uint32, error) {
		t.Fatal("launch must not run when installed")
		return 0, nil
	}
	act := EnsureTailscale(t.TempDir())
	if act.Status != "already_installed" {
		t.Fatalf("got %+v", act)
	}
}

func TestEnsureTailscaleLaunchesDownloadedMSI(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("Windows MSI path")
	}
	prevStatus := zigTailscaleStatus
	prevLaunch := zigTailscaleLaunch
	prevFeed := latestWindowsMSIURLFn
	t.Cleanup(func() {
		zigTailscaleStatus = prevStatus
		zigTailscaleLaunch = prevLaunch
		latestWindowsMSIURLFn = prevFeed
	})
	zigTailscaleStatus = func() ([]byte, error) {
		return []byte(`{"installed":false,"running":false,"logged_in":false,"tailnet_ipv4":"","version":"","error":"missing"}`), nil
	}
	dir := t.TempDir()
	name := "tailscale-setup-9.9.9-amd64.msi"
	dest := filepath.Join(dir, name)
	if err := os.WriteFile(dest, []byte("msi"), 0o600); err != nil {
		t.Fatal(err)
	}
	latestWindowsMSIURLFn = func() (string, error) {
		return "https://pkgs.tailscale.com/stable/" + name, nil
	}
	var launched string
	zigTailscaleLaunch = func(path string) (uint32, error) {
		launched = path
		return 42, nil
	}
	act := EnsureTailscale(dir)
	if act.Status != "downloading" {
		t.Fatalf("got %+v", act)
	}
	if launched != dest {
		t.Fatalf("launched=%q want %q", launched, dest)
	}
}

func TestTailscaleStatusJSONShape(t *testing.T) {
	st := TailscaleStatus{Installed: true, TailnetIPv4: "100.0.0.1"}
	raw, err := json.Marshal(st)
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"installed", "running", "logged_in", "tailnet_ipv4", "version", "error"} {
		if _, ok := m[k]; !ok {
			t.Fatalf("missing %s", k)
		}
	}
}
