package httpapi

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

const (
	maxTerminals     = 16
	idleKeepaliveS   = 10.0
	idleGraceS       = 60.0
	readChunk        = 8192
	maxInputBytes    = 65536
	maxQueueBytes    = 512 * 1024
	maxSSEBatch      = 16 * 1024
	winUTF8Init      = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;" +
		" [Console]::InputEncoding=[System.Text.Encoding]::UTF8;" +
		" chcp 65001 > $null"
)

var ansiRE = regexp.MustCompile(
	`\x1b\[[0-9;?]*[ -/]*[@-~]` +
		`|\x1b\][^\x07]*(?:\x07|\x1b\\)` +
		`|\x1b[()][0-9A-Za-z]` +
		`|\x1b[=>]`,
)

// TerminalProc is the ConPTY / test-double process surface.
type TerminalProc interface {
	Write(data []byte) error
	ReadSync(n int) ([]byte, error)
	Poll() *int
	Kill()
	Resize(cols, rows int) error
}

// TerminalSpawnFunc is a test hook (cwd, cols, rows) -> proc.
type TerminalSpawnFunc func(cwd string, cols, rows int) (TerminalProc, error)

type terminalSession struct {
	id        string
	proc      TerminalProc
	cwd       string
	q         chan []byte
	closed    bool
	returnCode *int
	created   time.Time
	dropped   int
	mu        sync.Mutex
	idleTimer *time.Timer
}

type terminalRegistry struct {
	mu       sync.Mutex
	byID     map[string]*terminalSession
	spawn    TerminalSpawnFunc
}

func newTerminalRegistry() *terminalRegistry {
	return &terminalRegistry{byID: make(map[string]*terminalSession)}
}

func stripANSI(text string) string {
	text = ansiRE.ReplaceAllString(text, "")
	var b strings.Builder
	for _, r := range text {
		if r == '\n' || r == '\t' || r == '\r' || r >= 32 {
			b.WriteRune(r)
		}
	}
	return b.String()
}

