package connect

import (
	"strings"
	"testing"
)

func TestDenyJobsNextFamilyIsHard403(t *testing.T) {
	def := DefaultPanes()
	cases := []struct {
		method, path, query string
	}{
		{"GET", "/api/computer/jobs/next", ""},
		{"GET", "/api/computer/jobs/next/", ""},
		{"GET", "/api/computer/jobs/next", "wait_ms=5000"},
		{"GET", "/api/computer/jobs/next", "wait_ms=0&driver=rust"},
		{"GET", "/api/computer/jobs/next", "driver=web"},
		{"GET", "/api/computer/jobs/next", "only=navigate"},
		{"GET", "/api/computer/jobs/next", "only=click&take=1"},
		{"GET", "/api/computer/jobs/next?wait_ms=2500&driver=rust", ""},
		{"POST", "/api/computer/jobs/abc/complete", ""},
		{"POST", "/api/computer/jobs/abc/cancel", ""},
		{"GET", "/api/computer/jobs/next", "take=1"},
		{"GET", "/API/COMPUTER/JOBS/NEXT", ""},
		{"GET", "/api/computer/jobs%2Fnext", ""},
		{"GET", "/%61pi/computer/jobs/next", ""},
		{"GET", "//api/computer/jobs/next", ""},
	}
	for _, tc := range cases {
		reason := ConnectForbidden(tc.method, tc.path, tc.query, def)
		if reason == "" {
			t.Fatalf("%s %s?%s: expected deny", tc.method, tc.path, tc.query)
		}
		if !strings.Contains(reason, "host-poller") && !strings.Contains(reason, "jobs") && reason != "path" {
			t.Fatalf("%s %s: got %q", tc.method, tc.path, reason)
		}
	}
}

func TestDenyHostPollerSiblingsAreHard403(t *testing.T) {
	def := DefaultPanes()
	cases := []struct {
		method, path string
	}{
		{"POST", "/api/computer/host/hello"},
		{"GET", "/api/computer/host/hello"},
		{"GET", "/api/computer/host/status"},
		{"GET", "/api/computer/host/other"},
		{"GET", "/api/computer/ui/command"},
		{"POST", "/api/computer/ui/command/ack"},
		{"POST", "/api/computer/a11y/push"},
	}
	for _, tc := range cases {
		reason := ConnectForbidden(tc.method, tc.path, "", def)
		if reason == "" || !strings.Contains(reason, "host-poller") {
			t.Fatalf("%s %s: got %q", tc.method, tc.path, reason)
		}
	}
}

func TestDenyLocalBootstrapFamilyIsHard403(t *testing.T) {
	def := DefaultPanes()
	cases := []struct {
		method, path, query string
	}{
		{"GET", "/api/auth/local-bootstrap", ""},
		{"POST", "/api/auth/local-bootstrap", ""},
		{"GET", "/api/auth/local-bootstrap/", ""},
		{"GET", "/api/auth/local-bootstrap", "x=1"},
		{"GET", "/api/auth/local-bootstrap?token=1", ""},
	}
	for _, tc := range cases {
		reason := ConnectForbidden(tc.method, tc.path, tc.query, def)
		if reason == "" || !strings.Contains(reason, "bootstrap") {
			t.Fatalf("%s %s: got %q", tc.method, tc.path, reason)
		}
	}
}

func TestDenyCapture403WhenPreviewPaneOff(t *testing.T) {
	off := NormalizePanes(map[string]bool{"computer_preview": false})
	if ConnectForbidden("POST", "/api/computer/capture", "", off) == "" {
		t.Fatal("expected preview deny")
	}
	on := NormalizePanes(map[string]bool{"computer_preview": true})
	if ConnectForbidden("POST", "/api/computer/capture", "", on) != "" {
		t.Fatal("expected allow when preview on")
	}
}

