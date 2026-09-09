package workers

import (
	"context"
	"errors"
	"fmt"
	"log"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/ipc"
	"github.com/AhmiDarrow/RemedyAI/native/go/protocol"
)

// Supervision policy for the RMDY tool worker.
const (
	restartBackoffMin = time.Second
	restartBackoffMax = 30 * time.Second
	restartWindow     = 10 * time.Minute
	restartBudget     = 5
)

var (
	// ErrWorkerGaveUp is returned by LiveCaller once the supervisor exhausted
	// its restart budget. Operators must look at the worker log.
	ErrWorkerGaveUp = errors.New("RMDY tool worker gave up after repeated crashes")
	// ErrToolsPythonDownloading is returned while the managed CPython is still
	// being fetched and no worker exists yet.
	ErrToolsPythonDownloading = errors.New("RMDY tool worker unavailable: managed Python is downloading")
)

// LiveCaller is the FrameCaller handed to tools.RMDYExecutor and the voice /
// vision workers. It forwards to whichever ipc.Client is current, so a
// respawned worker is picked up by every registered Python tool without
// re-registration. A nil client yields a clear, typed error.
type LiveCaller struct {
	client atomic.Pointer[ipc.Client]
	// unavailable explains a nil client (downloading / gave up / restarting).
	unavailable atomic.Pointer[error]
}

// Call forwards to the current client.
func (c *LiveCaller) Call(ctx context.Context, frame protocol.Frame) (protocol.Frame, error) {
	if c == nil {
		return protocol.Frame{}, ipc.ErrDisconnected
	}
	client := c.client.Load()
	if client == nil {
		if reason := c.unavailable.Load(); reason != nil && *reason != nil {
			return protocol.Frame{}, *reason
		}
		return protocol.Frame{}, ipc.ErrDisconnected
	}
	return client.Call(ctx, frame)
}

// Client returns the current ipc.Client (nil while restarting).
func (c *LiveCaller) Client() *ipc.Client {
	if c == nil {
		return nil
	}
	return c.client.Load()
}

func (c *LiveCaller) set(client *ipc.Client, reason error) {
	c.unavailable.Store(&reason)
	c.client.Store(client)
}

// Supervisor keeps one RMDY tool worker alive: it waits on the process
// handle and the IPC client, fails in-flight calls when the worker dies
// (ipc.ErrDisconnected), respawns with exponential backoff (1 s → 30 s), and
// gives up after restartBudget restarts inside restartWindow.
type Supervisor struct {
	opts   RMDYToolOptions
	caller *LiveCaller
	logf   func(string, ...any)

	ctx    context.Context
	cancel context.CancelFunc

	mu       sync.Mutex
	session  *RMDYToolSession
	restarts []time.Time
	gaveUp   bool
	closed   bool
	done     chan struct{}
	started  chan struct{}
	startErr error
}

// Caller is the stable FrameCaller for this supervisor.
func (s *Supervisor) Caller() *LiveCaller { return s.caller }

// Session returns the current session (nil while restarting or after Close).
func (s *Supervisor) Session() *RMDYToolSession {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.session
}

// GaveUp reports whether the restart budget was exhausted.
func (s *Supervisor) GaveUp() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.gaveUp
}

// Done is closed when the supervision loop has exited (Close or gave up).
func (s *Supervisor) Done() <-chan struct{} { return s.done }

// Ready blocks until the first attach attempt finished and returns its error.
func (s *Supervisor) Ready(ctx context.Context) error {
	select {
	case <-s.started:
		return s.startErr
	case <-ctx.Done():
		return ctx.Err()
	}
}

// StartSupervisedRMDYToolWorker attaches the first worker synchronously
// (fail closed, like StartRMDYToolWorker) and then supervises it.
func StartSupervisedRMDYToolWorker(ctx context.Context, opts RMDYToolOptions) (*Supervisor, error) {
	s := newSupervisor(ctx, opts)
	session, err := StartRMDYToolWorker(s.ctx, opts)
	if err != nil {
		s.cancel()
		close(s.done)
		return nil, err
	}
	s.adopt(session)
	s.startErr = nil
	close(s.started)
	go s.run()
	return s, nil
}

