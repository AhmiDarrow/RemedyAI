package workers

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/ipc"
	"github.com/AhmiDarrow/RemedyAI/native/go/protocol"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// Compile-time: LiveCaller is the FrameCaller handed to Python tools.
var _ tools.FrameCaller = (*LiveCaller)(nil)

// waitingProcessStarter is execProcessStarter plus Wait, and records the
// running *exec.Cmd so a test can kill the worker mid-call.
func waitingProcessStarter(t *testing.T, env map[string]string, onStart func(*exec.Cmd)) ProcessStarter {
	t.Helper()
	return func(ctx context.Context, argv []string, extra map[string]string, cwd string) (*StartedProcess, error) {
		merged := map[string]string{}
		for k, v := range extra {
			merged[k] = v
		}
		for k, v := range env {
			merged[k] = v
		}
		cmd := exec.Command(argv[0], argv[1:]...)
		if cwd != "" {
			cmd.Dir = cwd
		}
		cmd.Env = make([]string, 0, len(merged))
		for k, v := range merged {
			cmd.Env = append(cmd.Env, k+"="+v)
		}
		cmd.Stderr = os.Stderr
		if err := cmd.Start(); err != nil {
			return nil, err
		}
		if onStart != nil {
			onStart(cmd)
		}
		var once sync.Once
		waitErr := make(chan error, 1)
		var state *os.ProcessState
		wait := func() {
			once.Do(func() {
				err := cmd.Wait()
				state = cmd.ProcessState
				waitErr <- err
				close(waitErr)
			})
		}
		return &StartedProcess{
			PID: uint32(cmd.Process.Pid),
			Cleanup: func() error {
				_ = cmd.Process.Kill()
				wait()
				return nil
			},
			Wait: func() (uint32, error) {
				wait()
				<-waitErr
				if state == nil {
					return 0, errors.New("no process state")
				}
				return uint32(state.ExitCode()), nil
			},
		}, nil
	}
}

func testSupervisorOptions(t *testing.T, starter ProcessStarter) RMDYToolOptions {
	t.Helper()
	root := repoRoot(t)
	return RMDYToolOptions{
		HomeDir:         t.TempDir(),
		Cwd:             root,
		PythonArgv:      testPythonArgv(t),
		AttachTimeout:   60 * time.Second,
		StartProcess:    starter,
		AttachProbeTool: "text.slugify",
	}
}

func testWorkerEnv(t *testing.T) map[string]string {
	t.Helper()
	root := repoRoot(t)
	return map[string]string{
		"PYTHONPATH":             filepath.Join(root, "src"),
		"REMEDY_WORKSPACE":       root,
		"REMEDY_RMDY_TEST_TOOLS": "1",
	}
}

func callTool(ctx context.Context, caller tools.FrameCaller, toolID string, input map[string]any) (map[string]any, error) {
	raw, err := json.Marshal(input)
	if err != nil {
		return nil, err
	}
	exec := tools.NewRMDYExecutor(caller)
	res, err := exec.Execute(ctx, tools.Request{ToolID: toolID, Version: 1, Input: raw})
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal(res.Output, &out); err != nil {
		return nil, err
	}
	return out, nil
}

func TestSupervisorRespawnsAfterWorkerKilledMidCall(t *testing.T) {
	var mu sync.Mutex
	var cmds []*exec.Cmd
	starter := waitingProcessStarter(t, testWorkerEnv(t), func(c *exec.Cmd) {
		mu.Lock()
		cmds = append(cmds, c)
		mu.Unlock()
	})
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	sup, err := StartSupervisedRMDYToolWorker(ctx, testSupervisorOptions(t, starter))
	if err != nil {
		t.Fatal(err)
	}
	defer sup.Close()
	caller := sup.Caller()

	// Sanity: the live caller works through the registered executor.
	if out, err := callTool(ctx, caller, "text.slugify", map[string]any{"text": "Before Kill"}); err != nil || out["slug"] != "before-kill" {
		t.Fatalf("pre-kill call out=%v err=%v", out, err)
	}
	firstPID := sup.Session().PID

	// Start a slow call, then kill the worker while it is in flight.
	type result struct {
		out map[string]any
		err error
	}
	slow := make(chan result, 1)
	go func() {
		out, err := callTool(ctx, caller, "debug.sleep", map[string]any{"seconds": 20})
		slow <- result{out, err}
	}()
	time.Sleep(700 * time.Millisecond)
	mu.Lock()
	victim := cmds[0]
	mu.Unlock()
	if err := victim.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	select {
	case r := <-slow:
		if r.err == nil {
			t.Fatalf("in-flight call must fail after worker death, got %v", r.out)
		}
		if !errors.Is(r.err, ipc.ErrDisconnected) {
			t.Fatalf("in-flight call error = %v, want ErrDisconnected", r.err)
		}
	case <-time.After(30 * time.Second):
		t.Fatal("in-flight call did not fail after worker death")
	}

	// The next call succeeds once the supervisor has respawned (backoff 1 s).
	deadline := time.Now().Add(90 * time.Second)
	var last error
	for time.Now().Before(deadline) {
		callCtx, callCancel := context.WithTimeout(ctx, 10*time.Second)
		out, err := callTool(callCtx, caller, "text.slugify", map[string]any{"text": "After Respawn"})
		callCancel()
		if err == nil {
			if out["slug"] != "after-respawn" {
				t.Fatalf("post-respawn slug = %v", out["slug"])
			}
			last = nil
			break
		}
		last = err
		time.Sleep(250 * time.Millisecond)
	}
	if last != nil {
		t.Fatalf("call after respawn still failing: %v", last)
	}
	if sess := sup.Session(); sess == nil || sess.PID == firstPID {
		t.Fatalf("expected a new worker process after respawn (first pid %d, session %+v)", firstPID, sess)
	}
	if sup.GaveUp() {
		t.Fatal("supervisor must not give up after one crash")
	}
}

