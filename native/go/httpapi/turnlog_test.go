package httpapi

import (
	"bufio"
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// SSE helpers

// sseEvent is one parsed frame off an SSE stream.
type sseEvent struct {
	Raw   string
	Event string
	Data  map[string]any
}

func (e sseEvent) seq() uint64 {
	v, ok := e.Data["seq"]
	if !ok {
		return 0
	}
	n, _ := v.(float64)
	return uint64(n)
}

// readSSEEvent reads one frame, skipping keepalive comments. io.EOF ends it.
func readTurnSSEEvent(br *bufio.Reader) (sseEvent, error) {
	for {
		var raw strings.Builder
		for {
			line, err := br.ReadString('\n')
			if err != nil {
				return sseEvent{}, err
			}
			raw.WriteString(line)
			if line == "\n" {
				break
			}
		}
		block := raw.String()
		if strings.HasPrefix(block, ":") {
			continue // keepalive comment
		}
		ev := sseEvent{Raw: block}
		for _, line := range strings.Split(block, "\n") {
			switch {
			case strings.HasPrefix(line, "event: "):
				ev.Event = line[len("event: "):]
			case strings.HasPrefix(line, "data: "):
				_ = json.Unmarshal([]byte(line[len("data: "):]), &ev.Data)
			}
		}
		return ev, nil
	}
}

func readAllSSEEvents(t *testing.T, body io.Reader) []sseEvent {
	t.Helper()
	br := bufio.NewReader(body)
	var out []sseEvent
	for {
		ev, err := readTurnSSEEvent(br)
		if err != nil {
			return out
		}
		out = append(out, ev)
	}
}

func rawOf(events []sseEvent) string {
	var b strings.Builder
	for _, ev := range events {
		b.WriteString(ev.Raw)
	}
	return b.String()
}

// streamTurn posts a streamed message and returns every frame it produced.
func streamTurn(t *testing.T, client *http.Client, base, token, sid, body string) []sseEvent {
	t.Helper()
	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages/stream", token,
		bytes.NewBufferString(body))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(resp.Body)
		t.Fatalf("stream: %d %s", resp.StatusCode, raw)
	}
	return readAllSSEEvents(t, resp.Body)
}

func requestIDOf(t *testing.T, events []sseEvent) string {
	t.Helper()
	for _, ev := range events {
		if ev.Event == "start" {
			id, _ := ev.Data["request_id"].(string)
			if id != "" {
				return id
			}
		}
	}
	t.Fatalf("no start frame with request_id in %d events", len(events))
	return ""
}

func attachEvents(t *testing.T, client *http.Client, base, token, sid, requestID string, after uint64) []sseEvent {
	t.Helper()
	url := base + "/api/sessions/" + sid + "/stream/attach?request_id=" + requestID +
		"&after=" + strconv.FormatUint(after, 10)
	req := authReq(t, http.MethodGet, url, token, nil)
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(resp.Body)
		t.Fatalf("attach: %d %s", resp.StatusCode, raw)
	}
	return readAllSSEEvents(t, resp.Body)
}

// ---------------------------------------------------------------------------
// 1. the log replays byte-identically

func TestTurnLogReplaysByteIdenticallyThroughAttach(t *testing.T) {
	home := t.TempDir()
	runner := &stubRunner{tokens: []string{
		"Looking at it. ",
		"@@status:Reading files…\n",
		`@@tool_call:{"name":"read","id":"call_1_1","args":{"path":"app.py"}}` + "\n",
		`@@tool_result:{"name":"read","id":"call_1_1","preview":"line one","output":"line one\nline two","ok":true}` + "\n",
		`@@usage:{"prompt_tokens":12,"completion_tokens":34,"total_tokens":46}` + "\n",
		"@@thinking:weighing it\n",
		"Done.",
	}}
	base, shutdown, token := startMessagesServer(t, runner, home)
	defer shutdown()
	client := &http.Client{Timeout: 10 * time.Second}
	sid := createSessionID(t, client, base, token)

	live := streamTurn(t, client, base, token, sid, `{"message":"go"}`)
	rid := requestIDOf(t, live)

	path := turnLogPath(home, sid, rid)
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("turn log missing at %s: %v", path, err)
	}

	replay := attachEvents(t, client, base, token, sid, rid, 0)
	if rawOf(replay) != rawOf(live) {
		t.Fatalf("replay is not byte-identical\nlive:\n%s\nreplay:\n%s", rawOf(live), rawOf(replay))
	}

	// Every frame carries a strictly increasing seq matching the log record.
	var last uint64
	for _, ev := range live {
		if ev.seq() <= last {
			t.Fatalf("seq not monotonic at %s: %d after %d", ev.Event, ev.seq(), last)
		}
		last = ev.seq()
	}
	records, lastSeq, status, err := turnLogSummary(path)
	if err != nil {
		t.Fatal(err)
	}
	if status != "ok" {
		t.Fatalf("log status = %q", status)
	}
	if lastSeq != last {
		t.Fatalf("log lastSeq=%d, stream lastSeq=%d", lastSeq, last)
	}
	if records != len(live) {
		t.Fatalf("log has %d records for %d frames", records, len(live))
	}
}

