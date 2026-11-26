package httpapi

import (
	"database/sql"
	"os"
	"path/filepath"
	"testing"
)

func TestCLIGatewaySessionCounts(t *testing.T) {
	dir := t.TempDir()
	missing := filepath.Join(dir, "memory.db")
	if s, m, err := GatewaySessionCounts(missing); err != nil || s != 0 || m != 0 {
		t.Fatalf("missing db: %d %d %v", s, m, err)
	}
	if _, err := os.Stat(missing); err == nil {
		t.Fatal("counting created the database")
	}
	db, err := sql.Open("sqlite", missing)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`CREATE TABLE chat_sessions (id TEXT PRIMARY KEY, origin_channel TEXT);
		INSERT INTO chat_sessions VALUES ('a', NULL), ('b', ''), ('c', 'telegram'), ('d', 'discord');`); err != nil {
		t.Fatal(err)
	}
	_ = db.Close()
	s, m, err := GatewaySessionCounts(missing)
	if err != nil || s != 4 || m != 2 {
		t.Fatalf("counts: %d %d %v", s, m, err)
	}
	if len(MessengerCatalog()) == 0 {
		t.Fatal("empty messenger catalog")
	}
}
