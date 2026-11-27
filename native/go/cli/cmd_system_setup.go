package cli

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/httpapi"
)

// `remedy setup`: a port of src/remedy/interfaces/wizard.py run_wizard with
// the same steps, questions and order. Answers come from c.In.

var sysWelcomeArt = []string{
	"██████╗ ███████╗███╗   ███╗███████╗██████╗ ██╗   ██╗",
	"██╔══██╗██╔════╝████╗ ████║██╔════╝██╔══██╗╚██╗ ██╔╝",
	"██████╔╝█████╗  ██╔████╔██║█████╗  ██║  ██║ ╚████╔╝ ",
	"██╔══██╗██╔══╝  ██║╚██╔╝██║██╔══╝  ██║  ██║  ╚██╔╝  ",
	"██║  ██║███████╗██║ ╚═╝ ██║███████╗██████╔╝   ██║   ",
	"╚═╝  ╚═╝╚══════╝╚═╝     ╚═╝╚══════╝╚═════╝    ╚═╝   ",
	"",
	"  The self-improving, multi-channel AI agent framework.",
}

var sysWelcomeASCII = []string{
	" #####  ######  #    #  ######  ####   #   # ",
	" #    #  #       ##  ##  #       #   #   # #  ",
	" #####   #####   # ## #  #####   #   #    #   ",
	" #  #    #       #    #  #       #   #    #   ",
	" #   ##  ######  #    #  ######  ####     #   ",
	"",
	"  The self-improving, multi-channel AI agent framework.",
}

type sysChannel struct{ key, label, token string }

var sysChannels = []sysChannel{
	{"cli", "CLI (command-line)", ""},
	{"web", "REST API + Web dashboard", ""},
	{"telegram", "Telegram Bot", "bot_token"},
	{"discord", "Discord Bot", "bot_token"},
	{"slack", "Slack Bot", "bot_token"},
}

type sysPersona struct{ name, desc string }

var sysPersonas = []sysPersona{
	{"default", "Balanced, helpful agent"},
	{"concise", "Short, direct responses only"},
	{"verbose", "Detailed explanations with context"},
	{"sarcastic", "Witty, dry humor (experimental)"},
	{"minimal", "Commands only, no conversation"},
}

var sysLogLevels = []string{"DEBUG", "INFO", "WARNING", "ERROR"}

type sysProvider struct{ key, label, model, baseURL string }

var sysLLMProviders = []sysProvider{
	{"openai", "OpenAI", "", "https://api.openai.com/v1"},
	{"anthropic", "Anthropic", "", "https://api.anthropic.com/v1"},
	{"google", "Google Gemini", "", "https://generativelanguage.googleapis.com/v1beta/openai"},
	{"deepseek", "DeepSeek", "", "https://api.deepseek.com/v1"},
	{"xai", "xAI (Grok) — OAuth or API key", "", "https://api.x.ai/v1"},
	{"groq", "Groq", "", "https://api.groq.com/openai/v1"},
	{"mistral", "Mistral", "", "https://api.mistral.ai/v1"},
	{"poe", "Poe (multi-model API)", "", "https://api.poe.com/v1"},
	{"openrouter", "OpenRouter", "", "https://openrouter.ai/api/v1"},
	{"ollama", "Ollama (local)", "", "http://localhost:11434/v1"},
	{"custom", "Custom OpenAI-compatible (advanced)", "", ""},
}

// sysValidSkillName is core.security.VALID_SKILL_NAME_RE.
var sysValidSkillName = regexp.MustCompile(`^[a-zA-Z0-9_\-]+$`)

func sysNameOK(name string) bool {
	n := strings.ToLower(strings.TrimSpace(name))
	return n != "" && len([]rune(n)) <= 100 && sysValidSkillName.MatchString(n)
}

type sysSetupOpts struct{ quick, skipProviders, skipMessaging, skipSkills bool }

