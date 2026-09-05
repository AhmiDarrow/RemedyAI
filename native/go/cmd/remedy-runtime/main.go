// Command remedy-runtime is the native runtime probe and production local API.
// Packaged Desktop and `remedy serve` launch this binary as the :7400 authority.
// --serve binds 127.0.0.1:7400 by default; --listen overrides (desktop-chosen
// port or ephemeral). Never implied by --probe alone.
// Python does not bind :7400. `tauri:dev` may still use a live Python sidecar
// unless REMEDY_RUNTIME_SIDECAR=1; packaged builds fail closed without this binary.
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
	"github.com/AhmiDarrow/RemedyAI/native/go/workers"
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
	// the file.
	if _, err := secret.EnsureHostSigningKey(""); err != nil {
		fmt.Fprintf(os.Stderr, "remedy-runtime host signing key: %v\n", err)
		os.Exit(1)
	}

	var runner httpapi.TurnRunner
	var voiceWorker httpapi.VoiceWorker
	var visionWorker httpapi.VisionWorker
	if *smokeFixture {
		runner = httpapi.NewFixtureTurnRunner()
	} else {
		// Per-turn ResolveChatModel: xAI OAuth bearer, then vision helper, else Scripted.
		home := httpapi.ResolveHomeDir("")
		cognition := httpapi.NewCognitionTurnRunner(httpapi.ResolveListenModel(home))
		cognition.HomeDir = home
		// Equal-or-better vs pre-cutover Python ReAct: supervise the RMDY tool
		// worker, dial FrameCaller, AttachPythonWorker. Fail closed — never
		// serve forever with only Go demo builtins pretending product tools.
		// Same session hosts voice/vision handlers (forever-Python ML lanes).
		cwd, _ := os.Getwd()
		session, err := workers.StartRMDYToolWorker(ctx, workers.RMDYToolOptions{
			HomeDir: home,
			Cwd:     cwd,
		})
		if err != nil {
			fmt.Fprintf(os.Stderr, "remedy-runtime: RMDY tool worker required: %v\n", err)
			os.Exit(1)
		}
		defer func() { _ = session.Close() }()
		if err := cognition.AttachPythonWorker(session.Client); err != nil {
			_ = session.Close()
			fmt.Fprintf(os.Stderr, "remedy-runtime: AttachPythonWorker failed: %v\n", err)
			os.Exit(1)
		}
		voiceWorker, visionWorker, err = httpapi.AttachMLWorkers(session.Client)
		if err != nil {
			_ = session.Close()
			fmt.Fprintf(os.Stderr, "remedy-runtime: voice/vision workers required: %v\n", err)
			os.Exit(1)
		}
		fmt.Fprintf(os.Stderr, "remedy-runtime: RMDY tool worker attached (pid=%d endpoint=%s; voice+vision)\n", session.PID, session.Endpoint)
		runner = cognition
	}

	err := httpapi.ListenAndServe(ctx, addr, httpapi.Config{
		TurnRunner:   runner,
		VoiceWorker:  voiceWorker,
		VisionWorker: visionWorker,
	}, func(bound string) {
		fmt.Fprintf(os.Stderr, "remedy-runtime listening on http://%s\n", bound)
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "remedy-runtime serve failed: %v\n", err)
		os.Exit(1)
	}
}
