package cli

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"sort"
	"time"
)

func toolUnreachable(c *Ctx, err error) int {
	c.Errorf("remedy tool needs Go remedy-runtime.\n" +
		"  Start `remedy serve` (or Desktop), then retry.\n" +
		"  First serve downloads a checksum-verified runtime from GitHub Releases when it is not already on PATH.\n")
	c.Errorf("  (%s unreachable: %v)\n", runtimeBase(), err)
	c.Errorf("  Tool ABI ids look like runtime.probe, workspace.read, shell.exec.\n")
	return 2
}

func cmdTool(c *Ctx) int {
	timeout := time.Duration(c.P.Float("timeout") * float64(time.Second))
	if timeout <= 0 {
		timeout = 30 * time.Second
	}
	call := func(method, path string, body any) (int, any, bool) {
		code, payload, err := runtimeRequest(method, path, c.Home, body, timeout)
		if errors.Is(err, errUnreachable) {
			toolUnreachable(c, err)
			return 0, nil, false
		}
		if err != nil {
			c.Errorf("Tool request failed: %v\n", err)
			return 0, nil, false
		}
		return code, payload, true
	}
	tools := func(payload any) []map[string]any {
		var out []map[string]any
		if m, ok := payload.(map[string]any); ok {
			if list, ok := m["tools"].([]any); ok {
				for _, t := range list {
					if tm, ok := t.(map[string]any); ok {
						out = append(out, tm)
					}
				}
			}
		}
		return out
	}

	switch c.P.Sub(1) {
	case "list":
		code, payload, ok := call("GET", "/api/tools", nil)
		if !ok {
			return 2
		}
		if _, isMap := payload.(map[string]any); code != 200 || !isMap {
			c.Errorf("Tool list failed (%d): %v\n", code, detailOf(payload))
			return 1
		}
		t := newTable("Tool ABI (remedy-runtime)", "Runtime", "Id", "Risk", "Description")
		for _, tm := range tools(payload) {
			t.add(asString(tm["runtime"]), asString(tm["id"]), asString(tm["risk"]), clip(asString(tm["description"]), 60))
		}
		t.print(c.Out)
	case "search":
		q := url.QueryEscape(c.P.Str("query"))
		code, payload, ok := call("GET", "/api/tools?q="+q, nil)
		if !ok {
			return 2
		}
		if _, isMap := payload.(map[string]any); code != 200 || !isMap {
			c.Errorf("Tool search failed (%d): %v\n", code, detailOf(payload))
			return 1
		}
		found := tools(payload)
		if len(found) == 0 {
			c.Printf("No tools matching '%s'\n", c.P.Str("query"))
			return 0
		}
		for _, tm := range found {
			c.Printf("[%s] %s: %s\n", asString(tm["runtime"]), asString(tm["id"]), asString(tm["description"]))
		}
	case "stats":
		code, payload, ok := call("GET", "/api/tools", nil)
		if !ok {
			return 2
		}
		m, isMap := payload.(map[string]any)
		if code != 200 || !isMap {
			c.Errorf("Tool stats failed (%d): %v\n", code, detailOf(payload))
			return 1
		}
		count, _ := m["count"].(float64)
		byRuntime := map[string]int{}
		for _, tm := range tools(payload) {
			rt := asString(tm["runtime"])
			if rt == "" {
				rt = "unknown"
			}
			byRuntime[rt]++
		}
		keys := make([]string, 0, len(byRuntime))
		for k := range byRuntime {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		c.Println("Tool Stats")
		c.Printf("Registered (Tool ABI): %d\n", int(count))
		c.Printf("By runtime:")
		for _, k := range keys {
			c.Printf(" %s=%d", k, byRuntime[k])
		}
		c.Println()
		c.Println("Invocation counters live on Go turn metrics, not this CLI.")
	case "run":
		var input any
		if err := json.Unmarshal([]byte(c.P.Str("args")), &input); err != nil {
			c.Errorf("Invalid --args JSON: %v\n", err)
			return 2
		}
		if _, ok := input.(map[string]any); !ok {
			c.Errorf("--args must be a JSON object\n")
			return 2
		}
		name := c.P.Str("name")
		c.Printf("Running Tool ABI: %s\n", name)
		// --retries is accepted for old scripts and ignored, as it always was:
		// a failed mutation tool must not run twice behind the owner's back.
		code, payload, ok := call("POST", "/api/tools/invoke", map[string]any{"id": name, "input": input})
		if !ok {
			return 2
		}
		if m, isMap := payload.(map[string]any); code == 200 && isMap && m["ok"] == true {
			c.Println("Success")
			if out, has := m["output"]; has && out != nil {
				if s, isStr := out.(string); isStr {
					c.Println(s)
				} else {
					raw, _ := json.MarshalIndent(out, "", "  ")
					c.Println(string(raw))
				}
			}
			return 0
		}
		c.Errorf("Failed (%d): %v\n", code, fmt.Sprint(detailOf(payload)))
		return 1
	}
	return 0
}
