package httpapi

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"strconv"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

// startConnectGateway builds/refreshes the Connect Gateway from on-disk settings.
// SidecarPort is the loopback API port this server is bound to (inner HTTP proxy).
func (s *Server) startConnectGateway(sidecarPort int) {
	if s == nil {
		return
	}
	if s.connectGW == nil {
		s.connectGW = connect.NewGateway(nil)
	}
	_ = s.connectGW.ApplySettings(s.connectSettings(sidecarPort))
}

func (s *Server) connectSettings(sidecarPort int) connect.GatewaySettings {
	home := ResolveHomeDir(s.homeDir)
	raw := LoadConfig(s.homeDir)
	gs := connect.SettingsFromMap(map[string]any(raw))
	gs.Home = home
	gs.APIKey = s.token
	if sidecarPort > 0 {
		gs.Sidecar = sidecarPort
	} else if gs.Sidecar <= 0 {
		gs.Sidecar = 7400
	}
	return gs
}

func (s *Server) stopConnectGateway() {
	if s == nil || s.connectGW == nil {
		return
	}
	_ = s.connectGW.Stop()
}

func (s *Server) refreshConnectAfterSettings() {
	if s == nil || s.connectGW == nil {
		return
	}
	_ = s.connectGW.ApplySettings(s.connectSettings(s.apiListenPort))
}

func (s *Server) ensureConnectGateway() *connect.Gateway {
	if s.connectGW == nil {
		s.connectGW = connect.NewGateway(nil)
	}
	return s.connectGW
}

func listenPort(ln net.Listener) int {
	if ln == nil {
		return 0
	}
	if tcp, ok := ln.Addr().(*net.TCPAddr); ok {
		return tcp.Port
	}
	return 0
}

// --- Connect management surface (Python routes/connect.py parity) ---

func refuseConnectProxy(w http.ResponseWriter, r *http.Request) bool {
	if strings.TrimSpace(r.Header.Get("X-Remedy-Connect-Hop")) != "" {
		writeJSON(w, http.StatusForbidden, map[string]string{
			"detail": "connect proxy cannot call connect management",
		})
		return true
	}
	return false
}

func requireLoopbackConnect(w http.ResponseWriter, r *http.Request) bool {
	if !clientIsLoopback(r) {
		writeJSON(w, http.StatusForbidden, map[string]string{"detail": "loopback only"})
		return false
	}
	if !hostHeaderIsLoopback(r) {
		writeJSON(w, http.StatusForbidden, map[string]string{
			"detail": "host not loopback (rebinding blocked)",
		})
		return false
	}
	return true
}

func hostHeaderIsLoopback(r *http.Request) bool {
	raw := strings.TrimSpace(strings.ToLower(r.Host))
	if raw == "" {
		return true
	}
	host := raw
	if strings.HasPrefix(raw, "[") {
		end := strings.IndexByte(raw, ']')
		if end > 0 {
			host = raw[1:end]
		}
	} else if i := strings.LastIndexByte(raw, ':'); i > 0 {
		// host:port (IPv4 / hostname). Keep bare host when no port.
		if _, err := strconv.Atoi(raw[i+1:]); err == nil {
			host = raw[:i]
		}
	}
	return loopbackHostName(host)
}

type connectUpdateBody struct {
	Enabled  *bool          `json:"enabled"`
	BindHost *string        `json:"bind_host"`
	BindPort *int           `json:"bind_port"`
	Paused   *bool          `json:"paused"`
	Panes    map[string]any `json:"panes"`
	RelayURL *string        `json:"relay_url"`
}

