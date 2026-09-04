package tools

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
)

// RegisterZigHostTools installs RuntimeZig Tool ABI executors that call
// remedy_core through native/go/core. Registration always succeeds; execute
// fails closed when the library is missing or the host export is unsupported.
// No Python / os soft fallback.
func RegisterZigHostTools(registry *Registry) error {
	if registry == nil {
		return fmt.Errorf("%w: nil registry", ErrInvalidDescriptor)
	}

	if err := registry.Register(Descriptor{
		ID:           "computer.screenshot",
		Version:      1,
		Description:  "Capture the virtual screen to a PNG under REMEDY_HOME/computer/shots (Zig)",
		Runtime:      RuntimeZig,
		Risk:         RiskReadOnly,
		Capabilities: []string{"computer.read"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"properties":{
				"label":{"type":"string"},
				"x":{"type":"integer"},
				"y":{"type":"integer"},
				"width":{"type":"integer","minimum":1},
				"height":{"type":"integer","minimum":1},
				"scale":{"type":"number","exclusiveMinimum":0}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["path","width","height","origin"],
			"properties":{
				"path":{"type":"string","minLength":1},
				"width":{"type":"integer","minimum":1},
				"height":{"type":"integer","minimum":1},
				"origin":{
					"type":"object",
					"required":["x","y"],
					"properties":{
						"x":{"type":"integer"},
						"y":{"type":"integer"}
					},
					"additionalProperties":false
				},
				"requested":{
					"type":"object",
					"properties":{
						"x":{"type":"integer"},
						"y":{"type":"integer"},
						"width":{"type":"integer"},
						"height":{"type":"integer"},
						"scale":{"type":"number"}
					},
					"additionalProperties":false
				}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeComputerScreenshot)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:           "computer.windows",
		Version:      1,
		Description:  "List visible titled top-level windows (Zig)",
		Runtime:      RuntimeZig,
		Risk:         RiskReadOnly,
		Capabilities: []string{"computer.read"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"properties":{
				"limit":{"type":"integer","minimum":1,"maximum":200}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["windows","total"],
			"properties":{
				"windows":{"type":"array"},
				"total":{"type":"integer","minimum":0}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeComputerWindows)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:           "computer.monitors",
		Version:      1,
		Description:  "List display monitors with bounds and scale (Zig)",
		Runtime:      RuntimeZig,
		Risk:         RiskReadOnly,
		Capabilities: []string{"computer.read"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["monitors","total"],
			"properties":{
				"monitors":{"type":"array"},
				"total":{"type":"integer","minimum":0}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeComputerMonitors)); err != nil {
		return err
	}

	return nil
}

func executeComputerScreenshot(_ context.Context, request Request) (Result, error) {
	var body struct {
		Label  string   `json:"label"`
		X      *int     `json:"x"`
		Y      *int     `json:"y"`
		Width  *int     `json:"width"`
		Height *int     `json:"height"`
		Scale  *float64 `json:"scale"`
	}
	if len(request.Input) > 0 {
		if err := json.Unmarshal(request.Input, &body); err != nil {
			return Result{}, ErrInvalidInput
		}
	}
	label := strings.TrimSpace(body.Label)
	if label == "" {
		label = "capture"
	}
	home := resolveToolHome()
	if home == "" {
		return Result{}, fmt.Errorf("%w: REMEDY_HOME required for computer.screenshot", core.ErrUnavailable)
	}
	hasBounds := body.X != nil && body.Y != nil && body.Width != nil && body.Height != nil
	var info map[string]any
	var err error
	if hasBounds {
		scale := 1.0
		if body.Scale != nil && *body.Scale > 0 {
			scale = *body.Scale
		}
		info, err = core.ScreenshotRegionPNG(home, label, *body.X, *body.Y, *body.Width, *body.Height, scale)
	} else {
		info, err = core.ScreenshotPNG(home, label)
	}
	if err != nil {
		return Result{}, err
	}
	out, err := json.Marshal(info)
	return Result{Output: out}, err
}

func executeComputerWindows(_ context.Context, request Request) (Result, error) {
	var body struct {
		Limit int `json:"limit"`
	}
	if len(request.Input) > 0 {
		if err := json.Unmarshal(request.Input, &body); err != nil {
			return Result{}, ErrInvalidInput
		}
	}
	limit := body.Limit
	if limit <= 0 {
		limit = 50
	}
	if limit > 200 {
		limit = 200
	}
	raw, err := core.ListWindowsJSON(uint32(limit))
	if err != nil {
		return Result{}, err
	}
	var windows []any
	if len(raw) == 0 {
		windows = []any{}
	} else if err := json.Unmarshal(raw, &windows); err != nil {
		return Result{}, fmt.Errorf("list_windows: invalid JSON: %w", err)
	}
	out, err := json.Marshal(map[string]any{
		"windows": windows,
		"total":   len(windows),
	})
	return Result{Output: out}, err
}

func executeComputerMonitors(context.Context, Request) (Result, error) {
	raw, err := core.ListMonitorsJSON()
	if err != nil {
		return Result{}, err
	}
	var monitors []any
	if len(raw) == 0 {
		monitors = []any{}
	} else if err := json.Unmarshal(raw, &monitors); err != nil {
		return Result{}, fmt.Errorf("list_monitors: invalid JSON: %w", err)
	}
	out, err := json.Marshal(map[string]any{
		"monitors": monitors,
		"total":    len(monitors),
	})
	return Result{Output: out}, err
}

func resolveToolHome() string {
	if env := strings.TrimSpace(os.Getenv("REMEDY_HOME")); env != "" {
		return env
	}
	userHome, err := os.UserHomeDir()
	if err != nil || userHome == "" {
		return ""
	}
	return filepath.Join(userHome, ".remedy")
}
