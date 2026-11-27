package cli

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"math"
	"os"
	"os/signal"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/httpapi"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// `remedy config`, `remedy settings` and `remedy auth` (Python
// interfaces/cli/cmd_settings.py). Settings go through the runtime's own
// apply path, keys through its secret store, xAI through its device-code flow.

// Seams the tests replace: no network, no browser, no real clock.
var (
	cfgProjectSeed = cfgEnsureNewProjectSeed
	cfgXaiStart    = httpapi.CLIXaiStartLogin
	cfgXaiStatus   = httpapi.CLIXaiLoginStatus
	cfgStoreKey    = httpapi.CLIStoreProviderKey
	cfgOpenURL     = openInBrowser
	cfgSleep       = time.Sleep
	cfgNow         = time.Now
)

// ---------------------------------------------------------------- config

func cmdConfig(c *Ctx) int {
	switch c.P.Sub(1) {
	case "init":
		path, err := cfgCreateDefault(c.Home)
		if err != nil {
			c.Errorf("Config init failed: %v\n", err)
			return 1
		}
		c.Printf("Config created: %s\n", path)
	case "show":
		resolved := httpapi.CLIResolveConfig(c.Home, os.Environ())
		return cfgPrintJSON(c, cfgRedact(resolved, 0))
	case "path":
		path := filepath.Join(c.Home, "config.toml")
		if st, err := os.Stat(path); err == nil && !st.IsDir() {
			c.Println(path)
		} else {
			c.Printf("No config found at %s\n", path)
			c.Println("Run 'remedy config init' to create one.")
		}
	}
	return 0
}

// cfgCreateDefault writes the first-run config.toml (Python
// create_default_config). An existing file is never touched.
func cfgCreateDefault(home string) (string, error) {
	if err := os.MkdirAll(home, 0o700); err != nil {
		return "", err
	}
	path := filepath.Join(home, "config.toml")
	if _, err := os.Lstat(path); err == nil {
		return path, nil
	}
	seed := ""
	if dir, err := cfgProjectSeed(); err == nil {
		seed = dir
	}
	if err := secret.WriteFileAtomic(path, []byte(cfgDefaultConfig(home, seed)), 0o600); err != nil {
		return "", err
	}
	httpapi.InvalidateConfigCache()
	return path, nil
}

// cfgDefaultConfig is Python generate_default_config.
func cfgDefaultConfig(home, firstRunProject string) string {
	proj := `# project_path = ""  # empty = no default; New Session = root (full access)`
	scope := `# access_scope = "project"   # project | home | full | untrusted`
	if firstRunProject != "" {
		proj = `project_path = "` + filepath.ToSlash(firstRunProject) + `"`
		scope = `access_scope = "project"   # first-run sandbox; change to full when ready`
	}
	return strings.NewReplacer(
		"{HOME}", filepath.ToSlash(home),
		"{MEMDB}", filepath.ToSlash(filepath.Join(home, "memory.db")),
		"{PROJ_LINE}", proj,
		"{SCOPE_LINE}", scope,
		"{BT}", "`",
	).Replace(cfgDefaultTemplate)
}

// cfgEnsureNewProjectSeed makes ~/Documents/Remedy Projects/New Project, the
// first-run sandbox folder (Python workspace.ensure_new_project_seed).
func cfgEnsureNewProjectSeed() (string, error) {
	uh, err := os.UserHomeDir()
	if err != nil || uh == "" {
		return "", fmt.Errorf("no user home")
	}
	dir := filepath.Join(uh, "Documents", "Remedy Projects", "New Project")
	if abs, err := filepath.Abs(dir); err == nil {
		dir = abs
	}
	if st, err := os.Stat(dir); err == nil {
		if !st.IsDir() {
			return "", fmt.Errorf("project path is not a directory: %s", dir)
		}
	} else if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	if real, err := filepath.EvalSymlinks(dir); err == nil {
		dir = real
	}
	return dir, nil
}

// cfgSecretKey matches key names whose values `config show` never prints.
var cfgSecretKey = regexp.MustCompile(`(?i)(api[_-]?key|token|secret|password|passwd|authorization|private[_-]?key|bot[_-]?token)$`)

