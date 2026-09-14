package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// runtimeLocalAuthorizer accepts any non-empty capability token for
// process-local turn execution.
//
// This is a wiring check, not a credential check, and it is deliberately not
// one. The token is stamped by the executor and never comes from model input,
// so it cannot be forged by a prompt; and the only other way to present one is
// to write RMDY frames into the worker pipe, which is created current-user-only
// with a random name — an attacker who can do that already runs as the owner
// and does not need Remedy's tools. What this gate catches is a caller that
// forgot to ask for a token at all, which is a bug, not an attack. The real
// boundaries for a protected tool are the policy decision, the owner approval,
// the write jail, and the Zig capability token that guards a spawn (that one
// IS signed and binds argv and environment — see native/zig/src/spawn_auth.zig).
func runtimeLocalAuthorizer(_ context.Context, _ tools.Descriptor, _ tools.Request) error {
	return nil
}

// RuntimeCapabilityToken marks a request as coming from the turn executor.
// See runtimeLocalAuthorizer for why this is a fixed value and what actually
// guards a protected tool.
func RuntimeCapabilityToken(desc tools.Descriptor) []byte {
	protected := desc.Risk != tools.RiskReadOnly || len(desc.Capabilities) != 0 || len(desc.Permissions) != 0
	if !protected {
		return nil
	}
	return []byte("runtime-local")
}

// Per-tool execution bounds. The shell tools own their timeout (timeout_ms,
// up to 10 minutes) so they get none here.
const (
	promptToolDeadline  = 30 * time.Second
	webToolDeadline     = 90 * time.Second
	defaultToolDeadline = 120 * time.Second
)

// deadlineForTool is the default Execute bound for a tool id. Descriptors
// registered with their own Deadline keep it; this rule fills the rest.
func deadlineForTool(id string) time.Duration {
	id = strings.TrimSpace(id)
	switch {
	case id == "shell.exec", id == "bash", id == "delegate":
		// These own their timeout (timeout_ms) and kill their process tree
		// when it expires. A delegated mission runs for tens of minutes; a
		// runtime deadline here would abandon it mid-build.
		return 0
	case strings.HasPrefix(id, "prompt."):
		return promptToolDeadline
	case strings.HasPrefix(id, "web."):
		return webToolDeadline
	default:
		return defaultToolDeadline
	}
}

// RegistryToolExecutor adapts the Tool ABI registry to cognition.ToolExecutor.
type RegistryToolExecutor struct {
	Registry *tools.Registry
	// TokenFor supplies capability tokens for protected tools. Nil is valid when
	// every registered tool is unrestricted read-only.
	TokenFor func(tools.Descriptor) []byte
}

// Execute runs one tool call under its descriptor deadline. A parent
// cancellation yields Err "cancelled"; a per-tool deadline yields
// "tool timed out after Ns". An executor that ignores its context is not
// waited on past the deadline — the call is reported and the executor's late
// result is discarded.
// toolCancelGrace bounds how long a cancelled tool may take to finish its own
// cleanup before the executor gives up waiting on it.
const toolCancelGrace = 10 * time.Second

