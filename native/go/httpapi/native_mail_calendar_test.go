package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

func TestMailGoesNativeOnlyForAConnectedGoogleAccount(t *testing.T) {
	register := func(t *testing.T, home string, worker *int) *tools.Registry {
		t.Helper()
		reg := tools.NewRegistry()
		err := reg.Register(tools.Descriptor{
			ID: "mail.list", Version: 1, Description: "list mail", Runtime: tools.RuntimePython, Risk: tools.RiskReadOnly,
			InputSchema:  json.RawMessage(`{"type":"object"}`),
			OutputSchema: json.RawMessage(`{"type":"object"}`),
		}, tools.ExecutorFunc(func(context.Context, tools.Request) (tools.Result, error) {
			*worker++
			return tools.Result{Output: json.RawMessage(`{"from":"worker"}`)}, nil
		}))
		if err != nil {
			t.Fatal(err)
		}
		if err := attachNativeMailCalendar(reg, home); err != nil {
			t.Fatal(err)
		}
		return reg
	}
	run := func(reg *tools.Registry) string {
		d, err := reg.Resolve("mail.list", 1)
		if err != nil {
			t.Fatal(err)
		}
		_ = d
		res, err := reg.Execute(context.Background(), tools.Request{ToolID: "mail.list", Version: 1, Input: json.RawMessage(`{}`)})
		if err != nil {
			t.Fatal(err)
		}
		return string(res.Output)
	}

	// Nothing connected: answered here ("not connected"), never by the worker.
	worker := 0
	empty := t.TempDir()
	t.Setenv("REMEDY_HOME", empty)
	reg := register(t, empty, &worker)
	_, err := reg.Execute(context.Background(), tools.Request{ToolID: "mail.list", Version: 1, Input: json.RawMessage(`{}`)})
	if worker != 0 {
		t.Fatalf("with nothing connected the worker must still not be called (calls=%d, err=%v)", worker, err)
	}

	// A connected Google account: answered here, and the worker is never called.
	_, home := newGMCFake(t, "fake-access", gmcFarFuture(), "fake-refresh", func(w http.ResponseWriter, r *http.Request) {
		gmcJSON(w, map[string]any{"messages": []any{}})
	})
	worker = 0
	out := run(register(t, home, &worker))
	if worker != 0 {
		t.Fatalf("with Google connected the worker must not be called (calls=%d)", worker)
	}
	var body map[string]any
	if err := json.Unmarshal([]byte(out), &body); err != nil || body["from"] == "worker" {
		t.Fatalf("native result expected, got %s (%v)", out, err)
	}
}
