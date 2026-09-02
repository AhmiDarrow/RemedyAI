//go:build !windows

package ipc

import (
	"context"
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
	accepted := make(chan error, 1)
	go func() {
		conn, err := listener.Accept()
		if err == nil {
			_ = conn.Close()
		}
		accepted <- err
	}()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	conn, err := Dial(ctx, endpoint)
	if err != nil {
		t.Fatal(err)
	}
	_ = conn.Close()
	if err := <-accepted; err != nil {
		t.Fatal(err)
	}
	if err := listener.Close(); err != nil {
		t.Fatal(err)
	}
}
