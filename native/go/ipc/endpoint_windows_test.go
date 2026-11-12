//go:build windows

package ipc

import (
	"context"
	"fmt"
	"os"
	"testing"
	"time"
)

func TestWindowsNamedPipeRoundTrip(t *testing.T) {
	endpoint := fmt.Sprintf(`\\.\pipe\remedy-test-%d-%d`, os.Getpid(), time.Now().UnixNano())
	listener, err := Listen(endpoint)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()

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
		t.Fatal(err)
	}
	// Close the client only after Accept has the server end. Closing earlier
	// can leave go-winio's Accept waiting for a connection that already ended.
	select {
	case err := <-accepted:
		_ = conn.Close()
		if err != nil {
			t.Fatal(err)
		}
	case <-ctx.Done():
		_ = conn.Close()
		_ = listener.Close()
		t.Fatal("named pipe accept timed out")
	}
}

func TestAcceptContextUnblocksOnCancel(t *testing.T) {
	endpoint := fmt.Sprintf(`\\.\pipe\remedy-test-cancel-%d-%d`, os.Getpid(), time.Now().UnixNano())
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
