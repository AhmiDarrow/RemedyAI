package soul

import (
	"regexp"
	"strings"
)

// RE2 has no lookbehind: "(?<![a-z0-9])x" is written "(?:^|[^a-z0-9])x",
// which matches the same texts (these patterns only answer yes or no).
var (
	jobResumeFactRE = regexp.MustCompile(`(?i)^\s*(?:` +
		`stay with:\s*continue|` +
		`continue:\s|` +
		`keep going|` +
		`resume (?:the |this )?(?:task|job|build|work)|` +
		`pick up where|` +
		`don'?t stop|` +
		`continue remaining work|` +
		`from the last successful tool` +
		`)`)
	workResidueRE = regexp.MustCompile(`(?i)(?:` +
		`(?:^|[^A-Za-z0-9_])[a-z]:[\\/]|` +
		`(?:^|[^A-Za-z0-9_])/(?:home|users|mnt|srv|opt|var|tmp|etc|c|d)/|` +
		`\bintent=|` +
		`\|\s*user:|` +
		`\|\s*did:|` +
		`\|\s*tasks:|` +
		`retry or work around|` +
		`last failing tool|` +
		`last successful tool|` +
		`(?:^|:\s*)continue:\s|` +
		`(?:^|:\s*)(?:resume|continue) (?:the |this )?(?:build|job|task|work)\b` +
		`)`)

	secretRE = regexp.MustCompile(`(?i)(` +
		`api[_-]?key\s*(?:[:=]|\bis\b)\s*\S+|secret[_-]?key\s*(?:[:=]|\bis\b)\s*\S+|` +
		`secret[_-]?access[_-]?key\s*[:=]\s*\S+` +
		`|password\s*(?:[:=]|\bis\b)\s*\S+|passwd\s*(?:[:=]|\bis\b)\s*\S+|bearer\s+[a-z0-9\-._~+/]+=*` +
		`|(?:^|[^a-z0-9])sk-[a-z0-9_-]{10,}|(?:^|[^a-z0-9])xai-[a-z0-9_-]{10,}` +
		`|gh[pousr]_[a-z0-9]{20,}|github_pat_[a-z0-9_]{20,}` +
		`|(?:^|[^a-z0-9])glpat-[a-z0-9_-]{10,}|hf_[a-z0-9]{20,}` +
		`|(?:^|[^a-z0-9])xox[abposr]-[a-z0-9-]{10,}|(?:^|[^a-z0-9])xapp-[a-z0-9-]{10,}` +
		`|AIza[a-z0-9_-]{30,}` +
		`|AKIA[a-z0-9]{16}|ASIA[a-z0-9]{16}` +
		`|sk_live_[a-z0-9]{20,}|rk_live_[a-z0-9]{20,}` +
		`|SG\.[a-z0-9_-]{20,}` +
		`|-----begin (?:rsa |ec |dsa |openssh )?private key-----` +
		`|authorization:\s*bearer` +
		`)`)
	longTokenRE = regexp.MustCompile(`[A-Za-z0-9_\-+/=]{40,}`)
	wordyRE     = regexp.MustCompile(`(?i)[a-z]{4,}`)
	soulProseRE = regexp.MustCompile(`(?i)\b(password|passwd|token|secret)\s*(?:is|[:=])\s*\S+`)
)

// LooksLikeJobResumeFact is true for leftover "continue the last job" lines.
func LooksLikeJobResumeFact(text string) bool {
	t := strings.TrimSpace(text)
	return t != "" && jobResumeFactRE.MatchString(t)
}

// LooksLikeWorkResidue is true when text is job/project residue (a path, a
// build arc, a "continue:" thread) rather than durable identity. It gates
// every write into an owner-global store.
func LooksLikeWorkResidue(text string) bool {
	t := strings.TrimSpace(text)
	if t == "" {
		return false
	}
	return jobResumeFactRE.MatchString(t) || workResidueRE.MatchString(t)
}

// LooksLikeSecret is partner_memory.looks_like_secret: credentials are never stored.
func LooksLikeSecret(text string) bool {
	if strings.TrimSpace(text) == "" {
		return false
	}
	if secretRE.MatchString(text) {
		return true
	}
	for _, tok := range longTokenRE.FindAllString(text, -1) {
		if !wordyRE.MatchString(tok) {
			return true
		}
	}
	return false
}

// LooksLikeSecretSoul adds prose forms ("my password is ...").
func LooksLikeSecretSoul(text string) bool {
	return LooksLikeSecret(text) || soulProseRE.MatchString(text)
}
