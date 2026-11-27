//go:build !windows

package sysres

import "testing"

func TestXdgUserDir(t *testing.T) {
	text := "# comment\nXDG_DESKTOP_DIR=\"$HOME/Schreibtisch\"\nXDG_DOCUMENTS_DIR=\"/data/docs\"\nXDG_DOWNLOAD_DIR=\"$HOME/\"\n"
	for key, want := range map[string]string{
		"XDG_DESKTOP_DIR":   "/home/a/Schreibtisch",
		"XDG_DOCUMENTS_DIR": "/data/docs",
		"XDG_DOWNLOAD_DIR":  "", // set to the home: no such folder
		"XDG_MUSIC_DIR":     "",
	} {
		if got := xdgUserDir(text, key, "/home/a"); got != want {
			t.Fatalf("%s: got %q want %q", key, got, want)
		}
	}
}
