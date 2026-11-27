package httpapi

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
	"github.com/AhmiDarrow/RemedyAI/native/go/workers"
)

// The managed ClaimIDX host.
//
// ClaimIDX is a Python program, so it is not in the installer. After the
// runtime starts, and only while the owner's ClaimIDX switch is on, the
// runtime makes a private environment under <home>/claimidx, installs the
// pinned ClaimIDX release into it with pip, and runs it on loopback. A Python
// already on the PC is used to make that environment; a PC with none gets a
// private, checksum-verified CPython in the same folder.
//
// Two settings decide what it does:
//   - claimidx_enabled: off stops the host and nothing is installed or
//     started. Never set, an installed host runs and nothing new is installed.
//   - claimidx_public_ledger (default OFF): claims stay on this computer
//     unless the owner turns sharing on. Off sets CLAIMIDX_SHARE=0 and
//     CLAIMIDX_COMMONS=0 and writes "share": false to the host's config.
//
// The host never sees the owner's own ClaimIDX configuration or credentials:
// it has its own config, database and token.

const (
	claimidxVersion  = "0.7.31"
	claimidxWheel    = "claimidx-" + claimidxVersion + "-py3-none-any.whl"
	claimidxWheelURL = "https://files.pythonhosted.org/packages/b5/2d/aee5031bb7bbb2e740f30ba6e88a0e858821d24f1f5523544723d6282920/" + claimidxWheel
	claimidxWheelSHA = "64b715fb3899198a2a72d9a29a6b09270b16ed1d3b46a929270343e13eb6d51a"
	claimidxBindHost = "127.0.0.1"
	// Its own port: never the 7340 an owner's separately run ClaimIDX uses.
	claimidxPort = 17340
)

// claimidxExec runs a short command and returns its output. Tests replace it.
var claimidxExec = func(home string, argv []string, env map[string]string, cwd string, timeout time.Duration) (string, error) {
	key, err := secret.EnsureHostSigningKey(home)
	if err != nil {
		return "", err
	}
	if err := core.EnsureSigningKey(key); err != nil {
		return "", err
	}
	token, nowMS, err := core.IssueProcessSpawnToken(argv, env, false, false)
	if err != nil {
		return "", err
	}
	var res core.ExecCaptureResult
	if err := core.WithWriteJail(nil, func() error {
		var err error
		res, err = core.ExecCaptureAuthorized(argv, cwd, env, false, token, "", "", false, nowMS, uint32(timeout/time.Millisecond))
		return err
	}); err != nil {
		return "", err
	}
	out := strings.TrimSpace(string(res.Stdout))
	if res.TimedOut {
		return out, fmt.Errorf("%s timed out", filepath.Base(argv[0]))
	}
	if res.ExitCode != 0 {
		tail := strings.TrimSpace(string(res.Stderr))
		if tail == "" {
			tail = out
		}
		if len(tail) > 400 {
			tail = tail[len(tail)-400:]
		}
		return out, fmt.Errorf("%s exited %d: %s", filepath.Base(argv[0]), res.ExitCode, tail)
	}
	return out, nil
}

type claimidxHost struct {
	home string

	mu      sync.Mutex
	stop    func() error // nil while not running
	public  bool         // the sharing setting the running host was started with
	lastErr string
	busy    bool
}

func newClaimidxHost(home string) *claimidxHost {
	return &claimidxHost{home: ResolveHomeDir(home)}
}

func (h *claimidxHost) root() string    { return filepath.Join(h.home, "claimidx") }
func (h *claimidxHost) runtime() string { return filepath.Join(h.root(), "runtime-"+claimidxVersion) }
func (h *claimidxHost) marker() string  { return filepath.Join(h.runtime(), "remedy-installed.json") }
func (h *claimidxHost) baseURL() string {
	return fmt.Sprintf("http://%s:%d", claimidxBindHost, claimidxPort)
}

func (h *claimidxHost) venvPython() string {
	if runtime.GOOS == "windows" {
		return filepath.Join(h.runtime(), "Scripts", "python.exe")
	}
	return filepath.Join(h.runtime(), "bin", "python")
}

