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

	for _, key := range []string{"PYTHONPATH", "pythonpath", "NODE_PATH", "CLASSPATH", "PSModulePath", "NODE_OPTIONS", "BASH_ENV", "LD_PRELOAD"} {
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
	if got := modelSpawnEnvKey(map[string]string{"CI": "1", "LANG": "C", "GIT_TERMINAL_PROMPT": "0", "NO_COLOR": "1"}); got != "" {
		t.Fatalf("ordinary variables must pass, refused %q", got)
	}
}

// Variables that pick the program, run one from git config, run code in the
// shell itself or route traffic elsewhere: neither bash nor shell.exec may
// have them from a tool call, because the guards read the command only.
func TestShellToolsRefuseSteeringEnv(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	if err := RegisterShellTools(registry); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{
		"PATH", "Path", "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0", "GIT_CONFIG_PARAMETERS",
		"GIT_PAGER", "GIT_ASKPASS", "EDITOR", "LESSOPEN", "PS4", "SHELLOPTS", "HTTPS_PROXY", "https_proxy", "ALL_PROXY",
	} {
		for tool, body := range map[string]map[string]any{
			"shell.exec": {"argv": []string{"hostname"}},
			"bash":       {"command": "git log -1"},
		} {
			body["env"] = map[string]string{key: "x"}
			input, err := json.Marshal(body)
			if err != nil {
				t.Fatal(err)
			}
			_, execErr := registry.Execute(context.Background(), Request{
				ToolID: tool, Version: 1, Input: input, CapabilityToken: []byte("test-token"),
			})
			if !errors.Is(execErr, ErrInvalidInput) {
				t.Fatalf("%s: %s must be refused, got %v", tool, key, execErr)
			}
			if !strings.Contains(strings.ToUpper(execErr.Error()), strings.ToUpper(key)) {
				t.Fatalf("%s: %s: error should name the variable: %v", tool, key, execErr)
			}
		}
	}
}
