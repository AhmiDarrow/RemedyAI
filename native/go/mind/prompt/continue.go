package prompt

import (
	"regexp"
	"strconv"
	"strings"
	"unicode"
)

// Native port of prompt.should_continue (src/remedy/runtime/prompt_assemble.py
// _should_continue_async) for a non-chat turn, with the heuristics it calls in
// src/remedy/core/react_policy.py and build_engine.looks_like_build_request.
//
// Left out on purpose (their sources are the build ledger, missions and the
// Session Brief; each is treated as "had nothing to say"):
//   - _observe_build_evidence and the BuildTurnState `st` (prompt_assemble.py
//     688-694): state_unverified (line 757-761) is always false, and the
//     force_implement explore-streak gate (lines 783-795) never fires.
//   - turn_has_unfinished_work (lines 802-815, react_policy.py 2494-2579):
//     open mission steps, the read-only build short-cut, build phase. The
//     "unfinished" reason and the tracker's "mission" counter are never used.

// ContinueInput is what the engine knows when a round ends with text only.
// One field per input _should_continue_async reads.
type ContinueInput struct {
	Goal         string // goal, else message
	Text         string // the finished assistant text
	SessionID    string // only keys the Python runtime and gate tracker
	HomeDir      string // only selects the Python runtime
	ProjectPath  string // only selects the Python runtime
	ToolCount    int    // tool calls this turn
	ChatMode     bool
	PlanMode     bool
	HasToolCalls bool
	// VerifySeen is the engine's verify flag: nil when absent or neither a
	// bool nor a string (Python then neither "is False" nor "is True").
	VerifySeen  *bool
	LastResults []ContinueResult
}

// ContinueResult is one {name, ok, tail, exit_code} row of the last batch.
type ContinueResult struct {
	Name     string
	OK       bool
	Tail     string
	ExitCode *int // exit_code field, else parsed from the tail
}

// ContinueState is the per-turn memory the Python keeps in its gate tracker
// (keyed by session and goal). The tracker's "mission" counter belongs to the
// left-out unfinished-work check and is not carried.
type ContinueState struct {
	KnowledgeOnce bool // a knowledge_gap nudge already fired this turn
	WantsTools    bool // the wants_tools nudge already fired this turn
}

const (
	nudgeUnfinished = "The user asked for real work. A reply with no function calls is not done. Call tools now via the function-calling API (glob, read, edit, write, bash, grep). The next real step is a tool call, not a description of one."
	nudgeVerifyOnce = "[Verify] Files changed and no later check has passed. Run the project's check with bash (tests, compiler, or the program itself) before reporting the result."
	nudgeAgency     = "Do not only *say* you will use tools or activate a skill. Call tools now via the function-calling API (e.g. skill.activate, glob, grep, read). Start the real review/work immediately."
	nudgeDelegated  = "The delegated report is not a passing check. Run the verify " +
		"command and read its exit code before reporting the result."
)

func failedVerifyNudge(name string, exit *int) string {
	code := "a failed result"
	if exit != nil {
		code = "exit_code=" + strconv.Itoa(*exit)
	}
	return "[Verify] `" + name + "` returned " + code + ", so the work is not done yet. " +
		"Read the failure, fix it with edit, then re-run the same command with bash."
}

func knowledgeGapNudge(name string) string {
	where := "This looks like a fact outside the repo"
	if name != "" {
		where = "`" + name + "` failed on something outside this repo"
	}
	return "[Verify] " + where + " (a missing module, an unknown command, or a missing page). " +
		"Look it up once with web.search before editing. If that tool is off, say what you could not find."
}

