package gateway

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestSignalCLIDownloadURLNonEmpty(t *testing.T) {
	url := SignalCLIDownloadURL()
	if url == "" || !strings.Contains(url, "signal-cli") {
		t.Fatalf("url=%q", url)
	}
}

func TestLookupManagedSignalCLIEmpty(t *testing.T) {
	home := t.TempDir()
	if got := LookupManagedSignalCLI(home); got != "" {
		t.Fatalf("expected empty, got %q", got)
	}
}

func TestLookupManagedSignalCLIFindsBin(t *testing.T) {
	home := t.TempDir()
	dir := ManagedSignalDir(home)
	binDir := filepath.Join(dir, "bin")
	if err := os.MkdirAll(binDir, 0o700); err != nil {
		t.Fatal(err)
	}
	name := "signal-cli"
	if runtime.GOOS == "windows" {
		name = "signal-cli.exe"
	}
	path := filepath.Join(binDir, name)
	if err := os.WriteFile(path, []byte(""), 0o755); err != nil {
		t.Fatal(err)
	}
	got := LookupManagedSignalCLI(home)
	if got == "" {
		t.Fatal("expected managed binary")
	}
}

func TestEnsureManagedSignalCLISkipDownload(t *testing.T) {
	t.Setenv("REMEDY_SKIP_MANAGED_SIGNAL_DOWNLOAD", "1")
	home := t.TempDir()
	if _, err := EnsureManagedSignalCLI(home); err == nil {
		t.Fatal("expected error when missing and download skipped")
	}
}

func TestSignalCLINeedsJavaMatchesPin(t *testing.T) {
	pin, ok := signalCLIPinForGOOS()
	if !ok {
		t.Skip("no pin")
	}
	if SignalCLINeedsJava() != pin.jvm {
		t.Fatalf("NeedsJava=%v pin.jvm=%v", SignalCLINeedsJava(), pin.jvm)
	}
}

func TestJavaOnPATHFakePATH(t *testing.T) {
	dir := t.TempDir()
	name := "java"
	if runtime.GOOS == "windows" {
		name = "java.exe"
		t.Setenv("PATHEXT", ".EXE;.BAT;.CMD")
	}
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir)
	if !JavaOnPATH() {
		t.Fatal("expected java on fake PATH")
	}
	if !SignalCLIJavaOK() && SignalCLINeedsJava() {
		t.Fatal("SignalCLIJavaOK should be true when java is on PATH")
	}

	empty := filepath.Join(dir, "empty")
	if err := os.MkdirAll(empty, 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", empty)
	if JavaOnPATH() {
		t.Fatal("expected no java on empty PATH")
	}
	if SignalCLINeedsJava() && SignalCLIJavaOK() {
		t.Fatal("SignalCLIJavaOK should be false when JVM needed and java missing")
	}
	if !SignalCLINeedsJava() && !SignalCLIJavaOK() {
		t.Fatal("native pin should report java OK without a JRE")
	}
}

func TestEnsureManagedSignalCLIJVMViaHTTPtest(t *testing.T) {
	if runtime.GOOS != "windows" && runtime.GOOS != "linux" && runtime.GOOS != "darwin" {
		t.Skip("no JVM pin for this GOOS")
	}
	// Force JVM pin path even on linux/amd64 so extract layout is covered.
	prevNative := signalCLINativePins
	prevBase := signalCLIDownloadBase
	prevDL := managedDownloadFile
	prevJVM := signalCLIJVMPin
	t.Cleanup(func() {
		signalCLINativePins = prevNative
		signalCLIDownloadBase = prevBase
		managedDownloadFile = prevDL
		signalCLIJVMPin = prevJVM
	})
	signalCLINativePins = map[string]signalCLIPin{} // force JVM fallback

	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	scriptName := "signal-cli-" + signalCLIVersion + "/bin/signal-cli"
	if runtime.GOOS == "windows" {
		scriptName = "signal-cli-" + signalCLIVersion + "/bin/signal-cli.bat"
	}
	body := []byte("@echo off\r\necho fixture\r\n")
	if err := tw.WriteHeader(&tar.Header{Name: scriptName, Mode: 0o755, Size: int64(len(body))}); err != nil {
		t.Fatal(err)
	}
	if _, err := tw.Write(body); err != nil {
		t.Fatal(err)
	}
	libName := "signal-cli-" + signalCLIVersion + "/lib/signal-cli.jar"
	jar := []byte("jar-fixture")
	if err := tw.WriteHeader(&tar.Header{Name: libName, Mode: 0o644, Size: int64(len(jar))}); err != nil {
		t.Fatal(err)
	}
	if _, err := tw.Write(jar); err != nil {
		t.Fatal(err)
	}
	if err := tw.Close(); err != nil {
		t.Fatal(err)
	}
	if err := gz.Close(); err != nil {
		t.Fatal(err)
	}
	archive := buf.Bytes()
	sum := sha256.Sum256(archive)
	wantHex := hex.EncodeToString(sum[:])
	signalCLIJVMPin = signalCLIPin{
		filename: "signal-cli-" + signalCLIVersion + ".tar.gz",
		sha256:   wantHex,
		jvm:      true,
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, "/"+signalCLIJVMPin.filename) {
			http.NotFound(w, r)
			return
		}
		_, _ = w.Write(archive)
	}))
	t.Cleanup(srv.Close)
	signalCLIDownloadBase = srv.URL + "/"
	managedDownloadFile = downloadFileSHA256

	home := t.TempDir()
	got, err := EnsureManagedSignalCLI(home)
	if err != nil {
		t.Fatalf("ensure: %v", err)
	}
	if got == "" {
		t.Fatal("empty path")
	}
	if _, err := os.Stat(got); err != nil {
		t.Fatalf("missing binary %s: %v", got, err)
	}
}
