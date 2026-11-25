package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/sysres"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// roomyPC is a machine with plenty of free memory and 8 threads.
func roomyPC() sysres.Snapshot {
	return sysres.Snapshot{LogicalCPUs: 8, TotalBytes: 16 << 30, AvailableBytes: 10 << 30}
}

func newHeadTestServer(t *testing.T, runner TurnRunner) *Server {
	t.Helper()
	home := t.TempDir()
	s, err := New(Config{
		Token:      "test-token-not-a-secret-16",
		DBPath:     filepath.Join(t.TempDir(), "memory.db"),
		HomeDir:    home,
		TurnRunner: runner,
	})
	if err != nil {
		t.Fatal(err)
	}
	s.partner.readRes = roomyPC
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func waitFor(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s", what)
}

// ── governor ────────────────────────────────────────────────────────────

func TestPartnerCapacityFamily(t *testing.T) {
	gib := uint64(1 << 30)
	cases := []struct {
		name       string
		snap       sysres.Snapshot
		local      bool
		ownerBusy  bool
		busy       int
		wantSlots  int
		wantFree   int
		wantPaused bool
	}{
		{"cloud, 8 threads", sysres.Snapshot{LogicalCPUs: 8, TotalBytes: 16 * gib, AvailableBytes: 8 * gib}, false, false, 0, 2, 2, false},
		{"cloud, 32 threads caps at 3", sysres.Snapshot{LogicalCPUs: 32, TotalBytes: 64 * gib, AvailableBytes: 40 * gib}, false, false, 1, 3, 2, false},
		{"cloud, 2 threads still gets 1", sysres.Snapshot{LogicalCPUs: 2, TotalBytes: 8 * gib, AvailableBytes: 4 * gib}, false, false, 0, 1, 1, false},
		{"cloud, owner busy does not pause", sysres.Snapshot{LogicalCPUs: 8, TotalBytes: 16 * gib, AvailableBytes: 8 * gib}, false, true, 0, 2, 2, false},
		{"local model is one slot", sysres.Snapshot{LogicalCPUs: 32, TotalBytes: 64 * gib, AvailableBytes: 40 * gib}, true, false, 0, 1, 1, false},
		{"local model waits for the owner", sysres.Snapshot{LogicalCPUs: 32, TotalBytes: 64 * gib, AvailableBytes: 40 * gib}, true, true, 0, 1, 0, true},
		{"low absolute memory pauses", sysres.Snapshot{LogicalCPUs: 8, TotalBytes: 8 * gib, AvailableBytes: gib}, false, false, 0, 2, 0, true},
		{"low fraction pauses even with GBs free", sysres.Snapshot{LogicalCPUs: 8, TotalBytes: 128 * gib, AvailableBytes: 10 * gib}, false, false, 0, 2, 0, true},
		{"unknown memory does not pause", sysres.Snapshot{LogicalCPUs: 8}, false, false, 0, 2, 2, false},
		{"full slots", sysres.Snapshot{LogicalCPUs: 8, TotalBytes: 16 * gib, AvailableBytes: 8 * gib}, false, false, 2, 2, 0, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c := computePartnerCapacity(tc.snap, tc.local, tc.ownerBusy, tc.busy, 0)
			if c.Slots != tc.wantSlots || c.Free() != tc.wantFree || c.Paused != tc.wantPaused {
				t.Fatalf("slots=%d free=%d paused=%v (%s), want %d/%d/%v", c.Slots, c.Free(), c.Paused, c.Reason, tc.wantSlots, tc.wantFree, tc.wantPaused)
			}
			if strings.TrimSpace(c.Sentence()) == "" {
				t.Fatal("capacity must explain itself in words")
			}
		})
	}
}

// ── schedules ───────────────────────────────────────────────────────────

