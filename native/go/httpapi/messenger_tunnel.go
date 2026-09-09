package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
	"github.com/AhmiDarrow/RemedyAI/native/go/gateway"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// tunnelStartedProcess is one supervised cloudflared child.
type tunnelStartedProcess struct {
	PID     uint32
	Handle  uint64
	Cleanup func() error
}

// Tunnel process starter — production uses Zig; tests inject doubles.
type tunnelProcessStarter func(ctx context.Context, argv []string, env map[string]string, cwd string) (*tunnelStartedProcess, error)

func zigTunnelStarter(home string) tunnelProcessStarter {
	return func(_ context.Context, argv []string, env map[string]string, cwd string) (*tunnelStartedProcess, error) {
		key, err := secret.EnsureHostSigningKey(home)
		if err != nil {
			return nil, err
		}
		if err := core.EnsureSigningKey(key); err != nil {
			return nil, err
		}
		_ = core.WriteJailSetRoots(nil)
		token, nowMS, err := core.IssueProcessSpawnToken(argv, env, false, false)
		if err != nil {
			return nil, err
		}
		pid, handle, err := core.ProcessSpawnAuthorized(argv, cwd, env, false, token, "", "", false, nowMS)
		if err != nil {
			return nil, err
		}
		return &tunnelStartedProcess{
			PID:    pid,
			Handle: handle,
			Cleanup: func() error {
				var joined error
				if pid != 0 {
					joined = errors.Join(joined, core.ProcessKillTree(pid))
				}
				joined = errors.Join(joined, core.ProcessClose(handle))
				return joined
			},
		}, nil
	}
}

var defaultTunnelStarter = zigTunnelStarter

// pollQuickTunnelHostname GETs metrics /quicktunnel until hostname appears.
var pollQuickTunnelHostname = func(metricsURL string, timeout time.Duration) (string, error) {
	deadline := time.Now().Add(timeout)
	client := &http.Client{Timeout: 2 * time.Second}
	var lastErr error
	for time.Now().Before(deadline) {
		resp, err := client.Get(metricsURL)
		if err != nil {
			lastErr = err
			time.Sleep(250 * time.Millisecond)
			continue
		}
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		_ = resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			lastErr = fmt.Errorf("HTTP %s", resp.Status)
			time.Sleep(250 * time.Millisecond)
			continue
		}
		var payload struct {
			Hostname string `json:"hostname"`
		}
		if err := json.Unmarshal(body, &payload); err != nil {
			lastErr = err
			time.Sleep(250 * time.Millisecond)
			continue
		}
		host := strings.TrimSpace(payload.Hostname)
		if host != "" {
			if !strings.HasPrefix(host, "http://") && !strings.HasPrefix(host, "https://") {
				host = "https://" + host
			}
			return strings.TrimRight(host, "/"), nil
		}
		lastErr = fmt.Errorf("empty hostname")
		time.Sleep(250 * time.Millisecond)
	}
	if lastErr == nil {
		lastErr = fmt.Errorf("timeout")
	}
	return "", fmt.Errorf("quick tunnel hostname not ready: %w", lastErr)
}

type messengerTunnelState struct {
	mu sync.Mutex

	running     bool
	mode        string // quick | named
	publicURL   string
	metricsAddr string
	binary      string
	pid         uint32
	setEnv      bool // we exported REMEDY_PUBLIC_BASE_URL
	prevEnv     string
	prevEnvSet  bool
	proc        *tunnelStartedProcess
	origin      *webhookOrigin
	err         string
}

// webhookOrigin is the loopback listener a tunnel is pointed at. It serves the
// platform webhook routes and nothing else, so a rewritten Host header cannot
// walk from the tunnel into the API, the SPA, or token bootstrap.
type webhookOrigin struct {
	ln   net.Listener
	srv  *http.Server
	port int
}

