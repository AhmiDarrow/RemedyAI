package tools

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// Session-scoped tools: the checklist the owner watches, the screenshot the
// model can actually see, and the delegation hand-off.

// todoStatuses mirrors remedy.core.build_todos._VALID, which is what the
// Desktop checklist renders.
var todoStatuses = map[string]struct{}{
	"pending":     {},
	"in_progress": {},
	"completed":   {},
	"cancelled":   {},
}

const (
	todoMaxItems   = 100
	todoMaxTextLen = 240
)

// RegisterSessionTools installs todo, screenshot and delegate.
func RegisterSessionTools(registry *Registry) error {
	if registry == nil {
		return fmt.Errorf("%w: nil registry", ErrInvalidDescriptor)
	}

	if err := registry.Register(Descriptor{
		ID:      "todo",
		Version: 1,
		Description: "Replace the visible task checklist for this session. Send the whole list every time — " +
			"items you leave out are dropped. Keep exactly one item in_progress while you work on it.",
		Runtime:      RuntimeGo,
		Risk:         RiskMutation,
		Capabilities: []string{"session.state"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["items"],
			"properties":{
				"items":{
					"type":"array",
					"maxItems":100,
					"description":"The complete checklist, in the order it should be shown.",
					"items":{
						"type":"object",
						"required":["text"],
						"properties":{
							"id":{"type":"string","description":"Stable id for this item. Reuse it across calls so the owner sees one row change status instead of a new row."},
							"text":{"type":"string","minLength":1,"description":"What the step is, in the owner's words."},
							"status":{"type":"string","enum":["pending","in_progress","completed","cancelled"],"description":"Defaults to pending."}
						},
						"additionalProperties":false
					}
				},
				` + propHomeDir + `,
				"session_id":{"type":"string","description":"Session that owns the checklist (bound by the runtime; model-supplied values are ignored)"}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["todos","open"],
			"properties":{
				"todos":{
					"type":"array",
					"items":{
						"type":"object",
						"required":["id","content","status"],
						"properties":{
							"id":{"type":"string"},
							"content":{"type":"string"},
							"status":{"type":"string"}
						},
						"additionalProperties":false
					}
				},
				"open":{"type":"integer","minimum":0},
				"path":{"type":"string"}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeTodo)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:      "screenshot",
		Version: 1,
		Description: "Capture the screen and look at it. Returns the image itself plus the PNG path. " +
			"Pass x/y/width/height to capture one region instead of the whole virtual screen.",
		Runtime:      RuntimeZig,
		Risk:         RiskReadOnly,
		Capabilities: []string{"computer.read"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"properties":{
				"label":{"type":"string","description":"Short name for the capture; it becomes part of the file name."},
				"x":{"type":"integer","description":"Left edge of the region, in virtual-screen pixels. Requires y, width and height."},
				"y":{"type":"integer","description":"Top edge of the region, in virtual-screen pixels."},
				"width":{"type":"integer","minimum":1,"description":"Region width in pixels."},
				"height":{"type":"integer","minimum":1,"description":"Region height in pixels."},
				"scale":{"type":"number","exclusiveMinimum":0,"description":"Scale the captured region before encoding (0.5 halves it). Default 1."}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["path","width","height"],
			"properties":{
				"path":{"type":"string","minLength":1},
				"width":{"type":"integer","minimum":1},
				"height":{"type":"integer","minimum":1},
				"media_type":{"type":"string"},
				"bytes":{"type":"integer","minimum":0},
				"origin":{
					"type":"object",
					"required":["x","y"],
					"properties":{"x":{"type":"integer"},"y":{"type":"integer"}},
					"additionalProperties":false
				}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeScreenshot)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:      "delegate",
		Version: 1,
		Description: "Hand one self-contained coding mission to Claude Code running headless in a folder, and wait for its report. " +
			"Use it for a long build you would otherwise do step by step here; its progress appears in this conversation as it works. " +
			"The agent cannot ask questions and cannot delegate again, so put everything it needs in the mission.",
		Runtime:      RuntimeZig,
		Risk:         RiskMutation,
		Capabilities: []string{"process.spawn"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["mission"],
			"properties":{
				"mission":{"type":"string","minLength":1,"description":"The whole assignment in one message: what to change, where, and how it will be verified. The agent cannot ask you questions."},
				"cwd":{"type":"string","description":"Absolute folder the agent works in. Defaults to the bound project folder; a folder outside it is refused."},
				"agent":{"type":"string","enum":["claude-code","codex"],"description":"Which coding agent to hand the mission to. Default claude-code."},
				"allow_shell":{"type":"boolean","description":"Let the agent run shell commands as well as edit files. Default false, which allows file edits only."},
				"timeout_ms":{"type":"integer","minimum":1,"maximum":7200000,"description":"Kill the agent's process tree after this long (default 1800000, thirty minutes; maximum two hours)."},
				` + propWorkspaceRoot + `,
				"write_roots":{"type":"array","items":{"type":"string","minLength":1},"maxItems":16,"description":"Write-jail roots (bound by the runtime from the session; model-supplied values are ignored)"}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["report","agent","exit_code"],
			"properties":{
				"report":{"type":"string"},
				"agent":{"type":"string"},
				"exit_code":{"type":"integer"},
				"cwd":{"type":"string"},
				"session_id":{"type":"string"},
				"duration_ms":{"type":"integer"},
				"truncated":{"type":"boolean"}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeDelegate)); err != nil {
		return err
	}

	return nil
}

// ---------------------------------------------------------------------------
// todo
// ---------------------------------------------------------------------------

// TodoRow is one checklist row as the Desktop and the @@todos: token carry it.
type TodoRow struct {
	ID      string `json:"id"`
	Content string `json:"content"`
	Status  string `json:"status"`
}

// TodoState is <home>/sessions/<sid>/todos.json.
type TodoState struct {
	Todos     []TodoRow `json:"todos"`
	Open      int       `json:"open"`
	UpdatedMS int64     `json:"updated_ms,omitempty"`
}

func executeTodo(_ context.Context, request Request) (Result, error) {
	var body struct {
		Items []struct {
			ID     string `json:"id"`
			Text   string `json:"text"`
			Status string `json:"status"`
		} `json:"items"`
		HomeDir   string `json:"home_dir"`
		SessionID string `json:"session_id"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, fmt.Errorf("%w: %v", ErrInvalidInput, err)
	}
	if len(body.Items) > todoMaxItems {
		return Result{}, fmt.Errorf("%w: %d items is more than the %d the checklist holds — keep it to the steps that matter",
			ErrInvalidInput, len(body.Items), todoMaxItems)
	}
	rows := make([]TodoRow, 0, len(body.Items))
	seen := map[string]struct{}{}
	inProgress := 0
	for i, item := range body.Items {
		text := strings.TrimSpace(item.Text)
		if text == "" {
			return Result{}, fmt.Errorf("%w: item %d has empty text", ErrInvalidInput, i+1)
		}
		if len(text) > todoMaxTextLen {
			text = text[:runeFloorAt([]byte(text), todoMaxTextLen)]
		}
		status := strings.TrimSpace(strings.ToLower(item.Status))
		if status == "" {
			status = "pending"
		}
		if _, ok := todoStatuses[status]; !ok {
			return Result{}, fmt.Errorf("%w: item %d has status %q; use pending, in_progress, completed or cancelled",
				ErrInvalidInput, i+1, item.Status)
		}
		if status == "in_progress" {
			inProgress++
		}
		id := strings.TrimSpace(item.ID)
		if id == "" {
			id = "t" + strconv.Itoa(i+1)
		}
		if _, dup := seen[id]; dup {
			return Result{}, fmt.Errorf("%w: item %d repeats id %q; every item needs its own id", ErrInvalidInput, i+1, id)
		}
		seen[id] = struct{}{}
		rows = append(rows, TodoRow{ID: id, Content: text, Status: status})
	}
	if inProgress > 1 {
		return Result{}, fmt.Errorf("%w: %d items are in_progress; mark exactly one, so the owner can see what you are doing now",
			ErrInvalidInput, inProgress)
	}

	state := TodoState{Todos: rows, Open: openTodoCount(rows)}
	path, err := todosPath(body.HomeDir, body.SessionID)
	if err != nil {
		return Result{}, err
	}
	if err := writeTodoState(path, state); err != nil {
		return Result{}, err
	}
	out, err := json.Marshal(map[string]any{
		"todos": rows,
		"open":  state.Open,
		"path":  filepath.ToSlash(path),
	})
	return Result{Output: out}, err
}

func openTodoCount(rows []TodoRow) int {
	open := 0
	for _, row := range rows {
		if row.Status == "pending" || row.Status == "in_progress" {
			open++
		}
	}
	return open
}

func todosPath(home, sessionID string) (string, error) {
	home = strings.TrimSpace(home)
	if home == "" {
		home = resolveToolHome()
	}
	if home == "" {
		return "", fmt.Errorf("%w: no Remedy home is available, so the checklist cannot be saved", ErrInvalidInput)
	}
	sid := strings.TrimSpace(sessionID)
	if sid == "" {
		return "", fmt.Errorf("%w: the checklist belongs to a session and this call has none", ErrInvalidInput)
	}
	if !sessionIDPattern.MatchString(sid) {
		return "", fmt.Errorf("%w: session id %q is not a usable directory name", ErrInvalidInput, sid)
	}
	return filepath.Join(home, "sessions", sid, "todos.json"), nil
}

func writeTodoState(path string, state TodoState) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return fmt.Errorf("%w: could not create %s: %v", ErrInvalidInput, filepath.Dir(path), err)
	}
	data, err := json.Marshal(state)
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return fmt.Errorf("%w: could not save the checklist to %s: %v", ErrInvalidInput, path, err)
	}
	if err := os.Rename(tmp, path); err != nil {
		_ = os.Remove(tmp)
		return fmt.Errorf("%w: could not save the checklist to %s: %v", ErrInvalidInput, path, err)
	}
	return nil
}

// LoadTodoState reads a session checklist back. Absent state is an empty list,
// not an error: a session simply may not have one yet.
func LoadTodoState(home, sessionID string) (TodoState, error) {
	path, err := todosPath(home, sessionID)
	if err != nil {
		return TodoState{}, err
	}
	data, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return TodoState{}, nil
		}
		return TodoState{}, err
	}
	var state TodoState
	if err := json.Unmarshal(data, &state); err != nil {
		return TodoState{}, err
	}
	return state, nil
}

// ---------------------------------------------------------------------------
// screenshot
// ---------------------------------------------------------------------------

// executeScreenshot captures through the Zig host (computer.screenshot's
// executor) and then attaches the PNG itself, so the model sees the pixels
// instead of a path it cannot open.
func executeScreenshot(ctx context.Context, request Request) (Result, error) {
	captured, err := executeComputerScreenshot(ctx, request)
	if err != nil {
		return Result{}, err
	}
	var shot struct {
		Path   string `json:"path"`
		Width  int    `json:"width"`
		Height int    `json:"height"`
		Origin *struct {
			X int `json:"x"`
			Y int `json:"y"`
		} `json:"origin"`
	}
	if err := json.Unmarshal(captured.Output, &shot); err != nil {
		return Result{}, fmt.Errorf("screenshot: capture result could not be read: %w", err)
	}
	data, err := os.ReadFile(shot.Path)
	if err != nil {
		return Result{}, fmt.Errorf("screenshot: the capture at %s could not be read back: %w", shot.Path, err)
	}
	payload := map[string]any{
		"path":       filepath.ToSlash(shot.Path),
		"width":      shot.Width,
		"height":     shot.Height,
		"media_type": "image/png",
		"bytes":      len(data),
	}
	if shot.Origin != nil {
		payload["origin"] = map[string]any{"x": shot.Origin.X, "y": shot.Origin.Y}
	}
	out, err := json.Marshal(payload)
	if err != nil {
		return Result{}, err
	}
	return Result{Output: out, Images: []ImageResult{{MediaType: "image/png", Data: data}}}, nil
}

// delegate's executor lives in delegate.go: it resolves the coding agent on
// PATH, spawns it through the same authorized path as bash, and streams its
// events into this turn's trail.
