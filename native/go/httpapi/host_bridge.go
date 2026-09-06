package httpapi

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

const (
	jobTextMax = 4000
	hostAliveMaxAgeS = 15.0
)

var payloadSecretKeys = map[string]struct{}{
	"text": {}, "type": {}, "type_text": {}, "content": {}, "password": {},
	"value": {}, "secret": {}, "token": {}, "api_key": {}, "authorization": {},
}

var resultTextKeys = []string{
	"text", "page_text", "content", "html", "message", "stdout", "stderr", "body", "error",
}

// ComputerJob mirrors Python host_bridge.ComputerJob.
type ComputerJob struct {
	ID        string         `json:"id"`
	Action    string         `json:"action"`
	Payload   map[string]any `json:"payload"`
	Status    string         `json:"status"`
	Result    map[string]any `json:"result"`
	Error     *string        `json:"error"`
	CreatedAt string         `json:"created_at"`
	UpdatedAt string         `json:"updated_at"`
	SessionID string         `json:"session_id"`
}

func (j *ComputerJob) toDict() map[string]any {
	if j == nil {
		return nil
	}
	out := map[string]any{
		"id": j.ID, "action": j.Action, "payload": j.Payload,
		"status": j.Status, "created_at": j.CreatedAt, "updated_at": j.UpdatedAt,
		"session_id": j.SessionID,
	}
	if j.Result != nil {
		out["result"] = j.Result
	} else {
		out["result"] = nil
	}
	if j.Error != nil {
		out["error"] = *j.Error
	} else {
		out["error"] = nil
	}
	return out
}

// HostBridge is the filesystem job queue for desktop computer-use.
type HostBridge struct {
	homeDir string
	root    string
	uiPath  string

	mu                       sync.Mutex
	hostSeenAt               float64
	lastPollAt               float64
	lastPollDriver           string
	lastClaimAt              float64
	pollIdleEmpty            bool
	wake                     chan struct{}
	browserBounds            map[string]float64
	browserScale             float64
	uiCommand                map[string]any
	focusedSession           string
	lastElements             []map[string]any
	sessionStreaming         func(string) bool
	lastObservedURL          string
	lastObservedURLBySession map[string]string
	lastNavigateURL          string
	lastNavigateURLBySession map[string]string
}

func newHostBridge(homeDir string) *HostBridge {
	home := ResolveHomeDir(homeDir)
	root := filepath.Join(home, "computer", "jobs")
	_ = os.MkdirAll(root, 0o700)
	ui := filepath.Join(home, "computer", "ui_command.json")
	_ = os.MkdirAll(filepath.Dir(ui), 0o700)
	return &HostBridge{
		homeDir:                  home,
		root:                     root,
		uiPath:                   ui,
		wake:                     make(chan struct{}, 1),
		browserScale:             1,
		lastObservedURLBySession: map[string]string{},
		lastNavigateURLBySession: map[string]string{},
	}
}

func (b *HostBridge) setSessionStreaming(fn func(string) bool) {
	b.mu.Lock()
	b.sessionStreaming = fn
	b.mu.Unlock()
}

func (b *HostBridge) markHostAlive(poller bool, driver string) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.markHostAliveLocked(poller, driver)
}

func (b *HostBridge) markHostAliveLocked(poller bool, driver string) {
	now := float64(time.Now().UnixNano()) / 1e9
	b.hostSeenAt = now
	if poller {
		b.lastPollAt = now
		d := strings.ToLower(strings.TrimSpace(driver))
		if d == "rust" || d == "cli" {
			b.lastPollDriver = d
		}
	}
}

func (b *HostBridge) hostConnected() bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.hostConnectedLocked()
}

func (b *HostBridge) hostConnectedLocked() bool {
	now := float64(time.Now().UnixNano()) / 1e9
	if b.lastPollAt > 0 && (now-b.lastPollAt) <= hostAliveMaxAgeS {
		return true
	}
	return b.lastClaimAt > 0 && (now-b.lastClaimAt) <= hostAliveMaxAgeS
}