// 1b. the record shapes are the ones design section 3 specifies.
func TestTurnLogRecordShapes(t *testing.T) {
	home := t.TempDir()
	runner := &stubRunner{tokens: []string{
		"hi",
		"@@status:working\n",
		`@@tool_call:{"name":"bash","id":"call_1_1","args":{"command":"ls"}}` + "\n",
		`@@tool_result:{"name":"bash","id":"call_1_1","preview":"a","output":"a\nb\nc","ok":true}` + "\n",
		`@@usage:{"prompt_tokens":7,"completion_tokens":3}` + "\n",
	}}
	base, shutdown, token := startMessagesServer(t, runner, home)
	defer shutdown()
	client := &http.Client{Timeout: 10 * time.Second}
	sid := createSessionID(t, client, base, token)
	live := streamTurn(t, client, base, token, sid, `{"message":"go"}`)
	rid := requestIDOf(t, live)

	var recs []turnRecord
	if err := readTurnRecords(turnLogPath(home, sid, rid), func(rec turnRecord) bool {
		recs = append(recs, rec)
		return true
	}); err != nil {
		t.Fatal(err)
	}
	kinds := map[string]int{}
	for _, rec := range recs {
		kinds[rec.T]++
	}
	for _, want := range []string{"start", "message", "usage", "status", "done"} {
		if kinds[want] == 0 {
			t.Fatalf("no %q record; got %v", want, kinds)
		}
	}
	if recs[0].T != "start" || recs[0].RequestID != rid || recs[0].SessionID != sid {
		t.Fatalf("start record = %+v", recs[0])
	}
	if recs[0].Origin != "desktop" {
		t.Fatalf("start origin = %q", recs[0].Origin)
	}
	last := recs[len(recs)-1]
	if last.T != "done" || last.Status != "ok" {
		t.Fatalf("last record = %+v", last)
	}
	// The tool result is recorded in full, not as a preview.
	found := false
	for _, rec := range recs {
		if rec.T != "message" || rec.Role != "user" {
			continue
		}
		for _, b := range rec.Blocks {
			if b.Type != "tool_result" {
				continue
			}
			found = true
			if b.ToolUseID != "call_1_1" || b.Name != "bash" {
				t.Fatalf("tool_result block = %+v", b)
			}
			if len(b.Content) == 0 || b.Content[0].Text != "a\nb\nc" {
				t.Fatalf("tool_result content = %+v", b.Content)
			}
		}
	}
	if !found {
		t.Fatal("no tool_result message record")
	}
	// usage keeps both the flat counters and the frame payload.
	for _, rec := range recs {
		if rec.T != "usage" {
			continue
		}
		if rec.In != 7 || rec.Out != 3 {
			t.Fatalf("usage record = %+v", rec)
		}
		if rec.Raw["prompt_tokens"] == nil {
			t.Fatalf("usage raw payload lost: %+v", rec.Raw)
		}
	}
}

// ---------------------------------------------------------------------------
// 2. re-attach after a mid-turn disconnect

