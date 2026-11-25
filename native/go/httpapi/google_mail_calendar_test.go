package httpapi

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

type gmcFake struct {
	mu       sync.Mutex
	reqs     []*http.Request
	bodies   []string
	handler  func(w http.ResponseWriter, r *http.Request)
	tokenHit int
}

// newGMCFake points the Gmail, Calendar and token endpoints at one server and
// returns a temp home holding fake tokens.
func newGMCFake(t *testing.T, access string, expires float64, refresh string, h func(w http.ResponseWriter, r *http.Request)) (*gmcFake, string) {
	t.Helper()
	t.Setenv("REMEDY_GOOGLE_OAUTH_CLIENT_ID", "fake-client.example.com")
	t.Setenv("REMEDY_GOOGLE_OAUTH_CLIENT_SECRET", "fake-secret")
	f := &gmcFake{handler: h}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		f.mu.Lock()
		f.reqs = append(f.reqs, r)
		f.bodies = append(f.bodies, string(b))
		f.mu.Unlock()
		if r.URL.Path == "/token" {
			f.mu.Lock()
			f.tokenHit++
			f.mu.Unlock()
		}
		f.handler(w, r)
	}))
	t.Cleanup(srv.Close)
	oldG, oldC, oldT := googleGmailAPI, googleCalendarAPI, googleTokenEndpoint
	googleGmailAPI, googleCalendarAPI, googleTokenEndpoint = srv.URL+"/gmail", srv.URL+"/cal", srv.URL+"/token"
	t.Cleanup(func() { googleGmailAPI, googleCalendarAPI, googleTokenEndpoint = oldG, oldC, oldT })
	home := t.TempDir()
	if access != "" || refresh != "" {
		if err := saveGoogleTokens(home, googleTokens{AccessToken: access, RefreshToken: refresh, ExpiresAt: expires, Email: "me@example.com"}); err != nil {
			t.Fatal(err)
		}
	}
	return f, home
}

func gmcJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}

func gmcFarFuture() float64 { return float64(time.Now().Unix() + 3600) }

func TestGoogleMailListDefaultsAndQuery(t *testing.T) {
	f, home := newGMCFake(t, "fake-access", gmcFarFuture(), "", func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/gmail/users/me/messages":
			gmcJSON(w, map[string]any{"messages": []any{map[string]any{"id": "m1"}, map[string]any{"id": "m2"}}})
		case r.URL.Path == "/gmail/users/me/messages/m1":
			gmcJSON(w, map[string]any{"snippet": "only a snippet", "payload": map[string]any{"headers": []any{
				map[string]any{"name": "From", "value": "a@example.com"},
				map[string]any{"name": "Subject", "value": "Hi"},
				map[string]any{"name": "Date", "value": "Mon, 1 Jan 2026"},
			}}})
		case r.URL.Path == "/gmail/users/me/messages/m2":
			gmcJSON(w, map[string]any{"snippet": "multi", "payload": map[string]any{"mimeType": "multipart/alternative",
				"parts": []any{map[string]any{"mimeType": "text/plain"}}}})
		}
	})
	out, err := toolMailListGoogle(context.Background(), home, map[string]any{})
	if err != nil {
		t.Fatal(err)
	}
	if out["ok"] != true || out["count"] != 2 || out["query"] != "in:inbox" {
		t.Fatalf("out=%v", out)
	}
	q := f.reqs[0].URL.Query()
	if q.Get("maxResults") != "12" || q.Get("q") != "in:inbox" {
		t.Fatalf("query %v", q)
	}
	if got := f.reqs[1].URL.Query(); got.Get("format") != "metadata" || got.Get("metadataHeaders") != "From,Subject,Date" {
		t.Fatalf("meta %v", got)
	}
	if f.reqs[0].Header.Get("Authorization") != "Bearer fake-access" {
		t.Fatal("bearer")
	}
	rows := out["messages"].([]any)
	m1, m2 := rows[0].(map[string]any), rows[1].(map[string]any)
	if m1["from"] != "a@example.com" || m1["subject"] != "Hi" || m1["snippet"] != "only a snippet" || m1["date"] != "Mon, 1 Jan 2026" {
		t.Fatalf("m1=%v", m1)
	}
	if m2["subject"] != "(no subject)" || m2["snippet"] != "multi" || m2["from"] != "" {
		t.Fatalf("m2=%v", m2)
	}

	out, err = toolMailListGoogle(context.Background(), home, map[string]any{"query": "  from:b@example.com ", "limit": float64(500)})
	if err != nil || out["query"] != "from:b@example.com" {
		t.Fatalf("%v %v", out, err)
	}
	last := f.reqs[len(f.reqs)-3].URL.Query()
	if last.Get("maxResults") != "50" || last.Get("q") != "from:b@example.com" {
		t.Fatalf("clamp %v", last)
	}
}