// decideContinue is prompt.should_continue for a non-chat turn. It reads and
// updates st, the per-turn gate memory (nil means a fresh turn).
func decideContinue(in ContinueInput, st *ContinueState) (cont bool, nudge, reason string) {
	if in.ChatMode {
		return false, "", "chat_mode"
	}
	if st == nil {
		st = &ContinueState{}
	}
	ev := summarizeToolEvidence(in.LastResults)
	buildGoal := looksLikeBuildRequest(in.Goal)
	plan := in.PlanMode

	// 1. Evidence beats prose: a verify-class tool failed and the text claims done.
	if ev.verifyFailed && textClaimsCompletion(in.Text) {
		if failureIsKnowledge(ev.failedTail) && !st.KnowledgeOnce {
			st.KnowledgeOnce = true
			return true, knowledgeGapNudge(ev.failedName), "knowledge_gap"
		}
		return true, failedVerifyNudge(ev.failedName, ev.failedExit), "verify_failed"
	}

	// 1b. Work answer that stops on a fact outside the repo, nothing searched.
	if !plan && buildGoal && textAdmitsUnknown(in.Text) && !batchUsedWeb(ev.names) &&
		!ev.lastVerifyOK && !st.KnowledgeOnce {
		st.KnowledgeOnce = true
		return true, knowledgeGapNudge(""), "knowledge_gap"
	}

	// 2. A build stays open until a check after the last edit is green. No
	// once-latch: another "done" while the check is stale re-arms again.
	batchUnverified := ev.mutateAfterVerify || (ev.mutates > 0 && !ev.verifySeen)
	engineTrue := in.VerifySeen != nil && *in.VerifySeen
	engineUnverified := in.VerifySeen != nil && !*in.VerifySeen && in.ToolCount > 0
	delegated := false
	for _, n := range ev.names {
		if normTool(n) == "delegate" {
			delegated = true
			break
		}
	}
	if buildGoal && !plan && delegated && !ev.lastVerifyOK {
		return true, nudgeDelegated, "delegate_unverified"
	}
	if buildGoal && !plan && !engineTrue && (engineUnverified || batchUnverified) {
		return true, nudgeVerifyOnce, "unverified"
	}

	// 3. (explore streak / force_implement: needs the build ledger, left out.)

	// 4. Narrated tool promise with zero tools and a goal that wants them.
	if in.ToolCount == 0 && !in.HasToolCalls && !looksLikePseudoTools(in.Text) &&
		messageWantsTools(in.Goal) && agencyToolPromiseClaim(in.Text) && !plan {
		return true, nudgeAgency, "agency_promise"
	}

	// 5. (mission / brief open work: left out.)

	// 6. A work request that produced no tools at all this turn.
	if in.ToolCount == 0 && !plan && messageWantsTools(in.Goal) && !st.WantsTools {
		st.WantsTools = true
		return true, nudgeUnfinished, "wants_tools"
	}
	return false, "", "done"
}

// parseContinueInput builds ContinueInput from the worker-style input map.
func parseContinueInput(m map[string]any) ContinueInput {
	in := ContinueInput{
		Goal:         pyStr(m, "goal"),
		Text:         pyStr(m, "text"),
		SessionID:    pyStrip(pyStr(m, "session_id")),
		HomeDir:      pyStrip(pyStr(m, "home_dir")),
		ProjectPath:  pyStrip(pyStr(m, "project_path")),
		ToolCount:    pyInt0(m["tool_count"]),
		ChatMode:     boolIn(m, "chat_mode"),
		PlanMode:     boolIn(m, "plan_mode"),
		HasToolCalls: boolIn(m, "has_tool_calls"),
	}
	if in.Goal == "" {
		in.Goal = pyStr(m, "message")
	}
	switch t := m["verify_seen"].(type) {
	case bool:
		in.VerifySeen = &t
	case string:
		v := !contains([]string{"", "false", "0", "no"}, strings.ToLower(pyStrip(t)))
		in.VerifySeen = &v
	}
	var rows []any
	switch t := m["last_results"].(type) {
	case []any:
		rows = t
	case []map[string]any:
		for _, r := range t {
			rows = append(rows, r)
		}
	}
	for _, r := range rows {
		if row, ok := r.(map[string]any); ok {
			in.LastResults = append(in.LastResults, parseContinueResult(row))
		}
	}
	return in
}

