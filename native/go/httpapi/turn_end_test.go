package httpapi

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/mind/drives"
)

// turnEndRunner is a real CognitionTurnRunner with the native prompt on (as
// the server sets it up) and a scripted model that answers each turn once.
func turnEndRunner(t *testing.T, turns int) (*CognitionTurnRunner, string) {
	t.Helper()
	home := t.TempDir()
	rounds := make([][]cognition.ModelEvent, turns)
	for i := range rounds {
		rounds[i] = []cognition.ModelEvent{{Text: "Glad that helped.", Done: true}}
	}
	model := &cognition.ScriptedModel{Rounds: rounds}
	r := NewCognitionTurnRunner(model)
	r.forcePrimary = model
	r.HomeDir = home
	r.PromptSources = serverPromptSources{}
	r.MemoryDBPath = filepath.Join(home, "memory.db")
	return r, home
}

func runTurn(t *testing.T, r *CognitionTurnRunner, req TurnRequest) {
	t.Helper()
	if _, err := CollectTokens(context.Background(), r, req); err != nil {
		t.Fatal(err)
	}
}

func aliveOutcomes(t *testing.T, home string) []map[string]any {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(home, "alive.json"))
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		t.Fatal(err)
	}
	var st drives.State
	if err := json.Unmarshal(raw, &st); err != nil {
		t.Fatal(err)
	}
	return st.Outcomes
}

func TestTurnEndRecordsTheOutcome(t *testing.T) {
	r, home := turnEndRunner(t, 2)
	runTurn(t, r, TurnRequest{Prompt: "Thanks, that worked perfectly.", SessionID: "s1"})
	rows := aliveOutcomes(t, home)
	if len(rows) != 1 || rows[0]["kind"] != "accepted" {
		t.Fatalf("outcomes after a thank-you turn: %v", rows)
	}
	runTurn(t, r, TurnRequest{Prompt: "That's wrong, still broken.", SessionID: "s1"})
	rows = aliveOutcomes(t, home)
	if len(rows) != 2 || rows[1]["kind"] != "unhappy" {
		t.Fatalf("outcomes after an unhappy turn: %v", rows)
	}
	// What the drives and partner status read.
	st, err := drives.Load(mindEnv(home))
	if err != nil || st.Affect.Valence >= 0.61 {
		t.Fatalf("affect=%+v err=%v", st.Affect, err)
	}
}

func TestTurnEndOutcomeOnlyForTheOwner(t *testing.T) {
	r, home := turnEndRunner(t, 2)
	runTurn(t, r, TurnRequest{Prompt: "thanks, perfect", SessionID: "tg-1", Origin: "telegram"})
	runTurn(t, r, TurnRequest{Prompt: "thanks, perfect", SessionID: "hive_abc"})
	if rows := aliveOutcomes(t, home); len(rows) != 0 {
		t.Fatalf("a stranger's or a hive pulse's message moved her outcomes: %v", rows)
	}
}

