package cognition

import (
	"encoding/json"
	"strings"
)

// Role is the author of a transcript message. Tool results are user-authored
// blocks, so there is no separate tool role here — adapters map them.
type Role string

const (
	RoleUser      Role = "user"
	RoleAssistant Role = "assistant"
)

// Block types carried by Message.Blocks.
const (
	BlockText       = "text"
	BlockImage      = "image"
	BlockToolUse    = "tool_use"
	BlockToolResult = "tool_result"
	BlockThinking   = "thinking"
)

// Block is one piece of a message. Only the fields named by Type are meaningful.
type Block struct {
	Type string

	// Text carries "text" and "thinking" bodies.
	Text string

	// MediaType / Data carry an "image" block (also allowed inside a
	// tool_result's Content). Data is raw bytes; adapters base64 as needed.
	MediaType string
	Data      []byte

	// ID / Name / Input carry a "tool_use" block. Name is always the Tool ABI
	// id — never the advertised/sanitized provider name, which lives only
	// inside the adapter that needs it.
	ID    string
	Name  string
	Input json.RawMessage

	// ToolUseID / Content / IsError carry a "tool_result" block. Content holds
	// text and image blocks.
	ToolUseID string
	Content   []Block
	IsError   bool
}

// TextBlock is a text block with body s.
func TextBlock(s string) Block { return Block{Type: BlockText, Text: s} }

// ThinkingBlock is a reasoning block. Adapters that cannot carry reasoning drop it.
func ThinkingBlock(s string) Block { return Block{Type: BlockThinking, Text: s} }

// ImageBlock is an image block with raw bytes.
func ImageBlock(mediaType string, data []byte) Block {
	return Block{Type: BlockImage, MediaType: mediaType, Data: data}
}

// ToolUseBlock records one model-requested tool call by ABI id.
func ToolUseBlock(call ToolCall) Block {
	return Block{Type: BlockToolUse, ID: call.ID, Name: call.Name, Input: append(json.RawMessage(nil), call.Input...)}
}

// Message is one append-only transcript entry.
type Message struct {
	Role   Role
	Blocks []Block
}

// UserText is a user message carrying a single text block.
func UserText(text string) Message {
	return Message{Role: RoleUser, Blocks: []Block{TextBlock(text)}}
}

// Text concatenates the message's text blocks (thinking and tool blocks are
// not prose and are excluded).
func (m Message) Text() string {
	var b strings.Builder
	for _, block := range m.Blocks {
		if block.Type == BlockText {
			b.WriteString(block.Text)
		}
	}
	return b.String()
}

// ToolUses returns the tool_use blocks of an assistant message.
func (m Message) ToolUses() []Block {
	var out []Block
	for _, block := range m.Blocks {
		if block.Type == BlockToolUse {
			out = append(out, block)
		}
	}
	return out
}

// HasToolResults reports whether the message carries tool_result blocks, which
// must stay paired with the assistant message that requested them.
func (m Message) HasToolResults() bool {
	for _, block := range m.Blocks {
		if block.Type == BlockToolResult {
			return true
		}
	}
	return false
}

// Turn is one model request: the system prompt plus the whole transcript so
// far. The engine appends to Messages and never rewrites earlier entries.
type Turn struct {
	System string
	// Messages is the full transcript for this request: prior conversation,
	// the current user message, then one assistant message per model round and
	// one user message per tool batch.
	Messages []Message
	// Iteration is the 1-based model round inside this turn.
	Iteration int
}

// FirstUserText is the goal: the text of the first user message that carries prose.
func (t Turn) FirstUserText() string {
	for _, m := range t.Messages {
		if m.Role != RoleUser || m.HasToolResults() {
			continue
		}
		if text := strings.TrimSpace(m.Text()); text != "" {
			return text
		}
	}
	return ""
}

// LastAssistantText is the most recent assistant prose in the transcript.
func (t Turn) LastAssistantText() string {
	for i := len(t.Messages) - 1; i >= 0; i-- {
		if t.Messages[i].Role != RoleAssistant {
			continue
		}
		if text := strings.TrimSpace(t.Messages[i].Text()); text != "" {
			return text
		}
	}
	return ""
}

// imageTokenCost is the flat per-image budget charge (a 1024×1024 PNG costs
// roughly this many tokens on every frontier vision model).
const imageTokenCost = 1_500

// estimateTokens approximates the prompt cost of a transcript: four characters
// per token plus a flat charge per image.
func estimateTokens(msgs []Message) int {
	total := 0
	for _, m := range msgs {
		total += 4 // role framing
		total += blocksTokens(m.Blocks)
	}
	return total
}

func blocksTokens(blocks []Block) int {
	total := 0
	for _, b := range blocks {
		switch b.Type {
		case BlockImage:
			total += imageTokenCost
		case BlockToolUse:
			total += (len(b.Name) + len(b.Input) + 3) / 4
		case BlockToolResult:
			total += blocksTokens(b.Content)
		default:
			total += (len(b.Text) + 3) / 4
		}
	}
	return total
}

// cloneMessages copies the transcript so a model adapter can never mutate the
// engine's append-only history.
func cloneMessages(msgs []Message) []Message {
	if len(msgs) == 0 {
		return nil
	}
	out := make([]Message, len(msgs))
	for i, m := range msgs {
		out[i] = Message{Role: m.Role, Blocks: append([]Block(nil), m.Blocks...)}
	}
	return out
}

// resultBlocks renders one executed tool result as a tool_result block paired
// with the call that produced it. Errors are kept, never dropped.
func resultBlocks(call ToolCall, res ToolResult) Block {
	block := Block{Type: BlockToolResult, ToolUseID: call.ID, IsError: res.IsError || res.Err != ""}
	body := string(res.Output)
	if res.Err != "" {
		body = res.Err
	}
	if strings.TrimSpace(body) != "" {
		block.Content = append(block.Content, TextBlock(body))
	}
	block.Content = append(block.Content, res.Blocks...)
	if len(block.Content) == 0 {
		block.Content = append(block.Content, TextBlock("(no output)"))
	}
	return block
}