// StartSupervisedRMDYToolWorkerAsync returns immediately with a LiveCaller
// that reports ErrToolsPythonDownloading until the first attach succeeds.
// Used when the only interpreter path is the managed CPython download, so the
// HTTP API binds first and /api/status can report tools_python=downloading.
// onFirstAttach (optional) receives the first attach result.
func StartSupervisedRMDYToolWorkerAsync(ctx context.Context, opts RMDYToolOptions, onFirstAttach func(error)) *Supervisor {
	s := newSupervisor(ctx, opts)
	s.caller.set(nil, ErrToolsPythonDownloading)
	setToolsPythonState(ToolsPythonDownloading, "")
	go func() {
		session, err := StartRMDYToolWorker(s.ctx, opts)
		if err != nil {
			s.logf("remedy-runtime: RMDY tool worker attach failed: %v", err)
			setToolsPythonState(ToolsPythonFailed, err.Error())
			s.caller.set(nil, fmt.Errorf("%w: %v", ErrWorkerAttachRequired, err))
			s.startErr = err
			close(s.started)
			s.cancel()
			close(s.done)
			if onFirstAttach != nil {
				onFirstAttach(err)
			}
			return
		}
		s.adopt(session)
		s.startErr = nil
		close(s.started)
		if onFirstAttach != nil {
			onFirstAttach(nil)
		}
		s.run()
	}()
	return s
}

func newSupervisor(ctx context.Context, opts RMDYToolOptions) *Supervisor {
	if ctx == nil {
		ctx = context.Background()
	}
	sctx, cancel := context.WithCancel(ctx)
	return &Supervisor{
		opts:    opts,
		caller:  &LiveCaller{},
		logf:    log.Printf,
		ctx:     sctx,
		cancel:  cancel,
		done:    make(chan struct{}),
		started: make(chan struct{}),
	}
}

func (s *Supervisor) adopt(session *RMDYToolSession) {
	s.mu.Lock()
	s.session = session
	s.mu.Unlock()
	s.caller.set(session.Client, nil)
	setToolsPythonState(ToolsPythonReady, "")
	s.logf("remedy-runtime: RMDY tool worker attached (pid=%d endpoint=%s)", session.PID, session.Endpoint)
}

// Close stops supervision and tears the current worker down.
func (s *Supervisor) Close() error {
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return nil
	}
	s.closed = true
	session := s.session
	s.session = nil
	s.mu.Unlock()
	s.cancel()
	s.caller.set(nil, ipc.ErrDisconnected)
	var err error
	if session != nil {
		err = session.Close()
	}
	<-s.done
	return err
}