func (b *HostBridge) hostDriver() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	if !b.hostConnectedLocked() {
		return ""
	}
	return b.lastPollDriver
}

func (b *HostBridge) pendingCount() int {
	entries, err := os.ReadDir(b.root)
	if err != nil {
		return 0
	}
	n := 0
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(b.root, e.Name()))
		if err != nil {
			continue
		}
		var m map[string]any
		if json.Unmarshal(raw, &m) != nil {
			continue
		}
		if m["status"] == "pending" {
			n++
		}
	}
	return n
}

func (b *HostBridge) setFocusedSession(sessionID string) {
	b.focusedSession = strings.TrimSpace(sessionID)
}

func (b *HostBridge) focusedSessionID() string { return b.focusedSession }

func (b *HostBridge) setBrowserBounds(bounds map[string]any, scale *float64) {
	if bounds == nil {
		return
	}
	out := map[string]float64{}
	for _, k := range []string{"x", "y", "width", "height"} {
		out[k] = asFloat(bounds[k])
	}
	b.browserBounds = out
	if scale != nil && *scale > 0 {
		b.browserScale = *scale
	}
}

func (b *HostBridge) getBrowserBounds() map[string]any {
	if b.browserBounds == nil {
		return nil
	}
	return map[string]any{
		"x": b.browserBounds["x"], "y": b.browserBounds["y"],
		"width": b.browserBounds["width"], "height": b.browserBounds["height"],
		"scale": b.browserScale,
	}
}

func (b *HostBridge) peekUICommand() map[string]any {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.peekUICommandLocked()
}

func (b *HostBridge) peekUICommandLocked() map[string]any {
	if b.uiCommand != nil {
		return cloneMap(b.uiCommand)
	}
	raw, err := os.ReadFile(b.uiPath)
	if err != nil {
		return nil
	}
	var m map[string]any
	if json.Unmarshal(raw, &m) != nil || m["action"] == nil {
		return nil
	}
	b.uiCommand = m
	return cloneMap(m)
}

func (b *HostBridge) setUICommand(command map[string]any) {
	b.mu.Lock()
	defer b.mu.Unlock()
	cmd := cloneMap(command)
	b.uiCommand = cmd
	raw, _ := json.Marshal(cmd)
	_ = os.WriteFile(b.uiPath, raw, 0o600)
}

func (b *HostBridge) clearUICommand(jobID *string) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if jobID != nil && b.uiCommand != nil {
		if strOr(b.uiCommand["job_id"], "") != *jobID {
			return
		}
	}
	b.uiCommand = nil
	_ = os.Remove(b.uiPath)
}

