package connect

import (
	"context"
	"fmt"
	"net"
	"strings"
	"sync"
	"time"
)

// Default heal backoffs match Python _HEAL_DELAYS_S.
var defaultHealDelays = []time.Duration{
	1 * time.Second,
	3 * time.Second,
	8 * time.Second,
	15 * time.Second,
}

const maxHeals = 8

// GatewayHealth is the crash / self-heal / serving snapshot for the desktop panel.
type GatewayHealth struct {
	Crashes     int
	LastCrash   string
	LastCrashTS float64
	Healing     bool
	Serving     bool
	ThreadAlive bool
	Listening   bool
	BindHost    string
	BindPort    int
}

// GatewaySettings is the Connect slice of owner Settings.
type GatewaySettings struct {
	Enabled  bool
	Paused   bool
	Host     string
	Port     int
	Home     string
	APIKey   string
	Sidecar  int
	RelayURL string
	RDV      bool // connect_rdv_enabled; default true when unset at the call site
}

// Gateway owns the Connect listener lifecycle: start/stop, pause, health, self-heal.
// A nil ConnHandler uses NoiseConnHandler (Noise IK → allowlist → inner mux).
// Supervisors (relay/rdv) are started beside the listener when configured.
type Gateway struct {
	mu sync.Mutex

	listener *Listener
	handler  ConnHandler
	settings GatewaySettings

	stopCh      chan struct{}
	serving     bool
	healing     bool
	crashes     int
	lastCrash   string
	lastCrashTS float64

	healDelays []time.Duration
	now        func() time.Time
}

// NewGateway builds an idle gateway. A nil handler selects NoiseConnHandler at start.
func NewGateway(handler ConnHandler) *Gateway {
	return &Gateway{
		listener:   NewListener(),
		handler:    handler,
		healDelays: append([]time.Duration(nil), defaultHealDelays...),
		now:        time.Now,
		stopCh:     make(chan struct{}),
	}
}

// SetHandler replaces the per-connection handler (tests / late wire-up).
// Pass nil to restore the default Noise session path on the next MaybeStart.
func (g *Gateway) SetHandler(handler ConnHandler) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.handler = handler
}

// SessionConfig builds a SessionConfig from live Gateway settings.
// Listener is bound so BindDevice tracks sockets for pause/revoke.
func (g *Gateway) SessionConfig() SessionConfig {
	live := g.LiveConfig()
	home := live.Home
	sidecar := live.Sidecar
	if sidecar <= 0 {
		sidecar = 7400
	}
	return SessionConfig{
		Home:        home,
		APIKey:      live.APIKey,
		SidecarPort: sidecar,
		Listener:    g.listener,
		ShouldStop: func() bool {
			if IsPaused(home) {
				return true
			}
			h := g.Health()
			return !h.Serving && !h.Listening
		},
	}
}

// NoiseConnHandler returns the production ConnHandler: RunSession with live settings.
// Each accept re-reads Home / APIKey / Sidecar so ApplySettings applies without rebind.
func (g *Gateway) NoiseConnHandler() ConnHandler {
	return func(ctx context.Context, conn net.Conn) {
		_, _ = RunSession(ctx, conn, g.SessionConfig())
	}
}

// Health returns a snapshot for GET /api/connect.
func (g *Gateway) Health() GatewayHealth {
	g.mu.Lock()
	defer g.mu.Unlock()
	h := GatewayHealth{
		Crashes:     g.crashes,
		LastCrash:   g.lastCrash,
		LastCrashTS: g.lastCrashTS,
		Healing:     g.healing,
		Serving:     g.serving,
	}
	if host, port, ok := g.listener.ListeningAddr(); ok {
		h.Listening = true
		h.ThreadAlive = true
		h.BindHost = host
		h.BindPort = port
	}
	return h
}

// ListeningAddr returns the bound address when the listener is up.
func (g *Gateway) ListeningAddr() (host string, port int, ok bool) {
	return g.listener.ListeningAddr()
}

