package tools

import (
	"context"
	"encoding/json"
	"errors"
	"sync"
	"testing"
)

func descriptor(version uint32, runtime Runtime) Descriptor {
	return Descriptor{ID: "filesystem.read", Version: version, Description: "Read a scoped file", Runtime: runtime, Risk: RiskReadOnly, Capabilities: []string{"filesystem.read"}, Permissions: []string{"workspace"}, InputSchema: json.RawMessage(`{"type":"object","required":["path"],"properties":{"path":{"type":"string","minLength":1}},"additionalProperties":false}`), OutputSchema: json.RawMessage(`{"type":"object","required":["content"],"properties":{"content":{"type":"string"}},"additionalProperties":false}`)}
}

func TestRegistryValidatesInputAndOutput(t *testing.T) {
	registry := NewRegistry()
	calls := 0
	executor := ExecutorFunc(func(_ context.Context, request Request) (Result, error) {
		calls++
		return Result{Output: json.RawMessage(`{"content":"ok"}`), Evidence: []byte("audit")}, nil
	})
	if err := registry.Register(descriptor(1, RuntimeZig), executor); err != nil {
		t.Fatal(err)
	}
	result, err := registry.Execute(context.Background(), Request{ToolID: "filesystem.read", Version: 1, Input: json.RawMessage(`{"path":"safe.txt"}`), CapabilityToken: []byte("opaque")})
	if err != nil || string(result.Output) != `{"content":"ok"}` || calls != 1 {
		t.Fatalf("result=%s err=%v calls=%d", result.Output, err, calls)
	}
	if _, err := registry.Execute(context.Background(), Request{ToolID: "filesystem.read", Version: 1, Input: json.RawMessage(`{"wrong":true}`)}); !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("input=%v", err)
	}
	if _, err := registry.Execute(context.Background(), Request{ToolID: "filesystem.read", Version: 1, Input: json.RawMessage(`{"path":"x"} {"path":"y"}`)}); !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("trailing input = %v", err)
	}
	bad := NewRegistry()
	_ = bad.Register(descriptor(1, RuntimeGo), ExecutorFunc(func(context.Context, Request) (Result, error) {
		return Result{Output: json.RawMessage(`{"wrong":true}`)}, nil
	}))
	if _, err := bad.Execute(context.Background(), Request{ToolID: "filesystem.read", Version: 1, Input: json.RawMessage(`{"path":"x"}`)}); !errors.Is(err, ErrInvalidOutput) {
		t.Fatalf("output=%v", err)
	}
}

func TestRegistryVersionsAndConcurrentResolution(t *testing.T) {
	registry := NewRegistry()
	executor := ExecutorFunc(func(context.Context, Request) (Result, error) {
		return Result{Output: json.RawMessage(`{"content":"ok"}`)}, nil
	})
	for version := uint32(1); version <= 3; version++ {
		if err := registry.Register(descriptor(version, RuntimePython), executor); err != nil {
			t.Fatal(err)
		}
	}
	latest, err := registry.Latest("filesystem.read")
	if err != nil || latest.Version != 3 {
		t.Fatalf("latest=%#v err=%v", latest, err)
	}
	if err := registry.Register(descriptor(1, RuntimePython), executor); !errors.Is(err, ErrAlreadyRegistered) {
		t.Fatalf("duplicate=%v", err)
	}
	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, err := registry.Resolve("filesystem.read", 2); err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
}

func TestRegistrySupportsEveryExecutorRuntime(t *testing.T) {
	for _, runtime := range []Runtime{RuntimeGo, RuntimeZig, RuntimePython, RuntimeWASM} {
		registry := NewRegistry()
		if err := registry.Register(descriptor(1, runtime), ExecutorFunc(func(context.Context, Request) (Result, error) {
			return Result{Output: json.RawMessage(`{"content":"ok"}`)}, nil
		})); err != nil {
			t.Fatalf("%s: %v", runtime, err)
		}
	}
}
