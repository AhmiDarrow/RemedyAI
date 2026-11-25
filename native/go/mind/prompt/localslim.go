package prompt

import (
	"regexp"
	"strconv"
	"strings"
)

// Port of local_agent_optimize.slim_system_for_local and the helpers it
// calls (local_system_contract, local_create_nudge, message_wants_implement)
// plus react_policy.is_pure_trivia_message.

const localContract = "[Local agent mode — auto-optimized]\n" +
	"You run on a fixed on-device window. Tasks use RESEARCH → PLAN → BUILD **via tools only**.\n" +
	"1. Call tools immediately. First step must include tool_calls (not markdown).\n" +
	"2. RESEARCH = list_dir / file_read / repo_search (small batches).\n" +
	"3. PLAN = short mental checklist — never a long RESEARCH/PLAN/BUILD essay in chat.\n" +
	"4. BUILD = file_write / file_edit / host_run / bash_exec. Code on disk, not in chat.\n" +
	"5. **Illegal:** tutorial monologue, \"pip install …\" guides, full source in ``` fences\n" +
	"   instead of file_write, \"Step 1/2/3\" how-to without tools.\n" +
	"6. Prefer absolute paths under the **project workspace**. Desktop is often PATH_DENIED.\n" +
	"7. After tools succeed, one short user-facing summary. No DSML/XML tool dumps as text.\n"

const localCreateNudge = "[Local create · tools required] Your next reply MUST use native function calls. " +
	"Prefer **file_write(path, content)** with a complete working program under the " +
	"project workspace, then verify (bash_exec py_compile / run). " +
	"Do **not** paste full source only in chat. Do **not** write RESEARCH/PLAN/BUILD " +
	"sections as the answer. Desktop is often PATH_DENIED under project scope."

// _IMPLEMENT_RE and _CONTINUE_RE.
var (
	implementRE = regexp.MustCompile(`(?is)\b(` +
		`create|build|implement|write|make|scaffold|generate|code|app|program|` +
		`script|calculator|todo|cli|gui|server|fix|debug|refactor|patch|add|` +
		`file|project|feature|function|class|module|test|` +
		`launch|serve|preview|localhost|site|webpage` +
		`)\b`)
	continueRE = regexp.MustCompile(`(?is)^\s*(` +
		`keep\s+going|continue|resume|carry\s+on|go\s+on|proceed|` +
		`finish\s+(?:it|this|the\s+(?:task|job|build|app))|` +
		`don'?t\s+stop|do\s+it|ship\s+it|make\s+progress|` +
		`try\s+again|retry|pick\s+up(?:\s+where)?|` +
		`next\s+step|keep\s+(?:building|working|coding)` +
		`)\b`)
)

// react_policy regexes behind is_pure_trivia_message that pytext_gen.go
// does not carry. RE2 \b and \w are ASCII (see chat.go).
const workExts = `py|tsx?|jsx?|mjs|cjs|rs|go|c|cpp|h|hpp|gd|json|toml|ya?ml|md|` +
	`html?|css|scss|vue|svelte|java|kt|swift|rb|php|sql|sh|ps1|` +
	`txt|csv|xml|svg|png|jpe?g|webp|gif`

var (
	toolHintRE = regexp.MustCompile(`(?i)\b(` +
		`read|write|edit|create|delete|list|ls|cat|open|save|` +
		`file|files|folder|directory|path|workspace|codebase|repo|repository|project|` +
		`review|analyze|analyse|explore|overview|inspect|structure|architecture|` +
		`audit|` +
		`skill_activate|skill_search|skill_run|skill_reload|activate|skill|` +
		`run|execute|shell|bash|command|terminal|install|build|test|` +
		`implement|implemen\w*|refactor|debug|fix(?:es)?|bug|bugsweep|bugfix|` +
		`hotfix|triage|cleanup|dogfood|` +
		`sweep|qa|error|stack|trace|` +
		`patch|apply|ship|deploy|` +
		`git|commit|diff|branch|src/|` +
		`\.(?:` + workExts + `)\b|` +
		`asset|assets|logo|logos|icon|icons|favicon|png|jpe?g|webp|svg|gif|` +
		`alpha|transparent|cutout|brighten|background|process|` +
		`comfyui|comfy|txt2img|img2img|portrait|nebula|spacey|` +
		`rmb|gguf|llama\.cpp|llamacpp|muscle\s+bridge|local\s+model|` +
		`walkthrough|what'?s installed|installed apps|this (pc|machine|computer)|` +
		`house_status|computer_apps|` +
		`generate(\s+an?)?\s+image|image\s+generation|render(\s+an?)?\s+image|` +
		`make\s+(me\s+)?(an?\s+)?(image|picture|photo)|` +
		`draw\s+(me\s+)?|illustrat|picture\s+of|photo\s+of|` +
		`show\s+(it|me|the\s+image)|embed(\s+it)?|display(\s+it)?|` +
		`goto|go\s+to|navigate|browser|rail|webview|` +
		`bring\s+up|pull\s+up|fire\s+up|take\s+me\s+to|head\s+to|jump\s+to|` +
		`gmail|youtube|wikipedia|wiki|fandom|` +
		`https?://|www\.|` +
		`play|game|pygame|click|launch|compile|gcc|clang` +
		`)\b|` +
		`(?:[A-Za-z]:)?[\\/][\w.\\/ -]+`)
	workShapeRE = regexp.MustCompile(`(?i)(?:[A-Za-z]:)?[\\/][\w.\\/ -]+|` +
		`\.(?:` + workExts + `)\b|` +
		`\b(?:src|app|ui|ux|dialog|panel|window|modal|page|screen|` +
		`config|settings|preference|preferences|` +
		`about\s+(?:window|dialog|ui|page|screen)|` +
		`module|package|class|function|handler|component|` +
		`test|tests|spec|bug|build|compile|` +
		`lock|timeout|auth|login)\b`)
	verbalOnlyRE = regexp.MustCompile(`(?is)^(?:turn\s+\d+\s*:\s*)?(?:please\s+)?` +
		`(?:reply|say|respond|answer|print|output)\s+` +
		`(?:only\s+|just\s+|with\s+)?\S`)
	arithOnlyRE = regexp.MustCompile(`(?i)^\s*` +
		`(?:what(?:'s|\s+is)\s+)?` +
		`\d+(?:\.\d+)?\s*[\+\-\*\/x×÷]\s*\d+(?:\.\d+)?` +
		`(?:\s*=\s*)?\s*\??\s*$`)
	pseudoToolRE = regexp.MustCompile(`(?i)\b(file_read|file_write|list_dir|bash_exec|host_run|host_mkdir|host_which|` +
		`host_script|comfyui|local_discover|rmb|` +
		`get_settings|update_settings|mail_list|mail_send|mail_create_draft|` +
		`calendar_list_events|calendar_create_event|budget_set|bill_upsert|` +
		`debt_upsert|assistant_brief)\s*\(`)
	dsmlToolRE = regexp.MustCompile(`(?i)(tool[_\s-]?calls|function[_\s-]?calls|DSML|` +
		`</?invoke\b|</?tool_invoke\b|invoke_parameter|invoke_step|` +
		`</?parameter\b|name\s*=\s*["'][a-z][a-z0-9_]{1,64}["'])`)
)

