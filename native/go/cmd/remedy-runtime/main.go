// Command remedy-runtime is the native runtime's packaged probe and future
// process entry point. It never owns Remedy's HTTP port; the compatibility
// sidecar remains the single local API authority during the layered cutover.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"runtime"
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
	flag.Parse()
	if !*probe {
		fmt.Fprintln(os.Stderr, "remedy-runtime currently requires --probe")
		os.Exit(2)
	}
	if err := json.NewEncoder(os.Stdout).Encode(currentProbe()); err != nil {
		fmt.Fprintln(os.Stderr, "unable to encode probe")
		os.Exit(1)
	}
}