func (b *HostBridge) markObservedURL(url, sessionID string) {
	u := strings.TrimSpace(url)
	if u == "" {
		return
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	b.lastObservedURL = u
	if sid := strings.TrimSpace(sessionID); sid != "" {
		if b.lastObservedURLBySession == nil {
			b.lastObservedURLBySession = map[string]string{}
		}
		b.lastObservedURLBySession[sid] = u
	}
}

func (b *HostBridge) lastObservedURLFor(sessionID string) string {
	b.mu.Lock()
	defer b.mu.Unlock()
	if sid := strings.TrimSpace(sessionID); sid != "" {
		if u := strings.TrimSpace(b.lastObservedURLBySession[sid]); u != "" {
			return u
		}
	}
	return b.lastObservedURL
}

func (b *HostBridge) markNavigated(url string, optimistic bool, sessionID string) {
	_ = optimistic
	u := strings.TrimSpace(url)
	b.mu.Lock()
	defer b.mu.Unlock()
	if u != "" {
		b.lastNavigateURL = u
	}
	if sid := strings.TrimSpace(sessionID); sid != "" {
		if b.lastNavigateURLBySession == nil {
			b.lastNavigateURLBySession = map[string]string{}
		}
		if u != "" {
			b.lastNavigateURLBySession[sid] = u
		}
	}
}

func (b *HostBridge) lastNavigateURLFor(sessionID string) string {
	b.mu.Lock()
	defer b.mu.Unlock()
	if sid := strings.TrimSpace(sessionID); sid != "" {
		if u := strings.TrimSpace(b.lastNavigateURLBySession[sid]); u != "" {
			return u
		}
	}
	return b.lastNavigateURL
}

// Enqueue writes a pending computer job and optionally publishes open_browser.
func (b *HostBridge) Enqueue(action string, payload map[string]any, sessionID string) *ComputerJob {
	pl := cloneMap(payload)
	if pl == nil {
		pl = map[string]any{}
	}
	sid := strings.TrimSpace(sessionID)
	if sid == "" {
		sid = strings.TrimSpace(strOr(pl["session_id"], ""))
	}
	if sid != "" {
		if _, ok := pl["session_id"]; !ok {
			pl["session_id"] = sid
		}
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	job := &ComputerJob{
		ID:        newComputerJobID(),
		Action:    strings.TrimSpace(action),
		Payload:   pl,
		Status:    "pending",
		CreatedAt: now,
		UpdatedAt: now,
		SessionID: sid,
	}
	b.mu.Lock()
	_ = b.writeJob(job)
	b.pollIdleEmpty = false
	select {
	case b.wake <- struct{}{}:
	default:
	}
	b.mu.Unlock()

	rawUI, _ := pl["ui"].(map[string]any)
	openBrowser := false
	if rawUI != nil {
		if v, ok := rawUI["open_browser"].(bool); ok && v {
			openBrowser = true
		}
	}
	act := strings.ToLower(job.Action)
	switch act {
	case "navigate", "snapshot", "a11y", "page_text", "ready",
		"click", "type", "key", "scroll", "drag", "press_hold",
		"select", "hover", "screenshot":
		openBrowser = true
	}
	if openBrowser {
		cmd := map[string]any{
			"action":     "open_browser",
			"url":        strOr(pl["url"], ""),
			"job_id":     job.ID,
			"job_action": job.Action,
		}
		if sid != "" {
			cmd["session_id"] = sid
		}
		b.setUICommand(cmd)
	}
	return job
}

func (b *HostBridge) renudgeUIForJob(job *ComputerJob) {
	if job == nil || strings.ToLower(job.Action) != "navigate" {
		return
	}
	if job.Status != "pending" && job.Status != "running" {
		return
	}
	url := strOr(job.Payload["url"], "")
	if strings.TrimSpace(url) == "" {
		return
	}
	cmd := map[string]any{
		"action":     "open_browser",
		"url":        url,
		"job_id":     job.ID,
		"job_action": job.Action,
		"renudge":    true,
	}
	sid := strings.TrimSpace(job.SessionID)
	if sid == "" {
		sid = strOr(job.Payload["session_id"], "")
	}
	if sid != "" {
		cmd["session_id"] = sid
	}
	b.setUICommand(cmd)
}

// WaitOptions controls HostBridge.Wait.
type WaitOptions struct {
	TimeoutS          float64
	PollS             float64
	UnclaimedTimeoutS *float64 // nil = disabled (navigate default)
	GraceS            *float64
	AbortCheck        func() bool
}

// Wait blocks until the job completes, fails, or times out.
func (b *HostBridge) Wait(jobID string, opts WaitOptions) *ComputerJob {
	timeout := opts.TimeoutS
	if timeout <= 0 {
		timeout = 30
	}
	poll := opts.PollS
	if poll <= 0 {
		poll = 0.05
	}
	started := time.Now()
	deadline := started.Add(time.Duration(timeout * float64(time.Second)))
	nudgesDone := 0
	for time.Now().Before(deadline) {
		if opts.AbortCheck != nil && opts.AbortCheck() {
			job := b.readJob(jobID)
			if job != nil && (job.Status == "done" || job.Status == "error" || job.Status == "cancelled") {
				return job
			}
			return b.cancel(jobID)
		}
		job := b.readJob(jobID)
		if job == nil {
			time.Sleep(time.Duration(poll * float64(time.Second)))
			continue
		}
		if job.Status == "done" || job.Status == "error" || job.Status == "cancelled" {
			return job
		}
		elapsed := time.Since(started).Seconds()
		if strings.ToLower(job.Action) == "navigate" && (job.Status == "pending" || job.Status == "running") {
			if nudgesDone == 0 && elapsed >= 0.6 {
				cmd := b.peekUICommand()
				if strOr(cmd["job_id"], "") != jobID {
					b.renudgeUIForJob(job)
				}
				nudgesDone = 1
			} else if nudgesDone == 1 && elapsed >= 2.5 {
				cmd := b.peekUICommand()
				if strOr(cmd["job_id"], "") != jobID {
					b.renudgeUIForJob(job)
				}
				nudgesDone = 2
			}
		}
		if job.Status == "pending" && opts.UnclaimedTimeoutS != nil {
			limit := *opts.UnclaimedTimeoutS
			if limit < 0.5 {
				limit = 0.5
			}
			if elapsed >= limit {
				cmd := b.peekUICommand()
				if strOr(cmd["job_id"], "") != jobID {
					job2 := b.readJob(jobID)
					if job2 != nil && (job2.Status == "done" || job2.Status == "error" || job2.Status == "cancelled") {
						return job2
					}
					msg := "host did not claim job within " + itoa(int(limit)) + "s (Desktop poller offline or not authenticated)"
					b.mu.Lock()
					cur := b.readJob(jobID)
					if cur != nil && (cur.Status == "pending" || cur.Status == "running") {
						cur.Status = "error"
						cur.Error = &msg
						cur.Payload = scrubRetainedPayload(cur.Payload)
						_ = b.writeJob(cur)
						b.mu.Unlock()
						return cur
					}
					b.mu.Unlock()
					if cur != nil {
						return cur
					}
				}
			}
		}
		time.Sleep(time.Duration(poll * float64(time.Second)))
	}

	job := b.readJob(jobID)
	if job == nil {
		msg := "job missing"
		return &ComputerJob{ID: jobID, Action: "?", Status: "error", Error: &msg}
	}
	if job.Status == "done" || job.Status == "error" || job.Status == "cancelled" {
		return job
	}
	grace := 0.5
	if opts.GraceS != nil {
		grace = *opts.GraceS
	} else if strings.ToLower(job.Action) == "navigate" {
		grace = timeout * 0.25
		if grace < 0.05 {
			grace = 0.05
		}
		if grace > 2.5 {
			grace = 2.5
		}
	} else {
		grace = timeout * 0.1
		if grace < 0.05 {
			grace = 0.05
		}
		if grace > 0.5 {
			grace = 0.5
		}
	}
	graceDeadline := time.Now().Add(time.Duration(grace * float64(time.Second)))
	for time.Now().Before(graceDeadline) {
		time.Sleep(20 * time.Millisecond)
		job = b.readJob(jobID)
		if job != nil && (job.Status == "done" || job.Status == "error" || job.Status == "cancelled") {
			return job
		}
	}
	job = b.readJob(jobID)
	if job == nil {
		msg := "job missing"
		return &ComputerJob{ID: jobID, Action: "?", Status: "error", Error: &msg}
	}
	if job.Status == "done" || job.Status == "error" || job.Status == "cancelled" {
		return job
	}
	msg := "timeout waiting for desktop host (" + formatFloat1(timeout) + "s)"
	b.mu.Lock()
	cur := b.readJob(jobID)
	if cur != nil && (cur.Status == "done" || cur.Status == "error" || cur.Status == "cancelled") {
		b.mu.Unlock()
		return cur
	}
	if cur != nil && (cur.Status == "pending" || cur.Status == "running") {
		cur.Status = "error"
		cur.Error = &msg
		cur.Payload = scrubRetainedPayload(cur.Payload)
		_ = b.writeJob(cur)
		b.mu.Unlock()
		return cur
	}
	b.mu.Unlock()
	return job
}

func newComputerJobID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

func formatFloat1(v float64) string {
	// Avoid fmt import churn in hot path; one decimal is enough for timeout msgs.
	i := int(v*10 + 0.5)
	whole := i / 10
	frac := i % 10
	return itoa(whole) + "." + itoa(frac)
}

func (b *HostBridge) takeUICommand(sessionID string) map[string]any {
	b.mu.Lock()
	defer b.mu.Unlock()
	var cmd map[string]any
	if b.uiCommand != nil {
		cmd = cloneMap(b.uiCommand)
	} else if raw, err := os.ReadFile(b.uiPath); err == nil {
		var m map[string]any
		if json.Unmarshal(raw, &m) == nil && m["action"] != nil {
			cmd = m
		}
	}
	want := strings.TrimSpace(sessionID)
	if want == "" {
		want = b.focusedSession
	}
	cmdSID := strOr((cmd)["session_id"], "")
	if !b.railAllowsLocked(cmdSID, want) {
		return nil
	}
	b.uiCommand = nil
	_ = os.Remove(b.uiPath)
	return cmd
}

func (b *HostBridge) isStreaming(sid string) bool {
	if b.sessionStreaming == nil || sid == "" {
		return false
	}
	return b.sessionStreaming(sid)
}

func (b *HostBridge) railAllowsLocked(jobSID, want string) bool {
	if want == "" || jobSID == "" || jobSID == want {
		return true
	}
	return !b.isStreaming(want) && b.isStreaming(jobSID)
}

func (b *HostBridge) jobPath(id string) string {
	safe := strings.Map(func(r rune) rune {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') || r == '-' || r == '_' {
			return r
		}
		return -1
	}, id)
	if safe == "" {
		safe = "job"
	}
	return filepath.Join(b.root, safe+".json")
}

func (b *HostBridge) writeJob(job *ComputerJob) error {
	job.UpdatedAt = time.Now().UTC().Format(time.RFC3339Nano)
	data := job.toDict()
	data["payload"] = sealPayload(job.Payload)
	raw, err := json.MarshalIndent(data, "", "  ")
	if err != nil {
		return err
	}
	return secret.WriteFileAtomic(b.jobPath(job.ID), raw, 0o600)
}

func (b *HostBridge) readJob(id string) *ComputerJob {
	raw, err := os.ReadFile(b.jobPath(id))
	if err != nil {
		return nil
	}
	var m map[string]any
	if json.Unmarshal(raw, &m) != nil {
		return nil
	}
	return jobFromDisk(m)
}

func jobFromDisk(raw map[string]any) *ComputerJob {
	pl, err := unsealPayload(asMap(raw["payload"]))
	if err != nil {
		return nil
	}
	sid := strOr(raw["session_id"], "")
	if sid == "" {
		sid = strOr(pl["session_id"], "")
	}
	var result map[string]any
	if r, ok := raw["result"].(map[string]any); ok {
		result = r
	}
	var errStr *string
	if e, ok := raw["error"].(string); ok {
		errStr = &e
	}
	return &ComputerJob{
		ID: strOr(raw["id"], ""), Action: strOr(raw["action"], ""),
		Payload: pl, Status: strOr(raw["status"], "pending"),
		Result: result, Error: errStr,
		CreatedAt: strOr(raw["created_at"], time.Now().UTC().Format(time.RFC3339Nano)),
		UpdatedAt: strOr(raw["updated_at"], time.Now().UTC().Format(time.RFC3339Nano)),
		SessionID: sid,
	}
}

func (b *HostBridge) claimNext(exclude, only map[string]struct{}, sessionID string, waitS float64) *ComputerJob {
	deadline := time.Now().Add(time.Duration(waitS * float64(time.Second)))
	for {
		if job := b.claimNextOnce(exclude, only, sessionID); job != nil {
			return job
		}
		if waitS <= 0 || time.Now().After(deadline) {
			return nil
		}
		remaining := time.Until(deadline)
		if remaining <= 0 {
			return nil
		}
		sleep := 50 * time.Millisecond
		b.mu.Lock()
		idle := b.pollIdleEmpty
		b.mu.Unlock()
		if idle {
			sleep = 500 * time.Millisecond
			if sleep > remaining {
				sleep = remaining
			}
			select {
			case <-b.wake:
			case <-time.After(sleep):
			}
		} else {
			if sleep > remaining {
				sleep = remaining
			}
			time.Sleep(sleep)
		}
	}
}

func (b *HostBridge) claimNextOnce(exclude, only map[string]struct{}, sessionID string) *ComputerJob {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.markHostAliveLocked(true, b.lastPollDriver)
	if b.pollIdleEmpty {
		return nil
	}
	want := strings.TrimSpace(sessionID)
	if want == "" {
		want = b.focusedSession
	}
	entries, err := os.ReadDir(b.root)
	if err != nil {
		b.pollIdleEmpty = true
		return nil
	}
	type fileStat struct {
		name string
		mod  time.Time
	}
	var files []fileStat
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		info, err := e.Info()
		if err != nil {
			continue
		}
		files = append(files, fileStat{e.Name(), info.ModTime()})
	}
	for i := 0; i < len(files); i++ {
		for j := i + 1; j < len(files); j++ {
			if files[j].mod.Before(files[i].mod) {
				files[i], files[j] = files[j], files[i]
			}
		}
	}
	sawPending := false
	for _, f := range files {
		raw, err := os.ReadFile(filepath.Join(b.root, f.name))
		if err != nil {
			continue
		}
		var m map[string]any
		if json.Unmarshal(raw, &m) != nil {
			continue
		}
		job := jobFromDisk(m)
		if job == nil || job.Status != "pending" {
			continue
		}
		sawPending = true
		act := strings.ToLower(job.Action)
		if _, skip := exclude[act]; skip {
			continue
		}
		if only != nil {
			if _, ok := only[act]; !ok {
				continue
			}
		}
		jobSID := strings.TrimSpace(job.SessionID)
		if jobSID == "" {
			jobSID = strOr(job.Payload["session_id"], "")
		}
		if !b.railAllowsLocked(jobSID, want) {
			continue
		}
		job.Status = "running"
		_ = b.writeJob(job)
		b.lastClaimAt = float64(time.Now().UnixNano()) / 1e9
		b.pollIdleEmpty = false
		return job
	}
	if !sawPending {
		b.pollIdleEmpty = true
	}
	return nil
}

