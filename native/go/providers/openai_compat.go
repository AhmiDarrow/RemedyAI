package providers

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

// Default stream client: no overall Timeout (long thinking/tool streams must not
// die at 120s). Dial/TLS/header bounds only; body read follows ctx cancel / Stop.
func defaultStreamHTTPClient() *http.Client {
	return &http.Client{
		Timeout: 0,
		Transport: &http.Transport{
			Proxy: http.ProxyFromEnvironment,
			DialContext: (&net.Dialer{
				Timeout:   30 * time.Second,
				KeepAlive: 30 * time.Second,
			}).DialContext,
			TLSHandshakeTimeout:   30 * time.Second,
			ResponseHeaderTimeout: 120 * time.Second,
			IdleConnTimeout:       90 * time.Second,
			ExpectContinueTimeout: 1 * time.Second,
			ForceAttemptHTTP2:     true,
		},
	}
}

// OpenAICompat streams OpenAI-compatible chat completions SSE into cognition.ModelEvent.
type OpenAICompat struct {
	BaseURL    string
	APIKey     string
	Model      string
	HTTPClient *http.Client
	// Tools is the OpenAI tools array advertised on each chat completion request.
	// Empty omits the field (text-only). Populated from the Tool ABI registry.
	Tools []map[string]any
	// ToolNameMap maps advertised function names → real Tool ABI ids.
	// DeepSeek/OpenAI require ^[a-zA-Z0-9_-]+$ so dotted ABI ids are sanitized.
	ToolNameMap map[string]string
	// ContextWindow is the physical n_ctx for local models (0 = cloud / unknown).
	ContextWindow int
	// LocalFit forces FitLocalRequest even when BaseURL is not loopback.
	LocalFit bool
}

func (c *OpenAICompat) client() *http.Client {
	if c.HTTPClient != nil {
		return c.HTTPClient
	}
	return defaultStreamHTTPClient()
}

func (c *OpenAICompat) Stream(ctx context.Context, turn cognition.Turn) (<-chan cognition.ModelEvent, error) {
	base := strings.TrimRight(strings.TrimSpace(c.BaseURL), "/")
	model := strings.TrimSpace(c.Model)
	if base == "" || model == "" {
		return nil, fmt.Errorf("openai-compat model requires base URL and model id")
	}
	messages := buildMessages(turn)
	toolSchemas := c.Tools
	window := c.ContextWindow
	if window <= 0 {
		window = envContextWindow()
	}
	if c.LocalFit || IsLocalBaseURL(base) {
		if window <= 0 {
			window = 16384 // conservative RMB/Ollama default when unknown
		}
		messages, toolSchemas, _ = FitLocalRequest(messages, toolSchemas, window)
	}
	payload := map[string]any{
		"model":    model,
		"messages": messages,
		"stream":   true,
	}
	if len(toolSchemas) > 0 {
		payload["tools"] = toolSchemas
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, base+"/chat/completions", bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "text/event-stream")
	key := strings.TrimSpace(c.APIKey)
	if key == "" {
		key = "unused"
	}
	req.Header.Set("Authorization", "Bearer "+key)

	resp, err := c.client().Do(req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		defer resp.Body.Close()
		snippet, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return nil, fmt.Errorf("openai-compat HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(snippet)))
	}

	out := make(chan cognition.ModelEvent, 16)
	go func() {
		defer close(out)
		defer resp.Body.Close()
		// Keep advertised (sanitized) tool names on ToolCall so follow-up
		// assistant.tool_calls round-trips match provider expectations.
		// Remap to Tool ABI ids at execute time (see ResolveToolName).
		parseSSE(ctx, resp.Body, out, nil)
	}()
	return out, nil
}

// ResolveToolName maps an advertised OpenAI function name back to the Tool ABI id.
func (c *OpenAICompat) ResolveToolName(advertised string) string {
	if c == nil {
		return advertised
	}
	if c.ToolNameMap != nil {
		if real, ok := c.ToolNameMap[advertised]; ok && real != "" {
			return real
		}
	}
	return advertised
}

// RegistryTool is the providers-local view of a Tool ABI descriptor.
type RegistryTool struct {
	ID          string
	Description string
	InputSchema json.RawMessage
}

// ToolSchemasFromRegistry converts registry tool metadata into an OpenAI tools
// array with provider-safe function names (no dots). Prefer
// ToolSchemasFromRegistryMapped when you need the reverse map for tool calls.
func ToolSchemasFromRegistry(list []RegistryTool) []map[string]any {
	schemas, _ := ToolSchemasFromRegistryMapped(list)
	return schemas
}

// ToolSchemasFromRegistryMapped returns OpenAI tool schemas plus advertised→real
// name map. Names are sanitized to ^[a-zA-Z0-9_-]+$ (DeepSeek / OpenAI strict).
func ToolSchemasFromRegistryMapped(list []RegistryTool) ([]map[string]any, map[string]string) {
	out := make([]map[string]any, 0, len(list))
	nameMap := make(map[string]string, len(list))
	used := map[string]struct{}{}
	for _, d := range list {
		params := map[string]any{"type": "object", "properties": map[string]any{}}
		if len(d.InputSchema) > 0 {
			var decoded any
			if json.Unmarshal(d.InputSchema, &decoded) == nil {
				if m, ok := decoded.(map[string]any); ok && m != nil {
					params = m
				}
			}
		}
		advertised := uniqueSanitizedToolName(d.ID, used)
		used[advertised] = struct{}{}
		nameMap[advertised] = d.ID
		out = append(out, map[string]any{
			"type": "function",
			"function": map[string]any{
				"name":        advertised,
				"description": d.Description,
				"parameters":   params,
			},
		})
	}
	return out, nameMap
}

// sanitizeToolName maps Tool ABI ids to OpenAI/DeepSeek-safe function names.
func sanitizeToolName(id string) string {
	id = strings.TrimSpace(id)
	if id == "" {
		return "tool"
	}
	var b strings.Builder
	b.Grow(len(id))
	for _, r := range id {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9', r == '_', r == '-':
			b.WriteRune(r)
		default:
			b.WriteByte('_')
		}
	}
	out := b.String()
	if out == "" {
		return "tool"
	}
	if out[0] >= '0' && out[0] <= '9' {
		return "t_" + out
	}
	return out
}

