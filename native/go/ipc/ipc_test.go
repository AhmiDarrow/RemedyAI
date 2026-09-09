package ipc

import (
	"context"
	"errors"
	"net"
	"sync"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/protocol"
)

func TestConcurrentCallsRemainCorrelated(t *testing.T) {
	serverConn, clientConn := net.Pipe()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go ServeConn(ctx, serverConn, HandlerFunc(func(_ context.Context, request protocol.Frame) ([]protocol.Frame, error) {
		return []protocol.Frame{{Kind: protocol.KindToolResult, Payload: request.Payload}}, nil
	}))
	client := NewClient(clientConn)
	defer client.Close()

	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			var id [16]byte
			id[0] = byte(i + 1)
			response, err := client.Call(context.Background(), protocol.Frame{Kind: protocol.KindToolRequest, CorrelationID: id, Payload: []byte{byte(i)}})
			if err != nil {
				t.Error(err)
				return
			}
			if response.CorrelationID != id || len(response.Payload) != 1 || response.Payload[0] != byte(i) {
				t.Errorf("response %d = %#v", i, response)
			}
		}(i)
	}
	wg.Wait()
}

func TestCancellationReachesServerHandler(t *testing.T) {
	serverConn, clientConn := net.Pipe()
	serverCanceled := make(chan struct{})
	go ServeConn(context.Background(), serverConn, HandlerFunc(func(ctx context.Context, _ protocol.Frame) ([]protocol.Frame, error) {
		<-ctx.Done()
		close(serverCanceled)
		return nil, ctx.Err()
	}))
	client := NewClient(clientConn)
	defer client.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()
	_, err := client.Call(ctx, protocol.Frame{Kind: protocol.KindToolRequest, CorrelationID: [16]byte{1}})
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("Call = %v", err)
	}
	select {
	case <-serverCanceled:
	case <-time.After(time.Second):
		t.Fatal("server handler was not canceled")
	}
}

func TestDisconnectUnblocksPendingCall(t *testing.T) {
	serverConn, clientConn := net.Pipe()
	client := NewClient(clientConn)
	done := make(chan error, 1)
	go func() {
		_, err := client.Call(context.Background(), protocol.Frame{Kind: protocol.KindHealth, CorrelationID: [16]byte{2}})
		done <- err
	}()
	_ = serverConn.Close()
	select {
	case err := <-done:
		if !errors.Is(err, ErrDisconnected) && err == nil {
			t.Fatalf("Call = %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("pending call remained blocked")
	}
}

func TestServerRejectsDuplicateActiveCorrelationID(t *testing.T) {
	serverConn, clientConn := net.Pipe()
	release := make(chan struct{})
	started := make(chan struct{})
	go ServeConn(context.Background(), serverConn, HandlerFunc(func(_ context.Context, request protocol.Frame) ([]protocol.Frame, error) {
		close(started)
		<-release
		return []protocol.Frame{{Kind: protocol.KindToolResult, Payload: request.Payload}}, nil
	}))
	defer clientConn.Close()
	id := [16]byte{7}
	first := protocol.Frame{Kind: protocol.KindToolRequest, CorrelationID: id, Payload: []byte("first")}
	if err := protocol.WriteFrame(clientConn, first); err != nil {
		t.Fatal(err)
	}
	<-started
	wroteDuplicate := make(chan error, 1)
	go func() {
		wroteDuplicate <- protocol.WriteFrame(clientConn, protocol.Frame{Kind: protocol.KindToolRequest, CorrelationID: id, Payload: []byte("second")})
	}()
	rejected, err := protocol.ReadFrame(clientConn)
	if err != nil {
		t.Fatal(err)
	}
	if err := <-wroteDuplicate; err != nil {
		t.Fatal(err)
	}
	if rejected.Flags != 1 || string(rejected.Payload) != ErrDuplicateCall.Error() {
		t.Fatalf("duplicate response = %#v", rejected)
	}
	close(release)
	original, err := protocol.ReadFrame(clientConn)
	if err != nil {
		t.Fatal(err)
	}
	if string(original.Payload) != "first" {
		t.Fatalf("original response = %#v", original)
	}
}

func TestServeConnCancellationClosesIdleConnection(t *testing.T) {
	serverConn, clientConn := net.Pipe()
	defer clientConn.Close()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	invoked := make(chan struct{}, 1)
	go func() {
		ServeConn(ctx, serverConn, HandlerFunc(func(context.Context, protocol.Frame) ([]protocol.Frame, error) {
			invoked <- struct{}{}
			return nil, nil
		}))
		close(done)
	}()
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("ServeConn remained blocked after context cancellation")
	}
	select {
	case <-invoked:
		t.Fatal("idle connection invoked handler")
	default:
	}
}

func TestCallEnqueueRespectsContextWhenWriterBlocked(t *testing.T) {
	// A peer that never reads: the first write parks the writer goroutine;
	// further Calls must still return on ctx cancellation instead of
	// blocking on a write mutex.
	serverConn, clientConn := net.Pipe()
	defer serverConn.Close()
	client := NewClient(clientConn)
	defer client.Close()

	fill := make(chan struct{})
	for i := 0; i < writeQueueDepth+4; i++ {
		go func(i int) {
			var id [16]byte
			id[0] = byte(i + 1)
			id[1] = 1
			ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
			defer cancel()
			_, _ = client.Call(ctx, protocol.Frame{Kind: protocol.KindToolRequest, CorrelationID: id})
			fill <- struct{}{}
		}(i)
	}
	deadline := time.After(5 * time.Second)
	for i := 0; i < writeQueueDepth+4; i++ {
		select {
		case <-fill:
		case <-deadline:
			t.Fatal("Call blocked behind a stalled writer instead of honoring ctx")
		}
	}
}

func TestLateResponseAfterCancelIsDropped(t *testing.T) {
	serverConn, clientConn := net.Pipe()
	release := make(chan struct{})
	go ServeConn(context.Background(), serverConn, HandlerFunc(func(_ context.Context, request protocol.Frame) ([]protocol.Frame, error) {
		<-release
		return []protocol.Frame{{Kind: protocol.KindToolResult, Payload: request.Payload}}, nil
	}))
	client := NewClient(clientConn)
	defer client.Close()

	id := [16]byte{42}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if _, err := client.Call(ctx, protocol.Frame{Kind: protocol.KindToolRequest, CorrelationID: id, Payload: []byte("late")}); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("Call = %v", err)
	}
	close(release) // handler now emits the late response (handler ignores ctx)

	// A new call with a fresh id must get its own payload, never the stale one.
	fresh := [16]byte{43}
	got, err := client.Call(context.Background(), protocol.Frame{Kind: protocol.KindToolRequest, CorrelationID: fresh, Payload: []byte("fresh")})
	if err != nil {
		t.Fatal(err)
	}
	if got.CorrelationID != fresh || string(got.Payload) != "fresh" {
		t.Fatalf("fresh call got %#v", got)
	}
}

func TestClientDoneClosesOnPeerEOF(t *testing.T) {
	serverConn, clientConn := net.Pipe()
	client := NewClient(clientConn)
	defer client.Close()
	_ = serverConn.Close()
	select {
	case <-client.Done():
	case <-time.After(time.Second):
		t.Fatal("Done not closed after peer EOF")
	}
	if _, err := client.Call(context.Background(), protocol.Frame{Kind: protocol.KindHealth, CorrelationID: [16]byte{5}}); !errors.Is(err, ErrDisconnected) {
		t.Fatalf("Call after EOF = %v", err)
	}
}