func TestNextRunAfterSchedules(t *testing.T) {
	loc := time.FixedZone("test", -5*3600)
	fri := time.Date(2026, 11, 20, 9, 0, 0, 0, loc) // a Friday, 09:00
	cases := []struct {
		name string
		sc   partnerSchedule
		want time.Time
	}{
		{"daily later today", partnerSchedule{Kind: "daily", At: "17:30"}, time.Date(2026, 11, 20, 17, 30, 0, 0, loc)},
		{"daily already passed → tomorrow", partnerSchedule{Kind: "daily", At: "07:00"}, time.Date(2026, 11, 21, 7, 0, 0, 0, loc)},
		{"daily at the exact minute → tomorrow", partnerSchedule{Kind: "daily", At: "09:00"}, time.Date(2026, 11, 21, 9, 0, 0, 0, loc)},
		{"weekdays skip the weekend", partnerSchedule{Kind: "weekdays", At: "07:00"}, time.Date(2026, 11, 23, 7, 0, 0, 0, loc)},
		{"weekly picks the next listed day", partnerSchedule{Kind: "weekly", At: "10:00", Days: []string{"wed", "sun"}}, time.Date(2026, 11, 22, 10, 0, 0, 0, loc)},
		{"every adds the interval", partnerSchedule{Kind: "every", EveryMinutes: 90}, fri.Add(90 * time.Minute)},
		{"once in the past never runs", partnerSchedule{Kind: "once", OnceAt: "2026-01-01T00:00:00Z"}, time.Time{}},
		{"once in the future runs then", partnerSchedule{Kind: "once", OnceAt: "2026-12-01T12:00:00Z"}, time.Date(2026, 12, 1, 12, 0, 0, 0, time.UTC)},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := nextRunAfter(tc.sc, fri, loc)
			if !got.Equal(tc.want) {
				t.Fatalf("got %v want %v", got, tc.want)
			}
		})
	}
}

func TestNormalizeScheduleRejectsBadInput(t *testing.T) {
	bad := []partnerSchedule{
		{Kind: "every", EveryMinutes: 5},
		{Kind: "daily", At: "25:00"},
		{Kind: "daily", At: "7am"},
		{Kind: "weekly", At: "08:00"},
		{Kind: "weekly", At: "08:00", Days: []string{"funday"}},
		{Kind: "once", OnceAt: "tomorrow"},
		{Kind: "cron"},
	}
	for _, sc := range bad {
		if _, err := normalizeSchedule(sc); err == nil {
			t.Errorf("schedule %+v should be refused", sc)
		}
	}
	good, err := normalizeSchedule(partnerSchedule{Kind: "Weekly", At: "08:00", Days: []string{"Monday", "mon", "FRI"}})
	if err != nil || len(good.Days) != 2 || good.Kind != "weekly" {
		t.Fatalf("weekly normalize: %+v %v", good, err)
	}
}

func TestInQuietHoursWrapsMidnight(t *testing.T) {
	at := func(h, m int) time.Time { return time.Date(2026, 1, 1, h, m, 0, 0, time.UTC) }
	cases := []struct {
		t          time.Time
		start, end string
		want       bool
	}{
		{at(23, 0), "22:00", "07:00", true},
		{at(3, 0), "22:00", "07:00", true},
		{at(7, 0), "22:00", "07:00", false},
		{at(12, 0), "22:00", "07:00", false},
		{at(13, 0), "12:00", "14:00", true},
		{at(14, 0), "12:00", "14:00", false},
		{at(13, 0), "", "", false},
		{at(13, 0), "09:00", "09:00", false},
		{at(13, 0), "junk", "07:00", false},
	}
	for _, tc := range cases {
		if got := inQuietHours(tc.t, tc.start, tc.end); got != tc.want {
			t.Errorf("%s in [%s,%s) = %v, want %v", tc.t.Format("15:04"), tc.start, tc.end, got, tc.want)
		}
	}
}

// ── status line ─────────────────────────────────────────────────────────

func TestParsePartnerStatusFamily(t *testing.T) {
	cases := []struct {
		reply, state, why string
	}{
		{"Did it.\nSTATUS: done — booked the table", "done", "booked the table"},
		{"Did it.\n**STATUS:** waiting - need your card", "waiting", "need your card"},
		{"x\nstatus: blocked: site is down", "blocked", "site is down"},
		{"  STATUS: Working — still reading", "working", "still reading"},
		{"STATUS: working — a\nmore\nSTATUS: done — b", "done", "b"},
		{"The status quo is done for.", "working", ""},
		{"No line at all", "working", ""},
		{"STATUS: finished", "working", ""}, // unknown word is not a state
	}
	for _, tc := range cases {
		state, why := parsePartnerStatus(tc.reply)
		if state != tc.state || why != tc.why {
			t.Errorf("%q → (%q,%q), want (%q,%q)", tc.reply, state, why, tc.state, tc.why)
		}
	}
	if got := stripPartnerStatus("Hello\nSTATUS: done — x"); got != "Hello" {
		t.Fatalf("strip: %q", got)
	}
}

// ── autopilot rules ─────────────────────────────────────────────────────