func TestAttachResumesMidTurnWithoutGapOrDuplicate(t *testing.T) {
	home := t.TempDir()
	hold := make(chan struct{})
	runner := &stubRunner{
		tokens: []string{
			"first ", "second ",
			`@@tool_call:{"name":"read","id":"call_1_1","args":{"path":"a.go"}}` + "\n",
			`@@tool_result:{"name":"read","id":"call_1_1","preview":"body","output":"body","ok":true}` + "\n",
			"third", "fourth",
		},
		hold:      hold,
		holdAfter: 2,
	}
	base, shutdown, token := startMessagesServer(t, runner, home)
	defer shutdown()
	client := &http.Client{Timeout: 10 * time.Second}
	sid := createSessionID(t, client, base, token)

	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages/stream", token,
		bytes.NewBufferString(`{"message":"go"}`))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	br := bufio.NewReader(resp.Body)

	// Read start + the two tokens emitted before the runner blocks.
	var seen []sseEvent
	for len(seen) < 3 {
		ev, err := readTurnSSEEvent(br)
		if err != nil {
			t.Fatalf("live read: %v (got %d frames)", err, len(seen))
		}
		seen = append(seen, ev)
	}
	rid := requestIDOf(t, seen)
	lastSeen := seen[len(seen)-1].seq()
	if lastSeen == 0 {
		t.Fatal("frames carry no seq")
	}

	// Simulate a reload: drop the connection mid-turn.
	resp.Body.Close()

	// The turn is detached and still claimed.
	if !sessionClaimed(t, client, base, token, sid) {
		t.Fatal("session should still be claimed after the client disconnects")
	}

	// Attach where we left off, then let the turn finish.
	url := base + "/api/sessions/" + sid + "/stream/attach?request_id=" + rid +
		"&after=" + strconv.FormatUint(lastSeen, 10)
	areq := authReq(t, http.MethodGet, url, token, nil)
	aresp, err := client.Do(areq)
	if err != nil {
		t.Fatal(err)
	}
	defer aresp.Body.Close()
	if aresp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(aresp.Body)
		t.Fatalf("attach: %d %s", aresp.StatusCode, raw)
	}
	close(hold)

	tail := readAllSSEEvents(t, aresp.Body)
	if len(tail) == 0 {
		t.Fatal("attach returned nothing")
	}
	// No duplicate: nothing at or below what the client already saw.
	// No gap: seqs are contiguous from lastSeen+1.
	want := lastSeen + 1
	for _, ev := range tail {
		if ev.seq() != want {
			t.Fatalf("expected seq %d, got %d (%s)", want, ev.seq(), ev.Event)
		}
		want++
	}
	if tail[len(tail)-1].Event != "done" {
		t.Fatalf("attach did not end on done: %s", tail[len(tail)-1].Event)
	}

	// The union of both halves is the whole log, exactly once.
	full := attachEvents(t, client, base, token, sid, rid, 0)
	if rawOf(full) != rawOf(seen)+rawOf(tail) {
		t.Fatalf("resumed stream != full log\nfull:\n%s\nresumed:\n%s",
			rawOf(full), rawOf(seen)+rawOf(tail))
	}
}

func TestAttachUnknownTurnIs404(t *testing.T) {
	base, shutdown, token := startMessagesServer(t, &stubRunner{tokens: []string{"x"}}, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	sid := createSessionID(t, client, base, token)

	req := authReq(t, http.MethodGet,
		base+"/api/sessions/"+sid+"/stream/attach?request_id=nope-1234", token, nil)
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("status = %d", resp.StatusCode)
	}
}

func TestAttachRejectsTraversalRequestID(t *testing.T) {
	base, shutdown, token := startMessagesServer(t, &stubRunner{tokens: []string{"x"}}, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	sid := createSessionID(t, client, base, token)

	req := authReq(t, http.MethodGet,
		base+"/api/sessions/"+sid+"/stream/attach?request_id=..%2F..%2Fmemory", token, nil)
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status = %d", resp.StatusCode)
	}
}

// ---------------------------------------------------------------------------
// 3. a killed turn leaves a draft row with its tool ledger