// cfgRedact is Python util.redact_cli_mapping.
func cfgRedact(obj any, depth int) any {
	if depth > 8 {
		return "[redacted-depth]"
	}
	switch t := obj.(type) {
	case httpapi.ConfigMap:
		return cfgRedact(map[string]any(t), depth)
	case map[string]any:
		out := make(map[string]any, len(t))
		for k, v := range t {
			kl := strings.ReplaceAll(strings.ToLower(k), "-", "_")
			if cfgSecretKey.MatchString(kl) || strings.Contains(kl, "api_key") ||
				kl == "provider_keys" || kl == "secrets" || kl == "credentials" {
				if cfgEmptyValue(v) {
					out[k] = v
				} else {
					out[k] = "[redacted]"
				}
				continue
			}
			out[k] = cfgRedact(v, depth+1)
		}
		return out
	case []any:
		n := len(t)
		if n > 80 {
			n = 80
		}
		out := make([]any, n)
		for i := 0; i < n; i++ {
			out[i] = cfgRedact(t[i], depth+1)
		}
		return out
	case []map[string]any:
		list := make([]any, len(t))
		for i, m := range t {
			list[i] = m
		}
		return cfgRedact(list, depth)
	}
	return obj
}

// cfgEmptyValue is Python's `v in (None, "", [])`.
func cfgEmptyValue(v any) bool {
	switch t := v.(type) {
	case nil:
		return true
	case string:
		return t == ""
	case []any:
		return len(t) == 0
	case []string:
		return len(t) == 0
	}
	return false
}

// ---------------------------------------------------------------- settings

func cmdSettings(c *Ctx) int {
	defer cfgQuietLog()()
	switch c.P.Sub(1) {
	case "", "show":
		c.Println("Remedy settings (secrets redacted)")
		return cfgPrintJSON(c, cfgSnapshot(c.Home))
	case "keys":
		t := newTable("Settable settings keys", "Key")
		for _, k := range httpapi.SettableSettingsKeys() {
			t.add(k)
		}
		t.print(c.Out)
		c.Println("Usage: remedy settings set thinking_level=high tool_process=full")
	case "get":
		key := strings.TrimSpace(c.P.Str("key"))
		snap := cfgSnapshot(c.Home)
		if v, ok := snap[key]; ok && key != "" {
			return cfgPrintOrdered(c, []string{key}, map[string]any{key: v})
		}
		if httpapi.IsSettableSettingsKey(key) {
			c.Printf("%s is settable but not in public snapshot (secret or nested). Use settings show.\n", key)
			return 0
		}
		c.Errorf("Unknown key: %s\n", key)
		c.Errorf("Run: remedy settings keys\n")
		return 1
	case "set":
		return cfgSettingsSet(c)
	}
	return 0
}

// cfgSnapshot is the Settings payload. The version is the one the launcher
// passes in, else the runtime's own.
func cfgSnapshot(home string) map[string]any {
	return httpapi.CLISettingsSnapshot(home, strings.TrimSpace(os.Getenv("REMEDY_CLI_VERSION")))
}

func cfgSettingsSet(c *Ctx) int {
	patch := map[string]any{}
	var order []string
	if raw := c.P.Str("json"); raw != "" {
		var loaded any
		dec := json.NewDecoder(strings.NewReader(raw))
		dec.UseNumber()
		err := dec.Decode(&loaded)
		if err == nil && dec.More() {
			err = errors.New("extra data after the JSON value")
		}
		if err != nil {
			c.Errorf("Invalid --json: %v\n", err)
			return 1
		}
		obj, ok := loaded.(map[string]any)
		if !ok {
			c.Errorf("--json must be an object\n")
			return 1
		}
		for k, v := range obj {
			patch[k] = cfgFromJSON(v)
			order = append(order, k)
		}
		sort.Strings(order)
	}
	for _, pair := range c.P.List("pairs") {
		k, v, found := strings.Cut(pair, "=")
		if !found {
			c.Errorf("Expected KEY=VALUE, got: %s\n", pair)
			return 1
		}
		k = strings.TrimSpace(k)
		if k == "" {
			c.Errorf("Empty key in: %s\n", pair)
			return 1
		}
		if _, seen := patch[k]; !seen {
			order = append(order, k)
		}
		patch[k] = cfgParseValue(v)
	}
	if len(patch) == 0 {
		c.Errorf("No settings to apply. Use KEY=VALUE or --json '{...}'\n")
		return 1
	}
	result, err := httpapi.CLIApplySettings(c.Home, patch)
	if errors.Is(err, httpapi.ErrSettingsRejected) {
		c.Errorf("Settings rejected: %v\n", err)
		return 1
	}
	if err != nil {
		c.Errorf("Write failed: %v\n", err)
		return 1
	}
	var changes []string
	switch ch := result["changes"].(type) {
	case []string:
		changes = append(changes, ch...)
	case []any:
		for _, x := range ch {
			changes = append(changes, fmt.Sprint(x))
		}
	}
	sort.Strings(changes)
	msg, _ := result["message"].(string)
	if msg == "" {
		msg = "Applied settings: " + strings.Join(changes, ", ")
	}
	c.Println(msg)
	if len(changes) > 0 {
		c.Printf("Changed: %s\n", strings.Join(changes, ", "))
	}
	var shown []string
	for _, k := range order {
		if _, ok := result[k]; ok {
			shown = append(shown, k)
		}
	}
	if len(shown) > 0 {
		return cfgPrintOrdered(c, shown, result)
	}
	return 0
}

