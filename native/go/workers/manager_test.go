package workers

import (
	"context"
	"errors"
	"sync/atomic"
	"testing"
	"time"
)

type fakeWorker struct {
	protocol int
	ready    bool
	call     func(context.Context, Request) (Response, error)
	closed   atomic.Bool
}

func (w *fakeWorker) Health(context.Context) (Health, error) {
	return Health{Protocol: w.protocol, Ready: w.ready}, nil
}
func (w *fakeWorker) Call(ctx context.Context, r Request) (Response, error) { return w.call(ctx, r) }
func (w *fakeWorker) Stream(ctx context.Context, r Request) (<-chan Response, error) {
	response, err := w.call(ctx, r)
	if err != nil {
		return nil, err
	}
	ch := make(chan Response, 1)
	ch <- response
	close(ch)
	return ch, nil
}
func (w *fakeWorker) Close() error { w.closed.Store(true); return nil }

func TestCapabilityRoutingAndCrashRestart(t *testing.T) {
	var connections atomic.Int32
	manager := New(FactoryFunc(func(context.Context, Spec) (Worker, error) {
		attempt := connections.Add(1)
		return &fakeWorker{protocol: ProtocolVersion, ready: true, call: func(context.Context, Request) (Response, error) {
			if attempt == 1 {
				return Response{}, errors.New("crash")
			}
			return Response{Payload: []byte("ok"), Final: true}, nil
		}}, nil
	}))
	defer manager.Close()
	if err := manager.Register(Spec{ID: "python-model", Capabilities: []Capability{Model, Vision}, MaxRestarts: 1}); err != nil {
		t.Fatal(err)
	}
	response, err := manager.Call(context.Background(), Request{Capability: Vision})
	if err != nil || string(response.Payload) != "ok" || connections.Load() != 2 {
		t.Fatalf("response=%#v err=%v connections=%d", response, err, connections.Load())
	}
	if _, err := manager.Call(context.Background(), Request{Capability: Speech}); !errors.Is(err, ErrNoCapability) {
		t.Fatalf("route=%v", err)
	}
}

func TestProtocolMismatchAndTimeout(t *testing.T) {
	manager := New(FactoryFunc(func(context.Context, Spec) (Worker, error) {
		return &fakeWorker{protocol: 99, ready: true, call: func(context.Context, Request) (Response, error) { return Response{}, nil }}, nil
	}))
	_ = manager.Register(Spec{ID: "old", Capabilities: []Capability{Research}})
	if _, err := manager.Call(context.Background(), Request{Capability: Research}); !errors.Is(err, ErrProtocolMismatch) {
		t.Fatalf("protocol=%v", err)
	}
	manager = New(FactoryFunc(func(context.Context, Spec) (Worker, error) {
		return &fakeWorker{protocol: ProtocolVersion, ready: true, call: func(ctx context.Context, _ Request) (Response, error) { <-ctx.Done(); return Response{}, ctx.Err() }}, nil
	}))
	_ = manager.Register(Spec{ID: "slow", Capabilities: []Capability{Speech}, CallTimeout: time.Millisecond})
	if _, err := manager.Call(context.Background(), Request{Capability: Speech}); err == nil {
		t.Fatal("timeout succeeded")
	}
}

func TestStreamingSurface(t *testing.T) {
	manager := New(FactoryFunc(func(context.Context, Spec) (Worker, error) {
		return &fakeWorker{protocol: ProtocolVersion, ready: true, call: func(context.Context, Request) (Response, error) {
			return Response{Payload: []byte("chunk"), Final: true}, nil
		}}, nil
	}))
	_ = manager.Register(Spec{ID: "model", Capabilities: []Capability{Model}})
	stream, err := manager.Stream(context.Background(), Request{Capability: Model})
	if err != nil {
		t.Fatal(err)
	}
	if response := <-stream; string(response.Payload) != "chunk" || !response.Final {
		t.Fatalf("response=%#v", response)
	}
}
