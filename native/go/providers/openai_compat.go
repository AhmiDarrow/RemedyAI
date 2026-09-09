package providers

import (
	"bufio"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"strings"
	"time"
	"unicode/utf8"

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
	// NCtx is the physical context window for local models (0 = cloud / unknown).
	NCtx int
	// LocalFit forces FitLocalRequest even when BaseURL is not loopback.
	LocalFit bool
}

// ContextWindow reports the prompt window in tokens: the configured n_ctx, the
// REMEDY_LOCAL_CTX / REMEDY_N_CTX override, a conservative default for loopback
// runtimes, else the frontier cloud default.
func (c *OpenAICompat) ContextWindow() int {
	if c == nil {
		return 0
	}
	if c.NCtx > 0 {
		return c.NCtx
	}
	if n := envContextWindow(); n > 0 {
		return n
	}
	if c.LocalFit || IsLocalBaseURL(c.BaseURL) {
		return localDefaultContextWindow
	}
	return cognition.DefaultContextWindow
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
	messages := renderMessages(turn, c.advertisedName())
	toolSchemas := c.Tools
	if c.LocalFit || IsLocalBaseURL(base) {
		window := c.ContextWindow()
		if window <= 0 {
			window = localDefaultContextWindow
		}
		messages, toolSchemas, _ = FitLocalRequest(messages, toolSchemas, window)
	}
	payload := map[string]any{
		"model":          model,
		"messages":       messages,
		"stream":         true,
		"stream_options": map[string]any{"include_usage": true},
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
		// ToolCall.Name carries the advertised (sanitized) function name here;
		// the turn runner resolves it to the Tool ABI id once (ResolveToolName)
		// and records the wire name in ToolCall.Advertised. Everything after
		// that point — policy, approvals, the executor and the transcript —
		// sees only the ABI id; advertisedName maps it back on the way out.
		parseSSE(ctx, resp.Body, out)
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
				"parameters":  params,
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
	maxAssistantChars  = 12_000
)

// clipString keeps the head and tail of s within max bytes, cutting only on
// rune boundaries.
func clipString(s string, max int) string {
	if max <= 0 || len(s) <= max {
		return s
	}
	head := max * 2 / 3
	tail := max - head - 64
	if tail < 32 {
		return s[:runeFloor(s, max)] + "…"
	}
	return s[:runeFloor(s, head)] + fmt.Sprintf("\n…[truncated %d chars]…\n", len(s)-max) + s[runeCeil(s, len(s)-tail):]
}

// runeFloor returns the largest index <= i that starts a rune in s.
func runeFloor(s string, i int) int {
	for i > 0 && i < len(s) && !utf8.RuneStart(s[i]) {
		i--
	}
	return i
}

// runeCeil returns the smallest index >= i that starts a rune in s (or len(s)).
func runeCeil(s string, i int) int {
	for i < len(s) && !utf8.RuneStart(s[i]) {
		i++
	}
	return i
}

// advertisedName reverses ToolNameMap so a tool_use block (always a Tool ABI
// id) is echoed back under the function name the provider actually saw.
func (c *OpenAICompat) advertisedName() func(string) string {
	if c == nil || len(c.ToolNameMap) == 0 {
		return nil
	}
	reverse := make(map[string]string, len(c.ToolNameMap))
	for advertised, abi := range c.ToolNameMap {
		if _, ok := reverse[abi]; !ok {
			reverse[abi] = advertised
		}
	}
	return func(abi string) string {
		if advertised, ok := reverse[abi]; ok {
			return advertised
		}
		// Advertised under an earlier schema set (the runner narrows the
		// surface mid-build) — the sanitizer is deterministic, so it
		// reproduces the name the provider saw.
		return sanitizeToolName(abi)
	}
}

// renderMessages converts the append-only transcript into chat-completions
// messages. wireName maps a Tool ABI id to the advertised function name (nil
// keeps the ABI id, which is what a provider with no name map saw).
func renderMessages(turn cognition.Turn, wireName func(string) string) []map[string]any {
	msgs := make([]map[string]any, 0, len(turn.Messages)+2)
	if sys := strings.TrimSpace(turn.System); sys != "" {
		msgs = append(msgs, map[string]any{"role": "system", "content": sys})
	}
	state := &renderState{announced: map[string]bool{}, wireName: wireName}
	body := 0
	for _, m := range turn.Messages {
		var rendered []map[string]any
		if m.Role == cognition.RoleAssistant {
			rendered = state.assistant(m)
		} else {
			rendered = state.user(m)
		}
		msgs = append(msgs, rendered...)
		body += len(rendered)
	}
	if body == 0 {
		msgs = append(msgs, map[string]any{"role": "user", "content": "continue"})
	}
	return msgs
}

// renderState carries the tool_call ids the assistant has announced so far, so
// a tool_result whose tool_use was compacted away never becomes an orphan
// role=tool message (HTTP 400 on every OpenAI-compatible provider).
type renderState struct {
	announced map[string]bool
	unnamed   []string
	seq       int
	wireName  func(string) string
}

func (s *renderState) assistant(m cognition.Message) []map[string]any {
	var text strings.Builder
	toolCalls := make([]map[string]any, 0, len(m.Blocks))
	for _, b := range m.Blocks {
		switch b.Type {
		case cognition.BlockText:
			text.WriteString(b.Text)
		case cognition.BlockThinking:
			// Reasoning is never sent back: no chat-completions field carries it.
		case cognition.BlockToolUse:
			id := strings.TrimSpace(b.ID)
			if id == "" {
				s.seq++
				id = fmt.Sprintf("call_%d", s.seq)
				s.unnamed = append(s.unnamed, id)
			}
			s.announced[id] = true
			args := strings.TrimSpace(string(b.Input))
			if args == "" {
				args = "{}"
			} else {
				args = clipString(args, maxToolArgChars)
			}
			name := b.Name
			if s.wireName != nil {
				name = s.wireName(b.Name)
			}
			toolCalls = append(toolCalls, map[string]any{
				"id":   id,
				"type": "function",
				"function": map[string]any{
					"name":      name,
					"arguments": args,
				},
			})
		}
	}
	content := clipString(strings.TrimSpace(text.String()), maxAssistantChars)
	if content == "" && len(toolCalls) == 0 {
		return nil
	}
	msg := map[string]any{"role": "assistant"}
	if content != "" {
		msg["content"] = content
	}
	if len(toolCalls) > 0 {
		msg["tool_calls"] = toolCalls
	}
	return []map[string]any{msg}
}

func (s *renderState) user(m cognition.Message) []map[string]any {
	out := make([]map[string]any, 0, len(m.Blocks)+1)
	var parts []map[string]any
	addText := func(text string) {
		if strings.TrimSpace(text) == "" {
			return
		}
		parts = append(parts, map[string]any{"type": "text", "text": text})
	}
	for _, b := range m.Blocks {
		switch b.Type {
		case cognition.BlockText:
			addText(b.Text)
		case cognition.BlockImage:
			parts = append(parts, imagePart(b))
		case cognition.BlockToolResult:
			id := s.resolveToolUseID(b.ToolUseID)
			content := clipString(toolResultText(b), maxToolResultChars)
			if id != "" {
				out = append(out, map[string]any{
					"role":         "tool",
					"tool_call_id": id,
					"content":      content,
				})
			} else {
				addText("[tool result] " + content)
			}
			// A tool message cannot carry an image, so screenshots trail the
			// batch as a user message in the OpenAI vision shape.
			for _, inner := range b.Content {
				if inner.Type != cognition.BlockImage {
					continue
				}
				if id != "" {
					addText("[image returned by tool call " + id + "]")
				}
				parts = append(parts, imagePart(inner))
			}
		}
	}
	if len(parts) > 0 {
		out = append(out, map[string]any{"role": "user", "content": userContent(parts)})
	}
	return out
}

// resolveToolUseID returns the wire tool_call_id for a result, or "" when no
// assistant message announced it.
func (s *renderState) resolveToolUseID(toolUseID string) string {
	id := strings.TrimSpace(toolUseID)
	if id != "" {
		if s.announced[id] {
			return id
		}
		return ""
	}
	if len(s.unnamed) == 0 {
		return ""
	}
	id, s.unnamed = s.unnamed[0], s.unnamed[1:]
	return id
}

// toolResultText flattens a tool_result's text content; images are carried
// separately by the trailing user message.
func toolResultText(b cognition.Block) string {
	var out strings.Builder
	if b.IsError {
		out.WriteString("[error] ")
	}
	for _, inner := range b.Content {
		if inner.Type == cognition.BlockText {
			out.WriteString(inner.Text)
		}
	}
	text := strings.TrimSpace(out.String())
	if text == "" || text == "[error]" {
		return text + "(no output)"
	}
	return text
}

func imagePart(b cognition.Block) map[string]any {
	mediaType := strings.TrimSpace(b.MediaType)
	if mediaType == "" {
		mediaType = "image/png"
	}
	return map[string]any{
		"type": "image_url",
		"image_url": map[string]any{
			"url": "data:" + mediaType + ";base64," + base64.StdEncoding.EncodeToString(b.Data),
		},
	}
}

// userContent keeps the plain-string shape when a message is text only (the
// widest provider compatibility) and switches to content parts for images.
func userContent(parts []map[string]any) any {
	var text strings.Builder
	for _, p := range parts {
		if p["type"] != "text" {
			return parts
		}
		if text.Len() > 0 {
			text.WriteString("\n\n")
		}
		text.WriteString(fmt.Sprint(p["text"]))
	}
	return text.String()
}

type sseDelta struct {
	Content          string `json:"content"`
	ReasoningContent string `json:"reasoning_content"`
	// Reasoning is the OpenRouter spelling of reasoning_content.
	Reasoning string `json:"reasoning"`
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

type sseUsage struct {
	PromptTokens     int `json:"prompt_tokens"`
	CompletionTokens int `json:"completion_tokens"`
	TotalTokens      int `json:"total_tokens"`
}

type sseChunk struct {
	Choices []sseChoice `json:"choices"`
	Usage   *sseUsage   `json:"usage"`
}

type toolAcc struct {
	id, name, args string
}

// sseParser accumulates one chat-completions stream into ModelEvents.
type sseParser struct {
	ctx      context.Context
	out      chan<- cognition.ModelEvent
	tools    map[int]*toolAcc
	finished bool // a finish_reason was seen
	sawUsage bool
}

func (p *sseParser) emit(ev cognition.ModelEvent) bool {
	select {
	case <-p.ctx.Done():
		return false
	case p.out <- ev:
		return true
	}
}

// flushTools emits accumulated tool calls in index order and clears them.
func (p *sseParser) flushTools() bool {
	maxIdx := -1
	for i := range p.tools {
		if i > maxIdx {
			maxIdx = i
		}
	}
	for i := 0; i <= maxIdx; i++ {
		acc := p.tools[i]
		if acc == nil || acc.name == "" {
			continue
		}
		call := &cognition.ToolCall{ID: acc.id, Name: acc.name, Input: []byte(acc.args)}
		if len(call.Input) == 0 {
			call.Input = []byte("{}")
		}
		if !p.emit(cognition.ModelEvent{ToolCall: call}) {
			return false
		}
	}
	p.tools = map[int]*toolAcc{}
	return true
}

func (p *sseParser) accumulate(delta sseDelta) {
	for _, tc := range delta.ToolCalls {
		acc := p.tools[tc.Index]
		if acc == nil {
			acc = &toolAcc{}
			p.tools[tc.Index] = acc
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
}

// finish handles a finish_reason: tool calls are flushed for every reason;
// stop/length also complete the round (length marks it truncated).
func (p *sseParser) finish(reason string) bool {
	p.finished = true
	if !p.flushTools() {
		return false
	}
	switch reason {
	case "length":
		return p.emit(cognition.ModelEvent{Done: true, Truncated: true})
	case "tool_calls", "function_call":
		return true
	default:
		return p.emit(cognition.ModelEvent{Done: true})
	}
}

// chunk processes one data payload. It returns false once nothing more is
// expected from the stream or the context is done.
func (p *sseParser) chunk(c sseChunk) bool {
	if c.Usage != nil {
		p.sawUsage = true
		if !p.emit(cognition.ModelEvent{Usage: &cognition.Usage{
			PromptTokens:     c.Usage.PromptTokens,
			CompletionTokens: c.Usage.CompletionTokens,
			TotalTokens:      c.Usage.TotalTokens,
		}}) {
			return false
		}
	}
	if len(c.Choices) == 0 {
		// Usage-only trailer after finish_reason: the round is complete.
		return !(p.finished && p.sawUsage)
	}
	ch := c.Choices[0]
	thought := ch.Delta.ReasoningContent
	if thought == "" {
		thought = ch.Delta.Reasoning
	}
	if thought != "" {
		if !p.emit(cognition.ModelEvent{Thinking: thought}) {
			return false
		}
	}
	if text := ch.Delta.Content; text != "" {
		if !p.emit(cognition.ModelEvent{Text: text}) {
			return false
		}
	}
	p.accumulate(ch.Delta)
	if ch.FinishReason != nil {
		if reason := strings.TrimSpace(*ch.FinishReason); reason != "" {
			if !p.finish(reason) {
				return false
			}
			return !p.sawUsage
		}
	}
	return true
}

// parseSSE streams chat-completions SSE frames into out. Tool calls are
// flushed on any finish_reason and at [DONE] or end of body; a read error
// (frame over the scanner limit, broken connection) ends the stream without
// Done so the engine reports it as incomplete.
func parseSSE(ctx context.Context, body io.Reader, out chan<- cognition.ModelEvent) {
	scanner := bufio.NewScanner(body)
	// Provider frames can be large when tool args stream in.
	buf := make([]byte, 0, 64*1024)
	scanner.Buffer(buf, 1024*1024)

	p := &sseParser{ctx: ctx, out: out, tools: map[int]*toolAcc{}}
	sawDone := false
	for scanner.Scan() {
		select {
		case <-ctx.Done():
			return
		default:
		}
		line := scanner.Text()
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
		if !p.chunk(chunk) {
			return
		}
	}
	if scanner.Err() != nil || p.finished {
		return
	}
	if !p.flushTools() {
		return
	}
	if sawDone {
		_ = p.emit(cognition.ModelEvent{Done: true})
	}
}