var cfgLeadingZero = regexp.MustCompile(`^[+-]?0\d+$`)

// cfgParseValue is Python _parse_setting_value: bool / null words, JSON
// objects and arrays, ints, floats, else the text itself.
func cfgParseValue(raw string) any {
	s := strings.TrimSpace(raw)
	switch strings.ToLower(s) {
	case "true", "yes", "on":
		return true
	case "false", "no", "off":
		return false
	case "null", "none":
		return nil
	}
	if (strings.HasPrefix(s, "{") && strings.HasSuffix(s, "}")) || (strings.HasPrefix(s, "[") && strings.HasSuffix(s, "]")) {
		var v any
		dec := json.NewDecoder(strings.NewReader(s))
		dec.UseNumber()
		if dec.Decode(&v) == nil && !dec.More() {
			return cfgFromJSON(v)
		}
	}
	if !(strings.HasPrefix(s, "0") && len(s) > 1 && !strings.Contains(s, ".")) {
		if n, err := strconv.ParseInt(s, 10, 64); err == nil {
			return n
		}
	}
	if cfgLeadingZero.MatchString(s) {
		return s
	}
	// Python float(): no hex, and inf / nan stay text (they have no JSON or
	// TOML spelling the rest of Remedy reads).
	if !strings.ContainsAny(s, "xX") {
		if f, err := strconv.ParseFloat(s, 64); err == nil && !math.IsInf(f, 0) && !math.IsNaN(f) {
			return f
		}
	}
	return s
}

// cfgFromJSON turns json.Number into int64 or float64 the way Python's
// json.loads types numbers, so config.toml gets 30, not "30".
func cfgFromJSON(v any) any {
	switch t := v.(type) {
	case json.Number:
		s := t.String()
		if !strings.ContainsAny(s, ".eE") {
			if n, err := t.Int64(); err == nil {
				return n
			}
		}
		if f, err := t.Float64(); err == nil {
			return f
		}
		return s
	case map[string]any:
		for k, x := range t {
			t[k] = cfgFromJSON(x)
		}
		return t
	case []any:
		for i, x := range t {
			t[i] = cfgFromJSON(x)
		}
		return t
	}
	return v
}

// ---------------------------------------------------------------- JSON out

// cfgPrintJSON prints v as two-space-indented JSON (rich print_json). Floats
// keep their point (0.8, 1.0) as Python printed them.
func cfgPrintJSON(c *Ctx, v any) int {
	raw, err := cfgMarshal(v)
	if err != nil {
		c.Errorf("Could not print JSON: %v\n", err)
		return 1
	}
	c.Println(raw)
	return 0
}

// cfgPrintOrdered prints the keys of m in the given order as one JSON object.
func cfgPrintOrdered(c *Ctx, keys []string, m map[string]any) int {
	var b strings.Builder
	b.WriteString("{\n")
	for i, k := range keys {
		kj, _ := json.Marshal(k)
		vj, err := cfgMarshal(m[k])
		if err != nil {
			c.Errorf("Could not print JSON: %v\n", err)
			return 1
		}
		b.WriteString("  " + string(kj) + ": " + strings.ReplaceAll(vj, "\n", "\n  "))
		if i < len(keys)-1 {
			b.WriteString(",")
		}
		b.WriteString("\n")
	}
	b.WriteString("}")
	c.Println(b.String())
	return 0
}

