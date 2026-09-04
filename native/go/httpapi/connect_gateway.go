package httpapi

import (
	"net"
	"net/http"

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

func (s *Server) handleConnect(w http.ResponseWriter, _ *http.Request) {
	if s.connectGW == nil {
		writeJSON(w, http.StatusOK, map[string]any{
			"serving":   false,
			"listening": false,
		})
		return
	}
	h := s.connectGW.Health()
	body := map[string]any{
		"crashes":       h.Crashes,
		"last_crash":    h.LastCrash,
		"last_crash_ts": h.LastCrashTS,
		"healing":       h.Healing,
		"serving":       h.Serving,
		"thread_alive":  h.ThreadAlive,
		"listening":     h.Listening,
		"bind_host":     h.BindHost,
		"bind_port":     h.BindPort,
	}
	writeJSON(w, http.StatusOK, body)
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
