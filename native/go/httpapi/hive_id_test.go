package httpapi

import (
	"os"
	"path/filepath"
	"testing"
)

// A hive id from a request used to be joined into a path as is, so
// "../../settings" read (and update rewrote) the settings file.
func TestHiveIDOutsideTheStoreIsNotFound(t *testing.T) {
	home := t.TempDir()
	fs := openHiveStore(home)
	if err := os.WriteFile(filepath.Join(home, "settings.json"), []byte(`{"id":"x","goal":"secret"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"../../settings", "../episodes/../../settings", "ABCDEF00ABCDEF00ABCDEF00ABCDEF00"} {
		if _, ok, _ := fs.get(id); ok {
			t.Fatalf("get(%q) found a record outside the hive store", id)
		}
		if _, ok, _ := fs.update(id, func(*hiveDaughter) bool { return true }); ok {
			t.Fatalf("update(%q) touched a record outside the hive store", id)
		}
	}
	d, err := fs.hire("check the oven", "", "", "", "", 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok, err := fs.get(d.ID); !ok || err != nil {
		t.Fatalf("a real hive id is not found: ok=%v err=%v", ok, err)
	}
}