func cmdSetup(c *Ctx) int {
	opts := sysSetupOpts{
		quick:         c.P.Bool("quick"),
		skipProviders: c.P.Bool("skip-providers"),
		skipMessaging: c.P.Bool("skip-messaging"),
		skipSkills:    c.P.Bool("skip-skills"),
	}
	return sysRunWizard(c, opts)
}

func sysRule(c *Ctx, title string) {
	c.Printf("\n── %s ──\n", title)
}

func sysPanel(c *Ctx, title, body string) {
	c.Println()
	if title != "" {
		c.Printf("[ %s ]\n", title)
	}
	for _, l := range strings.Split(body, "\n") {
		c.Printf("  %s\n", l)
	}
	c.Println()
}

// sysSupportsUnicode stands in for the stdout-encoding probe: Windows
// consoles get UTF-16 writes from Go; elsewhere a non-UTF-8 locale gets the
// ASCII banner.
func sysSupportsUnicode() bool {
	if runtime.GOOS == "windows" {
		return true
	}
	for _, k := range []string{"LC_ALL", "LC_CTYPE", "LANG"} {
		if v := os.Getenv(k); v != "" {
			low := strings.ToLower(v)
			return strings.Contains(low, "utf-8") || strings.Contains(low, "utf8")
		}
	}
	return true
}

func sysPrintWelcome(c *Ctx) {
	art := sysWelcomeArt
	if !sysSupportsUnicode() {
		art = sysWelcomeASCII
	}
	for _, l := range art {
		c.Println(l)
	}
	sysPanel(c, "Setup Wizard", "Welcome to the Remedy setup wizard.\n"+
		"This is the traditional (form) setup. On the desktop app,\n"+
		"Remedy talks you through the same settings as a conversation,\n"+
		"and you can switch to this form at any time without losing answers.\n"+
		"Every question is skippable; skipped items keep the defaults.\n"+
		"You can always change these later in chat or in ~/.remedy/config.toml")
}

func sysRunWizard(c *Ctx, opts sysSetupOpts) (code int) {
	p := newSysPrompter(c)
	defer func() {
		if r := recover(); r != nil {
			if err, ok := r.(error); ok && errors.Is(err, errSysNoInput) {
				c.Println()
				c.Errorf("Setup cancelled: input ended before the wizard finished. No changes made.\n")
				code = 1
				return
			}
			panic(r)
		}
	}()
	sysPrintWelcome(c)
	cfg := map[string]any{}

	sysRule(c, "Step 1: Agent Identity")
	if opts.quick {
		cfg["name"] = "Remedy"
		cfg["persona"] = "default"
		c.Println("Quick mode: using defaults (Remedy / default persona)")
	} else {
		name := p.Ask("Agent name", "Remedy")
		if !sysNameOK(name) {
			c.Println("Name contains special chars; using 'remedy'")
			name = "remedy"
		}
		cfg["name"] = name
		cfg["persona"] = sysPickPersona(c, p)
	}

	sysRule(c, "Step 2: LLM Provider")
	sysConfigureProvider(c, p, cfg, opts.quick, opts.skipProviders)

	sysRule(c, "Step 3: Messaging Apps")
	sysConfigureMessaging(c, p, cfg, opts.quick, opts.skipMessaging)

	sysRule(c, "Step 4: Runtime Settings")
	cfg["home_dir"] = filepath.ToSlash(c.Home)
	if opts.quick {
		cfg["log_level"] = "INFO"
		cfg["auto_approve_threshold"] = 0.8
		cfg["allow_skill_creation"] = true
		c.Println("Quick mode: INFO logging, 0.8 auto-approve")
	} else {
		cfg["log_level"] = sysPickOption(c, p, "Log level", sysLogLevels, "INFO")
		cfg["auto_approve_threshold"] = p.Float("Auto-approve threshold for skill creation", 0.8)
		cfg["allow_skill_creation"] = p.Confirm("Allow automatic skill creation from traces?", true)
	}

	sysRule(c, "Step 5: Skill Discovery")
	sysConfigureSkills(c, p, cfg, opts.quick, opts.skipSkills)

	cfg["gateway"] = map[string]any{"heartbeat_interval": 60, "rate_limit": 120}
	cfg["execution"] = map[string]any{"default_timeout": 30, "max_retries": 3, "retry_backoff": 1.0}
	cfg["setup_completed"] = true

	sysRule(c, "Review")
	sysPrintSummary(c, cfg)

	if opts.quick {
		c.Println("Quick mode: auto-saving")
	} else if !p.Confirm("Save this configuration?", true) {
		c.Println("Setup cancelled. No changes made.")
		return 0
	}

	path, err := httpapi.SetupWriteConfig(c.Home, cfg)
	if err != nil {
		c.Errorf("Could not write config: %v\n", err)
		return 1
	}
	c.Printf("\nConfig written to %s\n", path)

	sysPanel(c, "Setup Complete", fmt.Sprintf("Agent %v is ready.\n\n", cfg["name"])+
		fmt.Sprintf("Config:  %s\n", path)+
		fmt.Sprintf("Memory:  %s\n\n", sysSetupDBPath(c, cfg))+
		"Next steps:\n"+
		"  remedy serve                  Start the API server\n"+
		"  remedy memory add 'hello' 'My first memory'\n"+
		"  remedy session start           Begin a working session\n"+
		"  remedy --help                  See all commands")
	return 0
}

