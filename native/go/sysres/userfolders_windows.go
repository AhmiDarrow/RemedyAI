//go:build windows

package sysres

import "golang.org/x/sys/windows"

func systemFolder(kind FolderKind, home string) string {
	if !isOwnHome(home) {
		return ""
	}
	id := windows.FOLDERID_Desktop
	switch kind {
	case FolderDocuments:
		id = windows.FOLDERID_Documents
	case FolderDownloads:
		id = windows.FOLDERID_Downloads
	}
	p, err := windows.KnownFolderPath(id, 0)
	if err != nil {
		return ""
	}
	return p
}