func TestPartnerAutopilotDue(t *testing.T) {
	now := time.Date(2026, 11, 19, 12, 0, 0, 0, time.UTC)
	ago := func(m int) string { return now.Add(-time.Duration(m) * time.Minute).Format(time.RFC3339) }
	base := partnerGoalLink{GoalID: "g", SessionID: "s", Autopilot: true, EveryMinutes: 60}
	with := func(f func(*partnerGoalLink)) partnerGoalLink { l := base; f(&l); return l }
	cases := []struct {
		name string
		link partnerGoalLink
		msgs int
		want bool
	}{
		{"never ran", base, 0, true},
		{"off", with(func(l *partnerGoalLink) { l.Autopilot = false }), 0, false},
		{"no session", with(func(l *partnerGoalLink) { l.SessionID = "" }), 0, false},
		{"ran recently", with(func(l *partnerGoalLink) { l.LastRunAt = ago(30) }), 0, false},
		{"interval passed", with(func(l *partnerGoalLink) { l.LastRunAt = ago(61) }), 0, true},
		{"already queued", with(func(l *partnerGoalLink) { l.State = partnerStateQueued }), 0, false},
		// A stale "working" left on disk must not block the next step
		// (dedupe by key already prevents a second live run).
		{"stale working does not block", with(func(l *partnerGoalLink) { l.State = partnerStateWorking; l.LastRunAt = ago(61) }), 0, true},
		{"waiting, owner silent", with(func(l *partnerGoalLink) { l.State = partnerStateWaiting; l.OwnerSeenCount = 4; l.LastRunAt = ago(600) }), 4, false},
		{"waiting, owner replied", with(func(l *partnerGoalLink) { l.State = partnerStateWaiting; l.OwnerSeenCount = 4; l.LastRunAt = ago(600) }), 6, true},
		{"stalled twice waits 4x", with(func(l *partnerGoalLink) { l.Stalls = 2; l.LastRunAt = ago(200) }), 0, false},
		{"stalled twice after 4x runs", with(func(l *partnerGoalLink) { l.Stalls = 2; l.LastRunAt = ago(241) }), 0, true},
		{"stall backoff caps at 8x", with(func(l *partnerGoalLink) { l.Stalls = 9; l.LastRunAt = ago(481) }), 0, true},
		{"floor of 30 minutes", with(func(l *partnerGoalLink) { l.EveryMinutes = 1; l.LastRunAt = ago(10) }), 0, false},
	}
	for _, tc := range cases {
		if got := partnerAutopilotDue(tc.link, now, tc.msgs); got != tc.want {
			t.Errorf("%s: got %v want %v", tc.name, got, tc.want)
		}
	}
}

// ── trust + binding ─────────────────────────────────────────────────────

func TestPartnerOriginTrust(t *testing.T) {
	for _, o := range []string{"partner:child:s1", "partner:routine:rt-1", "partner:goal:g1", "PARTNER:child:x"} {
		if !OriginIsOwner(o) {
			t.Errorf("%q should run as owner-authorized", o)
		}
	}
	for _, o := range []string{"telegram:42", "hive:s1", "phone:dev", "partnerX:child", "child:partner:"} {
		if OriginIsOwner(o) {
			t.Errorf("%q must stay untrusted", o)
		}
	}
	if !isChildOrigin("partner:child:abc") || isChildOrigin("partner:routine:abc") {
		t.Fatal("child origin detection")
	}
}

func TestBindToolInputPartnerToolsGetCallerBinding(t *testing.T) {
	call := cognition.ToolCall{Name: "children.spawn", Input: []byte(`{"task":"x","session_id":"evil","caller_origin":"desktop"}`)}
	got := bindToolInput(call, toolBinding{SessionID: "s-real", Origin: "partner:child:head"})
	var m map[string]any
	if err := json.Unmarshal(got.Input, &m); err != nil {
		t.Fatal(err)
	}
	if m["session_id"] != "s-real" || m["caller_origin"] != "partner:child:head" {
		t.Fatalf("binding not enforced: %v", m)
	}
	// The underscore spelling a provider may send resolves to the same tool.
	call2 := cognition.ToolCall{Name: "children_spawn", Input: []byte(`{"task":"x"}`)}
	got2 := bindToolInput(call2, toolBinding{SessionID: "s2", Origin: ""})
	if !strings.Contains(string(got2.Input), `"caller_origin":""`) {
		t.Fatalf("model-supplied origin must be replaced even when empty: %s", got2.Input)
	}
	if !hiveDeniedTool("children.spawn") || !hiveDeniedTool("routines_create") || !hiveDeniedTool("owner.notify") {
		t.Fatal("hive foragers must not hold Remedy's head tools")
	}
}

