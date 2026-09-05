package workers

import (
	"archive/tar"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// Pinned python-build-standalone (same release as remedy.voice.runtime).
const (
	pbsTag = "20260814"
	pbsPy  = "3.12.14"
	pbsBase = "https://github.com/astral-sh/python-build-standalone/releases/download/"
)

type pbsPin struct {
	triple string
	sha256 string
}

var pbsPins = map[string]pbsPin{
	"windows/amd64": {
		triple: "x86_64-pc-windows-msvc",
		sha256: "89f18f6932917163b74339ebcec2645c8e47ae7f1c5f2ac37f2b4f4cf3beb647",
	},
	"linux/amd64": {
		triple: "x86_64-unknown-linux-gnu",
		sha256: "5acfa3e9ba26b51ae161c83aff278da915b590d22373a424b2ba55b8afe91fcc",
	},
	"linux/arm64": {
		triple: "aarch64-unknown-linux-gnu",
		sha256: "2d8e17dfd732102cfeb18e0e1fa6769b24caa034e159981129590fe409c7157a",
	},
}

// ensureManagedPython returns an absolute interpreter path, downloading the
// pinned CPython into ~/.remedy/voice/runtime when nothing else is available.
// Shares the voice managed runtime tree so voice packs and RMDY use one Python.
func ensureManagedPython() (string, error) {
	if abs := managedVoicePython(); abs != "" {
		return abs, nil
	}
	if skipManagedPythonDownload() {
		return "", fmt.Errorf("no managed python and download disabled (REMEDY_SKIP_MANAGED_PYTHON_DOWNLOAD)")
	}
	pin, ok := pbsPinForGOOS()
	if !ok {
		return "", fmt.Errorf("no managed python pin for %s/%s", runtime.GOOS, runtime.GOARCH)
	}
	home := remedyHomeForManagedPython()
	if home == "" {
		return "", fmt.Errorf("cannot resolve REMEDY_HOME for managed python")
	}
	rdir := filepath.Join(home, "voice", "runtime")
	if err := os.MkdirAll(rdir, 0o700); err != nil {
		return "", err
	}
	py := managedPythonBinary(rdir)
	if st, err := os.Stat(py); err == nil && !st.IsDir() {
		return absPath(py), nil
	}

	filename := fmt.Sprintf("cpython-%s+%s-%s-install_only_stripped.tar.gz", pbsPy, pbsTag, pin.triple)
	url := pbsBase + pbsTag + "/" + strings.ReplaceAll(filename, "+", "%2B")
	archive := filepath.Join(rdir, filename)

	log.Printf("remedy-runtime: downloading managed CPython for tools (%s)", pin.triple)
	if err := downloadFileSHA256(url, archive, pin.sha256); err != nil {
		return "", fmt.Errorf("download managed python: %w", err)
	}
	target := filepath.Join(rdir, "python")
	_ = os.RemoveAll(target)
	if err := extractTarGz(archive, rdir); err != nil {
		return "", fmt.Errorf("extract managed python: %w", err)
	}
	_ = os.Remove(archive)
	if st, err := os.Stat(py); err != nil || st.IsDir() {
		return "", fmt.Errorf("managed python missing after extract: %s", py)
	}
	_ = writeManagedPythonMarker(rdir, pin.triple)
	log.Printf("remedy-runtime: managed CPython ready at %s", py)
	return absPath(py), nil
}

func skipManagedPythonDownload() bool {
	v := strings.TrimSpace(strings.ToLower(os.Getenv("REMEDY_SKIP_MANAGED_PYTHON_DOWNLOAD")))
	return v == "1" || v == "true" || v == "yes"
}

func pbsPinForGOOS() (pbsPin, bool) {
	key := runtime.GOOS + "/" + runtime.GOARCH
	pin, ok := pbsPins[key]
	return pin, ok
}

func remedyHomeForManagedPython() string {
	if h := strings.TrimSpace(os.Getenv("REMEDY_HOME")); h != "" {
		return h
	}
	uh, err := os.UserHomeDir()
	if err != nil || strings.TrimSpace(uh) == "" {
		return ""
	}
	return filepath.Join(uh, ".remedy")
}

func managedPythonBinary(runtimeDir string) string {
	if runtime.GOOS == "windows" {
		return filepath.Join(runtimeDir, "python", "python.exe")
	}
	return filepath.Join(runtimeDir, "python", "bin", "python3")
}

func absPath(p string) string {
	abs, err := filepath.Abs(p)
	if err != nil {
		return p
	}
	return abs
}

func downloadFileSHA256(url, dest, wantHex string) error {
	_ = os.Remove(dest)
	tmp := dest + ".partial"
	_ = os.Remove(tmp)

	client := &http.Client{Timeout: 10 * time.Minute}
	resp, err := client.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %s for %s", resp.Status, url)
	}
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	h := sha256.New()
	if _, err := io.Copy(io.MultiWriter(f, h), resp.Body); err != nil {
		_ = f.Close()
		_ = os.Remove(tmp)
		return err
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	got := hex.EncodeToString(h.Sum(nil))
	if !strings.EqualFold(got, wantHex) {
		_ = os.Remove(tmp)
		return fmt.Errorf("sha256 mismatch: got %s want %s", got, wantHex)
	}
	return os.Rename(tmp, dest)
}

func extractTarGz(archive, destDir string) error {
	f, err := os.Open(archive)
	if err != nil {
		return err
	}
	defer f.Close()
	gz, err := gzip.NewReader(f)
	if err != nil {
		return err
	}
	defer gz.Close()
	tr := tar.NewReader(gz)
	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return err
		}
		name := filepath.Clean(hdr.Name)
		if name == "." || name == ".." || strings.HasPrefix(name, ".."+string(os.PathSeparator)) {
			continue
		}
		// Refuse absolute / drive paths and escapes outside destDir.
		if filepath.IsAbs(name) || (runtime.GOOS == "windows" && len(name) >= 2 && name[1] == ':') {
			return fmt.Errorf("refusing absolute archive path: %s", hdr.Name)
		}
		target := filepath.Join(destDir, name)
		if !pathUnderManaged(target, destDir) {
			return fmt.Errorf("archive path escapes runtime dir: %s", hdr.Name)
		}
		switch hdr.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
		case tar.TypeReg, tar.TypeRegA:
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			mode := os.FileMode(hdr.Mode)
			if mode == 0 {
				mode = 0o644
			}
			out, err := os.OpenFile(target, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, mode)
			if err != nil {
				return err
			}
			if _, err := io.Copy(out, tr); err != nil {
				_ = out.Close()
				return err
			}
			if err := out.Close(); err != nil {
				return err
			}
		case tar.TypeSymlink:
			// Skip symlinks in stripped install_only archives on Windows;
			// on Unix recreate when the link stays under destDir.
			if runtime.GOOS == "windows" {
				continue
			}
			link := hdr.Linkname
			if filepath.IsAbs(link) {
				continue
			}
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			_ = os.Remove(target)
			if err := os.Symlink(link, target); err != nil {
				return err
			}
		}
	}
	return nil
}

func pathUnderManaged(path, root string) bool {
	absPath, err := filepath.Abs(path)
	if err != nil {
		return false
	}
	absRoot, err := filepath.Abs(root)
	if err != nil {
		return false
	}
	absPath = filepath.Clean(absPath)
	absRoot = filepath.Clean(absRoot)
	if absPath == absRoot {
		return true
	}
	prefix := absRoot + string(os.PathSeparator)
	return strings.HasPrefix(absPath, prefix)
}

func writeManagedPythonMarker(runtimeDir, triple string) error {
	marker := map[string]any{
		"ok":      true,
		"python":  pbsPy,
		"tag":     pbsTag,
		"triple":  triple,
		"source":  "rmdy-ensure",
		"at":      time.Now().UTC().Format(time.RFC3339),
		"packs":   map[string]any{},
	}
	raw, err := json.MarshalIndent(marker, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(runtimeDir, "runtime.json"), append(raw, '\n'), 0o600)
}