func (e *RegistryToolExecutor) Execute(ctx context.Context, call cognition.ToolCall) cognition.ToolResult {
	if e == nil || e.Registry == nil {
		return cognition.ToolResult{ID: call.ID, Name: call.Name, Err: "tool registry is not configured"}
	}
	desc, err := e.Registry.Latest(call.Name)
	if err != nil {
		return cognition.ToolResult{ID: call.ID, Name: call.Name, Err: err.Error()}
	}
	input := json.RawMessage(call.Input)
	if len(input) == 0 {
		input = json.RawMessage(`{}`)
	}
	req := tools.Request{
		ToolID:  desc.ID,
		Version: desc.Version,
		Input:   append(json.RawMessage(nil), input...),
	}
	if e.TokenFor != nil {
		req.CapabilityToken = e.TokenFor(desc)
	}
	deadline := desc.Deadline
	if deadline == 0 {
		deadline = deadlineForTool(desc.ID)
	}
	toolCtx := ctx
	if deadline > 0 {
		var cancel context.CancelFunc
		toolCtx, cancel = context.WithTimeout(ctx, deadline)
		defer cancel()
	}
	type outcome struct {
		result tools.Result
		err    error
	}
	done := make(chan outcome, 1)
	go func() {
		result, err := e.Registry.Execute(toolCtx, req)
		done <- outcome{result: result, err: err}
	}()
	var out outcome
	select {
	case out = <-done:
	case <-toolCtx.Done():
		// The tool owns its own cancellation cleanup (kill the process tree,
		// close handles). Give it a bounded grace period so that finishes
		// before the turn moves on — abandoning the goroutine would leave a
		// child process running and the cleanup calling into the host after
		// the caller is gone.
		select {
		case out = <-done:
		case <-time.After(toolCancelGrace):
			out.err = toolCtx.Err()
		}
	}
	if out.err != nil {
		return cognition.ToolResult{ID: call.ID, Name: call.Name, Err: describeToolFailure(ctx, toolCtx, deadline, out.err)}
	}
	return cognition.ToolResult{
		ID:     call.ID,
		Name:   call.Name,
		Output: append([]byte(nil), out.result.Output...),
		Blocks: toolImageBlocks(out.result.Images),
	}
}

// toolImageBlocks lifts a tool's binary images (screenshot, read of an image
// file) into transcript image blocks. The bytes never pass through the JSON
// output, so the model pays for the picture once.
func toolImageBlocks(images []tools.ImageResult) []cognition.Block {
	if len(images) == 0 {
		return nil
	}
	blocks := make([]cognition.Block, 0, len(images))
	for _, img := range images {
		if len(img.Data) == 0 {
			continue
		}
		mediaType := strings.TrimSpace(img.MediaType)
		if mediaType == "" {
			mediaType = "image/png"
		}
		blocks = append(blocks, cognition.ImageBlock(mediaType, append([]byte(nil), img.Data...)))
	}
	return blocks
}

func describeToolFailure(parent, toolCtx context.Context, deadline time.Duration, err error) string {
	switch {
	case parent.Err() != nil && errors.Is(parent.Err(), context.Canceled):
		return "cancelled"
	case deadline > 0 && errors.Is(toolCtx.Err(), context.DeadlineExceeded):
		return fmt.Sprintf("tool timed out after %ds", int64(deadline/time.Second))
	case errors.Is(err, context.Canceled):
		return "cancelled"
	default:
		return err.Error()
	}
}

// RegistryPolicy allows registered read-only tools, asks for mutation/checkpoint,
// and denies unknown names. No silent allow for unregistered tools.
//
// Approval mode (ask/auto/full) matches the Python partner trust loop:
// coding mutations run under Auto/Full; Ask enqueues for the owner banner;
// RiskCheckpoint (money/credentials/send) always asks — no mode waives it.
// A mutation whose call shape is a payment / credential step
// (isSensitiveComputerAction) is treated exactly like RiskCheckpoint.
type RegistryPolicy struct {
	Registry  *tools.Registry
	Approvals *approvalQueue
	SessionID string
	// ForceAsk makes every RiskMutation decision Ask regardless of approval
	// mode. Set for untrusted origins (messenger, phone, hive) — see OriginIsOwner.
	ForceAsk bool
	// HiveRestricted denies computer input, mail, calendar and hive-control
	// tools. A daughter may read and search; it may not click, type, or send.
	HiveRestricted bool
	// LiveContext is the host-observed URL and labels for this session. The
	// model-supplied page_context field is ignored — a checkout click without
	// this probe is an owner moment.
	LiveContext func(sessionID string) string
}

func (p *RegistryPolicy) livePageContext() string {
	if p == nil || p.LiveContext == nil {
		return ""
	}
	return strings.TrimSpace(p.LiveContext(p.SessionID))
}