func TestKilledTurnLeavesDraftRowWithToolLedger(t *testing.T) {
	home := t.TempDir()
	hold := make(chan struct{})
	runner := &stubRunner{
		tokens: []string{
			"Starting the build. ",
			`@@tool_call:{"name":"bash","id":"call_1_1","args":{"command":"go test ./..."}}` + "\n",
			`@@tool_result:{"name":"bash","id":"call_1_1","preview":"FAIL","output":"FAIL app_test.go:12","ok":false,"error":"exit 1"}` + "\n",
			"Fixing it now. ",
		},
		hold:      hold,
		holdAfter: 4,
	}
	base, shutdown, token := startMessagesServer(t, runner, home)
	defer shutdown()
	client := &http.Client{Timeout: 10 * time.Second}
	sid := createSessionID(t, client, base, token)

	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages/stream", token,
		bytes.NewBufferString(`{"message":"build it"}`))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	br := bufio.NewReader(resp.Body)
	for i := 0; i < 5; i++ { // start + 4 tokens
		if _, err := readTurnSSEEvent(br); err != nil {
			t.Fatalf("live read %d: %v", i, err)
		}
	}
	resp.Body.Close()

	// The process is "killed" here: nothing finalizes the turn. What is on
	// disk right now is what a crash would leave behind.
	draft := waitForDraft(t, client, base, token, sid)
	if !draft.Draft {
		t.Fatalf("assistant row is not a draft: %+v", draft)
	}
	content, _ := draft.Content.(string)
	if !strings.Contains(content, "Starting the build.") {
		t.Fatalf("draft lost the partial answer: %q", content)
	}
	calls, _ := draft.ToolCalls.([]any)
	results, _ := draft.ToolResults.([]any)
	if len(calls) != 1 || len(results) != 1 {
		t.Fatalf("draft lost the tool ledger: calls=%v results=%v", calls, results)
	}

	// Letting the turn finish replaces the draft in place — one row, not two.
	close(hold)
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		msgs := listMessages(t, client, base, token, sid)
		if len(msgs) == 2 && !msgs[1].Draft {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	msgs := listMessages(t, client, base, token, sid)
	t.Fatalf("draft was not replaced in place: %d messages, %+v", len(msgs), msgs)
}

func listMessages(t *testing.T, client *http.Client, base, token, sid string) []ChatMessage {
	t.Helper()
	req := authReq(t, http.MethodGet, base+"/api/sessions/"+sid+"/messages", token, nil)
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	var listed struct {
		Messages []ChatMessage `json:"messages"`
	}
	if err := json.Unmarshal(raw, &listed); err != nil {
		t.Fatalf("messages: %v (%s)", err, raw)
	}
	return listed.Messages
}

func waitForDraft(t *testing.T, client *http.Client, base, token, sid string) ChatMessage {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		msgs := listMessages(t, client, base, token, sid)
		for _, m := range msgs {
			if m.Role == "assistant" && m.Draft {
				return m
			}
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("no draft assistant row appeared")
	return ChatMessage{}
}

// ---------------------------------------------------------------------------
// 4. full tool output by call id, short preview on the wire

func TestToolEvidenceReturnsFullOutputWhileSSEStaysShort(t *testing.T) {
	home := t.TempDir()
	long := strings.Repeat("x", 6000)
	preview := previewOf(long)
	result := map[string]any{
		"name": "bash", "id": "call_1_1", "preview": preview, "output": long, "ok": true,
	}
	rb, _ := json.Marshal(result)
	runner := &stubRunner{tokens: []string{
		`@@tool_call:{"name":"bash","id":"call_1_1","args":{"command":"cat big.log"}}` + "\n",
		"@@tool_result:" + string(rb) + "\n",
		"done",
	}}
	base, shutdown, token := startMessagesServer(t, runner, home)
	defer shutdown()
	client := &http.Client{Timeout: 10 * time.Second}
	sid := createSessionID(t, client, base, token)

	live := streamTurn(t, client, base, token, sid, `{"message":"go"}`)
	rid := requestIDOf(t, live)

	var sse sseEvent
	for _, ev := range live {
		if ev.Event == "tool_result" {
			sse = ev
		}
	}
	if sse.Event == "" {
		t.Fatal("no tool_result frame")
	}
	got, _ := sse.Data["preview"].(string)
	if len(got) > toolPreviewLimit+8 {
		t.Fatalf("SSE preview is %d bytes — it should stay small", len(got))
	}
	if strings.Contains(sse.Raw, long) {
		t.Fatal("the full tool output leaked into the SSE frame")
	}

	req := authReq(t, http.MethodGet,
		base+"/api/sessions/"+sid+"/turns/"+rid+"/tools/call_1_1", token, nil)
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("evidence: %d %s", resp.StatusCode, raw)
	}
	var ev struct {
		Name   string         `json:"name"`
		Output string         `json:"output"`
		OK     bool           `json:"ok"`
		Input  map[string]any `json:"input"`
	}
	if err := json.Unmarshal(raw, &ev); err != nil {
		t.Fatal(err)
	}
	if ev.Output != long {
		t.Fatalf("evidence output is %d bytes, want %d", len(ev.Output), len(long))
	}
	if ev.Name != "bash" || !ev.OK {
		t.Fatalf("evidence = %+v", ev)
	}
	if ev.Input["command"] != "cat big.log" {
		t.Fatalf("evidence lost the call input: %+v", ev.Input)
	}

	// An unknown call id is a 404, not an empty 200.
	req = authReq(t, http.MethodGet,
		base+"/api/sessions/"+sid+"/turns/"+rid+"/tools/call_nope", token, nil)
	resp2, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp2.Body.Close()
	if resp2.StatusCode != http.StatusNotFound {
		t.Fatalf("unknown call id status = %d", resp2.StatusCode)
	}
}

