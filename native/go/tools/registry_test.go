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
	authorized := 0
	registry := NewRegistry(AuthorizerFunc(func(_ context.Context, descriptor Descriptor, request Request) error {
		authorized++
		if descriptor.Risk != RiskReadOnly || descriptor.Capabilities[0] != "filesystem.read" || descriptor.Permissions[0] != "workspace" || string(request.CapabilityToken) != "valid-token" {
			return errors.New("wrong authorization context")
		}
		return nil
	}))
	calls := 0
	executor := ExecutorFunc(func(_ context.Context, request Request) (Result, error) {
		calls++
		return Result{Output: json.RawMessage(`{"content":"ok"}`), Evidence: []byte("audit")}, nil
	})
	if err := registry.Register(descriptor(1, RuntimeZig), executor); err != nil {
		t.Fatal(err)
	}
	result, err := registry.Execute(context.Background(), Request{ToolID: "filesystem.read", Version: 1, Input: json.RawMessage(`{"path":"safe.txt"}`), CapabilityToken: []byte("valid-token")})
	if err != nil || string(result.Output) != `{"content":"ok"}` || calls != 1 || authorized != 1 {
		t.Fatalf("result=%s err=%v calls=%d authorized=%d", result.Output, err, calls, authorized)
	}
	if _, err := registry.Execute(context.Background(), Request{ToolID: "filesystem.read", Version: 1, Input: json.RawMessage(`{"wrong":true}`), CapabilityToken: []byte("valid-token")}); !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("input=%v", err)
	}
	if _, err := registry.Execute(context.Background(), Request{ToolID: "filesystem.read", Version: 1, Input: json.RawMessage(`{"path":"x"} {"path":"y"}`), CapabilityToken: []byte("valid-token")}); !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("trailing input = %v", err)
	}
	bad := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error { return nil }))
	_ = bad.Register(descriptor(1, RuntimeGo), ExecutorFunc(func(context.Context, Request) (Result, error) {
		return Result{Output: json.RawMessage(`{"wrong":true}`)}, nil
	}))
	if _, err := bad.Execute(context.Background(), Request{ToolID: "filesystem.read", Version: 1, Input: json.RawMessage(`{"path":"x"}`), CapabilityToken: []byte("valid-token")}); !errors.Is(err, ErrInvalidOutput) {
		t.Fatalf("output=%v", err)
	}
}

func TestRegistryFailsClosedForProtectedTools(t *testing.T) {
	protected := descriptor(1, RuntimeZig)
	registry := NewRegistry()
	if err := registry.Register(protected, ExecutorFunc(func(context.Context, Request) (Result, error) {
		return Result{Output: json.RawMessage(`{"content":"unsafe"}`)}, nil
	})); err != nil {
		t.Fatal(err)
	}
	request := Request{ToolID: protected.ID, Version: 1, Input: json.RawMessage(`{"path":"x"}`), CapabilityToken: []byte("token")}
	if _, err := registry.Execute(context.Background(), request); !errors.Is(err, ErrAuthorizationRequired) {
		t.Fatalf("without authorizer: %v", err)
	}
	request.CapabilityToken = nil
	if _, err := registry.Execute(context.Background(), request); !errors.Is(err, ErrAuthorizationRequired) {
		t.Fatalf("without token: %v", err)
	}
}

func TestRegistryDoesNotExecuteAfterAuthorizationFailure(t *testing.T) {
	calls := 0
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return errors.New("expired grant")
	}))
	protected := descriptor(1, RuntimeZig)
	if err := registry.Register(protected, ExecutorFunc(func(context.Context, Request) (Result, error) {
		calls++
		return Result{Output: json.RawMessage(`{"content":"unsafe"}`)}, nil
	})); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID: protected.ID, Version: protected.Version,
		Input: json.RawMessage(`{"path":"x"}`), CapabilityToken: []byte("expired-token"),
	})
	if !errors.Is(err, ErrUnauthorized) || calls != 0 {
		t.Fatalf("err=%v calls=%d", err, calls)
	}
}

func TestRegistryRejectsInvalidInputBeforeConsumingAuthorization(t *testing.T) {
	authorized := 0
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		authorized++
		return nil
	}))
	protected := descriptor(1, RuntimeZig)
	if err := registry.Register(protected, ExecutorFunc(func(context.Context, Request) (Result, error) {
		return Result{}, nil
	})); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID: protected.ID, Version: protected.Version,
		Input: json.RawMessage(`{"wrong":true}`), CapabilityToken: []byte("single-use"),
	})
	if !errors.Is(err, ErrInvalidInput) || authorized != 0 {
		t.Fatalf("err=%v authorized=%d", err, authorized)
	}
}

func TestRegistryAllowsOnlyUnrestrictedReadOnlyCompatibility(t *testing.T) {
	readOnly := descriptor(1, RuntimeGo)
	readOnly.Capabilities = nil
	readOnly.Permissions = nil
	registry := NewRegistry()
	if err := registry.Register(readOnly, ExecutorFunc(func(context.Context, Request) (Result, error) {
		return Result{Output: json.RawMessage(`{"content":"ok"}`)}, nil
	})); err != nil {
		t.Fatal(err)
	}
	if _, err := registry.Execute(context.Background(), Request{ToolID: readOnly.ID, Version: 1, Input: json.RawMessage(`{"path":"x"}`)}); err != nil {
		t.Fatal(err)
	}
	readOnly.Version = 2
	readOnly.Risk = RiskMutation
	if err := registry.Register(readOnly, ExecutorFunc(func(context.Context, Request) (Result, error) { return Result{}, nil })); err != nil {
		t.Fatal(err)
	}
	if _, err := registry.Execute(context.Background(), Request{ToolID: readOnly.ID, Version: 2, Input: json.RawMessage(`{"path":"x"}`)}); !errors.Is(err, ErrAuthorizationRequired) {
		t.Fatalf("mutation without authorization: %v", err)
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

func TestResolvedDescriptorCannotMutateAuthorizationMetadata(t *testing.T) {
	registry := NewRegistry()
	original := descriptor(1, RuntimeZig)
	if err := registry.Register(original, ExecutorFunc(func(context.Context, Request) (Result, error) { return Result{}, nil })); err != nil {
		t.Fatal(err)
	}
	resolved, err := registry.Resolve(original.ID, original.Version)
	if err != nil {
		t.Fatal(err)
	}
	resolved.Capabilities[0] = "filesystem.delete"
	resolved.Permissions[0] = "global"
	again, err := registry.Resolve(original.ID, original.Version)
	if err != nil {
		t.Fatal(err)
	}
	if again.Capabilities[0] != "filesystem.read" || again.Permissions[0] != "workspace" {
		t.Fatalf("registry metadata mutated: %#v", again)
	}
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
