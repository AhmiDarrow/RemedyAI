package httpapi

import (
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

// The ledger had readers and no writer: the Usage screen and /usage showed zero
// however much was used.
func TestAModelRoundIsWrittenToTheUsageLedger(t *testing.T) {
	s, home := newPartnerTestServer(t)
	if err := recordUsage(home, "s1", "anthropic", "claude-fable-5-2", cognition.Usage{PromptTokens: 1200, CompletionTokens: 300, TotalTokens: 1500, CacheReadTokens: 900}); err != nil {
		t.Fatal(err)
	}
	// A provider that leaves the total out still counts.
	if err := recordUsage(home, "s1", "ollama", "llama", cognition.Usage{PromptTokens: 40, CompletionTokens: 10}); err != nil {
		t.Fatal(err)
	}
	// A round with nothing to count writes nothing.
	if err := recordUsage(home, "s1", "ollama", "llama", cognition.Usage{}); err != nil {
		t.Fatal(err)
	}
	out, err := usageSummary(home, 7, "")
	if err != nil {
		t.Fatal(err)
	}
	totals := out["totals"].(map[string]any)
	if totals["total_tokens"] != 1550 || totals["events"] != 2 || totals["prompt_tokens"] != 1240 {
		t.Fatalf("totals = %v", totals)
	}
	// No price is invented.
	if cost, _ := totals["estimated_cost_usd"].(float64); cost != 0 {
		t.Fatalf("cost = %v", cost)
	}
	text, _ := s.executeSlashCommand("", "/usage")["text"].(string)
	if !strings.Contains(text, "1,550 in all") || strings.Contains(text, "$") {
		t.Fatalf("/usage shows the counts and no made-up price: %s", text)
	}
}

func TestTheModelWrapperReportsEachRoundsUsage(t *testing.T) {
	var got []cognition.Usage
	m := &emittingModel{emit: func(string) {}, onUsage: func(u cognition.Usage) { got = append(got, u) }}
	m.emitEvent(cognition.ModelEvent{Text: "hello"})
	m.emitEvent(cognition.ModelEvent{Usage: &cognition.Usage{PromptTokens: 5, CompletionTokens: 2, TotalTokens: 7}})
	if len(got) != 1 || got[0].TotalTokens != 7 {
		t.Fatalf("got %v", got)
	}
	// Without a recorder nothing breaks.
	(&emittingModel{emit: func(string) {}}).emitEvent(cognition.ModelEvent{Usage: &cognition.Usage{TotalTokens: 1}})
}
