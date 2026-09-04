// Package httpapi serves the Phase-4 opt-in local HTTP API for remedy-runtime.
// Python remains the production authority on :7400; this package is additive.
package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)


// Version matches pyproject.toml until a shared ldflag/sync lands.
// TODO: wire via -ldflags or scripts/sync_version.py.
const Version = "0.50.2"

// Config controls the minimal local API server.
type Config struct {
	// Token is the expected Bearer / X-Remedy-Token value. Empty resolves
	// from REMEDY_API_KEY or $REMEDY_HOME/auth/local_api_token (plaintext).
	Token string
	// HomeDir overrides REMEDY_HOME when resolving the on-disk token.
	HomeDir string
	// Version overrides the reported product version (tests).
	Version string
	// DBPath is the SQLite memory.db path. Empty derives from HomeDir /
	// REMEDY_HOME / ~/.remedy/memory.db (or :memory: when no home).
	DBPath string
	// TurnRunner powers POST /api/sessions/{id}/messages and .../messages/stream.
	// Nil → 503 (matches Python when runtime is unavailable).
	TurnRunner TurnRunner
}

// Server is the Phase-4 first-slice HTTP API.
type Server struct {
	started  time.Time
	version  string
	token    string
	homeDir  string
	mux      *http.ServeMux
	sessions *sessionStore
	events   *sessionEventHub
	claims   *streamClaims
	runner   TurnRunner

	connectGW     *connect.Gateway
	apiListenPort int
}

// New builds a server with ping/status/turn-active, auth bootstrap, settings,
// sessions CRUD, messages list/create/stream, abort, and session-events SSE.
func New(cfg Config) (*Server, error) {
	version := cfg.Version
	if version == "" {
		version = Version
	}
	homeDir := strings.TrimSpace(cfg.HomeDir)
	token := cfg.Token
	if token == "" {
		token = ResolveToken(homeDir)
	}
	// First-run: generate + persist only when a home is explicit (HomeDir /
	// REMEDY_HOME). Avoid writing into the real ~/.remedy from bare tests.
	if token == "" && AuthEnabled() {
		if homeDir != "" || strings.TrimSpace(os.Getenv("REMEDY_HOME")) != "" {
			token = secret.EnsureLocalAPIToken(ResolveHomeDir(homeDir), "")
		}
	}
	store, err := openSessionStore(resolveDBPath(cfg))
	if err != nil {
		return nil, fmt.Errorf("open session store: %w", err)
	}
	s := &Server{
		started:  time.Now(),
		version:  version,
		token:    token,
		homeDir:  homeDir,
		mux:      http.NewServeMux(),
		sessions: store,
		events:   newSessionEventHub(),
		claims:   newStreamClaims(),
		runner:   cfg.TurnRunner,
	}
	s.mux.HandleFunc("GET /api/ping", s.handlePing)
	s.mux.HandleFunc("GET /api/status", s.handleStatus)
	s.mux.HandleFunc("GET /api/turn-active", s.handleTurnActive)
	s.mux.HandleFunc("GET /api/auth/local-bootstrap", s.handleLocalBootstrap)
	s.mux.HandleFunc("GET /api/settings", s.handleGetSettings)
	s.mux.HandleFunc("PUT /api/settings", s.handlePutSettings)
	s.mux.HandleFunc("GET /api/sessions", s.handleListSessions)
	s.mux.HandleFunc("POST /api/sessions", s.handleCreateSession)
	s.mux.HandleFunc("GET /api/sessions/{id}", s.handleGetSession)
	s.mux.HandleFunc("PATCH /api/sessions/{id}", s.handleUpdateSession)
	s.mux.HandleFunc("DELETE /api/sessions/{id}", s.handleDeleteSession)
	s.mux.HandleFunc("GET /api/sessions/{id}/messages", s.handleListMessages)
	s.mux.HandleFunc("POST /api/sessions/{id}/messages", s.handleSendMessage)
	s.mux.HandleFunc("POST /api/sessions/{id}/messages/stream", s.handleStreamMessage)
	s.mux.HandleFunc("POST /api/sessions/{id}/abort", s.handleAbortSession)
	s.mux.HandleFunc("GET /api/events/sessions", s.handleSessionEvents)
	s.mux.HandleFunc("GET /api/connect", s.handleGetConnect)
	s.mux.HandleFunc("PUT /api/connect", s.handlePutConnect)
	s.mux.HandleFunc("GET /api/connect/addresses", s.handleConnectAddresses)
	s.mux.HandleFunc("POST /api/connect/pair/start", s.handleConnectPairStart)
	s.mux.HandleFunc("POST /api/connect/pause", s.handleConnectPause)
	s.mux.HandleFunc("POST /api/connect/resume", s.handleConnectResume)
	s.mux.HandleFunc("POST /api/connect/devices/{id}/revoke", s.handleConnectRevoke)
	return s, nil
}