func sysSetupDBPath(c *Ctx, cfg map[string]any) string {
	home := strings.TrimSpace(asString(cfg["home_dir"]))
	if home == "" {
		home = c.Home
	}
	return filepath.Join(expandUser(home), "memory.db")
}

func sysClamp(n, lo, hi int) int {
	if n < lo {
		return lo
	}
	if n > hi {
		return hi
	}
	return n
}

func sysPickPersona(c *Ctx, p *sysPrompter) string {
	t := newTable("Available Personas", "#", "Name", "Description")
	for i, ps := range sysPersonas {
		t.add(strconv.Itoa(i+1), ps.name, ps.desc)
	}
	t.print(c.Out)
	n := p.Int(fmt.Sprintf("Select persona (1-%d)", len(sysPersonas)), 1)
	return sysPersonas[sysClamp(n, 1, len(sysPersonas))-1].name
}

// sysParseChannels is the parsing half of _pick_channels: comma-separated
// row numbers, spaces ignored, duplicates and junk dropped, CLI when empty.
func sysParseChannels(raw string) []string {
	var out []string
	seen := map[string]bool{}
	for _, part := range strings.Split(strings.ReplaceAll(raw, " ", ""), ",") {
		n, err := strconv.Atoi(part)
		if err != nil {
			continue
		}
		if idx := n - 1; idx >= 0 && idx < len(sysChannels) {
			k := sysChannels[idx].key
			if !seen[k] {
				seen[k] = true
				out = append(out, k)
			}
		}
	}
	if len(out) == 0 {
		return []string{"cli"}
	}
	return out
}

func sysPickChannels(c *Ctx, p *sysPrompter) []string {
	t := newTable("Channel Setup", "#", "Channel", "Requires Token")
	for i, ch := range sysChannels {
		tok := ch.token
		if tok == "" {
			tok = "none"
		}
		t.add(strconv.Itoa(i+1), ch.label, tok)
	}
	t.print(c.Out)
	c.Println("Enter channel numbers to enable (comma-separated, e.g. 1,3,4)\nor press Enter for CLI only.")
	return sysParseChannels(p.Ask("Channels", "1"))
}

