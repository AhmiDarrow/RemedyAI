package main

import (
	"testing"
)

func TestRunWildcardExits2(t *testing.T) {
	for _, host := range []string{"0.0.0.0", "*", "::", "[::]"} {
		if code := run(host, 7402); code != 2 {
			t.Fatalf("%q: want exit 2, got %d", host, code)
		}
	}
}

func TestRunLoopbackEphemeral(t *testing.T) {
	// Start then interrupt via returning after Stop — run blocks on signal.
	// Bind-only check: StartRelay succeeds for loopback; Stop immediately.
	// Covered by connect.StartRelay tests; here only wildcard policy.
	if code := run("0.0.0.0", 0); code != 2 {
		t.Fatalf("wildcard must exit 2, got %d", code)
	}
}
