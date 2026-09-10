package providers

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"

	"github.com/anthropics/anthropic-sdk-go"
	"github.com/anthropics/anthropic-sdk-go/option"
	"github.com/anthropics/anthropic-sdk-go/packages/ssestream"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

// TurnShape is how much thinking the turn is worth. It selects
// output_config.effort and the system cache TTL.
type TurnShape string

const (
	// ShapeAuto derives the shape from the request: a turn with no tools is
	// chat, a turn with tools is build.
	ShapeAuto    TurnShape = ""
	ShapeChat    TurnShape = "chat"
	ShapePlan    TurnShape = "plan"
	ShapeBuild   TurnShape = "build"
	ShapeMission TurnShape = "mission"
)

const (
	// anthropicDefaultMaxTokens is the per-round output ceiling. Streaming is
	// always on, so this is bounded by the model's output cap, not by an HTTP
	// timeout. 32k leaves room for a long edit without truncating mid-file.
	anthropicDefaultMaxTokens = 32_000
	// anthropicPauseResendLimit bounds pause_turn resends inside one round so
	// a server that keeps pausing cannot spin the turn.
	anthropicPauseResendLimit = 2
	// anthropicFrontierWindow is the prompt window of the current Claude line.
	anthropicFrontierWindow = 1_000_000
	// anthropicHaikuWindow is the Haiku 4.5 prompt window.
	anthropicHaikuWindow = 200_000
)

// systemVolatileMarkers are the section headers Python's prompt.assemble
// appends after the stable operational prompt (see
// src/remedy/runtime/prompt_assemble.py: _HISTORY_HEADER, EPOCH_BRIEF_MARKER).
// Everything from the earliest marker onwards changes every turn and must sit
// after the cache breakpoint; everything before it is byte-stable and is what
// prompt caching pays for.
var systemVolatileMarkers = []string{
	"[Turn context]",
	"Recent conversation (oldest first;",
	"[Session Brief · epoch working memory]",
}

// errStreamDropped marks a round whose SSE body ended before the model
// reported a stop_reason. It is retried once at the round level.
var errStreamDropped = errors.New("anthropic: stream ended before stop_reason")

// Anthropic is a first-class Claude adapter on the official Go SDK: native
// block rendering, prompt caching, adaptive thinking, strict tools, image
// blocks, stop_reason granularity and per-round usage.
type Anthropic struct {
	// BaseURL overrides the API root. Accepts the root
	// ("https://api.anthropic.com") or the Remedy-configured "/v1" form; the
	// SDK appends "v1/messages" itself. Empty uses the production root.
	BaseURL string
	// APIKey comes from the owner's secret store. Required — this adapter
	// never falls back to ambient environment credentials.
	APIKey string
	Model  string
	// HTTPClient is injected by tests (httptest) and by callers that need a
	// proxy. Nil uses the shared streaming client.
	HTTPClient *http.Client
	// MaxTokens is the per-round output ceiling (0 = anthropicDefaultMaxTokens).
	MaxTokens int
	// Shape selects effort and cache TTL. ShapeAuto derives it from the turn.
	Shape TurnShape
	// SystemStable overrides the stable/volatile split of Turn.System. Empty
	// splits on the markers prompt.assemble writes.
	SystemStable string

	// tools is the advertised Tool ABI surface, set through SetTools.
	tools []anthropic.ToolUnionParam
	// abiByWire maps the advertised (dot-free) tool name back to the Tool ABI
	// id. Approvals are fingerprinted on the ABI id, so every ToolCall this
	// adapter emits carries the ABI id in Name and the wire name in Advertised.
	abiByWire map[string]string
	wireByABI map[string]string
}

// ID identifies the provider binding.
func (a *Anthropic) ID() string { return "anthropic" }