func (s *Server) connectSnapshot(includeLive bool) map[string]any {
	cfg := LoadConfig(s.homeDir)
	home := ResolveHomeDir(s.homeDir)
	port := cfgInt(cfg, "connect_bind_port", connect.DefaultBindPort)
	if port < 1 || port > 65535 {
		port = connect.DefaultBindPort
	}
	paused := cfgBool(cfg, "connect_paused", false) || connect.IsPaused(home)
	snap := map[string]any{
		"enabled":   cfgBool(cfg, "connect_enabled", false),
		"bind_host": strings.TrimSpace(cfgString(cfg, "connect_bind_host", "")),
		"bind_port": port,
		"paused":    paused,
		"panes":     connect.PanesFromConfig(map[string]any(cfg)),
		"relay_url": strings.TrimSpace(cfgString(cfg, "connect_relay_url", "")),
		"devices":   connect.DevicePublicMetaList(home),
		"listening": nil,
	}
	if !includeLive {
		return snap
	}
	gw := s.ensureConnectGateway()
	snap["listening"] = gw.ListeningTuple()
	snap["gateway"] = gw.HealthMap()
	return snap
}

func validateBindForEnable(host string) error {
	h := strings.TrimSpace(host)
	if h == "" || connect.IsWildcardBind(h) || !connect.IsChosenIPv4(h) {
		return fmt.Errorf("connect bind must be a chosen IPv4, not wildcard")
	}
	if _, err := connect.AssertChosenBind(h); err != nil {
		return fmt.Errorf("connect bind must be a chosen IPv4, not wildcard")
	}
	return nil
}

func (s *Server) handleGetConnect(w http.ResponseWriter, r *http.Request) {
	if refuseConnectProxy(w, r) {
		return
	}
	writeJSON(w, http.StatusOK, s.connectSnapshot(true))
}

func (s *Server) handlePutConnect(w http.ResponseWriter, r *http.Request) {
	if refuseConnectProxy(w, r) {
		return
	}
	var req connectUpdateBody
	dec := json.NewDecoder(r.Body)
	dec.UseNumber()
	if err := dec.Decode(&req); err != nil && err != io.EOF {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	cfg := LoadConfig(s.homeDir)
	enabled := cfgBool(cfg, "connect_enabled", false)
	if req.Enabled != nil {
		enabled = *req.Enabled
	}
	host := strings.TrimSpace(cfgString(cfg, "connect_bind_host", ""))
	if req.BindHost != nil {
		host = strings.TrimSpace(*req.BindHost)
	}
	if enabled {
		if err := validateBindForEnable(host); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"detail": err.Error()})
			return
		}
	}
	patch := map[string]any{}
	if req.Enabled != nil {
		patch["connect_enabled"] = *req.Enabled
	}
	if req.BindHost != nil {
		patch["connect_bind_host"] = host
	}
	if req.BindPort != nil {
		p := *req.BindPort
		if p < 1 || p > 65535 {
			writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "bind_port out of range"})
			return
		}
		patch["connect_bind_port"] = p
	}
	if req.Paused != nil {
		patch["connect_paused"] = *req.Paused
	}
	if req.Panes != nil {
		normalized := connect.NormalizePanes(req.Panes)
		panesAny := make(map[string]any, len(normalized))
		for k, v := range normalized {
			panesAny[k] = v
		}
		patch["connect_panes"] = panesAny
	}
	if req.RelayURL != nil {
		patch["connect_relay_url"] = strings.TrimSpace(*req.RelayURL)
	}
	if len(patch) == 0 {
		writeJSON(w, http.StatusOK, s.connectSnapshot(false))
		return
	}
	if _, err := s.applySettingsUpdate(patch); err != nil {
		if _, ok := err.(settingsValueError); ok {
			writeJSON(w, http.StatusBadRequest, map[string]string{"detail": err.Error()})
			return
		}
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	// applySettingsUpdate already refreshes the gateway; return PUT echo shape.
	writeJSON(w, http.StatusOK, s.connectSnapshot(false))
}

func (s *Server) handleConnectAddresses(w http.ResponseWriter, r *http.Request) {
	if refuseConnectProxy(w, r) {
		return
	}
	rows := connect.ListCandidateIPv4()
	if rows == nil {
		rows = []string{}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"addresses": rows,
		"defaults":  connect.DefaultPanes(),
	})
}