func parseContinueResult(row map[string]any) ContinueResult {
	r := ContinueResult{
		Name: pyStrip(pyStr(row, "name")),
		OK:   true,
		Tail: pyStr(row, "tail"),
	}
	if v, ok := row["ok"]; ok && v != nil {
		r.OK = truthy(v)
	}
	if v, ok := row["exit_code"]; ok && v != nil {
		if n, ok := pyInt(v); ok {
			r.ExitCode = &n
			return r
		}
	}
	r.ExitCode = parseExitCode(r.Tail)
	return r
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

// pyInt is int(v) for a decoded JSON value; ok is false where Python raises.
func pyInt(v any) (int, bool) {
	switch t := v.(type) {
	case bool:
		if t {
			return 1, true
		}
		return 0, true
	case int:
		return t, true
	case int64:
		return int(t), true
	case float64:
		if t != t || t > 9e18 || t < -9e18 {
			return 0, false
		}
		return int(t), true
	case string:
		n, err := strconv.Atoi(pyStrip(t))
		return n, err == nil
	}
	return 0, false
}

// pyInt0 is int(v or 0).
func pyInt0(v any) int {
	if !truthy(v) {
		return 0
	}
	n, _ := pyInt(v)
	return n
}

// ---- react_policy: tool evidence -----------------------------------------

type toolEvidence struct {
	mutates           int
	explores          int
	verifySeen        bool
	verifyFailed      bool
	failedName        string
	failedExit        *int
	failedTail        string
	lastVerifyOK      bool // last_verify_ok is True (None and False both read false)
	mutateAfterVerify bool
	names             []string
}

var (
	mutateExact  = setOf("apply_patch", "clipboard_write", "computer_click", "computer_drag", "computer_focus", "computer_key", "computer_key_hold", "computer_navigate", "computer_scroll", "computer_type", "computer_uia_action", "computer_window", "delegate", "edit", "file_delete", "file_edit", "file_write", "search_replace", "str_replace", "workspace_create", "workspace_delete", "workspace_edit", "workspace_mkdir", "workspace_rename", "workspace_write", "write")
	verifyExact  = setOf("bash", "bash_exec", "build_drive", "cargo_test", "host_run", "job_run", "mission_verify", "process_spawn", "pytest", "shell_exec")
	exploreExact = setOf("codebase_search", "file_read", "glob", "grep", "list_dir", "memory_search", "read", "search", "skill_search", "web_search", "workspace_grep", "workspace_list", "workspace_read", "workspace_search")
	webToolNames = setOf("web.fetch", "web.search", "web_fetch", "web_search")
)

func setOf(items ...string) map[string]bool {
	m := make(map[string]bool, len(items))
	for _, s := range items {
		m[s] = true
	}
	return m
}

func normTool(name string) string {
	return strings.ReplaceAll(strings.ReplaceAll(strings.ToLower(pyStrip(name)), "-", "_"), ".", "_")
}

// toolKind returns "mutate", "verify" or "explore" (react_policy.tool_kind).
func toolKind(name string) string {
	n := normTool(name)
	if n == "" {
		return "explore"
	}
	switch {
	case mutateExact[n]:
		return "mutate"
	case verifyExact[n]:
		return "verify"
	case exploreExact[n]:
		return "explore"
	}
	for _, p := range []string{"write", "edit", "delete", "patch", "create"} {
		if strings.Contains(n, p) {
			return "mutate"
		}
	}
	for _, p := range []string{"verify", "test", "shell", "exec"} {
		if strings.Contains(n, p) {
			return "verify"
		}
	}
	return "explore"
}

func parseExitCode(tail string) *int {
	m := reExitCode.FindStringSubmatch(cut(tail, 4000))
	if m == nil {
		return nil
	}
	n, err := strconv.Atoi(m[1])
	if err != nil {
		return nil
	}
	return &n
}

func summarizeToolEvidence(rows []ContinueResult) toolEvidence {
	var ev toolEvidence
	lastKind := ""
	for _, r := range rows {
		if r.Name == "" {
			continue
		}
		ev.names = append(ev.names, r.Name)
		switch toolKind(r.Name) {
		case "mutate":
			ev.mutates++
			lastKind = "mutate"
		case "explore":
			ev.explores++
			lastKind = "explore"
		default:
			failed := !r.OK || (r.ExitCode != nil && *r.ExitCode != 0)
			ev.verifySeen = true
			ev.lastVerifyOK = !failed
			if failed {
				ev.verifyFailed = true
				ev.failedName = r.Name
				ev.failedExit = r.ExitCode
				ev.failedTail = cut(r.Tail, 2000)
			}
			lastKind = "verify"
		}
	}
	ev.mutateAfterVerify = lastKind == "mutate"
	return ev
}

// tailCP is s[-n:] on code points.
func tailCP(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[len(r)-n:])
}