func cfgMarshal(v any) (string, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(cfgJSONView(reflect.ValueOf(v))); err != nil {
		return "", err
	}
	return strings.TrimRight(buf.String(), "\n"), nil
}

// cfgJSONView copies v with every float as a Python-style number literal.
func cfgJSONView(v reflect.Value) any {
	if !v.IsValid() {
		return nil
	}
	switch v.Kind() {
	case reflect.Interface, reflect.Pointer:
		if v.IsNil() {
			return nil
		}
		return cfgJSONView(v.Elem())
	case reflect.Float32, reflect.Float64:
		return cfgPyFloat(v.Float())
	case reflect.Map:
		if v.IsNil() {
			return nil
		}
		out := make(map[string]any, v.Len())
		it := v.MapRange()
		for it.Next() {
			out[fmt.Sprint(it.Key().Interface())] = cfgJSONView(it.Value())
		}
		return out
	case reflect.Slice, reflect.Array:
		if v.Kind() == reflect.Slice && v.IsNil() {
			return []any{}
		}
		if v.Type().Elem().Kind() == reflect.Uint8 {
			return v.Interface()
		}
		out := make([]any, v.Len())
		for i := range out {
			out[i] = cfgJSONView(v.Index(i))
		}
		return out
	}
	return v.Interface()
}

// cfgPyFloat spells f the way Python's repr does (1.0, 0.75, 1e-05).
func cfgPyFloat(f float64) any {
	if math.IsNaN(f) || math.IsInf(f, 0) {
		return fmt.Sprint(f)
	}
	a := math.Abs(f)
	var s string
	if a != 0 && (a >= 1e16 || a < 1e-4) {
		s = strconv.FormatFloat(f, 'e', -1, 64)
	} else {
		s = strconv.FormatFloat(f, 'f', -1, 64)
		if !strings.Contains(s, ".") {
			s += ".0"
		}
	}
	return json.Number(s)
}

// cfgPyStr is Python str() for a status value.
func cfgPyStr(v any) string {
	switch t := v.(type) {
	case nil:
		return "None"
	case bool:
		if t {
			return "True"
		}
		return "False"
	case float64:
		return string(cfgPyFloat(t).(json.Number))
	}
	return fmt.Sprint(v)
}

// ---------------------------------------------------------------- auth

func cmdAuth(c *Ctx) int {
	defer cfgQuietLog()()
	provider := strings.ToLower(strings.TrimSpace(c.P.Str("provider")))
	if provider == "" {
		provider = "xai"
	}
	sub := c.P.Sub(1)
	if provider != "xai" {
		return cfgAuthAPIKeyProvider(c, provider, sub)
	}
	switch sub {
	case "status":
		t := newTable("xAI auth", "Field", "Value")
		for _, f := range httpapi.CLIXaiStatus(c.Home) {
			t.add(f.Name, cfgPyStr(f.Value))
		}
		t.print(c.Out)
		return 0
	case "logout":
		httpapi.CLIXaiClear(c.Home)
		c.Println("Signed out of xAI (cleared ~/.remedy/auth/xai.json).")
		return 0
	case "apikey":
		key := c.P.Str("api_key")
		if key == "" {
			var err error
			if key, err = cfgReadSecret(c, "xAI API key"); err != nil {
				c.Errorf("%v\n", err)
				return 1
			}
		}
		connected, err := httpapi.CLIXaiSaveAPIKey(c.Home, key)
		if err != nil {
			c.Errorf("%v\n", err)
			return 1
		}
		c.Printf("Saved xAI API key. connected=%s\n", cfgPyStr(connected))
		return 0
	case "login":
		return cfgXaiLogin(c)
	}
	c.Println("Usage: remedy auth login|logout|status|apikey xai")
	return 0
}

