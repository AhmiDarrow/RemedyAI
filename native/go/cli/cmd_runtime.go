package cli

import (
	"context"
	"errors"
	"fmt"
	"net"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
	"github.com/AhmiDarrow/RemedyAI/native/go/httpapi"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

var loopbackHosts = map[string]bool{"127.0.0.1": true, "localhost": true, "::1": true, "[::1]": true}

func isLoopbackHost(h string) bool { return loopbackHosts[strings.ToLower(strings.TrimSpace(h))] }

// serveAddr maps --host/--port onto the runtime's listen address.
func serveAddr(host string, port int) string {
	host = strings.TrimSpace(host)
	if host == "" {
		host = "127.0.0.1"
	}
	if port == 0 {
		port = 7400
	}
	h := strings.Trim(host, "[]")
	return net.JoinHostPort(h, strconv.Itoa(port))
}

func cmdServe(c *Ctx) int {
	host := c.P.Str("host")
	if host == "" {
		host = "127.0.0.1"
	}
	if c.P.Str("config") != "" {
		c.Printf("--config is ignored. remedy-runtime reads config from $REMEDY_HOME or ~/.remedy, not a --config path.\n")
	}
	if !isLoopbackHost(host) {
		c.Errorf("Refusing non-loopback serve bind.\n  host=%q — remedy-runtime only listens on 127.0.0.1 / ::1.\n  Use --host 127.0.0.1 (default).\n", host)
		return 2
	}
	if c.Hooks.Serve == nil {
		c.Errorf("remedy serve: this build has no local API to start.\n")
		return 2
	}
	addr := serveAddr(host, c.P.Int("port"))
	c.Printf("Starting remedy-runtime on http://%s  Open http://%s in a browser, or the Desktop app.\n", addr, addr)
	return c.Hooks.Serve(addr, c.Home, func(bound string) {
		if !httpapi.AuthEnabled() || !httpapi.HTTPBootstrapEnabled(c.Home) {
			return
		}
		go func() {
			if link := waitForLink("http://"+bound, c.Home, 60*time.Second); link != "" {
				c.Printf("WebUI: %s\nOne-time link (10 min). Run `remedy open` for a new one.\n", link)
			}
		}()
	})
}

func cmdOpen(c *Ctx) int {
	host := c.P.Str("host")
	if !isLoopbackHost(host) {
		c.Errorf("remedy open only talks to a loopback remedy-runtime.\n")
		return 2
	}
	port := c.P.Int("port")
	base := "http://" + serveAddr(host, port)
	link := waitForLink(base, c.Home, 5*time.Second)
	if link == "" {
		c.Errorf("remedy open: no remedy-runtime answered on %s:%d with this account's API token. Start it with `remedy serve`.\n", host, port)
		return 1
	}
	c.Printf("WebUI: %s\nOne-time link (10 min).\n", link)
	if !c.P.Bool("no-browser") {
		if err := openInBrowser(link); err != nil {
			c.Errorf("Could not open a browser (%v); open the link above yourself.\n", err)
		}
	}
	return 0
}

// mintLink asks a running runtime for a one-time WebUI link ("" on failure).
func mintLink(base, token string, timeout time.Duration) string {
	if token == "" {
		return ""
	}
	code, payload, err := doRequest(base, "POST", "/api/auth/bootstrap-link", token, nil, timeout)
	if err != nil || code != 200 {
		return ""
	}
	m, _ := payload.(map[string]any)
	u, _ := m["url"].(string)
	if strings.Contains(u, "?bootstrap=") {
		return u
	}
	return ""
}

// waitForLink polls until the runtime answers and the token exists, then mints.
func waitForLink(base, home string, deadline time.Duration) string {
	end := time.Now().Add(deadline)
	for time.Now().Before(end) {
		if tok := httpapi.ResolveToken(home); tok != "" {
			if link := mintLink(base, tok, 3*time.Second); link != "" {
				return link
			}
		}
		time.Sleep(500 * time.Millisecond)
	}
	return ""
}

// openInBrowser hands url to the desktop's default handler. On Windows it
// goes through explorer.exe so the browser is not a child of this process.
func openInBrowser(url string) error {
	var argv []string
	switch runtime.GOOS {
	case "windows":
		argv = []string{"explorer.exe", url}
	case "darwin":
		argv = []string{"open", url}
	default:
		argv = []string{"xdg-open", url}
	}
	code, _, err := tools.RunAttached(context.Background(), argv, "", nil, nil, discard{}, discard{}, 30*time.Second)
	if err != nil {
		return err
	}
	// explorer.exe exits 1 even when it opened the page.
	if code != 0 && runtime.GOOS != "windows" {
		return fmt.Errorf("%s exited %d", argv[0], code)
	}
	return nil
}

type discard struct{}

func (discard) Write(p []byte) (int, error) { return len(p), nil }

func cmdChat(c *Ctx) int {
	c.Errorf("remedy chat retired the Python ReAct loop.\n" +
		"Production turns use Go CognitionTurnRunner on :7400.\n" +
		"  Run `remedy serve` (first run downloads a checksum-verified runtime from GitHub Releases if needed) " +
		"and open the Desktop or WebUI, or POST /api/sessions/{id}/messages/stream against remedy-runtime.\n")
	return 2
}

func cmdConnectRelay(c *Ctx) int {
	relay, err := connect.StartRelay(c.P.Str("host"), c.P.Int("port"), 900*time.Second)
	if err != nil {
		c.Errorf("connect-relay: %v\n", err)
		return 2
	}
	defer relay.Stop()
	c.Errorf("connect-relay listening on %s (forwards framed blobs; does not decrypt)\n", relay.Addr())
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	<-ctx.Done()
	return 0
}

func cmdExec(c *Ctx) int {
	command := c.P.List("cmdline")
	if len(command) > 0 && command[0] == "--" {
		command = command[1:]
	}
	if len(command) == 0 {
		c.Errorf("No command specified\n")
		return 2
	}
	workdir := c.P.Str("workdir")
	if workdir != "" {
		abs, err := filepath.Abs(workdir)
		if err != nil {
			c.Errorf("Bad --workdir: %v\n", err)
			return 2
		}
		workdir = abs
	}
	timeout := time.Duration(c.P.Float("timeout") * float64(time.Second))
	c.Printf("Executing: %s\n", strings.Join(command, " "))
	start := time.Now()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	code, timedOut, err := tools.RunAttached(ctx, command, workdir, map[string]string{"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}, nil, c.Out, c.Err, timeout)
	if errors.Is(err, tools.ErrSpawnRefused) {
		c.Errorf("WARNING: %v\nExecution blocked by security policy\n", err)
		return 2
	}
	if err != nil {
		if code == 127 {
			c.Errorf("Command not found: %s\n", command[0])
			return code
		}
		c.Errorf("%v\n", err)
		if code == 0 {
			code = 1
		}
		return code
	}
	if timedOut {
		c.Errorf("Timed out after %s\n", timeout)
	}
	c.Printf("Exit code: %d (%dms)\n", code, time.Since(start).Milliseconds())
	return code
}

func cmdGateway(c *Ctx) int {
	switch c.P.Sub(1) {
	case "start":
		if c.P.Str("telegram-token") != "" || c.P.Str("discord-token") != "" || c.P.Str("slack-token") != "" {
			c.Errorf("Passing messenger tokens on the command line exposes them in process lists. " +
				"Prefer TELEGRAM_BOT_TOKEN / DISCORD_BOT_TOKEN / SLACK_BOT_TOKEN.\n")
		}
		c.Errorf("remedy gateway start retired Python BasicRuntime/ReAct. " +
			"Messengers inbound run inside Go remedy-runtime (remedy serve).\n")
		return 1
	case "status":
		db := c.DBPath()
		_, statErr := os.Stat(db)
		t := newTable("Remedy Gateway Status", "Metric", "Value")
		sessions, messenger, err := httpapi.GatewaySessionCounts(db)
		if err != nil {
			t.add("error", err.Error())
		} else {
			t.add("sessions", strconv.Itoa(sessions))
			t.add("messenger_sessions", strconv.Itoa(messenger))
			t.add("db_path", db)
			t.add("db_exists", asString(statErr == nil))
		}
		t.print(c.Out)
	case "serve":
		return cmdServe(c)
	case "channels":
		c.Println("Internal channels:")
		for _, ch := range []string{"cli", "web", "api"} {
			c.Printf("  %s\n", ch)
		}
		c.Println("\nMessengers:")
		for _, m := range httpapi.MessengerCatalog() {
			var flags []string
			if m.Inbound {
				flags = append(flags, "in")
			}
			if m.Outbound {
				flags = append(flags, "out")
			}
			dir := strings.Join(flags, "/")
			if dir == "" {
				dir = "—"
			}
			c.Printf("  %-14s %-8s %s  [%s]\n", m.ID, m.Status, m.Name, dir)
		}
	}
	return 0
}
