// Package parentwatch stops a desktop sidecar when its GUI parent dies.
//
// Windows already binds the sidecar to a kill-on-close job object; Linux
// sets PR_SET_PDEATHSIG in the child at spawn. macOS has neither, so the
// runtime watches the parent PID itself (kqueue EVFILT_PROC NOTE_EXIT)
// when launched with REMEDY_DESKTOP_SIDECAR=1.
package parentwatch

import (
	"errors"
	"fmt"
	"os"
	"runtime"
	"strconv"
)

const desktopSidecarEnv = "REMEDY_DESKTOP_SIDECAR"

// ErrNoParent means there is no useful process to watch (pid 0, 1, or self).
var ErrNoParent = errors.New("no parent process to watch")

// Watcher is the hook tests replace to observe whether a watch was started.
var startWatch = watchAndTerminate

// StartIfDesktopSidecar begins a background watch of the parent PID when
// this process is a Desktop sidecar on Darwin. Other OS / CLI launches are
// no-ops (Linux pdeathsig and Windows job objects already cover them).
func StartIfDesktopSidecar() {
	if os.Getenv(desktopSidecarEnv) != "1" {
		return
	}
	if runtime.GOOS != "darwin" {
		return
	}
	pid := os.Getppid()
	if pid <= 1 {
		return
	}
	go func() {
		_ = startWatch(pid)
	}()
}

// ParentPIDFromEnv returns an explicit override used by tests, or zero.
func ParentPIDFromEnv() int {
	raw := os.Getenv("REMEDY_WATCH_PARENT_PID")
	if raw == "" {
		return 0
	}
	pid, err := strconv.Atoi(raw)
	if err != nil || pid <= 1 {
		return 0
	}
	return pid
}

func watchAndTerminate(pid int) error {
	return WatchPID(pid, func() {
		p, err := os.FindProcess(os.Getpid())
		if err != nil {
			os.Exit(0)
		}
		// Match Linux pdeathsig (SIGTERM) so serve shutdown hooks can run.
		if err := terminateSelf(p); err != nil {
			os.Exit(0)
		}
	})
}

func describeWatch(pid int) string {
	return fmt.Sprintf("parentwatch: watching pid %d", pid)
}