func uniqueSanitizedToolName(id string, used map[string]struct{}) string {
	base := sanitizeToolName(id)
	if _, ok := used[base]; !ok {
		return base
	}
	for i := 2; i < 10000; i++ {
		cand := fmt.Sprintf("%s_%d", base, i)
		if _, ok := used[cand]; !ok {
			return cand
		}
	}
	return base + "_x"
}

const (
	maxToolArgChars    = 12_000
	maxToolResultChars = 24_000
	maxAssistantChars  = 4_000
)

func clipString(s string, max int) string {
	if max <= 0 || len(s) <= max {
		return s
	}
	head := max * 2 / 3
	tail := max - head - 64
	if tail < 32 {
		return s[:max] + "…"
	}
	return s[:head] + fmt.Sprintf("\n…[truncated %d chars]…\n", len(s)-max) + s[len(s)-tail:]
}

func buildMessages(turn cognition.Turn) []map[string]any {
	msgs := make([]map[string]any, 0, 4+len(turn.Results))
	if sys := strings.TrimSpace(turn.System); sys != "" {
		msgs = append(msgs, map[string]any{"role": "system", "content": sys})
	}
	goal := strings.TrimSpace(turn.Goal)
	if goal == "" {
		goal = "continue"
	}
	msgs = append(msgs, map[string]any{"role": "user", "content": goal})

	// OpenAI / DeepSeek require assistant.tool_calls then tool messages with
	// matching tool_call_id. Plain role=tool without ids is rejected (HTTP 400).
	if len(turn.Calls) > 0 && len(turn.Results) > 0 {
		toolCalls := make([]map[string]any, 0, len(turn.Calls))
		for i, call := range turn.Calls {
			id := strings.TrimSpace(call.ID)
			if id == "" {
				id = fmt.Sprintf("call_%d", i+1)
			}
			args := string(call.Input)
			if strings.TrimSpace(args) == "" {
				args = "{}"
			} else {
				args = clipString(args, maxToolArgChars)
			}
			toolCalls = append(toolCalls, map[string]any{
				"id":   id,
				"type": "function",
				"function": map[string]any{
					"name":      call.Name,
					"arguments": args,
				},
			})
		}
		assistant := map[string]any{
			"role":       "assistant",
			"tool_calls": toolCalls,
		}
		if text := strings.TrimSpace(turn.Text); text != "" {
			assistant["content"] = clipString(text, maxAssistantChars)
		}
		msgs = append(msgs, assistant)

		byID := map[string]cognition.ToolResult{}
		byName := map[string]cognition.ToolResult{}
		for _, res := range turn.Results {
			if id := strings.TrimSpace(res.ID); id != "" {
				byID[id] = res
			}
			if name := strings.TrimSpace(res.Name); name != "" {
				byName[name] = res
			}
		}
		for i, call := range turn.Calls {
			id := strings.TrimSpace(call.ID)
			if id == "" {
				id = fmt.Sprintf("call_%d", i+1)
			}
			res, ok := byID[strings.TrimSpace(call.ID)]
			if !ok {
				res, ok = byName[strings.TrimSpace(call.Name)]
			}
			if !ok && i < len(turn.Results) {
				res = turn.Results[i]
			}
			content := string(res.Output)
			if res.Err != "" {
				content = res.Err
			}
			if content == "" {
				content = "{}"
			} else {
				content = clipString(content, maxToolResultChars)
			}
			msgs = append(msgs, map[string]any{
				"role":         "tool",
				"tool_call_id": id,
				"content":      content,
			})
		}
		return msgs
	}

	// No paired Calls — never emit orphan role=tool (provider 400). Fold any
	// leftover results into assistant text as working memory.
	text := strings.TrimSpace(turn.Text)
	if len(turn.Results) > 0 {
		var b strings.Builder
		if text != "" {
			b.WriteString(text)
			b.WriteString("\n\n")
		}
		b.WriteString("Working memory:\n")
		for _, res := range turn.Results {
			content := string(res.Output)
			if res.Err != "" {
				content = res.Err
			}
			content = strings.ReplaceAll(strings.TrimSpace(content), "\n", " ")
			if len(content) > 200 {
				content = content[:200] + "…"
			}
			if content == "" {
				continue
			}
			b.WriteString("- ")
			b.WriteString(strings.TrimSpace(res.Name))
			b.WriteString(": ")
			b.WriteString(content)
			b.WriteByte('\n')
		}
		text = b.String()
	}
	if text != "" {
		msgs = append(msgs, map[string]any{"role": "assistant", "content": clipString(text, maxAssistantChars)})
	}
	return msgs
}

