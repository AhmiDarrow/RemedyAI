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
)

const partnerTestToken = "tok-partner-test-not-a-secret"

func newPartnerTestServer(t *testing.T) (*Server, string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	cfgPath := filepath.Join(home, "config.toml")
	cfg := "access_scope = \"project\"\nharness_mode = \"auto\"\napproval_mode = \"ask\"\n"
	if err := os.WriteFile(cfgPath, []byte(cfg), 0o600); err != nil {
		t.Fatal(err)
	}
	InvalidateConfigCache()

	s, err := New(Config{
		HomeDir: home,
		Token:   partnerTestToken,
		DBPath:  filepath.Join(home, "memory.db"),
		Version: "0.50.2-test",
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s, home
}

func doPartnerReq(t *testing.T, s *Server, method, path, body string) (int, map[string]any) {
	t.Helper()
	var r *http.Request
	if body != "" {
		r = httptest.NewRequest(method, path, strings.NewReader(body))
		r.Header.Set("Content-Type", "application/json")
	} else {
		r = httptest.NewRequest(method, path, nil)
	}
	r.Header.Set("Authorization", "Bearer "+partnerTestToken)
	r.Host = "127.0.0.1:7400"
	r.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, r)
	var out map[string]any
	if rr.Body.Len() > 0 {
		if err := json.Unmarshal(rr.Body.Bytes(), &out); err != nil {
			t.Fatalf("json decode status=%d body=%s err=%v", rr.Code, rr.Body.String(), err)
		}
	}
	return rr.Code, out
}

func TestPartnerStatusLean(t *testing.T) {
	s, _ := newPartnerTestServer(t)
	code, body := doPartnerReq(t, s, http.MethodGet, "/api/partner/status", "")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%v", code, body)
	}
	if body["version"] != "0.50.2-test" {
		t.Fatalf("version=%v", body["version"])
	}
	if body["pending_approvals"].(float64) != 0 {
		t.Fatalf("pending=%v", body["pending_approvals"])
	}
	if body["access_scope"] != "project" {
		t.Fatalf("scope=%v", body["access_scope"])
	}
	if body["approval_mode"] != "ask" {
		t.Fatalf("mode=%v", body["approval_mode"])
	}
}

func TestApprovalsListAndResolve(t *testing.T) {
	s, _ := newPartnerTestServer(t)
	sid := "sess-a"
	item := s.approvals.Enqueue("shell.exec", "echo hi", "shell", &sid, "")
	code, body := doPartnerReq(t, s, http.MethodGet, "/api/approvals?session_id="+sid, "")
	if code != http.StatusOK {
		t.Fatalf("list status=%d", code)
	}
	arr, _ := body["approvals"].([]any)
	if len(arr) != 1 {
		t.Fatalf("approvals=%v", body["approvals"])
	}
	code, body = doPartnerReq(t, s, http.MethodPost,
		"/api/approvals/"+item.ID+"/resolve",
		`{"approve":true,"scope":"session"}`)
	if code != http.StatusOK {
		t.Fatalf("resolve status=%d body=%v", code, body)
	}
	if body["status"] != "approved" {
		t.Fatalf("status=%v", body["status"])
	}
	hint, _ := body["hint"].(string)
	if !strings.Contains(hint, "can run now without asking again") {
		t.Fatalf("hint=%q want fingerprint-ready wording", hint)
	}
	if body["resumed"] == true {
		t.Fatalf("resumed=%v want false when no turn waiter", body["resumed"])
	}
	code, body = doPartnerReq(t, s, http.MethodGet, "/api/approvals", "")
	if code != http.StatusOK {
		t.Fatalf("list2 status=%d", code)
	}
	arr, _ = body["approvals"].([]any)
	if len(arr) != 0 {
		t.Fatalf("still pending: %v", body)
	}
}

