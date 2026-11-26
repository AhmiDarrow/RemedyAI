package httpapi

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

// An unreadable goals file must not be silently replaced by an empty one on
// the next save: the owner's goals are kept beside it.
func TestUnreadableGoalsFileIsKeptNotOverwritten(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	s := &Server{homeDir: home}
	path := s.lifeGoalsPath()
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	broken := []byte(`{"goals":[{"title":"Ship Remedy 1.0"}],` + "\x00")
	if err := os.WriteFile(path, broken, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := s.saveLifeGoalsFile(s.loadLifeGoalsFile()); err != nil {
		t.Fatal(err)
	}
	kept, _ := filepath.Glob(path + ".corrupt-*")
	if len(kept) != 1 {
		t.Fatalf("corrupt goals were not kept aside: %v", kept)
	}
	got, err := os.ReadFile(kept[0])
	if err != nil || !bytes.Equal(got, broken) {
		t.Fatalf("kept file = %q, %v", got, err)
	}
}