// SetTools advertises the Tool ABI surface. Names are sanitized to the
// Anthropic tool-name charset (^[a-zA-Z0-9_-]+$, so dotted ABI ids cannot go
// on the wire) and mapped back on every tool_use block.
func (a *Anthropic) SetTools(list []RegistryTool) {
	if a == nil {
		return
	}
	if len(list) == 0 {
		a.tools, a.abiByWire, a.wireByABI = nil, nil, nil
		return
	}
	tools := make([]anthropic.ToolUnionParam, 0, len(list))
	abiByWire := make(map[string]string, len(list))
	wireByABI := make(map[string]string, len(list))
	used := map[string]struct{}{}
	for _, d := range list {
		wire := uniqueSanitizedToolName(d.ID, used)
		used[wire] = struct{}{}
		abiByWire[wire] = d.ID
		if _, ok := wireByABI[d.ID]; !ok {
			wireByABI[d.ID] = wire
		}
		tool := anthropic.ToolParam{Name: wire, InputSchema: toolInputSchema(d.InputSchema)}
		if desc := strings.TrimSpace(d.Description); desc != "" {
			tool.Description = anthropic.String(desc)
		}
		if schemaIsStrict(d.InputSchema) {
			tool.Strict = anthropic.Bool(true)
		}
		tools = append(tools, anthropic.ToolUnionParam{OfTool: &tool})
	}
	// Prompt caching breakpoint: tools render before system and messages, so a
	// marker on the last tool caches the whole schema prefix.
	if last := tools[len(tools)-1].OfTool; last != nil {
		last.CacheControl = anthropic.NewCacheControlEphemeralParam()
	}
	a.tools, a.abiByWire, a.wireByABI = tools, abiByWire, wireByABI
}

// AdvertisedToolCount reports how many tools the next request will carry.
// Diagnostics and tests read it; the rendered schemas stay adapter-private.
func (a *Anthropic) AdvertisedToolCount() int {
	if a == nil {
		return 0
	}
	return len(a.tools)
}

// ContextWindow reports the prompt window in tokens.
func (a *Anthropic) ContextWindow() int {
	if a == nil {
		return 0
	}
	if n := envContextWindow(); n > 0 {
		return n
	}
	if strings.Contains(strings.ToLower(a.Model), "haiku") {
		return anthropicHaikuWindow
	}
	return anthropicFrontierWindow
}

// Stream renders the transcript natively and streams one turn's rounds.
//
// The first connect happens synchronously so an auth or billing failure is a
// Stream error the runner can fall back on, exactly like the OpenAI adapter.
func (a *Anthropic) Stream(ctx context.Context, turn cognition.Turn) (<-chan cognition.ModelEvent, error) {
	if a == nil {
		return nil, errors.New("anthropic: nil adapter")
	}
	if strings.TrimSpace(a.Model) == "" {
		return nil, errors.New("anthropic: model id is required")
	}
	if strings.TrimSpace(a.APIKey) == "" {
		return nil, errors.New("anthropic: API key is required")
	}
	client := a.client()
	params := a.buildParams(turn)

	stream := client.Messages.NewStreaming(ctx, params)
	first, ok := advance(stream)
	if !ok {
		defer stream.Close()
		if err := stream.Err(); err != nil {
			return nil, anthropicError(err)
		}
		return nil, errors.New("anthropic: stream closed before the first event")
	}

	out := make(chan cognition.ModelEvent, 16)
	go func() {
		defer close(out)
		a.pump(ctx, client, params, stream, first, out)
	}()
	return out, nil
}

func (a *Anthropic) client() anthropic.Client {
	opts := []option.RequestOption{
		// Remedy resolves credentials itself; ambient ANTHROPIC_* env vars and
		// on-disk CLI profiles must never decide which account a turn bills.
		option.WithoutEnvironmentDefaults(),
		option.WithAPIKey(strings.TrimSpace(a.APIKey)),
	}
	if base := anthropicAPIRoot(a.BaseURL); base != "" {
		opts = append(opts, option.WithBaseURL(base))
	}
	if a.HTTPClient != nil {
		opts = append(opts, option.WithHTTPClient(a.HTTPClient))
	} else {
		opts = append(opts, option.WithHTTPClient(defaultStreamHTTPClient()))
	}
	return anthropic.NewClient(opts...)
}