func pickShell() (string, []string) {
	if runtime.GOOS == "windows" {
		args := []string{"-NoLogo", "-NoProfile", "-NoExit", "-Command", winUTF8Init}
		pf := os.Getenv("ProgramFiles")
		if pf == "" {
			pf = `C:\Program Files`
		}
		pf86 := os.Getenv("ProgramFiles(X86)")
		if pf86 == "" {
			pf86 = `C:\Program Files (x86)`
		}
		sysroot := os.Getenv("SystemRoot")
		if sysroot == "" {
			sysroot = `C:\Windows`
		}
		for _, c := range []string{
			filepath.Join(pf, "PowerShell", "7", "pwsh.exe"),
			filepath.Join(pf86, "PowerShell", "7", "pwsh.exe"),
			filepath.Join(sysroot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
			`C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`,
		} {
			if st, err := os.Stat(c); err == nil && !st.IsDir() {
				return c, args
			}
		}
		// Fail closed: no absolute shell found (Go cannot soft-fallback via os/exec).
		return "", nil
	}
	shell := strings.TrimSpace(os.Getenv("SHELL"))
	if shell == "" {
		shell = "/bin/sh"
	}
	if st, err := os.Stat(shell); err == nil && !st.IsDir() {
		return shell, []string{"-i"}
	}
	if st, err := os.Stat("/bin/sh"); err == nil && !st.IsDir() {
		return "/bin/sh", nil
	}
	return "", nil
}

func defaultTerminalCWD(homeDir string) string {
	cfg := LoadConfig(homeDir)
	raw := strings.TrimSpace(cfgString(cfg, "project_path", ""))
	if !isUnsetProjectPath(raw) && !isPackagedInstallDir(raw) {
		if st, err := os.Stat(raw); err == nil && st.IsDir() {
			abs, err := filepath.Abs(raw)
			if err == nil {
				return abs
			}
			return raw
		}
	}
	if owner := defaultOwnerFilesBase(); owner != "" {
		return owner
	}
	return ""
}

func (reg *terminalRegistry) setSpawnOverride(fn TerminalSpawnFunc) {
	reg.mu.Lock()
	reg.spawn = fn
	reg.mu.Unlock()
}

func (reg *terminalRegistry) open(homeDir, cwd string, cols, rows int) (*terminalSession, string, error) {
	reg.mu.Lock()
	defer reg.mu.Unlock()
	if len(reg.byID) >= maxTerminals {
		var oldest *terminalSession
		for _, t := range reg.byID {
			if oldest == nil || t.created.Before(oldest.created) {
				oldest = t
			}
		}
		if oldest != nil {
			oldest.close()
			delete(reg.byID, oldest.id)
		}
	}
	if cwd != "" {
		if st, err := os.Stat(cwd); err != nil || !st.IsDir() {
			cwd = defaultTerminalCWD(homeDir)
		}
	} else {
		cwd = defaultTerminalCWD(homeDir)
	}
	var proc TerminalProc
	var err error
	shellName := ""
	if reg.spawn != nil {
		proc, err = reg.spawn(cwd, cols, rows)
		if err != nil {
			return nil, "", err
		}
		shell, _ := pickShell()
		shellName = filepath.Base(shell)
	} else {
		shell, args := pickShell()
		if shell == "" {
			return nil, "", fmt.Errorf("no absolute shell found")
		}
		shellName = filepath.Base(shell)
		argv := append([]string{shell}, args...)
		proc, err = spawnConptyTerminal(homeDir, argv, cwd, cols, rows)
		if err != nil {
			return nil, "", err
		}
	}
	sess := &terminalSession{
		id:      fmt.Sprintf("term-%s", randomHex(12)),
		proc:    proc,
		cwd:     cwd,
		q:       make(chan []byte, 64),
		created: time.Now(),
	}
	go sess.pump()
	reg.byID[sess.id] = sess
	return sess, shellName, nil
}

func spawnConptyTerminal(homeDir string, argv []string, cwd string, cols, rows int) (TerminalProc, error) {
	if runtime.GOOS != "windows" {
		return nil, fmt.Errorf("%w: ConPTY is Windows-only; no pipe fallback in Go", core.ErrUnavailable)
	}
	ok, err := core.ConptyAvailable()
	if err != nil {
		return nil, fmt.Errorf("ConPTY unavailable: %w", err)
	}
	if !ok {
		// Fail closed (Python soft-falls back to pipes; Go cannot use os/exec).
		return nil, fmt.Errorf("%w: ConPTY not supported on this host", core.ErrUnsupported)
	}
	key, err := secret.EnsureHostSigningKey(ResolveHomeDir(homeDir))
	if err != nil {
		return nil, err
	}
	if err := core.EnsureSigningKey(key); err != nil {
		return nil, err
	}
	_ = core.WriteJailSetRoots(nil) // Full / unbound for interactive terminal
	token, nowMS, err := core.IssueProcessSpawnToken(argv, false)
	if err != nil {
		return nil, err
	}
	env := map[string]string{}
	for _, e := range os.Environ() {
		if i := strings.IndexByte(e, '='); i > 0 {
			env[e[:i]] = e[i+1:]
		}
	}
	c, r := uint16(cols), uint16(rows)
	if c == 0 {
		c = 120
	}
	if r == 0 {
		r = 40
	}
	_, handle, err := core.ConptySpawnAuthorized(argv, cwd, env, c, r, token, "", "", false, nowMS)
	if err != nil {
		return nil, err
	}
	return &conptyProc{handle: handle}, nil
}

type conptyProc struct {
	handle     uint64
	returnCode *int
	mu         sync.Mutex
}

func (p *conptyProc) Write(data []byte) error {
	_, err := core.ConptyWrite(p.handle, data)
	return err
}

func (p *conptyProc) ReadSync(n int) ([]byte, error) {
	return core.ConptyRead(p.handle, n)
}

func (p *conptyProc) Poll() *int {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.returnCode != nil {
		return p.returnCode
	}
	code, err := core.ConptyPoll(p.handle)
	if err != nil || code == nil {
		return nil
	}
	c := int(*code)
	p.returnCode = &c
	return p.returnCode
}

func (p *conptyProc) Kill() {
	_ = core.ConptyKill(p.handle)
	_ = core.ConptyClosePipe(p.handle, core.ConptyPipeStdin)
	_ = core.ConptyClosePipe(p.handle, core.ConptyPipeStdout)
	_ = core.ConptyClose(p.handle)
	p.mu.Lock()
	if p.returnCode == nil {
		c := 1
		p.returnCode = &c
	}
	p.handle = 0
	p.mu.Unlock()
}

func (p *conptyProc) Resize(_, _ int) error {
	// ABI 5 has no ConPTY resize export yet.
	return nil
}

func (s *terminalSession) pump() {
	for {
		s.mu.Lock()
		closed := s.closed
		s.mu.Unlock()
		if closed {
			break
		}
		data, err := s.proc.ReadSync(readChunk)
		if err != nil || len(data) == 0 {
			break
		}
		s.push(data)
	}
	if code := s.proc.Poll(); code != nil {
		s.mu.Lock()
		s.returnCode = code
		s.mu.Unlock()
	}
	select {
	case s.q <- nil:
	default:
	}
}

func (s *terminalSession) push(data []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed {
		return
	}
	if len(s.q)*readChunk >= maxQueueBytes {
		s.dropped += len(data)
		return
	}
	select {
	case s.q <- append([]byte(nil), data...):
	default:
		s.dropped += len(data)
	}
}

func (s *terminalSession) write(data string) error {
	s.mu.Lock()
	closed := s.closed
	s.mu.Unlock()
	if closed {
		return errTerminalClosed
	}
	if len(data) > maxInputBytes {
		return errTerminalInputTooLarge
	}
	raw := []byte(data)
	if !utf8.Valid(raw) {
		raw = []byte(strings.ToValidUTF8(data, "�"))
	}
	if len(raw) == 0 {
		return nil
	}
	return s.proc.Write(raw)
}

func (s *terminalSession) resize(cols, rows int) error {
	return s.proc.Resize(cols, rows)
}

func (s *terminalSession) close() {
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return
	}
	s.closed = true
	if s.idleTimer != nil {
		s.idleTimer.Stop()
		s.idleTimer = nil
	}
	s.mu.Unlock()
	s.proc.Kill()
	select {
	case s.q <- nil:
	default:
	}
}

