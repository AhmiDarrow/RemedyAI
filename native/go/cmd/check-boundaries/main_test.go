package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestBoundaryCheckerFindsForbiddenAndMisownedImports(t *testing.T) {
	root := t.TempDir()
	write := func(path, body string) {
		path = filepath.Join(root, path)
		_ = os.MkdirAll(filepath.Dir(path), 0o700)
		if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	write("runtime/good.go", "package runtime\nimport \"context\"\n")
	write("runtime/bad.go", "package runtime\nimport (\"os/exec\"; _ \"github.com/Microsoft/go-winio\")\n")
	violations, err := check(root)
	if err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(violations, "\n")
	if !strings.Contains(joined, "forbidden os/exec") || !strings.Contains(joined, "owned by ipc") {
		t.Fatalf("violations=%v", violations)
	}
}