// anthropicAPIRoot normalizes a configured base URL to the API root. Remedy
// stores OpenAI-compatible URLs ("https://api.anthropic.com/v1"); the SDK
// appends "v1/messages" itself, so the version segment is stripped.
func anthropicAPIRoot(raw string) string {
	base := strings.TrimSpace(raw)
	if base == "" {
		return ""
	}
	base = strings.TrimRight(base, "/")
	if strings.HasSuffix(base, "/v1") {
		base = strings.TrimSuffix(base, "/v1")
	}
	return base + "/"
}

// pump drives the rounds of one turn: deltas out as they arrive, then usage,
// tool calls and the stop_reason verdict.
func (a *Anthropic) pump(
	ctx context.Context,
	client anthropic.Client,
	params anthropic.MessageNewParams,
	stream *ssestream.Stream[anthropic.MessageStreamEventUnion],
	first anthropic.MessageStreamEventUnion,
	out chan<- cognition.ModelEvent,
) {
	emit := func(ev cognition.ModelEvent) bool {
		select {
		case <-ctx.Done():
			return false
		case out <- ev:
			return true
		}
	}
	// reconnect re-opens the stream with the same transcript. It returns false
	// once the round can no longer be served.
	reconnect := func() bool {
		stream.Close()
		stream = client.Messages.NewStreaming(ctx, params)
		ev, ok := advance(stream)
		if !ok {
			stream.Close()
			return false
		}
		first = ev
		return true
	}

	pauses := 0
	for {
		msg, err := drainRound(ctx, stream, first, emit)
		if err != nil {
			stream.Close()
			// A body that dies mid-round is the common failure of a long
			// build. Retry the round once with the same transcript rather
			// than losing the turn; the engine sees one continuous round.
			if !errors.Is(err, errStreamDropped) {
				return
			}
			if !emit(cognition.ModelEvent{Thinking: "\n[stream dropped mid-round — retrying once]\n"}) {
				return
			}
			if !reconnect() {
				return
			}
			if msg, err = drainRound(ctx, stream, first, emit); err != nil {
				stream.Close()
				return
			}
		}
		stream.Close()

		if usage := roundUsage(msg.Usage); usage != nil && !emit(cognition.ModelEvent{Usage: usage}) {
			return
		}

		switch msg.StopReason {
		case anthropic.StopReasonToolUse:
			a.emitToolCalls(msg, emit)
			return
		case anthropic.StopReasonMaxTokens:
			a.emitToolCalls(msg, emit)
			emit(cognition.ModelEvent{Done: true, Truncated: true})
			return
		case anthropic.StopReasonPauseTurn:
			if pauses >= anthropicPauseResendLimit {
				emit(cognition.ModelEvent{Done: true})
				return
			}
			pauses++
			if !reconnect() {
				return
			}
			continue
		case anthropic.StopReasonRefusal:
			emit(cognition.ModelEvent{
				Status: refusalStatus(msg.StopDetails),
				Text:   refusalText(msg.StopDetails),
				Done:   true,
			})
			return
		case anthropic.StopReasonModelContextWindowExceeded:
			emit(cognition.ModelEvent{
				Status: "The transcript no longer fits the model's context window — compacting before the next turn.",
				Done:   true,
			})
			return
		default: // end_turn, stop_sequence
			a.emitToolCalls(msg, emit)
			emit(cognition.ModelEvent{Done: true})
			return
		}
	}
}

// drainRound streams one model round into emit and returns the accumulated
// message. A body that ends before stop_reason returns errStreamDropped.
func drainRound(
	ctx context.Context,
	stream *ssestream.Stream[anthropic.MessageStreamEventUnion],
	first anthropic.MessageStreamEventUnion,
	emit func(cognition.ModelEvent) bool,
) (*anthropic.Message, error) {
	acc := anthropic.Message{}
	handle := func(ev anthropic.MessageStreamEventUnion) bool {
		if err := acc.Accumulate(ev); err != nil {
			return true // a block we do not model; keep streaming
		}
		delta, ok := ev.AsAny().(anthropic.ContentBlockDeltaEvent)
		if !ok {
			return true
		}
		switch d := delta.Delta.AsAny().(type) {
		case anthropic.TextDelta:
			if d.Text != "" {
				return emit(cognition.ModelEvent{Text: d.Text})
			}
		case anthropic.ThinkingDelta:
			if d.Thinking != "" {
				return emit(cognition.ModelEvent{Thinking: d.Thinking})
			}
		}
		return true
	}
	if !handle(first) {
		return nil, ctx.Err()
	}
	for stream.Next() {
		if !handle(stream.Current()) {
			return nil, ctx.Err()
		}
	}
	if err := stream.Err(); err != nil {
		return nil, errors.Join(errStreamDropped, anthropicError(err))
	}
	if acc.StopReason == "" {
		return nil, errStreamDropped
	}
	return &acc, nil
}