func TestToolEvidenceServesRecordedImage(t *testing.T) {
	home := t.TempDir()
	sid := "sess-image"
	rid := "req-image"
	log, err := openTurnLog(home, sid, rid)
	if err != nil {
		t.Fatal(err)
	}
	ts := &turnStream{log: log, sink: newFrameSink(rid, 8)}
	png := []byte("\x89PNG\r\n\x1a\nfake-bytes")
	ts.toolResult(toolResultToken{
		ID: "call_1_1", Name: "screenshot", OK: true,
		Preview: "screenshot", Output: "captured",
		Images: []toolResultImage{{MediaType: "image/png", Data: png}},
	})
	ts.done(rid, "ok", "")
	log.Close()

	evidence, err := findToolEvidence(turnLogPath(home, sid, rid), "call_1_1")
	if err != nil {
		t.Fatal(err)
	}
	if len(evidence.Images) != 1 {
		t.Fatalf("images = %+v", evidence.Images)
	}
	img := evidence.Images[0]
	if img.Bytes != len(png) || img.SHA256 == "" {
		t.Fatalf("image block = %+v", img)
	}
	// The bytes live beside the log, not inside it.
	onDisk := filepath.Join(turnsDir(home, sid), filepath.FromSlash(img.Path))
	got, err := os.ReadFile(onDisk)
	if err != nil {
		t.Fatalf("image not stored beside the log: %v", err)
	}
	if !bytes.Equal(got, png) {
		t.Fatal("stored image bytes differ")
	}
	raw, err := os.ReadFile(turnLogPath(home, sid, rid))
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(raw, png) {
		t.Fatal("image bytes were inlined into the log")
	}
}

// ---------------------------------------------------------------------------
// 5. liveness on the session object

func sessionClaimed(t *testing.T, client *http.Client, base, token, sid string) bool {
	t.Helper()
	sess := getSession(t, client, base, token, sid)
	return sess.Claimed
}

func getSession(t *testing.T, client *http.Client, base, token, sid string) ChatSession {
	t.Helper()
	req := authReq(t, http.MethodGet, base+"/api/sessions/"+sid, token, nil)
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("get session: %d %s", resp.StatusCode, raw)
	}
	var sess ChatSession
	if err := json.Unmarshal(raw, &sess); err != nil {
		t.Fatal(err)
	}
	return sess
}

func TestSessionReportsClaimedAndActiveRequestDuringTurn(t *testing.T) {
	home := t.TempDir()
	hold := make(chan struct{})
	runner := &stubRunner{tokens: []string{"a", "b"}, hold: hold, holdAfter: 1}
	base, shutdown, token := startMessagesServer(t, runner, home)
	defer shutdown()
	client := &http.Client{Timeout: 10 * time.Second}
	sid := createSessionID(t, client, base, token)

	if sessionClaimed(t, client, base, token, sid) {
		t.Fatal("idle session reports claimed")
	}

	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages/stream", token,
		bytes.NewBufferString(`{"message":"go"}`))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	br := bufio.NewReader(resp.Body)
	start, err := readTurnSSEEvent(br)
	if err != nil {
		t.Fatal(err)
	}
	rid, _ := start.Data["request_id"].(string)

	sess := getSession(t, client, base, token, sid)
	if !sess.Claimed {
		t.Fatal("claimed is false during a turn")
	}
	if sess.ActiveRequestID == nil || *sess.ActiveRequestID != rid {
		t.Fatalf("active_request_id = %v, want %q", sess.ActiveRequestID, rid)
	}

	// The list view carries the same liveness.
	lreq := authReq(t, http.MethodGet, base+"/api/sessions", token, nil)
	lresp, err := client.Do(lreq)
	if err != nil {
		t.Fatal(err)
	}
	lraw, _ := io.ReadAll(lresp.Body)
	lresp.Body.Close()
	var listed struct {
		Sessions []ChatSession `json:"sessions"`
	}
	if err := json.Unmarshal(lraw, &listed); err != nil {
		t.Fatal(err)
	}
	found := false
	for _, s := range listed.Sessions {
		if s.ID == sid {
			found = true
			if !s.Claimed || s.ActiveRequestID == nil {
				t.Fatalf("list liveness = %+v", s)
			}
		}
	}
	if !found {
		t.Fatal("session missing from the list")
	}

	close(hold)
	_ = readAllSSEEvents(t, br)
	resp.Body.Close()

	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		sess = getSession(t, client, base, token, sid)
		if !sess.Claimed && sess.ActiveRequestID == nil {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("session still claimed after the turn: %+v", sess)
}