func (o *webhookOrigin) close() {
	if o == nil {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if o.srv != nil {
		_ = o.srv.Shutdown(ctx)
	}
	if o.ln != nil {
		_ = o.ln.Close()
	}
}

// startWebhookOrigin binds a fresh loopback port that only answers
// /api/webhooks/* and /api/webhook/{source}. Every other path is 404 — the
// tunnel never learns the API exists.
func (s *Server) startWebhookOrigin() (*webhookOrigin, error) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("webhook origin listen: %w", err)
	}
	port := 0
	if tcp, ok := ln.Addr().(*net.TCPAddr); ok {
		port = tcp.Port
	}
	mux := s.mux
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !isWebhookPath(r.URL.Path) {
			writeJSON(w, http.StatusNotFound, map[string]string{
				"detail": "not found (this origin serves messenger webhooks only)",
			})
			return
		}
		mux.ServeHTTP(w, r)
	})
	srv := &http.Server{
		Handler:           handler,
		ReadHeaderTimeout: 15 * time.Second,
	}
	go func() { _ = srv.Serve(ln) }()
	log.Printf("messenger tunnel: webhook-only origin on 127.0.0.1:%d (the API and Web UI stay off the tunnel)", port)
	return &webhookOrigin{ln: ln, srv: srv, port: port}, nil
}

// tunnelRunning reports whether a managed cloudflared tunnel is up. Token
// bootstrap is refused while it is (auth.go), so a tunnelled request can never
// be answered with the local API token.
func (s *Server) tunnelRunning() bool {
	ts := s.tunnelState()
	if ts == nil {
		return false
	}
	ts.mu.Lock()
	defer ts.mu.Unlock()
	return ts.running
}

func (s *Server) tunnelState() *messengerTunnelState {
	if s == nil {
		return nil
	}
	s.tunnelOnce.Do(func() {
		s.tunnel = &messengerTunnelState{}
	})
	return s.tunnel
}

// MessengerTunnelStatus is the JSON shape for GET /api/messengers/tunnel.
type MessengerTunnelStatus struct {
	Running       bool   `json:"running"`
	Mode          string `json:"mode,omitempty"`
	PublicURL     string `json:"public_url,omitempty"`
	OriginURL     string `json:"origin_url,omitempty"`
	Binary        string `json:"binary,omitempty"`
	BinaryReady   bool   `json:"binary_ready"`
	DownloadURL   string `json:"download_url,omitempty"`
	EnvConfigured bool   `json:"env_configured"`
	PID           uint32 `json:"pid,omitempty"`
	Error         string `json:"error,omitempty"`
	Hint          string `json:"hint,omitempty"`
}

func (s *Server) messengerTunnelStatus() MessengerTunnelStatus {
	home := ResolveHomeDir(s.homeDir)
	bin := gateway.LookupManagedCloudflared(home)
	st := MessengerTunnelStatus{
		BinaryReady:   bin != "",
		Binary:        bin,
		DownloadURL:   gateway.CloudflaredDownloadURL(),
		EnvConfigured: publicHTTPSTunnelConfigured(),
		Hint: "Exposes local messenger webhooks (WhatsApp / Teams / Google Chat) over HTTPS. " +
			"The tunnel is pointed at a webhook-only loopback origin; the API, the Web UI and " +
			"token bootstrap are not reachable through it.",
	}
	ts := s.tunnelState()
	if ts == nil {
		return st
	}
	ts.mu.Lock()
	defer ts.mu.Unlock()
	st.Running = ts.running
	st.Mode = ts.mode
	st.PublicURL = ts.publicURL
	if ts.origin != nil {
		st.OriginURL = fmt.Sprintf("http://127.0.0.1:%d", ts.origin.port)
	}
	st.PID = ts.pid
	st.Error = ts.err
	if st.Binary == "" && ts.binary != "" {
		st.Binary = ts.binary
		st.BinaryReady = true
	}
	if st.PublicURL != "" {
		st.EnvConfigured = true
	}
	return st
}

type tunnelStartBody struct {
	Mode          string `json:"mode"`                      // quick (default) | named
	Token         string `json:"token,omitempty"`           // named tunnel token
	PublicBaseURL string `json:"public_base_url,omitempty"` // required for named; optional override for quick
	OriginPort    int    `json:"origin_port,omitempty"`     // default apiListenPort or 7400
}

func (s *Server) handleMessengerTunnelStatus(w http.ResponseWriter, r *http.Request) {
	if refuseConnectProxy(w, r) {
		return
	}
	if !requireLoopbackConnect(w, r) {
		return
	}
	writeJSON(w, http.StatusOK, s.messengerTunnelStatus())
}