func TestSettingsBodySafeProviderOnlySwitchesProviderModel(t *testing.T) {
	if !SettingsBodySafeProvider([]byte(`{"llm_provider":"deepseek"}`)) {
		t.Fatal("llm_provider")
	}
	if !SettingsBodySafeProvider([]byte(`{"llm_model":"deepseek-v4-flash","llm_provider":"deepseek"}`)) {
		t.Fatal("provider+model")
	}
	if !SettingsBodySafeProvider([]byte(`{"provider":"openai","model":"gpt-4o"}`)) {
		t.Fatal("provider/model aliases")
	}
	for _, body := range []string{
		`{"llm_api_key":"x"}`,
		`{"connect_relay_url":"1.2.3.4:9"}`,
		`{"llm_provider":"deepseek","llm_api_key":"x"}`,
		``,
		`[]`,
		`{}`,
		`{"llm_provider":123}`,
	} {
		if SettingsBodySafeProvider([]byte(body)) {
			t.Fatalf("expected false for %q", body)
		}
	}
}

func TestDenySettingsWrite403UntilOptedIn(t *testing.T) {
	off := NormalizePanes(map[string]bool{"settings_write": false})
	if ConnectForbidden("PUT", "/api/settings", "", off) == "" {
		t.Fatal("PUT settings off")
	}
	if ConnectForbidden("PATCH", "/api/settings", "", off) == "" {
		t.Fatal("PATCH settings off")
	}
	if ConnectForbidden("GET", "/api/settings", "", off) != "" {
		t.Fatal("GET settings should allow")
	}
	if ConnectForbidden("GET", "/api/status", "", off) != "" {
		t.Fatal("GET status should allow")
	}
	on := NormalizePanes(map[string]bool{"settings_write": true})
	if ConnectForbidden("PUT", "/api/settings", "", on) != "" {
		t.Fatal("PUT settings on")
	}
}

func TestDenyRailsOffFailsClosedOnKnownPrefixes(t *testing.T) {
	off := NormalizePanes(map[string]bool{"rails": false})
	for _, path := range []string{
		"/api/files", "/api/files/search", "/api/workspace",
		"/api/scratch", "/api/terminal", "/api/browser",
	} {
		if got := ConnectForbidden("GET", path, "", off); got != "pane:rails" {
			t.Fatalf("%s: got %q", path, got)
		}
	}
	on := NormalizePanes(map[string]bool{"rails": true})
	if ConnectForbidden("GET", "/api/files", "", on) != "" {
		t.Fatal("rails on")
	}
}

func TestDenyApprovalsAndStopStayOn(t *testing.T) {
	raw := NormalizePanes(map[string]bool{
		"approvals": false,
		"sessions":  false,
		"chat":      false,
	})
	if !raw["approvals"] {
		t.Fatal("approvals must stay on")
	}
	if ConnectForbidden("GET", "/api/approvals", "", raw) != "" {
		t.Fatal("approvals list")
	}
	if ConnectForbidden("POST", "/api/approvals/abc/resolve", "", raw) != "" {
		t.Fatal("approvals resolve")
	}
	if ConnectForbidden("POST", "/api/sessions/sid/abort", "", raw) != "" {
		t.Fatal("session abort")
	}
}

func TestDenyAdjacentStatusPathIsNotJobsNext(t *testing.T) {
	def := DefaultPanes()
	if ConnectForbidden("GET", "/api/status", "", def) != "" {
		t.Fatal("status")
	}
	if ConnectForbidden("GET", "/api/computer/capture", "", def) != "" {
		t.Fatal("capture get")
	}
}

func TestDenyJobsNextTraversalAndAbsoluteURI(t *testing.T) {
	def := DefaultPanes()
	for _, path := range []string{
		"/api/foo/../../api/computer/jobs/next",
		"/api/computer/jobs/../jobs/next",
		"/api/%2e%2e/computer/jobs/next",
		"http://127.0.0.1:7400/api/computer/jobs/next",
		"//evil.example/api/computer/jobs/next",
		"/api/computer/jobs/next/../../jobs/next",
	} {
		if ConnectForbidden("GET", path, "", def) == "" {
			t.Fatalf("expected deny for %q", path)
		}
	}
}

