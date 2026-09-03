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