func (s *Server) handleMessengerTunnelStart(w http.ResponseWriter, r *http.Request) {
	if refuseConnectProxy(w, r) {
		return
	}
	if !requireLoopbackConnect(w, r) {
		return
	}
	var body tunnelStartBody
	if r.Body != nil {
		defer r.Body.Close()
		dec := json.NewDecoder(io.LimitReader(r.Body, 1<<20))
		dec.DisallowUnknownFields()
		if err := dec.Decode(&body); err != nil && !errors.Is(err, io.EOF) {
			writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON: " + err.Error()})
			return
		}
	}
	st, err := s.startMessengerTunnel(body)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, st)
}

func (s *Server) handleMessengerTunnelStop(w http.ResponseWriter, r *http.Request) {
	if refuseConnectProxy(w, r) {
		return
	}
	if !requireLoopbackConnect(w, r) {
		return
	}
	st := s.stopMessengerTunnel()
	writeJSON(w, http.StatusOK, st)
}

func (s *Server) handleMessengerSignalEnsure(w http.ResponseWriter, r *http.Request) {
	if refuseConnectProxy(w, r) {
		return
	}
	if !requireLoopbackConnect(w, r) {
		return
	}
	home := ResolveHomeDir(s.homeDir)
	path, err := gateway.EnsureManagedSignalCLI(home)
	needsJava := gateway.SignalCLINeedsJava()
	javaOK := gateway.SignalCLIJavaOK()
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{
			"detail":       err.Error(),
			"download_url": gateway.SignalCLIDownloadURL(),
			"needs_java":   needsJava,
			"java_ok":      javaOK,
		})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"status":       "ok",
		"cli_path":     path,
		"download_url": gateway.SignalCLIDownloadURL(),
		"needs_java":   needsJava,
		"java_ok":      javaOK,
		"hint":         gatewaySignalEnsureHint(javaOK),
	})
}

func gatewaySignalEnsureHint(javaOK bool) string {
	if gateway.SignalCLINeedsJava() && !javaOK {
		return "signal-cli installed under ~/.remedy/signal. Install Java 21+ (Temurin), keep it on PATH, and restart Remedy, then set the Signal account phone number."
	}
	return "signal-cli installed under ~/.remedy/signal. Set the Signal account phone number to finish setup."
}

func (s *Server) startMessengerTunnel(body tunnelStartBody) (MessengerTunnelStatus, error) {
	ts := s.tunnelState()
	ts.mu.Lock()
	if ts.running {
		ts.mu.Unlock()
		return s.messengerTunnelStatus(), nil
	}
	ts.mu.Unlock()

	mode := strings.ToLower(strings.TrimSpace(body.Mode))
	if mode == "" {
		mode = "quick"
	}
	if mode != "quick" && mode != "named" {
		return MessengerTunnelStatus{}, fmt.Errorf("mode must be quick or named")
	}
	token := strings.TrimSpace(body.Token)
	publicOverride := strings.TrimSpace(body.PublicBaseURL)
	if mode == "named" {
		if token == "" {
			return MessengerTunnelStatus{}, fmt.Errorf("named tunnel requires a Cloudflare tunnel token")
		}
		if publicOverride == "" {
			return MessengerTunnelStatus{}, fmt.Errorf("named tunnel requires public_base_url (https://… hostname you published)")
		}
		if err := validatePublicHTTPSBase(publicOverride); err != nil {
			return MessengerTunnelStatus{}, err
		}
	}
	if publicOverride != "" {
		if err := validatePublicHTTPSBase(publicOverride); err != nil {
			return MessengerTunnelStatus{}, err
		}
	}

	home := ResolveHomeDir(s.homeDir)
	bin, err := gateway.EnsureManagedCloudflared(home)
	if err != nil {
		return MessengerTunnelStatus{}, err
	}
	if body.OriginPort > 0 {
		log.Printf("messenger tunnel: origin_port=%d ignored; the tunnel only ever serves the webhook origin", body.OriginPort)
	}
	webhookOrig, err := s.startWebhookOrigin()
	if err != nil {
		return MessengerTunnelStatus{}, err
	}
	origin := fmt.Sprintf("http://127.0.0.1:%d", webhookOrig.port)

	metricsLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		webhookOrig.close()
		return MessengerTunnelStatus{}, fmt.Errorf("metrics listen: %w", err)
	}
	metricsPort := metricsLn.Addr().(*net.TCPAddr).Port
	_ = metricsLn.Close()
	metricsAddr := fmt.Sprintf("127.0.0.1:%d", metricsPort)

	argv := []string{bin, "tunnel", "--no-autoupdate", "--metrics", metricsAddr}
	if mode == "quick" {
		argv = append(argv, "--url", origin)
	} else {
		argv = append(argv, "run", "--token", token)
	}

	starter := s.tunnelStarter
	if starter == nil {
		starter = defaultTunnelStarter(home)
	}
	proc, err := starter(context.Background(), argv, nil, filepath.Dir(bin))
	if err != nil {
		webhookOrig.close()
		return MessengerTunnelStatus{}, fmt.Errorf("start cloudflared: %w", err)
	}

	publicURL := publicOverride
	if mode == "quick" && publicURL == "" {
		host, perr := pollQuickTunnelHostname("http://"+metricsAddr+"/quicktunnel", 45*time.Second)
		if perr != nil {
			_ = proc.Cleanup()
			webhookOrig.close()
			return MessengerTunnelStatus{}, fmt.Errorf("quick tunnel URL: %w", perr)
		}
		publicURL = host
	}
	s.applyTunnelPublicURL(publicURL, true)

	ts.mu.Lock()
	ts.running = true
	ts.mode = mode
	ts.publicURL = strings.TrimRight(publicURL, "/")
	ts.metricsAddr = metricsAddr
	ts.binary = bin
	ts.pid = proc.PID
	ts.proc = proc
	ts.origin = webhookOrig
	ts.setEnv = true
	ts.err = ""
	ts.mu.Unlock()
	if mode == "named" {
		log.Printf("messenger tunnel: named mode — point the Cloudflare ingress at %s so only webhooks are published", origin)
	}

	return s.messengerTunnelStatus(), nil
}

