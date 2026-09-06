package providers

import (
	"encoding/json"
	"net/url"
	"os"
	"strconv"
	"strings"
)

// CodingPackABI is the mid-build / local tool allowlist (Go Tool ABI ids).
// Keeps coding + mission + memory/skills + light web; drops the long tail.
var CodingPackABI = []string{
	"workspace.read", "workspace.list", "workspace.write", "workspace.edit", "workspace.search",
	"shell.exec",
	"memory.search", "memory.save",
	"skill.search", "skill.activate",
	"web.fetch", "web.search",
	"mission.start", "mission.status", "mission.update", "mission.verify", "mission.complete",
}

// IsLocalBaseURL reports loopback OpenAI-compatible endpoints (RMB/Ollama/llama.cpp).
func IsLocalBaseURL(raw string) bool {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || u.Host == "" {
		host := strings.ToLower(strings.TrimSpace(raw))
		return strings.Contains(host, "127.0.0.1") || strings.Contains(host, "localhost")
	}
	host := strings.ToLower(u.Hostname())
	return host == "127.0.0.1" || host == "localhost" || host == "::1"
}

func envContextWindow() int {
	raw := strings.TrimSpace(os.Getenv("REMEDY_LOCAL_CTX"))
	if raw == "" {
		raw = strings.TrimSpace(os.Getenv("REMEDY_N_CTX"))
	}
	if raw == "" {
		return 0
	}
	n, err := strconv.Atoi(raw)
	if err != nil || n < 2048 {
		return 0
	}
	if n > 256_000 {
		n = 256_000
	}
	return n
}

func estimateMessagesTokens(msgs []map[string]any) int {
	total := 0
	for _, m := range msgs {
		total += 6
		if c, ok := m["content"].(string); ok {
			total += (len(c) + 3) / 4
		}
		if tcs, ok := m["tool_calls"].([]map[string]any); ok {
			for _, tc := range tcs {
				b, _ := jsonMarshalLen(tc)
				total += (b + 3) / 4
			}
		}
	}
	return total
}

func estimateToolsTokens(tools []map[string]any) int {
	if len(tools) == 0 {
		return 0
	}
	n := 0
	for _, t := range tools {
		b, _ := jsonMarshalLen(t)
		n += (b + 3) / 4
	}
	return n
}

func jsonMarshalLen(v any) (int, error) {
	b, err := json.Marshal(v)
	return len(b), err
}

// FitLocalRequest shrinks messages+tools into a fixed n_ctx. Never removes all tools
// on coding turns — falls back to CodingPackABI name-only schemas under pressure.
func FitLocalRequest(
	messages []map[string]any,
	tools []map[string]any,
	window int,
) (msgs []map[string]any, tls []map[string]any, meta map[string]any) {
	msgs = messages
	tls = tools
	if window < 2048 {
		window = 8192
	}
	budget := int(float64(window) * 0.72)
	if budget < 1024 {
		budget = 1024
	}
	meta = map[string]any{
		"window": window, "prompt_budget": budget, "levels": []string{},
	}
	est := func() int { return estimateMessagesTokens(msgs) + estimateToolsTokens(tls) }
	before := est()
	meta["est_before"] = before
	if before <= budget {
		meta["est_after"] = before
		meta["levels"] = append(meta["levels"].([]string), "ok")
		return msgs, tls, meta
	}

	levels := []string{"slim_tools"}
	tls = filterToolsCodingPack(tls, false)
	if est() > budget {
		levels = append(levels, "shrink_system")
		msgs = shrinkSystemMessages(msgs, max(800, budget*3/10))
	}
	if est() > budget {
		levels = append(levels, "clip_tools")
		msgs = clipToolMessageBodies(msgs, 2000)
	}
	if est() > budget {
		levels = append(levels, "name_only_coding")
		tls = filterToolsCodingPack(tools, true)
		msgs = clipToolMessageBodies(msgs, 800)
		msgs = shrinkSystemMessages(msgs, max(600, budget/4))
	}
	meta["levels"] = levels
	meta["est_after"] = est()
	meta["tools_after"] = len(tls)
	return msgs, tls, meta
}

func filterToolsCodingPack(tools []map[string]any, nameOnly bool) []map[string]any {
	if len(tools) == 0 {
		return tools
	}
	allow := map[string]bool{}
	for _, id := range CodingPackABI {
		allow[sanitizeToolName(id)] = true
		allow[id] = true
	}
	out := make([]map[string]any, 0, len(CodingPackABI))
	for _, t := range tools {
		fn, _ := t["function"].(map[string]any)
		if fn == nil {
			continue
		}
		name, _ := fn["name"].(string)
		if name == "" {
			continue
		}
		if !allow[name] && !codingNameFuzzy(name) {
			continue
		}
		if nameOnly {
			out = append(out, map[string]any{
				"type": "function",
				"function": map[string]any{
					"name":        name,
					"description": truncStr(strOr(fn["description"]), 80),
					"parameters":   map[string]any{"type": "object", "properties": map[string]any{}},
				},
			})
			continue
		}
		out = append(out, t)
		if len(out) >= 20 {
			break
		}
	}
	if len(out) == 0 {
		return tools // never strip everything
	}
	return out
}

func codingNameFuzzy(name string) bool {
	n := strings.ToLower(name)
	return strings.Contains(n, "workspace") ||
		strings.Contains(n, "shell") ||
		strings.Contains(n, "mission") ||
		strings.Contains(n, "memory") ||
		strings.Contains(n, "skill") ||
		strings.HasPrefix(n, "web_") ||
		strings.HasPrefix(n, "file_") ||
		strings.HasPrefix(n, "host_")
}

func shrinkSystemMessages(msgs []map[string]any, maxChars int) []map[string]any {
	out := make([]map[string]any, len(msgs))
	copy(out, msgs)
	for i, m := range out {
		if m["role"] != "system" {
			continue
		}
		c, _ := m["content"].(string)
		if len(c) <= maxChars {
			continue
		}
		// Prefer keeping the tail (brief / working memory often appended last).
		nm := map[string]any{}
		for k, v := range m {
			nm[k] = v
		}
		nm["content"] = "…\n" + c[len(c)-maxChars:]
		out[i] = nm
	}
	return out
}

func clipToolMessageBodies(msgs []map[string]any, maxChars int) []map[string]any {
	out := make([]map[string]any, len(msgs))
	copy(out, msgs)
	for i, m := range out {
		if m["role"] != "tool" {
			continue
		}
		c, _ := m["content"].(string)
		if len(c) <= maxChars {
			continue
		}
		nm := map[string]any{}
		for k, v := range m {
			nm[k] = v
		}
		nm["content"] = clipString(c, maxChars)
		out[i] = nm
	}
	return out
}

func strOr(v any) string {
	s, _ := v.(string)
	return s
}

func truncStr(s string, n int) string {
	if n <= 0 || len(s) <= n {
		return s
	}
	return s[:n]
}


