package tools

import (
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestLookPathFindsPATHProgram(t *testing.T) {
	name := "hostname"
	if runtime.GOOS == "windows" {
		name = "cmd"
	}
	want, err := exec.LookPath(name)
	if err != nil {
		t.Skip(name + " not on PATH")
	}
	got, err := lookPath(name)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.EqualFold(filepath.Clean(got), filepath.Clean(want)) {
		t.Fatalf("lookPath(%q)=%q want %q", name, got, want)
	}
}

func TestLookPathMissing(t *testing.T) {
	if _, err := lookPath("remedy-no-such-binary-9f3c"); err == nil {
		t.Fatal("expected missing")
	}
}
