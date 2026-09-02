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
