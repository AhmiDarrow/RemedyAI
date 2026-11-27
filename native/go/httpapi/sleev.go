package httpapi

import (
	"encoding/json"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// Sleev (https://sleev.ai) is a local gateway that shortens stale conversation
// history before a request reaches the provider. With "Use Sleev" on, a cloud
// provider's requests go to the gateway instead of the provider, with headers
// that tell it where to forward them. Keys and models stay the same.
//
//   - sleev-harness: remedy on every routed request
//   - sleev-provider: <id> for the providers the gateway knows by name
//   - sleev-base-url: <provider API base> for every other provider
//
// The gateway receives the provider key with each request, so only a gateway
// on this computer is used unless sleev_allow_remote_gateway is on.

const (
	sleevHarness        = "remedy"
	sleevDefaultGateway = "http://127.0.0.1:17321"
)

var (
	sleevBuiltinProviders = map[string]string{"anthropic": "anthropic", "openai": "openai", "google": "google"}
	// Nothing to save on a model that runs here or costs nothing.
	sleevLocalOnly = map[string]bool{"ollama": true, "llamacpp": true, "rmb": true, "demo": true}
)

// sleevLoopback is true only for a literal loopback host. A *.local name is
// another machine and gets no key without the owner's say.
func sleevLoopback(raw string) bool {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil {
		return false
	}
	switch strings.ToLower(u.Hostname()) {
	case "localhost", "127.0.0.1", "0.0.0.0", "::1":
		return true
	}
	return false
}

func sleevPort(raw string) string {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil {
		return ""
	}
	if p := u.Port(); p != "" {
		return p
	}
	if u.Scheme == "https" {
		return "443"
	}
	return "80"
}

// sleevGatewayRoot strips a trailing slash and /v1 so paths append cleanly.
func sleevGatewayRoot(raw string) string {
	u := strings.TrimRight(strings.TrimSpace(raw), "/")
	if strings.HasSuffix(strings.ToLower(u), "/v1") {
		u = strings.TrimRight(u[:len(u)-3], "/")
	}
	return u
}

// sleevInstallGateway reads the address from Sleev's own config file, when
// Sleev is installed.
func sleevInstallGateway() string {
	var dirs []string
	if v := os.Getenv("APPDATA"); v != "" {
		dirs = append(dirs, filepath.Join(v, "sleev"))
	}
	if v := os.Getenv("XDG_CONFIG_HOME"); v != "" {
		dirs = append(dirs, filepath.Join(v, "sleev"))
	}
	if home, err := os.UserHomeDir(); err == nil && home != "" {
		dirs = append(dirs, filepath.Join(home, ".config", "sleev"))
	}
	for _, dir := range dirs {
		data, err := os.ReadFile(filepath.Join(dir, "config.json"))
		if err != nil {
			continue
		}
		var conf struct {
			Proxy struct {
				Host string `json:"host"`
				Port any    `json:"port"`
			} `json:"proxy"`
		}
		if json.Unmarshal(data, &conf) != nil {
			continue
		}
		host := strings.TrimSpace(conf.Proxy.Host)
		// A bind-all address is reached through loopback.
		if host == "" || host == "0.0.0.0" || host == "::" || host == "[::]" {
			host = "127.0.0.1"
		}
		port := anyInt(conf.Proxy.Port, 0)
		if port <= 0 {
			return sleevDefaultGateway
		}
		return "http://" + host + ":" + strconv.Itoa(port)
	}
	return ""
}

// sleevGateway is the gateway to use, or "" when the configured one is on
// another machine and that has not been allowed.
func sleevGateway(cfg ConfigMap) string {
	gw := sleevGatewayRoot(cfgString(cfg, "sleev_gateway_url", ""))
	if gw == "" {
		gw = sleevGatewayRoot(sleevInstallGateway())
	}
	if gw == "" {
		gw = sleevDefaultGateway
	}
	if !sleevLoopback(gw) && !cfgBool(cfg, "sleev_allow_remote_gateway", false) {
		return ""
	}
	return gw
}

// sleevRoute returns where a provider's requests go and the extra headers
// they carry. With Sleev off, or for a local model, it returns base and nil.
func sleevRoute(cfg ConfigMap, provider, base string) (string, map[string]string) {
	prov := strings.ToLower(strings.TrimSpace(provider))
	if !cfgBool(cfg, "sleev_enabled", false) || prov == "" || sleevLocalOnly[prov] {
		return base, nil
	}
	gw := sleevGateway(cfg)
	if gw == "" {
		return base, nil
	}
	atGateway := sleevLoopback(base) && sleevPort(base) == sleevPort(gw)
	// Another program on this computer (a local model server) is not a cloud
	// provider: it is left alone.
	if sleevLoopback(base) && !atGateway {
		return base, nil
	}
	headers := map[string]string{"sleev-harness": sleevHarness}
	if id, ok := sleevBuiltinProviders[prov]; ok {
		headers["sleev-provider"] = id
		return gw, headers
	}
	upstream := strings.TrimRight(strings.TrimSpace(base), "/")
	if upstream == "" || atGateway {
		// The saved address is the gateway itself: forward to the provider's own.
		upstream = strings.TrimRight(strings.TrimSpace(providerDefaults[prov].baseURL), "/")
	}
	if upstream == "" {
		return base, nil
	}
	headers["sleev-base-url"] = upstream
	return gw, headers
}