// isVerbalOnlyRequest lives in continue.go.

// isPureTrivia is react_policy.is_pure_trivia_message.
func isPureTrivia(message string) bool {
	msg := coerceText(message)
	if msg == "" {
		return false
	}
	if isVerbalOnlyRequest(msg) || dsmlToolRE.MatchString(msg) || pseudoToolRE.MatchString(msg) {
		return true
	}
	return arithOnlyRE.MatchString(msg) // Python re.match: the pattern is ^-anchored
}

// messageWantsImplement is local_agent_optimize.message_wants_implement
// (including message_wants_continue_work).
func messageWantsImplement(message string) bool {
	t := coerceText(message)
	if cpLen(t) < 2 {
		return false
	}
	if continueRE.MatchString(cut(t, 220)) {
		return true
	}
	return implementRE.MatchString(t)
}

// slimSystemForLocal is local_agent_optimize.slim_system_for_local.
//
//	system                    the full system prompt (system_prompt)
//	context                   the built turn context (the text after [Turn context])
//	provider, model, baseURL  the turn's binding (baseURL is only used by Python
//	                          for the window lookup, so it is not a parameter here)
//	maxSteps                  the safety ceiling (maxReactSteps)
//	userMessage               this turn's user text
//	window                    physical n_ctx from resolve_local_window; <2048 = unknown
//
// Left out: when window < 2048 Python calls endless_context.resolve_local_window
// (rmb.json ctx_size, token_nanobot cache). That is not in Go, so this falls
// to Python's own last resort, 8192. Callers that know the window must pass it.
func slimSystemForLocal(system, context, provider, model string, maxSteps int, userMessage string, window int) string {
	shown := model
	if shown == "" {
		shown = "local"
	}
	if isPureTrivia(userMessage) {
		return "You are a local assistant on this PC (model: " + shown + ").\n" +
			"Answer in one short sentence. No tools. No essays. No chain-of-thought."
	}
	if coerceText(userMessage) != "" && (isChatOnlyMessage(userMessage) || isFeelingPresenceQuestion(userMessage)) {
		return "You are a local assistant on this PC (model: " + shown + ").\n" +
			"Answer in plain chat. No tools. No empty shell calls. " +
			"Do not continue a previous tab's job unless this message asks."
	}
	win := window
	if win < 2048 {
		win = 8192
	}
	var headCap, ctxCap int
	switch {
	case win >= 24576:
		headCap, ctxCap = 1400, 18000
	case win >= 16384:
		headCap, ctxCap = 1100, 12000
	case win >= 12288:
		headCap, ctxCap = 900, 8000
	default:
		headCap, ctxCap = 700, 6000
	}
	head := pyStrip(system)
	if cpLen(head) > headCap+200 {
		first := head
		if i := strings.Index(head, "\n\n"); i >= 0 {
			first = head[:i]
		}
		head = cut(first, headCap) + "\nStyle: concise, tool-first. Prefer action over narration."
	}
	runtime := "Provider: " + provider + " · model: " + model + "\n" +
		"Mode: local fixed-window agent · n_ctx=" + strconv.Itoa(win) + " · run until done " +
		"(safety ceiling " + strconv.Itoa(maxSteps) + " steps).\n" +
		"Answer provider/model questions from this line — no tools needed."
	parts := []string{head, runtime, localContract}
	if ctx := pyStrip(context); ctx != "" {
		if cpLen(ctx) > ctxCap {
			keep := ctxCap - 500
			if keep < 2000 {
				keep = 2000
			}
			ctx = cut(ctx, keep) + "\n…[context trimmed for local window]"
		}
		parts = append(parts, ctx)
	}
	if messageWantsImplement(userMessage) {
		parts = append(parts, localCreateNudge)
	}
	var out []string
	for _, p := range parts {
		if pyStrip(p) != "" {
			out = append(out, p)
		}
	}
	return strings.Join(out, "\n\n")
}