// MaybeStart starts the listener when Settings enable a chosen IPv4.
// Same bind is a no-op (live config still updates). Never touches :7400.
func (g *Gateway) MaybeStart(cfg GatewaySettings) error {
	g.mu.Lock()
	g.settings = cfg
	if !g.healing {
		g.crashes = 0
	}
	g.mu.Unlock()

	ok, host, port := EnabledChosen(GatewayConfig{
		Enabled: cfg.Enabled,
		Host:    cfg.Host,
		Port:    cfg.Port,
	})
	if !ok {
		return nil
	}

	if curHost, curPort, listening := g.listener.ListeningAddr(); listening {
		if curHost == host && (port == 0 || curPort == port) {
			return nil
		}
		_ = g.Stop()
	}

	g.mu.Lock()
	select {
	case <-g.stopCh:
		g.stopCh = make(chan struct{})
	default:
	}
	handler := g.handler
	g.mu.Unlock()
	if handler == nil {
		handler = g.NoiseConnHandler()
	}

	if err := g.listener.Start(host, port, handler); err != nil {
		return err
	}
	g.mu.Lock()
	g.serving = true
	g.mu.Unlock()
	return nil
}

// Stop closes the listener and drops sessions. Idempotent.
func (g *Gateway) Stop() error {
	g.mu.Lock()
	select {
	case <-g.stopCh:
	default:
		close(g.stopCh)
	}
	g.serving = false
	g.mu.Unlock()
	err := g.listener.Stop()
	g.listener.DropAllSessions()
	return err
}

// ApplySettings hot-applies pause / enable. Pause drops sockets; disable stops.
func (g *Gateway) ApplySettings(cfg GatewaySettings) error {
	g.mu.Lock()
	g.settings = cfg
	home := cfg.Home
	g.mu.Unlock()

	_ = SetPaused(cfg.Paused, home)
	if cfg.Paused {
		g.listener.DropAllSessions()
	}
	if !cfg.Enabled {
		return g.Stop()
	}
	return g.MaybeStart(cfg)
}

// NoteCrash records a crash and optionally schedules self-heal.
// Returns whether a heal was scheduled.
func (g *Gateway) NoteCrash(err error, heal func()) bool {
	if err == nil {
		return false
	}
	g.mu.Lock()
	g.crashes++
	g.lastCrash = fmt.Sprintf("%T: %v", err, err)
	if len(g.lastCrash) > 300 {
		g.lastCrash = g.lastCrash[:300]
	}
	g.lastCrashTS = float64(g.now().UnixNano()) / 1e9
	g.serving = false
	n := g.crashes
	g.mu.Unlock()

	if n > maxHeals || heal == nil {
		return false
	}
	delay := g.healDelays[min(n, len(g.healDelays))-1]
	g.mu.Lock()
	g.healing = true
	stopCh := g.stopCh
	g.mu.Unlock()

	go func() {
		timer := time.NewTimer(delay)
		defer timer.Stop()
		select {
		case <-stopCh:
			g.mu.Lock()
			g.healing = false
			g.mu.Unlock()
			return
		case <-timer.C:
		}
		heal()
		g.mu.Lock()
		g.healing = false
		g.mu.Unlock()
	}()
	return true
}

// LiveConfig returns a copy of the last applied settings.
func (g *Gateway) LiveConfig() GatewaySettings {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.settings
}

// SettingsFromMap reads the Connect keys from a loose settings map (API shape).
func SettingsFromMap(raw map[string]any) GatewaySettings {
	cfg := GatewaySettings{RDV: true, Sidecar: 7400, Port: DefaultBindPort}
	if raw == nil {
		return cfg
	}
	cfg.Enabled = asBool(raw["connect_enabled"])
	cfg.Paused = asBool(raw["connect_paused"])
	cfg.Host = strings.TrimSpace(settingsString(raw["connect_bind_host"]))
	cfg.Port = settingsInt(raw["connect_bind_port"], DefaultBindPort)
	cfg.RelayURL = strings.TrimSpace(settingsString(raw["connect_relay_url"]))
	if _, ok := raw["connect_rdv_enabled"]; ok {
		cfg.RDV = asBool(raw["connect_rdv_enabled"])
	}
	if v := strings.TrimSpace(settingsString(raw["connect_home"])); v != "" {
		cfg.Home = v
	}
	if v := strings.TrimSpace(settingsString(raw["api_key"])); v != "" {
		cfg.APIKey = v
	}
	if _, ok := raw["sidecar_port"]; ok {
		cfg.Sidecar = settingsInt(raw["sidecar_port"], 7400)
	}
	return cfg
}

func settingsString(v any) string {
	switch t := v.(type) {
	case string:
		return t
	default:
		if v == nil {
			return ""
		}
		return fmt.Sprint(v)
	}
}

func settingsInt(v any, fallback int) int {
	switch t := v.(type) {
	case int:
		return t
	case float64:
		return int(t)
	case string:
		var n int
		if _, err := fmt.Sscanf(strings.TrimSpace(t), "%d", &n); err == nil {
			return n
		}
	}
	return fallback
}
