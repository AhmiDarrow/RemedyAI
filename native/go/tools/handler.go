package tools

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"unicode"

	"github.com/AhmiDarrow/RemedyAI/native/go/protocol"
)

// WorkerHandler serves KindToolRequest frames against an in-process registry.
// Production Python workers speak the same wire payloads; tests use this peer.
type WorkerHandler struct {
	Registry *Registry
}

func (h WorkerHandler) Handle(ctx context.Context, frame protocol.Frame) ([]protocol.Frame, error) {
	if h.Registry == nil {
		return nil, errors.New("worker handler requires a registry")
	}
	switch frame.Kind {
	case protocol.KindHealth:
		payload, err := json.Marshal(map[string]any{
			"protocol":     1,
			"ready":        true,
			"capabilities": []string{"tools"},
		})
		if err != nil {
			return nil, err
		}
		return []protocol.Frame{{Kind: protocol.KindHealth, Payload: payload}}, nil
	case protocol.KindToolRequest:
		request, err := UnmarshalWireRequest(frame.Payload)
		if err != nil {
			return nil, err
		}
		result, execErr := h.Registry.Execute(ctx, request)
		payload, err := MarshalWireResult(result, execErr)
		if err != nil {
			return nil, err
		}
		return []protocol.Frame{{Kind: protocol.KindToolResult, Payload: payload}}, nil
	default:
		return nil, errors.New("unsupported RMDY frame kind for tool worker")
	}
}