// emitToolCalls turns the round's tool_use blocks into ToolCalls carrying the
// Tool ABI id, which is what policy, approvals and the executor fingerprint.
func (a *Anthropic) emitToolCalls(msg *anthropic.Message, emit func(cognition.ModelEvent) bool) {
	for _, block := range msg.Content {
		use, ok := block.AsAny().(anthropic.ToolUseBlock)
		if !ok {
			continue
		}
		input := json.RawMessage(use.Input)
		if len(input) == 0 {
			input = json.RawMessage("{}")
		}
		call := &cognition.ToolCall{
			ID:         use.ID,
			Name:       a.abiName(use.Name),
			Advertised: use.Name,
			Input:      input,
		}
		if !emit(cognition.ModelEvent{ToolCall: call}) {
			return
		}
	}
}

// abiName maps an advertised tool name back to the Tool ABI id.
func (a *Anthropic) abiName(wire string) string {
	if a != nil {
		if abi, ok := a.abiByWire[wire]; ok && abi != "" {
			return abi
		}
	}
	return wire
}

// wireName maps a Tool ABI id to the name the model was shown, so a replayed
// tool_use block matches the tool definition it came from.
func (a *Anthropic) wireName(abi string) string {
	if a != nil {
		if wire, ok := a.wireByABI[abi]; ok && wire != "" {
			return wire
		}
	}
	return sanitizeToolName(abi)
}

// buildParams renders one request: cached system, cached tools, native blocks,
// adaptive thinking and the effort the turn's shape is worth.
func (a *Anthropic) buildParams(turn cognition.Turn) anthropic.MessageNewParams {
	shape := a.shape()
	model := strings.TrimSpace(a.Model)
	maxTokens := a.MaxTokens
	if maxTokens <= 0 {
		maxTokens = anthropicDefaultMaxTokens
	}
	params := anthropic.MessageNewParams{
		Model:     anthropic.Model(model),
		MaxTokens: int64(maxTokens),
		Messages:  a.renderMessages(turn),
		System:    a.renderSystem(turn, shape),
		Tools:     a.tools,
	}
	// Adaptive thinking with summaries is what feeds @@thinking:. Pre-4.6
	// models reject it, and none of them are worth a fixed budget_tokens here.
	if supportsAdaptiveThinking(model) {
		params.Thinking = anthropic.ThinkingConfigParamUnion{
			OfAdaptive: &anthropic.ThinkingConfigAdaptiveParam{
				Display: anthropic.ThinkingConfigAdaptiveDisplaySummarized,
			},
		}
		params.OutputConfig = anthropic.OutputConfigParam{Effort: effortFor(shape, model)}
	}
	// tool_choice is never sent: Fable 5.1 rejects a forced choice with a 400,
	// and "auto" is the default everywhere else.
	return params
}

func (a *Anthropic) shape() TurnShape {
	if a.Shape != ShapeAuto {
		return a.Shape
	}
	if len(a.tools) == 0 {
		return ShapeChat
	}
	return ShapeBuild
}

// effortFor maps the turn shape to output_config.effort. The 4.6 generation
// has no xhigh level, so a build there runs at high.
func effortFor(shape TurnShape, model string) anthropic.OutputConfigEffort {
	switch shape {
	case ShapeChat:
		return anthropic.OutputConfigEffortMedium
	case ShapePlan:
		return anthropic.OutputConfigEffortHigh
	default: // build, mission
		if !supportsXHighEffort(model) {
			return anthropic.OutputConfigEffortHigh
		}
		return anthropic.OutputConfigEffortXhigh
	}
}

