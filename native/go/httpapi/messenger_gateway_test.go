package httpapi

import (
	"os"
	"path/filepath"
	"testing"
)

func TestResolveMessengerSessionJoinsFocused(t *testing.T) {
	home := t.TempDir()
	dbPath := filepath.Join(home, "memory.db")
	store, err := openSessionStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()

	homeSess, err := store.Create(createSessionRequest{Title: "Write up a fancy x post"})
	if err != nil {
		t.Fatal(err)
	}

	s := &Server{sessions: store, homeDir: home}
	s.SetFocusedSession(homeSess.ID)

	got, err := s.resolveMessengerSession("telegram", "8720969343", "bob", "Hi reme")
	if err != nil {
		t.Fatal(err)
	}
	if got.ID != homeSess.ID {
		t.Fatalf("expected focused session %s, got %s", homeSess.ID, got.ID)
	}
	if got.OriginChannel == nil || *got.OriginChannel != "telegram" {
		t.Fatalf("origin=%v", got.OriginChannel)
	}
	if got.ExternalChatID == nil || *got.ExternalChatID != "8720969343" {
		t.Fatalf("ext=%v", got.ExternalChatID)
	}
	if ghost, ok, _ := store.Get("msg:telegram:8720969343"); ok {
		t.Fatalf("spawned parallel messenger session: %#v", ghost)
	}
}

func TestResolveMessengerSessionCreatesWhenUnfocused(t *testing.T) {
	home := t.TempDir()
	dbPath := filepath.Join(home, "memory.db")
	store, err := openSessionStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()

	s := &Server{sessions: store, homeDir: home}
	got, err := s.resolveMessengerSession("telegram", "42", "alice", "hello")
	if err != nil {
		t.Fatal(err)
	}
	if got.ID != "msg:telegram:42" {
		t.Fatalf("id=%s", got.ID)
	}
	if got.OriginChannel == nil || *got.OriginChannel != "telegram" {
		t.Fatalf("origin=%v", got.OriginChannel)
	}
}

// With messenger_join_desktop off, a sender gets a conversation of their own
// and the open desktop conversation is not bound to their chat.
func TestResolveMessengerSessionKeepsToItselfWhenJoiningIsOff(t *testing.T) {
	home := t.TempDir()
	if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte("[advanced]\nmessenger_join_desktop = false\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := openSessionStore(filepath.Join(home, "memory.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	desk, err := store.Create(createSessionRequest{Title: "Private notes"})
	if err != nil {
		t.Fatal(err)
	}
	s := &Server{sessions: store, homeDir: home}
	s.SetFocusedSession(desk.ID)

	got, err := s.resolveMessengerSession("telegram", "42", "sam", "hello")
	if err != nil {
		t.Fatal(err)
	}
	if got.ID == desk.ID || got.ID != "msg:telegram:42" {
		t.Fatalf("sender joined the desktop conversation: id=%s", got.ID)
	}
	if fresh, _, _ := store.Get(desk.ID); fresh.OriginChannel != nil || fresh.ExternalChatID != nil {
		t.Fatalf("desktop conversation was bound to the chat: %#v", fresh)
	}
}
