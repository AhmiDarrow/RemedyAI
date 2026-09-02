package hive

import (
	"context"
	"errors"
	"testing"
	"time"
)

func waitStatus(t *testing.T, m *Manager, id string, want Status) {
	t.Helper()
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		snapshot, err := m.Snapshot(id)
		if err == nil && snapshot.Status == want {
			return
		}
		time.Sleep(time.Millisecond)
	}
	snapshot, _ := m.Snapshot(id)
	t.Fatalf("%s status=%s want=%s", id, snapshot.Status, want)
}

func TestScopedSpawnMessagingAndCancellation(t *testing.T) {
	manager := New(context.Background(), 4)
	defer manager.Shutdown()
	rootReady := make(chan struct{})
	if err := manager.Spawn(Spec{ID: "root", Goals: []string{"coordinate"}, MemoryScope: "goal:g", Capabilities: []string{"read", "search"}, Run: func(ctx context.Context, agent *Agent) error { close(rootReady); <-ctx.Done(); return ctx.Err() }}); err != nil {
		t.Fatal(err)
	}
	<-rootReady
	received := make(chan Message, 1)
	if err := manager.Spawn(Spec{ID: "child", Parent: "root", MemoryScope: "goal:g", Capabilities: []string{"read"}, Run: func(ctx context.Context, agent *Agent) error {
		select {
		case message := <-agent.Inbox():
			received <- message
			return nil
		case <-ctx.Done():
			return ctx.Err()
		}
	}}); err != nil {
		t.Fatal(err)
	}
	if err := manager.Send(Message{From: "root", To: "child", Type: "task", Payload: []byte("inspect")}); err != nil {
		t.Fatal(err)
	}
	select {
	case message := <-received:
		if string(message.Payload) != "inspect" {
			t.Fatalf("message=%#v", message)
		}
	case <-time.After(time.Second):
		t.Fatal("message not received")
	}
	waitStatus(t, manager, "child", Completed)
	if err := manager.Cancel("root"); err != nil {
		t.Fatal(err)
	}
	waitStatus(t, manager, "root", Canceled)
}

func TestPrivilegeEscalationQuotaAndCrashIsolation(t *testing.T) {
	manager := New(context.Background(), 2)
	defer manager.Shutdown()
	block := make(chan struct{})
	_ = manager.Spawn(Spec{ID: "root", MemoryScope: "m", Capabilities: []string{"read"}, Run: func(context.Context, *Agent) error { <-block; return nil }})
	if err := manager.Spawn(Spec{ID: "bad", Parent: "root", MemoryScope: "m", Capabilities: []string{"write"}, Run: func(context.Context, *Agent) error { return nil }}); !errors.Is(err, ErrCapabilityEscalation) {
		t.Fatalf("escalation=%v", err)
	}
	if err := manager.Spawn(Spec{ID: "crash", Parent: "root", MemoryScope: "m", Capabilities: []string{"read"}, Run: func(context.Context, *Agent) error { panic("boom") }}); err != nil {
		t.Fatal(err)
	}
	waitStatus(t, manager, "crash", Failed)
	snapshot, _ := manager.Snapshot("root")
	if snapshot.Status != Running {
		t.Fatalf("parent status=%s", snapshot.Status)
	}
	extraBlock := make(chan struct{})
	if err := manager.Spawn(Spec{ID: "extra", Run: func(context.Context, *Agent) error { <-extraBlock; return nil }}); err != nil {
		t.Fatal(err)
	}
	if err := manager.Spawn(Spec{ID: "overflow", Run: func(context.Context, *Agent) error { return nil }}); !errors.Is(err, ErrQuota) {
		t.Fatalf("quota=%v", err)
	}
	close(extraBlock)
	close(block)
}

func TestMailboxBackpressure(t *testing.T) {
	manager := New(context.Background(), 1)
	release := make(chan struct{})
	_ = manager.Spawn(Spec{ID: "agent", MailboxSize: 1, Run: func(context.Context, *Agent) error { <-release; return nil }})
	if err := manager.Send(Message{To: "agent"}); err != nil {
		t.Fatal(err)
	}
	if err := manager.Send(Message{To: "agent"}); !errors.Is(err, ErrMailboxFull) {
		t.Fatalf("mailbox=%v", err)
	}
	close(release)
	manager.Shutdown()
}

func TestAgentMessagesCannotSpoofOrCrossDelegationScope(t *testing.T) {
	manager := New(context.Background(), 4)
	defer manager.Shutdown()
	rootReady := make(chan *Agent, 1)
	otherReady := make(chan struct{})
	_ = manager.Spawn(Spec{ID: "root", MemoryScope: "goal:a", Capabilities: []string{"read"}, Run: func(ctx context.Context, agent *Agent) error {
		rootReady <- agent
		<-ctx.Done()
		return ctx.Err()
	}})
	_ = manager.Spawn(Spec{ID: "other", MemoryScope: "goal:b", Capabilities: []string{"read"}, Run: func(ctx context.Context, _ *Agent) error {
		close(otherReady)
		<-ctx.Done()
		return ctx.Err()
	}})
	agent := <-rootReady
	<-otherReady
	if err := agent.Send(Message{From: "other", To: "other", Type: "spoof"}); !errors.Is(err, ErrMessageScope) {
		t.Fatalf("cross-scope Send = %v", err)
	}
	received := make(chan Message, 1)
	_ = manager.Spawn(Spec{ID: "child", Parent: "root", MemoryScope: "goal:a", Capabilities: []string{"read"}, Run: func(ctx context.Context, child *Agent) error {
		select {
		case message := <-child.Inbox():
			received <- message
			return nil
		case <-ctx.Done():
			return ctx.Err()
		}
	}})
	if err := agent.Send(Message{From: "other", To: "child", Type: "task"}); err != nil {
		t.Fatal(err)
	}
	if message := <-received; message.From != "root" {
		t.Fatalf("From = %q, want root", message.From)
	}
}