// supportsAdaptiveThinking reports whether the model takes
// thinking:{type:"adaptive"} and output_config.effort. Both landed with the
// 4.6 generation; Haiku 4.5 and older reject them.
func supportsAdaptiveThinking(model string) bool {
	m := strings.ToLower(strings.TrimSpace(model))
	if m == "" || strings.Contains(m, "haiku") {
		return false
	}
	for _, legacy := range []string{"-4-5", "-4-1", "-3-5", "-3-7", "-2-"} {
		if strings.Contains(m, legacy) {
			return false
		}
	}
	return true
}

// supportsXHighEffort reports whether the model has the xhigh effort level,
// which arrived after the 4.6 generation.
func supportsXHighEffort(model string) bool {
	return !strings.Contains(strings.ToLower(strings.TrimSpace(model)), "-4-6")
}

// renderSystem splits the assembled system prompt into the stable operational
// prompt — which carries the cache breakpoint — and the volatile tail.
func (a *Anthropic) renderSystem(turn cognition.Turn, shape TurnShape) []anthropic.TextBlockParam {
	stable, volatile := a.splitSystem(turn.System)
	if stable == "" && volatile == "" {
		return nil
	}
	if stable == "" {
		stable, volatile = volatile, ""
	}
	cache := anthropic.NewCacheControlEphemeralParam()
	if shape == ShapeBuild || shape == ShapeMission {
		// A build runs for hours off one operational prompt; the 5m default
		// would expire between rounds that stop for approval or a long test.
		cache.TTL = anthropic.CacheControlEphemeralTTLTTL1h
	}
	blocks := []anthropic.TextBlockParam{{Text: stable, CacheControl: cache}}
	if volatile != "" {
		blocks = append(blocks, anthropic.TextBlockParam{Text: volatile})
	}
	return blocks
}

// splitSystem cuts the assembled prompt at the earliest volatile marker.
func (a *Anthropic) splitSystem(system string) (stable, volatile string) {
	full := strings.TrimSpace(system)
	if full == "" {
		return "", ""
	}
	if fixed := strings.TrimSpace(a.SystemStable); fixed != "" {
		if rest, ok := strings.CutPrefix(full, fixed); ok {
			return fixed, strings.TrimSpace(rest)
		}
		return full, ""
	}
	cut := -1
	for _, marker := range systemVolatileMarkers {
		if i := strings.Index(full, marker); i >= 0 && (cut < 0 || i < cut) {
			cut = i
		}
	}
	if cut <= 0 {
		return full, ""
	}
	return strings.TrimSpace(full[:cut]), strings.TrimSpace(full[cut:])
}

// renderMessages maps the append-only transcript onto Anthropic blocks. The
// mapping is 1:1 except that tool_result blocks ride inside a user message and
// thinking blocks are dropped — replaying one needs the signature the engine
// does not carry, and a modified thinking block is a 400.
func (a *Anthropic) renderMessages(turn cognition.Turn) []anthropic.MessageParam {
	out := make([]anthropic.MessageParam, 0, len(turn.Messages))
	for _, m := range turn.Messages {
		var blocks []anthropic.ContentBlockParamUnion
		if m.Role == cognition.RoleAssistant {
			blocks = a.assistantBlocks(m)
		} else {
			blocks = a.userBlocks(m)
		}
		if len(blocks) == 0 {
			continue
		}
		role := anthropic.MessageParamRoleUser
		if m.Role == cognition.RoleAssistant {
			role = anthropic.MessageParamRoleAssistant
		}
		out = append(out, anthropic.MessageParam{Role: role, Content: blocks})
	}
	// The request must start with a user turn and must not end on an assistant
	// turn — the 4.6+ models reject a trailing assistant message as a prefill.
	if len(out) == 0 || out[len(out)-1].Role == anthropic.MessageParamRoleAssistant {
		out = append(out, anthropic.NewUserMessage(anthropic.NewTextBlock("continue")))
	}
	if out[0].Role != anthropic.MessageParamRoleUser {
		out = append([]anthropic.MessageParam{
			anthropic.NewUserMessage(anthropic.NewTextBlock("continue")),
		}, out...)
	}
	markLastUserBreakpoint(out)
	return out
}