// cfgXaiLogin is the device-code sign-in: show the code, open the page, wait
// until xAI says connected, refused, or the code expires.
func cfgXaiLogin(c *Ctx) int {
	c.Println("Sign in with xAI (device-code OAuth)…")
	start, err := cfgXaiStart(c.Home)
	if err != nil {
		c.Errorf("Failed to start OAuth: %v\n", err)
		return 1
	}
	uri := strings.TrimSpace(cfgAnyString(start["verification_uri_complete"]))
	if uri == "" {
		uri = strings.TrimSpace(cfgAnyString(start["verification_uri"]))
	}
	code := cfgAnyString(start["user_code"])
	c.Printf("User code: %s\n", code)
	c.Printf("Open: %s\n", uri)
	// Only a web page is handed to the desktop's opener; the code and link
	// above are enough to finish by hand.
	if strings.HasPrefix(strings.ToLower(uri), "https://") {
		_ = cfgOpenURL(uri)
	}
	sessionID := cfgAnyString(start["session_id"])
	if sessionID == "" {
		sessionID = code
	}
	expires := cfgAnyInt(start["expires_in"])
	if expires == 0 {
		expires = 900
	}
	interval := cfgAnyInt(start["interval"])
	if interval == 0 {
		interval = 5
	}
	if interval < 3 {
		interval = 3
	}
	deadline := cfgNow().Add(time.Duration(expires) * time.Second)
	c.Println("Waiting for approval…")
	for cfgNow().Before(deadline) {
		cfgSleep(time.Duration(interval) * time.Second)
		status := cfgXaiStatus(c.Home, sessionID)
		sess, _ := status["session"].(map[string]any)
		switch cfgAnyString(sess["status"]) {
		case "connected":
			c.Println("Connected via xAI OAuth.")
			return 0
		case "error":
			reason := cfgAnyString(sess["error"])
			if reason == "" {
				reason = "unknown"
			}
			c.Errorf("Login failed: %s\n", reason)
			return 1
		}
	}
	c.Errorf("Login timed out. Run `remedy auth login xai` again.\n")
	return 1
}

// cfgAuthAPIKeyProvider is auth login/logout/status/apikey for every provider
// but xAI (Python _auth_api_key_provider).
func cfgAuthAPIKeyProvider(c *Ctx, provider, sub string) int {
	catalog := httpapi.CLIProviderCatalog(c.Home)
	if provider == "all" {
		switch sub {
		case "status":
			cfgPrintKeyStatus(c, catalog, "")
			return 0
		case "logout":
			if err := os.Remove(secret.ProviderKeysPath(c.Home)); err != nil && !errors.Is(err, os.ErrNotExist) {
				c.Errorf("Could not clear the key store: %v\n", err)
				return 1
			}
			c.Println("Cleared every stored provider API key.")
			return 0
		}
		c.Errorf("'all' works with status and logout. Name a provider to store or sign in to one.\n")
		return 2
	}
	entry, known := catalog[provider]
	if !known {
		names := make([]string, 0, len(catalog))
		for id := range catalog {
			names = append(names, id)
		}
		sort.Strings(names)
		c.Errorf("Unknown provider '%s'. Known: %s, all\n", provider, strings.Join(names, ", "))
		return 2
	}
	label := entry.Label
	if label == "" {
		label = provider
	}
	takesKey := cfgHas(entry.Auth, "api_key")

	switch sub {
	case "status":
		if !takesKey {
			c.Printf("%s needs no key — it runs locally or allows guest access.\n", label)
			return 0
		}
		cfgPrintKeyStatus(c, catalog, provider)
		return 0
	case "logout":
		if secret.GetProviderSecret(c.Home, provider) == "" {
			c.Printf("No stored key for %s; nothing to clear.\n", label)
			return 0
		}
		if err := secret.SetProviderSecret(c.Home, provider, ""); err != nil {
			c.Errorf("Could not clear the key: %v\n", err)
			return 1
		}
		c.Printf("Cleared the stored API key for %s.\n", label)
		return 0
	case "apikey":
		if !takesKey {
			c.Errorf("%s does not take an API key — it runs locally or allows guest access, so there is nothing to store.\n", label)
			return 2
		}
		key := c.P.Str("api_key")
		if key == "" {
			var err error
			if key, err = cfgReadSecret(c, label+" API key"); err != nil {
				c.Errorf("%v\n", err)
				return 1
			}
		}
		key = strings.TrimSpace(key)
		if key == "" {
			c.Errorf("API key is empty.\n")
			return 1
		}
		// The same door Settings uses: refuses Claude subscription tokens and
		// files a key under the provider that issued it.
		owner, err := cfgStoreKey(c.Home, provider, key)
		if err != nil {
			c.Errorf("%v\n", err)
			return 1
		}
		if owner != "" && owner != provider {
			c.Printf("That key belongs to %s, not %s; stored it under %s.\n", owner, label, owner)
			label = owner
		}
		c.Printf("Saved %s API key. fingerprint=%s\n", label, cfgFingerprint(key))
		return 0
	case "login":
		if cfgHas(entry.Auth, "oauth") {
			c.Errorf("%s supports OAuth, but only xAI's device-code flow is wired up.\n", label)
			return 2
		}
		c.Errorf("%s has no interactive sign-in — it authenticates with an API key. Run: remedy auth apikey %s\n", label, provider)
		return 2
	}
	c.Errorf("Usage: remedy auth login|logout|status|apikey %s\n", provider)
	return 2
}

