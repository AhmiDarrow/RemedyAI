package connect

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
)

const (
	pkgsFeed = "https://pkgs.tailscale.com/stable/?mode=json"
	pkgsBase = "https://pkgs.tailscale.com/stable/"
)

// TailscaleStatus mirrors Python remedy.connect.tailscale_bootstrap.tailscale_status.
type TailscaleStatus struct {
	Installed   bool   `json:"installed"`
	Running     bool   `json:"running"`
	LoggedIn    bool   `json:"logged_in"`
	TailnetIPv4 string `json:"tailnet_ipv4"`
	Version     string `json:"version"`
	Error       string `json:"error"`
}

// TailscaleAction mirrors install/login response shapes from the Python routes.
type TailscaleAction struct {
	Status       string `json:"status"`
	Message      string `json:"message"`
	LoginURL     string `json:"login_url"`
	MSIPath      string `json:"msi_path"`
	InstallerURL string `json:"installer_url"`
}

// Overridable Zig / feed entrypoints (tests).
var (
	zigTailscaleStatus    = core.TailscaleStatusJSON
	zigTailscaleLogin     = core.TailscaleLoginJSON
	zigTailscaleLaunch    = core.TailscaleLaunchMSI
	latestWindowsMSIURLFn = latestWindowsMSIURL
)

// GetTailscaleStatus queries Zig remedy_core. Fail-closed when the core ABI is
// unavailable — never os/exec and never a Python soft fallback.
func GetTailscaleStatus() TailscaleStatus {
	raw, err := zigTailscaleStatus()
	if err != nil {
		return TailscaleStatus{
			Error: "could not read Tailscale status: " + err.Error(),
		}
	}
	var st TailscaleStatus
	if err := json.Unmarshal(raw, &st); err != nil {
		return TailscaleStatus{
			Error: "could not read Tailscale status: malformed core response",
		}
	}
	return st
}

// StartTailscaleLogin runs `tailscale up` through Zig and returns the login URL.
func StartTailscaleLogin() TailscaleAction {
	raw, err := zigTailscaleLogin()
	if err != nil {
		return TailscaleAction{
			Status:  "error",
			Message: err.Error(),
		}
	}
	var act TailscaleAction
	if err := json.Unmarshal(raw, &act); err != nil {
		return TailscaleAction{
			Status:  "error",
			Message: "malformed core login response",
		}
	}
	return act
}

// EnsureTailscale downloads the official Windows MSI when missing and launches
// it via Zig (msiexec). Idempotent when already installed.
func EnsureTailscale(downloadDir string) TailscaleAction {
	st := GetTailscaleStatus()
	if st.Installed {
		return TailscaleAction{
			Status:  "already_installed",
			Message: "Tailscale is already installed.",
		}
	}
	if runtime.GOOS != "windows" {
		return TailscaleAction{
			Status:  "error",
			Message: "Automatic install is Windows-only here. Install Tailscale from tailscale.com/download.",
		}
	}
	url, err := latestWindowsMSIURLFn()
	if err != nil {
		return TailscaleAction{Status: "error", Message: err.Error()}
	}
	name := url[strings.LastIndex(url, "/")+1:]
	target := downloadDir
	if strings.TrimSpace(target) == "" {
		target = filepath.Join(os.TempDir(), "remedy-tailscale")
	}
	if err := os.MkdirAll(target, 0o700); err != nil {
		return TailscaleAction{
			Status:       "error",
			Message:      "could not create download dir: " + err.Error(),
			InstallerURL: url,
		}
	}
	dest := filepath.Join(target, name)
	if fi, err := os.Stat(dest); err != nil || fi.Size() == 0 {
		if err := downloadFile(url, dest); err != nil {
			return TailscaleAction{
				Status:       "error",
				Message:      "download failed: " + err.Error(),
				MSIPath:      dest,
				InstallerURL: url,
			}
		}
	}
	if _, err := zigTailscaleLaunch(dest); err != nil {
		return TailscaleAction{
			Status:       "error",
			Message:      "could not launch installer: " + err.Error(),
			MSIPath:      dest,
			InstallerURL: url,
		}
	}
	return TailscaleAction{
		Status: "downloading",
		Message: fmt.Sprintf(
			"Downloaded %s. Follow the installer, then sign in when it opens — use the same account as your phone.",
			name,
		),
		MSIPath:      dest,
		InstallerURL: url,
	}
}

func latestWindowsMSIURL() (string, error) {
	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.Get(pkgsFeed)
	if err != nil {
		return "", fmt.Errorf("could not fetch Tailscale package feed: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return "", fmt.Errorf("could not fetch Tailscale package feed: HTTP %d", resp.StatusCode)
	}
	var data struct {
		MSIs map[string]string `json:"MSIs"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&data); err != nil {
		return "", fmt.Errorf("could not fetch Tailscale package feed: %w", err)
	}
	name := strings.TrimSpace(data.MSIs["amd64"])
	if name == "" {
		return "", fmt.Errorf("Tailscale package feed has no amd64 MSI")
	}
	return pkgsBase + name, nil
}

func downloadFile(url, dest string) error {
	tmp := dest + ".part"
	defer os.Remove(tmp)
	client := &http.Client{Timeout: 120 * time.Second}
	resp, err := client.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	f, err := os.Create(tmp)
	if err != nil {
		return err
	}
	_, copyErr := io.Copy(f, resp.Body)
	closeErr := f.Close()
	if copyErr != nil {
		return copyErr
	}
	if closeErr != nil {
		return closeErr
	}
	return os.Rename(tmp, dest)
}