func sysConfigureProvider(c *Ctx, p *sysPrompter, cfg map[string]any, quick, skip bool) {
	if quick || skip {
		if quick {
			cfg["llm_provider"] = "openai"
			cfg["llm_model"] = sysLLMProviders[0].model
			cfg["llm_base_url"] = sysLLMProviders[0].baseURL
			cfg["llm_api_key"] = ""
			c.Println("Quick mode: OpenAI, provider default model (no API key)")
		}
		if skip {
			c.Println("Skipping provider setup")
		}
		return
	}
	if !p.Confirm("Configure an LLM provider?", true) {
		c.Println("Skipping provider setup. Set llm_api_key later in config.")
		return
	}
	t := newTable("Available LLM Providers", "#", "Provider", "Default Model")
	for i, pr := range sysLLMProviders {
		m := pr.model
		if m == "" {
			m = "(custom)"
		}
		t.add(strconv.Itoa(i+1), pr.label, m)
	}
	t.print(c.Out)
	n := p.Int(fmt.Sprintf("Select provider (1-%d)", len(sysLLMProviders)), 1)
	pr := sysLLMProviders[sysClamp(n, 1, len(sysLLMProviders))-1]
	c.Printf("Selected: %s\n", pr.label)
	cfg["llm_provider"] = pr.key

	key := strings.TrimSpace(p.Secret(fmt.Sprintf("  %s API key (or press Enter to skip): ", pr.label)))
	cfg["llm_api_key"] = key

	model := p.Ask("  Model name (blank = provider default)", pr.model)
	cfg["llm_model"] = model

	var base string
	if pr.key == "custom" || pr.baseURL == "" {
		base = p.Ask("  API base URL", "https://api.openai.com/v1")
	} else {
		base = p.Ask("  API base URL", pr.baseURL)
	}
	cfg["llm_base_url"] = strings.TrimRight(base, "/")

	if key != "" {
		c.Printf("  %s API key saved\n", pr.label)
	} else {
		c.Println("  No API key set. Set REMEDY_LLM_API_KEY env var or edit config.")
	}
	c.Printf("  Model: %s | Base URL: %s\n", model, base)
}

func sysTitle(s string) string {
	if s == "" {
		return s
	}
	return strings.ToUpper(s[:1]) + s[1:]
}

func sysConfigureMessaging(c *Ctx, p *sysPrompter, cfg map[string]any, quick, skip bool) {
	if quick || skip {
		cfg["enabled_channels"] = []string{"cli"}
		if quick {
			c.Println("Quick mode: CLI channel only")
		}
		if skip {
			c.Println("Skipping messaging app setup")
		}
		return
	}
	if !p.Confirm("Configure messaging apps (Telegram, Discord, Slack)?", false) {
		cfg["enabled_channels"] = []string{"cli"}
		c.Println("Skipping messaging setup. CLI channel enabled by default.")
		return
	}
	chans := sysPickChannels(c, p)
	cfg["enabled_channels"] = chans
	enabled := map[string]bool{}
	for _, ch := range chans {
		enabled[ch] = true
	}
	for _, nt := range []struct{ ch, text string }{
		{"telegram", "Telegram Bot Token (from @BotFather)"},
		{"discord", "Discord Bot Token (from Discord Developer Portal)"},
		{"slack", "Slack Bot Token (from Slack API)"},
	} {
		if !enabled[nt.ch] {
			continue
		}
		c.Printf("\n%s\n", nt.text)
		title := sysTitle(nt.ch)
		if p.Confirm(fmt.Sprintf("Do you have a %s token?", title), false) {
			tok := strings.TrimSpace(p.Secret(fmt.Sprintf("  %s token: ", title)))
			cfg[nt.ch] = map[string]any{"bot_token": tok}
			c.Printf("  %s token saved\n", title)
		} else {
			c.Printf("  Skipped. Add it later in ~/.remedy/config.toml under [%s]\n", nt.ch)
			cfg[nt.ch] = map[string]any{"bot_token": ""}
		}
	}
}

