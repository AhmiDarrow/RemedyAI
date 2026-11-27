package httpapi

import (
	"database/sql"
	"fmt"
	"os"
)

// MessengerInfo is one messenger as the `remedy gateway channels` command
// lists it.
type MessengerInfo struct {
	ID       string
	Name     string
	Status   string
	Inbound  bool
	Outbound bool
}

// MessengerCatalog is the embedded messenger catalog (the one Settings uses).
func MessengerCatalog() []MessengerInfo {
	out := make([]MessengerInfo, 0, len(messengerCatalog))
	for _, m := range messengerCatalog {
		out = append(out, MessengerInfo{ID: m.ID, Name: m.Name, Status: m.Status, Inbound: m.Inbound, Outbound: m.Outbound})
	}
	return out
}

// GatewaySessionCounts counts chat sessions in memory.db and how many came in
// through a messenger. A database that does not exist yet counts as empty and
// is not created.
func GatewaySessionCounts(dbPath string) (sessions, messenger int, err error) {
	if _, statErr := os.Stat(dbPath); statErr != nil {
		return 0, 0, nil
	}
	db, err := sql.Open("sqlite", "file:"+dbPath+"?mode=ro")
	if err != nil {
		return 0, 0, err
	}
	defer db.Close()
	var exists int
	if err := db.QueryRow(`SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='chat_sessions'`).Scan(&exists); err != nil {
		return 0, 0, fmt.Errorf("read memory.db: %w", err)
	}
	if exists == 0 {
		return 0, 0, nil
	}
	if err := db.QueryRow(`SELECT COUNT(*), COALESCE(SUM(CASE WHEN COALESCE(origin_channel,'') <> '' THEN 1 ELSE 0 END),0) FROM chat_sessions`).Scan(&sessions, &messenger); err != nil {
		return 0, 0, fmt.Errorf("read chat_sessions: %w", err)
	}
	return sessions, messenger, nil
}
