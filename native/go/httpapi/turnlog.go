package httpapi

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// The turn log is the durable record of one request: every event that reaches
// the SSE transport is first appended to
// <home>/sessions/<sid>/turns/<request_id>.jsonl, one JSON object per line.
// Images referenced by a tool result are written beside the log as
// <home>/sessions/<sid>/turns/<request_id>/<sha256>.png and carried in the
// record by path, never inlined.
//
// Record types (design §3): start, message, usage, status, approval, done.
// One extension type, "signal", carries the desktop side-channel frames the
// design does not enumerate (progress payloads, todos, life_task,
// library_suggest, aborted, error) so a replay is complete.
//
// Every record carries a monotonic per-turn "seq". The SSE frame built from a
// record carries the same seq, so a client that lost frames can resume from
// /stream/attach?after=<last seq it saw> with no gap and no duplicate.

const (
	turnLogDirName = "turns"
	// toolPreviewLimit is the SSE preview cap. The full body lives in the log
	// and is served by GET .../turns/{request_id}/tools/{call_id}.
	toolPreviewLimit = 500
)

// logBlock is one block inside a turn-log message record.
type logBlock struct {
	Type string `json:"type"`

	// text / thinking
	Text string `json:"text,omitempty"`
	// Replace marks a thinking block that supersedes the previous round.
	Replace bool `json:"replace,omitempty"`

	// image (stored beside the log, referenced by path)
	MediaType string `json:"media_type,omitempty"`
	Path      string `json:"path,omitempty"`
	SHA256    string `json:"sha256,omitempty"`
	Bytes     int    `json:"bytes,omitempty"`

	// Via names the sub-agent a delegated call belongs to. It travels on the
	// record so a replay attributes the work exactly as the live stream did.
	Via string `json:"via,omitempty"`

	// tool_use
	ID    string          `json:"id,omitempty"`
	Name  string          `json:"name,omitempty"`
	Input json.RawMessage `json:"input,omitempty"`

	// tool_result
	ToolUseID string     `json:"tool_use_id,omitempty"`
	Content   []logBlock `json:"content,omitempty"`
	IsError   bool       `json:"is_error,omitempty"`
	// Preview is the short body the SSE frame carries; the full body is the
	// text block inside Content.
	Preview string `json:"preview,omitempty"`
}

// turnRecord is one line of the turn log.
type turnRecord struct {
	T   string  `json:"t"`
	Seq uint64  `json:"seq"`
	TS  float64 `json:"ts,omitempty"`

	// start
	RequestID  string `json:"request_id,omitempty"`
	SessionID  string `json:"session_id,omitempty"`
	Origin     string `json:"origin,omitempty"`
	Model      string `json:"model,omitempty"`
	Provider   string `json:"provider,omitempty"`
	ClaimEpoch int    `json:"claim_epoch,omitempty"`

	// message
	Role   string     `json:"role,omitempty"`
	Blocks []logBlock `json:"blocks,omitempty"`

	// usage — flat counters plus the exact provider payload the frame carries.
	In         int            `json:"in,omitempty"`
	Out        int            `json:"out,omitempty"`
	CacheRead  int            `json:"cache_read,omitempty"`
	CacheWrite int            `json:"cache_write,omitempty"`
	Raw        map[string]any `json:"raw,omitempty"`

	// status
	Text string `json:"text,omitempty"`

	// approval
	ID       string `json:"id,omitempty"`
	Tool     string `json:"tool,omitempty"`
	Summary  string `json:"summary,omitempty"`
	Decision string `json:"decision,omitempty"`

	// done
	Status string `json:"status,omitempty"`
	Error  string `json:"error,omitempty"`

	// signal — a transport frame with no §3 record shape.
	Event string         `json:"event,omitempty"`
	Data  map[string]any `json:"data,omitempty"`
}

