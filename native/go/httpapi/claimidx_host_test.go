package httpapi

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestClaimidxSharingIsOffUnlessTheOwnerTurnsItOn(t *testing.T) {
	if claimidxPublic(ConfigMap{}) {
		t.Fatal("public sharing must be off by default")
	}
	if !claimidxEnabled(ConfigMap{}) {
		t.Fatal("the local host is on by default")
	}
	if claimidxPublic(ConfigMap{"claimidx_enabled": false, "claimidx_public_ledger": true}) {
		t.Fatal("the off switch turns sharing off too")
	}
	if !claimidxPublic(ConfigMap{"claimidx_public_ledger": true}) {
		t.Fatal("the owner can turn sharing on")
	}
	h := newClaimidxHost(t.TempDir())
	off, err := h.env(false)
	if err != nil {
		t.Fatal(err)
	}
	if off["CLAIMIDX_SHARE"] != "0" || off["CLAIMIDX_COMMONS"] != "0" {
		t.Fatalf("sharing off must be explicit in the host's environment: %v", off)
	}
	on, _ := h.env(true)
	if on["CLAIMIDX_SHARE"] != "1" {
		t.Fatalf("sharing on: %v", on)
	}
	// Its own config, database and token, under the home folder.
	for _, k := range []string{"CLAIMIDX_CONFIG", "CLAIMIDX_DB"} {
		if !strings.HasPrefix(off[k], h.root()) {
			t.Fatalf("%s must live under the managed folder: %s", k, off[k])
		}
	}
	if len(off["CLAIMIDX_HOME_TOKEN"]) < 32 || off["CLAIMIDX_HOME_TOKEN"] != on["CLAIMIDX_HOME_TOKEN"] {
		t.Fatal("the service token is generated once and kept")
	}
}

func TestClaimidxOffInstallsAndStartsNothing(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte("claimidx_enabled = false\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	ran := 0
	prev := claimidxExec
	claimidxExec = func(string, []string, map[string]string, string, time.Duration) (string, error) {
		ran++
		return "", nil
	}
	defer func() { claimidxExec = prev }()
	h := newClaimidxHost(home)
	if err := h.apply(); err != nil {
		t.Fatal(err)
	}
	if ran != 0 || h.installed() || fileExists(h.root()) {
		t.Fatalf("with ClaimIDX off nothing may run or be created (ran=%d)", ran)
	}
	st := h.Status()
	if st["enabled"] != false || st["running"] != false || st["public"] != false {
		t.Fatalf("status: %v", st)
	}
	// A server that does not manage a host has none, and every call is safe.
	var none *claimidxHost
	none.Apply()
	none.Stop()
}
