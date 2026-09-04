package providers

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

// OpenAICompat streams OpenAI-compatible chat completions SSE into cognition.ModelEvent.
type OpenAICompat struct {
	BaseURL    string
	APIKey     string
	Model      string
	HTTPClient *http.Client
}

func (c *OpenAICompat) client() *http.Client {
	if c.HTTPClient != nil {
		return c.HTTPClient
	}
	return &http.Client{Timeout: 120 * time.Second}
}

func (c *OpenAICompat) Stream(ctx context.Context, turn cognition.Turn) (<-chan cognition.ModelEvent, error) {
	base := strings.TrimRight(strings.TrimSpace(c.BaseURL), "/")
	model := strings.TrimSpace(c.Model)
	if base == "" || model == "" {
		return nil, fmt.Errorf("openai-compat model requires base URL and model id")
	}
	messages := buildMessages(turn)
	body, err := json.Marshal(map[string]any{
		"model":    model,
		"messages": messages,
		"stream":   true,
	})
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
		parseSSE(ctx, resp.Body, out)
	}()
	return out, nil
}

func buildMessages(turn cognition.Turn) []map[string]string {
	msgs := make([]map[string]string, 0, 2+len(turn.Results))
	goal := strings.TrimSpace(turn.Goal)
	if goal == "" {
		goal = "continue"
	}
	msgs = append(msgs, map[string]string{"role": "user", "content": goal})
	if text := strings.TrimSpace(turn.Text); text != "" {
		msgs = append(msgs, map[string]string{"role": "assistant", "content": text})
	}
	for _, res := range turn.Results {
		content := string(res.Output)
		if res.Err != "" {
			content = res.Err
		}
		if content == "" {
			continue
		}
		msgs = append(msgs, map[string]string{
			"role":    "tool",
			"content": content,
		})
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

func parseSSE(ctx context.Context, body io.Reader, out chan<- cognition.ModelEvent) {
	scanner := bufio.NewScanner(body)
	// Provider frames can be large when tool args stream in.
	buf := make([]byte, 0, 64*1024)
	scanner.Buffer(buf, 1024*1024)

	tools := map[int]*toolAcc{}
	sawDone := false

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
						Name:  acc.name,
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
			case "stop", "length":
				_ = emit(cognition.ModelEvent{Done: true})
				return
			}
		}
	}
	if sawDone {
		_ = emit(cognition.ModelEvent{Done: true})
	}
}