func (a *Anthropic) assistantBlocks(m cognition.Message) []anthropic.ContentBlockParamUnion {
	blocks := make([]anthropic.ContentBlockParamUnion, 0, len(m.Blocks))
	for _, b := range m.Blocks {
		switch b.Type {
		case cognition.BlockText:
			if strings.TrimSpace(b.Text) != "" {
				blocks = append(blocks, anthropic.NewTextBlock(b.Text))
			}
		case cognition.BlockToolUse:
			input := json.RawMessage(b.Input)
			if len(input) == 0 {
				input = json.RawMessage("{}")
			}
			blocks = append(blocks, anthropic.NewToolUseBlock(b.ID, input, a.wireName(b.Name)))
		}
	}
	return blocks
}

func (a *Anthropic) userBlocks(m cognition.Message) []anthropic.ContentBlockParamUnion {
	blocks := make([]anthropic.ContentBlockParamUnion, 0, len(m.Blocks))
	for _, b := range m.Blocks {
		switch b.Type {
		case cognition.BlockText:
			if strings.TrimSpace(b.Text) != "" {
				blocks = append(blocks, anthropic.NewTextBlock(b.Text))
			}
		case cognition.BlockImage:
			blocks = append(blocks, imageBlock(b))
		case cognition.BlockToolResult:
			blocks = append(blocks, toolResultBlock(b))
		}
	}
	return blocks
}

// toolResultBlock renders one executed tool result. Images ride inside the
// result itself — no trailing user message is needed here.
func toolResultBlock(b cognition.Block) anthropic.ContentBlockParamUnion {
	result := anthropic.ToolResultBlockParam{ToolUseID: b.ToolUseID}
	if b.IsError {
		result.IsError = anthropic.Bool(true)
	}
	for _, inner := range b.Content {
		switch inner.Type {
		case cognition.BlockText:
			if strings.TrimSpace(inner.Text) == "" {
				continue
			}
			text := clipString(inner.Text, maxToolResultChars)
			result.Content = append(result.Content, anthropic.ToolResultBlockParamContentUnion{
				OfText: &anthropic.TextBlockParam{Text: text},
			})
		case cognition.BlockImage:
			img := imageBlockParam(inner)
			result.Content = append(result.Content, anthropic.ToolResultBlockParamContentUnion{
				OfImage: &img,
			})
		}
	}
	if len(result.Content) == 0 {
		result.Content = append(result.Content, anthropic.ToolResultBlockParamContentUnion{
			OfText: &anthropic.TextBlockParam{Text: "(no output)"},
		})
	}
	return anthropic.ContentBlockParamUnion{OfToolResult: &result}
}

func imageBlock(b cognition.Block) anthropic.ContentBlockParamUnion {
	img := imageBlockParam(b)
	return anthropic.ContentBlockParamUnion{OfImage: &img}
}

func imageBlockParam(b cognition.Block) anthropic.ImageBlockParam {
	mediaType := strings.TrimSpace(b.MediaType)
	switch mediaType {
	case "image/png", "image/jpeg", "image/gif", "image/webp":
	default:
		mediaType = "image/png"
	}
	return anthropic.ImageBlockParam{
		Source: anthropic.ImageBlockParamSourceUnion{
			OfBase64: &anthropic.Base64ImageSourceParam{
				Data:      base64.StdEncoding.EncodeToString(b.Data),
				MediaType: anthropic.Base64ImageSourceMediaType(mediaType),
			},
		},
	}
}

// markLastUserBreakpoint puts the messages cache breakpoint on the last block
// of the last user message, so every completed round is served from cache.
func markLastUserBreakpoint(msgs []anthropic.MessageParam) {
	for i := len(msgs) - 1; i >= 0; i-- {
		if msgs[i].Role != anthropic.MessageParamRoleUser || len(msgs[i].Content) == 0 {
			continue
		}
		block := &msgs[i].Content[len(msgs[i].Content)-1]
		switch {
		case block.OfText != nil:
			block.OfText.CacheControl = anthropic.NewCacheControlEphemeralParam()
		case block.OfToolResult != nil:
			block.OfToolResult.CacheControl = anthropic.NewCacheControlEphemeralParam()
		case block.OfImage != nil:
			block.OfImage.CacheControl = anthropic.NewCacheControlEphemeralParam()
		}
		return
	}
}