func (s *Server) handleConnectPairStart(w http.ResponseWriter, r *http.Request) {
	if refuseConnectProxy(w, r) {
		return
	}
	if !requireLoopbackConnect(w, r) {
		return
	}
	cfg := LoadConfig(s.homeDir)
	home := ResolveHomeDir(s.homeDir)
	host := strings.TrimSpace(cfgString(cfg, "connect_bind_host", ""))
	port := cfgInt(cfg, "connect_bind_port", connect.DefaultBindPort)
	if port < 1 || port > 65535 {
		port = connect.DefaultBindPort
	}
	host = connect.ReachableLANHost(host)
	if host == "" {
		cands := connect.PreferLANIPv4(connect.ListCandidateIPv4())
		if len(cands) > 0 {
			host = cands[0]
		}
	}
	if host == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"detail": "set connect_bind_host to a chosen IPv4 first",
		})
		return
	}
	v6 := ""
	if cfgBool(cfg, "connect_allow_ipv6", false) {
		if g6 := connect.ListCandidateIPv6(); len(g6) > 0 {
			v6 = fmt.Sprintf("[%s]:%d", g6[0], port)
		}
	}
	relay := strings.TrimSpace(cfgString(cfg, "connect_relay_url", ""))
	tsHost := connect.TailscaleIPv4()
	qr, err := connect.StartPair(connect.PairStartOpts{
		Loopback:  true,
		BindHost:  host,
		BindPort:  port,
		V6:        v6,
		Relay:     relay,
		Tailscale: tsHost,
		Home:      home,
	})
	if err != nil {
		msg := err.Error()
		if strings.Contains(strings.ToLower(msg), "loopback") {
			writeJSON(w, http.StatusForbidden, map[string]string{"detail": "loopback only"})
			return
		}
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": msg})
		return
	}
	qr = qr + "\nrdv=" + connect.RDVQRValue()
	var exp any
	for _, line := range strings.Split(qr, "\n") {
		if strings.HasPrefix(line, "exp=") {
			if n, err := strconv.ParseInt(strings.TrimSpace(line[4:]), 10, 64); err == nil {
				exp = n
			}
			break
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"qr": qr, "exp": exp})
}

func (s *Server) handleConnectPause(w http.ResponseWriter, r *http.Request) {
	if refuseConnectProxy(w, r) {
		return
	}
	home := ResolveHomeDir(s.homeDir)
	_ = connect.SetPaused(true, home)
	if _, err := s.applySettingsUpdate(map[string]any{"connect_paused": true}); err != nil {
		// State flag already set; still report paused.
		_ = err
	}
	if gw := s.connectGW; gw != nil {
		gw.DropAllSessions()
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "paused": true})
}

func (s *Server) handleConnectResume(w http.ResponseWriter, r *http.Request) {
	if refuseConnectProxy(w, r) {
		return
	}
	home := ResolveHomeDir(s.homeDir)
	_ = connect.SetPaused(false, home)
	if _, err := s.applySettingsUpdate(map[string]any{"connect_paused": false}); err != nil {
		_ = err
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "paused": false})
}

func (s *Server) handleConnectRevoke(w http.ResponseWriter, r *http.Request) {
	if refuseConnectProxy(w, r) {
		return
	}
	deviceID := strings.TrimSpace(r.PathValue("id"))
	if deviceID == "" {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "device not found"})
		return
	}
	home := ResolveHomeDir(s.homeDir)
	rec, err := connect.GetDevice(deviceID, home)
	if err != nil || rec == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "device not found"})
		return
	}
	saved, err := connect.RevokeDevice(deviceID, home)
	if err != nil || saved == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "device not found"})
		return
	}
	if gw := s.connectGW; gw != nil {
		gw.DropSessionsForDevice(saved.ID)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":      true,
		"id":      saved.ID,
		"revoked": true,
	})
}