func TestTurnEndOutcomeKnobOff(t *testing.T) {
	r, home := turnEndRunner(t, 1)
	if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte("[advanced]\noutcome_learning = false\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	InvalidateConfigCache()
	t.Cleanup(InvalidateConfigCache)
	runTurn(t, r, TurnRequest{Prompt: "thanks, perfect", SessionID: "s1"})
	if rows := aliveOutcomes(t, home); len(rows) != 0 {
		t.Fatalf("outcome_learning off still recorded: %v", rows)
	}
}

const tickSkillMD = `---
name: tick-promote
description: A learned skill that keeps working across sessions
version: 0.1.0
kind: native
status: validated
tags: []
tools: []
metadata:
  auto_generated: true
  effort_weight: 0.2
  lifecycle: probation
---

# steps
do the thing
`

func seedPromotableSkill(t *testing.T, home string) {
	t.Helper()
	dir := filepath.Join(home, "skills", "tick-promote")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "SKILL.md"), []byte(tickSkillMD), 0o600); err != nil {
		t.Fatal(err)
	}
	stats := map[string]any{"version": 1, "skills": map[string]any{"tick-promote": map[string]any{
		"total_executions": 6, "successes": 6, "failures": 0, "consecutive_failures": 0,
		"execution_by_session": map[string]any{"a": 2, "b": 2, "c": 2},
	}}}
	raw, _ := json.Marshal(stats)
	if err := os.WriteFile(filepath.Join(home, "skill_stats.json"), raw, 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestTurnEndRunsTheSkillLifecycleTick(t *testing.T) {
	r, home := turnEndRunner(t, 1)
	seedPromotableSkill(t, home)
	runTurn(t, r, TurnRequest{Prompt: "what's on my calendar", SessionID: "s1"})
	raw, err := os.ReadFile(filepath.Join(home, "skills", "tick-promote", "SKILL.md"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), "status: active") || !strings.Contains(string(raw), "lifecycle_action: promote") {
		t.Fatalf("the turn end did not promote the skill:\n%s", raw)
	}
}

func TestTurnEndSkillTickOnlyForTheOwner(t *testing.T) {
	r, home := turnEndRunner(t, 1)
	seedPromotableSkill(t, home)
	runTurn(t, r, TurnRequest{Prompt: "hello", SessionID: "tg-1", Origin: "telegram"})
	raw, _ := os.ReadFile(filepath.Join(home, "skills", "tick-promote", "SKILL.md"))
	if !strings.Contains(string(raw), "status: validated") {
		t.Fatalf("a stranger's turn changed a skill:\n%s", raw)
	}
}

func seedSessionNotes(t *testing.T, dbPath, sid string, n int) {
	t.Helper()
	store, err := OpenCLIStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	for i := 0; i < n; i++ {
		e := NewCLIMemoryEntry("note", "note "+string(rune('a'+i)), "the garden needs water", []string{"garden"}, 0.5)
		s := sid
		e.SessionID = &s
		e.meta.set("action_items", []any{"water the tomatoes"})
		if err := store.Upsert(e); err != nil {
			t.Fatal(err)
		}
	}
}

func consolidations(t *testing.T, dbPath, sid string) []*CLIMemoryEntry {
	t.Helper()
	store, err := OpenCLIStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	rows, err := store.listBySession(sid, 200)
	if err != nil {
		t.Fatal(err)
	}
	var out []*CLIMemoryEntry
	for _, e := range rows {
		if e.Title == "Consolidation: "+sid {
			out = append(out, e)
		}
	}
	return out
}

func TestTurnEndConsolidatesSessionMemory(t *testing.T) {
	r, _ := turnEndRunner(t, 2)
	seedSessionNotes(t, r.MemoryDBPath, "s-few", consolidateMinNotes-1)
	runTurn(t, r, TurnRequest{Prompt: "ok", SessionID: "s-few"})
	if got := consolidations(t, r.MemoryDBPath, "s-few"); len(got) != 0 {
		t.Fatalf("consolidated with fewer than %d notes", consolidateMinNotes)
	}
	seedSessionNotes(t, r.MemoryDBPath, "s-mem", consolidateMinNotes)
	runTurn(t, r, TurnRequest{Prompt: "ok", SessionID: "s-mem"})
	got := consolidations(t, r.MemoryDBPath, "s-mem")
	if len(got) != 1 || !strings.Contains(got[0].Content, "water the tomatoes") || got[0].EntryType != "session" {
		t.Fatalf("consolidation=%+v", got)
	}
}

func TestTurnEndConsolidationSkips(t *testing.T) {
	r, home := turnEndRunner(t, 3)
	for _, sid := range []string{"hive_x", "tg-1", "s-off"} {
		seedSessionNotes(t, r.MemoryDBPath, sid, consolidateMinNotes)
	}
	runTurn(t, r, TurnRequest{Prompt: "ok", SessionID: "hive_x"})
	runTurn(t, r, TurnRequest{Prompt: "ok", SessionID: "tg-1", Origin: "telegram"})
	if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte("soul_field_enabled = false\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	InvalidateConfigCache()
	t.Cleanup(InvalidateConfigCache)
	runTurn(t, r, TurnRequest{Prompt: "ok", SessionID: "s-off"})
	for _, sid := range []string{"hive_x", "tg-1", "s-off"} {
		if got := consolidations(t, r.MemoryDBPath, sid); len(got) != 0 {
			t.Errorf("%s consolidated: %+v", sid, got)
		}
	}
}

func TestServerGivesTheRunnerItsMemoryDB(t *testing.T) {
	home := t.TempDir()
	r := NewCognitionTurnRunner(&cognition.ScriptedModel{})
	db := filepath.Join(home, "memory.db")
	s, err := New(Config{HomeDir: home, Token: "t", DBPath: db, TurnRunner: r})
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if r.MemoryDBPath != db {
		t.Fatalf("MemoryDBPath=%q", r.MemoryDBPath)
	}
}

func TestHearthSkillTickIsSpaced(t *testing.T) {
	s, home := newPartnerTestServer(t)
	seedPromotableSkill(t, home)
	now := time.Now()
	n, err := s.hearthSkillTick(now)
	if err != nil || n != 1 {
		t.Fatalf("first tick n=%d err=%v", n, err)
	}
	// Put the skill back to validated: inside the window it waits for the
	// next tick, after it the tick promotes it again.
	raw := tickSkillMD
	_ = os.WriteFile(filepath.Join(home, "skills", "tick-promote", "SKILL.md"), []byte(raw), 0o600)
	if n, _ := s.hearthSkillTick(now.Add(time.Minute)); n != 0 {
		t.Fatalf("ticked again within %v", skillTickEvery)
	}
	if n, _ := s.hearthSkillTick(now.Add(skillTickEvery + time.Second)); n != 1 {
		t.Fatalf("no tick after the window: n=%d", n)
	}
	// The learn clock's pulse records it in the learn log.
	_ = os.WriteFile(filepath.Join(home, "skills", "tick-promote", "SKILL.md"), []byte(raw), 0o600)
	s.skillTickAt.Store(0)
	_ = s.hearthPulseLearn(context.Background())
	var found bool
	for _, row := range readLearnLog(home, 20) {
		if row["kind"] != "improve" {
			continue
		}
		if ch, _ := row["changed"].(map[string]any); ch != nil && ch["skills_refined"] != nil {
			found = true
		}
	}
	if !found {
		t.Fatalf("the improve line does not name the refined skills: %v", readLearnLog(home, 20))
	}
}
