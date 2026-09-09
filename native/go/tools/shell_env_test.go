package tools

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

// A module-search-path variable makes an interpreter import attacker code
// before the command the classifier approved ever runs, so a tool call may not
// set one. The runtime's own worker spawn does not go through this path.
func TestShellExecRefusesInterpreterPathEnv(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}

	for _, key := range []string{"PYTHONPATH", "pythonpath", "NODE_PATH", "CLASSPATH", "PSModulePath"} {
		input, err := json.Marshal(map[string]any{
			"argv": []string{"hostname"},
			"env":  map[string]string{key: "C:\attacker"},
		})
		if err != nil {
			t.Fatal(err)
		}
		_, execErr := registry.Execute(context.Background(), Request{
			ToolID:          "shell.exec",
			Version:         1,
			Input:           input,
			CapabilityToken: []byte("test-token"),
		})
		if !errors.Is(execErr, ErrInvalidInput) {
			t.Fatalf("%s must be refused, got %v", key, execErr)
		}
		if !strings.Contains(strings.ToUpper(execErr.Error()), strings.ToUpper(strings.TrimSpace(key))) {
			t.Fatalf("%s: error should name the variable: %v", key, execErr)
		}
	}
}

func TestShellExecAllowsOrdinaryEnv(t *testing.T) {
	if got := modelDeniedEnvKey(map[string]string{"CI": "1", "LANG": "C"}); got != "" {
		t.Fatalf("ordinary variables must pass, refused %q", got)
	}
	if got := modelDeniedEnvKey(nil); got != "" {
		t.Fatalf("nil env must pass, refused %q", got)
	}
}