func (b *HostBridge) complete(jobID string, ok bool, result map[string]any, errMsg *string) *ComputerJob {
	b.mu.Lock()
	defer b.mu.Unlock()
	job := b.readJob(jobID)
	if job == nil {
		return nil
	}
	safeResult := scrubJobResult(result)
	var safeErr *string
	if errMsg != nil {
		s := scrubJobError(*errMsg)
		safeErr = &s
	}
	if job.Status == "cancelled" {
		return job
	}
	if ok {
		job.Status = "done"
		job.Result = safeResult
		job.Error = nil
	} else if job.Status == "done" {
		return job
	} else {
		job.Status = "error"
		job.Result = safeResult
		job.Error = safeErr
	}
	if job.Status == "done" || job.Status == "error" || job.Status == "cancelled" {
		job.Payload = scrubRetainedPayload(job.Payload)
	}
	_ = b.writeJob(job)
	if observed := observedURLFromResult(safeResult, job); observed != "" {
		b.lastObservedURL = observed
		if sid := strings.TrimSpace(job.SessionID); sid != "" {
			if b.lastObservedURLBySession == nil {
				b.lastObservedURLBySession = map[string]string{}
			}
			b.lastObservedURLBySession[sid] = observed
		}
	}
	return job
}

func observedURLFromResult(result map[string]any, job *ComputerJob) string {
	if result != nil {
		for _, k := range []string{"url", "final_url", "current_url", "href"} {
			if u := strings.TrimSpace(strOr(result[k], "")); u != "" {
				return u
			}
		}
	}
	if job != nil {
		if u := strings.TrimSpace(strOr(job.Payload["url"], "")); u != "" {
			return u
		}
	}
	return ""
}

