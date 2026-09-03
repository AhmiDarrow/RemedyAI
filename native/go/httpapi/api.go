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
	"time"
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
}

// Server is the Phase-4 first-slice HTTP API.
type Server struct {
	started time.Time
	version string
	token   string
	mux     *http.ServeMux
}

// New builds a server with ping/status/turn-active routes registered.
func New(cfg Config) *Server {
	version := cfg.Version
	if version == "" {
		version = Version
	}
	token := cfg.Token
	if token == "" {
		token = ResolveToken(cfg.HomeDir)
	}
	s := &Server{
		started: time.Now(),
		version: version,
		token:   token,
		mux:     http.NewServeMux(),
	}
	s.mux.HandleFunc("GET /api/ping", s.handlePing)
	s.mux.HandleFunc("GET /api/status", s.handleStatus)
	s.mux.HandleFunc("GET /api/turn-active", s.handleTurnActive)
	return s
}

// Handler returns the CORS + auth wrapped mux.
func (s *Server) Handler() http.Handler {
	return withCORS(s.withAuth(s.mux))
}

// Serve serves until the listener closes or ctx is canceled.
func (s *Server) Serve(ctx context.Context, ln net.Listener) error {
	s.started = time.Now()
	httpServer := &http.Server{Handler: s.Handler()}
	errCh := make(chan error, 1)
	go func() {
		errCh <- httpServer.Serve(ln)
	}()
	select {
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = httpServer.Shutdown(shutdownCtx)
		err := <-errCh
		if err == nil || errors.Is(err, http.ErrServerClosed) {
			return ctx.Err()
		}
		return err
	case err := <-errCh:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return err
	}
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
	s := New(cfg)
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

func (s *Server) handleStatus(w http.ResponseWriter, _ *http.Request) {
	// Unauthenticated tier: no DB, gateway not running, counts zero.
	writeJSON(w, http.StatusOK, map[string]any{
		"status":              "ok",
		"version":             s.version,
		"uptime":              formatUptime(time.Since(s.started)),
		"gateway":             map[string]any{"running": false},
		"memory_entries":      0,
		"skills_count":        0,
		"sessions_count":      0,
		"chat_sessions_count": 0,
	})
}

func (s *Server) handleTurnActive(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"status": "ok",
		"active": false,
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