func (p *RegistryPolicy) Decide(_ context.Context, call cognition.ToolCall) cognition.Decision {
	if p == nil || p.Registry == nil {
		return cognition.Deny
	}
	if p.HiveRestricted && hiveDeniedTool(call.Name) {
		return cognition.Deny
	}
	desc, err := p.Registry.Latest(call.Name)
	if err != nil {
		return cognition.Deny
	}
	switch desc.Risk {
	case tools.RiskReadOnly:
		return cognition.Allow
	case tools.RiskCheckpoint:
		return p.decideOwnerMoment(call)
	case tools.RiskMutation:
		if callIsReadOnlyAction(call) {
			return cognition.Allow
		}
		if classifySensitiveComputer(call, p.livePageContext()) {
			return p.decideOwnerMoment(call)
		}
		mode := "ask"
		if p.Approvals != nil {
			mode = p.Approvals.Mode()
		}
		if !p.ForceAsk && (mode == "auto" || mode == "full") {
			return cognition.Allow
		}
		if p.Approvals != nil && p.Approvals.IsApproved(call.Name, toolCommandPreview(call), p.SessionID, !p.ForceAsk) {
			return cognition.Allow
		}
		return cognition.Ask
	default:
		return cognition.Deny
	}
}

// callIsReadOnlyAction reports a call that only reads, on a tool whose
// descriptor risk is the highest thing it can do. `jobs` is registered
// RiskMutation because kill terminates a process tree, which means the registry
// demands a capability token for every jobs call; list and tail still only
// read, and asking the owner to approve reading a log would train them to
// approve without looking.
func callIsReadOnlyAction(call cognition.ToolCall) bool {
	if strings.TrimSpace(call.Name) != "jobs" {
		return false
	}
	var body struct {
		Action string `json:"action"`
	}
	if len(call.Input) == 0 || json.Unmarshal(call.Input, &body) != nil {
		return false
	}
	action := strings.TrimSpace(body.Action)
	return action == "list" || action == "tail"
}

// decideOwnerMoment is the non-waivable path (pay / credentials / irreversible
// send): only a one-shot grant for this exact call lets it through.
func (p *RegistryPolicy) decideOwnerMoment(call cognition.ToolCall) cognition.Decision {
	if p.Approvals != nil && p.Approvals.IsOneShotApproved(call.Name, toolCommandPreview(call), p.SessionID, call.ID) {
		return cognition.Allow
	}
	return cognition.Ask
}

// toolCommandPreviewLimit bounds the audited command text. It is the approval
// fingerprint and the text the sensitivity predicates see, so it is generous;
// the banner clips it further for display.
const toolCommandPreviewLimit = 8192

// toolCommandPreview is the command text an approval is keyed on. Truncation
// never splits a UTF-8 sequence.
func toolCommandPreview(call cognition.ToolCall) string {
	if len(call.Input) == 0 {
		return call.Name
	}
	return clipUTF8(strings.TrimSpace(string(call.Input)), toolCommandPreviewLimit)
}

// serverOwnedInputFields are set by the runtime from the session, never by
// the model or an API caller: they choose the jail root, the shell's write
// roots and the owner home the Python worker reads.
var serverOwnedInputFields = []string{"home_dir", "workspace_root", "project_path", "write_roots", "owner_confirmed", "page_context"}

// toolBinding is what the runtime, not the model, decides about a tool call:
// which folder it is jailed to, how far outside it the shell may reach, and
// which session's job list and checklist it writes.
type toolBinding struct {
	Root        string
	Scope       string
	HomeDir     string
	SessionID   string
	PageContext string
}

// fileToolIDs and sessionToolIDs are the frontier surface's Go-native tools.
// They take their binding from the runtime the same way workspace.* and
// shell.exec always have.
var (
	fileToolIDs    = map[string]struct{}{"read": {}, "edit": {}, "write": {}, "glob": {}, "grep": {}}
	sessionToolIDs = map[string]struct{}{"jobs": {}, "todo": {}}
)

