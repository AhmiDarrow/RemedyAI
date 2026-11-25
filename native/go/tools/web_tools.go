package tools

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
)

// web.search and web.fetch, Go-native ports of rmdy_tool_worker.py
// _web_search / _web_fetch (src/remedy/core/web_helpers.py, html_extract.py).
// The model-facing contract is unchanged from the Python worker descriptors.

// WebOptions carries what the tools package cannot import from httpapi.
type WebOptions struct {
	// Version goes into the User-Agent: RemedyAI-WebFetch/<Version>.
	Version string
	// Config returns the current config.toml values; read on every call so
	// web_tools_enabled and friends apply without a restart. Nil means empty.
	Config func() map[string]any
}

const (
	webSearchTimeout = 20 * time.Second
	webFetchTimeout  = 25 * time.Second
)

// RegisterWebTools installs web.search and web.fetch as RuntimeGo tools.
func RegisterWebTools(registry *Registry, opts WebOptions) error {
	return registerWebTools(registry, &webClient{
		version:      opts.Version,
		config:       opts.Config,
		ddgEndpoint:  webDDGEndpoint,
		openserpBase: webOpenSERPBase,
		minDelay:     webDefaultDelay,
	})
}

func registerWebTools(registry *Registry, c *webClient) error {
	if registry == nil {
		return fmt.Errorf("%w: nil registry", ErrInvalidDescriptor)
	}
	if err := registry.Register(Descriptor{
		ID:          "web.search",
		Version:     1,
		Description: "Search the public web via the Python agent web_search backend (OpenSERP/DDG)",
		Runtime:     RuntimeGo,
		Risk:        RiskReadOnly,
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["query"],
			"properties":{
				"query":{"type":"string","minLength":1,"description":"Search query (max 400 chars)"},
				"max_results":{"type":"integer","minimum":1,"maximum":10,"description":"Maximum results to return (default 5)"}
			},
			"additionalProperties":false
		}`),
		// blocked/note: the shape the Python handler returned on a human
		// check, which its old schema rejected.
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
				},
				"blocked":{"type":"boolean"},
				"note":{"type":"string"}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(c.executeSearch)); err != nil {
		return err
	}
	return registry.Register(Descriptor{
		ID:          "web.fetch",
		Version:     1,
		Description: "Fetch a public HTTP(S) URL as readable text via the Python agent web_fetch backend (SSRF-guarded)",
		Runtime:     RuntimeGo,
		Risk:        RiskReadOnly,
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["url"],
			"properties":{
				"url":{"type":"string","minLength":1,"description":"http:// or https:// URL to fetch"},
				"max_chars":{"type":"integer","minimum":1000,"maximum":200000,"description":"Cap on returned content characters (default 50000)"}
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
	}, ExecutorFunc(c.executeFetch))
}

func (c *webClient) cfg() map[string]any {
	if c.config == nil {
		return nil
	}
	return c.config()
}

// cfgBoolKey mirrors httpapi cfgBool: a missing key is def.
func cfgBoolKey(cfg map[string]any, key string, def bool) bool {
	v, ok := cfg[key]
	if !ok || v == nil {
		return def
	}
	switch t := v.(type) {
	case bool:
		return t
	case string:
		switch strings.ToLower(strings.TrimSpace(t)) {
		case "1", "true", "yes", "on", "enable", "enabled":
			return true
		case "0", "false", "no", "off", "disable", "disabled":
			return false
		}
		return def
	case int:
		return t != 0
	case int64:
		return t != 0
	case float64:
		return t != 0
	}
	return def
}

func cfgStringKey(cfg map[string]any, key string) string {
	v, ok := cfg[key]
	if !ok || v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return s
	}
	return fmt.Sprint(v)
}

var errWebDisabled = errors.New("web tools are disabled")

func (c *webClient) enabled() bool { return cfgBoolKey(c.cfg(), "web_tools_enabled", true) }

func (c *webClient) executeSearch(ctx context.Context, request Request) (Result, error) {
	if !c.enabled() {
		return Result{}, errWebDisabled
	}
	var body struct {
		Query      string `json:"query"`
		MaxResults *int   `json:"max_results"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, fmt.Errorf("%w: %v", ErrInvalidInput, err)
	}
	query := truncRunes(strings.TrimSpace(body.Query), 400)
	if query == "" {
		return Result{}, errors.New("query is required")
	}
	maxResults := 5
	if body.MaxResults != nil {
		maxResults = min(10, max(1, *body.MaxResults))
	}
	rows, backend, err := c.runSearch(ctx, query, maxResults, webSearchTimeout)
	var out map[string]any
	switch {
	case errors.Is(err, errSearchBlocked):
		// Say why there are no results so the model stops searching.
		out = map[string]any{
			"query": query, "backend": "DuckDuckGo HTML", "results": []searchRow{},
			"blocked": true, "note": webSearchBlockedNote,
		}
	case err != nil:
		return Result{}, err
	default:
		results := make([]searchRow, 0, len(rows))
		for _, r := range rows {
			r.Snippet = strings.TrimSpace(r.Snippet)
			results = append(results, r)
		}
		out = map[string]any{"query": query, "backend": backend, "results": results}
	}
	raw, err := json.Marshal(out)
	return Result{Output: raw}, err
}

// clampMaxChars: default 50,000, clamped to 1,000..200,000.
func clampMaxChars(v *int) int {
	if v == nil {
		return 50_000
	}
	return min(200_000, max(1000, *v))
}

func (c *webClient) executeFetch(ctx context.Context, request Request) (Result, error) {
	if !c.enabled() {
		return Result{}, errWebDisabled
	}
	var body struct {
		URL      string `json:"url"`
		MaxChars *int   `json:"max_chars"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, fmt.Errorf("%w: %v", ErrInvalidInput, err)
	}
	target := strings.TrimSpace(body.URL)
	if target == "" {
		return Result{}, errors.New("url is required")
	}
	if !strings.HasPrefix(target, "http://") && !strings.HasPrefix(target, "https://") {
		return Result{}, errors.New("url must start with http:// or https://")
	}
	limit := clampMaxChars(body.MaxChars)
	finalURL, raw, charset, err := c.politeFetch(ctx, target, max(limit*4, 200_000), webFetchTimeout, c.robotsRespected())
	if err != nil {
		return Result{}, err
	}
	raw2, err := json.Marshal(shapeFetch(target, finalURL, raw, charset, limit))
	return Result{Output: raw2}, err
}

// shapeFetch is the result shaping of _web_fetch.
func shapeFetch(target, finalURL string, raw []byte, charset string, limit int) map[string]any {
	text := decodeCharset(raw, charset)
	if finalURL == "" {
		finalURL = target
	}
	out := map[string]any{"url": target, "final_url": finalURL, "content": text, "format": "text"}
	truncated := false
	cut := func() {
		if r := []rune(text); len(r) > limit {
			out["content"] = string(r[:limit])
			truncated = true
		}
	}
	if looksLikeHTML(raw) {
		ex := htmlToMarkdown(text, limit)
		bodyMD := strings.TrimSpace(ex.markdown)
		title := strings.TrimSpace(ex.title)
		if bodyMD != "" {
			out["content"] = bodyMD
			out["format"] = "markdown"
			truncated = strings.Contains(ex.markdown, strings.TrimPrefix(webTruncMarker(limit), "\n\n"))
		} else {
			cut()
		}
		if title != "" {
			out["title"] = title
		}
	} else {
		cut()
	}
	if truncated {
		out["truncated"] = true
	}
	return out
}