func TestDenyConnectTraceMethodsDenied(t *testing.T) {
	def := DefaultPanes()
	if ConnectForbidden("CONNECT", "/api/status", "", def) == "" {
		t.Fatal("CONNECT")
	}
	if ConnectForbidden("TRACE", "/api/status", "", def) == "" {
		t.Fatal("TRACE")
	}
}

func TestSettingsWriteLockedBlocksConnectRetarget(t *testing.T) {
	on := NormalizePanes(map[string]bool{"settings_write": true})
	if ConnectForbidden("PUT", "/api/settings", "", on) != "" {
		t.Fatal("pane allows PUT")
	}
	if SettingsWriteLocked([]byte(`{"theme":"dark"}`)) != "" {
		t.Fatal("theme ok")
	}
	if SettingsWriteLocked([]byte(`{"connect_relay_url":"1.2.3.4:9"}`)) != "settings:locked" {
		t.Fatal("relay")
	}
	if SettingsWriteLocked([]byte(`{"llm_api_key":"x"}`)) != "settings:locked" {
		t.Fatal("api key")
	}
	if SettingsWriteLocked([]byte(`{"http_bootstrap":true}`)) != "settings:locked" {
		t.Fatal("bootstrap")
	}
}

func TestDenyConnectManagementIsHard403(t *testing.T) {
	def := DefaultPanes()
	cases := []struct {
		method, path string
	}{
		{"POST", "/api/connect/pair/start"},
		{"PUT", "/api/connect"},
		{"GET", "/api/connect"},
		{"GET", "/api/connect/addresses"},
		{"POST", "/api/connect/pause"},
		{"POST", "/api/connect/resume"},
		{"POST", "/api/connect/devices/abc/revoke"},
		{"GET", "/connect/pair/start"},
	}
	for _, tc := range cases {
		if got := ConnectForbidden(tc.method, tc.path, "", def); got != "connect:mgmt" {
			t.Fatalf("%s %s: got %q", tc.method, tc.path, got)
		}
	}
}

func TestDenyConnectMeAndPreviewAreNotMgmt(t *testing.T) {
	def := DefaultPanes()
	for _, path := range []string{
		"/connect/me", "/api/connect/me", "/connect/preview", "/api/connect/preview",
	} {
		if ConnectForbidden("GET", path, "", def) != "" {
			t.Fatalf("%s should allow", path)
		}
	}
}

func TestDenyWipeImportFamilyIsSettingsWrite(t *testing.T) {
	off := NormalizePanes(map[string]bool{"settings_write": false})
	if ConnectForbidden("POST", "/api/memory/persona-wipe", "", off) != "pane:settings_write" {
		t.Fatal("persona-wipe")
	}
	if ConnectForbidden("POST", "/api/custom/import", "", off) != "pane:settings_write" {
		t.Fatal("custom import")
	}
	if ConnectForbidden("GET", "/api/sessions", "", off) != "" {
		t.Fatal("sessions get")
	}
	on := NormalizePanes(map[string]bool{"settings_write": true})
	if ConnectForbidden("POST", "/api/custom/import", "", on) != "" {
		t.Fatal("import with pane on")
	}
}