// run is the supervision loop. It owns s.done.
func (s *Supervisor) run() {
	defer close(s.done)
	backoff := restartBackoffMin
	for {
		s.mu.Lock()
		session := s.session
		s.mu.Unlock()
		if session == nil {
			return
		}
		code, reason := s.waitExit(session)
		if s.ctx.Err() != nil {
			return
		}
		s.logf("remedy-runtime: RMDY tool worker exited (pid=%d exit=%s reason=%s); in-flight calls fail with %v",
			session.PID, code, reason, ipc.ErrDisconnected)
		if code == strconv.Itoa(ExitMissingDependencies) {
			s.logf("remedy-runtime: %v (exit=%s; see <REMEDY_HOME>/logs/rmdy_worker.log)", ErrWorkerMissingDeps, code)
			setToolsPythonState(ToolsPythonFailed, ErrWorkerMissingDeps.Error())
		}
		s.caller.set(nil, ipc.ErrDisconnected)
		s.mu.Lock()
		if s.session == session {
			s.session = nil
		}
		s.mu.Unlock()
		_ = session.Close()

		for {
			if !s.allowRestart() {
				s.logf("remedy-runtime: RMDY tool worker crashed %d times within %s; giving up. Inspect <REMEDY_HOME>/logs/rmdy_worker.log", restartBudget, restartWindow)
				s.mu.Lock()
				s.gaveUp = true
				s.mu.Unlock()
				s.caller.set(nil, ErrWorkerGaveUp)
				setToolsPythonState(ToolsPythonFailed, ErrWorkerGaveUp.Error())
				return
			}
			s.logf("remedy-runtime: respawning RMDY tool worker in %s", backoff)
			select {
			case <-time.After(backoff):
			case <-s.ctx.Done():
				return
			}
			if backoff < restartBackoffMax {
				backoff *= 2
				if backoff > restartBackoffMax {
					backoff = restartBackoffMax
				}
			}
			next, err := StartRMDYToolWorker(s.ctx, s.opts)
			if err != nil {
				if s.ctx.Err() != nil {
					return
				}
				s.logf("remedy-runtime: RMDY tool worker respawn failed: %v", err)
				continue
			}
			s.mu.Lock()
			closed := s.closed
			if !closed {
				s.session = next
			}
			s.mu.Unlock()
			if closed {
				_ = next.Close()
				return
			}
			s.caller.set(next.Client, nil)
			setToolsPythonState(ToolsPythonReady, "")
			s.logf("remedy-runtime: RMDY tool worker respawned (pid=%d endpoint=%s)", next.PID, next.Endpoint)
			break
		}
	}
}

// waitExit blocks until the worker process exits or its IPC client dies.
func (s *Supervisor) waitExit(session *RMDYToolSession) (code string, reason string) {
	procExit := make(chan string, 1)
	if session.proc != nil && session.proc.Wait != nil {
		go func() {
			exit, err := session.proc.Wait()
			if err != nil {
				procExit <- "unknown (" + err.Error() + ")"
				return
			}
			procExit <- fmt.Sprintf("%d", exit)
		}()
	}
	var clientDone <-chan struct{}
	if session.Client != nil {
		clientDone = session.Client.Done()
	}
	select {
	case <-s.ctx.Done():
		return "", "shutdown"
	case c := <-procExit:
		return c, "process-exit"
	case <-clientDone:
		// Give the process a moment to report its exit code for the log.
		select {
		case c := <-procExit:
			return c, "process-exit"
		case <-time.After(500 * time.Millisecond):
			return "unknown", "ipc-closed"
		case <-s.ctx.Done():
			return "", "shutdown"
		}
	}
}

// allowRestart records a restart attempt and enforces the budget window.
func (s *Supervisor) allowRestart() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := time.Now()
	kept := s.restarts[:0]
	for _, t := range s.restarts {
		if now.Sub(t) < restartWindow {
			kept = append(kept, t)
		}
	}
	s.restarts = kept
	if len(s.restarts) >= restartBudget {
		return false
	}
	s.restarts = append(s.restarts, now)
	return true
}

// ToolsPythonState is the managed-interpreter readiness reported on /api/status.
type ToolsPythonState string

const (
	ToolsPythonDownloading ToolsPythonState = "downloading"
	ToolsPythonReady       ToolsPythonState = "ready"
	ToolsPythonFailed      ToolsPythonState = "failed"
)

var toolsPython struct {
	mu     sync.Mutex
	state  ToolsPythonState
	detail string
}

func setToolsPythonState(state ToolsPythonState, detail string) {
	toolsPython.mu.Lock()
	defer toolsPython.mu.Unlock()
	toolsPython.state = state
	toolsPython.detail = strings.TrimSpace(detail)
}

// ToolsPythonStatus reports the tool-worker interpreter state for
// /api/status ("tools_python": "downloading"|"ready"|"failed") plus a short
// detail string (empty when ready). Empty state means no supervisor started.
func ToolsPythonStatus() (state ToolsPythonState, detail string) {
	toolsPython.mu.Lock()
	defer toolsPython.mu.Unlock()
	return toolsPython.state, toolsPython.detail
}