// bindToolInput strips server-owned fields from a call's input and re-injects
// the session binding: file tools and workspace.* get the root, bash /
// shell.exec get cwd and write_roots for the access scope, delegate gets the
// root it must start its sub-agent inside plus those write roots, memory.* /
// skill.* get project_path, and the session tools get home_dir and session_id.
// Shared by the engine path and POST /api/tools/invoke so neither surface can
// be steered to another jail by supplying workspace_root or home_dir.
func bindToolInput(call cognition.ToolCall, b toolBinding) cognition.ToolCall {
	call.Input = stripServerOwnedFields(call.Input)
	name := strings.TrimSpace(call.Name)
	root := strings.TrimSpace(b.Root)

	isShell := name == "bash" || name == "shell.exec" || name == "shell_exec"
	_, isSessionTool := sessionToolIDs[name]
	isRail := name == "computer.navigate" || name == "computer_navigate" ||
		name == "computer.snapshot" || name == "computer_snapshot"
	isComputer := strings.HasPrefix(name, "computer.") || strings.HasPrefix(name, "computer_")
	// shell.exec has no session in its schema; bash needs one for background
	// jobs, and the session tools are named for it. Rail tools take the same
	// session so a model-supplied session_id cannot drive another chat's browser.
	if isSessionTool || name == "bash" || isRail {
		call.Input = injectSessionBinding(call.Input, b.HomeDir, b.SessionID)
	}
	if isComputer {
		live := strings.TrimSpace(b.PageContext)
		if live != "" {
			call.Input = injectSingleField(call.Input, "page_context", live)
		}
	}
	if root == "" {
		return call
	}
	_, isFileTool := fileToolIDs[name]
	switch {
	case isFileTool:
		call.Input = injectSingleField(call.Input, "workspace_root", root)
	case name == "delegate":
		// The sub-agent is started in the bound project folder (or a folder
		// inside it) and spawns under the session's write jail, exactly like
		// bash. The model never chooses either.
		call.Input = injectSingleField(call.Input, "workspace_root", root)
		call.Input = injectShellWriteRoots(call.Input, root, b.Scope)
	case strings.HasPrefix(name, "workspace.") || strings.HasPrefix(name, "workspace_"):
		call.Input = injectWorkspaceRoot(call.Input, root)
	case isShell:
		call.Input = injectShellCwd(call.Input, root, b.Scope)
		call.Input = injectShellWriteRoots(call.Input, root, b.Scope)
	case strings.HasPrefix(name, "memory.") || strings.HasPrefix(name, "memory_") ||
		strings.HasPrefix(name, "skill.") || strings.HasPrefix(name, "skill_"):
		call.Input = injectProjectPathField(call.Input, root)
	}
	return call
}

// injectSessionBinding sets home_dir and session_id from the runtime. Both are
// overwritten, never merged: a background job or a checklist must land in the
// session that actually asked for it.
func injectSessionBinding(raw []byte, home, sessionID string) []byte {
	args := map[string]any{}
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &args); err != nil {
			return raw
		}
	}
	if home = strings.TrimSpace(home); home != "" {
		args["home_dir"] = home
	} else {
		delete(args, "home_dir")
	}
	if sessionID = strings.TrimSpace(sessionID); sessionID != "" {
		args["session_id"] = sessionID
	} else {
		delete(args, "session_id")
	}
	b, err := json.Marshal(args)
	if err != nil {
		return raw
	}
	return b
}

func injectSingleField(raw []byte, field, value string) []byte {
	args := map[string]any{}
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &args); err != nil {
			return raw
		}
	}
	args[field] = value
	b, err := json.Marshal(args)
	if err != nil {
		return raw
	}
	return b
}

func stripServerOwnedFields(raw []byte) []byte {
	if len(raw) == 0 {
		return raw
	}
	args := map[string]any{}
	if err := json.Unmarshal(raw, &args); err != nil {
		return raw
	}
	changed := false
	for _, field := range serverOwnedInputFields {
		if _, ok := args[field]; ok {
			delete(args, field)
			changed = true
		}
	}
	if !changed {
		return raw
	}
	b, err := json.Marshal(args)
	if err != nil {
		return raw
	}
	return b
}