// Close stops Connect, aborts in-flight turns, then releases the session store.
func (s *Server) Close() error {
	if s == nil {
		return nil
	}
	s.stopConnectGateway()
	if s.claims != nil {
		s.claims.AbortAll()
		s.claims.WaitTurns()
	}
	if s.sessions == nil {
		return nil
	}
	return s.sessions.Close()
}

// Handler returns the CORS + auth wrapped mux.
func (s *Server) Handler() http.Handler {
	return withCORS(s.withAuth(s.mux))
}

// Serve serves until the listener closes or ctx is canceled.
func (s *Server) Serve(ctx context.Context, ln net.Listener) error {
	s.started = time.Now()
	s.apiListenPort = listenPort(ln)
	s.startConnectGateway(s.apiListenPort)
	httpServer := &http.Server{Handler: s.Handler()}
	errCh := make(chan error, 1)
	go func() {
		errCh <- httpServer.Serve(ln)
	}()
	var serveErr error
	select {
	case <-ctx.Done():
		// Cancel turns before HTTP Shutdown so SSE handlers can drain.
		if s.claims != nil {
			s.claims.AbortAll()
		}
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = httpServer.Shutdown(shutdownCtx)
		err := <-errCh
		if err == nil || errors.Is(err, http.ErrServerClosed) {
			serveErr = ctx.Err()
		} else {
			serveErr = err
		}
	case err := <-errCh:
		if errors.Is(err, http.ErrServerClosed) {
			serveErr = nil
		} else {
			serveErr = err
		}
	}
	_ = s.Close()
	return serveErr
}

// ListenAndServe binds addr (must be loopback) and serves until ctx cancels.
// onBound receives the actual host:port once listening, before Serve blocks.
func ListenAndServe(ctx context.Context, addr string, cfg Config, onBound func(string)) error {
	if err := ValidateListenAddr(addr); err != nil {
		return err
	}
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return err
	}
	defer ln.Close()
	if onBound != nil {
		onBound(ln.Addr().String())
	}
	s, err := New(cfg)
	if err != nil {
		return err
	}
	err = s.Serve(ctx, ln)
	if errors.Is(err, context.Canceled) || errors.Is(err, net.ErrClosed) {
		return nil
	}
	return err
}

// ValidateListenAddr requires a loopback host:port (port may be 0).
func ValidateListenAddr(addr string) error {
	host, port, err := net.SplitHostPort(addr)
	if err != nil {
		return fmt.Errorf("listen address: %w", err)
	}
	if port == "" {
		return errors.New("listen address must include a port")
	}
	ip := net.ParseIP(host)
	if ip == nil || !ip.IsLoopback() {
		return errors.New("listen address must be loopback (127.0.0.1 or ::1)")
	}
	return nil
}

func (s *Server) handlePing(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"status":  "ok",
		"version": s.version,
		"ts":      float64(time.Now().UnixNano()) / 1e9,
		"native_runtime": map[string]any{
			"requested": "native",
			"effective": "native",
			"ready":     true,
			"components": map[string]any{
				"go":  map[string]any{"ready": true, "reason": "serving"},
				"zig": map[string]any{"ready": false, "reason": "not-probed"},
			},
		},
	})
}

func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	uptime := formatUptime(time.Since(s.started))
	body := map[string]any{
		"status":              "ok",
		"version":             s.version,
		"uptime":              uptime,
		"gateway":             map[string]any{"running": false},
		"memory_entries":      0,
		"skills_count":        0,
		"sessions_count":      0,
		"chat_sessions_count": 0,
	}
	if s.connectGW != nil {
		h := s.connectGW.Health()
		body["connect"] = map[string]any{
			"serving":   h.Serving,
			"listening": h.Listening,
			"bind_host": h.BindHost,
			"bind_port": h.BindPort,
			"healing":   h.Healing,
			"crashes":   h.Crashes,
		}
	}
	// Authenticated tier may touch SQLite for chat session counts.
	if s.token != "" && requestAuthorized(r, s.token) && s.sessions != nil {
		if n, err := s.sessions.Count(); err == nil {
			body["chat_sessions_count"] = n
		}
	}
	writeJSON(w, http.StatusOK, body)
}

func (s *Server) handleTurnActive(w http.ResponseWriter, _ *http.Request) {
	active := s.claims != nil && s.claims.AnyActive()
	writeJSON(w, http.StatusOK, map[string]any{
		"status": "ok",
		"active": active,
	})
}

func formatUptime(d time.Duration) string {
	if d < 0 {
		d = 0
	}
	secs := int(d.Seconds())
	h := secs / 3600
	m := (secs % 3600) / 60
	s := secs % 60
	if h > 0 {
		return fmt.Sprintf("%dh%dm%ds", h, m, s)
	}
	if m > 0 {
		return fmt.Sprintf("%dm%ds", m, s)
	}
	return fmt.Sprintf("%ds", s)
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}