// ── end to end with a stub model ────────────────────────────────────────

func TestChildSpawnRunsAndReportsToRemedy(t *testing.T) {
	runner := &stubRunner{tokens: []string{"@@text:Found three flights under $300.\n", "@@text:STATUS: done — cheapest is Tuesday"}}
	s := newHeadTestServer(t, runner)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.startPartnerDispatcher(ctx)

	head, err := s.partnerEnsureHeadSession()
	if err != nil {
		t.Fatal(err)
	}
	child, err := s.partnerSpawnChild(childSpec{Label: "Flights", Task: "Find flights to Denver", ParentSessionID: head, Origin: "desktop"})
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, "child to finish", func() bool {
		s.partner.mu.Lock()
		defer s.partner.mu.Unlock()
		c := s.partner.st.Children[child.SessionID]
		return c != nil && c.State == partnerStateDone
	})
	req, _ := runner.lastCall()
	if req.Origin != "partner:child:"+head {
		t.Fatalf("child origin = %q", req.Origin)
	}
	if !strings.Contains(req.Prompt, "Find flights to Denver") || !strings.Contains(req.Prompt, "frugal") {
		t.Fatalf("child brief missing job or frugal rule: %q", req.Prompt)
	}
	waitFor(t, "report in head", func() bool {
		msgs, _ := s.sessions.ListMessages(head, 10, 0)
		for _, m := range msgs {
			if c, _ := m.Content.(string); strings.Contains(c, "Flights") && strings.Contains(c, "reported back") && !strings.Contains(c, "STATUS:") {
				return true
			}
		}
		return false
	})
	s.partner.mu.Lock()
	last := s.partner.st.Children[child.SessionID].LastUpdate
	s.partner.mu.Unlock()
	if last != "cheapest is Tuesday" {
		t.Fatalf("last update = %q", last)
	}
}

func TestChildCannotSpawnGrandchild(t *testing.T) {
	s := newHeadTestServer(t, &stubRunner{})
	if _, err := s.partnerSpawnChild(childSpec{Task: "x", Origin: "partner:child:head"}); err == nil {
		t.Fatal("a child must not start children")
	}
}

func TestPartnerQueueDedupesAndCaps(t *testing.T) {
	s := newHeadTestServer(t, &stubRunner{})
	h := s.partner
	if ok, _, _ := h.enqueue(partnerRunRequest{Key: "goal:a", SessionID: "s1"}); !ok {
		t.Fatal("first enqueue")
	}
	h.enqueue(partnerRunRequest{Key: "goal:a", SessionID: "s1"})
	h.mu.Lock()
	n := len(h.queue)
	h.mu.Unlock()
	if n != 1 {
		t.Fatalf("same key queued %d times", n)
	}
	for i := 0; i < partnerQueueMax+5; i++ {
		h.enqueue(partnerRunRequest{Key: "k" + string(rune('a'+i)), SessionID: "s"})
	}
	if ok, _, reason := h.enqueue(partnerRunRequest{Key: "overflow"}); ok || reason == "" {
		t.Fatal("a full queue must refuse with a reason")
	}
}

func TestDispatchRespectsPausedGovernor(t *testing.T) {
	runner := &stubRunner{tokens: []string{"@@text:ok"}}
	s := newHeadTestServer(t, runner)
	s.partner.readRes = func() sysres.Snapshot {
		return sysres.Snapshot{LogicalCPUs: 8, TotalBytes: 16 << 30, AvailableBytes: 512 << 20}
	}
	head, _ := s.partnerEnsureHeadSession()
	s.partner.enqueue(partnerRunRequest{Key: "t", Kind: "routine", SessionID: head, Prompt: "x", Origin: "partner:routine:t"})
	s.partnerDispatch(context.Background())
	runner.mu.Lock()
	calls := len(runner.calls)
	runner.mu.Unlock()
	if calls != 0 {
		t.Fatal("low memory must hold background work")
	}
	if c := s.partnerCapacity(); !c.Paused || !strings.Contains(c.Reason, "memory") {
		t.Fatalf("capacity should explain the pause: %+v", c)
	}
}

