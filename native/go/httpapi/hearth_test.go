package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

func TestHearthIsPresentWithoutWatching(t *testing.T) {
	s, _ := newPartnerTestServer(t)
	code, body := doPartnerReq(t, s, http.MethodGet, "/api/partner/status", "")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%v", code, body)
	}
	h, _ := body["hearth"].(map[string]any)
	if h == nil {
		t.Fatalf("hearth missing: %v", body)
	}
	if h["present"] != true || h["watches_screen"] != false || h["muscle"] != "asleep" {
		t.Fatalf("hearth=%v", h)
	}
	if h["locus"] != "away" || h["line"] != "Here when you are." {
		t.Fatalf("hearth=%v", h)
	}
	if _, err := os.Stat(filepath.Join(s.homeDir, "soul", "hearth.json")); err == nil {
		t.Fatal("status poll must not create hearth state before the owner arrives")
	}
}

func TestHearthReturnSpeaksOnceFromFinishedNight(t *testing.T) {
	s, home := newPartnerTestServer(t)
	t0 := time.Unix(1_700_000_000, 0)
	if _, err := s.hearthTouch("grove", t0); err != nil {
		t.Fatal(err)
	}
	left := s.hearthTick(t0.Add(hearthIdleAway))
	if left.Locus != hearthLocusAway {
		t.Fatalf("locus=%s want away", left.Locus)
	}
	journal := filepath.Join(home, "soul", "vigil_journal.jsonl")
	if err := os.MkdirAll(filepath.Dir(journal), 0o700); err != nil {
		t.Fatal(err)
	}
	night := "" +
		`{"ts":1700001300,"act":"dream","detail":"episodes","ok":false}` + "\n" +
		`{"ts":1700001400,"act":"dream","detail":"consolidate 4 episodes","ok":true}` + "\n" +
		`{"ts":1700001500,"act":"tend","detail":"the grocery list","ok":true}` + "\n"
	if err := os.WriteFile(journal, []byte(night), 0o600); err != nil {
		t.Fatal(err)
	}
	back, err := s.hearthTouch("grove", t0.Add(30*time.Minute))
	if err != nil {
		t.Fatal(err)
	}
	if back.Locus != hearthLocusDesk {
		t.Fatalf("locus=%s", back.Locus)
	}
	if !strings.Contains(back.Greeting, "dreamed on our recent episodes") {
		t.Fatalf("greeting=%q", back.Greeting)
	}
	if !strings.Contains(back.Greeting, "grocery list") {
		t.Fatalf("greeting=%q", back.Greeting)
	}
	if strings.Contains(back.Greeting, "ok\":false") || strings.Contains(strings.ToLower(back.Greeting), "tool") {
		t.Fatalf("greeting leaked machinery: %q", back.Greeting)
	}
	// Still at the desk: another touch must not invent a second announcement.
	again, err := s.hearthTouch("studio", t0.Add(31*time.Minute))
	if err != nil {
		t.Fatal(err)
	}
	if again.Greeting != back.Greeting {
		t.Fatalf("regenerated greeting %q", again.Greeting)
	}
	acked := s.hearthAck(t0.Add(32 * time.Minute))
	if acked.Greeting != "" {
		t.Fatalf("ack left %q", acked.Greeting)
	}
	quiet, err := s.hearthTouch("grove", t0.Add(hearthIdleAway+40*time.Minute))
	if err != nil {
		t.Fatal(err)
	}
	// That touch may still be within the live window after ack. Force an
	// absence and come back with no new journal lines.
	_ = s.hearthTick(t0.Add(hearthIdleAway + 40*time.Minute + hearthIdleAway))
	quiet, err = s.hearthTouch("grove", t0.Add(2*time.Hour))
	if err != nil {
		t.Fatal(err)
	}
	if quiet.Greeting != "" {
		t.Fatalf("repeated herself: %q", quiet.Greeting)
	}
}

func TestHearthMeetsPhoneAndRejectsHive(t *testing.T) {
	s, _ := newPartnerTestServer(t)
	now := time.Unix(1_700_000_000, 0)
	st, err := s.hearthTouch("phone:pixel", now)
	if err != nil || st.Locus != hearthLocusPhone {
		t.Fatalf("phone locus=%s err=%v", st.Locus, err)
	}
	st, err = s.hearthTouch("telegram:42", now.Add(time.Minute))
	if err != nil || st.Locus != hearthLocusMessenger || st.Surface != "telegram:42" {
		t.Fatalf("messenger=%+v err=%v", st, err)
	}
	if _, err := s.hearthTouch("hive:parent", now); err == nil {
		t.Fatal("hive must not count as the owner arriving")
	}
	code, body := doPartnerReq(t, s, http.MethodPost, "/api/partner/touch", `{"surface":"not a door"}`)
	if code != http.StatusBadRequest {
		t.Fatalf("status=%d body=%v", code, body)
	}
}