func textClaimsCompletion(text string) bool {
	t := pyStrip(text)
	return t != "" && reCompletionClaim.MatchString(tailCP(t, 1500))
}

func failureIsKnowledge(tail string) bool {
	return reKnowledgeFailure.MatchString(cut(tail, 2000))
}

func textAdmitsUnknown(text string) bool {
	t := pyStrip(text)
	return t != "" && reUnknownGiveup.MatchString(tailCP(t, 800))
}

func batchUsedWeb(names []string) bool {
	for _, n := range names {
		n = strings.ToLower(pyStrip(n))
		if webToolNames[n] || strings.HasPrefix(n, "web.") {
			return true
		}
	}
	return false
}

// ---- react_policy: narrated tool promise ---------------------------------

var agencyPromiseHard = []string{
	"activating skill",
	"activate skill",
	"activating the review",
	"activating the skill",
	"using tools now",
	"i'll use tools",
	"i will use tools",
	"calling skill",
	"skill_activate",
	"calling edit",
	"calling write",
	"i'll call tools",
	"i will call tools",
}

var agencyPromiseSoft = []string{
	"let me review",
	"i'll review",
	"i will review",
	"dedicated procedure",
	"let me implement",
	"i'll implement",
	"i will implement",
	"let me fix",
	"i'll fix",
	"i will fix",
	"let me debug",
	"i'll debug",
	"i will debug",
	"let me edit",
	"i'll edit",
	"i will edit",
	"let me apply",
	"i'll apply",
	"i will apply",
	"let me refactor",
	"i'll refactor",
	"i will refactor",
	"let me test",
	"i'll test",
	"i will test",
	"let me write the test",
	"i'll write the test",
	"i will write the test",
	"let me write tests",
	"i'll write tests",
	"i will write tests",
	"let me run the test",
	"i'll run the test",
	"i will run the test",
	"let me run the tests",
	"i'll run the tests",
	"i will run the tests",
	"i'll start coding",
	"i will start coding",
	"let me start coding",
	"i'll patch",
	"i will patch",
	"working on the fix now",
	"applying the change",
	"debugging now",
	"fixing this now",
}

// agencyToolPromiseClaim is agency_tool_promise_claim(text) (no reasoning
// text, stub limit 480).
func agencyToolPromiseClaim(text string) bool {
	text = pyStrip(text)
	if text == "" {
		return false
	}
	claim := strings.ToLower(text + "\n")
	for _, p := range agencyPromiseHard {
		if strings.Contains(claim, p) {
			return true
		}
	}
	if cpLen(text) >= 480 {
		return false
	}
	for _, p := range agencyPromiseSoft {
		if strings.Contains(claim, p) {
			return true
		}
	}
	return reWorkPromise.MatchString(claim)
}

// ---- react_policy: pseudo tool calls -------------------------------------

