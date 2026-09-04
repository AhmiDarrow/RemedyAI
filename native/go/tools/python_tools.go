package tools

import (
	"encoding/json"
	"errors"
	"fmt"
)

// RegisterPythonWorkerTools installs Tool ABI descriptors that execute over
// RMDY KindToolRequest frames on the supervised Python worker. caller must be
// live; there is no in-process Python fallback.
func RegisterPythonWorkerTools(registry *Registry, caller FrameCaller) error {
	if registry == nil {
		return fmt.Errorf("%w: nil registry", ErrInvalidDescriptor)
	}
	if caller == nil {
		return errors.New("python worker frame caller is required")
	}
	exec := NewRMDYExecutor(caller)

	if err := registry.Register(Descriptor{
		ID:          "text.slugify",
		Version:     1,
		Description: "Slugify text in the Python worker",
		Runtime:     RuntimePython,
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
	}, exec); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:          "text.word_count",
		Version:     1,
		Description: "Count whitespace-separated words in the Python worker",
		Runtime:     RuntimePython,
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
	}, exec); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:          "workspace.read",
		Version:     1,
		Description: "Read a UTF-8 text file under the workspace (Python worker)",
		Runtime:     RuntimePython,
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
	}, exec); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:          "workspace.list",
		Version:     1,
		Description: "List files and directories under a workspace path (Python worker)",
		Runtime:     RuntimePython,
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
	}, exec); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:          "web.search",
		Version:     1,
		Description: "Search the public web via the Python agent web_search backend (OpenSERP/DDG)",
		Runtime:     RuntimePython,
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
	}, exec); err != nil {
		return err
	}

	return nil
}
