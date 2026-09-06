package tools

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// RegisterZigHostTools installs RuntimeZig Tool ABI executors that call
// remedy_core through native/go/core. Registration always succeeds; execute
// fails closed when the library is missing or the host export is unsupported.
// No Python / os/exec soft fallback.
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
		ID:           "computer.print_window",
		Version:      1,
		Description:  "Capture an HWND via Zig PrintWindow (occluded-window safe) to PNG under REMEDY_HOME/computer/shots",
		Runtime:      RuntimeZig,
		Risk:         RiskReadOnly,
		Capabilities: []string{"computer.read"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["hwnd"],
			"properties":{
				"hwnd":{"type":"integer","minimum":1},
				"label":{"type":"string"}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["path","width","height","origin","hwnd","method"],
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
				"hwnd":{"type":"integer","minimum":1},
				"method":{"type":"string","const":"PrintWindow"}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeComputerPrintWindow)); err != nil {
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
		ID:           "computer.foreground",
		Version:      1,
		Description:  "Foreground window detail {hwnd,title,pid,exe} via Zig (Windows + Linux/X11)",
		Runtime:      RuntimeZig,
		Risk:         RiskReadOnly,
		Capabilities: []string{"computer.read"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["hwnd","title","pid","exe"],
			"properties":{
				"hwnd":{"type":"integer","minimum":0},
				"title":{"type":"string"},
				"pid":{"type":"integer","minimum":0},
				"exe":{"type":"string"}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeComputerForeground)); err != nil {
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

	if err := registry.Register(Descriptor{
		ID:           "computer.snapshot",
		Version:      1,
		Description:  "Accessibility control snapshot via Zig UIA (Windows) or AT-SPI (Linux)",
		Runtime:      RuntimeZig,
		Risk:         RiskReadOnly,
		Capabilities: []string{"computer.read"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"properties":{
				"hwnd":{"type":"integer","minimum":0},
				"max_elements":{"type":"integer","minimum":1,"maximum":120},
				"preferred_only":{"type":"boolean"},
				"limit":{"type":"integer","minimum":1,"maximum":200}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["source","available","controls","total"],
			"properties":{
				"source":{"type":"string","enum":["uia","atspi","none"]},
				"available":{"type":"boolean"},
				"controls":{"type":"array"},
				"total":{"type":"integer","minimum":0}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeComputerSnapshot)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:           "computer.uia.focused",
		Version:      1,
		Description:  "Focused UIA element {name,role,value} via Zig (Windows; fail closed)",
		Runtime:      RuntimeZig,
		Risk:         RiskReadOnly,
		Capabilities: []string{"computer.read"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["available","element"],
			"properties":{
				"available":{"type":"boolean"},
				"element":{
					"oneOf":[
						{"type":"null"},
						{
							"type":"object",
							"properties":{
								"name":{"type":"string"},
								"role":{"type":"string"},
								"value":{"type":"string"}
							},
							"additionalProperties":true
						}
					]
				}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeComputerUIAFocused)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:           "computer.uia.read_text",
		Version:      1,
		Description:  "Read window title/text/fields via Zig UIA (Windows; fail closed)",
		Runtime:      RuntimeZig,
		Risk:         RiskReadOnly,
		Capabilities: []string{"computer.read"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["hwnd"],
			"properties":{
				"hwnd":{"type":"integer","minimum":1},
				"max_chars":{"type":"integer","minimum":1,"maximum":100000}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["available","hwnd","payload"],
			"properties":{
				"available":{"type":"boolean"},
				"hwnd":{"type":"integer","minimum":1},
				"payload":{
					"oneOf":[
						{"type":"null"},
						{
							"type":"object",
							"properties":{
								"title":{"type":"string"},
								"text":{"type":"string"},
								"fields":{"type":"array"}
							},
							"additionalProperties":true
						}
					]
				}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeComputerUIAReadText)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:           "computer.uia.action",
		Version:      1,
		Description:  "Invoke/set_value/toggle/scroll_into_view on a UIA element by hwnd+name+role (Zig; fail closed)",
		Runtime:      RuntimeZig,
		Risk:         RiskMutation,
		Capabilities: []string{"computer.input"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["hwnd","name","action"],
			"properties":{
				"hwnd":{"type":"integer","minimum":1},
				"name":{"type":"string","minLength":1,"maxLength":512},
				"role":{"type":"string","maxLength":128},
				"action":{"type":"string","enum":["invoke","set_value","toggle","scroll_into_view"]},
				"text":{"type":"string","maxLength":8000}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["ok","message","hwnd","name","action"],
			"properties":{
				"ok":{"type":"boolean"},
				"message":{"type":"string"},
				"verified":{"type":"boolean"},
				"hwnd":{"type":"integer","minimum":1},
				"name":{"type":"string"},
				"role":{"type":"string"},
				"action":{"type":"string","enum":["invoke","set_value","toggle","scroll_into_view"]}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeComputerUIAAction)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:           "computer.click",
		Version:      1,
		Description:  "Click at virtual-screen physical pixels via Zig SendInput/X11 (fail closed)",
		Runtime:      RuntimeZig,
		Risk:         RiskMutation,
		Capabilities: []string{"computer.input"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["x","y"],
			"properties":{
				"x":{"type":"integer"},
				"y":{"type":"integer"},
				"button":{"type":"string","enum":["left","right","middle"]},
				"clicks":{"type":"integer","minimum":1,"maximum":3}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["ok","x","y","button","clicks"],
			"properties":{
				"ok":{"type":"boolean","const":true},
				"x":{"type":"integer"},
				"y":{"type":"integer"},
				"button":{"type":"string","enum":["left","right","middle"]},
				"clicks":{"type":"integer","minimum":1}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeComputerClick)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:           "computer.type",
		Version:      1,
		Description:  "Type UTF-8 text via Zig Unicode key events (fail closed)",
		Runtime:      RuntimeZig,
		Risk:         RiskMutation,
		Capabilities: []string{"computer.input"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["text"],
			"properties":{
				"text":{"type":"string","minLength":1,"maxLength":8000},
				"per_char_delay_ms":{"type":"integer","minimum":0,"maximum":200}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["ok","chars"],
			"properties":{
				"ok":{"type":"boolean","const":true},
				"chars":{"type":"integer","minimum":0}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeComputerType)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:           "computer.key",
		Version:      1,
		Description:  "Press a key or combo via Zig (enter, tab, ctrl+s, alt+f4, …; fail closed)",
		Runtime:      RuntimeZig,
		Risk:         RiskMutation,
		Capabilities: []string{"computer.input"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["key"],
			"properties":{
				"key":{"type":"string","minLength":1,"maxLength":64}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["ok","key","vks"],
			"properties":{
				"ok":{"type":"boolean","const":true},
				"key":{"type":"string","minLength":1},
				"vks":{"type":"array","items":{"type":"integer","minimum":0,"maximum":65535},"minItems":1}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeComputerKey)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:           "computer.key_hold",
		Version:      1,
		Description:  "Press and hold a single key via Zig for hold_ms (enter, a, f4, …; no combos; fail closed)",
		Runtime:      RuntimeZig,
		Risk:         RiskMutation,
		Capabilities: []string{"computer.input"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["key","hold_ms"],
			"properties":{
				"key":{"type":"string","minLength":1,"maxLength":64},
				"hold_ms":{"type":"integer","minimum":0,"maximum":60000}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["ok","key","vk","hold_ms"],
			"properties":{
				"ok":{"type":"boolean","const":true},
				"key":{"type":"string","minLength":1},
				"vk":{"type":"integer","minimum":0,"maximum":65535},
				"hold_ms":{"type":"integer","minimum":0,"maximum":60000}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeComputerKeyHold)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:           "computer.move",
		Version:      1,
		Description:  "Move the pointer to virtual-screen physical pixels via Zig (hover without click)",
		Runtime:      RuntimeZig,
		Risk:         RiskMutation,
		Capabilities: []string{"computer.input"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["x","y"],
			"properties":{
				"x":{"type":"integer"},
				"y":{"type":"integer"}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["ok","x","y"],
			"properties":{
				"ok":{"type":"boolean","const":true},
				"x":{"type":"integer"},
				"y":{"type":"integer"}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeComputerMove)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:           "computer.scroll",
		Version:      1,
		Description:  "Scroll at virtual-screen pixels via Zig (dy>0 up, dx>0 right)",
		Runtime:      RuntimeZig,
		Risk:         RiskMutation,
		Capabilities: []string{"computer.input"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["x","y"],
			"properties":{
				"x":{"type":"integer"},
				"y":{"type":"integer"},
				"dx":{"type":"integer"},
				"dy":{"type":"integer"}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["ok","x","y","dx","dy"],
			"properties":{
				"ok":{"type":"boolean","const":true},
				"x":{"type":"integer"},
				"y":{"type":"integer"},
				"dx":{"type":"integer"},
				"dy":{"type":"integer"}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeComputerScroll)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:           "computer.drag",
		Version:      1,
		Description:  "Drag from (x1,y1) to (x2,y2) via Zig interpolated mouse path",
		Runtime:      RuntimeZig,
		Risk:         RiskMutation,
		Capabilities: []string{"computer.input"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["x1","y1","x2","y2"],
			"properties":{
				"x1":{"type":"integer"},
				"y1":{"type":"integer"},
				"x2":{"type":"integer"},
				"y2":{"type":"integer"},
				"steps":{"type":"integer","minimum":1,"maximum":200}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["ok","x1","y1","x2","y2","steps"],
			"properties":{
				"ok":{"type":"boolean","const":true},
				"x1":{"type":"integer"},
				"y1":{"type":"integer"},
				"x2":{"type":"integer"},
				"y2":{"type":"integer"},
				"steps":{"type":"integer","minimum":1}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeComputerDrag)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:           "computer.focus",
		Version:      1,
		Description:  "Focus/restore a top-level window by hwnd via Zig (fail closed)",
		Runtime:      RuntimeZig,
		Risk:         RiskMutation,
		Capabilities: []string{"computer.input"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["hwnd"],
			"properties":{
				"hwnd":{"type":"integer","minimum":1}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["ok","hwnd","focused"],
			"properties":{
				"ok":{"type":"boolean"},
				"hwnd":{"type":"integer","minimum":1},
				"focused":{"type":"boolean"}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeComputerFocus)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:           "computer.window",
		Version:      1,
		Description:  "Minimize/maximize/restore/close/move/resize a window by hwnd via Zig (fail closed)",
		Runtime:      RuntimeZig,
		Risk:         RiskMutation,
		Capabilities: []string{"computer.input"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["hwnd","action"],
			"properties":{
				"hwnd":{"type":"integer","minimum":1},
				"action":{"type":"string","enum":["minimize","maximize","restore","close","move","resize"]},
				"x":{"type":"integer"},
				"y":{"type":"integer"},
				"width":{"type":"integer","minimum":0},
				"height":{"type":"integer","minimum":0}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["ok","hwnd","action"],
			"properties":{
				"ok":{"type":"boolean","const":true},
				"hwnd":{"type":"integer","minimum":1},
				"action":{"type":"string","enum":["minimize","maximize","restore","close","move","resize"]},
				"x":{"type":"integer"},
				"y":{"type":"integer"},
				"width":{"type":"integer","minimum":0},
				"height":{"type":"integer","minimum":0}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeComputerWindow)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:           "clipboard.read",
		Version:      1,
		Description:  "Read OS text clipboard via Zig (CF_UNICODETEXT / X11 CLIPBOARD)",
		Runtime:      RuntimeZig,
		Risk:         RiskReadOnly,
		Capabilities: []string{"computer.read"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["text","chars"],
			"properties":{
				"text":{"type":"string"},
				"chars":{"type":"integer","minimum":0}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeClipboardRead)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:           "clipboard.read_files",
		Version:      1,
		Description:  "Read CF_HDROP clipboard file paths via Zig (Windows; empty when absent)",
		Runtime:      RuntimeZig,
		Risk:         RiskReadOnly,
		Capabilities: []string{"computer.read"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["files","count"],
			"properties":{
				"files":{"type":"array","items":{"type":"string"}},
				"count":{"type":"integer","minimum":0}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeClipboardReadFiles)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:           "clipboard.read_image",
		Version:      1,
		Description:  "Read CF_DIB clipboard image as PNG under REMEDY_HOME/computer/clipboard (Windows; path-based to avoid Tool ABI payload bloat)",
		Runtime:      RuntimeZig,
		Risk:         RiskReadOnly,
		Capabilities: []string{"computer.read"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"properties":{
				"label":{"type":"string"}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["available","path","bytes"],
			"properties":{
				"available":{"type":"boolean"},
				"path":{"type":"string"},
				"bytes":{"type":"integer","minimum":0}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeClipboardReadImage)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:           "clipboard.write",
		Version:      1,
		Description:  "Replace OS text clipboard via Zig (UTF-8)",
		Runtime:      RuntimeZig,
		Risk:         RiskMutation,
		Capabilities: []string{"computer.input"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["text"],
			"properties":{
				"text":{"type":"string","maxLength":1000000}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["ok","chars"],
			"properties":{
				"ok":{"type":"boolean","const":true},
				"chars":{"type":"integer","minimum":0}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeClipboardWrite)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:           "shell.exec",
		Version:      1,
		Description:  "Authorized one-shot argv capture via Zig (absolute argv[0]; no os/exec)",
		Runtime:      RuntimeZig,
		Risk:         RiskMutation,
		Capabilities: []string{"process.spawn"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["argv"],
			"properties":{
				"argv":{
					"type":"array",
					"minItems":1,
					"maxItems":256,
					"items":{"type":"string","minLength":1}
				},
				"cwd":{"type":"string"},
				"env":{
					"type":"object",
					"additionalProperties":{"type":"string"}
				},
				"timeout_ms":{"type":"integer","minimum":1,"maximum":600000},
				"owner_confirmed":{"type":"boolean"},
				"write_roots":{
					"type":"array",
					"items":{"type":"string","minLength":1},
					"maxItems":16
				}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["exit_code","timed_out","stdout","stderr"],
			"properties":{
				"exit_code":{"type":"integer","minimum":0},
				"timed_out":{"type":"boolean"},
				"stdout":{"type":"string"},
				"stderr":{"type":"string"}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeShellExec)); err != nil {
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

func executeComputerPrintWindow(_ context.Context, request Request) (Result, error) {
	var body struct {
		HWND  *uint64 `json:"hwnd"`
		Label string  `json:"label"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, ErrInvalidInput
	}
	if body.HWND == nil || *body.HWND == 0 {
		return Result{}, ErrInvalidInput
	}
	label := strings.TrimSpace(body.Label)
	if label == "" {
		label = "hwnd"
	}
	home := resolveToolHome()
	if home == "" {
		return Result{}, fmt.Errorf("%w: REMEDY_HOME required for computer.print_window", core.ErrUnavailable)
	}
	info, err := core.PrintWindowPNG(home, label, *body.HWND)
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

func executeComputerForeground(_ context.Context, request Request) (Result, error) {
	if len(request.Input) > 0 {
		var body map[string]any
		if err := json.Unmarshal(request.Input, &body); err != nil {
			return Result{}, ErrInvalidInput
		}
		if len(body) != 0 {
			return Result{}, ErrInvalidInput
		}
	}
	raw, err := core.ForegroundDetailJSON()
	if err != nil {
		return Result{}, err
	}
	var detail struct {
		HWND  uint64 `json:"hwnd"`
		Title string `json:"title"`
		PID   uint32 `json:"pid"`
		Exe   string `json:"exe"`
	}
	if len(raw) == 0 {
		raw = []byte(`{}`)
	}
	if err := json.Unmarshal(raw, &detail); err != nil {
		return Result{}, fmt.Errorf("foreground_detail: invalid JSON: %w", err)
	}
	out, err := json.Marshal(map[string]any{
		"hwnd": detail.HWND, "title": detail.Title, "pid": detail.PID, "exe": detail.Exe,
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

func executeComputerSnapshot(_ context.Context, request Request) (Result, error) {
	var body struct {
		HWND          *uint64 `json:"hwnd"`
		MaxElements   int     `json:"max_elements"`
		PreferredOnly *bool   `json:"preferred_only"`
		Limit         int     `json:"limit"`
	}
	if len(request.Input) > 0 {
		if err := json.Unmarshal(request.Input, &body); err != nil {
			return Result{}, ErrInvalidInput
		}
	}

	switch runtime.GOOS {
	case "windows":
		hwnd := uint64(0)
		if body.HWND != nil {
			hwnd = *body.HWND
		}
		maxElements := uint32(0)
		if body.MaxElements > 0 {
			maxElements = uint32(body.MaxElements)
		}
		preferred := true
		if body.PreferredOnly != nil {
			preferred = *body.PreferredOnly
		}
		available, err := core.UIAAvailable()
		if err != nil {
			return Result{}, err
		}
		if !available {
			out, err := json.Marshal(map[string]any{
				"source": "uia", "available": false, "controls": []any{}, "total": 0,
			})
			return Result{Output: out}, err
		}
		raw, err := core.UIAControlSnapshotJSON(hwnd, maxElements, preferred)
		if err != nil {
			return Result{}, err
		}
		controls, err := decodeJSONArrayOrNull(raw)
		if err != nil {
			return Result{}, fmt.Errorf("uia_control_snapshot: invalid JSON: %w", err)
		}
		out, err := json.Marshal(map[string]any{
			"source": "uia", "available": true, "controls": controls, "total": len(controls),
		})
		return Result{Output: out}, err
	case "linux":
		limit := body.Limit
		if limit <= 0 {
			limit = 40
		}
		if limit > 200 {
			limit = 200
		}
		raw, err := core.A11ySnapshotJSON(uint32(limit))
		if err != nil {
			return Result{}, err
		}
		controls, err := decodeJSONArrayOrNull(raw)
		if err != nil {
			return Result{}, fmt.Errorf("a11y_snapshot: invalid JSON: %w", err)
		}
		out, err := json.Marshal(map[string]any{
			"source": "atspi", "available": true, "controls": controls, "total": len(controls),
		})
		return Result{Output: out}, err
	default:
		return Result{}, fmt.Errorf("%w: computer.snapshot", core.ErrUnsupported)
	}
}

func executeComputerUIAFocused(_ context.Context, request Request) (Result, error) {
	if len(request.Input) > 0 {
		var body map[string]any
		if err := json.Unmarshal(request.Input, &body); err != nil {
			return Result{}, ErrInvalidInput
		}
		if len(body) != 0 {
			return Result{}, ErrInvalidInput
		}
	}
	available, err := core.UIAAvailable()
	if err != nil {
		return Result{}, err
	}
	if !available {
		out, err := json.Marshal(map[string]any{"available": false, "element": nil})
		return Result{Output: out}, err
	}
	raw, err := core.UIAFocusedElementJSON()
	if err != nil {
		return Result{}, err
	}
	element, err := decodeJSONObjectOrNull(raw)
	if err != nil {
		return Result{}, fmt.Errorf("uia_focused_element: invalid JSON: %w", err)
	}
	out, err := json.Marshal(map[string]any{"available": true, "element": element})
	return Result{Output: out}, err
}

func executeComputerUIAReadText(_ context.Context, request Request) (Result, error) {
	var body struct {
		HWND     *uint64 `json:"hwnd"`
		MaxChars int     `json:"max_chars"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, ErrInvalidInput
	}
	if body.HWND == nil || *body.HWND == 0 {
		return Result{}, ErrInvalidInput
	}
	if body.MaxChars < 0 || body.MaxChars > 100000 {
		return Result{}, ErrInvalidInput
	}
	hwnd := *body.HWND
	available, err := core.UIAAvailable()
	if err != nil {
		return Result{}, err
	}
	if !available {
		out, err := json.Marshal(map[string]any{
			"available": false, "hwnd": hwnd, "payload": nil,
		})
		return Result{Output: out}, err
	}
	maxChars := uint32(0)
	if body.MaxChars > 0 {
		maxChars = uint32(body.MaxChars)
	}
	raw, err := core.UIAReadWindowTextJSON(hwnd, maxChars)
	if err != nil {
		return Result{}, err
	}
	payload, err := decodeJSONObjectOrNull(raw)
	if err != nil {
		return Result{}, fmt.Errorf("uia_read_window_text: invalid JSON: %w", err)
	}
	out, err := json.Marshal(map[string]any{
		"available": true, "hwnd": hwnd, "payload": payload,
	})
	return Result{Output: out}, err
}

func executeComputerUIAAction(_ context.Context, request Request) (Result, error) {
	var body struct {
		HWND   *uint64 `json:"hwnd"`
		Name   string  `json:"name"`
		Role   string  `json:"role"`
		Action string  `json:"action"`
		Text   string  `json:"text"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, ErrInvalidInput
	}
	if body.HWND == nil || *body.HWND == 0 {
		return Result{}, ErrInvalidInput
	}
	name := strings.TrimSpace(body.Name)
	if name == "" || len(name) > 512 {
		return Result{}, ErrInvalidInput
	}
	role := strings.TrimSpace(body.Role)
	if len(role) > 128 {
		return Result{}, ErrInvalidInput
	}
	action := strings.ToLower(strings.TrimSpace(body.Action))
	switch action {
	case "invoke", "set_value", "toggle", "scroll_into_view":
	default:
		return Result{}, ErrInvalidInput
	}
	if len(body.Text) > 8000 {
		return Result{}, ErrInvalidInput
	}
	hwnd := *body.HWND
	raw, err := core.UIAElementActionJSON(hwnd, name, role, action, body.Text)
	if err != nil {
		return Result{}, err
	}
	var zigOut struct {
		OK       bool   `json:"ok"`
		Message  string `json:"message"`
		Verified *bool  `json:"verified"`
	}
	if err := json.Unmarshal(raw, &zigOut); err != nil {
		return Result{}, fmt.Errorf("uia_element_action: invalid JSON: %w", err)
	}
	payload := map[string]any{
		"ok": zigOut.OK, "message": zigOut.Message,
		"hwnd": hwnd, "name": name, "role": role, "action": action,
	}
	if zigOut.Verified != nil {
		payload["verified"] = *zigOut.Verified
	}
	out, err := json.Marshal(payload)
	return Result{Output: out}, err
}

func executeComputerClick(_ context.Context, request Request) (Result, error) {
	var body struct {
		X      *int   `json:"x"`
		Y      *int   `json:"y"`
		Button string `json:"button"`
		Clicks int    `json:"clicks"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, ErrInvalidInput
	}
	if body.X == nil || body.Y == nil {
		return Result{}, ErrInvalidInput
	}
	buttonName := strings.ToLower(strings.TrimSpace(body.Button))
	if buttonName == "" {
		buttonName = "left"
	}
	var button uint32
	switch buttonName {
	case "left":
		button = core.MouseLeft
	case "right":
		button = core.MouseRight
	case "middle":
		button = core.MouseMiddle
	default:
		return Result{}, ErrInvalidInput
	}
	clicks := body.Clicks
	if clicks <= 0 {
		clicks = 1
	}
	if clicks > 3 {
		return Result{}, ErrInvalidInput
	}
	if err := core.MouseClick(int32(*body.X), int32(*body.Y), button, uint32(clicks)); err != nil {
		return Result{}, err
	}
	out, err := json.Marshal(map[string]any{
		"ok": true, "x": *body.X, "y": *body.Y, "button": buttonName, "clicks": clicks,
	})
	return Result{Output: out}, err
}

func executeComputerType(_ context.Context, request Request) (Result, error) {
	var body struct {
		Text           string `json:"text"`
		PerCharDelayMS *int   `json:"per_char_delay_ms"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, ErrInvalidInput
	}
	if body.Text == "" {
		return Result{}, ErrInvalidInput
	}
	if len(body.Text) > 8000 {
		return Result{}, ErrInvalidInput
	}
	delay := uint32(5)
	if body.PerCharDelayMS != nil {
		if *body.PerCharDelayMS < 0 || *body.PerCharDelayMS > 200 {
			return Result{}, ErrInvalidInput
		}
		delay = uint32(*body.PerCharDelayMS)
	}
	if err := core.TypeText(body.Text, delay); err != nil {
		return Result{}, err
	}
	out, err := json.Marshal(map[string]any{
		"ok": true, "chars": len([]rune(body.Text)),
	})
	return Result{Output: out}, err
}

func executeComputerKey(_ context.Context, request Request) (Result, error) {
	var body struct {
		Key string `json:"key"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, ErrInvalidInput
	}
	key := strings.TrimSpace(body.Key)
	if key == "" || len(key) > 64 {
		return Result{}, ErrInvalidInput
	}
	vks, err := resolveKeyCombo(key)
	if err != nil {
		if errors.Is(err, core.ErrUnavailable) || errors.Is(err, core.ErrUnsupported) {
			return Result{}, err
		}
		var hostErr *core.HostError
		if errors.As(err, &hostErr) {
			return Result{}, err
		}
		return Result{}, fmt.Errorf("%w: %v", ErrInvalidInput, err)
	}
	if err := core.KeyCombo(vks); err != nil {
		return Result{}, err
	}
	vkOut := make([]int, len(vks))
	for i, vk := range vks {
		vkOut[i] = int(vk)
	}
	out, err := json.Marshal(map[string]any{
		"ok": true, "key": key, "vks": vkOut,
	})
	return Result{Output: out}, err
}

func executeComputerKeyHold(_ context.Context, request Request) (Result, error) {
	var body struct {
		Key    string `json:"key"`
		HoldMS *int   `json:"hold_ms"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, ErrInvalidInput
	}
	key := strings.TrimSpace(body.Key)
	if key == "" || len(key) > 64 {
		return Result{}, ErrInvalidInput
	}
	if body.HoldMS == nil || *body.HoldMS < 0 || *body.HoldMS > 60000 {
		return Result{}, ErrInvalidInput
	}
	vks, err := resolveKeyCombo(key)
	if err != nil {
		if errors.Is(err, core.ErrUnavailable) || errors.Is(err, core.ErrUnsupported) {
			return Result{}, err
		}
		var hostErr *core.HostError
		if errors.As(err, &hostErr) {
			return Result{}, err
		}
		return Result{}, fmt.Errorf("%w: %v", ErrInvalidInput, err)
	}
	if len(vks) != 1 {
		return Result{}, fmt.Errorf("%w: key_hold requires a single key, not a combo", ErrInvalidInput)
	}
	holdMS := uint32(*body.HoldMS)
	if err := core.KeyHold(vks[0], holdMS); err != nil {
		return Result{}, err
	}
	out, err := json.Marshal(map[string]any{
		"ok": true, "key": key, "vk": int(vks[0]), "hold_ms": *body.HoldMS,
	})
	return Result{Output: out}, err
}

func executeComputerMove(_ context.Context, request Request) (Result, error) {
	var body struct {
		X *int `json:"x"`
		Y *int `json:"y"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, ErrInvalidInput
	}
	if body.X == nil || body.Y == nil {
		return Result{}, ErrInvalidInput
	}
	if err := core.MouseMove(int32(*body.X), int32(*body.Y)); err != nil {
		return Result{}, err
	}
	out, err := json.Marshal(map[string]any{"ok": true, "x": *body.X, "y": *body.Y})
	return Result{Output: out}, err
}

func executeComputerScroll(_ context.Context, request Request) (Result, error) {
	var body struct {
		X  *int `json:"x"`
		Y  *int `json:"y"`
		Dx int  `json:"dx"`
		Dy int  `json:"dy"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, ErrInvalidInput
	}
	if body.X == nil || body.Y == nil {
		return Result{}, ErrInvalidInput
	}
	if err := core.MouseScroll(int32(*body.X), int32(*body.Y), int32(body.Dx), int32(body.Dy)); err != nil {
		return Result{}, err
	}
	out, err := json.Marshal(map[string]any{
		"ok": true, "x": *body.X, "y": *body.Y, "dx": body.Dx, "dy": body.Dy,
	})
	return Result{Output: out}, err
}

func executeComputerDrag(_ context.Context, request Request) (Result, error) {
	var body struct {
		X1    *int `json:"x1"`
		Y1    *int `json:"y1"`
		X2    *int `json:"x2"`
		Y2    *int `json:"y2"`
		Steps int  `json:"steps"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, ErrInvalidInput
	}
	if body.X1 == nil || body.Y1 == nil || body.X2 == nil || body.Y2 == nil {
		return Result{}, ErrInvalidInput
	}
	steps := body.Steps
	if steps <= 0 {
		steps = 12
	}
	if steps > 200 {
		return Result{}, ErrInvalidInput
	}
	if err := core.MouseDrag(
		int32(*body.X1), int32(*body.Y1), int32(*body.X2), int32(*body.Y2), uint32(steps),
	); err != nil {
		return Result{}, err
	}
	out, err := json.Marshal(map[string]any{
		"ok": true,
		"x1": *body.X1, "y1": *body.Y1, "x2": *body.X2, "y2": *body.Y2,
		"steps": steps,
	})
	return Result{Output: out}, err
}

func executeComputerFocus(_ context.Context, request Request) (Result, error) {
	var body struct {
		HWND *uint64 `json:"hwnd"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, ErrInvalidInput
	}
	if body.HWND == nil || *body.HWND == 0 {
		return Result{}, ErrInvalidInput
	}
	hwnd := *body.HWND
	focused, err := core.FocusWindow(hwnd)
	if err != nil {
		return Result{}, err
	}
	out, err := json.Marshal(map[string]any{
		"ok": focused, "hwnd": hwnd, "focused": focused,
	})
	return Result{Output: out}, err
}

func executeComputerWindow(_ context.Context, request Request) (Result, error) {
	var body struct {
		HWND   *uint64 `json:"hwnd"`
		Action string  `json:"action"`
		X      *int    `json:"x"`
		Y      *int    `json:"y"`
		Width  *int    `json:"width"`
		Height *int    `json:"height"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, ErrInvalidInput
	}
	if body.HWND == nil || *body.HWND == 0 {
		return Result{}, ErrInvalidInput
	}
	action := strings.ToLower(strings.TrimSpace(body.Action))
	hwnd := *body.HWND
	var verb uint32
	switch action {
	case "minimize":
		verb = core.WindowMinimize
	case "maximize":
		verb = core.WindowMaximize
	case "restore":
		verb = core.WindowRestore
	case "close":
		verb = core.WindowClose
	case "move", "resize":
		verb = core.WindowMoveResize
	default:
		return Result{}, ErrInvalidInput
	}

	var x, y, width, height int32
	if action == "move" || action == "resize" {
		if action == "move" && (body.X == nil || body.Y == nil) {
			return Result{}, fmt.Errorf("%w: move requires x and y", ErrInvalidInput)
		}
		if action == "resize" && (body.Width == nil || body.Height == nil) {
			return Result{}, fmt.Errorf("%w: resize requires width and height", ErrInvalidInput)
		}
		needRect := body.X == nil || body.Y == nil || body.Width == nil || body.Height == nil
		if needRect {
			left, top, right, bottom, err := core.WindowRect(hwnd)
			if err != nil {
				return Result{}, err
			}
			x, y = left, top
			width = right - left
			if width < 0 {
				width = 0
			}
			height = bottom - top
			if height < 0 {
				height = 0
			}
		}
		if body.X != nil {
			x = int32(*body.X)
		}
		if body.Y != nil {
			y = int32(*body.Y)
		}
		if body.Width != nil {
			if *body.Width < 0 {
				return Result{}, ErrInvalidInput
			}
			width = int32(*body.Width)
		}
		if body.Height != nil {
			if *body.Height < 0 {
				return Result{}, ErrInvalidInput
			}
			height = int32(*body.Height)
		}
	}

	if err := core.ManageWindow(hwnd, verb, x, y, width, height); err != nil {
		return Result{}, err
	}
	payload := map[string]any{"ok": true, "hwnd": hwnd, "action": action}
	if action == "move" || action == "resize" {
		payload["x"] = int(x)
		payload["y"] = int(y)
		payload["width"] = int(width)
		payload["height"] = int(height)
	}
	out, err := json.Marshal(payload)
	return Result{Output: out}, err
}

func executeClipboardRead(_ context.Context, request Request) (Result, error) {
	if len(request.Input) > 0 {
		var body map[string]any
		if err := json.Unmarshal(request.Input, &body); err != nil {
			return Result{}, ErrInvalidInput
		}
		if len(body) != 0 {
			return Result{}, ErrInvalidInput
		}
	}
	text, err := core.ClipboardGetText()
	if err != nil {
		return Result{}, err
	}
	out, err := json.Marshal(map[string]any{
		"text": text, "chars": len([]rune(text)),
	})
	return Result{Output: out}, err
}

func executeClipboardReadFiles(_ context.Context, request Request) (Result, error) {
	if len(request.Input) > 0 {
		var body map[string]any
		if err := json.Unmarshal(request.Input, &body); err != nil {
			return Result{}, ErrInvalidInput
		}
		if len(body) != 0 {
			return Result{}, ErrInvalidInput
		}
	}
	files, err := core.ClipboardGetFiles()
	if err != nil {
		return Result{}, err
	}
	if files == nil {
		files = []string{}
	}
	out, err := json.Marshal(map[string]any{
		"files": files, "count": len(files),
	})
	return Result{Output: out}, err
}

func executeClipboardReadImage(_ context.Context, request Request) (Result, error) {
	label := "clipboard"
	if len(request.Input) > 0 {
		var body map[string]any
		if err := json.Unmarshal(request.Input, &body); err != nil {
			return Result{}, ErrInvalidInput
		}
		for key, value := range body {
			if key != "label" {
				return Result{}, ErrInvalidInput
			}
			s, ok := value.(string)
			if !ok {
				return Result{}, ErrInvalidInput
			}
			if trimmed := strings.TrimSpace(s); trimmed != "" {
				label = trimmed
			}
		}
	}
	png, err := core.ClipboardGetImagePNG()
	if err != nil {
		return Result{}, err
	}
	if len(png) == 0 {
		out, err := json.Marshal(map[string]any{
			"available": false, "path": "", "bytes": 0,
		})
		return Result{Output: out}, err
	}
	home := resolveToolHome()
	if home == "" {
		return Result{}, fmt.Errorf("%w: REMEDY_HOME required for clipboard.read_image", core.ErrUnavailable)
	}
	path, err := core.WriteClipboardPNG(home, label, png)
	if err != nil {
		return Result{}, err
	}
	out, err := json.Marshal(map[string]any{
		"available": true, "path": path, "bytes": len(png),
	})
	return Result{Output: out}, err
}

func executeClipboardWrite(_ context.Context, request Request) (Result, error) {
	var body struct {
		Text *string `json:"text"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, ErrInvalidInput
	}
	if body.Text == nil {
		return Result{}, ErrInvalidInput
	}
	if len(*body.Text) > 1_000_000 {
		return Result{}, ErrInvalidInput
	}
	if err := core.ClipboardSetText(*body.Text); err != nil {
		return Result{}, err
	}
	out, err := json.Marshal(map[string]any{
		"ok": true, "chars": len([]rune(*body.Text)),
	})
	return Result{Output: out}, err
}

func executeShellExec(_ context.Context, request Request) (Result, error) {
	var body struct {
		Argv           []string          `json:"argv"`
		Cwd            string            `json:"cwd"`
		Env            map[string]string `json:"env"`
		TimeoutMS      uint32            `json:"timeout_ms"`
		OwnerConfirmed bool              `json:"owner_confirmed"`
		WriteRoots     []string          `json:"write_roots"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, ErrInvalidInput
	}
	if len(body.Argv) == 0 {
		return Result{}, ErrInvalidInput
	}
	for _, arg := range body.Argv {
		if arg == "" {
			return Result{}, ErrInvalidInput
		}
	}
	if !filepath.IsAbs(body.Argv[0]) {
		return Result{}, fmt.Errorf("%w: argv[0] must be absolute", ErrInvalidInput)
	}
	if body.Cwd != "" && !filepath.IsAbs(body.Cwd) {
		return Result{}, fmt.Errorf("%w: cwd must be absolute when set", ErrInvalidInput)
	}
	// Omitted/0 used to become Zig's 60s default — too short for cargo/pytest/npm.
	// Build-scale default: 10 minutes (schema max is 600000).
	if body.TimeoutMS == 0 {
		body.TimeoutMS = 600_000
	}
	// Model-supplied owner_confirmed is not proof — only the approval queue
	// (or an explicit runtime capability) may set the Zig owner bit.
	body.OwnerConfirmed = false

	home := resolveToolHome()
	key, err := secret.EnsureHostSigningKey(home)
	if err != nil {
		return Result{}, fmt.Errorf("shell.exec signing key: %w", err)
	}
	if err := core.EnsureSigningKey(key); err != nil {
		return Result{}, err
	}
	// write_roots from the session binder (project/home). Empty = Full for
	// partner life tasks; Zig still refuses auth paths.
	roots := make([]string, 0, len(body.WriteRoots))
	for _, r := range body.WriteRoots {
		r = strings.TrimSpace(r)
		if r != "" && filepath.IsAbs(r) {
			roots = append(roots, r)
		}
	}
	_ = core.WriteJailSetRoots(roots)

	token, nowMS, err := core.IssueProcessSpawnToken(body.Argv, body.OwnerConfirmed)
	if err != nil {
		return Result{}, err
	}
	res, err := core.ExecCaptureAuthorized(
		body.Argv,
		body.Cwd,
		body.Env,
		token,
		"",
		"",
		body.OwnerConfirmed,
		nowMS,
		body.TimeoutMS,
	)
	if err != nil {
		return Result{}, err
	}
	out, err := json.Marshal(map[string]any{
		"exit_code": res.ExitCode,
		"timed_out": res.TimedOut,
		"stdout":    string(res.Stdout),
		"stderr":    string(res.Stderr),
	})
	return Result{Output: out}, err
}

func decodeJSONArrayOrNull(raw []byte) ([]any, error) {
	trimmed := strings.TrimSpace(string(raw))
	if trimmed == "" || trimmed == "null" {
		return []any{}, nil
	}
	var controls []any
	if err := json.Unmarshal(raw, &controls); err != nil {
		return nil, err
	}
	if controls == nil {
		controls = []any{}
	}
	return controls, nil
}

func decodeJSONObjectOrNull(raw []byte) (any, error) {
	trimmed := strings.TrimSpace(string(raw))
	if trimmed == "" || trimmed == "null" {
		return nil, nil
	}
	var obj map[string]any
	if err := json.Unmarshal(raw, &obj); err != nil {
		return nil, err
	}
	return obj, nil
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

// Named VKs matching Python desktop_policy.VK (plus Arrow* aliases).
var namedVirtualKeys = map[string]uint16{
	"enter": 0x0D, "return": 0x0D,
	"tab": 0x09,
	"escape": 0x1B, "esc": 0x1B,
	"backspace": 0x08,
	"delete": 0x2E, "del": 0x2E,
	"space": 0x20,
	"up": 0x26, "arrowup": 0x26,
	"down": 0x28, "arrowdown": 0x28,
	"left": 0x25, "arrowleft": 0x25,
	"right": 0x27, "arrowright": 0x27,
	"home": 0x24, "end": 0x23,
	"pageup": 0x21, "pagedown": 0x22,
	"f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
	"f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
	"f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
	"insert": 0x2D, "ins": 0x2D,
	"printscreen": 0x2C, "prtsc": 0x2C, "prtscn": 0x2C,
	"ctrl": 0x11, "control": 0x11,
	"alt": 0x12,
	"shift": 0x10,
	"win": 0x5B, "meta": 0x5B, "cmd": 0x5B, "super": 0x5B,
}

var modifierVirtualKeys = map[uint16]struct{}{
	0x10: {}, 0x11: {}, 0x12: {}, 0x5B: {},
}

// resolveKeyCombo maps "ctrl+s" / "enter" / "?" to ordered VKs (modifiers first).
// Matches Python desktop_policy.resolve_key_combo.
func resolveKeyCombo(key string) ([]uint16, error) {
	normalized := strings.ReplaceAll(strings.TrimSpace(key), "-", "+")
	rawParts := strings.Split(normalized, "+")
	parts := make([]string, 0, len(rawParts))
	for _, p := range rawParts {
		p = strings.ToLower(strings.TrimSpace(p))
		if p != "" {
			parts = append(parts, p)
		}
	}
	if len(parts) == 0 {
		return nil, fmt.Errorf("empty key")
	}
	mods := make([]uint16, 0, 4)
	mains := make([]uint16, 0, 4)
	hasMod := func(vk uint16) bool {
		for _, m := range mods {
			if m == vk {
				return true
			}
		}
		return false
	}
	for _, p := range parts {
		if vk, ok := namedVirtualKeys[p]; ok {
			if _, isMod := modifierVirtualKeys[vk]; isMod {
				mods = append(mods, vk)
			} else {
				mains = append(mains, vk)
			}
			continue
		}
		runes := []rune(p)
		if len(runes) != 1 {
			return nil, fmt.Errorf("unknown key: %q", p)
		}
		ch := runes[0]
		// ASCII letters/digits map to stable Win32 VKs without layout scan.
		if ch >= 'a' && ch <= 'z' {
			mains = append(mains, uint16(ch-'a'+0x41))
			continue
		}
		if ch >= 'A' && ch <= 'Z' {
			if !hasMod(0x10) {
				mods = append(mods, 0x10)
			}
			mains = append(mains, uint16(ch-'A'+0x41))
			continue
		}
		if ch >= '0' && ch <= '9' {
			mains = append(mains, uint16(ch))
			continue
		}
		scan, err := core.VkKeyScan(uint32(ch))
		if err != nil {
			return nil, err
		}
		if scan == -1 {
			return nil, fmt.Errorf("key has no VK mapping on this layout: %q", p)
		}
		shiftState := (scan >> 8) & 0xFF
		if shiftState&1 != 0 && !hasMod(0x10) {
			mods = append(mods, 0x10)
		}
		if shiftState&2 != 0 && !hasMod(0x11) {
			mods = append(mods, 0x11)
		}
		if shiftState&4 != 0 && !hasMod(0x12) {
			mods = append(mods, 0x12)
		}
		mains = append(mains, uint16(scan&0xFF))
	}
	return append(mods, mains...), nil
}