func TestSupervisorOverlappingCallsFastReturnsFirst(t *testing.T) {
	starter := waitingProcessStarter(t, testWorkerEnv(t), nil)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	sup, err := StartSupervisedRMDYToolWorker(ctx, testSupervisorOptions(t, starter))
	if err != nil {
		t.Fatal(err)
	}
	defer sup.Close()
	caller := sup.Caller()

	order := make(chan string, 2)
	var wg sync.WaitGroup
	wg.Add(2)
	go func() {
		defer wg.Done()
		if _, err := callTool(ctx, caller, "debug.sleep", map[string]any{"seconds": 2}); err != nil {
			t.Errorf("slow call: %v", err)
		}
		order <- "slow"
	}()
	time.Sleep(200 * time.Millisecond)
	go func() {
		defer wg.Done()
		if _, err := callTool(ctx, caller, "text.word_count", map[string]any{"text": "a b c"}); err != nil {
			t.Errorf("fast call: %v", err)
		}
		order <- "fast"
	}()
	wg.Wait()
	if first := <-order; first != "fast" {
		t.Fatalf("first completed call = %q, want fast (worker must not serialize the pool)", first)
	}
}

func TestSupervisorCloseStopsLoopAndCaller(t *testing.T) {
	starter := waitingProcessStarter(t, testWorkerEnv(t), nil)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	sup, err := StartSupervisedRMDYToolWorker(ctx, testSupervisorOptions(t, starter))
	if err != nil {
		t.Fatal(err)
	}
	if err := sup.Close(); err != nil {
		t.Logf("close: %v", err)
	}
	select {
	case <-sup.Done():
	case <-time.After(10 * time.Second):
		t.Fatal("supervisor loop did not exit after Close")
	}
	var corr [16]byte
	corr[0] = 9
	if _, err := sup.Caller().Call(ctx, protocol.Frame{Kind: protocol.KindHealth, CorrelationID: corr}); !errors.Is(err, ipc.ErrDisconnected) {
		t.Fatalf("call after Close = %v, want ErrDisconnected", err)
	}
}

func TestLiveCallerReportsDownloadingWhileNil(t *testing.T) {
	var c LiveCaller
	c.set(nil, ErrToolsPythonDownloading)
	_, err := c.Call(context.Background(), protocol.Frame{Kind: protocol.KindHealth})
	if !errors.Is(err, ErrToolsPythonDownloading) {
		t.Fatalf("err = %v", err)
	}
	c.set(nil, nil)
	_, err = c.Call(context.Background(), protocol.Frame{Kind: protocol.KindHealth})
	if !errors.Is(err, ipc.ErrDisconnected) {
		t.Fatalf("err = %v", err)
	}
}

func TestSupervisorRestartBudget(t *testing.T) {
	s := &Supervisor{}
	for i := 0; i < restartBudget; i++ {
		if !s.allowRestart() {
			t.Fatalf("restart %d refused inside budget", i)
		}
	}
	if s.allowRestart() {
		t.Fatal("restart beyond budget must be refused")
	}
	// Old attempts fall out of the window.
	for i := range s.restarts {
		s.restarts[i] = time.Now().Add(-restartWindow - time.Second)
	}
	if !s.allowRestart() {
		t.Fatal("restart must be allowed once the window has passed")
	}
}
