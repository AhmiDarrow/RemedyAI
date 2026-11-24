package httpapi

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

func TestFormatToolResultTokenSurfacesExitAndOutput(t *testing.T) {
	raw, err := json.Marshal(map[string]any{
		"exit_code": 3,
		"timed_out": false,
		"stdout":    "build ok until here",
		"stderr":    "the signal was disposed",
	})
	if err != nil {
		t.Fatal(err)
	}
	token := formatToolResultToken(cognition.ToolResult{
		ID:      "c1",
		Name:    "bash",
		Output:  raw,
		IsError: true,
	})
	res := parseToolResultToken(token)
	if res.OK {
		t.Fatal("a non-zero exit must be marked failed")
	}
	if res.Error == "" || res.Error == "tool failed" {
		t.Fatalf("error must carry the real failure, not a bare tool failed: %#v", res)
	}
	if !strings.Contains(res.Error, "exit_code 3") {
		t.Fatalf("missing exit code: %q", res.Error)
	}
	if !strings.Contains(res.Error, "the signal was disposed") {
		t.Fatalf("missing stderr: %q", res.Error)
	}
	if !strings.Contains(res.Preview, "exit_code 3") {
		t.Fatalf("preview must also show the failure: %q", res.Preview)
	}
	rec := res.record()
	errMsg, _ := rec["error"].(string)
	if errMsg == "" || errMsg == "tool failed" || !strings.Contains(errMsg, "exit_code 3") {
		t.Fatalf("record error=%q", errMsg)
	}
}

func TestFormatToolResultTokenKeepsExplicitErr(t *testing.T) {
	res := parseToolResultToken(formatToolResultToken(cognition.ToolResult{
		ID: "c7", Name: "shell.exec", Err: "boom",
	}))
	if res.ID != "c7" || res.OK || res.Preview != "boom" || res.Error != "boom" {
		t.Fatalf("res=%#v", res)
	}
}

func TestToolResultRecordNeverSaysBareToolFailed(t *testing.T) {
	cases := []toolResultToken{
		{Name: "bash", OK: false},
		{Name: "bash", OK: false, Preview: ""},
		{Name: "grep", OK: false, Error: `C:\Users\Administrator\Projects\app is outside the workspace root`},
		{Name: "bash", OK: false, Output: "exit_code 1\nstderr:\nboom"},
	}
	for _, tok := range cases {
		rec := tok.record()
		errMsg, _ := rec["error"].(string)
		if errMsg == "tool failed" {
			t.Fatalf("bare tool failed for %#v", tok)
		}
		if strings.TrimSpace(errMsg) == "" {
			t.Fatalf("empty error for %#v", tok)
		}
	}
}

func TestToolResultRecordKeepsFullOutput(t *testing.T) {
	full := strings.Repeat("line of command output\n", 40)
	res := toolResultToken{
		Name:    "bash",
		OK:      true,
		Preview: previewOf(full),
		Output:  full,
	}
	rec := res.record()
	out, _ := rec["output"].(string)
	if out != full {
		t.Fatalf("persisted output was truncated: len=%d want=%d preview=%q", len(out), len(full), rec["preview"])
	}
	if rec["preview"] != previewOf(full) {
		t.Fatalf("preview=%v", rec["preview"])
	}
}
