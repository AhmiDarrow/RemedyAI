//go:build !windows

package httpapi

import "os/exec"

// hideChildWindow is a Windows concern; elsewhere a child has no window.
func hideChildWindow(*exec.Cmd) {}
