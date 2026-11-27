package cli

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/signal"
	"runtime"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
	"github.com/AhmiDarrow/RemedyAI/native/go/httpapi"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// sysComputerActions is host_binding.native(): the real OS actions. Tests
// replace sysNewComputerActions so nothing acts on the machine.
type sysComputerActions struct{}

var sysNewComputerActions = func() httpapi.ComputerActions { return sysComputerActions{} }

// sysOpenerArgv is the system browser opener for each OS (open_url in
// host_binding: os.startfile on Windows, xdg-open on Linux, open on macOS).
func sysOpenerArgv(goos, url string) []string {
	switch goos {
	case "windows":
		return []string{"explorer.exe", url}
	case "darwin":
		return []string{"open", url}
	default:
		return []string{"xdg-open", url}
	}
}

func (sysComputerActions) OpenURL(url string) (map[string]any, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	code, _, err := tools.RunAttached(ctx, sysOpenerArgv(runtime.GOOS, url), "", nil, nil, io.Discard, io.Discard, 15*time.Second)
	if err != nil {
		return nil, err
	}
	// explorer.exe exits 1 even when it opened the URL.
	if code != 0 && runtime.GOOS != "windows" {
		return nil, fmt.Errorf("browser opener exited %d", code)
	}
	return map[string]any{"opened": true}, nil
}

func (sysComputerActions) Screenshot(home string) (map[string]any, error) {
	return core.ScreenshotPNG(home, "cli_host")
}

func (sysComputerActions) DesktopSnapshot(limit int, mode string) ([]map[string]any, error) {
	if limit <= 0 {
		limit = 40
	}
	var raw []byte
	var err error
	if runtime.GOOS == "windows" {
		ok, aerr := core.UIAAvailable()
		if aerr != nil {
			return nil, aerr
		}
		if !ok {
			return nil, errors.New("UI Automation unavailable")
		}
		raw, err = core.UIAControlSnapshotJSON(0, uint32(limit), mode != "all")
	} else {
		raw, err = core.A11ySnapshotJSON(uint32(limit))
	}
	if err != nil {
		return nil, err
	}
	var els []map[string]any
	if len(raw) > 0 && string(raw) != "null" {
		if err := json.Unmarshal(raw, &els); err != nil {
			return nil, err
		}
	}
	for i, el := range els {
		if _, ok := el["ref"]; !ok {
			el["ref"] = fmt.Sprintf("d%d", i+1)
		}
	}
	return els, nil
}

func (sysComputerActions) Click(x, y int, button string, clicks int) error {
	b := core.MouseLeft
	switch strings.ToLower(button) {
	case "right":
		b = core.MouseRight
	case "middle":
		b = core.MouseMiddle
	}
	return core.MouseClick(int32(x), int32(y), b, uint32(clicks))
}

func (sysComputerActions) TypeText(text string) error { return core.TypeText(text, 0) }

func (sysComputerActions) PressKey(key string) error { return tools.PressKeyCombo(key) }

// sysComputerHost is the process-wide CLI host (get_local_computer_host).
var sysComputerHost *httpapi.CLIComputerHost

func sysLocalComputerHost(home string) *httpapi.CLIComputerHost {
	if sysComputerHost == nil {
		sysComputerHost = httpapi.NewCLIComputerHost(home, sysNewComputerActions())
	}
	return sysComputerHost
}

func sysStopComputerHost() {
	if sysComputerHost != nil {
		sysComputerHost.Stop(3*time.Second, true)
	}
}

// sysWaitInterrupt blocks until Ctrl+C or the host ends; replaceable in tests.
var sysWaitInterrupt = func(h *httpapi.CLIComputerHost) {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	h.Wait(ctx)
}

func cmdComputer(c *Ctx) int {
	sub := c.P.Sub(1)
	if sub == "" {
		sub = "status"
	}
	switch sub {
	case "status":
		host := sysLocalComputerHost(c.Home)
		st := host.Status()
		c.Println("Computer use")
		c.Printf("  host_connected:  %s\n", sysPyBool(host.HostConnected()))
		c.Printf("  pending_jobs:    %d\n", host.PendingJobs())
		state := "stopped"
		if host.Running() {
			state = "running"
		}
		c.Printf("  CLI host:        %s\n", state)
		c.Printf("  jobs_completed:  %v\n", st["jobs_completed"])
		if s := asString(st["last_action"]); s != "" {
			c.Printf("  last_action:     %s\n", s)
		}
		if s := asString(st["last_error"]); s != "" {
			c.Printf("  last_error:      %s\n", s)
		}
		c.Printf("  home:            %v\n", st["home"])
		c.Println("\nDesktop tools (app/windows/click) work in-process.\n" +
			"Browser rail needs Remedy Desktop, or start: remedy computer host start\n" +
			"Chat starts the CLI host only with --computer-host (Desktop owns the rail).")
		return 0
	case "host":
		action := strings.ToLower(strings.TrimSpace(c.P.Str("action")))
		if action == "" {
			action = "start"
		}
		if action == "stop" {
			sysStopComputerHost()
			c.Println("CLI computer host stopped.")
			return 0
		}
		host := sysLocalComputerHost(c.Home)
		host.StartCLI()
		if host.LastError() == "desktop_owns_host" {
			c.Println("Desktop already owns jobs/next; CLI host not started.")
			return 0
		}
		c.Printf("CLI computer host started. host_connected=%s\n", sysPyBool(host.HostConnected()))
		if action == "run" {
			c.Println("Foreground host running — Ctrl+C to stop")
			sysWaitInterrupt(host)
			c.Println("\nStopping…")
			sysStopComputerHost()
		}
		return 0
	}
	c.Println("Usage: remedy computer status|host [start|stop|run]")
	return 0
}

func sysPyBool(b bool) string {
	if b {
		return "True"
	}
	return "False"
}
