// Command remedy-runtime is the native runtime probe and production local API.
// Packaged Desktop launches this binary as the :7400 sidecar authority.
// --serve binds 127.0.0.1:7400 by default; --listen overrides (desktop-chosen
// port or ephemeral). Never implied by --probe alone.
// `tauri:dev` still prefers the live Python sidecar unless REMEDY_RUNTIME_SIDECAR=1.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"runtime"

	"github.com/AhmiDarrow/RemedyAI/native/go/httpapi"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

const (
	protocolVersion = 1
	toolABIVersion  = 1
	// defaultServeAddr is the production loopback listen for --serve.
	defaultServeAddr = "127.0.0.1:7400"
)

type probeResult struct {
	Status   string `json:"status"`
	Protocol int    `json:"protocol"`
	ToolABI  int    `json:"tool_abi"`
	OS       string `json:"os"`
	Arch     string `json:"arch"`
}

func currentProbe() probeResult {
	return probeResult{
		Status:   "ready",
		Protocol: protocolVersion,
		ToolABI:  toolABIVersion,
		OS:       runtime.GOOS,
		Arch:     runtime.GOARCH,
	}
}

// resolveServeAddr picks the loopback bind for --serve/--listen.
// Explicit --listen wins; bare --serve is production :7400.
func resolveServeAddr(listen string, serve bool) string {
	if listen != "" {
		return listen
	}
	if serve {
		return defaultServeAddr
	}
	return ""
}

func main() {
	probe := flag.Bool("probe", false, "emit one JSON readiness record and exit")
	listen := flag.String("listen", "", "loopback HTTP listen address (overrides --serve default; e.g. 127.0.0.1:7410 or 127.0.0.1:0)")
	serve := flag.Bool("serve", false, "serve the production local HTTP API (defaults --listen to 127.0.0.1:7400)")
	smokeFixture := flag.Bool("smoke-fixture", false, "use FixtureTurnRunner instead of cognition (explicit smoke only)")
	flag.Parse()

	if *probe {
		if err := json.NewEncoder(os.Stdout).Encode(currentProbe()); err != nil {
			fmt.Fprintln(os.Stderr, "unable to encode probe")
			os.Exit(1)
		}
		return
	}

	addr := resolveServeAddr(*listen, *serve)
	if addr == "" {
		fmt.Fprintln(os.Stderr, "remedy-runtime requires --probe or --listen/--serve")
		os.Exit(2)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()

	// Durable Zig HMAC key in the secret store (never logged). httpapi terminal
	// / computer routes install the same on-disk key into remedy_core when they
	// spawn ConPTY; Connect Tailscale management loads remedy_core lazily via
	// native/go/core for CLI/msiexec (no os/exec). Python host_binding shares
	// the file. Turn tools use the Go Tool ABI registry; AttachPythonWorker
	// adds RuntimePython tools over RMDY frames when a worker is connected.
	if _, err := secret.EnsureHostSigningKey(""); err != nil {
		fmt.Fprintf(os.Stderr, "remedy-runtime host signing key: %v\n", err)
		os.Exit(1)
	}

	var runner httpapi.TurnRunner
	if *smokeFixture {
		runner = httpapi.NewFixtureTurnRunner()
	} else {
		// Live OpenAI-compatible SSE when settings+secret are ready; else Scripted.
		runner = httpapi.NewCognitionTurnRunner(httpapi.ResolveListenModel(""))
	}

	err := httpapi.ListenAndServe(ctx, addr, httpapi.Config{
		TurnRunner: runner,
	}, func(bound string) {
		fmt.Fprintf(os.Stderr, "remedy-runtime listening on http://%s\n", bound)
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "remedy-runtime serve failed: %v\n", err)
		os.Exit(1)
	}
}