func TestDenyCredentialFamily403UntilSettingsWrite(t *testing.T) {
	cases := []struct {
		method, path string
	}{
		{"POST", "/api/auth/xai/apikey"},
		{"POST", "/api/auth/xai/apikey/"},
		{"POST", "/API/AUTH/XAI/APIKEY"},
		{"POST", "/api/auth/xai/login"},
		{"DELETE", "/api/auth/xai"},
		{"GET", "/api/auth/xai"},
		{"POST", "/api/providers/custom"},
		{"DELETE", "/api/providers/custom/custom-foo"},
		{"POST", "/api/providers/probe"},
		{"GET", "/api/providers"},
		{"PUT", "/api/assistant/google/app"},
		{"POST", "/api/assistant/google/oauth/start"},
		{"DELETE", "/api/assistant/google"},
		{"GET", "/api/assistant/status"},
		{"POST", "/api/webhooks/whatsapp"},
		{"POST", "/api/webhook/ci"},
		{"POST", "/api/memory/persona-wipe"},
		{"POST", "/api/memory/import"},
		{"POST", "/api/sessions/import"},
		{"POST", "/api/skills/import"},
		{"POST", "/api/partner/identity/import"},
	}
	off := NormalizePanes(map[string]bool{"settings_write": false})
	on := NormalizePanes(map[string]bool{"settings_write": true})
	for _, tc := range cases {
		if got := ConnectForbidden(tc.method, tc.path, "", off); got != "pane:settings_write" {
			t.Fatalf("off %s %s: got %q", tc.method, tc.path, got)
		}
		if got := ConnectForbidden(tc.method, tc.path, "", on); got != "" {
			t.Fatalf("on %s %s: got %q", tc.method, tc.path, got)
		}
	}
}

func allPanesOn() map[string]bool {
	return NormalizePanes(map[string]bool{
		"live_ui":          true,
		"chat":             true,
		"approvals":        true,
		"sessions":         true,
		"rails":            true,
		"computer_preview": true,
		"settings_write":   true,
	})
}

func TestDenyConnectMgmtFamilyIsHard403(t *testing.T) {
	on := allPanesOn()
	cases := []struct {
		method, path, query string
	}{
		{"POST", "/api/connect/pair/start", ""},
		{"POST", "/api/connect/pair/start/", ""},
		{"GET", "/api/connect", ""},
		{"PUT", "/api/connect", ""},
		{"GET", "/api/connect/addresses", ""},
		{"POST", "/api/connect/pause", ""},
		{"POST", "/api/connect/resume", ""},
		{"POST", "/api/connect/devices/abc/revoke", ""},
		{"POST", "/connect/pair/start", ""},
		{"POST", "/connect/pause", ""},
		{"GET", "/connect/addresses", ""},
		{"GET", "/API/CONNECT/PAIR/START", ""},
		{"POST", "/api/connect%2Fpair%2Fstart", ""},
		{"POST", "/%61pi/connect/pair/start", ""},
		{"POST", "//api/connect/pair/start", ""},
		{"POST", "/api/foo/../../api/connect/pair/start", ""},
		{"POST", "/api/connect/pair/../pair/start", ""},
	}
	for _, tc := range cases {
		reason := ConnectForbidden(tc.method, tc.path, tc.query, on)
		if reason == "" {
			t.Fatalf("%s %s: expected deny", tc.method, tc.path)
		}
		if !strings.Contains(reason, "connect") && reason != "path" {
			t.Fatalf("%s %s: got %q", tc.method, tc.path, reason)
		}
	}
}

func TestDenyAdjacentConnectionPathIsUnknownFamily(t *testing.T) {
	def := DefaultPanes()
	if ConnectForbidden("GET", "/api/status", "", def) != "" {
		t.Fatal("status")
	}
	if got := ConnectForbidden("GET", "/api/connection", "", def); got != "unknown:family" {
		t.Fatalf("connection: got %q", got)
	}
	if ConnectForbidden("GET", "/api/sessions", "", def) != "" {
		t.Fatal("sessions")
	}
}

func TestDenyLocalBootstrapStaysHardDeniedWhenSettingsWriteOn(t *testing.T) {
	reason := ConnectForbidden("GET", "/api/auth/local-bootstrap", "", allPanesOn())
	if reason == "" || !strings.Contains(reason, "bootstrap") {
		t.Fatalf("got %q", reason)
	}
}

func TestDenyPairStartStaysHardDeniedWhenSettingsWriteOn(t *testing.T) {
	if got := ConnectForbidden("POST", "/api/connect/pair/start", "", allPanesOn()); got != "connect:mgmt" {
		t.Fatalf("got %q", got)
	}
}

