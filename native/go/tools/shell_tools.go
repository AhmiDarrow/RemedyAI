package tools

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
)

// bash is the command-string shell a frontier model actually writes, and jobs
// is the background half of it. Neither of them is a second execution path:
// bash builds the platform shell argv and hands it to spawnAuthorized, the
// same call shell.exec makes, so the Zig deny classifier (which already
// unwraps cmd / bash / pwsh wrappers and scans the payload — see
// policy.isDangerousProcess), the capability token, the write jail and the
// kill-tree all see the real command.

const (
	bashDefaultTimeoutMS = 600_000
	bashMaxTimeoutMS     = 600_000
	bashOutputHead       = 64 << 10
	bashOutputTail       = 64 << 10

	jobLogLimit      = 8 << 20
	jobsListLimit    = 50
	jobTailDefault   = 100
	jobTailMaxLines  = 1000
	jobTailMaxBytes  = 256 << 10
	jobIDRandomBytes = 6
)

// sessionIDPattern is what may become a directory name under <home>/sessions.
var sessionIDPattern = regexp.MustCompile(`^[A-Za-z0-9._-]{1,128}$`)

// RegisterShellTools installs bash and jobs.
func RegisterShellTools(registry *Registry) error {
	if registry == nil {
		return fmt.Errorf("%w: nil registry", ErrInvalidDescriptor)
	}

	if err := registry.Register(Descriptor{
		ID:      "bash",
		Version: 1,
		Description: "Run a shell command string (cmd on Windows, bash on POSIX) and capture its output. " +
			"Always returns exit_code. Set background:true for a long-running command and follow it with the jobs tool. " +
			"Cancellation and timeout kill the whole process tree.",
		Runtime:      RuntimeZig,
		Risk:         RiskMutation,
		Capabilities: []string{"process.spawn"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["command"],
			"properties":{
				"command":{"type":"string","minLength":1,"description":"The command line to run, exactly as you would type it in a terminal. Pipes, redirection and && are the host shell's."},
				"cwd":{"type":"string","description":"Absolute working directory. Defaults to the bound project folder; a directory outside the allowed scope is clamped back to it."},
				"timeout_ms":{"type":"integer","minimum":1,"maximum":600000,"description":"Kill the command after this long (default 600000, ten minutes). On timeout exit_code is 1 and timed_out is true."},
				"background":{"type":"boolean","description":"Start the command and return a job_id immediately instead of waiting. Use the jobs tool to tail or kill it."},
				"env":{"type":"object","additionalProperties":{"type":"string"},"description":"Extra environment variables merged into the child's environment. Module-search paths (PYTHONPATH, NODE_PATH, …) are refused."},
				"write_roots":{"type":"array","items":{"type":"string","minLength":1},"maxItems":16,"description":"Write-jail roots (bound by the runtime from the session; model-supplied values are ignored)"},
				` + propHomeDir + `,
				"session_id":{"type":"string","description":"Session that owns a background job (bound by the runtime; model-supplied values are ignored)"}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"properties":{
				"exit_code":{"type":"integer","minimum":0},
				"timed_out":{"type":"boolean"},
				"stdout":{"type":"string"},
				"stderr":{"type":"string"},
				"stdout_truncated":{"type":"boolean"},
				"stderr_truncated":{"type":"boolean"},
				"command":{"type":"string"},
				"cwd":{"type":"string"},
				"job_id":{"type":"string"},
				"status":{"type":"string"},
				"log_path":{"type":"string"},
				"pid":{"type":"integer","minimum":0}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeBash)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:      "jobs",
		Version: 1,
		Description: "List, tail or kill the background commands started by bash with background:true. " +
			"list and tail only read; kill terminates the whole process tree.",
		Runtime:      RuntimeGo,
		Risk:         RiskMutation,
		Capabilities: []string{"process.spawn"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["action"],
			"properties":{
				"action":{"type":"string","enum":["list","tail","kill"],"description":"list every job of this session, tail one job's output, or kill one job."},
				"job_id":{"type":"string","description":"Job to tail or kill, as returned by bash with background:true. Required for tail and kill."},
				"lines":{"type":"integer","minimum":1,"maximum":1000,"description":"How many trailing output lines tail returns (default 100)."},
				` + propHomeDir + `,
				"session_id":{"type":"string","description":"Session that owns the jobs (bound by the runtime; model-supplied values are ignored)"}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["action"],
			"properties":{
				"action":{"type":"string"},
				"jobs":{"type":"array","items":{"type":"object"}},
				"job":{"type":"object"},
				"count":{"type":"integer","minimum":0},
				"output":{"type":"string"},
				"truncated":{"type":"boolean"}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeJobs)); err != nil {
		return err
	}

	return nil
}

// ---------------------------------------------------------------------------
// bash
// ---------------------------------------------------------------------------

func executeBash(ctx context.Context, request Request) (Result, error) {
	var body struct {
		Command    string            `json:"command"`
		Cwd        string            `json:"cwd"`
		TimeoutMS  uint32            `json:"timeout_ms"`
		Background bool              `json:"background"`
		Env        map[string]string `json:"env"`
		WriteRoots []string          `json:"write_roots"`
		HomeDir    string            `json:"home_dir"`
		SessionID  string            `json:"session_id"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, fmt.Errorf("%w: %v", ErrInvalidInput, err)
	}
	command := strings.TrimSpace(body.Command)
	if command == "" {
		return Result{}, fmt.Errorf("%w: command is required — pass the command line as a single string", ErrInvalidInput)
	}
	if body.Cwd != "" && !filepath.IsAbs(body.Cwd) {
		return Result{}, fmt.Errorf("%w: cwd must be an absolute path when set (got %q)", ErrInvalidInput, body.Cwd)
	}
	if bad := modelDeniedEnvKey(body.Env); bad != "" {
		return Result{}, fmt.Errorf(
			"%w: %s cannot be set from a tool call — it makes an interpreter load code "+
				"before the command runs. Put the path on the command line instead",
			ErrInvalidInput, bad)
	}
	argv, err := platformShellArgv(command)
	if err != nil {
		return Result{}, err
	}
	spec := shellSpawnSpec{argv: argv, cwd: body.Cwd, env: body.Env, writeRoots: body.WriteRoots}

	if body.Background {
		return startBackgroundJob(ctx, spec, command, body.HomeDir, body.SessionID)
	}

	timeout := body.TimeoutMS
	if timeout == 0 {
		timeout = bashDefaultTimeoutMS
	}
	if timeout > bashMaxTimeoutMS {
		timeout = bashMaxTimeoutMS
	}
	proc, err := spawnAuthorized(ctx, spec)
	if err != nil {
		return Result{}, err
	}
	capture := waitShellCapture(ctx, proc, time.Duration(timeout)*time.Millisecond)
	if capture.err != nil {
		return Result{}, capture.err
	}
	stdout, outClipped := clipHeadTail(capture.stdout)
	stderr, errClipped := clipHeadTail(capture.stderr)
	out, err := json.Marshal(map[string]any{
		"exit_code":        capture.exitCode,
		"timed_out":        capture.timedOut,
		"stdout":           stdout,
		"stderr":           stderr,
		"stdout_truncated": outClipped,
		"stderr_truncated": errClipped,
		"command":          command,
		"cwd":              body.Cwd,
	})
	return Result{Output: out}, err
}

// platformShellArgv wraps a command string in the host shell. The wrapper is
// the argv the classifier inspects: policy.isDangerousProcess recognises cmd,
// bash, sh and pwsh as wrappers and scans the payload that follows.
func platformShellArgv(command string) ([]string, error) {
	if runtime.GOOS == "windows" {
		shell := filepath.Join(os.Getenv("SystemRoot"), "System32", "cmd.exe")
		if _, err := os.Stat(shell); err != nil {
			resolved, lookErr := lookPath("cmd.exe")
			if lookErr != nil {
				return nil, fmt.Errorf("%w: no command shell on this host: %v", ErrInvalidInput, lookErr)
			}
			shell = resolved
		}
		abs, err := filepath.Abs(shell)
		if err != nil {
			return nil, fmt.Errorf("%w: command shell path %q is unusable: %v", ErrInvalidInput, shell, err)
		}
		// /d skips AutoRun, /s keeps the quoting of the command string intact.
		return []string{abs, "/d", "/s", "/c", command}, nil
	}
	shell, err := lookPath("bash")
	flag := "-lc"
	if err != nil {
		shell, err = lookPath("sh")
		flag = "-c"
		if err != nil {
			return nil, fmt.Errorf("%w: neither bash nor sh is on PATH: %v", ErrInvalidInput, err)
		}
	}
	abs, absErr := filepath.Abs(shell)
	if absErr != nil {
		return nil, fmt.Errorf("%w: shell path %q is unusable: %v", ErrInvalidInput, shell, absErr)
	}
	return []string{abs, flag, command}, nil
}

// clipHeadTail keeps the first and last 64 KB of a captured stream. A verbose
// test run has its failing summary at the end, so a head-only clip hides
// exactly the part the model needs.
func clipHeadTail(b []byte) (string, bool) {
	if len(b) <= bashOutputHead+bashOutputTail {
		return string(b), false
	}
	head := b[:runeFloorAt(b, bashOutputHead)]
	start := runeCeilAt(b, len(b)-bashOutputTail)
	note := fmt.Sprintf("\n…[remedy: %d bytes omitted from the middle]…\n", start-len(head))
	return string(head) + note + string(b[start:]), true
}

// runeFloorAt returns the largest index <= i that starts a rune.
func runeFloorAt(b []byte, i int) int {
	for i > 0 && i < len(b) && !utf8.RuneStart(b[i]) {
		i--
	}
	return i
}

// runeCeilAt returns the smallest index >= i that starts a rune.
func runeCeilAt(b []byte, i int) int {
	for i < len(b) && !utf8.RuneStart(b[i]) {
		i++
	}
	return i
}

// ---------------------------------------------------------------------------
// background jobs
// ---------------------------------------------------------------------------

// jobRecord is the on-disk state of one background command. It is the source
// of truth for list / tail / kill, so a job survives a turn ending.
type jobRecord struct {
	ID        string `json:"id"`
	SessionID string `json:"session_id"`
	Command   string `json:"command"`
	Cwd       string `json:"cwd"`
	PID       uint32 `json:"pid"`
	Status    string `json:"status"`
	ExitCode  *int64 `json:"exit_code,omitempty"`
	StartedMS int64  `json:"started_ms"`
	EndedMS   int64  `json:"ended_ms,omitempty"`
	LogPath   string `json:"log_path"`
}

// jobStateMu serializes record writes with the reads list/tail/kill make.
var jobStateMu sync.Mutex

func jobsDir(home, sessionID string) (string, error) {
	home = strings.TrimSpace(home)
	if home == "" {
		home = resolveToolHome()
	}
	if home == "" {
		return "", fmt.Errorf("%w: no Remedy home is available, so background jobs cannot be tracked", ErrInvalidInput)
	}
	sid := strings.TrimSpace(sessionID)
	if sid == "" {
		return "", fmt.Errorf("%w: background jobs belong to a session and this call has none", ErrInvalidInput)
	}
	if !sessionIDPattern.MatchString(sid) {
		return "", fmt.Errorf("%w: session id %q is not a usable directory name", ErrInvalidInput, sid)
	}
	return filepath.Join(home, "sessions", sid, "jobs"), nil
}

func newJobID() string {
	buf := make([]byte, jobIDRandomBytes)
	if _, err := rand.Read(buf); err != nil {
		return fmt.Sprintf("job_%d", time.Now().UnixNano())
	}
	return "job_" + hex.EncodeToString(buf)
}

func startBackgroundJob(ctx context.Context, spec shellSpawnSpec, command, home, sessionID string) (Result, error) {
	dir, err := jobsDir(home, sessionID)
	if err != nil {
		return Result{}, err
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return Result{}, fmt.Errorf("%w: could not create the job directory %s: %v", ErrInvalidInput, dir, err)
	}
	id := newJobID()
	logPath := filepath.Join(dir, id+".log")
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		return Result{}, fmt.Errorf("%w: could not open the job log %s: %v", ErrInvalidInput, logPath, err)
	}
	proc, err := spawnAuthorized(ctx, spec)
	if err != nil {
		_ = logFile.Close()
		_ = os.Remove(logPath)
		return Result{}, err
	}
	record := jobRecord{
		ID:        id,
		SessionID: strings.TrimSpace(sessionID),
		Command:   command,
		Cwd:       spec.cwd,
		PID:       proc.PID,
		Status:    "running",
		StartedMS: time.Now().UnixMilli(),
		LogPath:   filepath.ToSlash(logPath),
	}
	if err := writeJobRecord(dir, record); err != nil {
		_ = core.ProcessKillTree(proc.PID)
		_ = logFile.Close()
		return Result{}, err
	}
	// The job outlives this tool call by design, so it is reaped on its own
	// goroutine and never on the call's context.
	go superviseJob(dir, record, proc, logFile)

	out, err := json.Marshal(map[string]any{
		"job_id":   id,
		"status":   "running",
		"log_path": record.LogPath,
		"pid":      int64(proc.PID),
		"command":  command,
		"cwd":      spec.cwd,
	})
	return Result{Output: out}, err
}

// superviseJob streams the child's output into the job log, waits for it and
// records the outcome.
func superviseJob(dir string, record jobRecord, proc core.PipedProcess, logFile *os.File) {
	stdin, stdout, stderr := proc.Files()
	_ = stdin.Close()
	sink := &lockedLimitWriter{w: logFile, limit: jobLogLimit}

	var wg sync.WaitGroup
	wg.Add(2)
	go func() { defer wg.Done(); _, _ = io.Copy(sink, stdout) }()
	go func() { defer wg.Done(); _, _ = io.Copy(sink, stderr) }()

	var (
		code    *uint32
		waitErr error
	)
	for {
		c, err := core.ProcessWait(proc.Handle, 200)
		if err != nil {
			waitErr = err
			break
		}
		if c != nil {
			code = c
			break
		}
	}
	wg.Wait()
	_ = stdout.Close()
	_ = stderr.Close()
	_ = core.ProcessClose(proc.Handle)
	_ = logFile.Close()

	jobStateMu.Lock()
	current, err := readJobRecord(dir, record.ID)
	jobStateMu.Unlock()
	if err == nil {
		record = current
	}
	record.EndedMS = time.Now().UnixMilli()
	switch {
	case waitErr != nil:
		record.Status = "failed"
	case record.Status == "killed":
		// A kill already recorded the outcome; keep it and add the code.
	default:
		record.Status = "exited"
	}
	if code != nil {
		exit := int64(*code)
		record.ExitCode = &exit
	}
	_ = writeJobRecord(dir, record)
}

// lockedLimitWriter bounds one job log and is safe for the two copy goroutines.
type lockedLimitWriter struct {
	mu      sync.Mutex
	w       io.Writer
	limit   int
	written int
	noted   bool
}

func (l *lockedLimitWriter) Write(p []byte) (int, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	room := l.limit - l.written
	if room <= 0 {
		if !l.noted {
			l.noted = true
			_, _ = l.w.Write([]byte(fmt.Sprintf("\n[remedy: job log stopped at the %d-byte limit]\n", l.limit)))
		}
		return len(p), nil
	}
	if room > len(p) {
		room = len(p)
	}
	n, err := l.w.Write(p[:room])
	l.written += n
	return len(p), err
}

func jobRecordPath(dir, id string) string { return filepath.Join(dir, id+".json") }

func writeJobRecord(dir string, record jobRecord) error {
	data, err := json.Marshal(record)
	if err != nil {
		return err
	}
	jobStateMu.Lock()
	defer jobStateMu.Unlock()
	tmp := jobRecordPath(dir, record.ID) + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return fmt.Errorf("%w: could not record job %s: %v", ErrInvalidInput, record.ID, err)
	}
	if err := os.Rename(tmp, jobRecordPath(dir, record.ID)); err != nil {
		_ = os.Remove(tmp)
		return fmt.Errorf("%w: could not record job %s: %v", ErrInvalidInput, record.ID, err)
	}
	return nil
}

func readJobRecord(dir, id string) (jobRecord, error) {
	var record jobRecord
	data, err := os.ReadFile(jobRecordPath(dir, id))
	if err != nil {
		return record, err
	}
	err = json.Unmarshal(data, &record)
	return record, err
}

// ---------------------------------------------------------------------------
// jobs
// ---------------------------------------------------------------------------

func executeJobs(_ context.Context, request Request) (Result, error) {
	var body struct {
		Action    string `json:"action"`
		JobID     string `json:"job_id"`
		Lines     int    `json:"lines"`
		HomeDir   string `json:"home_dir"`
		SessionID string `json:"session_id"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, fmt.Errorf("%w: %v", ErrInvalidInput, err)
	}
	dir, err := jobsDir(body.HomeDir, body.SessionID)
	if err != nil {
		return Result{}, err
	}
	action := strings.TrimSpace(body.Action)
	switch action {
	case "list":
		return listJobs(dir)
	case "tail":
		return tailJob(dir, body.JobID, body.Lines)
	case "kill":
		return killJob(dir, body.JobID)
	default:
		return Result{}, fmt.Errorf("%w: action must be list, tail or kill (got %q)", ErrInvalidInput, action)
	}
}

func listJobs(dir string) (Result, error) {
	entries, err := os.ReadDir(dir)
	if err != nil && !os.IsNotExist(err) {
		return Result{}, fmt.Errorf("%w: could not read the job directory %s: %v", ErrInvalidInput, dir, err)
	}
	records := make([]jobRecord, 0, len(entries))
	jobStateMu.Lock()
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		id := strings.TrimSuffix(entry.Name(), ".json")
		if record, err := readJobRecord(dir, id); err == nil {
			records = append(records, record)
		}
	}
	jobStateMu.Unlock()
	sort.Slice(records, func(i, j int) bool { return records[i].StartedMS > records[j].StartedMS })
	if len(records) > jobsListLimit {
		records = records[:jobsListLimit]
	}
	rows := make([]map[string]any, 0, len(records))
	for _, record := range records {
		rows = append(rows, jobPublic(record))
	}
	out, err := json.Marshal(map[string]any{"action": "list", "jobs": rows, "count": len(rows)})
	return Result{Output: out}, err
}

func tailJob(dir, id string, lines int) (Result, error) {
	record, err := requireJob(dir, id)
	if err != nil {
		return Result{}, err
	}
	if lines <= 0 {
		lines = jobTailDefault
	}
	if lines > jobTailMaxLines {
		lines = jobTailMaxLines
	}
	text, truncated, err := tailFile(record.LogPath, lines)
	if err != nil {
		return Result{}, err
	}
	out, err := json.Marshal(map[string]any{
		"action": "tail", "job": jobPublic(record), "output": text, "truncated": truncated,
	})
	return Result{Output: out}, err
}

func killJob(dir, id string) (Result, error) {
	record, err := requireJob(dir, id)
	if err != nil {
		return Result{}, err
	}
	if record.Status != "running" {
		out, merr := json.Marshal(map[string]any{"action": "kill", "job": jobPublic(record)})
		return Result{Output: out}, merr
	}
	if err := core.ProcessKillTree(record.PID); err != nil {
		return Result{}, fmt.Errorf("job %s (pid %d) could not be killed: %w", record.ID, record.PID, err)
	}
	record.Status = "killed"
	record.EndedMS = time.Now().UnixMilli()
	if err := writeJobRecord(dir, record); err != nil {
		return Result{}, err
	}
	out, err := json.Marshal(map[string]any{"action": "kill", "job": jobPublic(record)})
	return Result{Output: out}, err
}

func requireJob(dir, id string) (jobRecord, error) {
	id = strings.TrimSpace(id)
	if id == "" {
		return jobRecord{}, fmt.Errorf("%w: job_id is required — call jobs with action list to see the ids", ErrInvalidInput)
	}
	if !sessionIDPattern.MatchString(id) {
		return jobRecord{}, fmt.Errorf("%w: %q is not a job id", ErrInvalidInput, id)
	}
	jobStateMu.Lock()
	record, err := readJobRecord(dir, id)
	jobStateMu.Unlock()
	if err != nil {
		return jobRecord{}, fmt.Errorf("%w: no job %q in this session — call jobs with action list", ErrInvalidInput, id)
	}
	return record, nil
}

func jobPublic(record jobRecord) map[string]any {
	row := map[string]any{
		"id":         record.ID,
		"command":    record.Command,
		"status":     record.Status,
		"pid":        int64(record.PID),
		"started_ms": record.StartedMS,
		"log_path":   record.LogPath,
	}
	if record.Cwd != "" {
		row["cwd"] = record.Cwd
	}
	if record.ExitCode != nil {
		row["exit_code"] = *record.ExitCode
	}
	if record.EndedMS != 0 {
		row["ended_ms"] = record.EndedMS
	}
	return row
}

// tailFile returns the last n lines of a job log, bounded by bytes as well as
// lines so a single enormous line cannot flood the transcript.
func tailFile(path string, n int) (string, bool, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return "", false, nil
		}
		return "", false, fmt.Errorf("%w: could not read the job log %s: %v", ErrInvalidInput, path, err)
	}
	truncated := false
	if len(data) > jobTailMaxBytes {
		data = data[runeCeilAt(data, len(data)-jobTailMaxBytes):]
		truncated = true
	}
	lines := splitLines(string(data))
	if len(lines) > n {
		lines = lines[len(lines)-n:]
		truncated = true
	}
	return strings.Join(lines, "\n"), truncated, nil
}
