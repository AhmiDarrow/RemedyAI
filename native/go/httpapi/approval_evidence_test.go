package httpapi

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

// What the owner was asked and what they decided is part of the turn's
// evidence: a reviewer should be able to see the gate, not just the tool call
// that followed it.
func TestApprovalActivityIsRecordedInTheTurnLog(t *testing.T) {
	home := t.TempDir()
	sid := "sess-approval-log"
	requestID := "req-approval"

	turnLog, err := openTurnLog(home, sid, requestID)
	if err != nil {
		t.Fatal(err)
	}
	defer turnLog.Close()

	hub := newTurnHub()
	live := hub.begin(sid, requestID)
	defer hub.end(live)
	ts := &turnStream{log: turnLog, live: live}
	hub.attachStream(sid, ts)
	defer hub.detachStream(sid, ts)

	queue := newApprovalQueue()
	queue.SetObserver(hub.RecordApproval)

	item := queue.Enqueue("shell.exec", `{"argv":["hostname"]}`, "Tool requires your approval", &sid, "run hostname")
	if item == nil {
		t.Fatal("enqueue returned nil")
	}
	if resolved := queue.Resolve(item.ID, true, "session"); resolved == nil {
		t.Fatal("resolve returned nil")
	}

	raw, err := os.ReadFile(turnLogPath(home, sid, requestID))
	if err != nil {
		t.Fatal(err)
	}
	decisions := map[string]string{}
	for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		if line == "" {
			continue
		}
		var rec map[string]any
		if err := json.Unmarshal([]byte(line), &rec); err != nil {
			t.Fatalf("log line is not JSON: %v", err)
		}
		if rec["t"] != "approval" {
			continue
		}
		id, _ := rec["id"].(string)
		decision, _ := rec["decision"].(string)
		decisions[decision] = id
		if tool, _ := rec["tool"].(string); tool != "shell.exec" {
			t.Fatalf("approval record names the wrong tool: %v", rec)
		}
	}
	if decisions["asked"] != item.ID {
		t.Fatalf("the request for approval was not recorded: %v", decisions)
	}
	if decisions["approved"] != item.ID {
		t.Fatalf("the owner's decision was not recorded: %v", decisions)
	}
}

// A decision made while nothing is running must not panic or invent a turn.
func TestApprovalWithNoLiveTurnIsNotRecorded(t *testing.T) {
	hub := newTurnHub()
	queue := newApprovalQueue()
	queue.SetObserver(hub.RecordApproval)
	sid := "sess-idle"
	item := queue.Enqueue("workspace.write", `{"path":"a"}`, "Tool requires your approval", &sid, "write a")
	if item == nil {
		t.Fatal("enqueue returned nil")
	}
	if resolved := queue.Resolve(item.ID, false, "session"); resolved == nil {
		t.Fatal("resolve returned nil")
	}
}
