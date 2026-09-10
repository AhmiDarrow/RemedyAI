package tools

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
)

// delegate hands one self-contained mission to a coding agent that is already
// excellent at long builds (Claude Code headless), and stays the conductor
// while it runs: the sub-agent's stream-json events are mirrored into Remedy's
// own trail as they arrive, its final report becomes the tool result, and the
// spawn goes through spawnAuthorized like every other process Remedy starts —
// same deny classifier, capability token, write jail and kill-tree.

// ---------------------------------------------------------------------------
// Progress: the live seam between a tool and the turn's token stream
// ---------------------------------------------------------------------------

// ProgressKind is what a running tool is reporting mid-execution.
type ProgressKind string

const (
	// ProgressStatus is a line of narration for the owner (@@status:).
	ProgressStatus ProgressKind = "status"
	// ProgressToolCall is a tool the sub-agent invoked (@@tool_call:).
	ProgressToolCall ProgressKind = "tool_call"
	// ProgressToolResult is what that tool returned (@@tool_result:).
	ProgressToolResult ProgressKind = "tool_result"
)

// ProgressEvent is one mid-execution report from a long-running tool. Via
// names the sub-agent the event came from, so the surface can label the row as
// somebody else's work rather than Remedy's own.
type ProgressEvent struct {
	Kind    ProgressKind
	Via     string
	Text    string
	Name    string
	CallID  string
	Input   json.RawMessage
	Output  string
	IsError bool
}

// ProgressFunc receives ProgressEvents. It is called from the tool's own
// goroutines and must be safe for concurrent use.
type ProgressFunc func(ProgressEvent)

type progressKey struct{}

// WithProgress installs a progress sink for the tools executed under ctx. The
// runtime owns token formatting, so the sink lives at the emitting seam in the
// turn runner; the tool only says what happened. A context without a sink is
// valid — the tool simply runs without a live trail.
func WithProgress(ctx context.Context, fn ProgressFunc) context.Context {
	if ctx == nil || fn == nil {
		return ctx
	}
	return context.WithValue(ctx, progressKey{}, fn)
}

// EmitProgress delivers one event to the sink installed by WithProgress, if any.
func EmitProgress(ctx context.Context, event ProgressEvent) {
	if ctx == nil {
		return
	}
	fn, _ := ctx.Value(progressKey{}).(ProgressFunc)
	if fn == nil {
		return
	}
	fn(event)
}

// ---------------------------------------------------------------------------
// Bounds
// ---------------------------------------------------------------------------

const (
	// DelegateDepthEnv is set in the sub-agent's environment. Every process in
	// that tree inherits it, so a Remedy reached from inside a delegated run
	// refuses to delegate again instead of nesting harnesses.
	DelegateDepthEnv = "REMEDY_DELEGATE_DEPTH"

	delegateDefaultTimeoutMS = 1_800_000 // 30 minutes: a real coding mission
	delegateMaxTimeoutMS     = 7_200_000 // 2 hours, hard ceiling
	delegateMissionMaxBytes  = 32 << 10
	delegateStatusMaxBytes   = 2 << 10 // one mirrored assistant line
	delegateToolTextMax      = 8 << 10 // one mirrored tool input / result
	delegateLineMaxBytes     = 1 << 20 // one stream-json line
	delegateMaxMirrored      = 5_000   // mirrored events per run
	delegateReadyGrace       = 2 * time.Second
	delegateReapGrace        = 3 * time.Second
)

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------

// delegateAgent is one coding CLI Remedy knows how to drive headlessly.
type delegateAgent struct {
	// id is the value of the agent input field.
	id string
	// label is what the owner reads in the approval banner and the trail.
	label string
	// binary is the executable resolved on PATH.
	binary string
	// install is the actionable half of a missing-binary error.
	install string
	// argv builds the arguments after the resolved binary. A nil argv means
	// Remedy has no verified headless contract for this agent yet.
	argv func(mission string, allowShell bool) []string
}