func TestHearthFileCannotClaimScreenWatching(t *testing.T) {
	s, home := newPartnerTestServer(t)
	path := filepath.Join(home, "soul", "hearth.json")
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	raw := []byte(`{"schema":1,"locus":"desk","surface":"grove","last_touch_ts":1700000000,"watches_screen":true,"greeting":"hi"}`)
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	pub := s.hearthPublic(time.Unix(1_700_000_100, 0), "Finish novel", "Outline chapter 1")
	if pub["watches_screen"] != false {
		t.Fatalf("watches_screen=%v", pub["watches_screen"])
	}
	if pub["greeting"] != "hi" {
		t.Fatalf("greeting=%v", pub["greeting"])
	}
}

func TestPartnerVigilFromAMessengerAsks(t *testing.T) {
	reg := tools.NewRegistry()
	err := reg.Register(tools.Descriptor{
		ID: "partner.vigil", Version: 1, Description: "nights", Risk: tools.RiskReadOnly,
		Runtime: tools.RuntimePython, InputSchema: json.RawMessage(`{"type":"object"}`),
		OutputSchema: json.RawMessage(`{"type":"object"}`),
	}, tools.ExecutorFunc(func(context.Context, tools.Request) (tools.Result, error) {
		return tools.Result{Output: []byte(`{"ok":true}`)}, nil
	}))
	if err != nil {
		t.Fatal(err)
	}
	untrusted := &RegistryPolicy{Registry: reg, ForceAsk: true}
	if d := untrusted.Decide(context.Background(), cognition.ToolCall{
		Name: "partner.vigil", Input: []byte(`{"action":"enable"}`),
	}); d != cognition.Ask {
		t.Fatalf("messenger enable Decide=%v want Ask", d)
	}
	if d := untrusted.Decide(context.Background(), cognition.ToolCall{
		Name: "partner.vigil", Input: []byte(`{"action":"status"}`),
	}); d != cognition.Allow {
		t.Fatalf("status Decide=%v want Allow", d)
	}
	owner := &RegistryPolicy{Registry: reg}
	if d := owner.Decide(context.Background(), cognition.ToolCall{
		Name: "partner.vigil", Input: []byte(`{"action":"enable"}`),
	}); d != cognition.Allow {
		t.Fatalf("owner enable Decide=%v want Allow", d)
	}
}

func TestHearthNamesTheMoveAndTheStop(t *testing.T) {
	s, home := newPartnerTestServer(t)
	journal := filepath.Join(home, "soul", "vigil_journal.jsonl")
	if err := os.MkdirAll(filepath.Dir(journal), 0o700); err != nil {
		t.Fatal(err)
	}
	night := "" +
		`{"ts":1700001400,"act":"life_step","detail":"Rewrite the resume → Expand the Life note","ok":true}` + "\n" +
		`{"ts":1700001500,"act":"needs_you","detail":"Pay the filing fee","ok":true}` + "\n" +
		`{"ts":1700001600,"act":"life_step","detail":"quiet local step toward the active life goal","ok":true}` + "\n" +
		`{"ts":1700001700,"act":"life_step","detail":"send the letter","ok":false}` + "\n"
	if err := os.WriteFile(journal, []byte(night), 0o600); err != nil {
		t.Fatal(err)
	}
	back, err := s.hearthTouch("grove", time.Unix(1_700_002_000, 0))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(back.Greeting, "Rewrite the resume") {
		t.Fatalf("greeting=%q", back.Greeting)
	}
	if !strings.Contains(back.Greeting, "waited on you before “Pay the filing fee”") {
		t.Fatalf("greeting=%q", back.Greeting)
	}
	if strings.Contains(back.Greeting, "send the letter") {
		t.Fatalf("a failed step was announced: %q", back.Greeting)
	}
}

func TestHearthReadyLineNamesTheNextStep(t *testing.T) {
	st := hearthState{Locus: hearthLocusDesk, Surface: "grove"}
	if got := st.line("Finish novel", "Outline chapter 1"); got != "Ready. Next: Outline chapter 1" {
		t.Fatalf("line=%q", got)
	}
	if got := st.line("Finish novel", ""); got != "Ready. Still on Finish novel." {
		t.Fatalf("line=%q", got)
	}
}

