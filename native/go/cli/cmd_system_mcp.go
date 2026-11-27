package cli

import (
	"bytes"
	"context"
	"errors"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/httpapi"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// `remedy mcp serve [--http --host --port]`: tools/mcp_server.py. stdio
// speaks newline-delimited JSON-RPC; --http serves the runtime's own /mcp and
// /a2a routes (the same dispatch remedy serve mounts) on a port other than
// 7400.

func cmdMCP(c *Ctx) int {
	if c.P.Sub(1) != "serve" {
		c.Errorf("Usage: remedy mcp serve [--http]\n")
		return 2
	}
	version := Version(httpapi.Version)
	if c.P.Bool("http") {
		host := strings.TrimSpace(c.P.Str("host"))
		if host == "" {
			host = "127.0.0.1"
		}
		port := c.P.Int("port")
		if port == 0 {
			port = 7401
		}
		ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
		defer stop()
		err := httpapi.ServeInteropHTTP(ctx, c.Home, version, host, port, func(addr string) {
			c.Errorf("interop-http listening on http://%s (MCP /mcp, A2A /a2a)\n", addr)
		})
		if err != nil {
			c.Errorf("%v\n", err)
			return 1
		}
		return 0
	}
	h := httpapi.NewMCPHost(c.Home, version)
	h.RunScript = sysRunSkillScript
	in := c.In
	if in == nil {
		in = strings.NewReader("")
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	if err := h.ServeStdio(ctx, in, c.Out); err != nil && !errors.Is(err, context.Canceled) {
		c.Errorf("%v\n", err)
		return 1
	}
	return 0
}

// sysSkillPython is core.build_python.python_cmd_for_subprocess: REMEDY_PYTHON,
// the launcher's interpreter, then python3 / python on PATH (never the
// Windows Store alias stub).
var sysSkillPython = func() string {
	for _, k := range []string{"REMEDY_PYTHON", "REMEDY_CLI_PYTHON"} {
		if v := strings.TrimSpace(os.Getenv(k)); v != "" {
			return v
		}
	}
	for _, name := range []string{"python3", "python"} {
		p, err := tools.ResolveArgv0(name, "")
		if err != nil {
			continue
		}
		if strings.Contains(strings.ToLower(filepath.ToSlash(p)), "/windowsapps/") {
			continue
		}
		return p
	}
	return ""
}

// sysScrubbedEnv blanks REMEDY_* and credential-looking variables for a
// skill script (execution.env.scrub_subprocess_env): the spawn API overlays
// the parent's environment, so they are set empty rather than removed.
func sysScrubbedEnv() map[string]string {
	env := map[string]string{}
	for _, kv := range os.Environ() {
		k, _, _ := strings.Cut(kv, "=")
		up := strings.ToUpper(k)
		if strings.HasPrefix(up, "REMEDY_") || strings.Contains(up, "API_KEY") ||
			strings.Contains(up, "TOKEN") || strings.Contains(up, "SECRET") || strings.Contains(up, "PASSWORD") {
			env[k] = ""
		}
	}
	return env
}

// sysRunSkillScript is SkillExecutor.run_script: python <script> args...,
// list form, cwd at the script's folder, 120 s timeout.
var sysRunSkillScript = func(ctx context.Context, script string, args []string) (string, int, string) {
	py := sysSkillPython()
	if py == "" {
		return "", 0, "No real Python interpreter for skill scripts (Desktop sidecar is not CPython). Set REMEDY_PYTHON."
	}
	var out bytes.Buffer
	argv := append([]string{py, script}, args...)
	code, timedOut, err := tools.RunAttached(ctx, argv, filepath.Dir(script), sysScrubbedEnv(), nil, &out, &out, 120*time.Second)
	if timedOut {
		return out.String(), code, "Script timed out after 120.0s"
	}
	if err != nil {
		if errors.Is(err, tools.ErrSpawnRefused) {
			return out.String(), code, "Blocked by security policy: " + err.Error()
		}
		return out.String(), code, err.Error()
	}
	return out.String(), code, ""
}