var (
	reJSONToolCall = regexp.MustCompile("(?s)(?:```\\s*json\\s*)?\\{\\s*\"name\"\\s*:\\s*\"[a-z0-9_]+\"\\s*,\\s*\"arguments\"\\s*:")
	reResponseCall = regexp.MustCompile("(?s)<\\s*response\\s*>\\s*\\{[^}]*\"name\"\\s*:")
	reNoiseWords   = regexp.MustCompile(`(?i)tool|invoke|parameter|comfyui|bash_exec|list_dir|settings|mail_|calendar_`)
	reLeakedMarkup = regexp.MustCompile(`(?i)(<\s*\|?\s*function_calls\b|</\s*\|?\s*function_calls\s*\|?\s*>|<\s*\|?\s*tool_call\b|<\s*\|?\s*tool_calls\b|<\s*\|?\s*tool_invoke\b|<\s*\|?\s*function_c|<\s*\|?\s*tool_c|get_settings\s*$)`)
	reAnyToolID    = regexp.MustCompile(`(?i)\b([a-z][a-z0-9_]{1,64})\b`)
	reCallShape    = regexp.MustCompile(`\w+\(\s*["']`)
)

func looksLikeToolMarkupPrefix(text string) bool {
	t := pyStrip(text)
	if t == "" {
		return false
	}
	if strings.HasPrefix(t, "```") {
		body := strings.TrimLeftFunc(t[3:], unicode.IsSpace)
		if strings.HasPrefix(strings.ToLower(cut(body, 4)), "json") {
			body = string([]rune(body)[4:])
		}
		rest := pyStrip(body)
		if !strings.Contains(t[3:], "```") && cpLen(rest) <= 200 {
			if rest == "" || strings.HasPrefix(rest, "{") || strings.HasPrefix(rest, "[") || strings.HasPrefix(rest, `"`) {
				return true
			}
		}
	}
	if cpLen(t) > 48 {
		return false
	}
	return reToolMarkupPrefix.MatchString(t)
}

func looksLikePseudoTools(text string) bool {
	if text == "" {
		return false
	}
	if looksLikeToolMarkupPrefix(text) {
		return true
	}
	if reJSONToolCall.MatchString(text) || reResponseCall.MatchString(text) {
		return true
	}
	if !strings.ContainsAny(text, "(<{") && !strings.Contains(text, "&&") &&
		!strings.Contains(text, "｜") && !strings.Contains(text, "DSML") {
		low := strings.ToLower(text)
		if !strings.Contains(low, "tool_call") && !strings.Contains(low, "function_call") &&
			!strings.Contains(low, "tool calls") && !strings.Contains(low, "function calls") &&
			!strings.Contains(low, "invoke") {
			return false
		}
	}
	if rePseudoTool.MatchString(text) {
		return true
	}
	if reDSMLNoise.MatchString(text) && reNoiseWords.MatchString(text) {
		return true
	}
	if reLeakedMarkup.MatchString(text) {
		return true
	}
	switch pyStrip(text) {
	case "<", "<>", "<function_c", "<tool_c", "tool_c":
		return true
	}
	if reDSMLTool.MatchString(text) && (reDSMLInvoke.MatchString(text) || reAnyToolID.MatchString(text)) {
		return true
	}
	return strings.Contains(text, "&&") && reCallShape.MatchString(text)
}

// ---- react_policy: does this message ask for work -------------------------

var reArith = regexp.MustCompile(`(?i)^\s*(?:what(?:'s|\s+is)\s+)?\d+(?:\.\d+)?\s*[\+\-\*\/x×÷]\s*\d+(?:\.\d+)?(?:\s*=\s*)?\s*\??\s*$`)

func isKnowledgeQuestion(message string) bool {
	msg := coerceText(message)
	if msg == "" || strings.Contains(msg, "\n") || cpLen(msg) > 160 {
		return false
	}
	if isChatOnlyMessage(msg) {
		return false
	}
	if actionKickRE.MatchString(msg) || requestWorkRE.MatchString(msg) || debugFollowupRE.MatchString(msg) {
		return false
	}
	feeling := feelingPresenceRE.MatchString(msg) && !hostWorkRE.MatchString(msg)
	if reToolHint.MatchString(msg) || reWorkShape.MatchString(msg) {
		return feeling && cpLen(msg) <= 120
	}
	if strings.HasSuffix(msg, "?") || reKnowledgeQStart.MatchString(msg) || feeling {
		return true
	}
	return reArith.MatchString(msg)
}