// toolInputSchema carries the registry's JSON Schema through unchanged:
// properties and required are typed fields, everything else (notably
// additionalProperties, which strict mode requires) rides in ExtraFields.
func toolInputSchema(raw json.RawMessage) anthropic.ToolInputSchemaParam {
	schema := anthropic.ToolInputSchemaParam{Properties: map[string]any{}}
	var decoded map[string]any
	if len(raw) == 0 || json.Unmarshal(raw, &decoded) != nil || decoded == nil {
		return schema
	}
	if props, ok := decoded["properties"]; ok {
		schema.Properties = props
	}
	if required, ok := decoded["required"].([]any); ok {
		for _, item := range required {
			if name, ok := item.(string); ok {
				schema.Required = append(schema.Required, name)
			}
		}
	}
	extra := map[string]any{}
	for k, v := range decoded {
		switch k {
		case "type", "properties", "required":
		default:
			extra[k] = v
		}
	}
	if len(extra) > 0 {
		schema.ExtraFields = extra
	}
	return schema
}

// schemaIsStrict reports whether the schema is closed enough for strict tool
// use: additionalProperties:false plus a non-empty required list.
func schemaIsStrict(raw json.RawMessage) bool {
	var decoded map[string]any
	if len(raw) == 0 || json.Unmarshal(raw, &decoded) != nil || decoded == nil {
		return false
	}
	closed, ok := decoded["additionalProperties"].(bool)
	if !ok || closed {
		return false
	}
	required, ok := decoded["required"].([]any)
	return ok && len(required) > 0
}

// roundUsage maps the round's token accounting, cache read/write included.
func roundUsage(u anthropic.Usage) *cognition.Usage {
	prompt := int(u.InputTokens)
	cacheRead := int(u.CacheReadInputTokens)
	cacheWrite := int(u.CacheCreationInputTokens)
	completion := int(u.OutputTokens)
	if prompt == 0 && completion == 0 && cacheRead == 0 && cacheWrite == 0 {
		return nil
	}
	return &cognition.Usage{
		PromptTokens:     prompt,
		CompletionTokens: completion,
		// Cached input is billed input: the total has to include it or the
		// status bar under-reports every cached round.
		TotalTokens:      prompt + completion + cacheRead + cacheWrite,
		CacheReadTokens:  cacheRead,
		CacheWriteTokens: cacheWrite,
	}
}

func refusalStatus(details anthropic.RefusalStopDetails) string {
	category := strings.TrimSpace(string(details.Category))
	if category == "" {
		return "Claude declined this request."
	}
	return "Claude declined this request (" + category + ")."
}

func refusalText(details anthropic.RefusalStopDetails) string {
	text := refusalStatus(details)
	if explanation := strings.TrimSpace(details.Explanation); explanation != "" {
		text += " " + explanation
	}
	return text
}

// advance pulls the next event, reporting whether one arrived.
func advance(stream *ssestream.Stream[anthropic.MessageStreamEventUnion]) (anthropic.MessageStreamEventUnion, bool) {
	if !stream.Next() {
		return anthropic.MessageStreamEventUnion{}, false
	}
	return stream.Current(), true
}

// anthropicError formats an SDK error so the runner's provider-fallback check
// sees the status the same way it sees an OpenAI-compatible one.
func anthropicError(err error) error {
	if err == nil {
		return nil
	}
	var apierr *anthropic.Error
	if errors.As(err, &apierr) && apierr.StatusCode > 0 {
		body := strings.TrimSpace(apierr.RawJSON())
		if len(body) > 512 {
			body = body[:512]
		}
		return fmt.Errorf("anthropic HTTP %d: %s", apierr.StatusCode, body)
	}
	return fmt.Errorf("anthropic: %w", err)
}