// delegateAgents is the enum in the descriptor. Codex stays listed with no
// argv builder: the shape is settled, the headless contract is not verified,
// and saying so is better than guessing at its flags.
var delegateAgents = map[string]delegateAgent{
	"claude-code": {
		id:     "claude-code",
		label:  "Claude Code",
		binary: "claude",
		install: "install it from https://claude.com/claude-code (or `npm i -g @anthropic-ai/claude-code`), " +
			"then run `claude` once to sign in",
		argv: claudeCodeArgv,
	},
	"codex": {
		id:      "codex",
		label:   "Codex CLI",
		binary:  "codex",
		install: "install the Codex CLI and sign in",
	},
}

// delegateAgentIDs is the sorted enum, for schema and error text.
var delegateAgentIDs = []string{"claude-code", "codex"}

// delegateEditTools is the sub-agent surface a mission always gets: read the
// folder, change files, look things up. delegateShellTools is what allow_shell
// adds. Names are Claude Code's own tool names.
var (
	delegateEditTools  = []string{"Read", "Glob", "Grep", "Edit", "Write", "NotebookEdit", "TodoWrite", "WebFetch", "WebSearch"}
	delegateShellTools = []string{"Bash", "BashOutput", "KillShell"}
)

// claudeCodeArgv is the headless invocation: print mode, streamed JSON events
// (which --verbose is required for), edits accepted without a prompt because
// Remedy already asked the owner for this whole mission, and a tool allowlist
// that is the actual permission boundary. With allow_shell false the shell
// tools are both left out of --allowedTools and named in --disallowedTools, so
// a project- or user-level Claude Code setting cannot widen the run.
// Long options take the --flag=value form. It is the CLI's own spelling, and
// it keeps each option one argv token: the Zig deny classifier scans the
// joined argv, where "--output-format stream-json" reads as the disk command
// "format ", and the whole spawn is refused.
func claudeCodeArgv(mission string, allowShell bool) []string {
	allowed := append([]string(nil), delegateEditTools...)
	if allowShell {
		allowed = append(allowed, delegateShellTools...)
	}
	argv := []string{
		"-p", mission,
		"--output-format=stream-json",
		"--verbose",
		"--permission-mode=acceptEdits",
		"--allowedTools=" + strings.Join(allowed, ","),
	}
	if !allowShell {
		argv = append(argv, "--disallowedTools="+strings.Join(delegateShellTools, ","))
	}
	return append(argv, "--append-system-prompt="+delegateSystemPrompt(allowShell))
}

// delegateSystemPrompt tells the sub-agent the two things its own harness
// cannot: nobody is at the keyboard, and the last message is the report Remedy
// will read back to the owner.
func delegateSystemPrompt(allowShell bool) string {
	lines := []string{
		"You were started by Remedy, which is handing you this mission on the owner's behalf.",
		"Nobody can answer questions: decide, act, and keep going until the mission is done or truly blocked.",
		"Work only inside the folder you were started in.",
	}
	if !allowShell {
		lines = append(lines, "Shell commands are disabled for this run — do the work with the file tools.")
	}
	lines = append(lines, "End with a short report: what you changed, how you verified it, and anything you could not do.")
	return strings.Join(lines, " ")
}

// ---------------------------------------------------------------------------
// Executor
// ---------------------------------------------------------------------------

