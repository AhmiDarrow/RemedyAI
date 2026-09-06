package gateway

import (
	"archive/tar"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
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

// Pinned signal-cli release (AsamK/signal-cli). Linux amd64 uses the GraalVM
// native build. Windows (and other arches) use the JVM fat tarball — needs a
// JRE (Java 21+, ideally 25+) on PATH after EnsureManagedSignalCLI.
const (
	signalCLIVersion = "0.14.7"
	signalCLIBaseURL = "https://github.com/AsamK/signal-cli/releases/download/v" + signalCLIVersion + "/"
)

type signalCLIPin struct {
	filename string
	sha256   string
	// binaryRel is path under extract root to the runnable binary (native only).
	binaryRel string
	// jvm is true for the fat tarball (keeps signal-cli-<ver>/ tree + .bat).
	jvm bool
}

var signalCLINativePins = map[string]signalCLIPin{
	"linux/amd64": {
		filename:  "signal-cli-" + signalCLIVersion + "-Linux-native.tar.gz",
		sha256:    "0fe065294adcf35df4c249b635d0ce57de7765d4fec660bffaa2e7f0549d4e5f",
		binaryRel: "signal-cli",
	},
}

// JVM fat tarball — Windows and any arch without a native pin.
var signalCLIJVMPin = signalCLIPin{
	filename: "signal-cli-" + signalCLIVersion + ".tar.gz",
	sha256:   "0e1eefdf4a2109edf7c899c9d1667167c54ac12c3ec824f27db7c1dac4fa7506",
	jvm:      true,
}

// signalCLIDownloadBase overrides the GitHub release base in tests.
var signalCLIDownloadBase = signalCLIBaseURL

func signalCLIPinForGOOS() (signalCLIPin, bool) {
	if pin, ok := signalCLINativePins[runtime.GOOS+"/"+runtime.GOARCH]; ok {
		return pin, true
	}
	switch runtime.GOOS {
	case "windows", "linux", "darwin":
		return signalCLIJVMPin, true
	default:
		return signalCLIPin{}, false
	}
}

// SignalCLIDownloadURL is the owner-facing install URL for this OS.
func SignalCLIDownloadURL() string {
	if pin, ok := signalCLIPinForGOOS(); ok {
		return signalCLIDownloadBase + pin.filename
	}
	return signalCLIDownloadBase + signalCLIJVMPin.filename
}

// SignalCLINeedsJava reports whether the managed artifact for this OS is the JVM build.
func SignalCLINeedsJava() bool {
	pin, ok := signalCLIPinForGOOS()
	return ok && pin.jvm
}

// JavaOnPATH reports whether a java / java.exe binary is findable on PATH.
// Presence is the health gate for JVM signal-cli; we do not spawn java -version
// on every poll (no os/exec; Zig capture would be heavier than this check).
func JavaOnPATH() bool {
	return lookupExecutableOnPATH("java") != ""
}

// SignalCLIJavaOK is true when this OS does not need a JRE, or java is on PATH.
func SignalCLIJavaOK() bool {
	return !SignalCLINeedsJava() || JavaOnPATH()
}

// lookupExecutableOnPATH finds name (plus PATHEXT on Windows) on PATH.
func lookupExecutableOnPATH(name string) string {
	name = strings.TrimSpace(name)
	if name == "" {
		return ""
	}
	pathEnv := os.Getenv("PATH")
	sep := string(os.PathListSeparator)
	exts := []string{""}
	if runtime.GOOS == "windows" {
		pathext := os.Getenv("PATHEXT")
		if pathext == "" {
			pathext = ".COM;.EXE;.BAT;.CMD"
		}
		for _, e := range strings.Split(pathext, ";") {
			e = strings.TrimSpace(e)
			if e != "" {
				exts = append(exts, e)
			}
		}
	}
	for _, dir := range strings.Split(pathEnv, sep) {
		dir = strings.TrimSpace(dir)
		if dir == "" {
			continue
		}
		for _, ext := range exts {
			candidate := filepath.Join(dir, name+ext)
			if st, err := os.Stat(candidate); err == nil && !st.IsDir() {
				if abs, err := filepath.Abs(candidate); err == nil {
					return abs
				}
				return candidate
			}
		}
	}
	return ""
}

func signalCLIInstallHint() string {
	if SignalCLINeedsJava() {
		return "Install signal-cli (JVM build needs Java 21+) from " + SignalCLIDownloadURL() + " or use EnsureManagedSignalCLI, then set cli_path if needed."
	}
	if _, ok := signalCLINativePins[runtime.GOOS+"/"+runtime.GOARCH]; ok {
		return "Install signal-cli from " + SignalCLIDownloadURL() + ", place it on PATH, or call EnsureManagedSignalCLI."
	}
	return "Install signal-cli from https://github.com/AsamK/signal-cli/releases and set cli_path."
}

// ManagedSignalDir is ~/.remedy/signal (or REMEDY_HOME/signal).
func ManagedSignalDir(home string) string {
	home = strings.TrimSpace(home)
	if home == "" {
		if h := strings.TrimSpace(os.Getenv("REMEDY_HOME")); h != "" {
			home = h
		} else if uh, err := os.UserHomeDir(); err == nil {
			home = filepath.Join(uh, ".remedy")
		}
	}
	if home == "" {
		return ""
	}
	return filepath.Join(home, "signal")
}

// LookupManagedSignalCLI returns an existing managed binary path, or "".
func LookupManagedSignalCLI(home string) string {
	dir := ManagedSignalDir(home)
	if dir == "" {
		return ""
	}
	candidates := []string{
		filepath.Join(dir, "bin", "signal-cli"),
		filepath.Join(dir, "signal-cli"),
	}
	if runtime.GOOS == "windows" {
		candidates = append([]string{
			filepath.Join(dir, "bin", "signal-cli.exe"),
			filepath.Join(dir, "signal-cli.exe"),
			filepath.Join(dir, "bin", "signal-cli.bat"),
		}, candidates...)
	}
	for _, p := range candidates {
		if st, err := os.Stat(p); err == nil && !st.IsDir() {
			if abs, err := filepath.Abs(p); err == nil {
				return abs
			}
			return p
		}
	}
	// JVM extract layout: signal-cli-<ver>/bin/signal-cli
	matches, _ := filepath.Glob(filepath.Join(dir, "signal-cli-*", "bin", "signal-cli*"))
	for _, p := range matches {
		base := strings.ToLower(filepath.Base(p))
		if strings.HasSuffix(base, ".bat") || base == "signal-cli" || base == "signal-cli.exe" {
			if st, err := os.Stat(p); err == nil && !st.IsDir() {
				if abs, err := filepath.Abs(p); err == nil {
					return abs
				}
				return p
			}
		}
	}
	return ""
}

// EnsureManagedSignalCLI downloads the pinned signal-cli build into
// ~/.remedy/signal when missing. Linux amd64 uses the native binary; Windows
// and other arches get the JVM tarball (requires Java 21+ on PATH).
func EnsureManagedSignalCLI(home string) (string, error) {
	if existing := LookupManagedSignalCLI(home); existing != "" {
		return existing, nil
	}
	if v := strings.TrimSpace(strings.ToLower(os.Getenv("REMEDY_SKIP_MANAGED_SIGNAL_DOWNLOAD"))); v == "1" || v == "true" || v == "yes" {
		return "", fmt.Errorf("signal-cli missing and download disabled (REMEDY_SKIP_MANAGED_SIGNAL_DOWNLOAD); see %s", SignalCLIDownloadURL())
	}
	pin, ok := signalCLIPinForGOOS()
	if !ok {
		return "", fmt.Errorf("no managed signal-cli pin for %s/%s — install from %s", runtime.GOOS, runtime.GOARCH, SignalCLIDownloadURL())
	}
	dir := ManagedSignalDir(home)
	if dir == "" {
		return "", fmt.Errorf("cannot resolve REMEDY_HOME for managed signal-cli")
	}
	if err := os.MkdirAll(filepath.Join(dir, "bin"), 0o700); err != nil {
		return "", err
	}
	url := signalCLIDownloadBase + pin.filename
	archive := filepath.Join(dir, pin.filename)
	log.Printf("signal: downloading managed signal-cli %s (%s)", signalCLIVersion, pin.filename)
	if err := managedDownloadFile(url, archive, pin.sha256); err != nil {
		return "", fmt.Errorf("download signal-cli: %w", err)
	}
	if pin.jvm {
		if err := extractTarGzFlat(archive, dir); err != nil {
			return "", fmt.Errorf("extract signal-cli: %w", err)
		}
		_ = os.Remove(archive)
		_ = os.WriteFile(filepath.Join(dir, "VERSION"), []byte(signalCLIVersion+"\n"), 0o600)
		_ = os.WriteFile(filepath.Join(dir, "JAVA_REQUIRED"), []byte("Java 21+ (signal-cli JVM build)\n"), 0o600)
		got := LookupManagedSignalCLI(home)
		if got == "" {
			return "", fmt.Errorf("signal-cli missing after JVM extract — install Java 21+ and re-check %s", dir)
		}
		log.Printf("signal: managed signal-cli (JVM) ready at %s — requires Java 21+ on PATH", got)
		return got, nil
	}

	extractRoot := filepath.Join(dir, "extract")
	_ = os.RemoveAll(extractRoot)
	if err := extractTarGzFlat(archive, extractRoot); err != nil {
		return "", fmt.Errorf("extract signal-cli: %w", err)
	}
	_ = os.Remove(archive)

	src := filepath.Join(extractRoot, pin.binaryRel)
	if st, err := os.Stat(src); err != nil || st.IsDir() {
		// Some archives nest under a versioned folder.
		found := ""
		_ = filepath.Walk(extractRoot, func(path string, info os.FileInfo, err error) error {
			if err != nil || info == nil || info.IsDir() {
				return nil
			}
			if filepath.Base(path) == "signal-cli" || filepath.Base(path) == pin.binaryRel {
				found = path
				return io.EOF
			}
			return nil
		})
		if found == "" {
			return "", fmt.Errorf("signal-cli binary missing after extract")
		}
		src = found
	}
	dest := filepath.Join(dir, "bin", "signal-cli")
	_ = os.Remove(dest)
	if err := os.Rename(src, dest); err != nil {
		data, rerr := os.ReadFile(src)
		if rerr != nil {
			return "", err
		}
		if err := os.WriteFile(dest, data, 0o755); err != nil {
			return "", err
		}
	}
	_ = os.Chmod(dest, 0o755)
	_ = os.RemoveAll(extractRoot)
	abs, err := filepath.Abs(dest)
	if err != nil {
		return dest, nil
	}
	log.Printf("signal: managed signal-cli ready at %s", abs)
	return abs, nil
}

func downloadFileSHA256(url, dest, wantHex string) error {
	_ = os.Remove(dest)
	tmp := dest + ".partial"
	_ = os.Remove(tmp)

	client := &http.Client{Timeout: 15 * time.Minute}
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

func extractTarGzFlat(archive, destDir string) error {
	if err := os.MkdirAll(destDir, 0o700); err != nil {
		return err
	}
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
		if filepath.IsAbs(name) || (runtime.GOOS == "windows" && len(name) >= 2 && name[1] == ':') {
			return fmt.Errorf("refusing absolute archive path: %s", hdr.Name)
		}
		target := filepath.Join(destDir, name)
		rel, err := filepath.Rel(destDir, target)
		if err != nil || strings.HasPrefix(rel, "..") {
			return fmt.Errorf("archive path escapes dest: %s", hdr.Name)
		}
		switch hdr.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, 0o700); err != nil {
				return err
			}
		case tar.TypeReg:
			if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
				return err
			}
			out, err := os.OpenFile(target, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, os.FileMode(hdr.Mode)|0o600)
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
		}
	}
	return nil
}