// claimidxEnabled is the owner's switch when they set it.
func claimidxEnabled(cfg ConfigMap) bool { return cfgBool(cfg, "claimidx_enabled", true) }

// wanted says whether the host should run. With the switch never touched, a
// host that is already installed keeps running, but nothing is downloaded and
// installed until the owner turns ClaimIDX on.
func (h *claimidxHost) wanted(cfg ConfigMap) bool {
	if _, set := cfg["claimidx_enabled"]; set {
		return claimidxEnabled(cfg)
	}
	return h != nil && h.installed()
}

// claimidxPublic: sharing to the public ledger is off unless the owner turned it on.
func claimidxPublic(cfg ConfigMap) bool {
	return claimidxEnabled(cfg) && cfgBool(cfg, "claimidx_public_ledger", false)
}

func (h *claimidxHost) installed() bool {
	raw, err := os.ReadFile(h.marker())
	if err != nil {
		return false
	}
	var m struct {
		OK      bool   `json:"ok"`
		Version string `json:"version"`
	}
	return json.Unmarshal(raw, &m) == nil && m.OK && m.Version == claimidxVersion && fileExists(h.venvPython())
}

// serviceToken protects the host's changing endpoints even on loopback.
func (h *claimidxHost) serviceToken() (string, error) {
	p := filepath.Join(h.root(), "service-token")
	if raw, err := os.ReadFile(p); err == nil && len(strings.TrimSpace(string(raw))) >= 32 {
		return strings.TrimSpace(string(raw)), nil
	}
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	tok := hex.EncodeToString(b)
	if err := os.MkdirAll(h.root(), 0o700); err != nil {
		return "", err
	}
	return tok, os.WriteFile(p, []byte(tok+"\n"), 0o600)
}

// env is what the host and its installer see. Everything ClaimIDX reads is
// set here, so nothing from the owner's own ClaimIDX leaks in.
func (h *claimidxHost) env(public bool) (map[string]string, error) {
	tok, err := h.serviceToken()
	if err != nil {
		return nil, err
	}
	env := map[string]string{
		"CLAIMIDX_CONFIG":               filepath.Join(h.root(), "config.json"),
		"CLAIMIDX_DB":                   filepath.Join(h.root(), "index.sqlite"),
		"CLAIMIDX_OWNER":                "did:claimidx:remedy",
		"CLAIMIDX_AGENT":                "remedy",
		"CLAIMIDX_HOME_TOKEN":           tok,
		"PIP_DISABLE_PIP_VERSION_CHECK": "1",
		"PIP_NO_INPUT":                  "1",
		"PYTHONUTF8":                    "1",
	}
	if public {
		env["CLAIMIDX_SHARE"] = "1"
		env["CLAIMIDX_COMMONS"] = "1"
	} else {
		env["CLAIMIDX_SHARE"] = "0"
		env["CLAIMIDX_COMMONS"] = "0"
	}
	return env, nil
}

// basePython finds a Python 3.11+ already on the PC, else downloads a private one.
func (h *claimidxHost) basePython(env map[string]string) (string, error) {
	const probe = "import sys; print(sys.executable if sys.version_info >= (3, 11) else '')"
	try := func(argv ...string) string {
		out, err := claimidxExec(h.home, append(argv, "-c", probe), env, "", 20*time.Second)
		if err != nil {
			return ""
		}
		line := strings.TrimSpace(strings.SplitN(out, "\n", 2)[0])
		if line != "" && fileExists(line) {
			return line
		}
		return ""
	}
	if runtime.GOOS == "windows" {
		if py, err := tools.LookPath("py"); err == nil {
			if exe := try(py, "-3"); exe != "" {
				return exe, nil
			}
		}
	}
	for _, name := range []string{"python3", "python"} {
		if p, err := tools.LookPath(name); err == nil {
			if exe := try(p); exe != "" {
				return exe, nil
			}
		}
	}
	return workers.EnsureManagedPythonIn(filepath.Join(h.root(), "python"))
}

