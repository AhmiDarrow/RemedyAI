package httpapi

import (
	"path/filepath"
	"testing"
)

// Recorded evidence is addressed by the turn that produced it. Without the
// request id on a stored message, the process trail can only show the full
// tool output for a turn this client happened to watch live — reopening the
// app would lose it, which defeats the point of recording it.
func TestStoredMessagesCarryTheirTurnID(t *testing.T) {
	home := t.TempDir()
	store, err := openSessionStore(filepath.Join(home, "memory.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	sess, err := store.Create(createSessionRequest{Title: "evidence"})
	if err != nil {
		t.Fatal(err)
	}

	const requestID = "req-evidence-1"
	if _, err := store.insertMessage(
		sess.ID, requestID, "assistant", "did the work", nil,
		[]map[string]any{{"id": "call-1", "name": "shell.exec"}},
		[]map[string]any{{"id": "call-1", "name": "shell.exec", "output": "ok"}},
		nil, nil, nil, false,
	); err != nil {
		t.Fatal(err)
	}

	msgs, err := store.ListMessages(sess.ID, 50, 0)
	if err != nil {
		t.Fatal(err)
	}
	var found bool
	for _, m := range msgs {
		if m.Role != "assistant" {
			continue
		}
		found = true
		if m.RequestID != requestID {
			t.Fatalf("assistant row request_id=%q want %q", m.RequestID, requestID)
		}
	}
	if !found {
		t.Fatal("assistant row not returned")
	}

	// A row written outside a turn simply has no id; it must not error.
	if _, err := store.AddMessage(sess.ID, "user", "hello", nil, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := store.ListMessages(sess.ID, 50, 0); err != nil {
		t.Fatalf("listing a row with no turn id failed: %v", err)
	}
}
