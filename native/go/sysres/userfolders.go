package sysres

import (
	"os"
	"path/filepath"
	"strings"
)

// The user's own folders, as the system knows them. A folder can live
// anywhere (Windows moves Desktop and Documents under a sync folder, Linux
// names them in user-dirs.dirs), so its place is asked for, never built
// from the home folder and a fixed name.
type FolderKind int

const (
	FolderDesktop FolderKind = iota
	FolderDocuments
	FolderDownloads
)

// The name the folder has when the system has no record of it.
var folderDefaultName = [...]string{"Desktop", "Documents", "Downloads"}

// UserFolder returns where a folder of the user is, or "" when there is no
// such folder. home is the user's home; a home that is not the signed-in
// user's own has no system record, so the default name under it is used.
func UserFolder(kind FolderKind, home string) string {
	if home == "" {
		home, _ = os.UserHomeDir()
	}
	if home == "" {
		return ""
	}
	p := systemFolder(kind, home)
	if p == "" {
		p = filepath.Join(home, folderDefaultName[kind])
	}
	abs, err := filepath.Abs(p)
	if err != nil {
		return ""
	}
	if st, err := os.Stat(abs); err != nil || !st.IsDir() {
		return ""
	}
	return abs
}

// UserFolders returns the Desktop, Documents and Downloads folders that exist.
func UserFolders(home string) []string {
	out := make([]string, 0, 3)
	for _, kind := range []FolderKind{FolderDesktop, FolderDocuments, FolderDownloads} {
		p := UserFolder(kind, home)
		if p == "" {
			continue
		}
		seen := false
		for _, o := range out {
			seen = seen || strings.EqualFold(o, p)
		}
		if !seen {
			out = append(out, p)
		}
	}
	return out
}

// isOwnHome reports whether home is the signed-in user's home.
func isOwnHome(home string) bool {
	own, err := os.UserHomeDir()
	if err != nil || own == "" {
		return false
	}
	a, errA := filepath.Abs(home)
	b, errB := filepath.Abs(own)
	return errA == nil && errB == nil && strings.EqualFold(filepath.Clean(a), filepath.Clean(b))
}
