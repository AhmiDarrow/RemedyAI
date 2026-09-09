package connect

import (
	"context"
	"fmt"
	"log"
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
	RDV      bool // connect_rdv_enabled; opt-in (public MQTT brokers)
}

// Gateway owns the Connect listener lifecycle: start/stop, pause, health, self-heal.
// A nil ConnHandler uses NoiseConnHandler (Noise IK → allowlist → inner mux).
// Relay and public-broker rdv supervisors run beside the listener when configured;
// dialed pipes use the same Noise session path as accepted TCP. mDNS advertises
// the bound chosen IPv4 (host-pub hash only) for same-L2 discovery.
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

	supMu     sync.Mutex // serializes refreshSupervisors / stopSupervisors
	supCancel context.CancelFunc
	supWG     sync.WaitGroup
	rdvNotice sync.Once

	mdnsStop func()
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

// supervisorHandler is the shared Noise path for relay / rdv dialed pipes.
func (g *Gateway) supervisorHandler() RelayConnHandler {
	return func(ctx context.Context, conn net.Conn) {
		_, _ = RunSession(ctx, conn, g.SessionConfig())
	}
}

// supervisorSession is supervisorHandler with the auth outcome kept, so the
// rendezvous dialer can budget refused handshakes instead of treating the
// first one as a dead broker.
func (g *Gateway) supervisorSession() func(context.Context, net.Conn) error {
	return func(ctx context.Context, conn net.Conn) error {
		_, err := RunSession(ctx, conn, g.SessionConfig())
		return err
	}
}

// stopSupervisors cancels relay/rdv dialers and waits for them to exit.
func (g *Gateway) stopSupervisors() {
	g.supMu.Lock()
	defer g.supMu.Unlock()
	g.stopSupervisorsLocked()
}

func (g *Gateway) stopSupervisorsLocked() {
	cancel := g.supCancel
	g.supCancel = nil
	if cancel != nil {
		cancel()
	}
	g.supWG.Wait()
}

// refreshSupervisors restarts relay/rdv dialers from live settings.
// No-op when not serving. Safe to call on same-bind ApplySettings.
func (g *Gateway) refreshSupervisors() {
	g.supMu.Lock()
	defer g.supMu.Unlock()

	g.mu.Lock()
	serving := g.serving
	live := g.settings
	g.mu.Unlock()
	if !serving {
		return
	}
	g.stopSupervisorsLocked()

	ctx, cancel := context.WithCancel(context.Background())
	g.supCancel = cancel

	handler := g.supervisorHandler()
	if url, err := RelayConfigured(live.RelayURL); err == nil && url != "" {
		g.supWG.Add(1)
		go func() {
			defer g.supWG.Done()
			_ = RunRelaySupervisor(ctx, RelaySupervisorOpts{
				URL:     live.RelayURL,
				Home:    live.Home,
				Handler: handler,
			})
		}()
	}
	if live.RDV {
		log.Printf("connect: rendezvous ON — this machine holds a topic on public MQTT brokers (%s) "+
			"so the paired phone can reach it off-LAN. Ids rotate every %ds. "+
			"Set connect_rdv_enabled = false to stop.", RDVQRValue(), RDVBucketSeconds)
		session := g.supervisorSession()
		g.supWG.Add(1)
		go func() {
			defer g.supWG.Done()
			_ = RunRDVSupervisor(ctx, RDVSupervisorOpts{
				Home:    live.Home,
				Session: session,
				Enabled: true,
			})
		}()
		return
	}
	g.rdvNotice.Do(func() {
		log.Printf("connect: rendezvous OFF (default) — the phone reaches this machine over LAN, " +
			"Tailscale or a relay you choose. Public-broker rendezvous is opt-in: " +
			"set connect_rdv_enabled = true if the phone must find it from anywhere.")
	})
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

// DropAllSessions closes every live Connect socket (pause). :7400 untouched.
func (g *Gateway) DropAllSessions() {
	if g == nil || g.listener == nil {
		return
	}
	g.listener.DropAllSessions()
}

// DropSessionsForDevice closes sockets for one paired device. :7400 untouched.
func (g *Gateway) DropSessionsForDevice(deviceID string) {
	if g == nil || g.listener == nil {
		return
	}
	g.listener.DropSessionsForDevice(deviceID)
}

// ListeningTuple returns [host, port] for JSON, or nil when not listening.
func (g *Gateway) ListeningTuple() any {
	if g == nil {
		return nil
	}
	host, port, ok := g.ListeningAddr()
	if !ok {
		return nil
	}
	return []any{host, port}
}

// HealthMap is the nested gateway object for GET /api/connect.
func (g *Gateway) HealthMap() map[string]any {
	if g == nil {
		return map[string]any{
			"crashes":       0,
			"last_crash":    "",
			"last_crash_ts": 0.0,
			"healing":       false,
			"serving":       false,
			"thread_alive":  false,
			"listening":     nil,
		}
	}
	h := g.Health()
	return map[string]any{
		"crashes":       h.Crashes,
		"last_crash":    h.LastCrash,
		"last_crash_ts": h.LastCrashTS,
		"healing":       h.Healing,
		"serving":       h.Serving,
		"thread_alive":  h.ThreadAlive,
		"listening":     g.ListeningTuple(),
	}
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
			// Same bind: panes/relay/rdv come from live settings; refresh dialers.
			g.refreshSupervisors()
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
	g.startMDNS()
	g.refreshSupervisors()
	return nil
}

// stopMDNS stops the mDNS advertiser if running.
func (g *Gateway) stopMDNS() {
	g.mu.Lock()
	stop := g.mdnsStop
	g.mdnsStop = nil
	g.mu.Unlock()
	if stop != nil {
		stop()
	}
}

// startMDNS advertises the live bind with the host static public key hash.
// Best-effort: bind/NIC failures leave Connect serving without multicast.
func (g *Gateway) startMDNS() {
	g.stopMDNS()
	host, port, ok := g.listener.ListeningAddr()
	if !ok {
		return
	}
	home := g.LiveConfig().Home
	kp, err := LoadOrCreateHostKeyPair(home)
	if err != nil {
		return
	}
	stop, err := StartAdvertiser(host, port, kp.Public)
	if err != nil {
		return
	}
	g.mu.Lock()
	g.mdnsStop = stop
	g.mu.Unlock()
}

// Stop closes mDNS, supervisors, the listener, and drops sessions. Idempotent.
func (g *Gateway) Stop() error {
	g.stopMDNS()
	g.stopSupervisors()
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

// SettingsFromMap reads the Connect keys from a loose settings map.
//
// Rendezvous defaults OFF: it publishes this machine on third-party public
// MQTT brokers, which is a reasonable thing to opt into and a poor thing to
// inherit. refreshSupervisors logs which way it resolved so the owner is not
// left guessing why the phone cannot find the PC off-LAN.
func SettingsFromMap(raw map[string]any) GatewaySettings {
	cfg := GatewaySettings{RDV: false, Sidecar: 7400, Port: DefaultBindPort}
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