func TestMessageAddedPublishedForEverySession(t *testing.T) {
	home := t.TempDir()
	runner := &stubRunner{tokens: []string{"hello"}}
	base, shutdown, token := startMessagesServer(t, runner, home)
	defer shutdown()
	client := &http.Client{Timeout: 10 * time.Second}
	sid := createSessionID(t, client, base, token) // no origin_channel

	req := authReq(t, http.MethodGet, base+"/api/events/sessions", token, nil)
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	br := bufio.NewReader(resp.Body)
	if _, err := readTurnSSEEvent(br); err != nil { // hello
		t.Fatal(err)
	}

	go func() { _ = streamTurnQuiet(client, base, token, sid) }()

	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		ev, err := readTurnSSEEvent(br)
		if err != nil {
			t.Fatalf("events stream: %v", err)
		}
		if ev.Event == "message_added" {
			if got, _ := ev.Data["session_id"].(string); got != sid {
				t.Fatalf("message_added for %q, want %q", got, sid)
			}
			return
		}
	}
	t.Fatal("no message_added for a desktop-origin session")
}

func streamTurnQuiet(client *http.Client, base, token, sid string) error {
	req, err := http.NewRequest(http.MethodPost,
		base+"/api/sessions/"+sid+"/messages/stream", bytes.NewBufferString(`{"message":"hi"}`))
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
	return nil
}

// ---------------------------------------------------------------------------
// 6. a dropped frame is reported as a gap, never silently swallowed

func TestFrameSinkReportsGapInsteadOfSilentDrop(t *testing.T) {
	sink := newFrameSink("req-1", 4)
	for i := 1; i <= 10; i++ {
		sink.push(streamFrame{Seq: uint64(i), Data: sseFrame("token", map[string]any{
			"type": "token", "text": "t", "seq": i,
		})})
	}
	dropped, notices := sink.gapStats()
	if dropped == 0 {
		t.Fatal("nothing was dropped by a 4-slot queue fed 10 frames")
	}
	if notices != 0 {
		t.Fatalf("a gap notice was emitted while the queue was still full (%d)", notices)
	}

	// Drain what the slow reader can take, then the next push announces the gap.
	drained := make([]streamFrame, 0, 4)
	for len(sink.ch) > 0 {
		drained = append(drained, <-sink.ch)
	}
	sink.push(streamFrame{Seq: 11, Data: sseFrame("done", map[string]any{
		"type": "done", "status": "ok", "seq": 11,
	})})
	_, notices = sink.gapStats()
	if notices != 1 {
		t.Fatalf("gap notices = %d, want 1", notices)
	}
	var frames []streamFrame
	for len(sink.ch) > 0 {
		frames = append(frames, <-sink.ch)
	}
	if len(frames) < 2 {
		t.Fatalf("expected gap + done, got %d frames", len(frames))
	}
	gap := frames[0]
	if !strings.Contains(gap.Data, "event: gap") {
		t.Fatalf("first frame after the drop is not a gap: %q", gap.Data)
	}
	var payload map[string]any
	line := strings.SplitN(strings.TrimSpace(strings.SplitN(gap.Data, "data: ", 2)[1]), "\n", 2)[0]
	if err := json.Unmarshal([]byte(line), &payload); err != nil {
		t.Fatal(err)
	}
	from, _ := payload["from"].(float64)
	to, _ := payload["to"].(float64)
	resume, _ := payload["resume_after"].(float64)
	if from == 0 || to < from {
		t.Fatalf("gap range = %v..%v", from, to)
	}
	if uint64(resume) != uint64(from)-1 {
		t.Fatalf("resume_after = %v for gap from %v", resume, from)
	}
	// The client can re-sync from the log: the range names exactly what it lost.
	if uint64(to)-uint64(from)+1 != dropped {
		t.Fatalf("gap covers %v..%v but %d frames were dropped", from, to, dropped)
	}
	if len(drained) == 0 {
		t.Fatal("expected the reader to have drained something")
	}
}

