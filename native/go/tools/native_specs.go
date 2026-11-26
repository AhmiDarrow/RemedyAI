package tools

import (
	"context"
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

type toolSpec struct {
	id, desc string
	risk     Risk
	in, out  string
}

func nativeToolSpecs() []toolSpec {
	return []toolSpec{
		{
			id: "text.slugify", desc: "Slugify text (diagnostic; hidden from the model)",
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
			id: "text.word_count", desc: "Count whitespace-separated words (diagnostic; hidden from the model)",
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
			id: "workspace.search", desc: "Regex search over workspace text files. pattern is a regular expression, not a literal; invalid regex is an error. capped=true means more matches exist beyond max_matches.",
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
			id: "mail.list", desc: "List recent email messages from the connected account",
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
			id: "calendar.list_events", desc: "List upcoming calendar events from the connected account",
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
				"confirm":{"type":"boolean","description":"Set true to apply a propose in one step; a sensitive change is then approved by the owner on a card"},
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

// mailCalendarToolIDs are the tools whose descriptions live in this file but
// which the runtime serves in Go: mail and calendar (Google, IMAP/SMTP,
// CalDAV), house rules and first-run setup (mind/houserules) and the other
// bots (peer.*).
var mailCalendarToolIDs = map[string]struct{}{
	"mail.list": {}, "mail.send": {}, "calendar.list_events": {}, "calendar.create_event": {},
	"house_rules": {}, "setup.session": {},
	"peer.list": {}, "peer.ask": {}, "peer.delegate": {}, "peer.status": {},
}

// ErrToolNotAttached is what a registered tool answers before the runtime has
// given it its executor.
var ErrToolNotAttached = errors.New("this tool is not attached to the runtime")

// RegisterMailCalendarTools registers the mail and calendar tools. The
// runtime wraps their executors with the native ones; until it does they
// answer ErrToolNotAttached.
func RegisterMailCalendarTools(registry *Registry) error {
	if registry == nil {
		return fmt.Errorf("%w: nil registry", ErrInvalidDescriptor)
	}
	unattached := ExecutorFunc(func(context.Context, Request) (Result, error) {
		return Result{}, ErrToolNotAttached
	})
	for _, r := range nativeToolSpecs() {
		if _, ok := mailCalendarToolIDs[r.id]; !ok {
			continue
		}
		if err := registry.Register(Descriptor{
			ID:           r.id,
			Version:      1,
			Description:  r.desc,
			Runtime:      RuntimeGo,
			Risk:         r.risk,
			InputSchema:  json.RawMessage(r.in),
			OutputSchema: json.RawMessage(r.out),
		}, unattached); err != nil {
			return err
		}
	}
	return nil
}
