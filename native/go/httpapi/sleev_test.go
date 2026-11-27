package httpapi

import "testing"

func TestSleevRouteOffAndLocalAreUntouched(t *testing.T) {
	base := "https://api.x.ai/v1"
	if got, h := sleevRoute(ConfigMap{}, "xai", base); got != base || h != nil {
		t.Fatalf("off: %q %v", got, h)
	}
	on := ConfigMap{"sleev_enabled": true, "sleev_gateway_url": "http://127.0.0.1:17321"}
	for prov, local := range map[string]string{"ollama": "http://127.0.0.1:11434/v1", "custom": "http://127.0.0.1:5001/v1"} {
		if got, h := sleevRoute(on, prov, local); got != local || h != nil {
			t.Fatalf("%s: a model on this computer was routed: %q %v", prov, got, h)
		}
	}
}

func TestSleevRouteNamesTheUpstream(t *testing.T) {
	on := ConfigMap{"sleev_enabled": true, "sleev_gateway_url": "http://127.0.0.1:17321/v1/"}
	gw, h := sleevRoute(on, "anthropic", "https://api.anthropic.com/v1")
	if gw != "http://127.0.0.1:17321" || h["sleev-harness"] != "remedy" || h["sleev-provider"] != "anthropic" || h["sleev-base-url"] != "" {
		t.Fatalf("anthropic: %q %v", gw, h)
	}
	gw, h = sleevRoute(on, "xai", "https://api.x.ai/v1/")
	if gw != "http://127.0.0.1:17321" || h["sleev-base-url"] != "https://api.x.ai/v1" || h["sleev-provider"] != "" {
		t.Fatalf("xai: %q %v", gw, h)
	}
	// A base saved as the gateway itself forwards to the provider's own address.
	_, h = sleevRoute(on, "deepseek", "http://127.0.0.1:17321")
	if h["sleev-base-url"] != "https://api.deepseek.com/v1" {
		t.Fatalf("deepseek: %v", h)
	}
}

// The gateway gets the provider key: one on another machine needs the owner's say.
func TestSleevRemoteGatewayNeedsOptIn(t *testing.T) {
	base := "https://api.x.ai/v1"
	cfg := ConfigMap{"sleev_enabled": true, "sleev_gateway_url": "http://gateway.local:17321"}
	if got, h := sleevRoute(cfg, "xai", base); got != base || h != nil {
		t.Fatalf("routed to another machine without opt-in: %q %v", got, h)
	}
	cfg["sleev_allow_remote_gateway"] = true
	if got, _ := sleevRoute(cfg, "xai", base); got != "http://gateway.local:17321" {
		t.Fatalf("opt-in ignored: %q", got)
	}
}
