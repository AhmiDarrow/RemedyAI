//go:build windows

package sysres

import (
	"strings"

	"golang.org/x/sys/windows"
)

// RealPath returns where an existing path really is. filepath.EvalSymlinks
// does not follow directory junctions on Windows, so a junction to a
// protected folder would pass a check made on its result; the path the
// system reports for an open handle has every link, junction and 8.3 name
// resolved.
func RealPath(p string) (string, error) {
	name, err := windows.UTF16PtrFromString(p)
	if err != nil {
		return "", err
	}
	// No access rights are needed to ask a handle for its path.
	h, err := windows.CreateFile(name, 0,
		windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE|windows.FILE_SHARE_DELETE,
		nil, windows.OPEN_EXISTING, windows.FILE_FLAG_BACKUP_SEMANTICS, 0)
	if err != nil {
		return "", err
	}
	defer windows.CloseHandle(h)
	buf := make([]uint16, windows.MAX_PATH)
	for {
		n, err := windows.GetFinalPathNameByHandle(h, &buf[0], uint32(len(buf)), 0)
		if err != nil {
			return "", err
		}
		if int(n) < len(buf) {
			break
		}
		buf = make([]uint16, n+1)
	}
	real := windows.UTF16ToString(buf)
	if rest, ok := strings.CutPrefix(real, `\\?\UNC\`); ok {
		return `\\` + rest, nil
	}
	return strings.TrimPrefix(real, `\\?\`), nil
}
