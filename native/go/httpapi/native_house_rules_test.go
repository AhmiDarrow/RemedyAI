package httpapi

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

func TestHouseRulesRunInGoAndWriteSettings(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	s := &Server{homeDir: home}

	worker := 0
	reg := tools.NewRegistry()
	for _, id := range []string{"house_rules", "setup.session"} {
		err := reg.Register(tools.Descriptor{
			ID: id, Version: 1, Description: id, Runtime: tools.RuntimePython, Risk: tools.RiskReadOnly, // authorization is tested with the registry
			InputSchema:  json.RawMessage(`{"type":"object"}`),
			OutputSchema: json.RawMessage(`{"type":"object"}`),
		}, tools.ExecutorFunc(func(context.Context, tools.Request) (tools.Result, error) {
			worker++
			return tools.Result{Output: json.RawMessage(`{}`)}, nil
		}))
		if err != nil {
			t.Fatal(err)
		}
	}
	if err := attachNativeHouseRules(reg, s); err != nil {
		t.Fatal(err)
	}
	call := func(id string, input map[string]any) map[string]any {
		t.Helper()
		raw, _ := json.Marshal(input)
		res, err := reg.Execute(context.Background(), tools.Request{ToolID: id, Version: 1, Input: raw})
		if err != nil {
			t.Fatalf("%s %v: %v", id, input, err)
		}
		var out map[string]any
		if err := json.Unmarshal(res.Output, &out); err != nil {
			t.Fatalf("%s result is not an object: %s", id, res.Output)
		}
		return out
	}

	if out := call("setup.session", map[string]any{"action": "get"}); out["ok"] != true {
		t.Fatalf("setup.session get is a status call: %v", out)
	}
	if out := call("house_rules", map[string]any{"action": "record", "answers": map[string]any{
		"user_name": "Alex Example", "name": "Remy", "agent_gender": "neutral",
	}}); out["ok"] != true {
		t.Fatalf("record: %v", out)
	}
	call("house_rules", map[string]any{"action": "skip_all"})
	call("house_rules", map[string]any{"action": "summarize"})
	if out := call("house_rules", map[string]any{"action": "confirm"}); out["ok"] != true {
		t.Fatalf("confirm: %v", out)
	}
	if worker != 0 {
		t.Fatalf("the Python worker was called %d time(s); house rules are native", worker)
	}
	InvalidateConfigCache()
	cfg, err := os.ReadFile(filepath.Join(home, "config.toml"))
	if err != nil {
		t.Fatalf("confirmed answers must be written to config.toml: %v", err)
	}
	for _, want := range []string{"Alex Example", "Remy", "neutral"} {
		if !strings.Contains(string(cfg), want) {
			t.Errorf("config.toml lacks %q:\n%s", want, cfg)
		}
	}
}