func isVerbalOnlyRequest(message string) bool {
	msg := coerceText(message)
	if msg == "" || strings.Contains(msg, "\n") || cpLen(msg) > 120 {
		return false
	}
	if reToolHint.MatchString(msg) || reWorkShape.MatchString(msg) {
		return false
	}
	if requestWorkRE.MatchString(msg) || actionKickRE.MatchString(msg) {
		return false
	}
	return reVerbalOnly.MatchString(msg)
}

func looksLikeInjectedToolMarkup(message string) bool {
	msg := coerceText(message)
	return msg != "" && (reDSMLTool.MatchString(msg) || rePseudoTool.MatchString(msg))
}

func messageHasWorkRequest(message string) bool {
	msg := coerceText(message)
	if msg == "" {
		return false
	}
	return actionKickRE.MatchString(msg) || requestWorkRE.MatchString(msg) ||
		reToolHint.MatchString(msg) || reWorkShape.MatchString(msg) ||
		debugFollowupRE.MatchString(msg) || reCompanion.MatchString(msg)
}

// messageWantsTools is react_policy.message_wants_tools.
func messageWantsTools(message string) bool {
	msg := coerceText(message)
	if msg == "" || isChatOnlyMessage(msg) || isVerbalOnlyRequest(msg) ||
		looksLikeInjectedToolMarkup(msg) {
		return false
	}
	if isKnowledgeQuestion(msg) && !debugFollowupRE.MatchString(msg) {
		return false
	}
	return messageHasWorkRequest(msg)
}

// looksLikeBuildRequest is build_engine.looks_like_build_request.
func looksLikeBuildRequest(message string) bool {
	msg := coerceText(message)
	if msg == "" {
		return false
	}
	low := strings.ToLower(msg)
	switch strings.TrimRight(low, "!.?") {
	case "hi", "hey", "hello", "thanks", "thank you", "ok", "okay", "yes", "no",
		"yep", "nope", "cool", "bye", "good morning", "good night":
		return false
	}
	if reBuild.MatchString(msg) {
		return true
	}
	if cpLen(msg) > 280 && (strings.Contains(msg, "```") || strings.Count(msg, "\n") >= 4) {
		return true
	}
	if cpLen(msg) <= 24 {
		return false
	}
	return reBuildFileCue.MatchString(msg) || strings.Contains(low, "need you to") ||
		strings.Contains(low, "file_write") || strings.Contains(low, "src/")
}

// ---- compiled patterns, copied verbatim from the Python sources ----------

