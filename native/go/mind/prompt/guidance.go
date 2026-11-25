package prompt

import "regexp"

// Gates and accessors for the computer-use and away-mode addenda.
// Text: guidance_text_gen.go (generated from the Python constants).

// cuHintRE is _CU_HINT_RE in src/remedy/core/computer/guidance.py.
var cuHintRE = regexp.MustCompile(`(?i)(` +
	`\bcomputer[._]` +
	`|\bscreenshot\b` +
	`|\bbrowser\b` +
	`|\bon (?:my |the )?screen\b` +
	`|\bplay (?:it|the (?:game|app|exe|program))\b` +
	`|\bfill (?:out|in)(?: the)? form\b` +
	`|\bcheckout\b|\badd to cart\b` +
	`|https?://` +
	`|\bgoto\b` +
	`|\bgo to (?:http|www|gmail|google|amazon|walmart|target)\b` +
	`|\bdesktop app\b` +
	`|\bclick the\b` +
	`|\btype (?:into|in) the\b` +
	`)`)

// awayRE is _AWAY_RE in src/remedy/core/away_mode.py.
var awayRE = regexp.MustCompile(`(?i)\b(` +
	`work alone|on your own|handle this on your own|` +
	`i need to go|be with my kids|step(ping)? away|` +
	`don'?t wait for me|do not wait for me|` +
	`unattended|fully autonomous|finish without me|` +
	`take it from here|you got this|i'?m (heading |stepping )?out|` +
	`away mode|keep going without me|do it while i'?m gone` +
	`)\b`)

// computerUsePlaybook is the full playbook text with no gate
// (COMPUTER_USE_SYSTEM_ADDENDUM). The Python preamble appends it to the turn
// context as context + "\n\n" + playbook; that separator is the caller's.
func computerUsePlaybook() string { return computerUseAddendum }

// computerUseGuidance is the computer-use playbook addendum, verbatim from
// the Python, or "" when the message does not ask for screen or browser work.
//
// Gate shortcut: the Python first asks browse_intent (wants_page_interaction,
// is_pure_action_kick, is_open_only_browse, parse_browse_navigate_url) and
// only then falls back to _CU_HINT_RE. That intent parser is not ported, so
// this applies _CU_HINT_RE alone: messages that only browse_intent recognises
// (e.g. "open youtube", "search amazon for X", bare domains like "example.com",
// pure action kicks such as "click it") get "" here but the playbook in Python.
// Every message _CU_HINT_RE matches matches in both. Go's \b is ASCII where
// Python's is Unicode (matters only next to non-ASCII letters).
func computerUseGuidance(message string) string {
	m := coerceText(message)
	if m == "" || !cuHintRE.MatchString(m) {
		return ""
	}
	return computerUseAddendum
}

// awayGuidance is the "work alone while I am away" addendum
// (format_away_block), or "" when the message has no away phrase. The Python
// appends it as context + "\n\n" + block. Its life_drive.take_step side effect
// is not performed here.
func awayGuidance(message string) string {
	if !awayRE.MatchString(coerceText(message)) {
		return ""
	}
	return awayAddendum
}
