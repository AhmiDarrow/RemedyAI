//go:build darwin

package parentwatch

import (
	"os"

	"golang.org/x/sys/unix"
)

// WatchPID blocks until pid exits, then calls onExit. If pid is already
// gone, onExit runs immediately. pid 0/1/self is rejected.
func WatchPID(pid int, onExit func()) error {
	if pid <= 1 || pid == os.Getpid() {
		return ErrNoParent
	}
	kq, err := unix.Kqueue()
	if err != nil {
		return err
	}
	defer func() { _ = unix.Close(kq) }()

	change := []unix.Kevent_t{{
		Ident:  uint64(pid),
		Filter: unix.EVFILT_PROC,
		Flags:  unix.EV_ADD | unix.EV_ENABLE | unix.EV_ONESHOT,
		Fflags: unix.NOTE_EXIT,
	}}
	if _, err := unix.Kevent(kq, change, nil, nil); err != nil {
		if err == unix.ESRCH {
			if onExit != nil {
				onExit()
			}
			return nil
		}
		return err
	}

	events := make([]unix.Kevent_t, 1)
	for {
		n, err := unix.Kevent(kq, nil, events, nil)
		if err == unix.EINTR {
			continue
		}
		if err != nil {
			return err
		}
		if n <= 0 {
			continue
		}
		if events[0].Fflags&unix.NOTE_EXIT != 0 {
			if onExit != nil {
				onExit()
			}
			return nil
		}
	}
}
