package httpapi

import (
	"os"
	"path/filepath"
	"testing"
)

// A session id of ".." used to put a session's attachments in the Remedy
// home, so an upload named local_api_token replaced the API token there.
func TestDotSessionIDsStayInsideAttachments(t *testing.T) {
	home := t.TempDir()
	tokenPath := filepath.Join(home, "local_api_token")
	if err := os.WriteFile(tokenPath, []byte("original-token-value\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	root := attachmentsRoot(home)
	for _, sid := range []string{".", "..", "...", " .. "} {
		dir := sessionAttachmentsDir(sid, home)
		if rel, err := filepath.Rel(root, dir); err != nil || rel == "." || rel == ".." || filepath.IsAbs(rel) || len(rel) >= 2 && rel[:2] == ".." {
			t.Fatalf("session %q attachments at %s, outside %s", sid, dir, root)
		}
		if _, err := saveUpload(sid, "local_api_token", []byte("attacker"), "text/plain", home); err != nil {
			t.Fatalf("save %q: %v", sid, err)
		}
		if d := turnsDir(home, sid); d != "" && filepath.Dir(filepath.Dir(d)) != filepath.Join(home, "sessions") {
			t.Fatalf("session %q turn log at %s", sid, d)
		}
	}
	got, _ := os.ReadFile(tokenPath)
	if string(got) != "original-token-value\n" {
		t.Fatalf("API token overwritten: %q", got)
	}
	if _, notFound, err := resolveAttachmentFile("..", "local_api_token", home); err == nil {
		if p, _, _ := resolveAttachmentFile("..", "local_api_token", home); p == tokenPath {
			t.Fatalf("read of .. resolved to the API token (notFound=%v)", notFound)
		}
	}
}
