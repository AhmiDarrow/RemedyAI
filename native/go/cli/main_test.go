package cli

import (
	"os"
	"path/filepath"
	"testing"
)

// On Windows the temp folder is often spelled with an 8.3 short name
// (C:\Users\LONGNA~1\...), while the commands print and compare the long
// form. Give every test the long spelling so the two agree.
func TestMain(m *testing.M) {
	if long, err := filepath.EvalSymlinks(os.TempDir()); err == nil {
		_ = os.Setenv("TMP", long)
		_ = os.Setenv("TEMP", long)
	}
	os.Exit(m.Run())
}