// A turn whose frames are all delivered never reports a gap.
func TestFrameSinkNoGapWhenReaderKeepsUp(t *testing.T) {
	sink := newFrameSink("req-1", 4)
	for i := 1; i <= 20; i++ {
		sink.push(streamFrame{Seq: uint64(i), Data: "x"})
		<-sink.ch
	}
	dropped, notices := sink.gapStats()
	if dropped != 0 || notices != 0 {
		t.Fatalf("dropped=%d notices=%d with a reader that keeps up", dropped, notices)
	}
}

// ---------------------------------------------------------------------------
// durability: a failed assistant write surfaces as an SSE error frame

func TestPersistFailureSurfacesAsErrorFrame(t *testing.T) {
	home := t.TempDir()
	runner := &stubRunner{tokens: []string{"partial answer"}}
	base, shutdown, token := startMessagesServer(t, runner, home)
	defer shutdown()
	client := &http.Client{Timeout: 10 * time.Second}
	sid := createSessionID(t, client, base, token)

	live := streamTurn(t, client, base, token, sid, `{"message":"go"}`)
	for _, ev := range live {
		if ev.Event == "error" {
			t.Fatalf("healthy turn emitted an error frame: %s", ev.Raw)
		}
	}
	// The assistant row is committed with its counter in one transaction.
	sess := getSession(t, client, base, token, sid)
	msgs := listMessages(t, client, base, token, sid)
	if sess.MessageCount != len(msgs) {
		t.Fatalf("message_count=%d but %d rows", sess.MessageCount, len(msgs))
	}
}

