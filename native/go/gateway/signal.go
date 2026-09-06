package gateway

import (
	"context"
	"encoding/json"
	"log"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// SignalRunner executes one signal-cli argv (absolute argv[0]) with a timeout.
// Tests inject doubles; production uses Zig authorized exec-capture.
type SignalRunner func(ctx context.Context, argv []string, timeout time.Duration) (exitCode int, stdout, stderr string, err error)

// SignalConfig configures the signal-cli adapter.
type SignalConfig struct {
	CLIPath   string
	Account   string
	AllowFrom any
	AllowAll  bool
	HomeDir   string
	// Runner overrides Zig exec (tests). Nil → Zig authorized capture.
	Runner SignalRunner
}

// SignalChannel owns signal-cli receive poll + send via Zig (no os/exec).
type SignalChannel struct {
	gateway  *Gateway
	cliPath  string
	account  string
	home     string
	allowed  map[string]struct{}
	allowAll bool
	run      SignalRunner

	mu         sync.Mutex
	running    bool
	pollActive bool
	cancel     context.CancelFunc
	wg         sync.WaitGroup
	lock       *PollLock
	resolved   string
}

// NewSignal builds a Signal channel. Replaces the former NewSignalOut stub.
func NewSignal(g *Gateway, cfg SignalConfig) *SignalChannel {
	cli := strings.TrimSpace(cfg.CLIPath)
	if cli == "" {
		cli = "signal-cli"
	}
	run := cfg.Runner
	if run == nil {
		run = zigSignalRunner(cfg.HomeDir)
	}
	return &SignalChannel{
		gateway:  g,
		cliPath:  cli,
		account:  strings.TrimSpace(cfg.Account),
		home:     cfg.HomeDir,
		allowed:  ParseIDs(cfg.AllowFrom),
		allowAll: cfg.AllowAll,
		run:      run,
	}
}

// NewSignalOut is kept for callers that only pass account; prefers stub-safe
// registration when cli_path is unset (defaults to "signal-cli").
func NewSignalOut(account string) Channel {
	return NewSignal(nil, SignalConfig{Account: account, CLIPath: "signal-cli"})
}

func (c *SignalChannel) Kind() ChannelKind { return ChannelSignal }

func (c *SignalChannel) Running() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.running
}

func (c *SignalChannel) Start(ctx context.Context) error {
	bin := c.resolveBin()
	if bin == "" || c.account == "" {
		// Do not mark Running — silent stub "running" lied to health/UI.
		log.Printf("signal: not starting (signal-cli=%v account=%v)", bin != "", c.account != "")
		return nil
	}

	c.mu.Lock()
	if c.running {
		c.mu.Unlock()
		return nil
	}
	c.running = true
	runCtx, cancel := context.WithCancel(ctx)
	c.cancel = cancel
	c.mu.Unlock()

	log.Printf("signal: active (cli=%s)", bin)
	if !c.tryStartReceive(runCtx) {
		log.Printf("signal: receive deferred — another process holds the bot lock; retrying")
		c.wg.Add(1)
		go c.lockRetryLoop(runCtx)
	}
	return nil
}

// Health reports setup gates for Settings live status (cli_ok / account_ok / java_ok).
func (c *SignalChannel) Health() map[string]any {
	cliOK := c.resolveBin() != ""
	accountOK := strings.TrimSpace(c.account) != ""
	needsJava := SignalCLINeedsJava()
	javaOK := SignalCLIJavaOK()
	out := map[string]any{
		"cli_ok":     cliOK,
		"account_ok": accountOK,
		"needs_java": needsJava,
		"java_ok":    javaOK,
	}
	if url := SignalCLIDownloadURL(); url != "" {
		out["download_url"] = url
	}
	if !cliOK {
		out["install_hint"] = signalCLIInstallHint()
	} else if needsJava && !javaOK {
		out["install_hint"] = "Install Java 21+ (Temurin) and restart Remedy so signal-cli can run."
	}
	return out
}