// frame renders the SSE event this record produces. ok is false for records
// that are evidence only (approval). Both the live turn and a replay build
// their frames here, so a replayed stream is byte-identical to the live one.
func (r *turnRecord) frame() (event string, payload map[string]any, ok bool) {
	switch r.T {
	case "start":
		return "start", map[string]any{
			"type":        "start",
			"request_id":  r.RequestID,
			"session_id":  r.SessionID,
			"claim_epoch": r.ClaimEpoch,
			"seq":         r.Seq,
		}, true
	case "message":
		return r.messageFrame()
	case "usage":
		out := map[string]any{}
		for k, v := range r.Raw {
			out[k] = v
		}
		out["type"] = "usage"
		out["seq"] = r.Seq
		return "usage", out, true
	case "status":
		return "progress", map[string]any{
			"type":  "progress",
			"label": r.Text,
			"seq":   r.Seq,
		}, true
	case "done":
		out := map[string]any{
			"type":       "done",
			"request_id": r.RequestID,
			"status":     r.Status,
			"seq":        r.Seq,
		}
		if r.Error != "" {
			out["error"] = r.Error
		}
		return "done", out, true
	case "signal":
		out := map[string]any{}
		for k, v := range r.Data {
			out[k] = v
		}
		out["type"] = r.Event
		out["seq"] = r.Seq
		return r.Event, out, true
	}
	return "", nil, false
}

func (r *turnRecord) messageFrame() (string, map[string]any, bool) {
	if len(r.Blocks) != 1 {
		return "", nil, false
	}
	b := r.Blocks[0]
	switch b.Type {
	case "text":
		return "token", map[string]any{"type": "token", "text": b.Text, "seq": r.Seq}, true
	case "thinking":
		out := map[string]any{"type": "thinking", "text": b.Text, "seq": r.Seq}
		if b.Replace {
			out["replace"] = true
		}
		return "thinking", out, true
	case "tool_use":
		args := map[string]any{}
		if len(b.Input) > 0 {
			_ = json.Unmarshal(b.Input, &args)
		}
		out := map[string]any{"type": "tool_call", "name": b.Name, "args": args, "seq": r.Seq}
		if b.ID != "" {
			out["id"] = b.ID
		}
		if b.Via != "" {
			out["via"] = b.Via
		}
		return "tool_call", out, true
	case "tool_result":
		out := map[string]any{
			"type":    "tool_result",
			"name":    b.Name,
			"preview": b.Preview,
			"ok":      !b.IsError,
			"seq":     r.Seq,
		}
		if b.ToolUseID != "" {
			out["id"] = b.ToolUseID
		}
		if b.Via != "" {
			out["via"] = b.Via
		}
		return "tool_result", out, true
	}
	return "", nil, false
}

// ---------------------------------------------------------------------------
// on-disk writer

// turnLog appends records for one request. Every record is written and flushed
// to the OS as it happens: nothing about a turn is held in memory waiting for
// the end, so a crash mid-build leaves everything up to the last event.
type turnLog struct {
	mu       sync.Mutex
	f        *os.File
	path     string
	assetDir string
	seq      uint64
	err      error
}

// safeTurnID validates a request id used as a filename component.
func safeTurnID(raw string) string {
	id := strings.TrimSpace(raw)
	if id == "" || len(id) > 128 || id == "." || id == ".." {
		return ""
	}
	for _, r := range id {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9':
		case r == '-', r == '_', r == '.':
		default:
			return ""
		}
	}
	return id
}

// turnsDir is <home>/sessions/<sid>/turns ("" when no home is configured).
func turnsDir(homeDir, sessionID string) string {
	home := strings.TrimSpace(homeDir)
	if home == "" {
		return ""
	}
	sid := safeSessionID(sessionID)
	if sid == "" || sid == "_" {
		return ""
	}
	return filepath.Join(home, "sessions", sid, turnLogDirName)
}

// turnLogPath is the .jsonl for one request ("" when unavailable).
func turnLogPath(homeDir, sessionID, requestID string) string {
	dir := turnsDir(homeDir, sessionID)
	rid := safeTurnID(requestID)
	if dir == "" || rid == "" {
		return ""
	}
	return filepath.Join(dir, rid+".jsonl")
}

// openTurnLog creates the turn log for one request. A nil log is a working
// no-op writer so a server without a home still streams.
func openTurnLog(homeDir, sessionID, requestID string) (*turnLog, error) {
	path := turnLogPath(homeDir, sessionID, requestID)
	if path == "" {
		return nil, nil
	}
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return nil, err
	}
	return &turnLog{
		f:        f,
		path:     path,
		assetDir: filepath.Join(dir, safeTurnID(requestID)),
	}, nil
}