func TestDenyServerKillPathsAreHardDenied(t *testing.T) {
	on := allPanesOn()
	cases := []struct {
		method, path string
	}{
		{"POST", "/api/shutdown"},
		{"POST", "/api/quit"},
		{"POST", "/api/restart"},
		{"POST", "/api/exit"},
		{"POST", "/api/app/command"},
		{"GET", "/api/app/command/restart"},
		{"POST", "/api/app/command/quit"},
		{"POST", "/API/SHUTDOWN"},
	}
	for _, tc := range cases {
		if got := ConnectForbidden(tc.method, tc.path, "", on); got != "server:kill" {
			t.Fatalf("%s %s: got %q", tc.method, tc.path, got)
		}
	}
}

func TestDenyPhoneStopIsTurnAbortAndStaysReachable(t *testing.T) {
	on := allPanesOn()
	if ConnectForbidden("POST", "/api/stop", "", on) != "" {
		t.Fatal("stop with all panes")
	}
	if ConnectForbidden("POST", "/api/stop", "", map[string]bool{}) != "" {
		t.Fatal("stop with empty panes")
	}
}

func TestSanitizeOriginPathRefusesAbsoluteAndTraversalJunk(t *testing.T) {
	if _, ok := SanitizeOriginPath("http://evil/api"); ok {
		t.Fatal("absolute")
	}
	if _, ok := SanitizeOriginPath("//evil/api"); ok {
		t.Fatal("protocol-relative")
	}
	if _, ok := SanitizeOriginPath(`/api\..\secret`); ok {
		t.Fatal("backslash")
	}
	got, ok := SanitizeOriginPath("/api/foo/../bar")
	if !ok || got != "/api/bar" {
		t.Fatalf("got %q ok=%v", got, ok)
	}
	got, ok = SanitizeOriginPath("/api/computer/jobs%2Fnext")
	if !ok || got != "/api/computer/jobs/next" {
		t.Fatalf("percent slash: %q ok=%v", got, ok)
	}
}

func TestPanesNormalizeAlwaysOnAndDefaults(t *testing.T) {
	raw := NormalizePanes(map[string]any{"approvals": false, "computer_preview": "yes"})
	if !raw["approvals"] {
		t.Fatal("approvals forced on")
	}
	if !raw["computer_preview"] {
		t.Fatal("string yes → true")
	}
	if !raw["chat"] {
		t.Fatal("default chat")
	}
	if raw["settings_write"] {
		t.Fatal("default settings_write off")
	}

	fromCfg := PanesFromConfig(map[string]any{
		"connect_panes": map[string]any{"rails": false},
	})
	if fromCfg["rails"] {
		t.Fatal("rails from config")
	}
	if !fromCfg["approvals"] {
		t.Fatal("approvals from config")
	}
	if PanesFromConfig(nil)["chat"] != true {
		t.Fatal("nil config defaults")
	}
}

func TestDenyUnknownFamilyFailClosed(t *testing.T) {
	def := DefaultPanes()
	for _, path := range []string{
		"/api/memory/notes",
		"/api/updates",
		"/api/claimidx",
		"/api/future-route",
	} {
		if got := ConnectForbidden("GET", path, "", def); got != "unknown:family" {
			t.Fatalf("%s: got %q", path, got)
		}
	}
}

func TestDenyChatPaneGatesHive(t *testing.T) {
	off := NormalizePanes(map[string]bool{"chat": false})
	if got := ConnectForbidden("POST", "/api/hive/spawn", "", off); got != "pane:chat" {
		t.Fatalf("got %q", got)
	}
	on := NormalizePanes(map[string]bool{"chat": true})
	if ConnectForbidden("POST", "/api/hive/spawn", "", on) != "" {
		t.Fatal("hive with chat on")
	}
}

func TestForbiddenProvidersGlanceWithoutSettingsWrite(t *testing.T) {
	off := NormalizePanes(map[string]bool{"settings_write": false})
	if ConnectForbidden("GET", "/api/providers/connected", "", off) != "" {
		t.Fatal("connected")
	}
	if ConnectForbidden("GET", "/api/providers/free", "", off) != "" {
		t.Fatal("free")
	}
}