func (c *SignalChannel) Stop(ctx context.Context) error {
	_ = ctx
	c.mu.Lock()
	if !c.running {
		c.mu.Unlock()
		return nil
	}
	c.running = false
	cancel := c.cancel
	c.cancel = nil
	lock := c.lock
	c.lock = nil
	c.mu.Unlock()
	if cancel != nil {
		cancel()
	}
	c.wg.Wait()
	if lock != nil {
		lock.Release()
	}
	return nil
}

func (c *SignalChannel) Send(ctx context.Context, message, target string) (bool, error) {
	bin := c.resolveBin()
	if bin == "" || c.account == "" {
		return false, nil
	}
	to := strings.TrimSpace(target)
	if to == "" {
		return false, nil
	}
	argv := []string{bin, "-a", c.account, "send", "-m", message, to}
	code, _, errText, err := c.run(ctx, argv, 90*time.Second)
	if err != nil {
		log.Printf("signal: send failed: %s", SafeErr(err.Error()))
		return false, err
	}
	if code != 0 {
		log.Printf("signal: send failed: %s", SafeErr(trimRunes(errText, 200)))
		return false, nil
	}
	return true, nil
}

func (c *SignalChannel) SendTyping(ctx context.Context, target string) error {
	_ = ctx
	_ = target
	return nil
}

func (c *SignalChannel) resolveBin() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.resolved != "" {
		return c.resolved
	}
	found := resolveSignalCLI(c.cliPath)
	if found == "" {
		found = LookupManagedSignalCLI(c.home)
	}
	c.resolved = found
	return found
}

func (c *SignalChannel) tryStartReceive(ctx context.Context) bool {
	c.mu.Lock()
	if c.pollActive {
		c.mu.Unlock()
		return true
	}
	if c.lock != nil && c.lock.Held {
		// Lock held but poller not marked active — fall through.
	} else {
		if c.lock != nil {
			c.lock.Release()
			c.lock = nil
		}
		c.mu.Unlock()
		lock := NewPollLock(c.home, "signal")
		if !lock.TryAcquire() {
			return false
		}
		c.mu.Lock()
		c.lock = lock
	}
	c.pollActive = true
	c.mu.Unlock()

	c.wg.Add(1)
	go c.receiveLoop(ctx)
	log.Printf("signal: receive task scheduled")
	return true
}

func (c *SignalChannel) lockRetryLoop(ctx context.Context) {
	defer c.wg.Done()
	for {
		select {
		case <-ctx.Done():
			return
		case <-time.After(20 * time.Second):
		}
		if c.tryStartReceive(ctx) {
			log.Printf("signal: receive acquired after retry")
			return
		}
	}
}

func (c *SignalChannel) receiveLoop(ctx context.Context) {
	defer c.wg.Done()
	defer func() {
		c.mu.Lock()
		c.pollActive = false
		c.mu.Unlock()
	}()
	hbDone := make(chan struct{})
	go func() {
		defer close(hbDone)
		t := time.NewTicker(20 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				c.mu.Lock()
				lock := c.lock
				c.mu.Unlock()
				if lock != nil {
					lock.Heartbeat()
				}
			}
		}
	}()
	defer func() { <-hbDone }()

	for {
		select {
		case <-ctx.Done():
			return
		default:
		}
		bin := c.resolveBin()
		if bin == "" || c.account == "" {
			return
		}
		argv := []string{bin, "-a", c.account, "receive", "-t", "10", "--json"}
		code, out, errText, err := c.run(ctx, argv, 30*time.Second)
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			log.Printf("signal: receive error: %s", SafeErr(err.Error()))
			select {
			case <-ctx.Done():
				return
			case <-time.After(2 * time.Second):
			}
			continue
		}
		if code != 0 && errText != "" {
			log.Printf("signal: receive: %s", SafeErr(trimRunes(errText, 120)))
		}
		for _, line := range strings.Split(out, "\n") {
			line = strings.TrimSpace(line)
			if line == "" {
				continue
			}
			var data map[string]any
			if json.Unmarshal([]byte(line), &data) != nil {
				continue
			}
			c.onEnvelope(ctx, data)
		}
	}
}