func (s *terminalSession) scheduleIdleClose(reg *terminalRegistry) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.idleTimer != nil {
		s.idleTimer.Stop()
	}
	s.idleTimer = time.AfterFunc(time.Duration(idleGraceS*float64(time.Second)), func() {
		s.close()
		reg.mu.Lock()
		delete(reg.byID, s.id)
		reg.mu.Unlock()
	})
}

func (s *terminalSession) cancelIdleClose() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.idleTimer != nil {
		s.idleTimer.Stop()
		s.idleTimer = nil
	}
}

var (
	errTerminalClosed        = errString("Terminal closed")
	errTerminalInputTooLarge = errString("Input too large (max 65536 bytes)")
)

func randomHex(nBytes int) string {
	b := make([]byte, nBytes)
	if _, err := rand.Read(b); err != nil {
		return fmt.Sprintf("%d", time.Now().UnixNano())
	}
	return hex.EncodeToString(b)[:nBytes]
}

func (s *Server) terminals() *terminalRegistry {
	s.termOnce.Do(func() {
		s.termReg = newTerminalRegistry()
	})
	return s.termReg
}

func (s *Server) handleTerminalOpen(w http.ResponseWriter, r *http.Request) {
	var req struct {
		CWD  *string `json:"cwd"`
		Cols int     `json:"cols"`
		Rows int     `json:"rows"`
	}
	req.Cols, req.Rows = 100, 28
	if r.Body != nil {
		defer r.Body.Close()
		if err := json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&req); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
			return
		}
	}
	if req.Cols < 20 {
		req.Cols = 20
	}
	if req.Cols > 400 {
		req.Cols = 400
	}
	if req.Rows < 5 {
		req.Rows = 5
	}
	if req.Rows > 120 {
		req.Rows = 120
	}
	cwd := ""
	if req.CWD != nil {
		cwd = strings.TrimSpace(*req.CWD)
	}
	sess, shell, err := s.terminals().open(s.homeDir, cwd, req.Cols, req.Rows)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{
			"detail": "Could not start terminal: " + err.Error(),
		})
		return
	}
	var cwdOut any
	if sess.cwd != "" {
		cwdOut = sess.cwd
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"terminal_id": sess.id,
		"cwd":         cwdOut,
		"shell":       shell,
	})
}