func TestReminderRoutineUsesNoModel(t *testing.T) {
	runner := &stubRunner{tokens: []string{"@@text:should not run"}}
	s := newHeadTestServer(t, runner)
	r, err := s.partner.addRoutine(partnerRoutine{
		Title: "Water plants", Prompt: "Water the plants", Mode: "remind",
		Schedule: partnerSchedule{Kind: "every", EveryMinutes: 60},
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.partnerFireRoutine(*r); err != nil {
		t.Fatal(err)
	}
	s.partnerDispatch(context.Background())
	runner.mu.Lock()
	calls := len(runner.calls)
	runner.mu.Unlock()
	if calls != 0 {
		t.Fatalf("a reminder must not call the model (calls=%d)", calls)
	}
	s.partner.mu.Lock()
	defer s.partner.mu.Unlock()
	last := s.partner.st.Activity[len(s.partner.st.Activity)-1]
	if !strings.Contains(last.Title, "Water plants") || s.partner.st.Routines[0].Runs != 1 {
		t.Fatalf("reminder not recorded: %+v", last)
	}
}

func TestDueRoutinesAdvanceOnce(t *testing.T) {
	s := newHeadTestServer(t, &stubRunner{})
	r, err := s.partner.addRoutine(partnerRoutine{Prompt: "check", Schedule: partnerSchedule{Kind: "every", EveryMinutes: 60}})
	if err != nil {
		t.Fatal(err)
	}
	later := time.Now().Add(5 * time.Hour) // slept through several slots
	if due := s.partner.dueRoutines(later); len(due) != 1 || due[0].ID != r.ID {
		t.Fatalf("one catch-up run expected, got %d", len(due))
	}
	if due := s.partner.dueRoutines(later); len(due) != 0 {
		t.Fatal("a routine must not fire twice for one slot")
	}
}

func TestHeartbeatSweepIsLocalAndQuietWhenUnchanged(t *testing.T) {
	runner := &stubRunner{tokens: []string{"@@text:no"}}
	s := newHeadTestServer(t, runner)
	now := time.Now()
	s.partner.mu.Lock()
	s.partner.st.Heartbeat.Enabled = true
	s.partner.st.Heartbeat.QuietStart, s.partner.st.Heartbeat.QuietEnd = "", ""
	s.partner.st.Children["c1"] = &partnerChild{SessionID: "c1", Label: "Taxes", State: partnerStateWaiting, CreatedAt: "x"}
	s.partner.mu.Unlock()

	sw, changed := s.partnerHeartbeatSweep(now, false)
	if !changed || len(sw.NeedsYou) != 1 || !strings.Contains(sw.sentence(), "Taxes") {
		t.Fatalf("first sweep should report Taxes: %+v changed=%v", sw, changed)
	}
	if _, changed := s.partnerHeartbeatSweep(now.Add(2*time.Hour), false); changed {
		t.Fatal("an unchanged house must not produce another note")
	}
	if _, changed := s.partnerHeartbeatSweep(now.Add(2*time.Hour+time.Minute), false); changed {
		t.Fatal("interval not elapsed since the last sweep")
	}
	runner.mu.Lock()
	calls := len(runner.calls)
	runner.mu.Unlock()
	if calls != 0 {
		t.Fatal("the heartbeat must never call the model")
	}
}

func TestPartnerHeadEndpoint(t *testing.T) {
	s := newHeadTestServer(t, &stubRunner{})
	if _, err := s.partner.addRoutine(partnerRoutine{Prompt: "brief me", Schedule: partnerSchedule{Kind: "weekdays", At: "07:00"}}); err != nil {
		t.Fatal(err)
	}
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/partner/head", nil)
	s.handlePartnerHead(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d", rec.Code)
	}
	var body map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"head", "capacity", "capacity_note", "children", "routines", "activity", "heartbeat", "channels"} {
		if _, ok := body[k]; !ok {
			t.Errorf("overview missing %q", k)
		}
	}
	routines := body["routines"].([]any)
	if len(routines) != 1 || routines[0].(map[string]any)["when"] != "Weekdays at 07:00" {
		t.Fatalf("routine row: %v", routines)
	}
}

func TestPartnerChannelMustHaveMessagedFirst(t *testing.T) {
	s := newHeadTestServer(t, &stubRunner{})
	put := func(body string) int {
		rec := httptest.NewRecorder()
		s.handlePartnerChannelPut(rec, httptest.NewRequest(http.MethodPut, "/api/partner/channel", strings.NewReader(body)))
		return rec.Code
	}
	if put(`{"channel":"telegram","chat_id":"99"}`) != http.StatusBadRequest {
		t.Fatal("an unknown chat must be refused")
	}
	s.partnerNoteMessenger("telegram", "99", "me", "hi", "msg:1")
	if put(`{"channel":"telegram","chat_id":"99"}`) != http.StatusOK {
		t.Fatal("a chat that messaged Remedy can be chosen")
	}
}

func TestPartnerStateSurvivesRestartWithoutPhantomWork(t *testing.T) {
	home := t.TempDir()
	h := newPartnerHub(home)
	h.mu.Lock()
	h.st.Children["c"] = &partnerChild{SessionID: "c", Label: "x", State: partnerStateWorking}
	h.st.Goals["g"] = &partnerGoalLink{GoalID: "g", State: partnerStateQueued, Autopilot: true}
	h.saveLocked()
	h.mu.Unlock()
	h2 := newPartnerHub(home)
	if h2.st.Children["c"].State != partnerStateIdle || h2.st.Goals["g"].State != partnerStateIdle || !h2.st.Goals["g"].Autopilot {
		t.Fatalf("restart state: %+v %+v", h2.st.Children["c"], h2.st.Goals["g"])
	}
}

func TestStoppingAChildShowsStopped(t *testing.T) {
	hold := make(chan struct{})
	runner := &stubRunner{tokens: []string{"@@text:still going"}, hold: hold}
	s := newHeadTestServer(t, runner)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	defer close(hold)
	s.startPartnerDispatcher(ctx)
	child, err := s.partnerSpawnChild(childSpec{Label: "Long job", Task: "wait", Origin: "desktop"})
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, "child to start", func() bool { return s.claims.IsClaimed(child.SessionID) })
	if !s.partnerStopChild(child.SessionID) {
		t.Fatal("stop refused")
	}
	waitFor(t, "run to wind down", func() bool {
		s.partner.mu.Lock()
		defer s.partner.mu.Unlock()
		_, running := s.partner.running[child.SessionID]
		return !running
	})
	s.partner.mu.Lock()
	state := s.partner.st.Children[child.SessionID].State
	s.partner.mu.Unlock()
	if state != partnerStateStopped {
		t.Fatalf("state after stop = %q", state)
	}
}