func TestGoogleMailListMetaFailureIsUnavailable(t *testing.T) {
	_, home := newGMCFake(t, "fake-access", gmcFarFuture(), "", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/gmail/users/me/messages" {
			gmcJSON(w, map[string]any{"messages": []any{map[string]any{"id": "m1"}}})
			return
		}
		http.Error(w, "boom", 500)
	})
	out, err := toolMailListGoogle(context.Background(), home, nil)
	if err != nil {
		t.Fatal(err)
	}
	m := out["messages"].([]any)[0].(map[string]any)
	if m["subject"] != "(unavailable)" || m["id"] != "m1" {
		t.Fatalf("%v", m)
	}
}

func TestGoogleMailSendRawMIME(t *testing.T) {
	f, home := newGMCFake(t, "fake-access", gmcFarFuture(), "", func(w http.ResponseWriter, r *http.Request) {
		gmcJSON(w, map[string]any{"id": "sent1", "threadId": "th1"})
	})
	out, err := toolMailSendGoogle(context.Background(), home, map[string]any{"to": "bob@example.com", "subject": "Hello", "text": "Grüße"})
	if err != nil {
		t.Fatal(err)
	}
	if out["ok"] != true || out["message_id"] != "sent1" || out["thread_id"] != "th1" || out["message"] != "Sent to bob@example.com: Hello" {
		t.Fatalf("%v", out)
	}
	if len(f.reqs) != 1 || f.reqs[0].Method != "POST" || f.reqs[0].URL.Path != "/gmail/users/me/messages/send" {
		t.Fatalf("reqs %d", len(f.reqs))
	}
	var body map[string]string
	if err := json.Unmarshal([]byte(f.bodies[0]), &body); err != nil {
		t.Fatal(err)
	}
	raw, err := base64.URLEncoding.DecodeString(body["raw"])
	if err != nil {
		t.Fatal(err)
	}
	msg := string(raw)
	for _, want := range []string{"Content-Type: text/plain; charset=\"utf-8\"\n", "MIME-Version: 1.0\n",
		"Content-Transfer-Encoding: base64\n", "To: bob@example.com\n", "Subject: Hello\n\n"} {
		if !strings.Contains(msg, want) {
			t.Fatalf("missing %q in %q", want, msg)
		}
	}
	payload := msg[strings.Index(msg, "\n\n")+2:]
	dec, err := base64.StdEncoding.DecodeString(strings.ReplaceAll(payload, "\n", ""))
	if err != nil || string(dec) != "Grüße" {
		t.Fatalf("body %q %v", dec, err)
	}
}

func TestGoogleMailSendValidation(t *testing.T) {
	f, home := newGMCFake(t, "fake-access", gmcFarFuture(), "", func(w http.ResponseWriter, r *http.Request) { gmcJSON(w, map[string]any{}) })
	for _, in := range []map[string]any{
		{"to": "nobody", "body": "x"},
		{"to": "a@example.com"},
		{"to": "a@example.com\nBcc: b@example.com", "body": "x"},
		{"to": "a@example.com", "subject": "x\r\nBcc: b@example.com"},
	} {
		out, err := toolMailSendGoogle(context.Background(), home, in)
		if err != nil || out["ok"] != false {
			t.Fatalf("%v -> %v %v", in, out, err)
		}
	}
	if len(f.reqs) != 0 {
		t.Fatal("must not call Google on invalid input")
	}
}