func (s *Server) stopMessengerTunnel() MessengerTunnelStatus {
	ts := s.tunnelState()
	ts.mu.Lock()
	proc := ts.proc
	origin := ts.origin
	ts.origin = nil
	setEnv := ts.setEnv
	ts.running = false
	ts.mode = ""
	ts.publicURL = ""
	ts.metricsAddr = ""
	ts.pid = 0
	ts.proc = nil
	ts.setEnv = false
	ts.err = ""
	prev := ts.prevEnv
	prevSet := ts.prevEnvSet
	ts.prevEnv = ""
	ts.prevEnvSet = false
	ts.mu.Unlock()

	if proc != nil && proc.Cleanup != nil {
		_ = proc.Cleanup()
	} else if proc != nil && proc.PID != 0 {
		_ = core.ProcessKillTree(proc.PID)
		_ = core.ProcessClose(proc.Handle)
	}
	origin.close()
	if setEnv {
		if prevSet {
			_ = os.Setenv("REMEDY_PUBLIC_BASE_URL", prev)
		} else {
			_ = os.Unsetenv("REMEDY_PUBLIC_BASE_URL")
		}
	}
	return s.messengerTunnelStatus()
}

func (s *Server) applyTunnelPublicURL(raw string, managed bool) {
	raw = strings.TrimRight(strings.TrimSpace(raw), "/")
	if raw == "" {
		return
	}
	ts := s.tunnelState()
	ts.mu.Lock()
	defer ts.mu.Unlock()
	if managed && !ts.prevEnvSet {
		if v, ok := os.LookupEnv("REMEDY_PUBLIC_BASE_URL"); ok {
			ts.prevEnv = v
			ts.prevEnvSet = true
		}
	}
	_ = os.Setenv("REMEDY_PUBLIC_BASE_URL", raw)
	if managed {
		ts.setEnv = true
	}
	// Persist a small marker for operators (not required for health).
	home := ResolveHomeDir(s.homeDir)
	if dir := gateway.ManagedTunnelDir(home); dir != "" {
		_ = os.MkdirAll(dir, 0o700)
		_ = os.WriteFile(filepath.Join(dir, "public_url"), []byte(raw+"\n"), 0o600)
	}
}

func validatePublicHTTPSBase(raw string) error {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil {
		return fmt.Errorf("invalid public_base_url: %w", err)
	}
	if !strings.EqualFold(u.Scheme, "https") || u.Host == "" {
		return fmt.Errorf("public_base_url must be https://hostname")
	}
	return nil
}