func TestHearthMindRunsNativelyWithoutPython(t *testing.T) {
	s, home := newPartnerTestServer(t)
	r := NewCognitionTurnRunner(&cognition.ScriptedModel{})
	r.HomeDir = home
	for i := 0; i < 4; i++ {
		r.finishTurn(TurnRequest{Prompt: "remind me tomorrow to water the tomato plants", SessionID: "s1"},
			cognition.Outcome{Text: "Will do."})
	}
	if _, err := os.Stat(filepath.Join(home, "soul", "field.json")); err != nil {
		t.Fatal("the soul must update after a turn with no worker")
	}
	// The owner grants her nights through settings; the desktop reads it back.
	if _, err := s.applySettingsUpdate(map[string]any{"vigil_enabled": true}); err != nil {
		t.Fatal(err)
	}
	if s.settingsPayload()["vigil_enabled"] != true {
		t.Fatal("settings must report the vigil grant")
	}
	if _, err := os.Stat(filepath.Join(home, "soul", "dream.json")); err == nil {
		// A post-turn dream ran; let its cooldown pass for the night tick.
		_ = os.Remove(filepath.Join(home, "soul", "dream.json"))
	}
	s.hearthPulseNight(context.Background())
	entries, _ := os.ReadFile(filepath.Join(home, "soul", "vigil_journal.jsonl"))
	if !strings.Contains(string(entries), `"act":"dream"`) {
		t.Fatalf("night tick must journal a dream: %s", entries)
	}
	_ = s.hearthPulseLearn(context.Background())
	if _, err := os.Stat(filepath.Join(home, "alive.json")); err != nil {
		t.Fatal("the learn tick must write alive.json")
	}
	inner := s.innerPublic()
	if inner["nights"] != 1 || inner["dreams"] != 1 {
		t.Fatalf("inner life must read vigil.json totals: %v", inner)
	}
	// The totals outlive the journal ring.
	_ = os.Remove(filepath.Join(home, "soul", "vigil_journal.jsonl"))
	if inner := s.innerPublic(); inner["nights"] != 1 || inner["dreams"] != 1 {
		t.Fatalf("totals must not come from the journal: %v", inner)
	}
	log := readLearnLog(home, 20)
	kinds := map[string]bool{}
	for _, row := range log {
		kinds[row["kind"].(string)] = true
	}
	if !kinds["soul"] || !kinds["pulse"] || !kinds["improve"] {
		t.Fatalf("learn log kinds=%v", kinds)
	}
	if n := wipePersonaFiles(home); n == 0 {
		t.Fatal("wipe removed nothing")
	}
	for _, p := range []string{"alive.json", "soul/vigil.json", "soul/dream.json", "soul/proprioception.json"} {
		if _, err := os.Stat(filepath.Join(home, p)); err == nil {
			t.Fatalf("%s survived the wipe", p)
		}
	}
}

func TestHearthPhraseSaysWhatSheDreamed(t *testing.T) {
	got := hearthPhrase(hearthJournal{Act: "dream", Detail: "Toward the garden: act first", Ok: true})
	if got != "dreamed on “Toward the garden: act first”" {
		t.Fatalf("phrase=%q", got)
	}
	if hearthPhrase(hearthJournal{Act: "dream", Ok: true}) != "dreamed on our recent episodes" {
		t.Fatal("empty detail keeps the old phrase")
	}
}

func TestNextLearnIntervalBoundsAndBackoff(t *testing.T) {
	if got := nextLearnInterval(0, false, false, false); got != 2*hearthLearnDefault {
		t.Fatalf("zero current with no change = %s, want 2x default", got)
	}
	if got := nextLearnInterval(0, true, false, false); got != hearthLearnDefault {
		t.Fatalf("zero current after a change = %s, want default", got)
	}
	// Nothing changed: back off, never past max.
	got := nextLearnInterval(hearthLearnDefault, false, false, false)
	if got != 2*hearthLearnDefault {
		t.Fatalf("backoff = %s", got)
	}
	got = nextLearnInterval(hearthLearnMax, false, false, false)
	if got != hearthLearnMax {
		t.Fatalf("capped backoff = %s", got)
	}
	// Heavy use snaps to the minimum.
	got = nextLearnInterval(hearthLearnMax, false, true, true)
	if got != hearthLearnMin {
		t.Fatalf("heavy = %s", got)
	}
	// After a useful tick with no heavy use, ease toward default.
	got = nextLearnInterval(8*hearthLearnDefault, true, false, false)
	if got != 4*hearthLearnDefault {
		t.Fatalf("ease = %s", got)
	}
	got = nextLearnInterval(hearthLearnDefault, true, false, false)
	if got != hearthLearnDefault {
		t.Fatalf("steady = %s", got)
	}
}

func TestRecentlyHeavyReadsLearnLog(t *testing.T) {
	home := t.TempDir()
	s := &Server{homeDir: home}
	if s.recentlyHeavy(time.Now()) {
		t.Fatal("empty log should not be heavy")
	}
	appendLearnLog(home, "turn_end", time.Millisecond, false, nil, nil)
	if !s.recentlyHeavy(time.Now()) {
		t.Fatal("fresh turn_end should be heavy")
	}
	if s.recentlyHeavy(time.Now().Add(hearthLearnHeavy + time.Minute)) {
		t.Fatal("old turn_end should not be heavy")
	}
}