func (b *HostBridge) cancel(jobID string) *ComputerJob {
	b.mu.Lock()
	defer b.mu.Unlock()
	job := b.readJob(jobID)
	if job == nil {
		return nil
	}
	if job.Status == "done" || job.Status == "error" || job.Status == "cancelled" {
		return job
	}
	job.Status = "cancelled"
	msg := "cancelled"
	job.Error = &msg
	job.Payload = scrubRetainedPayload(job.Payload)
	_ = b.writeJob(job)
	return job
}

func (b *HostBridge) completeA11yPush(jobID string, elements []map[string]any) *ComputerJob {
	b.mu.Lock()
	defer b.mu.Unlock()
	job := b.readJob(jobID)
	if job == nil {
		return nil
	}
	if job.Action != "snapshot" && job.Action != "a11y" {
		return nil
	}
	if job.Status != "pending" && job.Status != "running" {
		return nil
	}
	trimmed := elements
	if len(trimmed) > 120 {
		trimmed = trimmed[:120]
	}
	safe := scrubJobResult(map[string]any{
		"ok": true, "target": "browser", "action": "snapshot",
		"message":  itoa(len(trimmed)) + " interactive elements",
		"elements": trimmed,
	})
	job.Status = "done"
	job.Result = safe
	job.Payload = scrubRetainedPayload(job.Payload)
	b.lastElements = scrubElements(trimmed)
	_ = b.writeJob(job)
	return job
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b [20]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}

