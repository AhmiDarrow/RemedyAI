package rdna

import (
	"context"
	"errors"
	"testing"
)

func validIntent() Intent {
	return Intent{Version: Version, ID: "intent-1", Action: AcquireInformation, Target: "release status", Constraints: Constraints{Capabilities: []string{"network.read"}, ReadOnly: true, NoCredentials: true}, Expected: []string{"evidence"}, Fallbacks: []Intent{{Version: Version, ID: "fallback", Action: AcquireInformation, Target: "cached status", Constraints: Constraints{ReadOnly: true}, Expected: []string{"source"}}}}
}
func TestCanonicalCompileIsDeterministicAndPreservesPolicy(t *testing.T) {
	intent := validIntent()
	bindings := map[Action]ToolBinding{AcquireInformation: {ToolID: "web.read", Version: 1}}
	first, err := Compile(intent, bindings)
	if err != nil {
		t.Fatal(err)
	}
	second, _ := Compile(intent, bindings)
	if first.IntentHash != second.IntentHash {
		t.Fatal("nondeterministic hash")
	}
	if !first.Root.Constraints.ReadOnly || !first.Root.Constraints.NoCredentials || len(first.Root.Fallbacks) != 1 {
		t.Fatalf("plan=%#v", first)
	}
}
func TestInvalidFamiliesAndVersionRejected(t *testing.T) {
	intent := validIntent()
	intent.Version = 2
	if err := Validate(intent); !errors.Is(err, ErrUnsupportedVersion) {
		t.Fatalf("version=%v", err)
	}
	intent = validIntent()
	intent.Action = ChangeState
	if err := Validate(intent); !errors.Is(err, ErrInvalidIntent) {
		t.Fatalf("read-only mutation=%v", err)
	}
	intent = validIntent()
	intent.Action = Communicate
	intent.Constraints.OwnerCheckpoint = false
	if err := Validate(intent); !errors.Is(err, ErrInvalidIntent) {
		t.Fatalf("communication=%v", err)
	}
	if _, err := Decode([]byte(`{"version":1,"id":"x","action":"acquire_information","target":"x","expected":["x"],"constraints":{},"unknown":true}`)); err == nil {
		t.Fatal("unknown field accepted")
	}
}

type executorFunc func(context.Context, Step) error

func (f executorFunc) ExecuteStep(ctx context.Context, step Step) error { return f(ctx, step) }
func TestVMIsDisabledByDefault(t *testing.T) {
	plan, _ := Compile(validIntent(), map[Action]ToolBinding{AcquireInformation: {ToolID: "web.read", Version: 1}})
	called := false
	vm := VM{Executor: executorFunc(func(context.Context, Step) error { called = true; return nil })}
	if err := vm.Execute(context.Background(), plan); !errors.Is(err, ErrExperimentalDisabled) {
		t.Fatalf("Execute=%v", err)
	}
	if called {
		t.Fatal("disabled VM executed")
	}
	vm.Enabled = true
	if err := vm.Execute(context.Background(), plan); err != nil || !called {
		t.Fatalf("enabled err=%v called=%v", err, called)
	}
}