func (c *SignalChannel) onEnvelope(ctx context.Context, data map[string]any) {
	env, _ := data["envelope"].(map[string]any)
	if env == nil {
		env = data
	}
	source := stringField(env, "source")
	if source == "" {
		source = stringField(env, "sourceNumber")
	}
	dataMsg, _ := env["dataMessage"].(map[string]any)
	text := ""
	if dataMsg != nil {
		text, _ = dataMsg["message"].(string)
		text = strings.TrimSpace(text)
	}
	if text == "" {
		return
	}
	if !IsAllowed(c.allowed, c.allowAll, source) {
		return
	}
	chatID := source
	if chatID == "" {
		chatID = "signal"
	}
	ev := Event{
		ID:        NewEventID(),
		Kind:      EventMessage,
		Channel:   ChannelSignal,
		SourceID:  source,
		SessionID: chatID,
		Payload: map[string]any{
			"message":  text,
			"chat_id":  chatID,
			"user_id":  source,
			"username": source,
		},
		Raw: trimRunes(text, 500),
		At:  time.Now().UTC(),
	}
	if c.gateway != nil && c.gateway.Running() {
		c.gateway.Enqueue(ev)
	} else if c.gateway != nil {
		_ = c.gateway.Emit(ctx, ev)
	}
}

func stringField(m map[string]any, key string) string {
	if m == nil {
		return ""
	}
	v, ok := m[key]
	if !ok || v == nil {
		return ""
	}
	s, _ := v.(string)
	return strings.TrimSpace(s)
}

// ResolveSignalCLIForHealth resolves a signal-cli path for Settings health checks.
func ResolveSignalCLIForHealth(cliPath string) string {
	return resolveSignalCLI(cliPath)
}

// resolveSignalCLI mirrors Python SignalChannel._bin without os/exec.
func resolveSignalCLI(cliPath string) string {
	cliPath = strings.TrimSpace(cliPath)
	if cliPath == "" {
		cliPath = "signal-cli"
	}
	if st, err := os.Stat(cliPath); err == nil && !st.IsDir() {
		if abs, err := filepath.Abs(cliPath); err == nil {
			return abs
		}
		return cliPath
	}
	if filepath.IsAbs(cliPath) {
		return ""
	}
	pathEnv := os.Getenv("PATH")
	sep := string(os.PathListSeparator)
	exts := []string{""}
	if runtime.GOOS == "windows" {
		pathext := os.Getenv("PATHEXT")
		if pathext == "" {
			pathext = ".COM;.EXE;.BAT;.CMD"
		}
		for _, e := range strings.Split(pathext, ";") {
			e = strings.TrimSpace(e)
			if e != "" {
				exts = append(exts, e)
			}
		}
	}
	base := cliPath
	for _, dir := range strings.Split(pathEnv, sep) {
		dir = strings.TrimSpace(dir)
		if dir == "" {
			continue
		}
		for _, ext := range exts {
			candidate := filepath.Join(dir, base+ext)
			if st, err := os.Stat(candidate); err == nil && !st.IsDir() {
				if abs, err := filepath.Abs(candidate); err == nil {
					return abs
				}
				return candidate
			}
		}
	}
	return ""
}

func zigSignalRunner(home string) SignalRunner {
	return func(ctx context.Context, argv []string, timeout time.Duration) (int, string, string, error) {
		if err := ctx.Err(); err != nil {
			return 1, "", "", err
		}
		key, err := secret.EnsureHostSigningKey(home)
		if err != nil {
			return 1, "", "", err
		}
		if err := core.EnsureSigningKey(key); err != nil {
			return 1, "", "", err
		}
		_ = core.WriteJailSetRoots(nil)
		token, nowMS, err := core.IssueProcessSpawnToken(argv, false)
		if err != nil {
			return 1, "", "", err
		}
		ms := uint32(timeout.Milliseconds())
		if ms == 0 {
			ms = 60_000
		}
		res, err := core.ExecCaptureAuthorized(argv, "", nil, token, "", "", false, nowMS, ms)
		if err != nil {
			return 1, "", "", err
		}
		stdout := string(res.Stdout)
		stderr := string(res.Stderr)
		if res.TimedOut {
			return 1, stdout, "timeout", nil
		}
		return int(res.ExitCode), stdout, stderr, nil
	}
}