// Payment / credential predicates, ported from src/remedy/core/approvals.py
// so the Go path asks for the same owner moments as the Python path.
var (
	// Purchase finalization, money movement, payment credentials, and the rail
	// click labels that finalize send/delete/pay, plus vault handle use.
	sensitiveComputerRe = regexp.MustCompile(`(?is)\b(` +
		`place\s+(?:your\s+)?order|pay\s+now|buy\s+now|order\s+now|purchase\s+now|` +
		`confirm\s+(?:&\s*)?(?:order|purchase|payment|pay|subscription)|` +
		`complete\s+(?:order|purchase|payment|checkout)|` +
		`submit\s+(?:order|payment)|` +
		`start\s+(?:your\s+)?subscription|` +
		`send\s+money|transfer\s+funds|wire\s+transfer|donate\s+now|` +
		`card\s*number|cvv|cvc|security\s+code|` +
		`send|delete|pay|submit` +
		`)\b` +
		`|vault=|\{\{\s*vault:`)
	// Page-context signals that the live page is a checkout / payment surface.
	paymentSurfaceRe = regexp.MustCompile(`(?is)(` +
		`/(checkout|cart|payment|billing|pay|order|purchase|donate)\b` +
		`|checkout\.|\bcheckout\b|\bpayment\b|\bbilling\b` +
		`|place\s+order|pay\s+now|order\s+summary|order\s+total|` +
		`card\s*number|cardholder|cvv|cvc|expiry|billing\s+address` +
		`)`)
	// Checkout buttons whose copy is too short for sensitiveComputerRe but
	// still finalize a purchase on a payment surface.
	submitLabelRe = regexp.MustCompile(`(?is)\b(` +
		`submit|pay(?:ment)?|confirm|continue|complete|checkout|` +
		`place|buy|order|donate` +
		`)\b`)
	// CAPTCHA / press-and-hold walls: the owner completes those, not Remedy.
	challengeWallRe = regexp.MustCompile(`(?is)(` +
		`captcha|hcaptcha|recaptcha|turnstile|` +
		`cloudflare.{0,40}(challenge|captcha|turnstile|just.?a.?moment)|` +
		`(challenge|captcha|turnstile|just.?a.?moment).{0,40}cloudflare|` +
		`i.?m not a robot|press.?and.?hold|press.?&.?hold|` +
		`human.?check|verify you are human` +
		`)`)
	vaultHandleRe   = regexp.MustCompile(`\{\{\s*vault:`)
	cardCandidateRe = regexp.MustCompile(`(?:^|[^\d])((?:\d[ -]?){13,19})(?:[^\d]|$)`)
	passwordFieldRe = regexp.MustCompile(`(?is)\b(` +
		`password|passwd|passphrase|passcode|pin\b|otp|one[ -]?time|` +
		`2fa|mfa|totp|cvv|cvc|security\s+code|ssn|social\s+security` +
		`)\b`)

	// shell.exec argv shapes that move money or touch credentials.
	shellMoneyRe = regexp.MustCompile(`(?is)(` +
		`\b(pay\s*now|place\s+order|send\s+money|transfer\s+funds|wire\s+transfer|checkout|card\s*number|cvv|cvc)\b` +
		`|\bstripe\b.{0,80}\b(charges?|payment_intents?|payouts?|transfers?|refunds?)\b` +
		`|\bpaypal\b.{0,80}\b(pay|payment|payout|transfer)\b` +
		`|--amount[= ]` +
		`|\b(venmo|zelle|cashapp)\b` +
		`)`)
	shellCredentialRe = regexp.MustCompile(`(?is)(` +
		`\b(password|passwd|pwd|passphrase)\b\s*[=:]\s*\S` +
		`|--(password|passwd|token|api[-_]?key|secret)[= ]\S` +
		`|\b(api[_-]?key|secret[_-]?key|access[_-]?token|client[_-]?secret)\b\s*[=:]\s*\S{8,}` +
		`|\bnet\s+user\s+\S+\s+\S+` +
		`|\bcmdkey\s+/(add|generic)` +
		`|\bvaultcmd\b` +
		`|\bsecurity\s+find-(generic|internet)-password` +
		`|\bnetsh\s+wlan\s+show\s+profile.{0,80}key=clear` +
		`|\bgpg\b.{0,80}--export-secret` +
		`|\bssh-keygen\b` +
		`|\bopenssl\s+(genrsa|genpkey|ec\s+-genkey|pkcs12)\b` +
		`|\baws\s+configure\b|\bgcloud\s+auth\b|\baz\s+login\b` +
		`|\bkeytool\b.{0,80}-(store|key)pass` +
		`|\bchpasswd\b|\bpasswd\b` +
		`|[\\/]\.remedy[\\/]auth\b|[\\/]\.ssh[\\/]|[\\/]\.aws[\\/]credentials\b|[\\/]\.netrc\b` +
		`|\bid_(rsa|ed25519|ecdsa)\b` +
		`)`)
)

