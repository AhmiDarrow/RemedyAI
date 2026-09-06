package gateway

import (
	"fmt"
	"log"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

// Pinned cloudflared release (cloudflare/cloudflared). Used to expose loopback
// :7400 webhooks via a Quick Tunnel or a named tunnel token.
const (
	cloudflaredVersion = "2026.8.3"
	cloudflaredBaseURL = "https://github.com/cloudflare/cloudflared/releases/download/" + cloudflaredVersion + "/"
)

type cloudflaredPin struct {
	filename string
	sha256   string
	binName  string
}

var cloudflaredPins = map[string]cloudflaredPin{
	"windows/amd64": {
		filename: "cloudflared-windows-amd64.exe",
		sha256:   "83e726ed18ea78c5ad5213c4c3a3a27051393950d2bc8ed4de69bec12d14eaae",
		binName:  "cloudflared.exe",
	},
	"linux/amd64": {
		filename: "cloudflared-linux-amd64",
		sha256:   "f29324fe934d1e100617484c78deef803c4dc2cd351d645bbde42e96b4fccc5e",
		binName:  "cloudflared",
	},
	"linux/arm64": {
		filename: "cloudflared-linux-arm64",
		sha256:   "4bcfd35521a7cbc545ebfd5d57334a71ee180e2a64874981f374c81472118391",
		binName:  "cloudflared",
	},
}

// Overridable for tests (httptest). Production uses downloadFileSHA256.
var managedDownloadFile = downloadFileSHA256

// cloudflaredDownloadBase overrides the GitHub release base in tests.
var cloudflaredDownloadBase = cloudflaredBaseURL

// ManagedTunnelDir is ~/.remedy/tunnel (or REMEDY_HOME/tunnel).
func ManagedTunnelDir(home string) string {
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
	return filepath.Join(home, "tunnel")
}

// CloudflaredDownloadURL is the owner-facing install URL for this OS.
func CloudflaredDownloadURL() string {
	if pin, ok := cloudflaredPins[runtime.GOOS+"/"+runtime.GOARCH]; ok {
		return cloudflaredDownloadBase + pin.filename
	}
	return "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
}

// LookupManagedCloudflared returns an existing managed binary path, or "".
func LookupManagedCloudflared(home string) string {
	dir := ManagedTunnelDir(home)
	if dir == "" {
		return ""
	}
	candidates := []string{
		filepath.Join(dir, "bin", "cloudflared"),
		filepath.Join(dir, "cloudflared"),
	}
	if runtime.GOOS == "windows" {
		candidates = append([]string{
			filepath.Join(dir, "bin", "cloudflared.exe"),
			filepath.Join(dir, "cloudflared.exe"),
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
	return ""
}

// EnsureManagedCloudflared downloads the pinned cloudflared binary into
// ~/.remedy/tunnel/bin when missing.
func EnsureManagedCloudflared(home string) (string, error) {
	if existing := LookupManagedCloudflared(home); existing != "" {
		return existing, nil
	}
	if v := strings.TrimSpace(strings.ToLower(os.Getenv("REMEDY_SKIP_MANAGED_CLOUDFLARED_DOWNLOAD"))); v == "1" || v == "true" || v == "yes" {
		return "", fmt.Errorf("cloudflared missing and download disabled (REMEDY_SKIP_MANAGED_CLOUDFLARED_DOWNLOAD); see %s", CloudflaredDownloadURL())
	}
	pin, ok := cloudflaredPins[runtime.GOOS+"/"+runtime.GOARCH]
	if !ok {
		return "", fmt.Errorf("no managed cloudflared pin for %s/%s — install from %s", runtime.GOOS, runtime.GOARCH, CloudflaredDownloadURL())
	}
	dir := ManagedTunnelDir(home)
	if dir == "" {
		return "", fmt.Errorf("cannot resolve REMEDY_HOME for managed cloudflared")
	}
	binDir := filepath.Join(dir, "bin")
	if err := os.MkdirAll(binDir, 0o700); err != nil {
		return "", err
	}
	url := cloudflaredDownloadBase + pin.filename
	archive := filepath.Join(dir, pin.filename)
	log.Printf("tunnel: downloading managed cloudflared %s", cloudflaredVersion)
	if err := managedDownloadFile(url, archive, pin.sha256); err != nil {
		return "", fmt.Errorf("download cloudflared: %w", err)
	}
	dest := filepath.Join(binDir, pin.binName)
	_ = os.Remove(dest)
	if err := os.Rename(archive, dest); err != nil {
		data, rerr := os.ReadFile(archive)
		if rerr != nil {
			return "", err
		}
		if err := os.WriteFile(dest, data, 0o755); err != nil {
			return "", err
		}
		_ = os.Remove(archive)
	}
	_ = os.Chmod(dest, 0o755)
	_ = os.WriteFile(filepath.Join(dir, "VERSION"), []byte(cloudflaredVersion+"\n"), 0o600)
	abs, err := filepath.Abs(dest)
	if err != nil {
		return dest, nil
	}
	log.Printf("tunnel: managed cloudflared ready at %s", abs)
	return abs, nil
}
