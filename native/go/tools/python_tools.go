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
		ID:          "workspace.write",
		Version:     1,
		Description: "Write a UTF-8 text file under the workspace (Python worker)",
		Runtime:     RuntimePython,
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
	}, exec); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:          "workspace.search",
		Version:     1,
		Description: "Search workspace text via ripgrep or Python sniff (Python worker)",
		Runtime:     RuntimePython,
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

	if err := registry.Register(Descriptor{
		ID:          "web.fetch",
		Version:     1,
		Description: "Fetch a public HTTP(S) URL as readable text via the Python agent web_fetch backend (SSRF-guarded)",
		Runtime:     RuntimePython,
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
	}, exec); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:          "memory.search",
		Version:     1,
		Description: "Search Partner Memory + FTS entries (Python worker; context, not a grant)",
		Runtime:     RuntimePython,
		Risk:        RiskReadOnly,
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["query"],
			"properties":{
				"query":{"type":"string","minLength":1},
				"limit":{"type":"integer","minimum":1,"maximum":20},
				"home_dir":{"type":"string"},
				"project_path":{"type":"string"}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["query","hits","total","notice"],
			"properties":{
				"query":{"type":"string"},
				"hits":{
					"type":"array",
					"items":{
						"type":"object",
						"required":["kind","title","content","score"],
						"properties":{
							"kind":{"type":"string"},
							"title":{"type":"string"},
							"content":{"type":"string"},
							"score":{"type":"number"},
							"authority":{"type":"string"},
							"inferred":{"type":"boolean"},
							"why":{"type":"string"}
						},
						"additionalProperties":false
					}
				},
				"total":{"type":"integer","minimum":0},
				"notice":{"type":"string","minLength":1}
			},
			"additionalProperties":false
		}`),
	}, exec); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:          "prompt.assemble",
		Version:     1,
		Description: "Assemble system/soul/skills/memory context for a cognition turn (internal; not model-callable)",
		Runtime:     RuntimePython,
		Risk:        RiskReadOnly,
		InputSchema: json.RawMessage(`{
			"type":"object",
			"properties":{
				"message":{"type":"string"},
				"prompt":{"type":"string"},
				"session_id":{"type":"string"},
				"plan_mode":{"type":"boolean"},
				"chat_mode":{"type":"boolean"},
				"home_dir":{"type":"string"},
				"project_path":{"type":"string"},
				"provider":{"type":"string"},
				"model":{"type":"string"},
				"base_url":{"type":"string"}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["system","goal"],
			"properties":{
				"system":{"type":"string","minLength":1},
				"goal":{"type":"string"},
				"context_chars":{"type":"integer","minimum":0},
				"system_chars":{"type":"integer","minimum":0}
			},
			"additionalProperties":false
		}`),
	}, exec); err != nil {
		return err
	}

	return registerVoiceVisionWorkerTools(registry, exec)
}

func registerVoiceVisionWorkerTools(registry *Registry, exec Executor) error {
	type row struct {
		id, desc string
		risk     Risk
		in, out  string
	}
	rows := []row{
		{
			id: "voice.speak", desc: "Synthesize speech via Python voice lane (internal)",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"required":["text"],
			"properties":{
				"home_dir":{"type":"string"},
				"text":{"type":"string"},
				"gender":{"type":"string"},
				"voice":{"type":"string"},
				"speed":{"type":"number"}
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["wav_b64","sample_rate","unavailable"],
			"properties":{
				"wav_b64":{"type":"string"},
				"sample_rate":{"type":"integer"},
				"unavailable":{"type":"boolean"}
			},
			"additionalProperties":false
		}`,
		},
		{
			id: "voice.transcribe", desc: "Transcribe audio via Python STT lane (internal)",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"required":["audio_b64"],
			"properties":{
				"home_dir":{"type":"string"},
				"audio_b64":{"type":"string"},
				"suffix":{"type":"string"},
				"language":{"type":"string"}
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["text","unavailable"],
			"properties":{
				"text":{"type":"string"},
				"language":{"type":"string"},
				"unavailable":{"type":"boolean"}
			},
			"additionalProperties":false
		}`,
		},
		{
			id: "voice.install", desc: "Start voice pack/engine install (internal)",
			risk: RiskMutation,
			in: `{
			"type":"object",
			"properties":{
				"home_dir":{"type":"string"},
				"component":{"type":"string"}
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["ok","started"],
			"properties":{
				"ok":{"type":"boolean"},
				"started":{"type":"boolean"},
				"error":{"type":"string"}
			},
			"additionalProperties":false
		}`,
		},
		{
			id: "vision.activate", desc: "Activate local vision bundle (internal)",
			risk: RiskMutation,
			in: `{
			"type":"object",
			"properties":{
				"home_dir":{"type":"string"},
				"enabled":{"type":"boolean"}
			},
			"additionalProperties":false
		}`,
			out: `{"type":"object","additionalProperties":true}`,
		},
		{
			id: "vision.install", desc: "Install or activate vision model (internal)",
			risk: RiskMutation,
			in: `{
			"type":"object",
			"properties":{
				"home_dir":{"type":"string"},
				"model_id":{"type":"string"},
				"runtime_id":{"type":"string"},
				"prefer_cuda":{"type":"boolean"}
			},
			"additionalProperties":false
		}`,
			out: `{"type":"object","additionalProperties":true}`,
		},
		{
			id: "vision.cancel_install", desc: "Cancel in-flight vision install (internal)",
			risk: RiskMutation,
			in: `{
			"type":"object",
			"properties":{"home_dir":{"type":"string"}},
			"additionalProperties":false
		}`,
			out: `{"type":"object","additionalProperties":true}`,
		},
		{
			id: "vision.reinstall_runtime", desc: "Reinstall vision llama-server runtime (internal)",
			risk: RiskMutation,
			in: `{
			"type":"object",
			"properties":{
				"home_dir":{"type":"string"},
				"prefer_cuda":{"type":"boolean"}
			},
			"additionalProperties":false
		}`,
			out: `{"type":"object","additionalProperties":true}`,
		},
		{
			id: "vision.uninstall", desc: "Uninstall vision runtime/models (internal)",
			risk: RiskMutation,
			in: `{
			"type":"object",
			"properties":{
				"home_dir":{"type":"string"},
				"keep_models":{"type":"boolean"}
			},
			"additionalProperties":false
		}`,
			out: `{"type":"object","additionalProperties":true}`,
		},
		{
			id: "vision.start", desc: "Start local vision server (internal)",
			risk: RiskMutation,
			in: `{
			"type":"object",
			"properties":{"home_dir":{"type":"string"}},
			"additionalProperties":false
		}`,
			out: `{"type":"object","additionalProperties":true}`,
		},
		{
			id: "vision.stop", desc: "Stop local vision server (internal)",
			risk: RiskMutation,
			in: `{
			"type":"object",
			"properties":{"home_dir":{"type":"string"}},
			"additionalProperties":false
		}`,
			out: `{"type":"object","additionalProperties":true}`,
		},
		{
			id: "vision.progress", desc: "Vision install progress snapshot (internal)",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"properties":{"home_dir":{"type":"string"}},
			"additionalProperties":false
		}`,
			out: `{"type":"object","additionalProperties":true}`,
		},
	}
	for _, r := range rows {
		if err := registry.Register(Descriptor{
			ID:           r.id,
			Version:      1,
			Description:  r.desc,
			Runtime:      RuntimePython,
			Risk:         r.risk,
			InputSchema:  json.RawMessage(r.in),
			OutputSchema: json.RawMessage(r.out),
		}, exec); err != nil {
			return err
		}
	}
	return nil
}
