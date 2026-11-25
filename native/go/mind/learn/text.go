package learn

import (
	"regexp"
	"strings"
)

// Python's \s is Unicode-aware; this is the same set for Go's RE2.
const ws = `\s\x0b\x1c-\x1f\x85\p{Z}`

var (
	reWinPath   = regexp.MustCompile(`[A-Za-z]:\\[^` + ws + `]+`)
	reUnixPath  = regexp.MustCompile(`/[^` + ws + `]+`)
	reVault     = regexp.MustCompile(`\{\{[` + ws + `]*vault:[^}]+\}\}`)
	reAuth      = regexp.MustCompile(`(?i)(?:authorization[` + ws + `]*[:=][` + ws + `]*)?bearer[` + ws + `]+[^` + ws + `]+`)
	reSecretish = regexp.MustCompile(`(?i)['"]?(api[_-]?key|secret|token|password|passwd|bearer|authorization)['"]?[` + ws + `]*[:=][` + ws + `]*['"]?[^` + ws + `]+`)
	reRedacted  = regexp.MustCompile(`(?i)(?:api[_-]?key|secret|token|password|passwd|bearer|authorization)=\[redacted\]`)
	reSpaces    = regexp.MustCompile(`[` + ws + `]+`)

	// partner_memory._SECRET_RE (py 183-199) without its lookbehind branches.
	reSecret = regexp.MustCompile(`(?i)(?:` +
		`api[_-]?key[` + ws + `]*(?:[:=]|\bis\b)[` + ws + `]*[^` + ws + `]+|secret[_-]?key[` + ws + `]*(?:[:=]|\bis\b)[` + ws + `]*[^` + ws + `]+|` +
		`secret[_-]?access[_-]?key[` + ws + `]*[:=][` + ws + `]*[^` + ws + `]+` +
		`|password[` + ws + `]*(?:[:=]|\bis\b)[` + ws + `]*[^` + ws + `]+|passwd[` + ws + `]*(?:[:=]|\bis\b)[` + ws + `]*[^` + ws + `]+|bearer[` + ws + `]+[a-z0-9\-._~+/]+=*` +
		`|gh[pousr]_[a-z0-9]{20,}|github_pat_[a-z0-9_]{20,}` +
		`|hf_[a-z0-9]{20,}` +
		`|AIza[a-z0-9_-]{30,}` +
		`|AKIA[a-z0-9]{16}|ASIA[a-z0-9]{16}` +
		`|sk_live_[a-z0-9]{20,}|rk_live_[a-z0-9]{20,}` +
		`|SG\.[a-z0-9_-]{20,}` +
		`|-----begin (?:rsa |ec |dsa |openssh )?private key-----` +
		`|authorization:[` + ws + `]*bearer)`)
	// The branches Python guards with (?<![a-z0-9]); RE2 has no lookbehind.
	reSecretBounded = regexp.MustCompile(`(?i)^(?:sk-[a-z0-9_-]{10,}|xai-[a-z0-9_-]{10,}|glpat-[a-z0-9_-]{10,}|xox[abposr]-[a-z0-9-]{10,}|xapp-[a-z0-9-]{10,})`)
	reLongToken     = regexp.MustCompile(`[A-Za-z0-9_\-+/=]{40,}`)
	reWord          = regexp.MustCompile(`(?i)[a-z]{4,}`)
)

func isAlnum(b byte) bool {
	return b >= '0' && b <= '9' || b >= 'a' && b <= 'z' || b >= 'A' && b <= 'Z'
}

// looksLikeSecret is partner_memory.looks_like_secret (py 216-226).
func looksLikeSecret(text string) bool {
	if strings.TrimSpace(text) == "" {
		return false
	}
	if reSecret.MatchString(text) {
		return true
	}
	for i := 0; i < len(text); i++ {
		if i > 0 && isAlnum(text[i-1]) {
			continue
		}
		if reSecretBounded.MatchString(text[i:]) {
			return true
		}
	}
	for _, tok := range reLongToken.FindAllString(text, -1) {
		if !reWord.MatchString(tok) {
			return true
		}
	}
	return false
}

// sanitizeTitleLine is agent_learn._sanitize_title_line (py 53-69).
func sanitizeTitleLine(message string) string {
	line := strings.SplitN(strings.TrimSpace(message), "\n", 2)[0]
	line = reWinPath.ReplaceAllString(line, "")
	line = reUnixPath.ReplaceAllString(line, "")
	line = reVault.ReplaceAllString(line, "")
	line = reAuth.ReplaceAllString(line, "")
	line = reSecretish.ReplaceAllString(line, "")
	line = strings.Trim(reSpaces.ReplaceAllString(line, " "), " -_.,")
	if looksLikeSecret(line) {
		return ""
	}
	return rcut(line, 60)
}

const omitted = "Owner task (secrets omitted)."

// safeLearnDescription is agent_learn.safe_learn_description (py 72-87).
func safeLearnDescription(message string) string {
	text := reVault.ReplaceAllString(message, "{{vault}}")
	text = reAuth.ReplaceAllString(text, "Bearer [redacted]")
	text = reSecretish.ReplaceAllString(text, "${1}=[redacted]")
	probe := reRedacted.ReplaceAllString(text, "")
	probe = strings.ReplaceAll(probe, "Bearer [redacted]", "")
	probe = strings.ReplaceAll(probe, "{{vault}}", "")
	if looksLikeSecret(probe) {
		return omitted
	}
	return rcut(text, 400)
}

var toolVerb = map[string]string{
	"file_write": "write", "file_edit": "edit", "file_edit_batch": "edit", "apply_patch": "edit",
	"bash_exec": "shell", "host_run": "host", "job_run": "verify", "repo_search": "search",
	"file_read": "read", "list_dir": "browse", "computer_click": "click", "computer_type": "type",
}

var reAlnumStrip = regexp.MustCompile(`[^A-Za-z0-9]`)

// skillTitle is agent_learn._skill_title_from_steps (py 90-109).
func skillTitle(message string, steps []nstep) string {
	line := sanitizeTitleLine(message)
	if len(reAlnumStrip.ReplaceAllString(line, "")) >= 4 {
		return line
	}
	var verbs []string
	for _, s := range steps {
		if metaTools[s.tool] || s.tool == "" {
			continue
		}
		if v := toolVerb[s.tool]; v != "" {
			verbs = appendOnce(verbs, v)
		}
		if len(verbs) >= 2 {
			break
		}
	}
	if len(verbs) > 0 {
		return rcut(strings.Join(verbs, "-"), 60)
	}
	return "session-task"
}
