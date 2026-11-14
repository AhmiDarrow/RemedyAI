//go:build windows

package secret

import (
	"errors"
	"time"

	"golang.org/x/sys/windows"
)

// replaceBudget bounds how long replaceFile keeps retrying a destination that
// another handle holds open. Go opens files without FILE_SHARE_DELETE, so a
// reader polling the destination (the host bridge's Wait loop reads job files
// every 20-50ms) makes MoveFileEx fail with ERROR_ACCESS_DENIED until that
// read finishes. Reads are short, so a brief retry turns a silently lost write
// into a slightly later one.
var replaceBudget = 2 * time.Second

func isTransientReplaceError(err error) bool {
	return errors.Is(err, windows.ERROR_ACCESS_DENIED) ||
		errors.Is(err, windows.ERROR_SHARING_VIOLATION) ||
		errors.Is(err, windows.ERROR_LOCK_VIOLATION)
}

func replaceFile(source, destination string) error {
	from, err := windows.UTF16PtrFromString(source)
	if err != nil {
		return err
	}
	to, err := windows.UTF16PtrFromString(destination)
	if err != nil {
		return err
	}
	deadline := time.Now().Add(replaceBudget)
	delay := time.Millisecond
	for {
		err = windows.MoveFileEx(from, to, windows.MOVEFILE_REPLACE_EXISTING|windows.MOVEFILE_WRITE_THROUGH)
		if err == nil || !isTransientReplaceError(err) || time.Now().After(deadline) {
			return err
		}
		time.Sleep(delay)
		if delay < 50*time.Millisecond {
			delay *= 2
		}
	}
}