func (h *claimidxHost) install(env map[string]string) error {
	if h.installed() {
		return nil
	}
	if err := os.MkdirAll(h.root(), 0o700); err != nil {
		return err
	}
	// No marker means an interrupted install: start that runtime over.
	_ = os.RemoveAll(h.runtime())
	base, err := h.basePython(env)
	if err != nil {
		return fmt.Errorf("no Python to install ClaimIDX with: %w", err)
	}
	if _, err := claimidxExec(h.home, []string{base, "-m", "venv", h.runtime()}, env, h.root(), 2*time.Minute); err != nil {
		return fmt.Errorf("could not create the ClaimIDX environment: %w", err)
	}
	wheel := filepath.Join(h.root(), claimidxWheel)
	defer os.Remove(wheel)
	if err := fetchVoiceFile(claimidxWheelURL, wheel, claimidxWheelSHA); err != nil {
		return fmt.Errorf("ClaimIDX download: %w", err)
	}
	// file:///C:/... on Windows, file:///home/... elsewhere.
	wheelURL := url.URL{Scheme: "file", Path: "/" + strings.TrimPrefix(filepath.ToSlash(wheel), "/")}
	py := h.venvPython()
	if _, err := claimidxExec(h.home, []string{py, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
		"claimidx[server] @ " + wheelURL.String()}, env, h.root(), 6*time.Minute); err != nil {
		_ = os.RemoveAll(h.runtime())
		return fmt.Errorf("pip could not install ClaimIDX: %w", err)
	}
	got, err := claimidxExec(h.home, []string{py, "-c", "import claimidx; print(claimidx.__version__)"}, env, h.root(), 30*time.Second)
	if err != nil || strings.TrimSpace(got) != claimidxVersion {
		_ = os.RemoveAll(h.runtime())
		return fmt.Errorf("ClaimIDX did not verify after install (got %q)", strings.TrimSpace(got))
	}
	m, _ := json.Marshal(map[string]any{
		"ok": true, "version": claimidxVersion, "wheel_sha256": claimidxWheelSHA,
		"installed_at": time.Now().UTC().Format(time.RFC3339),
	})
	return secret.WriteFileAtomic(h.marker(), m, 0o600)
}

// setup writes the host's own config and seeds its index once per version.
func (h *claimidxHost) setup(env map[string]string, public bool) error {
	cfg := map[string]any{"owner": "did:claimidx:remedy", "agent": "remedy"}
	if !public {
		cfg["share"] = false
	}
	raw, _ := json.MarshalIndent(cfg, "", "  ")
	if err := secret.WriteFileAtomic(filepath.Join(h.root(), "config.json"), raw, 0o600); err != nil {
		return err
	}
	seed := filepath.Join(h.root(), "seed.json")
	var seeded struct {
		Version string `json:"version"`
	}
	if raw, err := os.ReadFile(seed); err == nil && json.Unmarshal(raw, &seeded) == nil && seeded.Version == claimidxVersion {
		return nil
	}
	if _, err := claimidxExec(h.home, []string{h.venvPython(), "-m", "claimidx.cli", "--db",
		filepath.Join(h.root(), "index.sqlite"), "seed"}, env, h.root(), 2*time.Minute); err != nil {
		return fmt.Errorf("ClaimIDX seed: %w", err)
	}
	m, _ := json.Marshal(map[string]any{"version": claimidxVersion, "seeded_at": time.Now().UTC().Format(time.RFC3339)})
	return secret.WriteFileAtomic(seed, m, 0o600)
}

// healthy: the thing answering on the port is our host, not something else.
func (h *claimidxHost) healthy() bool {
	client := &http.Client{Timeout: 800 * time.Millisecond}
	resp, err := client.Get(h.baseURL() + "/api/whoami")
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return false
	}
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
	return claimidxIsOurs(raw)
}

// claimidxIsOurs reports a /api/whoami answer from the host Remedy started:
// the right product, running as Remedy's own operator.
func claimidxIsOurs(raw []byte) bool {
	var v struct {
		Product  string `json:"product"`
		Operator struct {
			DID   string `json:"did"`
			Agent string `json:"agent"`
		} `json:"operator"`
	}
	// The service has spelled its own name both "Claimidx" and "ClaimIDX".
	return json.Unmarshal(raw, &v) == nil && strings.EqualFold(v.Product, "claimidx") &&
		v.Operator.DID == "did:claimidx:remedy" && v.Operator.Agent == "remedy"
}

