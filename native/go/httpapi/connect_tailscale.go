package httpapi

import (
	"net/http"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

// Injectable for tests; production points at connect helpers.
var (
	tailscaleStatus = connect.GetTailscaleStatus
	tailscaleEnsure = connect.EnsureTailscale
	tailscaleLogin  = connect.StartTailscaleLogin
)

func (s *Server) handleConnectTailscaleStatus(w http.ResponseWriter, r *http.Request) {
	if refuseConnectProxy(w, r) {
		return
	}
	if !requireLoopbackConnect(w, r) {
		return
	}
	writeJSON(w, http.StatusOK, tailscaleStatus())
}

func (s *Server) handleConnectTailscaleInstall(w http.ResponseWriter, r *http.Request) {
	if refuseConnectProxy(w, r) {
		return
	}
	if !requireLoopbackConnect(w, r) {
		return
	}
	act := tailscaleEnsure("")
	if act.Status == "error" {
		writeJSON(w, http.StatusInternalServerError, map[string]string{
			"detail": act.Message,
		})
		return
	}
	writeJSON(w, http.StatusOK, act)
}

func (s *Server) handleConnectTailscaleLogin(w http.ResponseWriter, r *http.Request) {
	if refuseConnectProxy(w, r) {
		return
	}
	if !requireLoopbackConnect(w, r) {
		return
	}
	writeJSON(w, http.StatusOK, tailscaleLogin())
}
