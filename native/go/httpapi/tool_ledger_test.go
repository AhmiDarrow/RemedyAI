package httpapi

import (
	"strings"
	"testing"
)

func TestToolLedgerLineNamesToolsTargetsAndFailures(t *testing.T) {
	calls := []any{
		map[string]any{"id": "1", "name": "read", "args": map[string]any{"path": "src/a.go"}},
		map[string]any{"id": "2", "name": "bash", "args": map[string]any{"command": "go test\n  ./..."}},
		map[string]any{"id": "3", "name": "todo", "args": map[string]any{"items": []any{}}},
	}
	results := []any{
		map[string]any{"id": "1", "name": "read", "error": nil},
		map[string]any{"id": "2", "name": "bash", "error": "exit 1"},
	}
	got := toolLedgerLine(calls, results)
	want := "[Tools used in this message: read src/a.go · bash go test ./... (failed) · todo]"
	if got != want {
		t.Fatalf("got  %q\nwant %q", got, want)
	}
	if toolLedgerLine(nil, nil) != "" || toolLedgerLine([]any{}, nil) != "" {
		t.Fatal("a message with no tools has no ledger line")
	}
	many := make([]any, 0, 60)
	for i := 0; i < 60; i++ {
		many = append(many, map[string]any{"name": "read", "args": map[string]any{"path": strings.Repeat("d/", 60) + "f.go"}})
	}
	long := toolLedgerLine(many, nil)
	if len(long) > toolLedgerChars+8 || !strings.HasSuffix(long, "…]") {
		t.Fatalf("a long ledger is capped: %d bytes, ends %q", len(long), long[len(long)-12:])
	}
}