// Apply brings the host in line with the settings: installed and running when
// ClaimIDX is on, stopped when it is off, restarted when sharing changed.
// It returns at once; the work happens in the background and never blocks
// or fails the runtime.
func (h *claimidxHost) Apply() {
	if h == nil {
		return
	}
	h.mu.Lock()
	if h.busy {
		h.mu.Unlock()
		return
	}
	h.busy = true
	h.mu.Unlock()
	go func() {
		err := h.apply()
		h.mu.Lock()
		h.busy = false
		if err != nil {
			h.lastErr = err.Error()
			log.Printf("claimidx: %v", err)
		} else {
			h.lastErr = ""
		}
		h.mu.Unlock()
	}()
}

func (h *claimidxHost) apply() error {
	cfg := LoadConfig(h.home)
	if !h.wanted(cfg) {
		h.Stop()
		return nil
	}
	public := claimidxPublic(cfg)
	h.mu.Lock()
	running, was := h.stop != nil, h.public
	h.mu.Unlock()
	if running && was == public && h.healthy() {
		return nil
	}
	h.Stop()
	env, err := h.env(public)
	if err != nil {
		return err
	}
	if err := h.install(env); err != nil {
		return err
	}
	if err := h.setup(env, public); err != nil {
		return err
	}
	argv := []string{h.venvPython(), "-m", "claimidx.cli", "--db", filepath.Join(h.root(), "index.sqlite"),
		"serve", "--host", claimidxBindHost, "--port", fmt.Sprint(claimidxPort)}
	proc, err := zigTunnelStarter(h.home)(context.Background(), argv, env, h.root())
	if err != nil {
		return fmt.Errorf("could not start ClaimIDX: %w", err)
	}
	h.mu.Lock()
	h.stop, h.public = proc.Cleanup, public
	h.mu.Unlock()
	for deadline := time.Now().Add(20 * time.Second); time.Now().Before(deadline); time.Sleep(250 * time.Millisecond) {
		if h.healthy() {
			log.Printf("claimidx: %s on %s (public sharing %v)", claimidxVersion, h.baseURL(), public)
			return nil
		}
	}
	h.Stop()
	return errors.New("ClaimIDX did not answer after starting")
}

// Stop ends the host. Safe to call when it is not running.
func (h *claimidxHost) Stop() {
	if h == nil {
		return
	}
	h.mu.Lock()
	stop := h.stop
	h.stop = nil
	h.mu.Unlock()
	if stop != nil {
		_ = stop()
	}
}

// claimidxFor is the host for a server that manages one, else nil (every
// method on a nil host does nothing).
func claimidxFor(cfg Config) *claimidxHost {
	if !cfg.ManageClaimidx {
		return nil
	}
	return newClaimidxHost(cfg.HomeDir)
}

func (s *Server) handleClaimidxStatus(w http.ResponseWriter, _ *http.Request) {
	if s.claimidx == nil {
		cfg := LoadConfig(s.homeDir)
		writeJSON(w, http.StatusOK, map[string]any{
			"enabled": s.claimidx.wanted(cfg), "public": claimidxPublic(cfg),
			"installed": false, "running": false, "working": false, "version": claimidxVersion,
			"error": "ClaimIDX is not managed by this runtime.",
		})
		return
	}
	writeJSON(w, http.StatusOK, s.claimidx.Status())
}

// Status is what Settings shows.
func (h *claimidxHost) Status() map[string]any {
	cfg := LoadConfig(h.home)
	h.mu.Lock()
	running, busy, lastErr := h.stop != nil, h.busy, h.lastErr
	h.mu.Unlock()
	return map[string]any{
		"enabled":   h.wanted(cfg),
		"public":    claimidxPublic(cfg),
		"installed": h.installed(),
		"running":   running,
		"working":   busy,
		"version":   claimidxVersion,
		"url":       h.baseURL(),
		"error":     lastErr,
	}
}