var submitKeys = map[string]struct{}{"enter": {}, "return": {}, "\n": {}, "space": {}, " ": {}}

// approvalIsSensitive reports whether a call must be queued as a sensitive
// (one-shot, mode-proof) approval. enqueueToolApproval consults it so the
// banner item carries Sensitive and SetMode sweeps leave it pending.
func approvalIsSensitive(call cognition.ToolCall) bool {
	return classifySensitiveComputer(call, "")
}

func isSensitiveComputerAction(call cognition.ToolCall) bool {
	return classifySensitiveComputer(call, "")
}

func toolNameDotted(name string) string {
	n := strings.ToLower(strings.TrimSpace(name))
	return strings.ReplaceAll(n, "_", ".")
}

// hiveDeniedTool is the mother-only surface a forager must never hold:
// computer input, mail, calendar, hive control. Read/search stay allowed.
// Unknown computer.* names fail closed so a new input verb cannot slip through.
func hiveDeniedTool(name string) bool {
	n := toolNameDotted(name)
	if strings.HasPrefix(n, "mail.") || strings.HasPrefix(n, "calendar.") ||
		strings.HasPrefix(n, "hive.") || strings.HasPrefix(n, "mcp.") {
		return true
	}
	if n == "clipboard.write" {
		return true
	}
	if strings.HasPrefix(n, "computer.") {
		switch n {
		case "computer.screenshot", "computer.print.window", "computer.windows",
			"computer.foreground", "computer.monitors", "computer.snapshot",
			"computer.uia.focused", "computer.uia.read.text":
			return false
		default:
			return true
		}
	}
	return false
}

// classifySensitiveComputer is the Go port of the Python payment /
// credential checkpoints. live is host-observed URL + labels; the model's
// page_context field is never read. An unlabeled click or submit key with
// no live probe is an owner moment (fail closed).
func classifySensitiveComputer(call cognition.ToolCall, live string) bool {
	name := toolNameDotted(call.Name)
	args := map[string]any{}
	if len(call.Input) > 0 {
		if err := json.Unmarshal(call.Input, &args); err != nil {
			raw := string(call.Input)
			switch name {
			case "shell.exec", "bash":
				return shellMoneyRe.MatchString(raw) || shellCredentialRe.MatchString(raw)
			case "computer.click", "computer.uia.action", "computer.key",
				"computer.type", "computer.key.hold", "computer.drag":
				return sensitiveComputerRe.MatchString(raw) || challengeWallRe.MatchString(raw)
			}
			return false
		}
	}
	switch name {
	case "shell.exec", "bash":
		return shellArgvIsSensitive(args)
	case "computer.click", "computer.uia.action", "computer.key",
		"computer.type", "computer.key.hold", "computer.drag":
	default:
		return false
	}
	label := strings.TrimSpace(asString(args["label"]))
	uiaName := strings.TrimSpace(asString(args["name"]))
	text := asString(args["text"])
	target := strings.TrimSpace(label + " " + uiaName)
	pageContext := strings.TrimSpace(live)
	blob := pageContext + " " + target

	if sensitiveComputerRe.MatchString(target) {
		return true
	}
	if passwordFieldRe.MatchString(target) || passwordFieldRe.MatchString(pageContext) {
		if name == "computer.type" || name == "computer.uia.action" || name == "computer.key" {
			return true
		}
	}
	if vaultHandleRe.MatchString(text) || strings.Contains(text, "vault=") {
		return true
	}
	if (name == "computer.type" || name == "computer.uia.action") && looksLikeRawCard(text) {
		return true
	}
	if challengeWallRe.MatchString(blob) {
		switch name {
		case "computer.click", "computer.key.hold", "computer.drag", "computer.key":
			return true
		}
	}
	payment := paymentSurfaceRe.MatchString(pageContext)
	probeEmpty := pageContext == ""
	switch name {
	case "computer.click":
		if payment {
			return label == "" || submitLabelRe.MatchString(label)
		}
		// Coordinate click with no readable target and no live page: we cannot
		// tell this is not Place order.
		return probeEmpty && label == ""
	case "computer.uia.action":
		if payment {
			return submitLabelRe.MatchString(uiaName) || uiaName == ""
		}
		return probeEmpty && uiaName == ""
	case "computer.key":
		key := strings.ToLower(strings.TrimSpace(asString(args["key"])))
		if i := strings.LastIndex(key, "+"); i >= 0 {
			key = key[i+1:]
		}
		_, submits := submitKeys[key]
		if !submits {
			return false
		}
		return payment || probeEmpty
	case "computer.type":
		return payment
	case "computer.key.hold", "computer.drag":
		return payment || probeEmpty || challengeWallRe.MatchString(blob)
	}
	return false
}

