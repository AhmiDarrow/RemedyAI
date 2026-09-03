// Command remedy-runtime is the native runtime probe and opt-in local API.
// Python remains the production HTTP authority on :7400. Pass --listen or
// --serve to enable the Phase-4 Go API on a loopback address (never implied).
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
)

const (
	protocolVersion = 1
	toolABIVersion  = 1
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

func main() {
	probe := flag.Bool("probe", false, "emit one JSON readiness record and exit")
	listen := flag.String("listen", "", "loopback HTTP listen address (e.g. 127.0.0.1:0); opt-in, not :7400")
	serve := flag.Bool("serve", false, "serve the Phase-4 local HTTP API (defaults --listen to 127.0.0.1:0)")
	flag.Parse()

	if *probe {
		if err := json.NewEncoder(os.Stdout).Encode(currentProbe()); err != nil {
			fmt.Fprintln(os.Stderr, "unable to encode probe")
			os.Exit(1)
		}
		return
	}

	addr := *listen
	if *serve && addr == "" {
		addr = "127.0.0.1:0"
	}
	if addr == "" {
		fmt.Fprintln(os.Stderr, "remedy-runtime requires --probe or --listen/--serve")
		os.Exit(2)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()

	err := httpapi.ListenAndServe(ctx, addr, httpapi.Config{}, func(bound string) {
		fmt.Fprintf(os.Stderr, "remedy-runtime listening on http://%s\n", bound)
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "remedy-runtime serve failed: %v\n", err)
		os.Exit(1)
	}
}