func sealPayload(payload map[string]any) map[string]any {
	if payload == nil {
		return map[string]any{}
	}
	if _, sealed := payload["_sealed"]; sealed {
		return payload
	}
	plain, err := json.Marshal(payload)
	if err != nil {
		return map[string]any{}
	}
	cipher, err := secret.Protect(plain)
	if err != nil {
		// Fail closed for secret-bearing payloads; otherwise keep plain.
		if payloadHasSecrets(payload) {
			return map[string]any{}
		}
		return cloneMap(payload)
	}
	return map[string]any{
		"_sealed":  true,
		"encoding": "dpapi",
		"blob":     base64.StdEncoding.EncodeToString(cipher),
	}
}

func unsealPayload(payload map[string]any) (map[string]any, error) {
	if payload == nil {
		return map[string]any{}, nil
	}
	if payload["_sealed"] != true {
		return cloneMap(payload), nil
	}
	enc := strings.ToLower(strOr(payload["encoding"], ""))
	blob, _ := payload["blob"].(string)
	if (enc != "dpapi" && enc != "nacl") || strings.TrimSpace(blob) == "" {
		return nil, errSealUnreadable
	}
	if enc == "nacl" {
		// NaCl seals are Linux Python path; fail closed in this Go slice.
		return nil, errSealUnreadable
	}
	cipher, err := base64.StdEncoding.DecodeString(blob)
	if err != nil {
		return nil, errSealUnreadable
	}
	plain, err := secret.Unprotect(cipher)
	if err != nil {
		return nil, errSealUnreadable
	}
	var inner map[string]any
	if json.Unmarshal(plain, &inner) != nil {
		return nil, errSealUnreadable
	}
	return inner, nil
}

