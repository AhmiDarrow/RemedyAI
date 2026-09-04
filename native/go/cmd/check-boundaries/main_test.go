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
	write("cmd/bad/main.go", "package main\nimport \"unsafe\"\n")
	write("secret/ok_unsafe.go", "package secret\nimport \"unsafe\"\nvar _ = unsafe.Sizeof(0)\n")
	write("core/ok_unsafe.go", "package core\nimport \"unsafe\"\nvar _ = unsafe.Sizeof(0)\n")
	write("core/ok_sys.go", "package core\nimport _ \"golang.org/x/sys/windows\"\n")
	write("memory/bad_sqlite.go", "package memory\nimport _ \"modernc.org/sqlite\"\n")
	write("httpapi/ok_sqlite.go", "package httpapi\nimport _ \"modernc.org/sqlite\"\n")
	write("connect/ok_crypto.go", "package connect\nimport _ \"golang.org/x/crypto/blake2s\"\n")
	write("runtime/bad_crypto.go", "package runtime\nimport _ \"golang.org/x/crypto/blake2s\"\n")
	violations, err := check(root)
	if err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(violations, "\n")
	if !strings.Contains(joined, "forbidden os/exec") || !strings.Contains(joined, "owned by ipc") || !strings.Contains(joined, "cmd/bad/main.go imports forbidden unsafe") {
		t.Fatalf("violations=%v", violations)
	}
	if !strings.Contains(joined, "memory/bad_sqlite.go imports modernc.org/sqlite owned by httpapi") {
		t.Fatalf("expected modernc ownership violation, got %v", violations)
	}
	if strings.Contains(joined, "httpapi/ok_sqlite.go") {
		t.Fatalf("httpapi must be allowed to import modernc.org/sqlite: %v", violations)
	}
	if strings.Contains(joined, "connect/ok_crypto.go") {
		t.Fatalf("connect must be allowed to import golang.org/x/crypto: %v", violations)
	}
	if !strings.Contains(joined, "runtime/bad_crypto.go imports golang.org/x/crypto/blake2s owned by connect") {
		t.Fatalf("expected x/crypto ownership violation, got %v", violations)
	}
	if strings.Contains(joined, "secret/ok_unsafe.go") {
		t.Fatalf("secret must be allowed to import unsafe: %v", violations)
	}
	if strings.Contains(joined, "core/ok_unsafe.go") || strings.Contains(joined, "core/ok_sys.go") {
		t.Fatalf("core must be allowed unsafe and golang.org/x/sys: %v", violations)
	}
}