func TestGoogleCalendarListWindow(t *testing.T) {
	long := strings.Repeat("é", 600)
	f, home := newGMCFake(t, "fake-access", gmcFarFuture(), "", func(w http.ResponseWriter, r *http.Request) {
		gmcJSON(w, map[string]any{"items": []any{
			map[string]any{"id": "e1", "summary": "Standup", "start": map[string]any{"dateTime": "2026-01-02T09:00:00Z"},
				"end": map[string]any{"dateTime": "2026-01-02T09:15:00Z"}, "location": "Room 1", "description": long},
			map[string]any{"id": "e2", "start": map[string]any{"date": "2026-01-03"}, "end": map[string]any{"date": "2026-01-04"}},
		}})
	})
	out, err := toolCalendarListGoogle(context.Background(), home, map[string]any{"time_min": "2026-01-01T00:00:00Z", "time_max": "2026-01-08T00:00:00Z"})
	if err != nil {
		t.Fatal(err)
	}
	q := f.reqs[0].URL.Query()
	if f.reqs[0].URL.Path != "/cal/calendars/primary/events" || q.Get("timeMin") != "2026-01-01T00:00:00Z" ||
		q.Get("timeMax") != "2026-01-08T00:00:00Z" || q.Get("singleEvents") != "true" ||
		q.Get("orderBy") != "startTime" || q.Get("maxResults") != "40" {
		t.Fatalf("%v", f.reqs[0].URL)
	}
	if out["count"] != 2 || out["days"] != 7 {
		t.Fatalf("%v", out)
	}
	rows := out["events"].([]any)
	e1, e2 := rows[0].(map[string]any), rows[1].(map[string]any)
	if e1["title"] != "Standup" || e1["location"] != "Room 1" || len([]rune(e1["description"].(string))) != 500 {
		t.Fatalf("%v", e1)
	}
	if e2["title"] != "(no title)" || e2["start"] != "2026-01-03" || e2["end"] != "2026-01-04" {
		t.Fatalf("%v", e2)
	}

	// Default window: days clamped, timeMin/timeMax computed.
	if _, err = toolCalendarListGoogle(context.Background(), home, map[string]any{"days": float64(999)}); err != nil {
		t.Fatal(err)
	}
	q = f.reqs[1].URL.Query()
	lo, e1p := time.Parse(time.RFC3339Nano, q.Get("timeMin"))
	hi, e2p := time.Parse(time.RFC3339Nano, q.Get("timeMax"))
	if e1p != nil || e2p != nil || hi.Sub(lo).Round(time.Hour) != 60*24*time.Hour {
		t.Fatalf("window %v %v", q.Get("timeMin"), q.Get("timeMax"))
	}
}

func TestGoogleCalendarCreateRequest(t *testing.T) {
	f, home := newGMCFake(t, "fake-access", gmcFarFuture(), "", func(w http.ResponseWriter, r *http.Request) {
		gmcJSON(w, map[string]any{"id": "ev9", "summary": "Lunch"})
	})
	out, err := toolCalendarCreateGoogle(context.Background(), home, map[string]any{
		"title": " Lunch ", "start": "2026-02-03", "end": "2026-02-03T13:00:00+02:00", "description": " eat "})
	if err != nil {
		t.Fatal(err)
	}
	var body map[string]any
	if err := json.Unmarshal([]byte(f.bodies[0]), &body); err != nil {
		t.Fatal(err)
	}
	want := map[string]any{"summary": "Lunch", "description": "eat",
		"start": map[string]any{"date": "2026-02-03"}, "end": map[string]any{"dateTime": "2026-02-03T13:00:00+02:00"}}
	a, _ := json.Marshal(body)
	b, _ := json.Marshal(want)
	if string(a) != string(b) {
		t.Fatalf("body %s", a)
	}
	if out["ok"] != true || out["id"] != "ev9" || out["title"] != "Lunch" || out["start"] != "2026-02-03" ||
		out["message"] != "Created calendar event: Lunch" {
		t.Fatalf("%v", out)
	}
	// Naive datetime gets a local offset appended.
	if got := googleEventTime("2026-02-03T10:00:00")["dateTime"].(string); len(got) != len("2026-02-03T10:00:00")+6 || !googleHasTZ(got) {
		t.Fatalf("tz %q", got)
	}
	// Missing field: no call.
	n := len(f.reqs)
	out, _ = toolCalendarCreateGoogle(context.Background(), home, map[string]any{"title": "x", "start": "2026-02-03"})
	if out["ok"] != false || len(f.reqs) != n {
		t.Fatalf("%v", out)
	}
}

func TestGoogleExpiredTokenRefreshesOnce(t *testing.T) {
	f, home := newGMCFake(t, "old-access", float64(time.Now().Unix()-100), "fake-refresh", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/token" {
			gmcJSON(w, map[string]any{"access_token": "new-access", "expires_in": 3600})
			return
		}
		if r.URL.Path == "/gmail/users/me/messages" {
			gmcJSON(w, map[string]any{"messages": []any{map[string]any{"id": "m1"}}})
			return
		}
		gmcJSON(w, map[string]any{"snippet": "s"})
	})
	out, err := toolMailListGoogle(context.Background(), home, map[string]any{})
	if err != nil || out["count"] != 1 {
		t.Fatalf("%v %v", out, err)
	}
	if f.tokenHit != 1 {
		t.Fatalf("token endpoint hit %d times", f.tokenHit)
	}
	for _, r := range f.reqs {
		if r.URL.Path != "/token" && r.Header.Get("Authorization") != "Bearer new-access" {
			t.Fatalf("stale bearer on %s", r.URL.Path)
		}
	}
	form := f.bodies[0]
	if !strings.Contains(form, "grant_type=refresh_token") || !strings.Contains(form, "refresh_token=fake-refresh") {
		t.Fatalf("form %s", form)
	}
	if tok := loadGoogleTokens(home); tok.AccessToken != "new-access" || tok.RefreshToken != "fake-refresh" || tok.ExpiresAt < gmcFarFuture()-60 {
		t.Fatalf("saved %+v", tok)
	}
}