func TestResolveApprovalResumedHintWhenTurnWaiting(t *testing.T) {
	s, _ := newPartnerTestServer(t)
	sid := "sess-wait"
	item := s.approvals.Enqueue("shell.exec", `{"argv":["echo"]}`, "test", &sid, "run echo")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	waitDone := make(chan bool, 1)
	go func() {
		ok, err := s.approvals.WaitAll(ctx, []string{item.ID})
		if err != nil {
			waitDone <- false
			return
		}
		waitDone <- ok
	}()
	deadline := time.Now().Add(2 * time.Second)
	for !s.approvals.HasWaiter(item.ID) && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}
	if !s.approvals.HasWaiter(item.ID) {
		t.Fatal("waiter not registered")
	}
	code, body := doPartnerReq(t, s, http.MethodPost,
		"/api/approvals/"+item.ID+"/resolve",
		`{"approve":true,"scope":"session"}`)
	if code != http.StatusOK {
		t.Fatalf("resolve status=%d body=%v", code, body)
	}
	if body["resumed"] != true {
		t.Fatalf("resumed=%v want true", body["resumed"])
	}
	hint, _ := body["hint"].(string)
	if !strings.Contains(hint, "continuing") {
		t.Fatalf("hint=%q want continuing wording", hint)
	}
	select {
	case ok := <-waitDone:
		if !ok {
			t.Fatal("WaitAll should succeed after approve")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("WaitAll did not unblock")
	}
}

func TestPlansLatestAndApprove(t *testing.T) {
	s, _ := newPartnerTestServer(t)
	sid := "chat-1"
	code, body := doPartnerReq(t, s, http.MethodPost, "/api/plans",
		`{"title":"Ship it","goal":"Ship","steps":["Draft","Build"],"session_id":"`+sid+`","status":"draft"}`)
	if code != http.StatusOK {
		t.Fatalf("create status=%d body=%v", code, body)
	}
	plan, _ := body["plan"].(map[string]any)
	id, _ := plan["id"].(string)
	if id == "" {
		t.Fatalf("plan id missing: %v", body)
	}
	code, body = doPartnerReq(t, s, http.MethodGet,
		"/api/plans/latest?session_id="+sid+"&actionable=1", "")
	if code != http.StatusOK {
		t.Fatalf("latest status=%d", code)
	}
	plan, _ = body["plan"].(map[string]any)
	if plan["id"] != id || plan["status"] != "draft" {
		t.Fatalf("latest=%v", body)
	}
	code, body = doPartnerReq(t, s, http.MethodPost,
		"/api/plans/"+id+"/status", `{"status":"approved"}`)
	if code != http.StatusOK {
		t.Fatalf("approve status=%d body=%v", code, body)
	}
	plan, _ = body["plan"].(map[string]any)
	if plan["status"] != "approved" {
		t.Fatalf("approved=%v", body)
	}
	// Other session must not see this plan.
	code, body = doPartnerReq(t, s, http.MethodGet,
		"/api/plans/latest?session_id=other", "")
	if code != http.StatusOK {
		t.Fatalf("other latest status=%d", code)
	}
	if body["plan"] != nil {
		t.Fatalf("leaked plan: %v", body)
	}
}