var (
	reToolHint         = regexp.MustCompile(`(?i)\b(read|write|edit|create|delete|list|ls|cat|open|save|file|files|folder|directory|path|workspace|codebase|repo|repository|project|review|analyze|analyse|explore|overview|inspect|structure|architecture|audit|skill_activate|skill_search|skill_run|skill_reload|activate|skill|run|execute|shell|bash|command|terminal|install|build|test|implement|implemen\w*|refactor|debug|fix(?:es)?|bug|bugsweep|bugfix|hotfix|triage|cleanup|dogfood|sweep|qa|error|stack|trace|patch|apply|ship|deploy|git|commit|diff|branch|src/|\.(?:py|tsx?|jsx?|mjs|cjs|rs|go|c|cpp|h|hpp|gd|json|toml|ya?ml|md|html?|css|scss|vue|svelte|java|kt|swift|rb|php|sql|sh|ps1|txt|csv|xml|svg|png|jpe?g|webp|gif)\b|asset|assets|logo|logos|icon|icons|favicon|png|jpe?g|webp|svg|gif|alpha|transparent|cutout|brighten|background|process|comfyui|comfy|txt2img|img2img|portrait|nebula|spacey|rmb|gguf|llama\.cpp|llamacpp|muscle\s+bridge|local\s+model|walkthrough|what'?s installed|installed apps|this (pc|machine|computer)|house_status|computer_apps|generate(\s+an?)?\s+image|image\s+generation|render(\s+an?)?\s+image|make\s+(me\s+)?(an?\s+)?(image|picture|photo)|draw\s+(me\s+)?|illustrat|picture\s+of|photo\s+of|show\s+(it|me|the\s+image)|embed(\s+it)?|display(\s+it)?|goto|go\s+to|navigate|browser|rail|webview|bring\s+up|pull\s+up|fire\s+up|take\s+me\s+to|head\s+to|jump\s+to|gmail|youtube|wikipedia|wiki|fandom|https?://|www\.|play|game|pygame|click|launch|compile|gcc|clang)\b|(?:[A-Za-z]:)?[\\/][\w.\\/ -]+`)
	rePseudoTool       = regexp.MustCompile(`(?i)\b(file_read|file_write|list_dir|bash_exec|host_run|host_mkdir|host_which|host_script|comfyui|local_discover|rmb|get_settings|update_settings|mail_list|mail_send|mail_create_draft|calendar_list_events|calendar_create_event|budget_set|bill_upsert|debt_upsert|assistant_brief)\s*\(`)
	reDSMLTool         = regexp.MustCompile(`(?i)(tool[_\s-]?calls|function[_\s-]?calls|DSML|</?invoke\b|</?tool_invoke\b|invoke_parameter|invoke_step|</?parameter\b|name\s*=\s*[\"'][a-z][a-z0-9_]{1,64}[\"'])`)
	reDSMLInvoke       = regexp.MustCompile(`(?i)name\s*=\s*["']([a-z][a-z0-9_]{1,64})["']`)
	reDSMLNoise        = regexp.MustCompile(`(?i)[|｜]{1,2}\s*DSML\s*[|｜]{1,2}|[|｜]{2}`)
	reKnowledgeQStart  = regexp.MustCompile(`(?i)(?i)^(?:what|who|when|where|why|how|which|is|are|does|do|did|was|were)\b`)
	reWorkShape        = regexp.MustCompile(`(?i)(?i)(?:[A-Za-z]:)?[\\/][\w.\\/ -]+|\.(?:py|tsx?|jsx?|mjs|cjs|rs|go|c|cpp|h|hpp|gd|json|toml|ya?ml|md|html?|css|scss|vue|svelte|java|kt|swift|rb|php|sql|sh|ps1|txt|csv|xml|svg|png|jpe?g|webp|gif)\b|\b(?:src|app|ui|ux|dialog|panel|window|modal|page|screen|config|settings|preference|preferences|about\s+(?:window|dialog|ui|page|screen)|module|package|class|function|handler|component|test|tests|spec|bug|build|compile|lock|timeout|auth|login)\b`)
	reVerbalOnly       = regexp.MustCompile(`(?is)(?is)^(?:turn\s+\d+\s*:\s*)?(?:please\s+)?(?:reply|say|respond|answer|print|output)\s+(?:only\s+|just\s+|with\s+)?\S`)
	reToolMarkupPrefix = regexp.MustCompile(`(?i)^\s*(?:<\s*)?(?:tool_c|tool_calls|function_c|function_calls|invoke\b|DSML|｜)|^\s*<(?:tool|function)\b|^\s*(?:tool|function|invoke)\s*$`)
	reWorkPromise      = regexp.MustCompile(`(?is)(?is)\b(?:i(?:'ll| will)|let me|i am going to|i'm going to)\s+(?:find|look|check|read|open|search|inspect|scan|review|implement|fix|add|resize|change|wire|edit|build|debug|tighten|shrink|update|set up|make|do|research)\b`)
	reExitCode         = regexp.MustCompile(`(?i)(?i)\bexit[_ ]code\s*[=:]\s*(-?\d+)`)
	reKnowledgeFailure = regexp.MustCompile(`(?is)(?is)(modulenotfounderror|\bno module named\b|importerror\s*:|\bcannot find module\b|\bcommand not found\b|is not recognized as an internal or external command|could not find a version that satisfies|no matching distribution found|\bunknown command\b|\bunrecognized (?:command|option|arguments)\b|\bunknown (?:flag|option)\b|\bhttp\s*404\b|\b404 not found\b)`)
	reUnknownGiveup    = regexp.MustCompile(`(?i)(?i)\b(?:i (?:do not|don't) know|i(?:'m| am) not sure how|not sure how|no idea how|could not find (?:any |a )?(?:docs|documentation|reference)|i (?:couldn't|could not) find)\b`)
	reCompletionClaim  = regexp.MustCompile(`(?i)(?i)\b(?:(?:all|everything|the\s+\w+)\s+(?:is|are)\s+(?:now\s+)?(?:done|complete|finished|fixed|passing|green)|(?:done|complete|completed|finished|fixed|implemented|working|passing|green)\b[.!]|tests?\s+(?:pass|passed|are\s+green)|successfully\s+(?:implemented|fixed|built|added|created)|(?:is|are)\s+(?:now\s+)?(?:working|in\s+place|ready))`)
	reBuild            = regexp.MustCompile(`(?i)(?i)\b(implement|build|ship|scaffold|create\s+(an?\s+)?(app|api|service|module|package|feature|cli|library|tool|file|program|simple\s+\w+|c\s+program|\w+\s+program)|write\s+(a\s+)?(script|module|service|test|app|file|program)|add\s+(a\s+)?(feature|endpoint|command|test)|fix\s+(the\s+)?(bug|build|tests?|ci|error)|refactor|wire\s+up|make\s+(it|me)|develop|end[- ]to[- ]end|from\s+scratch|green\s+tests|pytest|npm\s+test|review|audit|investigate|research|debug|set\s*up|setup|sweep|bugsweep|bugfix|bug-hunt|hotfix|triage|cleanup|dogfood|migrate|upgrade|replace|prototype|design\s+(the\s+)?(system|api|feature)|calculator|todo\s+app|cli\b|compile|gcc|clang|\.c\b|hello\.c|main\.c|pygame|play\s+(it|the\s+game)|try\s+it|godot|gdscript|platformer|roguelike|game\s+loop|sprite\s*sheet|tileset|level\s+design|vertical\s+slice|we need (a |an |to )|landing\s+page|web\s*page|product\s+page|marketing\s+page|launch\s+(the\s+)?(site|server|app|page|preview)|serve\s+(it\s+)?locally|start\s+(the\s+)?(dev\s+)?server|preview\s+(the\s+)?(site|page)|can we (add|resize|change|shrink|tighten)|resize|autolock|auto[- ]?lock|settings (and |/ )?(about )?(ui|dialog|panel|window))\b`)
	reBuildFileCue     = regexp.MustCompile(`(?i)(?i)(?:\.(?:py|tsx?|jsx?|rs|go|cpp|h|gd)\b|\.c\b|\bgcc\b|\bcompile\b|\bclang\b)`)
	reCompanion        = regexp.MustCompile(`(?i)(?i)\b(clipboard|what'?s on (my |the )?(screen|clipboard|desk)|look at (this|that|my screen|the (screen|clipboard|desktop|ui))|i copied|i just copied|paste( this)?|on my (screen|desktop|monitor)|what am i looking at|what'?s (open|focused)|design (this|that|the|my)|critique (this|that|the)|does this look|make (it|this) (pretty|beautiful|sleek|nice)|mock(up)?|screenshot|visual(ly)?)\b`)
)

// DecideContinue is prompt.should_continue for the runtime: input is the
// hook's input map and st is the turn's own gate memory (one per turn).
func DecideContinue(input map[string]any, st *ContinueState) (cont bool, nudge, reason string) {
	return decideContinue(parseContinueInput(input), st)
}