var errSealUnreadable = errString("computer job payload seal is unreadable")

type errString string

func (e errString) Error() string { return string(e) }

func payloadHasSecrets(payload map[string]any) bool {
	if payload == nil {
		return false
	}
	if payload["_has_secret"] == true {
		return true
	}
	text := strOr(payload["text"], "")
	if strings.Contains(text, "{{") && strings.Contains(strings.ToLower(text), "vault") {
		return true
	}
	for k, v := range payload {
		kl := strings.ToLower(strings.ReplaceAll(k, "-", "_"))
		_, named := payloadSecretKeys[kl]
		if named || strings.HasSuffix(kl, "_password") || strings.HasSuffix(kl, "_secret") {
			if s, ok := v.(string); ok && strings.TrimSpace(s) != "" {
				return true
			}
			if _, ok := v.(map[string]any); ok {
				return true
			}
		}
	}
	return false
}

func scrubElements(els []map[string]any) []map[string]any {
	out := make([]map[string]any, 0, len(els))
	for i, el := range els {
		if i >= 120 {
			break
		}
		e := cloneMap(el)
		if elementLooksSecret(e) || e["value_redacted"] == true {
			if s, ok := e["value"].(string); ok && s != "" {
				e["value"] = "[filled]"
				e["value_redacted"] = true
			}
		} else if strings.ToLower(strOr(e["tag"], "")) == "input" {
			if s, ok := e["value"].(string); ok && len(s) > 40 {
				e["value"] = s[:40] + "…"
			}
		}
		out = append(out, e)
	}
	return out
}

