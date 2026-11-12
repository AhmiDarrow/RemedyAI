//go:build !windows

package ipc

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestUnixSocketRoundTripAndCleanup(t *testing.T) {
	endpoint := filepath.Join(t.TempDir(), "remedy-test.sock")
	listener, err := Listen(endpoint)
	if err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	accepted := make(chan error, 1)
	go func() {
		conn, err := AcceptContext(ctx, listener)
		if err == nil {
			_ = conn.Close()
		}
		accepted <- err
	}()
	conn, err := Dial(ctx, endpoint)
	if err != nil {
		_ = listener.Close()
		t.Fatal(err)
	}
	select {
	case err := <-accepted:
		_ = conn.Close()
		if err != nil {
			_ = listener.Close()
			t.Fatal(err)
		}
	case <-ctx.Done():
		_ = conn.Close()
		_ = listener.Close()
		t.Fatal("unix socket accept timed out")
	}
	if err := listener.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Lstat(endpoint); !os.IsNotExist(err) {
		t.Fatalf("unix socket left behind: %v", err)
	}
}

func TestAcceptContextUnblocksOnCancel(t *testing.T) {
	endpoint := filepath.Join(t.TempDir(), "remedy-test-cancel.sock")
	listener, err := Listen(endpoint)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	start := time.Now()
	_, err = AcceptContext(ctx, listener)
	if err == nil {
		t.Fatal("AcceptContext must fail when ctx is done")
	}
	if time.Since(start) > 2*time.Second {
		t.Fatalf("AcceptContext did not fail fast: %s", time.Since(start))
	}
}