// append stamps seq + ts on the record, writes one line, and returns the seq.
// Seq 0 means the record was not persisted (no log configured).
func (l *turnLog) append(rec *turnRecord) uint64 {
	if l == nil || rec == nil {
		return 0
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.f == nil {
		return 0
	}
	l.seq++
	rec.Seq = l.seq
	if rec.TS == 0 {
		rec.TS = float64(time.Now().UnixNano()) / 1e9
	}
	line, err := json.Marshal(rec)
	if err != nil {
		l.err = err
		return rec.Seq
	}
	line = append(line, '\n')
	if _, err := l.f.Write(line); err != nil && l.err == nil {
		l.err = err
	}
	return rec.Seq
}

// storeImage writes an image block beside the log and returns its reference.
func (l *turnLog) storeImage(mediaType string, data []byte) (logBlock, error) {
	sum := sha256.Sum256(data)
	sha := hex.EncodeToString(sum[:])
	block := logBlock{
		Type:      "image",
		MediaType: mediaType,
		SHA256:    sha,
		Bytes:     len(data),
	}
	if l == nil || l.assetDir == "" {
		return block, nil
	}
	name := sha + imageExtForMedia(mediaType)
	if err := os.MkdirAll(l.assetDir, 0o700); err != nil {
		return block, err
	}
	full := filepath.Join(l.assetDir, name)
	if err := os.WriteFile(full, data, 0o600); err != nil {
		return block, err
	}
	block.Path = filepath.Base(l.assetDir) + "/" + name
	return block, nil
}

// Sync flushes the log to stable storage (called once the turn is done).
func (l *turnLog) Sync() {
	if l == nil {
		return
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.f != nil {
		_ = l.f.Sync()
	}
}

func (l *turnLog) Close() {
	if l == nil {
		return
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.f != nil {
		_ = l.f.Sync()
		_ = l.f.Close()
		l.f = nil
	}
}

// Err reports the first write failure, if any.
func (l *turnLog) Err() error {
	if l == nil {
		return nil
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.err
}

func imageExtForMedia(mediaType string) string {
	switch strings.ToLower(strings.TrimSpace(mediaType)) {
	case "image/jpeg", "image/jpg":
		return ".jpg"
	case "image/webp":
		return ".webp"
	case "image/gif":
		return ".gif"
	default:
		return ".png"
	}
}

func mediaForImageExt(name string) string {
	switch strings.ToLower(filepath.Ext(name)) {
	case ".jpg", ".jpeg":
		return "image/jpeg"
	case ".webp":
		return "image/webp"
	case ".gif":
		return "image/gif"
	default:
		return "image/png"
	}
}

// ---------------------------------------------------------------------------
// live turn registry

// liveTurn lets an attaching reader block until the turn writes more, instead
// of polling the file.
type liveTurn struct {
	sessionID string
	requestID string

	mu   sync.Mutex
	ch   chan struct{}
	done bool
}

func newLiveTurn(sessionID, requestID string) *liveTurn {
	return &liveTurn{sessionID: sessionID, requestID: requestID, ch: make(chan struct{})}
}

// signal wakes every waiter (a record was appended).
func (l *liveTurn) signal() {
	if l == nil {
		return
	}
	l.mu.Lock()
	close(l.ch)
	l.ch = make(chan struct{})
	l.mu.Unlock()
}

// wait returns a channel closed on the next signal or finish.
func (l *liveTurn) wait() <-chan struct{} {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.ch
}

func (l *liveTurn) finish() {
	if l == nil {
		return
	}
	l.mu.Lock()
	l.done = true
	close(l.ch)
	l.ch = make(chan struct{})
	l.mu.Unlock()
}

func (l *liveTurn) finished() bool {
	if l == nil {
		return true
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.done
}

// turnHub tracks the turns currently writing a log so attach can tail them.
type turnHub struct {
	mu   sync.Mutex
	live map[string]*liveTurn
	// streams is the live turnStream per session, so an event that arrives
	// outside the turn goroutine — an approval the owner resolves in the UI —
	// still lands in that turn's evidence log.
	streams map[string]*turnStream
}

func newTurnHub() *turnHub {
	return &turnHub{live: map[string]*liveTurn{}, streams: map[string]*turnStream{}}
}

// attachStream registers the writer for a session's in-flight turn.
func (h *turnHub) attachStream(sessionID string, ts *turnStream) {
	if h == nil || ts == nil {
		return
	}
	sid := strings.TrimSpace(sessionID)
	if sid == "" {
		return
	}
	h.mu.Lock()
	h.streams[sid] = ts
	h.mu.Unlock()
}

// detachStream removes the writer when the turn ends (only if it is still ours).
func (h *turnHub) detachStream(sessionID string, ts *turnStream) {
	if h == nil || ts == nil {
		return
	}
	sid := strings.TrimSpace(sessionID)
	h.mu.Lock()
	if cur, ok := h.streams[sid]; ok && cur == ts {
		delete(h.streams, sid)
	}
	h.mu.Unlock()
}

// RecordApproval writes an owner decision into the session's live turn log.
// A decision made when no turn is running is simply not recorded.
func (h *turnHub) RecordApproval(sessionID, id, tool, summary, decision string) {
	if h == nil {
		return
	}
	h.mu.Lock()
	ts := h.streams[strings.TrimSpace(sessionID)]
	h.mu.Unlock()
	if ts != nil {
		ts.approval(id, tool, summary, decision)
	}
}

func turnKey(sessionID, requestID string) string {
	return strings.TrimSpace(sessionID) + "\x00" + strings.TrimSpace(requestID)
}

func (h *turnHub) begin(sessionID, requestID string) *liveTurn {
	if h == nil {
		return nil
	}
	lt := newLiveTurn(sessionID, requestID)
	h.mu.Lock()
	h.live[turnKey(sessionID, requestID)] = lt
	h.mu.Unlock()
	return lt
}

func (h *turnHub) end(lt *liveTurn) {
	if h == nil || lt == nil {
		return
	}
	h.mu.Lock()
	key := turnKey(lt.sessionID, lt.requestID)
	if cur, ok := h.live[key]; ok && cur == lt {
		delete(h.live, key)
	}
	h.mu.Unlock()
	lt.finish()
}

func (h *turnHub) get(sessionID, requestID string) *liveTurn {
	if h == nil {
		return nil
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.live[turnKey(sessionID, requestID)]
}

// ---------------------------------------------------------------------------
// turn stream: one event → one log record → one SSE frame

// turnStream is the single writer for a turn. Every event goes through record
// so the log and the SSE transport can never diverge.
type turnStream struct {
	log  *turnLog
	live *liveTurn
	sink *frameSink
}

// record appends rec to the log and pushes the frame it renders. It returns
// the assigned seq.
func (t *turnStream) record(rec turnRecord) uint64 {
	if t == nil {
		return 0
	}
	seq := t.log.append(&rec)
	if seq == 0 {
		// No log configured: the transport still needs a monotonic seq.
		seq = t.sink.nextSyntheticSeq()
		rec.Seq = seq
	}
	if event, payload, ok := rec.frame(); ok {
		t.sink.push(streamFrame{Seq: seq, Data: sseFrame(event, payload)})
	}
	t.live.signal()
	return seq
}

// status writes a §3 status record.
func (t *turnStream) status(text string) {
	if strings.TrimSpace(text) == "" {
		return
	}
	t.record(turnRecord{T: "status", Text: text})
}

// signal writes a transport frame that has no §3 record shape.
func (t *turnStream) signal(event string, data map[string]any) {
	t.record(turnRecord{T: "signal", Event: event, Data: data})
}

// approval writes an evidence-only approval record (no SSE frame).
func (t *turnStream) approval(id, tool, summary, decision string) {
	t.record(turnRecord{T: "approval", ID: id, Tool: tool, Summary: summary, Decision: decision})
}

// assistantText records one model text delta (full text, never a preview).
func (t *turnStream) assistantText(text string) {
	t.record(turnRecord{
		T:      "message",
		Role:   "assistant",
		Blocks: []logBlock{{Type: "text", Text: text}},
	})
}

func (t *turnStream) assistantThinking(text string, replace bool) {
	t.record(turnRecord{
		T:      "message",
		Role:   "assistant",
		Blocks: []logBlock{{Type: "thinking", Text: text, Replace: replace}},
	})
}

func (t *turnStream) toolUse(id, name, via string, args map[string]any) {
	input, err := json.Marshal(args)
	if err != nil {
		input = []byte("{}")
	}
	t.record(turnRecord{
		T:      "message",
		Role:   "assistant",
		Blocks: []logBlock{{Type: "tool_use", ID: id, Name: name, Via: via, Input: input}},
	})
}

// toolResult records the full tool output. Images are written beside the log
// and referenced by path.
func (t *turnStream) toolResult(res toolResultToken) {
	block := logBlock{
		Type:      "tool_result",
		ToolUseID: res.ID,
		Name:      res.Name,
		IsError:   !res.OK,
		Preview:   res.Preview,
		Via:       res.Via,
	}
	body := res.Output
	if body == "" && !res.OK {
		body = res.Error
	}
	if body != "" {
		block.Content = append(block.Content, logBlock{Type: "text", Text: body})
	}
	for _, img := range res.Images {
		stored, err := t.log.storeImage(img.MediaType, img.Data)
		if err != nil {
			continue
		}
		block.Content = append(block.Content, stored)
	}
	t.record(turnRecord{T: "message", Role: "user", Blocks: []logBlock{block}})
}

func (t *turnStream) usage(raw map[string]any) {
	rec := turnRecord{T: "usage", Raw: raw}
	rec.In = usageInt(raw, "prompt_tokens", "input_tokens", "in")
	rec.Out = usageInt(raw, "completion_tokens", "output_tokens", "out")
	rec.CacheRead = usageInt(raw, "cache_read_input_tokens", "cache_read")
	rec.CacheWrite = usageInt(raw, "cache_creation_input_tokens", "cache_write")
	t.record(rec)
}

func usageInt(raw map[string]any, keys ...string) int {
	for _, k := range keys {
		v, ok := raw[k]
		if !ok {
			continue
		}
		switch n := v.(type) {
		case float64:
			return int(n)
		case int:
			return n
		case int64:
			return int(n)
		case json.Number:
			if i, err := n.Int64(); err == nil {
				return int(i)
			}
		}
	}
	return 0
}

func (t *turnStream) done(requestID, status, errMsg string) uint64 {
	seq := t.record(turnRecord{T: "done", RequestID: requestID, Status: status, Error: errMsg})
	t.log.Sync()
	return seq
}

// ---------------------------------------------------------------------------
// frame transport

// streamFrame is one SSE frame with the seq of the record that produced it.
// Seq 0 marks a transport-only frame (gap notices) that is not in the log.
type streamFrame struct {
	Seq  uint64
	Data string
}

// frameSink is the bounded queue between the turn goroutine and the SSE
// writer. When the reader is too slow the oldest frame is dropped — but the
// drop is recorded and announced as a "gap" frame naming the seq range that
// was lost, so the client re-syncs from the turn log instead of silently
// missing tool calls.
type frameSink struct {
	ch        chan streamFrame
	requestID string

	mu        sync.Mutex
	pendFrom  uint64
	pendTo    uint64
	gapFrames int
	dropped   uint64
	synthetic uint64
}

func newFrameSink(requestID string, size int) *frameSink {
	return &frameSink{ch: make(chan streamFrame, size), requestID: requestID}
}

// nextSyntheticSeq numbers frames for a turn that has no on-disk log.
func (f *frameSink) nextSyntheticSeq() uint64 {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.synthetic++
	return f.synthetic
}

func (f *frameSink) push(frame streamFrame) {
	if f == nil {
		return
	}
	f.mu.Lock()
	from, to := f.pendFrom, f.pendTo
	if from != 0 && len(f.ch) < cap(f.ch) {
		f.pendFrom, f.pendTo = 0, 0
		f.gapFrames++
		f.mu.Unlock()
		f.ch <- streamFrame{Data: sseFrame("gap", map[string]any{
			"type":         "gap",
			"request_id":   f.requestID,
			"from":         from,
			"to":           to,
			"resume_after": from - 1,
		})}
		f.mu.Lock()
	}
	for i := 0; i <= cap(f.ch); i++ {
		select {
		case f.ch <- frame:
			f.mu.Unlock()
			return
		default:
		}
		select {
		case old := <-f.ch:
			if old.Seq != 0 {
				f.dropped++
				if f.pendFrom == 0 || old.Seq < f.pendFrom {
					f.pendFrom = old.Seq
				}
				if old.Seq > f.pendTo {
					f.pendTo = old.Seq
				}
			}
		default:
		}
	}
	f.mu.Unlock()
}

// gapStats reports how many frames were dropped and how many gap notices were
// emitted (tests and diagnostics).
func (f *frameSink) gapStats() (dropped uint64, notices int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.dropped, f.gapFrames
}

func (f *frameSink) close() { close(f.ch) }

// ---------------------------------------------------------------------------
// reading a turn log back

// readTurnRecords streams the records of a turn log to fn. Reading stops when
// fn returns false.
func readTurnRecords(path string, fn func(turnRecord) bool) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	for len(data) > 0 {
		idx := indexByte(data, '\n')
		if idx < 0 {
			// Trailing partial line: a crash mid-write. Ignore it.
			break
		}
		line := data[:idx]
		data = data[idx+1:]
		if len(line) == 0 {
			continue
		}
		var rec turnRecord
		if err := json.Unmarshal(line, &rec); err != nil {
			continue
		}
		if !fn(rec) {
			return nil
		}
	}
	return nil
}

func indexByte(b []byte, c byte) int {
	for i := range b {
		if b[i] == c {
			return i
		}
	}
	return -1
}

var errTurnLogMissing = errors.New("turn log not found")

// turnLogReader tails an append-only turn log, handing back one complete
// record at a time and never re-reading a line it already returned.
type turnLogReader struct {
	f      *os.File
	buf    []byte
	closed bool
}

func openTurnLogReader(path string) (*turnLogReader, error) {
	if strings.TrimSpace(path) == "" {
		return nil, errTurnLogMissing
	}
	f, err := os.Open(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, errTurnLogMissing
		}
		return nil, err
	}
	return &turnLogReader{f: f}, nil
}

func (r *turnLogReader) Close() {
	if r == nil || r.closed {
		return
	}
	r.closed = true
	_ = r.f.Close()
}

// next returns the next complete record. ok=false means "nothing more right
// now" — the caller should wait and call again.
func (r *turnLogReader) next() (rec turnRecord, ok bool) {
	for {
		if idx := indexByte(r.buf, '\n'); idx >= 0 {
			line := r.buf[:idx]
			r.buf = r.buf[idx+1:]
			if len(line) == 0 {
				continue
			}
			if err := json.Unmarshal(line, &rec); err != nil {
				continue
			}
			return rec, true
		}
		chunk := make([]byte, 64*1024)
		n, err := r.f.Read(chunk)
		if n > 0 {
			r.buf = append(r.buf, chunk[:n]...)
			continue
		}
		if err != nil {
			return turnRecord{}, false
		}
		return turnRecord{}, false
	}
}

// toolEvidence is the recorded result of one tool call.
type toolEvidence struct {
	CallID    string
	Name      string
	Input     json.RawMessage
	Output    string
	Preview   string
	IsError   bool
	Images    []logBlock
	Found     bool
	RequestID string
}

// findToolEvidence scans a turn log for the call and its result.
func findToolEvidence(path, callID string) (toolEvidence, error) {
	out := toolEvidence{CallID: callID}
	err := readTurnRecords(path, func(rec turnRecord) bool {
		if rec.T != "message" {
			return true
		}
		for _, b := range rec.Blocks {
			switch b.Type {
			case "tool_use":
				if b.ID == callID {
					out.Name = b.Name
					out.Input = b.Input
				}
			case "tool_result":
				if b.ToolUseID != callID {
					continue
				}
				out.Found = true
				out.IsError = b.IsError
				out.Preview = b.Preview
				if b.Name != "" {
					out.Name = b.Name
				}
				var text strings.Builder
				for _, c := range b.Content {
					switch c.Type {
					case "text":
						text.WriteString(c.Text)
					case "image":
						out.Images = append(out.Images, c)
					}
				}
				out.Output = text.String()
			}
		}
		return true
	})
	if err != nil {
		return out, err
	}
	return out, nil
}

func previewOf(s string) string {
	if len(s) <= toolPreviewLimit {
		return s
	}
	cut := toolPreviewLimit
	for cut > 0 && !utf8RuneStart(s[cut]) {
		cut--
	}
	return s[:cut] + "…"
}

func utf8RuneStart(b byte) bool { return b&0xC0 != 0x80 }

// turnLogSummary describes a recorded turn (diagnostics / tests).
func turnLogSummary(path string) (records int, lastSeq uint64, status string, err error) {
	err = readTurnRecords(path, func(rec turnRecord) bool {
		records++
		if rec.Seq > lastSeq {
			lastSeq = rec.Seq
		}
		if rec.T == "done" {
			status = rec.Status
		}
		return true
	})
	return records, lastSeq, status, err
}