func (s *Server) handleTerminalStream(w http.ResponseWriter, r *http.Request) {
	tid := r.PathValue("terminal_id")
	reg := s.terminals()
	reg.mu.Lock()
	sess := reg.byID[tid]
	reg.mu.Unlock()
	if sess == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Terminal not found"})
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": "streaming unsupported"})
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	_, _ = fmt.Fprintf(w, ": connected\n\n")
	flusher.Flush()

	sess.cancelIdleClose()
	lastTouch := time.Now()
	dropped := sess.dropped
	defer func() {
		sess.mu.Lock()
		closed := sess.closed
		sess.mu.Unlock()
		if !closed {
			sess.scheduleIdleClose(reg)
		} else {
			reg.mu.Lock()
			delete(reg.byID, sess.id)
			reg.mu.Unlock()
		}
	}()

	for {
		select {
		case <-r.Context().Done():
			return
		case chunk, ok := <-sess.q:
			if !ok || chunk == nil {
				code := sess.returnCode
				if code == nil {
					code = sess.proc.Poll()
				}
				payload, _ := json.Marshal(map[string]any{"type": "exit", "code": code})
				_, _ = fmt.Fprintf(w, "event: exit\ndata: %s\n\n", payload)
				flusher.Flush()
				return
			}
			lastTouch = time.Now()
			parts := [][]byte{chunk}
			total := len(chunk)
			for total < maxSSEBatch {
				select {
				case nxt := <-sess.q:
					if nxt == nil {
						select {
						case sess.q <- nil:
						default:
						}
						goto flush
					}
					parts = append(parts, nxt)
					total += len(nxt)
				default:
					goto flush
				}
			}
		flush:
			if sess.dropped != dropped {
				notice := fmt.Sprintf("\n[output truncated: %d bytes dropped]\n", sess.dropped-dropped)
				parts = append([][]byte{[]byte(notice)}, parts...)
				dropped = sess.dropped
			}
			text := stripANSI(string(bytesJoin(parts)))
			if text == "" {
				continue
			}
			payload, _ := json.Marshal(map[string]any{"type": "output", "text": text})
			_, _ = fmt.Fprintf(w, "event: output\ndata: %s\n\n", payload)
			flusher.Flush()
		case <-time.After(50 * time.Millisecond):
			if time.Since(lastTouch).Seconds() >= idleKeepaliveS {
				_, _ = fmt.Fprintf(w, ": keepalive\n\n")
				flusher.Flush()
				lastTouch = time.Now()
			}
		}
	}
}

func bytesJoin(parts [][]byte) []byte {
	n := 0
	for _, p := range parts {
		n += len(p)
	}
	out := make([]byte, 0, n)
	for _, p := range parts {
		out = append(out, p...)
	}
	return out
}

func (s *Server) handleTerminalInput(w http.ResponseWriter, r *http.Request) {
	tid := r.PathValue("terminal_id")
	reg := s.terminals()
	reg.mu.Lock()
	sess := reg.byID[tid]
	reg.mu.Unlock()
	if sess == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Terminal not found"})
		return
	}
	var req struct {
		Data string `json:"data"`
	}
	if r.Body != nil {
		defer r.Body.Close()
		if err := json.NewDecoder(io.LimitReader(r.Body, maxInputBytes+1024)).Decode(&req); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
			return
		}
	}
	if err := sess.write(req.Data); err != nil {
		if err == errTerminalClosed {
			writeJSON(w, http.StatusGone, map[string]string{"detail": err.Error()})
			return
		}
		if err == errTerminalInputTooLarge {
			writeJSON(w, http.StatusRequestEntityTooLarge, map[string]string{"detail": err.Error()})
			return
		}
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) handleTerminalResize(w http.ResponseWriter, r *http.Request) {
	tid := r.PathValue("terminal_id")
	reg := s.terminals()
	reg.mu.Lock()
	sess := reg.byID[tid]
	reg.mu.Unlock()
	if sess == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Terminal not found"})
		return
	}
	var req struct {
		Cols int `json:"cols"`
		Rows int `json:"rows"`
	}
	req.Cols, req.Rows = 100, 28
	if r.Body != nil {
		defer r.Body.Close()
		_ = json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&req)
	}
	_ = sess.resize(req.Cols, req.Rows)
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) handleTerminalClose(w http.ResponseWriter, r *http.Request) {
	tid := r.PathValue("terminal_id")
	reg := s.terminals()
	reg.mu.Lock()
	sess := reg.byID[tid]
	if sess != nil {
		delete(reg.byID, tid)
	}
	reg.mu.Unlock()
	if sess == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Terminal not found"})
		return
	}
	sess.close()
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}
