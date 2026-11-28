package httpapi

import (
	"encoding/json"
	"net/http"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// The owner's side of connectors: list, add or change, remove, reconnect.
// These are owner routes (the local API token); Remedy has no tool that adds a
// connector or reads a token.
//
//	GET    /api/mcp/servers
//	POST   /api/mcp/servers                  {name, command|url, args, token, token_env, approval, enabled}
//	DELETE /api/mcp/servers/{name}
//	POST   /api/mcp/servers/{name}/reconnect

func (s *Server) handleMCPList(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"servers": s.mcp.status()})
}

// saveMCPServers rewrites mcp_servers in config.toml.
func (s *Server) saveMCPServers(list []mcpServerConfig) error {
	cfg := LoadConfig(s.homeDir)
	if cfg == nil {
		cfg = ConfigMap{}
	}
	rows := make([]any, 0, len(list))
	for _, c := range list {
		rows = append(rows, c.table())
	}
	cfg["mcp_servers"] = rows
	path := FindConfigPath(s.homeDir)
	if path == "" {
		path = DefaultConfigPath(s.homeDir)
	}
	return WriteConfig(path, cfg)
}

func (s *Server) handleMCPUpsert(w http.ResponseWriter, r *http.Request) {
	if s.mcp == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": "connectors are not available in this runtime"})
		return
	}
	var body struct {
		Name     string   `json:"name"`
		Command  string   `json:"command"`
		Args     []string `json:"args"`
		URL      string   `json:"url"`
		Token    *string  `json:"token"`
		TokenEnv string   `json:"token_env"`
		Approval string   `json:"approval"`
		Enabled  *bool    `json:"enabled"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "JSON body required"})
		return
	}
	c := mcpServerConfig{
		Name: strings.ToLower(strings.TrimSpace(body.Name)), Command: strings.TrimSpace(body.Command),
		Args: body.Args, URL: strings.TrimSpace(body.URL), TokenEnv: strings.TrimSpace(body.TokenEnv),
		Enabled: true, Approval: "ask",
	}
	if body.Enabled != nil {
		c.Enabled = *body.Enabled
	}
	if strings.EqualFold(strings.TrimSpace(body.Approval), "auto") {
		c.Approval = "auto"
	}
	switch {
	case !mcpNameRE.MatchString(c.Name):
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "Give the connector a short name: lowercase letters, digits, - or _ (for example github)."})
		return
	case c.Command == "" && c.URL == "":
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "A connector needs a command to run or a URL to connect to."})
		return
	case c.Command != "" && c.URL != "":
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "Give a command or a URL, not both."})
		return
	}
	home := ResolveHomeDir(s.homeDir)
	// A token that is sent replaces the stored one; an empty one removes it;
	// leaving it out keeps what is stored.
	if body.Token != nil {
		if err := secret.SetProviderSecret(home, mcpSecretKey(c.Name), strings.TrimSpace(*body.Token)); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": "could not store the token"})
			return
		}
	}
	list := loadMCPServers(LoadConfig(s.homeDir))
	replaced := false
	for i := range list {
		if list[i].Name == c.Name {
			list[i], replaced = c, true
		}
	}
	if !replaced {
		list = append(list, c)
	}
	if err := s.saveMCPServers(list); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	s.mcp.set(c)
	writeJSON(w, http.StatusOK, map[string]any{"servers": s.mcp.status()})
}

func (s *Server) handleMCPDelete(w http.ResponseWriter, r *http.Request) {
	name := strings.ToLower(strings.TrimSpace(r.PathValue("name")))
	list := loadMCPServers(LoadConfig(s.homeDir))
	kept := list[:0]
	for _, c := range list {
		if c.Name != name {
			kept = append(kept, c)
		}
	}
	if err := s.saveMCPServers(kept); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	// The token goes with the connector.
	_ = secret.SetProviderSecret(ResolveHomeDir(s.homeDir), mcpSecretKey(name), "")
	s.mcp.remove(name)
	writeJSON(w, http.StatusOK, map[string]any{"servers": s.mcp.status()})
}

func (s *Server) handleMCPReconnect(w http.ResponseWriter, r *http.Request) {
	name := strings.ToLower(strings.TrimSpace(r.PathValue("name")))
	for _, c := range loadMCPServers(LoadConfig(s.homeDir)) {
		if c.Name == name {
			s.mcp.set(c)
			writeJSON(w, http.StatusOK, map[string]any{"servers": s.mcp.status()})
			return
		}
	}
	writeJSON(w, http.StatusNotFound, map[string]string{"detail": "no connector with that name"})
}