type sseDelta struct {
	Content   string `json:"content"`
	ToolCalls []struct {
		Index    int    `json:"index"`
		ID       string `json:"id"`
		Type     string `json:"type"`
		Function struct {
			Name      string `json:"name"`
			Arguments string `json:"arguments"`
		} `json:"function"`
	} `json:"tool_calls"`
}

type sseChoice struct {
	Delta        sseDelta `json:"delta"`
	FinishReason *string  `json:"finish_reason"`
}

type sseChunk struct {
	Choices []sseChoice `json:"choices"`
}

type toolAcc struct {
	id, name, args string
}

func parseSSE(ctx context.Context, body io.Reader, out chan<- cognition.ModelEvent, nameMap map[string]string) {
	scanner := bufio.NewScanner(body)
	// Provider frames can be large when tool args stream in.
	buf := make([]byte, 0, 64*1024)
	scanner.Buffer(buf, 1024*1024)

	tools := map[int]*toolAcc{}
	sawDone := false

	resolveName := func(advertised string) string {
		if nameMap != nil {
			if real, ok := nameMap[advertised]; ok && real != "" {
				return real
			}
		}
		return advertised
	}

	emit := func(ev cognition.ModelEvent) bool {
		select {
		case <-ctx.Done():
			return false
		case out <- ev:
			return true
		}
	}

	for scanner.Scan() {
		select {
		case <-ctx.Done():
			return
		default:
		}
		line := scanner.Text()
		if line == "" || strings.HasPrefix(line, ":") {
			continue
		}
		if !strings.HasPrefix(line, "data:") {
			continue
		}
		payload := strings.TrimSpace(strings.TrimPrefix(line, "data:"))
		if payload == "[DONE]" {
			sawDone = true
			break
		}
		var chunk sseChunk
		if err := json.Unmarshal([]byte(payload), &chunk); err != nil {
			continue
		}
		if len(chunk.Choices) == 0 {
			continue
		}
		ch := chunk.Choices[0]
		if text := ch.Delta.Content; text != "" {
			if !emit(cognition.ModelEvent{Text: text}) {
				return
			}
		}
		for _, tc := range ch.Delta.ToolCalls {
			acc := tools[tc.Index]
			if acc == nil {
				acc = &toolAcc{}
				tools[tc.Index] = acc
			}
			if tc.ID != "" {
				acc.id = tc.ID
			}
			if tc.Function.Name != "" {
				acc.name = tc.Function.Name
			}
			if tc.Function.Arguments != "" {
				acc.args += tc.Function.Arguments
			}
		}
		if ch.FinishReason != nil {
			reason := strings.TrimSpace(*ch.FinishReason)
			switch reason {
			case "tool_calls":
				maxIdx := -1
				for i := range tools {
					if i > maxIdx {
						maxIdx = i
					}
				}
				for i := 0; i <= maxIdx; i++ {
					acc := tools[i]
					if acc == nil || acc.name == "" {
						continue
					}
					call := &cognition.ToolCall{
						ID:    acc.id,
						Name:  resolveName(acc.name),
						Input: []byte(acc.args),
					}
					if len(call.Input) == 0 {
						call.Input = []byte("{}")
					}
					if !emit(cognition.ModelEvent{ToolCall: call}) {
						return
					}
				}
				return
			case "stop":
				_ = emit(cognition.ModelEvent{Done: true})
				return
			case "length":
				// Hit max_tokens — engine auto-continues instead of ending the turn.
				_ = emit(cognition.ModelEvent{Done: true, Truncated: true})
				return
			}
		}
	}
	if sawDone {
		_ = emit(cognition.ModelEvent{Done: true})
	}
}