func TestPartnerToolsThroughRegistry(t *testing.T) {
	s := newHeadTestServer(t, &stubRunner{})
	reg := tools.NewRegistry(tools.AuthorizerFunc(runtimeLocalAuthorizer))
	if err := RegisterPartnerTools(reg, func() *Server { return s }); err != nil {
		t.Fatal(err)
	}
	exec := func(id, input string) map[string]any {
		t.Helper()
		desc, err := reg.Latest(id)
		if err != nil {
			t.Fatal(err)
		}
		out, err := reg.Execute(context.Background(), tools.Request{ToolID: id, Version: 1, Input: json.RawMessage(input), CapabilityToken: RuntimeCapabilityToken(desc)})
		if err != nil {
			t.Fatalf("%s: %v", id, err)
		}
		var body map[string]any
		_ = json.Unmarshal(out.Output, &body)
		return body
	}
	list := exec("children.list", `{}`)
	if list["ok"] != true || list["room"] == "" {
		t.Fatalf("children.list must report room on this PC: %v", list)
	}
	refused := exec("children.spawn", `{"task":"x","session_id":"s","caller_origin":"partner:child:head"}`)
	if refused["ok"] != false || !strings.Contains(refused["error"].(string), "cannot start children") {
		t.Fatalf("grandchild must be refused in words: %v", refused)
	}
	// Her face reads her own life: counted from the journal, never estimated.
	if err := os.MkdirAll(filepath.Dir(s.facePath("soul/x")), 0o700); err != nil {
		t.Fatal(err)
	}
	journal := `{"ts":1,"act":"dream","detail":"a","ok":true}` + "\n" + `{"ts":2,"act":"tidy","detail":"b","ok":true}` + "\n" + `{"ts":3,"act":"dream","detail":"c","ok":false}` + "\n"
	if err := os.WriteFile(s.facePath("soul/vigil_journal.jsonl"), []byte(journal), 0o600); err != nil {
		t.Fatal(err)
	}
	inner := s.innerPublic()
	if inner["nights"] != 2 || inner["dreams"] != 1 || inner["activity"] != "" {
		t.Fatalf("inner life must count finished acts only: %v", inner)
	}
	s.innerAct.Store("learning")
	if s.innerPublic()["activity"] != "learning" {
		t.Fatal("inner activity must report what she is doing now")
	}
	s.innerAct.Store("")
	// The seed is the setup: same people and install, same face; any change, another.
	a := faceSeedFor("Ahmi", "Remedy", "salt-1", 0)
	if a != faceSeedFor("Ahmi", "Remedy", "salt-1", 0) {
		t.Fatal("face seed must be stable")
	}
	for _, other := range []uint64{
		faceSeedFor("Joe", "Remedy", "salt-1", 0), faceSeedFor("Ahmi", "Reme", "salt-1", 0),
		faceSeedFor("Ahmi", "Remedy", "salt-2", 0), faceSeedFor("Ahmi", "Remedy", "salt-1", 1),
		faceSeedFor("AhmiR", "emedy", "salt-1", 0),
	} {
		if other == a {
			t.Fatal("a different owner, name, install or generation must change the face")
		}
	}
	// Regenerate keeps the old face; restore brings it back.
	cur, prev := s.facePath("face.seed"), s.facePath("face.seed.prev")
	if err := os.WriteFile(cur, []byte("42"), 0o600); err != nil {
		t.Fatal(err)
	}
	if got := exec("face.regenerate", `{}`); got["ok"] != true {
		t.Fatalf("face.regenerate: %v", got)
	}
	if b, _ := os.ReadFile(prev); string(b) != "42" {
		t.Fatalf("old face not kept: %q", b)
	}
	if b, _ := os.ReadFile(cur); string(b) == "42" || len(b) == 0 {
		t.Fatalf("face not regenerated: %q", b)
	}
	if got := exec("face.restore", `{}`); got["ok"] != true {
		t.Fatalf("face.restore: %v", got)
	}
	if b, _ := os.ReadFile(cur); string(b) != "42" {
		t.Fatalf("old face not restored: %q", b)
	}
	rt := exec("routines.create", `{"what":"Take the bins out","mode":"remind","schedule":{"kind":"weekly","at":"19:00","days":["sun"]}}`)
	if rt["ok"] != true {
		t.Fatalf("routines.create: %v", rt)
	}
	// A too-frequent routine is refused by the schema before any code runs.
	rdesc, _ := reg.Latest("routines.create")
	if _, err := reg.Execute(context.Background(), tools.Request{ToolID: "routines.create", Version: 1,
		Input:           json.RawMessage(`{"what":"x","schedule":{"kind":"every","every_minutes":5}}`),
		CapabilityToken: RuntimeCapabilityToken(rdesc)}); err == nil {
		t.Fatal("a too-frequent routine must be refused")
	}
	if g := exec("goals.add", `{"title":"Learn Spanish","autopilot":true}`); g["ok"] != true || g["autopilot"] != true {
		t.Fatalf("goals.add with autopilot: %v", g)
	}
	open := exec("grove.open", `{"surface":"studio"}`)
	cmd, _ := open["command"].(map[string]any)
	params, _ := cmd["params"].(map[string]any)
	if params["target"] != "studio" {
		t.Fatalf("grove.open must use the app's switch_surface target param: %v", open)
	}
	// Head tools are visible to the model; the runtime-bound fields are not.
	for _, id := range []string{"children.spawn", "routines.create", "owner.notify"} {
		desc, _ := reg.Latest(id)
		schema := string(tools.ModelInputSchema(desc.InputSchema))
		if strings.Contains(schema, "caller_origin") || strings.Contains(schema, "session_id") {
			t.Fatalf("%s leaks bound fields to the model: %s", id, schema)
		}
	}
}