func sysConfigureSkills(c *Ctx, p *sysPrompter, cfg map[string]any, quick, skip bool) {
	if quick || skip {
		cfg["skills_dir"] = []string{}
		if quick {
			c.Println("Quick mode: no skill scanning")
		}
		if skip {
			c.Println("Skipping skill discovery setup")
		}
		return
	}
	if !p.Confirm("Configure skill directories?", true) {
		cfg["skills_dir"] = []string{}
		c.Println("Skipping skill discovery")
		return
	}
	paths := []string{}
	for {
		def := ""
		if len(paths) == 0 {
			def = "skills"
		}
		v := p.Ask("Skills directory path (Enter empty to finish)", def)
		if v == "" {
			break
		}
		paths = append(paths, v)
	}
	cfg["skills_dir"] = paths
	if len(paths) > 0 {
		c.Printf("  Will scan: %s\n", strings.Join(paths, ", "))
	}
}

// sysPickOption is _pick_option: a row number or an exact option, else the
// default.
func sysPickOption(c *Ctx, p *sysPrompter, prompt string, options []string, def string) string {
	for i, o := range options {
		marker := ""
		if o == def {
			marker = " [default]"
		}
		c.Printf("  %d. %s%s\n", i+1, o, marker)
	}
	return sysResolveOption(p.Ask(prompt, def), options, def)
}

func sysResolveOption(choice string, options []string, def string) string {
	if n, err := strconv.Atoi(choice); err == nil {
		if n-1 >= 0 && n-1 < len(options) {
			return options[n-1]
		}
	}
	for _, o := range options {
		if o == choice {
			return choice
		}
	}
	return def
}

func sysStrList(v any) []string {
	switch x := v.(type) {
	case []string:
		return x
	case []any:
		out := make([]string, 0, len(x))
		for _, e := range x {
			out = append(out, asString(e))
		}
		return out
	}
	return nil
}

func sysPrintSummary(c *Ctx, cfg map[string]any) {
	t := newTable("Configuration Summary", "Setting", "Value")
	get := func(k, def string) string {
		if v, ok := cfg[k]; ok {
			return asString(v)
		}
		return def
	}
	t.add("Agent", get("name", "Remedy"))
	t.add("Persona", get("persona", "default"))
	model := asString(cfg["llm_model"])
	if model == "" {
		model = "provider default"
	}
	if asString(cfg["llm_api_key"]) != "" {
		model += " (key set)"
	} else {
		model += " (no key)"
	}
	t.add("LLM Provider", model)
	chans := sysStrList(cfg["enabled_channels"])
	ch := strings.Join(chans, ", ")
	if ch == "" {
		ch = "cli"
	}
	t.add("Channels", ch)
	t.add("Log Level", get("log_level", "INFO"))
	thr := 0.8
	if f, ok := cfg["auto_approve_threshold"].(float64); ok {
		thr = f
	}
	t.add("Auto-approve", sysPyFloat(thr))
	allow := true
	if b, ok := cfg["allow_skill_creation"].(bool); ok {
		allow = b
	}
	if allow {
		t.add("Allow skill creation", "yes")
	} else {
		t.add("Allow skill creation", "no")
	}
	sd := strings.Join(sysStrList(cfg["skills_dir"]), ", ")
	if sd == "" {
		sd = "(none)"
	}
	t.add("Skills dir", sd)
	t.add("Home", get("home_dir", "~/.remedy"))
	t.add("Memory DB", sysSetupDBPath(c, cfg))
	enabled := map[string]bool{}
	for _, e := range chans {
		enabled[e] = true
	}
	for _, chn := range []string{"telegram", "discord", "slack"} {
		sec, _ := cfg[chn].(map[string]any)
		if sec != nil && asString(sec["bot_token"]) != "" {
			t.add(sysTitle(chn)+" token", "configured")
		} else if enabled[chn] {
			t.add(sysTitle(chn)+" token", "not set")
		}
	}
	t.print(c.Out)
}