func TestGoalsCRUD(t *testing.T) {
	s, _ := newPartnerTestServer(t)
	code, body := doPartnerReq(t, s, http.MethodPost, "/api/goals",
		`{"title":"Finish novel"}`)
	if code != http.StatusOK {
		t.Fatalf("create status=%d body=%v", code, body)
	}
	id, _ := body["id"].(string)
	if id == "" || body["title"] != "Finish novel" {
		t.Fatalf("goal=%v", body)
	}
	code, body = doPartnerReq(t, s, http.MethodGet, "/api/goals", "")
	if code != http.StatusOK {
		t.Fatalf("list status=%d", code)
	}
	goals, _ := body["goals"].([]any)
	if len(goals) != 1 {
		t.Fatalf("goals=%v", body)
	}
	code, body = doPartnerReq(t, s, http.MethodPatch, "/api/goals/"+id,
		`{"next_action":"Outline chapter 1"}`)
	if code != http.StatusOK {
		t.Fatalf("patch status=%d body=%v", code, body)
	}
	if body["next_action"] != "Outline chapter 1" {
		t.Fatalf("patched=%v", body)
	}
	code, body = doPartnerReq(t, s, http.MethodGet, "/api/partner/status", "")
	if code != http.StatusOK {
		t.Fatalf("partner status=%d", code)
	}
	if body["open_goals"].(float64) < 1 {
		t.Fatalf("open_goals=%v", body["open_goals"])
	}
	if body["active_goal"] != "Finish novel" {
		t.Fatalf("active_goal=%v", body["active_goal"])
	}
	code, _ = doPartnerReq(t, s, http.MethodDelete, "/api/goals/"+id, "")
	if code != http.StatusOK {
		t.Fatalf("delete status=%d", code)
	}
}

func TestLifeTaskCurrentAndAct(t *testing.T) {
	s, _ := newPartnerTestServer(t)
	sid := "life-sess"
	item := s.approvals.Enqueue("life_drive", "Remedy will book a flight.", sensitivePrefix+" — plan", &sid, "Remedy will book a flight.")
	code, body := doPartnerReq(t, s, http.MethodGet, "/api/life-tasks/current?session_id="+sid, "")
	if code != http.StatusOK {
		t.Fatalf("current status=%d body=%v", code, body)
	}
	task, _ := body["task"].(map[string]any)
	if task == nil || task["kind"] != "plan_gate" {
		t.Fatalf("task=%v", body)
	}
	appr, _ := body["approval"].(map[string]any)
	if appr == nil || appr["id"] != item.ID {
		t.Fatalf("approval=%v", body)
	}
	code, body = doPartnerReq(t, s, http.MethodPost, "/api/life-tasks/act",
		`{"action":"yes","session_id":"`+sid+`","approval_id":"`+item.ID+`"}`)
	if code != http.StatusOK {
		t.Fatalf("act status=%d body=%v", code, body)
	}
	if body["ok"] != true || body["action"] != "yes" {
		t.Fatalf("act body=%v", body)
	}
	code, body = doPartnerReq(t, s, http.MethodGet, "/api/approvals?session_id="+sid, "")
	arr, _ := body["approvals"].([]any)
	if len(arr) != 0 {
		t.Fatalf("approval still pending after yes: %v", body)
	}
}

func TestCheckpointsLatestEmpty(t *testing.T) {
	s, home := newPartnerTestServer(t)
	code, body := doPartnerReq(t, s, http.MethodGet, "/api/checkpoints/latest?session_id=x", "")
	if code != http.StatusOK {
		t.Fatalf("status=%d", code)
	}
	if body["checkpoint"] != nil {
		t.Fatalf("expected nil checkpoint: %v", body)
	}
	// Seed one checkpoint on disk.
	cp := &turnCheckpoint{
		ID:        "cpabc1234567",
		SessionID: partnerStrPtr("x"),
		Title:     "Mid-build",
		Done:      []string{"wrote file"},
		NextSteps: []string{"run tests"},
		Reason:    "auto",
		CreatedAt: utcNowISO(),
	}
	if err := s.saveCheckpoint(cp); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(home, "checkpoints", "cpabc1234567.json")); err != nil {
		t.Fatal(err)
	}
	code, body = doPartnerReq(t, s, http.MethodGet, "/api/checkpoints/latest?session_id=x", "")
	if code != http.StatusOK {
		t.Fatalf("latest status=%d", code)
	}
	got, _ := body["checkpoint"].(map[string]any)
	if got == nil || got["title"] != "Mid-build" {
		t.Fatalf("checkpoint=%v", body)
	}
}

func partnerStrPtr(s string) *string { return &s }