func TestPartnerApprovalSummariesArePlain(t *testing.T) {
	cases := map[string]string{
		`{"task":"Compare three phone plans","label":"Phone plans"}`: "start a side job “Phone plans”",
	}
	for in, want := range cases {
		if got := plainToolApprovalSummary("children.spawn", in, ""); !strings.Contains(got, want) {
			t.Fatalf("got %q want ~%q", got, want)
		}
	}
	got := plainToolApprovalSummary("routines_create", `{"what":"Water plants","mode":"remind","schedule":{"kind":"weekdays","at":"07:00"}}`, "")
	if !strings.Contains(got, "reminder") || !strings.Contains(got, "weekdays at 07:00") {
		t.Fatalf("routine summary: %q", got)
	}
	for _, name := range []string{"children.spawn", "routines.create", "goals.add", "goals.update", "owner.notify", "children.stop"} {
		got := plainToolApprovalSummary(name, `{}`, "")
		if strings.Contains(got, name) || strings.Contains(got, "{") || !strings.HasPrefix(got, "Remedy wants to") {
			t.Errorf("%s summary is not plain: %q", name, got)
		}
	}
}

func TestAutopilotRunRestsAfterAProgressReport(t *testing.T) {
	runner := &stubRunner{tokens: []string{"@@text:Shortlisted two hotels.\n", "@@text:STATUS: working — need one more"}}
	s := newHeadTestServer(t, runner)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.startPartnerDispatcher(ctx)
	g, err := s.addLifeGoal("Plan a trip", "", "", "", "", "test")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.partnerSetAutopilot(g.ID, true, 0); err != nil {
		t.Fatal(err)
	}
	waitFor(t, "first autopilot step", func() bool {
		s.partner.mu.Lock()
		defer s.partner.mu.Unlock()
		l := s.partner.st.Goals[g.ID]
		return l != nil && l.Runs == 1
	})
	s.partner.mu.Lock()
	l := *s.partner.st.Goals[g.ID]
	s.partner.mu.Unlock()
	if l.State != partnerStateIdle || l.LastUpdate != "need one more" || !l.Autopilot {
		t.Fatalf("after a progress report the goal rests with its update: %+v", l)
	}
	later, _ := time.Parse(time.RFC3339, l.LastRunAt)
	if !partnerAutopilotDue(l, later.Add(3*time.Hour), 99) {
		t.Fatal("a resting autopilot goal must be due again after its interval")
	}
}

