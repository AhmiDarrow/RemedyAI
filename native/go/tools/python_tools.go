package tools

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
)

// modelHiddenToolIDs are tools that stay registered but must never be
// advertised to the model. Two groups live here:
//
//   - demo / diagnostic tools kept for tests and attach probes;
//   - the internal ABI the frontier surface replaced. workspace.* and
//     shell.exec keep their ids (approvals are fingerprinted on the tool
//     identity, and the CLI and existing tests still call them), but the model
//     sees read / edit / write / glob / grep / bash instead.
//
// The cognition runner consults IsModelHiddenTool when it builds the tool
// schema list.
var modelHiddenToolIDs = map[string]struct{}{
	"text.slugify":     {},
	"text.word_count":  {},
	"runtime.probe":    {},
	"json.canonical":   {},
	"text.sha256":      {},
	"workspace.read":   {},
	"workspace.list":   {},
	"workspace.write":  {},
	"workspace.edit":   {},
	"workspace.search": {},
	"shell.exec":       {},
	"partner.pulse":    {},
	"partner.improve":  {},
	"setup.session":    {},
}

// IsModelHiddenTool reports whether id is a demo/diagnostic tool that must be
// omitted from the model-visible tool surface.
func IsModelHiddenTool(id string) bool {
	_, hidden := modelHiddenToolIDs[id]
	return hidden
}

// ModelHiddenToolIDs returns the sorted hidden-tool list (for diagnostics).
func ModelHiddenToolIDs() []string {
	out := make([]string, 0, len(modelHiddenToolIDs))
	for id := range modelHiddenToolIDs {
		out = append(out, id)
	}
	sort.Strings(out)
	return out
}

// modelInternalInputKeys are accepted by the registry (Go injects them) but
// stripped from the schema advertised to the model. Advertising a field whose
// value the runtime overwrites invites the model to set it, observe no effect,
// and spend a round working out why — so every runtime-bound field belongs
// here, not only the ones that would be a security problem.
var modelInternalInputKeys = []string{
	"home_dir",
	"session_id",
	"workspace_root",
	"project_path",
	"access_scope",
	"page_context",
	"caller_origin",
	GoBoundField,
}

// ModelInputSchema returns schema with Go-internal properties (home_dir,
// _go_bound) removed from "properties" and "required". The registry keeps the
// full schema so Go-injected values still validate; the model never sees or
// supplies them. Non-object schemas are returned unchanged.
func ModelInputSchema(schema json.RawMessage) json.RawMessage {
	if len(schema) == 0 {
		return schema
	}
	var doc map[string]any
	if err := json.Unmarshal(schema, &doc); err != nil {
		return schema
	}
	props, _ := doc["properties"].(map[string]any)
	changed := false
	for _, key := range modelInternalInputKeys {
		if props != nil {
			if _, ok := props[key]; ok {
				delete(props, key)
				changed = true
			}
		}
	}
	if req, ok := doc["required"].([]any); ok {
		kept := make([]any, 0, len(req))
		for _, r := range req {
			name, _ := r.(string)
			internal := false
			for _, key := range modelInternalInputKeys {
				if name == key {
					internal = true
					break
				}
			}
			if internal {
				changed = true
				continue
			}
			kept = append(kept, r)
		}
		if len(kept) == 0 {
			delete(doc, "required")
		} else {
			doc["required"] = kept
		}
	}
	if !changed {
		return schema
	}
	out, err := json.Marshal(doc)
	if err != nil {
		return schema
	}
	return out
}

// Shared schema fragments for Go-bound binding keys. The worker ignores
// workspace_root/project_path/home_dir unless _go_bound is present (see
// rmdy_tool_worker.py module doc); httpapi's injection helper sets all of
// them after overwriting the paths with the session root.
const (
	propGoBound       = `"_go_bound":{"type":"boolean","description":"Set by the Go runtime after it bound workspace_root/project_path/home_dir; never model-supplied"}`
	propWorkspaceRoot = `"workspace_root":{"type":"string","description":"Absolute workspace root (bound by the runtime to the session project; model-supplied values are ignored)"},"access_scope":{"type":"string","description":"Access scope bound by the runtime (project, home, or full). Model-supplied values are ignored."}`
	propProjectPath   = `"project_path":{"type":"string","description":"Absolute project path (bound by the runtime; model-supplied values are ignored)"}`
	propHomeDir       = `"home_dir":{"type":"string","description":"Remedy home directory (internal; injected by the Go runtime)"}`
)

