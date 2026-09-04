package gateway

import (
	"fmt"
	"os"
	"strings"
)

// ParseIDs splits a list or comma/semicolon-separated string into a set.
func ParseIDs(raw any) map[string]struct{} {
	out := map[string]struct{}{}
	switch t := raw.(type) {
	case nil:
		return out
	case []string:
		for _, x := range t {
			if s := strings.TrimSpace(x); s != "" {
				out[s] = struct{}{}
			}
		}
	case []any:
		for _, x := range t {
			if s := strings.TrimSpace(fmt.Sprint(x)); s != "" && s != "<nil>" {
				out[s] = struct{}{}
			}
		}
	case string:
		repl := strings.ReplaceAll(t, ";", ",")
		for _, p := range strings.Split(repl, ",") {
			if s := strings.TrimSpace(p); s != "" {
				out[s] = struct{}{}
			}
		}
	}
	return out
}

// EnvAllowAll is true when the named env var is a truthy flag.
func EnvAllowAll(envKey string) bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv(envKey))) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

// IsAllowed implements the secure-default messenger allowlist:
// allow_all wins; empty allowlist rejects; otherwise any candidate may match.
func IsAllowed(allowlist map[string]struct{}, allowAll bool, candidates ...string) bool {
	if allowAll {
		return true
	}
	if len(allowlist) == 0 {
		return false
	}
	for _, c := range candidates {
		c = strings.TrimSpace(c)
		if c == "" {
			continue
		}
		if _, ok := allowlist[c]; ok {
			return true
		}
	}
	return false
}