func TestPartnerRunConcludedStopsReArmOnlyForBackgroundWork(t *testing.T) {
	report := "Here is the list.\nSTATUS: done — list ready"
	cases := []struct {
		origin, text string
		want         bool
	}{
		{"partner:child:h", report, true},
		{"partner:routine:rt-1", "x\n**STATUS:** waiting - need card", true},
		{"partner:goal:g", "Step taken.\nSTATUS: working — next: book", true},
		{"partner:child:h", "Still going, no report yet", false},
		{"partner:child:h", "The status quo is done.", false},
		{"", report, false},            // the owner's own turn
		{"desktop", report, false},     // ditto
		{"telegram:42", report, false}, // untrusted messenger text
	}
	for _, tc := range cases {
		if got := partnerRunConcluded(tc.origin, tc.text); got != tc.want {
			t.Errorf("origin=%q text=%q: got %v want %v", tc.origin, tc.text, got, tc.want)
		}
	}
}

func TestLeavingDemoMovesDemoConversationsOnly(t *testing.T) {
	s := newHeadTestServer(t, &stubRunner{})
	put := func(body string) {
		t.Helper()
		rec := httptest.NewRecorder()
		s.handlePutSettings(rec, httptest.NewRequest(http.MethodPut, "/api/settings", strings.NewReader(body)))
		if rec.Code != http.StatusOK {
			t.Fatalf("PUT settings %s: %d %s", body, rec.Code, rec.Body.String())
		}
	}
	mk := func(provider, model string) string {
		t.Helper()
		p, m := provider, model
		sess, err := s.sessions.Create(createSessionRequest{Title: provider, LLMProvider: &p, Model: &m})
		if err != nil {
			t.Fatal(err)
		}
		return sess.ID
	}
	put(`{"llm_provider":"demo","llm_model":"codestral-latest"}`)
	onDemo := mk("demo", "codestral-latest")
	onOpenAI := mk("openai", "gpt-x")
	put(`{"llm_provider":"xai","llm_model":"grok-4.5"}`)

	got, _, _ := s.sessions.Get(onDemo)
	if got.LLMProvider == nil || *got.LLMProvider != "xai" || got.Model == nil || *got.Model != "grok-4.5" {
		t.Fatalf("demo conversation should follow the new default: %+v", got)
	}
	kept, _, _ := s.sessions.Get(onOpenAI)
	if kept.LLMProvider == nil || *kept.LLMProvider != "openai" || *kept.Model != "gpt-x" {
		t.Fatalf("a deliberately chosen provider must be kept: %+v", kept)
	}
	// Choosing the demo again later is respected: a non-demo → non-demo
	// switch never touches it.
	again := mk("demo", "codestral-latest")
	put(`{"llm_provider":"openai","llm_model":"gpt-y"}`)
	if g, _, _ := s.sessions.Get(again); g.LLMProvider == nil || *g.LLMProvider != "demo" {
		t.Fatalf("only the move away from demo migrates: %+v", g)
	}
}
