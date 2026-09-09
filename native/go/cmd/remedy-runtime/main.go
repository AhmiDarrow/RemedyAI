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
	"github.com/AhmiDarrow/RemedyAI/native/go/servelock"
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

	home := httpapi.ResolveHomeDir("")
	serveLock := servelock.New(home)
	if ok, msg := serveLock.TryAcquire(); !ok {
		fmt.Fprintln(os.Stderr, msg)
		os.Exit(1)
	}
	defer serveLock.Release()
	go serveLock.RunHeartbeat(ctx)

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
		cognition := httpapi.NewCognitionTurnRunner(httpapi.ResolveListenModel(home))
		cognition.HomeDir = home
		// Equal-or-better vs pre-cutover Python ReAct: supervise the RMDY tool
		// worker, dial FrameCaller, AttachPythonWorker. Fail closed — never
		// serve forever with only Go demo builtins pretending product tools.
		// Same session hosts voice/vision handlers (forever-Python ML lanes).
		//
		// The supervisor respawns a crashed worker (backoff 1 s → 30 s, five
		// restarts per ten minutes) and swaps the ipc.Client behind one
		// LiveCaller, so registered tools follow the new process.
		cwd, _ := os.Getwd()
		opts := workers.RMDYToolOptions{
			HomeDir: home,
			Cwd:     cwd,
		}
		var supervisor *workers.Supervisor
		if workers.PythonWorkerNeedsDownload() {
			// The only interpreter is the managed CPython download. Bind HTTP
			// first so the Desktop connects; /api/status reports
			// tools_python=downloading|ready|failed (workers.ToolsPythonStatus)
			// and tool calls fail with a clear error until the worker attaches.
			fmt.Fprintln(os.Stderr, "remedy-runtime: no local Python; downloading managed CPython in the background")
			supervisor = workers.StartSupervisedRMDYToolWorkerAsync(ctx, opts, func(err error) {
				if err != nil {
					fmt.Fprintf(os.Stderr, "remedy-runtime: RMDY tool worker attach failed after download: %v\n", err)
				}
			})
		} else {
			var err error
			supervisor, err = workers.StartSupervisedRMDYToolWorker(ctx, opts)
			if err != nil {
				fmt.Fprintf(os.Stderr, "remedy-runtime: RMDY tool worker required: %v\n", err)
				os.Exit(1)
			}
		}
		defer func() { _ = supervisor.Close() }()
		caller := supervisor.Caller()
		if err := cognition.AttachPythonWorker(caller); err != nil {
			_ = supervisor.Close()
			fmt.Fprintf(os.Stderr, "remedy-runtime: AttachPythonWorker failed: %v\n", err)
			os.Exit(1)
		}
		var err error
		voiceWorker, visionWorker, err = httpapi.AttachMLWorkers(caller)
		if err != nil {
			_ = supervisor.Close()
			fmt.Fprintf(os.Stderr, "remedy-runtime: voice/vision workers required: %v\n", err)
			os.Exit(1)
		}
		if session := supervisor.Session(); session != nil {
			fmt.Fprintf(os.Stderr, "remedy-runtime: RMDY tool worker attached (pid=%d endpoint=%s; voice+vision)\n", session.PID, session.Endpoint)
		}
		runner = cognition
	}

	err := httpapi.ListenAndServe(ctx, addr, httpapi.Config{
		TurnRunner:   runner,
		VoiceWorker:  voiceWorker,
		VisionWorker: visionWorker,
		HomeDir:      home,
	}, func(bound string) {
		fmt.Fprintf(os.Stderr, "remedy-runtime listening on http://%s\n", bound)
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "remedy-runtime serve failed: %v\n", err)
		os.Exit(1)
	}
}