// RegisterPythonWorkerLocalMirrors installs the Python worker tool surface as
// Go executors for protocol tests. Production turns still route RuntimePython
// descriptors through RMDY to the real worker process.
func RegisterPythonWorkerLocalMirrors(registry *Registry) error {
	if registry == nil {
		return ErrInvalidDescriptor
	}
	if err := registry.Register(Descriptor{
		ID:          "text.slugify",
		Version:     1,
		Description: "Slugify text (local mirror of Python worker)",
		Runtime:     RuntimeGo,
		Risk:        RiskReadOnly,
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["text"],
			"properties":{"text":{"type":"string"}},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["slug"],
			"properties":{"slug":{"type":"string"}},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(func(_ context.Context, request Request) (Result, error) {
		var body struct {
			Text string `json:"text"`
		}
		if err := json.Unmarshal(request.Input, &body); err != nil {
			return Result{}, ErrInvalidInput
		}
		out, err := json.Marshal(map[string]string{"slug": slugify(body.Text)})
		return Result{Output: out}, err
	})); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:          "text.word_count",
		Version:     1,
		Description: "Count words (local mirror of Python worker)",
		Runtime:     RuntimeGo,
		Risk:        RiskReadOnly,
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["text"],
			"properties":{"text":{"type":"string"}},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["words"],
			"properties":{"words":{"type":"integer","minimum":0}},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(func(_ context.Context, request Request) (Result, error) {
		var body struct {
			Text string `json:"text"`
		}
		if err := json.Unmarshal(request.Input, &body); err != nil {
			return Result{}, ErrInvalidInput
		}
		out, err := json.Marshal(map[string]int{"words": wordCount(body.Text)})
		return Result{Output: out}, err
	})); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:          "workspace.read",
		Version:     1,
		Description: "Read a UTF-8 text file (local mirror of Python worker)",
		Runtime:     RuntimeGo,
		Risk:        RiskReadOnly,
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["path"],
			"properties":{
				"path":{"type":"string","minLength":1},
				"offset":{"type":"integer","minimum":0},
				"limit":{"type":"integer","minimum":1}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["path","content"],
			"properties":{
				"path":{"type":"string"},
				"content":{"type":"string"},
				"truncated":{"type":"boolean"}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(func(_ context.Context, request Request) (Result, error) {
		var body struct {
			Path string `json:"path"`
		}
		if err := json.Unmarshal(request.Input, &body); err != nil || strings.TrimSpace(body.Path) == "" {
			return Result{}, ErrInvalidInput
		}
		out, err := json.Marshal(map[string]any{
			"path":    body.Path,
			"content": "mirror:" + body.Path,
		})
		return Result{Output: out}, err
	})); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:          "workspace.list",
		Version:     1,
		Description: "List workspace entries (local mirror of Python worker)",
		Runtime:     RuntimeGo,
		Risk:        RiskReadOnly,
		InputSchema: json.RawMessage(`{
			"type":"object",
			"properties":{
				"path":{"type":"string"},
				"limit":{"type":"integer","minimum":1,"maximum":2000},
				"offset":{"type":"integer","minimum":0}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["path","entries","total"],
			"properties":{
				"path":{"type":"string"},
				"entries":{
					"type":"array",
					"items":{
						"type":"object",
						"required":["name","kind"],
						"properties":{
							"name":{"type":"string"},
							"kind":{"type":"string","enum":["file","dir"]}
						},
						"additionalProperties":false
					}
				},
				"total":{"type":"integer","minimum":0},
				"truncated":{"type":"boolean"}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(func(_ context.Context, request Request) (Result, error) {
		var body struct {
			Path string `json:"path"`
		}
		_ = json.Unmarshal(request.Input, &body)
		path := body.Path
		if path == "" {
			path = "."
		}
		out, err := json.Marshal(map[string]any{
			"path": path,
			"entries": []map[string]string{
				{"name": "mirror.txt", "kind": "file"},
			},
			"total": 1,
		})
		return Result{Output: out}, err
	})); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:          "workspace.write",
		Version:     1,
		Description: "Write a UTF-8 text file (local mirror of Python worker)",
		Runtime:     RuntimeGo,
		Risk:        RiskMutation,
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["path","content"],
			"properties":{
				"path":{"type":"string","minLength":1},
				"content":{"type":"string"}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["path","bytes_written"],
			"properties":{
				"path":{"type":"string"},
				"bytes_written":{"type":"integer","minimum":0},
				"created":{"type":"boolean"}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(func(_ context.Context, request Request) (Result, error) {
		var body struct {
			Path    string `json:"path"`
			Content string `json:"content"`
		}
		if err := json.Unmarshal(request.Input, &body); err != nil || strings.TrimSpace(body.Path) == "" {
			return Result{}, ErrInvalidInput
		}
		out, err := json.Marshal(map[string]any{
			"path":          body.Path,
			"bytes_written": len([]byte(body.Content)),
			"created":       true,
		})
		return Result{Output: out}, err
	})); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:          "workspace.search",
		Version:     1,
		Description: "Search workspace text (local mirror of Python worker)",
		Runtime:     RuntimeGo,
		Risk:        RiskReadOnly,
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["pattern"],
			"properties":{
				"pattern":{"type":"string","minLength":1},
				"path":{"type":"string"},
				"glob":{"type":"string"},
				"max_matches":{"type":"integer","minimum":1,"maximum":500},
				"case_insensitive":{"type":"boolean"}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["pattern","engine","matches","total"],
			"properties":{
				"pattern":{"type":"string"},
				"engine":{"type":"string"},
				"matches":{
					"type":"array",
					"items":{
						"type":"object",
						"required":["path","line","text"],
						"properties":{
							"path":{"type":"string"},
							"line":{"type":"integer","minimum":1},
							"text":{"type":"string"}
						},
						"additionalProperties":false
					}
				},
				"total":{"type":"integer","minimum":0}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(func(_ context.Context, request Request) (Result, error) {
		var body struct {
			Pattern string `json:"pattern"`
		}
		if err := json.Unmarshal(request.Input, &body); err != nil || strings.TrimSpace(body.Pattern) == "" {
			return Result{}, ErrInvalidInput
		}
		out, err := json.Marshal(map[string]any{
			"pattern": body.Pattern,
			"engine":  "mirror",
			"matches": []map[string]any{
				{"path": "mirror.txt", "line": 1, "text": "mirror:" + body.Pattern},
			},
			"total": 1,
		})
		return Result{Output: out}, err
	})); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:          "web.search",
		Version:     1,
		Description: "Search the public web (local mirror of Python worker)",
		Runtime:     RuntimeGo,
		Risk:        RiskReadOnly,
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["query"],
			"properties":{
				"query":{"type":"string","minLength":1},
				"max_results":{"type":"integer","minimum":1,"maximum":10}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["query","backend","results"],
			"properties":{
				"query":{"type":"string"},
				"backend":{"type":"string"},
				"results":{
					"type":"array",
					"items":{
						"type":"object",
						"required":["title","url"],
						"properties":{
							"title":{"type":"string"},
							"url":{"type":"string"},
							"snippet":{"type":"string"}
						},
						"additionalProperties":false
					}
				}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(func(_ context.Context, request Request) (Result, error) {
		var body struct {
			Query      string `json:"query"`
			MaxResults int    `json:"max_results"`
		}
		if err := json.Unmarshal(request.Input, &body); err != nil || strings.TrimSpace(body.Query) == "" {
			return Result{}, ErrInvalidInput
		}
		out, err := json.Marshal(map[string]any{
			"query":   body.Query,
			"backend": "mirror",
			"results": []map[string]string{
				{
					"title":   "mirror:" + body.Query,
					"url":     "https://example.invalid/mirror",
					"snippet": "local mirror result",
				},
			},
		})
		return Result{Output: out}, err
	})); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:          "web.fetch",
		Version:     1,
		Description: "Fetch a public HTTP(S) URL as readable text (local mirror of Python worker)",
		Runtime:     RuntimeGo,
		Risk:        RiskReadOnly,
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["url"],
			"properties":{
				"url":{"type":"string","minLength":1},
				"max_chars":{"type":"integer","minimum":1000,"maximum":200000}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["url","final_url","content","format"],
			"properties":{
				"url":{"type":"string"},
				"final_url":{"type":"string"},
				"content":{"type":"string"},
				"format":{"type":"string","enum":["markdown","text"]},
				"title":{"type":"string"},
				"truncated":{"type":"boolean"}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(func(_ context.Context, request Request) (Result, error) {
		var body struct {
			URL string `json:"url"`
		}
		if err := json.Unmarshal(request.Input, &body); err != nil || strings.TrimSpace(body.URL) == "" {
			return Result{}, ErrInvalidInput
		}
		url := strings.TrimSpace(body.URL)
		if !strings.HasPrefix(url, "http://") && !strings.HasPrefix(url, "https://") {
			return Result{}, ErrInvalidInput
		}
		out, err := json.Marshal(map[string]any{
			"url":       url,
			"final_url": url,
			"content":   "mirror fetch of " + url,
			"format":    "text",
			"title":     "mirror",
		})
		return Result{Output: out}, err
	})); err != nil {
		return err
	}
	return nil
}

func slugify(text string) string {
	var b strings.Builder
	lastHyphen := true
	for _, r := range strings.ToLower(text) {
		if unicode.IsLetter(r) || unicode.IsDigit(r) {
			b.WriteRune(r)
			lastHyphen = false
			continue
		}
		if !lastHyphen {
			b.WriteByte('-')
			lastHyphen = true
		}
	}
	return strings.Trim(b.String(), "-")
}

func wordCount(text string) int {
	fields := strings.Fields(text)
	return len(fields)
}
