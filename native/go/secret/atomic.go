package secret

import (
	"os"
	"path/filepath"
)

// WriteFileAtomic writes data to path via a unique temp file + replace.
// Parent directories are created with 0o700; the final file mode is applied
// before the replace when the OS allows.
func WriteFileAtomic(path string, data []byte, mode os.FileMode) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".remedy-tmp-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	cleanup := true
	defer func() {
		if cleanup {
			_ = os.Remove(tmpName)
		}
	}()
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
	if mode != 0 {
		_ = os.Chmod(tmpName, mode)
	}
	if err := replaceFile(tmpName, path); err != nil {
		return err
	}
	cleanup = false
	if mode != 0 {
		_ = os.Chmod(path, mode)
	}
	return nil
}