type pyToolSpec struct {
	id, desc string
	risk     Risk
	in, out  string
}

func pythonWorkerToolSpecs() []pyToolSpec {
	return []pyToolSpec{
		{
			id: "text.slugify", desc: "Slugify text in the Python worker (diagnostic; hidden from the model)",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"required":["text"],
			"properties":{"text":{"type":"string","description":"Text to slugify"}},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["slug"],
			"properties":{"slug":{"type":"string"}},
			"additionalProperties":false
		}`,
		},
		{
			id: "text.word_count", desc: "Count whitespace-separated words in the Python worker (diagnostic; hidden from the model)",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"required":["text"],
			"properties":{"text":{"type":"string","description":"Text to count"}},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["words"],
			"properties":{"words":{"type":"integer","minimum":0}},
			"additionalProperties":false
		}`,
		},
		{
			id: "workspace.read", desc: "Read a UTF-8 text file under the workspace. offset is a 1-based line number; use next_offset from the result to page.",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"required":["path"],
			"properties":{
				"path":{"type":"string","minLength":1,"description":"File path relative to the workspace root (absolute paths must stay inside it)"},
				"offset":{"type":"integer","minimum":0,"description":"1-based line number to start reading from (0 or 1 = first line)"},
				"limit":{"type":"integer","minimum":1,"description":"Maximum number of lines to return"},
				"line_numbers":{"type":"boolean","description":"Prefix each returned line with its 1-based line number and a tab"},
				` + propWorkspaceRoot + `,
				` + propProjectPath + `,
				` + propGoBound + `
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["path","content"],
			"properties":{
				"path":{"type":"string"},
				"content":{"type":"string"},
				"total_lines":{"type":"integer","minimum":0},
				"line_start":{"type":"integer","minimum":1},
				"line_end":{"type":"integer","minimum":0},
				"next_offset":{"type":"integer","minimum":1},
				"truncated":{"type":"boolean"}
			},
			"additionalProperties":false
		}`,
		},
		{
			id: "workspace.list", desc: "List files and directories under a workspace path",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"properties":{
				"path":{"type":"string","description":"Directory relative to the workspace root (default: the root)"},
				"limit":{"type":"integer","minimum":1,"maximum":2000,"description":"Maximum entries to return (default 200)"},
				"offset":{"type":"integer","minimum":0,"description":"Number of entries to skip (0-based) for paging"},
				` + propWorkspaceRoot + `,
				` + propProjectPath + `,
				` + propGoBound + `
			},
			"additionalProperties":false
		}`,
			out: `{
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
		}`,
		},
		{
			id: "workspace.write", desc: "Create or overwrite a UTF-8 text file under the workspace (atomic write)",
			risk: RiskMutation,
			in: `{
			"type":"object",
			"required":["path","content"],
			"properties":{
				"path":{"type":"string","minLength":1,"description":"File path relative to the workspace root; parent directories are created"},
				"content":{"type":"string","description":"Full file content to write (replaces any existing content)"},
				` + propWorkspaceRoot + `,
				` + propProjectPath + `,
				` + propGoBound + `
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["path","bytes_written"],
			"properties":{
				"path":{"type":"string"},
				"bytes_written":{"type":"integer","minimum":0},
				"created":{"type":"boolean"}
			},
			"additionalProperties":false
		}`,
		},
		{
			id: "workspace.edit", desc: "Search/replace edit of a UTF-8 text file under the workspace. Preserves BOM and CRLF/LF line endings byte-for-byte; old_string must match exactly once unless replace_all.",
			risk: RiskMutation,
			in: `{
			"type":"object",
			"required":["path"],
			"properties":{
				"path":{"type":"string","minLength":1,"description":"File path relative to the workspace root"},
				"old_string":{"type":"string","description":"Exact text to find (whitespace/indent-tolerant fallback when unique)"},
				"new_string":{"type":"string","description":"Replacement text"},
				"replace_all":{"type":"boolean","description":"Replace every occurrence instead of requiring a unique match"},
				` + propWorkspaceRoot + `,
				` + propProjectPath + `,
				` + propGoBound + `,
				"edits":{
					"type":"array",
					"description":"Multiple hunks applied in order; the file is untouched if any hunk fails",
					"items":{
						"type":"object",
						"required":["old_string","new_string"],
						"properties":{
							"old_string":{"type":"string","description":"Exact text to find"},
							"new_string":{"type":"string","description":"Replacement text"},
							"replace_all":{"type":"boolean","description":"Replace every occurrence of this hunk"}
						},
						"additionalProperties":false
					}
				}
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["path","occurrences","hunks_applied","changed"],
			"properties":{
				"path":{"type":"string"},
				"occurrences":{"type":"integer","minimum":0},
				"hunks_applied":{"type":"integer","minimum":0},
				"message":{"type":"string"},
				"changed":{"type":"boolean"}
			},
			"additionalProperties":false
		}`,
		},
		{
			id: "workspace.search", desc: "Regex search over workspace text files (ripgrep or Python fallback). pattern is a regular expression, not a literal; invalid regex is an error. capped=true means more matches exist beyond max_matches.",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"required":["pattern"],
			"properties":{
				"pattern":{"type":"string","minLength":1,"description":"Regular expression (Python/Rust syntax); escape literal metacharacters"},
				"path":{"type":"string","description":"Directory or file relative to the workspace root to search (default: whole workspace)"},
				"glob":{"type":"string","description":"Filename glob filter, e.g. *.py or src/**/*.ts"},
				"max_matches":{"type":"integer","minimum":1,"maximum":500,"description":"Global cap on returned matches (default 50)"},
				"case_insensitive":{"type":"boolean","description":"Ignore case"},
				"context":{"type":"integer","minimum":0,"maximum":5,"description":"Lines of context before and after each match, joined into the match text"},
				` + propWorkspaceRoot + `,
				` + propProjectPath + `,
				` + propGoBound + `
			},
			"additionalProperties":false
		}`,
			out: `{
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
				"total":{"type":"integer","minimum":0},
				"capped":{"type":"boolean"},
				"truncated":{"type":"boolean"}
			},
			"additionalProperties":false
		}`,
		},
		{
			id: "prompt.assemble", desc: "Assemble system/soul/skills/memory context for a cognition turn (internal; not model-callable)",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"properties":{
				"message":{"type":"string"},
				"prompt":{"type":"string"},
				"session_id":{"type":"string"},
				"plan_mode":{"type":"boolean"},
				"chat_mode":{"type":"boolean"},
				` + propHomeDir + `,
				` + propProjectPath + `,
				` + propGoBound + `,
				"provider":{"type":"string"},
				"model":{"type":"string"},
				"base_url":{"type":"string"}
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["system","goal"],
			"properties":{
				"system":{"type":"string","minLength":1},
				"goal":{"type":"string"},
				"context_chars":{"type":"integer","minimum":0},
				"system_chars":{"type":"integer","minimum":0}
			},
			"additionalProperties":false
		}`,
		},
		{
			id: "prompt.slim_epoch", desc: "Memory Harness prune/offload/brief at soft epochs (internal; not model-callable)",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"properties":{
				"system":{"type":"string"},
				"goal":{"type":"string"},
				"text":{"type":"string"},
				"checkpoint":{"type":"string"},
				"session_id":{"type":"string"},
				"epoch":{"type":"integer"},
				"total_steps":{"type":"integer"},
				"ledger":{
					"type":"array",
					"description":"Outcome lines from the epoch, oldest first.",
					"items":{"type":"string"}
				},
				` + propHomeDir + `,
				` + propProjectPath + `,
				` + propGoBound + `,
				"provider":{"type":"string"},
				"model":{"type":"string"},
				"base_url":{"type":"string"}
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"properties":{
				"ok":{"type":"boolean"},
				"system":{"type":"string"},
				"text":{"type":"string"},
				"brief":{"type":"string"},
				"meta":{"type":"object"},
				"error":{"type":"string"}
			},
			"additionalProperties":true
		}`,
		},
		{
			id: "prompt.should_continue", desc: "Unfinished-work / agency re-arm gate (internal; not model-callable)",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"properties":{
				"goal":{"type":"string"},
				"message":{"type":"string"},
				"text":{"type":"string"},
				"session_id":{"type":"string"},
				"tool_count":{"type":"integer"},
				"chat_mode":{"type":"boolean"},
				"plan_mode":{"type":"boolean"},
				"verify_seen":{
					"type":"boolean",
					"description":"True when a verify command has succeeded since the last mutation."
				},
				"last_results":{
					"type":"array",
					"description":"The last tool batch, so the gate decides on evidence rather than prose.",
					"items":{
						"type":"object",
						"properties":{
							"name":{"type":"string"},
							"ok":{"type":"boolean"},
							"tail":{"type":"string"}
						},
						"additionalProperties":true
					}
				},
				` + propHomeDir + `,
				` + propProjectPath + `,
				` + propGoBound + `
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"properties":{
				"ok":{"type":"boolean"},
				"continue":{"type":"boolean"},
				"nudge":{"type":"string"},
				"reason":{"type":"string"},
				"error":{"type":"string"}
			},
			"additionalProperties":true
		}`,
		},
		{
			id: "prompt.turn_end", desc: "Post-turn learning: residue, soul, skill genome, LearningLoop (internal; not model-callable)",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"properties":{
				"message":{"type":"string"},
				"goal":{"type":"string"},
				"user_text":{"type":"string"},
				"assistant_text":{"type":"string"},
				"text":{"type":"string"},
				"session_id":{"type":"string"},
				"plan_mode":{"type":"boolean"},
				"chat_mode":{"type":"boolean"},
				"soul_native":{"type":"boolean","description":"The Go runtime already updated the soul field; Python skips it"},
				"tool_steps":{
					"type":"array",
					"items":{
						"type":"object",
						"properties":{
							"tool":{"type":"string"},
							"name":{"type":"string"},
							"ok":{"type":"boolean"},
							"success":{"type":"boolean"},
							"result":{"type":"string"},
							"error":{"type":"string"},
							"duration_ms":{"type":"number"}
						},
						"additionalProperties":true
					}
				},
				` + propHomeDir + `,
				` + propProjectPath + `,
				` + propGoBound + `
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"properties":{
				"ok":{"type":"boolean"},
				"residue":{"type":"string"},
				"genome":{"type":"integer"},
				"learned":{"type":"boolean"},
				"skill":{"type":"string"},
				"skipped":{"type":"string"}
			},
			"additionalProperties":true
		}`,
		},
		{
			id: "prompt.after_tool_batch", desc: "Mid-turn metabolism after a tool batch: admit evidence, update the machine map (internal; not model-callable)",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"properties":{
				"session_id":{"type":"string"},
				"tier":{"type":"integer"},
				"results":{
					"type":"array",
					"items":{
						"type":"object",
						"properties":{
							"tool":{"type":"string"},
							"name":{"type":"string"},
							"ok":{"type":"boolean"},
							"success":{"type":"boolean"},
							"result":{"type":"string"},
							"error":{"type":"string"},
							"tool_call_id":{"type":"string"},
							"arguments":{"type":"object","additionalProperties":true},
							"input":{"type":"object","additionalProperties":true},
							"duration_ms":{"type":"number"}
						},
						"additionalProperties":true
					}
				},
				` + propHomeDir + `,
				` + propProjectPath + `,
				` + propGoBound + `
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"properties":{
				"ok":{"type":"boolean"},
				"eu_new":{"type":"integer"},
				"evidence_units":{"type":"integer"},
				"tools":{"type":"integer"},
				"skipped":{"type":"string"}
			},
			"additionalProperties":true
		}`,
		},

		{
			id: "mail.list", desc: "List recent email messages from the connected account (Python worker)",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"properties":{
				"query":{"type":"string","description":"Mailbox search query (provider syntax)"},
				"limit":{"type":"integer","minimum":1,"maximum":50,"description":"Maximum messages to return"},
				` + propHomeDir + `,
				` + propGoBound + `
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["ok","messages","count"],
			"properties":{
				"ok":{"type":"boolean"},
				"messages":{"type":"array"},
				"count":{"type":"integer","minimum":0},
				"query":{"type":"string"},
				"error":{"type":"string"}
			},
			"additionalProperties":true
		}`,
		},
		{
			id: "mail.send", desc: "Send an email from the connected account (always requires owner approval)",
			risk: RiskCheckpoint,
			in: `{
			"type":"object",
			"required":["to"],
			"properties":{
				"to":{"type":"string","minLength":3,"description":"Recipient email address"},
				"subject":{"type":"string","description":"Subject line"},
				"body":{"type":"string","description":"Message body"},
				"text":{"type":"string","description":"Alias of body"},
				` + propHomeDir + `,
				` + propGoBound + `
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["ok"],
			"properties":{
				"ok":{"type":"boolean"},
				"message_id":{"type":"string"},
				"to":{"type":"string"},
				"subject":{"type":"string"},
				"message":{"type":"string"},
				"error":{"type":"string"}
			},
			"additionalProperties":true
		}`,
		},
		{
			id: "calendar.list_events", desc: "List upcoming calendar events from the connected account (Python worker)",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"properties":{
				"days":{"type":"integer","minimum":1,"maximum":60,"description":"How many days ahead to list"},
				"time_min":{"type":"string","description":"ISO-8601 lower bound"},
				"time_max":{"type":"string","description":"ISO-8601 upper bound"},
				` + propHomeDir + `,
				` + propGoBound + `
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["ok","events","count"],
			"properties":{
				"ok":{"type":"boolean"},
				"events":{"type":"array"},
				"count":{"type":"integer","minimum":0},
				"days":{"type":"integer"},
				"error":{"type":"string"}
			},
			"additionalProperties":true
		}`,
		},
		{
			id: "calendar.create_event", desc: "Create a calendar event on the connected account (requires approval)",
			risk: RiskMutation,
			in: `{
			"type":"object",
			"required":["title","start","end"],
			"properties":{
				"title":{"type":"string","minLength":1,"description":"Event title"},
				"start":{"type":"string","minLength":1,"description":"ISO-8601 start"},
				"end":{"type":"string","minLength":1,"description":"ISO-8601 end"},
				"description":{"type":"string","description":"Event description"},
				` + propHomeDir + `,
				` + propGoBound + `
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["ok"],
			"properties":{
				"ok":{"type":"boolean"},
				"id":{"type":"string"},
				"title":{"type":"string"},
				"start":{"type":"string"},
				"end":{"type":"string"},
				"message":{"type":"string"},
				"error":{"type":"string"}
			},
			"additionalProperties":true
		}`,
		},
		{
			id:   "partner.vigil",
			desc: "Grant or stop Remedy's muscle-free nights, or report whether they are on. Enable and disable only after the owner says yes or no.",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"properties":{
				"action":{"type":"string","enum":["status","enable","disable"],"description":"status reports the night grant; enable or disable is the owner's yes or no"},
				` + propHomeDir + `,
				` + propGoBound + `
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["ok"],
			"properties":{
				"ok":{"type":"boolean"},
				"vigil":{"type":"object"},
				"error":{"type":"string"}
			},
			"additionalProperties":false
		}`,
		},
		{
			id:   "house_rules",
			desc: "Read or change the owner's house rules. Record first-run answers, skip all (use defaults), show a summary, confirm, propose a later change, roll back, or revisit. Peers cannot change house rules. Secrets are never stored in the audit log.",
			risk: RiskMutation,
			in: `{
			"type":"object",
			"properties":{
				"action":{"type":"string","enum":["status","record","skip_all","summarize","confirm","propose","decline","rollback","revisit","set_mode","update_draft"],"description":"status is read-only; confirm applies a pending change or the first-run summary; propose needs confirm for sensitive keys"},
				"answers":{"type":"object","description":"Answers to record"},
				"patch":{"type":"object","description":"Settings patch for propose or update_draft"},
				"skipped":{"type":"array","items":{"type":"string"},"description":"Question ids to skip"},
				"confirm":{"type":"boolean","description":"Apply a propose immediately when already confirmed"},
				"pending_id":{"type":"string"},
				"mode":{"type":"string","enum":["conversation","traditional"]},
				"source":{"type":"string"},
				"session_id":{"type":"string","description":"Bound by the runtime"},
				"caller_origin":{"type":"string","description":"Bound by the runtime"},
				` + propHomeDir + `,
				` + propGoBound + `
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["ok"],
			"properties":{
				"ok":{"type":"boolean"},
				"error":{"type":"string"},
				"summary":{"type":"string"},
				"needs_confirmation":{"type":"boolean"},
				"pending_id":{"type":"string"},
				"message":{"type":"string"},
				"prompt":{"type":"string"},
				"session":{"type":"object"},
				"changes":{"type":"object"},
				"diff":{"type":"object"}
			},
			"additionalProperties":true
		}`,
		},
		{
			id:   "setup.session",
			desc: "Internal first-run setup session (draft, mode, mind gate). Hidden from the model.",
			risk: RiskMutation,
			in: `{
			"type":"object",
			"properties":{
				"action":{"type":"string","enum":["status","update_draft","set_mode","skip_all","confirm","offer_prompt"]},
				"answers":{"type":"object"},
				"patch":{"type":"object"},
				"skipped":{"type":"array","items":{"type":"string"}},
				"mode":{"type":"string","enum":["conversation","traditional"]},
				"session_id":{"type":"string","description":"Bound by the runtime"},
				"caller_origin":{"type":"string","description":"Bound by the runtime"},
				` + propHomeDir + `,
				` + propGoBound + `
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["ok"],
			"properties":{
				"ok":{"type":"boolean"},
				"error":{"type":"string"},
				"session":{"type":"object"}
			},
			"additionalProperties":true
		}`,
		},
		{
			id:   "partner.pulse",
			desc: "One muscle-free night tick for the always-on hearth (internal; not model-callable)",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"properties":{
				` + propHomeDir + `,
				` + propGoBound + `
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["ok"],
			"properties":{
				"ok":{"type":"boolean"},
				"vigil":{"type":"object"},
				"error":{"type":"string"}
			},
			"additionalProperties":false
		}`,
		},
		{
			id:   "partner.improve",
			desc: "One unattended organism learning tick (internal; not model-callable)",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"properties":{
				` + propHomeDir + `,
				` + propGoBound + `,
				"project_path":{"type":"string"}
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["ok"],
			"properties":{
				"ok":{"type":"boolean"},
				"organism":{"type":"object"},
				"code":{"type":"object"},
				"skipped":{"type":"string"},
				"error":{"type":"string"}
			},
			"additionalProperties":true
		}`,
		},
		{
			id:   "peer.list",
			desc: "List the owner's peer-agent contacts (independent bots Remedy can talk to over A2A or MCP). Not muscles.",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"properties":{
				` + propHomeDir + `,
				` + propGoBound + `
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["ok","peers","total"],
			"properties":{
				"ok":{"type":"boolean"},
				"peers":{"type":"array","items":{"type":"object"}},
				"total":{"type":"integer","minimum":0},
				"error":{"type":"string"}
			},
			"additionalProperties":false
		}`,
		},
		{
			id:   "peer.ask",
			desc: "Ask a named peer bot a question over A2A. Peers keep their own memory and permissions. Money, send, and delete still need the owner.",
			risk: RiskMutation,
			in: `{
			"type":"object",
			"required":["question"],
			"properties":{
				"question":{"type":"string","minLength":1,"description":"Plain-language question for the peer"},
				"peer_id":{"type":"string","description":"Contact id from peer.list. If omitted, pick by skills."},
				` + propHomeDir + `,
				` + propGoBound + `
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["ok"],
			"properties":{
				"ok":{"type":"boolean"},
				"summary":{"type":"string"},
				"results":{"type":"array","items":{"type":"object"}},
				"error":{"type":"string"}
			},
			"additionalProperties":false
		}`,
		},
		{
			id:   "peer.delegate",
			desc: "Delegate a task to a peer bot (named, or picked by skills) over A2A and merge the replies.",
			risk: RiskMutation,
			in: `{
			"type":"object",
			"required":["task"],
			"properties":{
				"task":{"type":"string","minLength":1,"description":"Plain-language task for the peer"},
				"peer_id":{"type":"string","description":"Contact id from peer.list. If omitted, pick by skills."},
				` + propHomeDir + `,
				` + propGoBound + `
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["ok"],
			"properties":{
				"ok":{"type":"boolean"},
				"summary":{"type":"string"},
				"results":{"type":"array","items":{"type":"object"}},
				"error":{"type":"string"}
			},
			"additionalProperties":false
		}`,
		},
		{
			id:   "peer.status",
			desc: "Look up a peer task Remedy started, or a local inbound peer task.",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"required":["task_id"],
			"properties":{
				"task_id":{"type":"string","minLength":1},
				"peer_id":{"type":"string"},
				` + propHomeDir + `,
				` + propGoBound + `
			},
			"additionalProperties":false
		}`,
			out: `{
			"type":"object",
			"required":["ok"],
			"properties":{
				"ok":{"type":"boolean"},
				"task":{"type":"object"},
				"error":{"type":"string"}
			},
			"additionalProperties":false
		}`,
		},
	}
}

// RegisterPythonWorkerTools installs Tool ABI descriptors that execute over
// RMDY KindToolRequest frames on the supervised Python worker. caller must be
// live; there is no in-process Python fallback. Pass a *workers.LiveCaller so
// a respawned worker is picked up without re-registration.
func RegisterPythonWorkerTools(registry *Registry, caller FrameCaller) error {
	if registry == nil {
		return fmt.Errorf("%w: nil registry", ErrInvalidDescriptor)
	}
	if caller == nil {
		return errors.New("python worker frame caller is required")
	}
	exec := NewRMDYExecutor(caller)
	for _, r := range pythonWorkerToolSpecs() {
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
	return registerVoiceVisionWorkerTools(registry, exec)
}

func registerVoiceVisionWorkerTools(registry *Registry, exec Executor) error {
	rows := []pyToolSpec{
		{
			id: "voice.speak", desc: "Synthesize speech via Python voice lane (internal)",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"required":["text"],
			"properties":{
				` + propHomeDir + `,
				` + propGoBound + `,
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
				` + propHomeDir + `,
				` + propGoBound + `,
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
				` + propHomeDir + `,
				` + propGoBound + `,
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
				` + propHomeDir + `,
				` + propGoBound + `,
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
				` + propHomeDir + `,
				` + propGoBound + `,
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
			"properties":{` + propHomeDir + `,` + propGoBound + `},
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
				` + propHomeDir + `,
				` + propGoBound + `,
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
				` + propHomeDir + `,
				` + propGoBound + `,
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
			"properties":{` + propHomeDir + `,` + propGoBound + `},
			"additionalProperties":false
		}`,
			out: `{"type":"object","additionalProperties":true}`,
		},
		{
			id: "vision.stop", desc: "Stop local vision server (internal)",
			risk: RiskMutation,
			in: `{
			"type":"object",
			"properties":{` + propHomeDir + `,` + propGoBound + `},
			"additionalProperties":false
		}`,
			out: `{"type":"object","additionalProperties":true}`,
		},
		{
			id: "vision.progress", desc: "Vision install progress snapshot (internal)",
			risk: RiskReadOnly,
			in: `{
			"type":"object",
			"properties":{` + propHomeDir + `,` + propGoBound + `},
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