// cfgPrintKeyStatus shows what is stored, never what it is: fingerprints only.
func cfgPrintKeyStatus(c *Ctx, catalog map[string]httpapi.CLIProvider, provider string) {
	status := secret.PublicSecretStatus(c.Home)
	keys := secret.LoadProviderKeys(c.Home)
	var rows []string
	if provider != "" {
		rows = []string{provider}
	} else {
		for k := range keys {
			rows = append(rows, k)
		}
		sort.Strings(rows)
	}
	if len(rows) == 0 {
		c.Println("No provider API keys stored yet.")
	} else {
		t := newTable(fmt.Sprintf("API keys (%v)", status["encoding"]), "Provider", "Key stored", "Fingerprint", "Env fallback")
		for _, name := range rows {
			var env []string
			for _, k := range catalog[name].EnvKeys {
				if os.Getenv(k) != "" {
					env = append(env, k)
				}
			}
			stored, fp := "no", "—"
			if v := keys[name]; strings.TrimSpace(v) != "" {
				stored, fp = "yes", cfgFingerprint(v)
			}
			envText := strings.Join(env, ", ")
			if envText == "" {
				envText = "—"
			}
			t.add(name, stored, fp, envText)
		}
		t.print(c.Out)
	}
	c.Printf("Stored in %v\n", status["store_path"])
	if w, _ := status["encoding_warning"].(string); w != "" {
		c.Println(w)
	}
}

// cfgFingerprint is Python secret_store.fingerprint_key: 12 hex chars of
// SHA-256, never reversible to the key.
func cfgFingerprint(key string) string {
	k := strings.TrimSpace(key)
	if k == "" {
		return "—"
	}
	sum := sha256.Sum256([]byte(k))
	return hex.EncodeToString(sum[:])[:12]
}

// cfgReadSecret asks for a secret on the terminal without echoing it, as
// Python's password prompt did. Piped input is read as a plain line.
func cfgReadSecret(c *Ctx, prompt string) (string, error) {
	c.Printf("%s: ", prompt)
	if f, ok := c.In.(*os.File); ok {
		if restore, hidden := secret.DisableEcho(f); hidden {
			// Ctrl+C must not leave the terminal with echo off.
			sig := make(chan os.Signal, 1)
			signal.Notify(sig, os.Interrupt)
			done := make(chan struct{})
			go func() {
				select {
				case <-sig:
					restore()
					fmt.Fprintln(c.Out)
					os.Exit(130)
				case <-done:
				}
			}()
			line, err := cfgReadLine(f)
			close(done)
			signal.Stop(sig)
			restore()
			c.Println()
			return line, err
		}
	}
	return cfgReadLine(c.In)
}

func cfgReadLine(r io.Reader) (string, error) {
	if r == nil {
		return "", errors.New("no input to read the API key from")
	}
	line, err := bufio.NewReader(r).ReadString('\n')
	if err != nil && !errors.Is(err, io.EOF) {
		return "", err
	}
	return strings.TrimRight(line, "\r\n"), nil
}

// cfgQuietLog silences the runtime packages' log lines (written for the
// runtime's log, e.g. the plaintext-store notice on every key save) until the
// returned func runs. What the owner needs is printed by the command itself.
func cfgQuietLog() func() {
	prev := log.Writer()
	log.SetOutput(io.Discard)
	return func() { log.SetOutput(prev) }
}

func cfgHas(list []string, want string) bool {
	for _, x := range list {
		if x == want {
			return true
		}
	}
	return false
}

func cfgAnyString(v any) string {
	if v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return s
	}
	return fmt.Sprint(v)
}

func cfgAnyInt(v any) int {
	switch t := v.(type) {
	case int:
		return t
	case int64:
		return int(t)
	case float64:
		return int(t)
	case json.Number:
		if f, err := t.Float64(); err == nil {
			return int(f)
		}
	case string:
		if f, err := strconv.ParseFloat(strings.TrimSpace(t), 64); err == nil {
			return int(f)
		}
	}
	return 0
}