func executeDelegate(ctx context.Context, request Request) (Result, error) {
	var body struct {
		Mission       string   `json:"mission"`
		Cwd           string   `json:"cwd"`
		Agent         string   `json:"agent"`
		AllowShell    bool     `json:"allow_shell"`
		TimeoutMS     uint32   `json:"timeout_ms"`
		WorkspaceRoot string   `json:"workspace_root"`
		WriteRoots    []string `json:"write_roots"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, fmt.Errorf("%w: %v", ErrInvalidInput, err)
	}
	if depth := strings.TrimSpace(os.Getenv(DelegateDepthEnv)); depth != "" {
		return Result{}, fmt.Errorf(
			"%w: this run is itself a delegated agent (%s=%s), and a delegated agent may not delegate again. "+
				"Do the work here, or report back so the mission's owner can decide",
			ErrInvalidInput, DelegateDepthEnv, depth)
	}
	mission := strings.TrimSpace(body.Mission)
	if mission == "" {
		return Result{}, fmt.Errorf("%w: mission is required — say what to change, where, and how it will be verified", ErrInvalidInput)
	}
	if len(mission) > delegateMissionMaxBytes {
		return Result{}, fmt.Errorf("%w: the mission is %d bytes, more than the %d a hand-off carries — "+
			"put the detail in a file and point at it", ErrInvalidInput, len(mission), delegateMissionMaxBytes)
	}
	agent, err := resolveDelegateAgent(body.Agent)
	if err != nil {
		return Result{}, err
	}
	binary, err := resolveDelegateBinary(agent)
	if err != nil {
		return Result{}, err
	}
	workdir, err := delegateWorkdir(body.Cwd, body.WorkspaceRoot)
	if err != nil {
		return Result{}, err
	}
	env := delegateChildEnv()
	if bad := modelDeniedEnvKey(env); bad != "" {
		return Result{}, fmt.Errorf("delegate: %s must never be in the sub-agent environment", bad)
	}
	timeout := delegateTimeout(body.TimeoutMS)
	argv := append([]string{binary}, agent.argv(mission, body.AllowShell)...)

	EmitProgress(ctx, ProgressEvent{
		Kind: ProgressStatus, Via: agent.id,
		Text: fmt.Sprintf("Handing the mission to %s in %s…", agent.label, workdir),
	})
	proc, err := spawnAuthorized(ctx, shellSpawnSpec{
		argv:       argv,
		cwd:        workdir,
		env:        env,
		writeRoots: body.WriteRoots,
	})
	if err != nil {
		if strings.Contains(err.Error(), "access denied") {
			return Result{}, fmt.Errorf(
				"%s was refused by the command classifier before it started. "+
					"The mission text travels on the command line, so wording that reads like a "+
					"privileged command (disk, registry, account or service commands) refuses the whole "+
					"hand-off — reword it or put the detail in a file: %w", agent.label, err)
		}
		return Result{}, fmt.Errorf("%s could not be started: %w", agent.label, err)
	}
	run := runDelegate(ctx, proc, agent, timeout)
	if run.err != nil {
		return Result{}, run.err
	}
	report := strings.TrimSpace(run.report)
	if run.timedOut {
		return Result{}, fmt.Errorf("%s did not finish within %s and its process tree was killed. %s",
			agent.label, timeout, delegateTail(report, run.stderr))
	}
	if run.exitCode != 0 {
		return Result{}, fmt.Errorf("%s stopped with exit code %d. %s",
			agent.label, run.exitCode, delegateTail(report, run.stderr))
	}
	if run.isError {
		return Result{}, fmt.Errorf("%s ended the mission unfinished. %s",
			agent.label, delegateTail(report, run.stderr))
	}
	if report == "" {
		report = "(the agent finished without a closing report)"
	}
	clipped, truncated := clipHeadTail([]byte(report))
	EmitProgress(ctx, ProgressEvent{
		Kind: ProgressStatus, Via: agent.id,
		Text: fmt.Sprintf("%s finished the mission.", agent.label),
	})
	payload := map[string]any{
		"report":    clipped,
		"agent":     agent.id,
		"exit_code": int64(run.exitCode),
		"cwd":       filepath.ToSlash(workdir),
	}
	if truncated {
		payload["truncated"] = true
	}
	if run.sessionID != "" {
		payload["session_id"] = run.sessionID
	}
	if run.durationMS > 0 {
		payload["duration_ms"] = run.durationMS
	}
	out, err := json.Marshal(payload)
	return Result{Output: out}, err
}

// delegateTail is the failure body: the agent's own words first, its stderr
// only when it said nothing.
func delegateTail(report, stderr string) string {
	if report != "" {
		clipped, _ := clipHeadTail([]byte(report))
		return clipped
	}
	if s := strings.TrimSpace(stderr); s != "" {
		clipped, _ := clipHeadTail([]byte(s))
		return clipped
	}
	return "It produced no report."
}

func resolveDelegateAgent(raw string) (delegateAgent, error) {
	id := strings.ToLower(strings.TrimSpace(raw))
	if id == "" {
		id = "claude-code"
	}
	agent, known := delegateAgents[id]
	if !known {
		return delegateAgent{}, fmt.Errorf("%w: %q is not an agent Remedy knows — use one of %s",
			ErrInvalidInput, raw, strings.Join(delegateAgentIDs, ", "))
	}
	if agent.argv == nil {
		return delegateAgent{}, fmt.Errorf("%w: Remedy cannot drive %s yet — its headless contract is not wired up. Use agent claude-code",
			ErrInvalidInput, agent.label)
	}
	return agent, nil
}

// resolveDelegateBinary finds the agent on PATH. A missing agent is an
// ordinary tool failure that names what to install, not a crash.
func resolveDelegateBinary(agent delegateAgent) (string, error) {
	found, err := lookPath(agent.binary)
	if err != nil {
		return "", fmt.Errorf("%w: %s is not installed on this machine (no %q on PATH) — %s",
			ErrInvalidInput, agent.label, agent.binary, agent.install)
	}
	abs, err := filepath.Abs(found)
	if err != nil {
		return "", fmt.Errorf("%w: %s at %q could not be resolved: %v", ErrInvalidInput, agent.label, found, err)
	}
	return abs, nil
}

// delegateWorkdir is the folder the sub-agent is started in: the bound project
// folder, or a folder inside it when the model asked for one. Outside the
// binding is refused here as well as by the write jail, because the error the
// model can act on is this one.
func delegateWorkdir(cwd, workspaceRoot string) (string, error) {
	root := strings.TrimSpace(workspaceRoot)
	if root != "" {
		abs, err := filepath.Abs(root)
		if err != nil {
			return "", fmt.Errorf("%w: the bound project folder %q is not usable: %v", ErrInvalidInput, root, err)
		}
		root = filepath.Clean(abs)
	}
	requested := strings.TrimSpace(cwd)
	if requested == "" {
		if root == "" {
			return "", fmt.Errorf("%w: no project folder is bound for this turn, so there is nowhere to run the mission. "+
				"Open a project folder first", ErrInvalidInput)
		}
		return root, requireDelegateDir(root)
	}
	if !filepath.IsAbs(requested) {
		return "", fmt.Errorf("%w: cwd must be an absolute path when set (got %q)", ErrInvalidInput, requested)
	}
	abs := filepath.Clean(requested)
	if root != "" {
		if _, inside := relUnder(root, abs); !inside {
			return "", fmt.Errorf("%w: cwd %q is outside the bound project folder %q — a delegated agent works inside the project",
				ErrInvalidInput, abs, root)
		}
	}
	return abs, requireDelegateDir(abs)
}

func requireDelegateDir(dir string) error {
	info, err := os.Stat(dir)
	if err != nil {
		return fmt.Errorf("%w: the folder %q could not be opened: %v", ErrInvalidInput, dir, err)
	}
	if !info.IsDir() {
		return fmt.Errorf("%w: %q is not a folder", ErrInvalidInput, dir)
	}
	return nil
}

// delegateEnvHijackKeys and delegateEnvHijackPrefixes are the loader and
// startup hooks that make a benign executable run somebody else's code first.
// They mirror policy.zig's env_denied_exact / env_denied_prefixes, which
// refuses them as spawn overrides; dropping them here means the sub-agent
// simply never has them.
var (
	delegateEnvHijackKeys = []string{
		"LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT",
		"GIT_SSH_COMMAND", "GIT_EXEC_PATH", "BROWSER",
		"PYTHONSTARTUP", "PERL5OPT", "NODE_OPTIONS", "RUBYOPT",
		"BASH_ENV", "ENV", "PROMPT_COMMAND",
	}
	delegateEnvHijackPrefixes = []string{"DYLD_"}
)

// delegateChildEnv is the environment the sub-agent runs in, built explicitly
// rather than inherited: a coding agent needs a real environment (PATH, the
// user profile, its own credentials), and this is where the interpreter and
// loader hooks come out of it. The delegation marker goes in last, so a Remedy
// reached from inside this tree refuses to delegate again.
func delegateChildEnv() map[string]string {
	env := make(map[string]string, 64)
	for _, entry := range os.Environ() {
		key, value, ok := strings.Cut(entry, "=")
		if !ok || strings.TrimSpace(key) == "" {
			continue
		}
		if delegateEnvScrubbed(key) {
			continue
		}
		env[key] = value
	}
	env[DelegateDepthEnv] = "1"
	return env
}

func delegateEnvScrubbed(key string) bool {
	if strings.EqualFold(key, DelegateDepthEnv) {
		return true
	}
	if modelDeniedEnvKey(map[string]string{key: ""}) != "" {
		return true
	}
	for _, denied := range delegateEnvHijackKeys {
		if strings.EqualFold(key, denied) {
			return true
		}
	}
	for _, prefix := range delegateEnvHijackPrefixes {
		if len(key) >= len(prefix) && strings.EqualFold(key[:len(prefix)], prefix) {
			return true
		}
	}
	return false
}

func delegateTimeout(ms uint32) time.Duration {
	if ms == 0 {
		ms = delegateDefaultTimeoutMS
	}
	if ms > delegateMaxTimeoutMS {
		ms = delegateMaxTimeoutMS
	}
	return time.Duration(ms) * time.Millisecond
}

// ---------------------------------------------------------------------------
// Running one sub-agent
// ---------------------------------------------------------------------------

type delegateRun struct {
	report     string
	sessionID  string
	durationMS int64
	exitCode   uint32
	isError    bool
	timedOut   bool
	stderr     string
	err        error
}

// runDelegate streams the sub-agent's stream-json output while it works and
// waits for it to exit, the timeout, or cancellation. Timeout and cancellation
// both kill the whole process tree: a delegated mission that is stopped must
// not leave an agent editing files behind Remedy's back.
func runDelegate(ctx context.Context, proc core.PipedProcess, agent delegateAgent, timeout time.Duration) delegateRun {
	stdin, stdout, stderr := proc.Files()
	_ = stdin.Close() // headless: the sub-agent never gets interactive input

	parsed := make(chan delegateStream, 1)
	go func() { parsed <- consumeDelegateStream(ctx, agent, stdout) }()

	var errBuf boundedBuffer
	drained := make(chan struct{})
	go func() { defer close(drained); _, _ = io.Copy(&errBuf, stderr) }()

	type exitStatus struct {
		code uint32
		err  error
	}
	exited := make(chan exitStatus, 1)
	go func() {
		for {
			code, err := core.ProcessWait(proc.Handle, 100)
			if err != nil {
				exited <- exitStatus{err: err}
				return
			}
			if code != nil {
				exited <- exitStatus{code: *code}
				return
			}
		}
	}()

	finish := func(run delegateRun) delegateRun {
		// Pipe EOF follows the last writer in the job; give stragglers a
		// bounded grace, then close our ends and report what we have.
		var stream delegateStream
		select {
		case stream = <-parsed:
		case <-time.After(delegateReadyGrace):
		}
		select {
		case <-drained:
		case <-time.After(delegateReadyGrace):
		}
		_ = stdout.Close()
		_ = stderr.Close()
		_ = core.ProcessClose(proc.Handle)
		run.report = stream.report
		run.sessionID = stream.sessionID
		run.durationMS = stream.durationMS
		run.isError = run.isError || stream.isError
		run.stderr = string(errBuf.Bytes("stderr"))
		return run
	}
	killAndReap := func() {
		_ = core.ProcessKillTree(proc.PID)
		select {
		case <-exited:
		case <-time.After(delegateReapGrace):
		}
	}

	timer := time.NewTimer(timeout)
	defer timer.Stop()
	select {
	case st := <-exited:
		if st.err != nil {
			return finish(delegateRun{err: fmt.Errorf("%s could not be waited on: %w", agent.label, st.err)})
		}
		return finish(delegateRun{exitCode: st.code})
	case <-timer.C:
		killAndReap()
		return finish(delegateRun{exitCode: 1, timedOut: true})
	case <-ctx.Done():
		killAndReap()
		return finish(delegateRun{err: fmt.Errorf("%s: %w", agent.label, ctx.Err())})
	}
}

// ---------------------------------------------------------------------------
// stream-json
// ---------------------------------------------------------------------------

// delegateStream is what one run's event stream told us.
type delegateStream struct {
	report     string
	sessionID  string
	durationMS int64
	isError    bool
}

// delegateEvent is the subset of Claude Code's stream-json envelope Remedy
// reads. Unknown types and fields are ignored on purpose: the sub-agent's
// stream is allowed to grow without breaking the hand-off.
type delegateEvent struct {
	Type       string          `json:"type"`
	Subtype    string          `json:"subtype"`
	SessionID  string          `json:"session_id"`
	Model      string          `json:"model"`
	IsError    bool            `json:"is_error"`
	DurationMS int64           `json:"duration_ms"`
	Result     json.RawMessage `json:"result"`
	Error      json.RawMessage `json:"error"`
	Message    struct {
		Role    string `json:"role"`
		Content []struct {
			Type      string          `json:"type"`
			Text      string          `json:"text"`
			ID        string          `json:"id"`
			Name      string          `json:"name"`
			Input     json.RawMessage `json:"input"`
			ToolUseID string          `json:"tool_use_id"`
			Content   json.RawMessage `json:"content"`
			IsError   bool            `json:"is_error"`
		} `json:"content"`
	} `json:"message"`
}

// consumeDelegateStream mirrors the sub-agent's events into Remedy's trail as
// they arrive — assistant text as status, its tool activity as tool tokens
// tagged with the agent it came from — and keeps the closing report.
func consumeDelegateStream(ctx context.Context, agent delegateAgent, r io.Reader) delegateStream {
	var (
		stream   delegateStream
		mirrored int
		toolName = map[string]string{}
	)
	emit := func(event ProgressEvent) {
		if mirrored >= delegateMaxMirrored {
			return
		}
		mirrored++
		event.Via = agent.id
		EmitProgress(ctx, event)
	}
	reader := bufio.NewReaderSize(r, 64<<10)
	for {
		line, ok := readDelegateLine(reader)
		if !ok {
			break
		}
		line = strings.TrimSpace(line)
		if line == "" || !strings.HasPrefix(line, "{") {
			continue
		}
		var event delegateEvent
		if err := json.Unmarshal([]byte(line), &event); err != nil {
			continue
		}
		switch event.Type {
		case "system":
			if event.Subtype == "init" {
				stream.sessionID = event.SessionID
				text := fmt.Sprintf("%s is working", agent.label)
				if event.Model != "" {
					text += " (" + event.Model + ")"
				}
				emit(ProgressEvent{Kind: ProgressStatus, Text: text + "…"})
			}
		case "assistant":
			for _, block := range event.Message.Content {
				switch block.Type {
				case "text":
					if text := delegateStatusText(block.Text); text != "" {
						emit(ProgressEvent{Kind: ProgressStatus, Text: agent.label + ": " + text})
					}
				case "tool_use":
					if block.ID != "" && block.Name != "" {
						toolName[block.ID] = block.Name
					}
					emit(ProgressEvent{
						Kind:   ProgressToolCall,
						Name:   block.Name,
						CallID: block.ID,
						Input:  clipDelegateJSON(block.Input),
					})
				}
			}
		case "user":
			for _, block := range event.Message.Content {
				if block.Type != "tool_result" {
					continue
				}
				name := toolName[block.ToolUseID]
				if name == "" {
					name = "tool"
				}
				emit(ProgressEvent{
					Kind:    ProgressToolResult,
					Name:    name,
					CallID:  block.ToolUseID,
					Output:  clipDelegateText(delegateBlockText(block.Content), delegateToolTextMax),
					IsError: block.IsError,
				})
			}
		case "result":
			if event.SessionID != "" {
				stream.sessionID = event.SessionID
			}
			stream.durationMS = event.DurationMS
			stream.isError = event.IsError || (event.Subtype != "" && event.Subtype != "success")
			report := delegateBlockText(event.Result)
			if strings.TrimSpace(report) == "" {
				report = delegateBlockText(event.Error)
			}
			if strings.TrimSpace(report) == "" && event.Subtype != "" {
				report = "the agent stopped: " + event.Subtype
			}
			stream.report = report
		}
	}
	return stream
}

// readDelegateLine reads one newline-terminated record, bounded so a single
// enormous event cannot be buffered without limit. An over-long line is
// truncated and the rest of it discarded; the stream stays in sync.
func readDelegateLine(r *bufio.Reader) (string, bool) {
	var (
		b        strings.Builder
		overflow bool
	)
	for {
		chunk, err := r.ReadString('\n')
		if !overflow {
			if b.Len()+len(chunk) > delegateLineMaxBytes {
				overflow = true
			} else {
				b.WriteString(chunk)
			}
		}
		if err != nil {
			return b.String(), b.Len() > 0
		}
		if strings.HasSuffix(chunk, "\n") {
			return b.String(), true
		}
	}
}

// delegateBlockText renders a stream-json content field, which is a string for
// the simple case and a block array for the rich one.
func delegateBlockText(raw json.RawMessage) string {
	trimmed := strings.TrimSpace(string(raw))
	if trimmed == "" || trimmed == "null" {
		return ""
	}
	var text string
	if err := json.Unmarshal(raw, &text); err == nil {
		return text
	}
	var blocks []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}
	if err := json.Unmarshal(raw, &blocks); err == nil {
		parts := make([]string, 0, len(blocks))
		for _, block := range blocks {
			if strings.TrimSpace(block.Text) != "" {
				parts = append(parts, block.Text)
			}
		}
		if len(parts) > 0 {
			return strings.Join(parts, "\n")
		}
	}
	return trimmed
}

// delegateStatusText flattens one assistant paragraph into a single status
// line. @@ tokens are line-oriented, so a mirrored newline would look like the
// end of the status.
func delegateStatusText(text string) string {
	return clipDelegateText(strings.Join(strings.Fields(text), " "), delegateStatusMaxBytes)
}

func clipDelegateText(text string, limit int) string {
	if len(text) <= limit {
		return text
	}
	return text[:runeFloorAt([]byte(text), limit)] + "…"
}

// clipDelegateJSON bounds a mirrored tool input. An input too large to carry
// becomes a note, never a broken JSON fragment.
func clipDelegateJSON(raw json.RawMessage) json.RawMessage {
	if len(raw) == 0 {
		return nil
	}
	if len(raw) <= delegateToolTextMax {
		return raw
	}
	note, err := json.Marshal(map[string]any{
		"_truncated": fmt.Sprintf("%d bytes of tool input omitted", len(raw)),
	})
	if err != nil {
		return nil
	}
	return note
}
