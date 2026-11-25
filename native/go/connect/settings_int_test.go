package connect

import "testing"

func TestAPortFromConfigTomlIsHonoured(t *testing.T) {
	// TOML numbers decode to int64.
	got := SettingsFromMap(map[string]any{"connect_enabled": true, "connect_bind_host": "127.0.0.1", "connect_bind_port": int64(17481)})
	if got.Port != 17481 {
		t.Fatalf("port = %d, want the configured 17481", got.Port)
	}
	if d := SettingsFromMap(map[string]any{}); d.Port != DefaultBindPort {
		t.Fatalf("default port = %d", d.Port)
	}
}
