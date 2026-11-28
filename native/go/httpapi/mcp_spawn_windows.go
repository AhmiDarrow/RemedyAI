//go:build windows

package httpapi

import (
	"os/exec"
	"syscall"
)

// hideChildWindow keeps a connector's command from opening a console window.
func hideChildWindow(cmd *exec.Cmd) {
	const createNoWindow = 0x08000000
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindow}
}
