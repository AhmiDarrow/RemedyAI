package connect

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
)

// writeBytesAtomic writes data to path via a same-dir temp file then rename.
// Best-effort 0o600 on the published file (Windows ignores most mode bits).
func writeBytesAtomic(path string, data []byte) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".remedy-connect-*.tmp")
	if err != nil {
		return err
	}
	tmpPath := tmp.Name()
	cleanup := true
	defer func() {
		if cleanup {
			_ = os.Remove(tmpPath)
		}
	}()
	if err := tmp.Chmod(0o600); err != nil && runtime.GOOS != "windows" {
		_ = tmp.Close()
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	// Windows os.Rename refuses an existing destination; remove then rename.
	_ = os.Remove(path)
	if err := os.Rename(tmpPath, path); err != nil {
		return fmt.Errorf("atomic replace: %w", err)
	}
	cleanup = false
	_ = os.Chmod(path, 0o600)
	return nil
}