func TestGoogleRefreshFailure(t *testing.T) {
	f, home := newGMCFake(t, "old-access", float64(time.Now().Unix()-100), "fake-refresh", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(400)
		gmcJSON(w, map[string]any{"error": "invalid_grant"})
	})
	out, err := toolCalendarCreateGoogle(context.Background(), home, map[string]any{"title": "x", "start": "2026-01-01", "end": "2026-01-02"})
	if err == nil || !strings.Contains(err.Error(), "invalid_grant") || out != nil {
		t.Fatalf("%v %v", out, err)
	}
	if len(f.reqs) != 1 {
		t.Fatalf("API must not be called after failed refresh: %d", len(f.reqs))
	}
}

func TestGoogleMailCalendarNotConnected(t *testing.T) {
	f, home := newGMCFake(t, "", 0, "", func(w http.ResponseWriter, r *http.Request) {})
	for _, kind := range []string{"mail", "calendar"} {
		if googleMailCalendarAvailable(home, kind) {
			t.Fatal(kind)
		}
	}
	if googleMailCalendarAvailable(home, "other") {
		t.Fatal("unknown kind")
	}
	ml, err := toolMailListGoogle(context.Background(), home, nil)
	if err != nil || ml["ok"] != false || ml["count"] != 0 || ml["error"] != googleNoMailMsg {
		t.Fatalf("%v %v", ml, err)
	}
	ms, _ := toolMailSendGoogle(context.Background(), home, map[string]any{"to": "a@example.com", "body": "x"})
	cl, _ := toolCalendarListGoogle(context.Background(), home, nil)
	cc, _ := toolCalendarCreateGoogle(context.Background(), home, map[string]any{"title": "x", "start": "a", "end": "b"})
	if ms["ok"] != false || cl["ok"] != false || cc["ok"] != false || cl["error"] != googleNoCalMsg || len(f.reqs) != 0 {
		t.Fatalf("%v %v %v", ms, cl, cc)
	}
	// Connected once a refresh token exists, even with no live access token.
	if err := saveGoogleTokens(home, googleTokens{RefreshToken: "fake-refresh"}); err != nil {
		t.Fatal(err)
	}
	if !googleMailCalendarAvailable(home, "mail") || !googleMailCalendarAvailable(home, "calendar") {
		t.Fatal("refresh token should count as connected")
	}
}

func TestGoogleMailCalendarHTTPErrors(t *testing.T) {
	_, home := newGMCFake(t, "fake-access", gmcFarFuture(), "", func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasPrefix(r.URL.Path, "/gmail"):
			w.WriteHeader(404)
			gmcJSON(w, map[string]any{"error": map[string]any{"message": "Requested entity was not found."}})
		case r.URL.Query().Get("orderBy") != "":
			w.WriteHeader(403)
			gmcJSON(w, map[string]any{"error": map[string]any{"message": "Google Calendar API has not been used in project 1 before"}})
		default:
			w.WriteHeader(503)
			_, _ = w.Write([]byte("upstream down"))
		}
	})
	_, err := toolMailListGoogle(context.Background(), home, nil)
	if err == nil || err.Error() != "Gmail API 404: Requested entity was not found." {
		t.Fatalf("%v", err)
	}
	_, err = toolCalendarListGoogle(context.Background(), home, nil)
	if err == nil || !strings.Contains(err.Error(), "Google Calendar API is disabled") || !strings.Contains(err.Error(), "Detail: Google Calendar API has not been used") {
		t.Fatalf("%v", err)
	}
	_, err = toolCalendarCreateGoogle(context.Background(), home, map[string]any{"title": "x", "start": "2026-01-01", "end": "2026-01-02"})
	if err == nil || err.Error() != "Google Calendar API 503: upstream down" {
		t.Fatalf("%v", err)
	}
}

func TestGoogleMailInsufficientScope(t *testing.T) {
	_, home := newGMCFake(t, "fake-access", gmcFarFuture(), "", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(403)
		gmcJSON(w, map[string]any{"error": map[string]any{"message": "Request had insufficient authentication scopes."}})
	})
	_, err := toolMailSendGoogle(context.Background(), home, map[string]any{"to": "a@example.com", "body": "x"})
	if err == nil || !strings.HasPrefix(err.Error(), "Google hasn't granted this permission yet.") {
		t.Fatalf("%v", err)
	}
}