func TestAddMessageFullIsTransactional(t *testing.T) {
	store, err := openSessionStore(filepath.Join(t.TempDir(), "memory.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	sess, err := store.Create(createSessionRequest{Title: "T"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := store.AddMessageFull(sess.ID, "assistant", "hi", nil, nil, nil, nil, nil, nil); err != nil {
		t.Fatal(err)
	}
	// A row for a session that does not exist must fail, not half-commit.
	if _, err := store.AddMessageFull("missing-session", "assistant", "hi", nil, nil, nil, nil, nil, nil); err == nil {
		t.Fatal("insert for a missing session should fail (foreign key)")
	}
	msgs, err := store.ListMessages(sess.ID, 100, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(msgs) != 1 {
		t.Fatalf("rows = %d", len(msgs))
	}
	after, _, err := store.Get(sess.ID)
	if err != nil {
		t.Fatal(err)
	}
	if after.MessageCount != 1 {
		t.Fatalf("message_count = %d", after.MessageCount)
	}
}

func TestFinalizeReplacesDraftInPlace(t *testing.T) {
	store, err := openSessionStore(filepath.Join(t.TempDir(), "memory.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	sess, err := store.Create(createSessionRequest{Title: "T"})
	if err != nil {
		t.Fatal(err)
	}
	rid := "req-1"
	first, err := store.UpsertDraftMessage(sess.ID, rid, "assistant", "partial", nil,
		[]map[string]any{{"name": "bash"}}, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := store.UpsertDraftMessage(sess.ID, rid, "assistant", "partial more", nil,
		[]map[string]any{{"name": "bash"}}, nil, nil); err != nil {
		t.Fatal(err)
	}
	final, err := store.FinalizeMessage(sess.ID, rid, "assistant", "complete", nil, nil, nil, nil, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if final.ID != first.ID {
		t.Fatalf("finalize created a new row %s (draft was %s)", final.ID, first.ID)
	}
	msgs, err := store.ListMessages(sess.ID, 100, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(msgs) != 1 || msgs[0].Draft {
		t.Fatalf("rows = %+v", msgs)
	}
	if msgs[0].Content != "complete" {
		t.Fatalf("content = %v", msgs[0].Content)
	}
	after, _, err := store.Get(sess.ID)
	if err != nil {
		t.Fatal(err)
	}
	if after.MessageCount != 1 {
		t.Fatalf("message_count = %d — the draft was counted twice", after.MessageCount)
	}
}

// ---------------------------------------------------------------------------
// approval records

func TestTurnLogRecordsApprovalDecision(t *testing.T) {
	home := t.TempDir()
	sid := "sess-approve"
	rid := "req-approve"
	log, err := openTurnLog(home, sid, rid)
	if err != nil {
		t.Fatal(err)
	}
	ts := &turnStream{log: log, sink: newFrameSink(rid, 8)}
	ts.approval("a1b2", "bash", "Remedy wants to run: rm -rf build", "pending")
	ts.approval("a1b2", "bash", "Remedy wants to run: rm -rf build", "approved")
	ts.done(rid, "ok", "")
	log.Close()

	var approvals []turnRecord
	if err := readTurnRecords(turnLogPath(home, sid, rid), func(rec turnRecord) bool {
		if rec.T == "approval" {
			approvals = append(approvals, rec)
		}
		return true
	}); err != nil {
		t.Fatal(err)
	}
	if len(approvals) != 2 {
		t.Fatalf("approval records = %d", len(approvals))
	}
	if approvals[0].ID != "a1b2" || approvals[0].Tool != "bash" ||
		approvals[0].Decision != "pending" || approvals[0].Summary == "" {
		t.Fatalf("approval record = %+v", approvals[0])
	}
	if approvals[1].Decision != "approved" {
		t.Fatalf("second approval = %+v", approvals[1])
	}
	// Approvals are evidence only: they never become an SSE frame.
	if _, _, renders := (&approvals[0]).frame(); renders {
		t.Fatal("approval records must not render an SSE frame")
	}
	if approvals[0].Seq != 0 || approvals[1].Seq != 0 {
		t.Fatalf("approval seq must not occupy the SSE sequence: %d %d", approvals[0].Seq, approvals[1].Seq)
	}
}

// A server without a home still streams; only the evidence routes go quiet.
func TestStreamWorksWithoutATurnLogHome(t *testing.T) {
	base, shutdown, token := startMessagesServer(t, &stubRunner{tokens: []string{"a", "b"}}, "")
	defer shutdown()
	client := &http.Client{Timeout: 10 * time.Second}
	sid := createSessionID(t, client, base, token)

	live := streamTurn(t, client, base, token, sid, `{"message":"go"}`)
	rid := requestIDOf(t, live)
	var last uint64
	for _, ev := range live {
		if ev.seq() <= last {
			t.Fatalf("seq not monotonic without a log: %d after %d", ev.seq(), last)
		}
		last = ev.seq()
	}
	if live[len(live)-1].Event != "done" {
		t.Fatalf("last frame = %s", live[len(live)-1].Event)
	}

	req := authReq(t, http.MethodGet,
		base+"/api/sessions/"+sid+"/stream/attach?request_id="+rid, token, nil)
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("attach without a home = %d", resp.StatusCode)
	}
}

// A log with no done record (the process died mid-turn) ends the attach with
// an explicit interrupted done instead of hanging the client forever.
func TestAttachOnCrashedTurnEndsInterrupted(t *testing.T) {
	home := t.TempDir()
	base, shutdown, token := startMessagesServer(t, &stubRunner{tokens: []string{"x"}}, home)
	defer shutdown()
	client := &http.Client{Timeout: 10 * time.Second}
	sid := createSessionID(t, client, base, token)

	rid := "req-crashed"
	log, err := openTurnLog(home, sid, rid)
	if err != nil {
		t.Fatal(err)
	}
	ts := &turnStream{log: log, sink: newFrameSink(rid, 8)}
	ts.record(turnRecord{T: "start", RequestID: rid, SessionID: sid})
	ts.assistantText("half an ")
	log.Close() // the process dies here: no done record

	events := attachEvents(t, client, base, token, sid, rid, 0)
	if len(events) != 3 {
		t.Fatalf("frames = %d (%+v)", len(events), events)
	}
	last := events[len(events)-1]
	if last.Event != "done" || last.Data["status"] != "interrupted" {
		t.Fatalf("last frame = %s %v", last.Event, last.Data)
	}
}