func shellArgvIsSensitive(args map[string]any) bool {
	parts := make([]string, 0, 8)
	switch v := args["argv"].(type) {
	case []any:
		for _, item := range v {
			parts = append(parts, asString(item))
		}
	case []string:
		parts = append(parts, v...)
	case string:
		parts = append(parts, v)
	}
	if cmd := asString(args["command"]); cmd != "" {
		parts = append(parts, cmd)
	}
	joined := strings.Join(parts, " ")
	if strings.TrimSpace(joined) == "" {
		return false
	}
	return shellMoneyRe.MatchString(joined) || shellCredentialRe.MatchString(joined)
}

// looksLikeRawCard is true when text contains a Luhn-valid 13–19 digit run.
func looksLikeRawCard(text string) bool {
	for _, m := range cardCandidateRe.FindAllStringSubmatch(text, -1) {
		if len(m) > 1 && luhnOK(m[1]) {
			return true
		}
	}
	return false
}

func luhnOK(candidate string) bool {
	digits := make([]int, 0, len(candidate))
	for _, r := range candidate {
		if r >= '0' && r <= '9' {
			digits = append(digits, int(r-'0'))
		}
	}
	if len(digits) < 13 || len(digits) > 19 {
		return false
	}
	sum := 0
	for i := 0; i < len(digits); i++ {
		n := digits[len(digits)-1-i]
		if i%2 == 1 {
			n *= 2
			if n > 9 {
				n -= 9
			}
		}
		sum += n
	}
	return sum%10 == 0
}

// NewDefaultToolRegistry builds the turn-time Tool ABI registry.
// Always registers Go builtins and RuntimeZig host tools (execute fails closed
// when remedy_core is unavailable). pythonCaller, when non-nil, registers
// RuntimePython tools over RMDY frames. Every descriptor without its own
// Deadline receives the deadlineForTool default.
func NewDefaultToolRegistry(pythonCaller tools.FrameCaller) (*tools.Registry, error) {
	registry := tools.NewRegistry(tools.AuthorizerFunc(runtimeLocalAuthorizer))
	if err := tools.RegisterGoBuiltins(registry); err != nil {
		return nil, err
	}
	if err := tools.RegisterZigHostTools(registry); err != nil {
		return nil, err
	}
	if err := tools.RegisterFileTools(registry); err != nil {
		return nil, err
	}
	if err := tools.RegisterShellTools(registry); err != nil {
		return nil, err
	}
	if err := tools.RegisterSessionTools(registry); err != nil {
		return nil, err
	}
	if pythonCaller != nil {
		if err := tools.RegisterPythonWorkerTools(registry, pythonCaller); err != nil {
			return nil, err
		}
	}
	registry.SetDefaultDeadlines(func(d tools.Descriptor) time.Duration { return deadlineForTool(d.ID) })
	return registry, nil
}