func elementLooksSecret(el map[string]any) bool {
	itype := strings.ToLower(strOr(el["type"], strOr(el["input_type"], strOr(el["itype"], ""))))
	if itype == "password" {
		return true
	}
	blob := strings.ToLower(strings.Join([]string{
		strOr(el["name"], ""), strOr(el["id"], ""), strOr(el["label"], ""),
		strOr(el["aria-label"], ""), strOr(el["placeholder"], ""), strOr(el["role"], ""),
	}, " "))
	for _, k := range []string{"password", "passwd", "passcode", "secret", "api_key", "token", "credential", "cvv"} {
		if strings.Contains(blob, k) {
			return true
		}
	}
	return false
}

func scrubJobResult(result map[string]any) map[string]any {
	if result == nil {
		return nil
	}
	out := cloneMap(result)
	for _, key := range resultTextKeys {
		if s, ok := out[key].(string); ok && len(s) > jobTextMax {
			out[key] = s[:jobTextMax-1] + "…"
			out[key+"_truncated"] = true
		}
	}
	if els, ok := out["elements"].([]any); ok {
		maps := make([]map[string]any, 0, len(els))
		for _, e := range els {
			if m, ok := e.(map[string]any); ok {
				maps = append(maps, m)
			}
		}
		cleaned := scrubElements(maps)
		anyEls := make([]any, len(cleaned))
		for i, m := range cleaned {
			anyEls[i] = m
		}
		out["elements"] = anyEls
	}
	return out
}

func scrubJobError(error string) string {
	if error == "" {
		return ""
	}
	if len(error) > 2000 {
		return error[:2000]
	}
	return error
}

func scrubRetainedPayload(payload map[string]any) map[string]any {
	if payload == nil {
		return map[string]any{}
	}
	out := cloneMap(payload)
	secretish := out["_has_secret"] == true || payloadHasSecrets(payload)
	for k, v := range out {
		kl := strings.ToLower(strings.ReplaceAll(k, "-", "_"))
		_, named := payloadSecretKeys[kl]
		if named || strings.HasSuffix(kl, "_password") || strings.HasSuffix(kl, "_secret") {
			if s, ok := v.(string); ok && s != "" {
				if secretish {
					out[k] = "[redacted]"
				} else {
					out[k] = "[redacted chars=" + itoa(len(s)) + "]"
				}
			} else if v != nil {
				switch v.(type) {
				case bool, float64, int:
					// keep scalars
				default:
					out[k] = "[redacted]"
				}
			}
		}
	}
	return out
}

func asFloat(v any) float64 {
	switch t := v.(type) {
	case float64:
		return t
	case int:
		return float64(t)
	case json.Number:
		f, _ := t.Float64()
		return f
	default:
		return 0
	}
}

func asMap(v any) map[string]any {
	if m, ok := v.(map[string]any); ok {
		return m
	}
	return nil
}

func strOr(v any, def string) string {
	if s, ok := v.(string); ok {
		return s
	}
	return def
}

