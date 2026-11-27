package tools

import (
	"context"
	"errors"
	"io"
	"strings"
	"sync"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
)

// ResolveArgv0 makes argv[0] an absolute executable the way shell.exec does
// (bare names on PATH, relative paths against cwd).
func ResolveArgv0(argv0, cwd string) (string, error) { return resolveShellArgv0(argv0, cwd) }

// ErrSpawnRefused marks a spawn the Zig policy or the secrets guard refused.
var ErrSpawnRefused = errors.New("refused by security policy")

// RunAttached runs argv through the same authorized spawn shell.exec uses (Zig
// deny classifier, capability token, kill-tree) and streams the child's
// stdout and stderr to the given writers as they arrive. stdin, when non-nil,
// is copied to the child. A timeout of 0 means none. It returns the exit code;
// a timeout kills the tree and returns 1 with timedOut set.
func RunAttached(ctx context.Context, argv []string, cwd string, env map[string]string, stdin io.Reader, stdout, stderr io.Writer, timeout time.Duration) (code int, timedOut bool, err error) {
	if len(argv) == 0 {
		return 0, false, ErrInvalidInput
	}
	argv0, err := resolveShellArgv0(argv[0], cwd)
	if err != nil {
		return 127, false, err
	}
	full := append([]string{argv0}, argv[1:]...)
	proc, err := spawnAuthorized(ctx, shellSpawnSpec{argv: full, cwd: cwd, env: env})
	if err != nil {
		if isPolicyRefusal(err) {
			return 2, false, errors.Join(ErrSpawnRefused, err)
		}
		return 1, false, err
	}
	in, out, errf := proc.Files()
	var wg sync.WaitGroup
	wg.Add(2)
	go func() { defer wg.Done(); _, _ = io.Copy(stdout, out) }()
	go func() { defer wg.Done(); _, _ = io.Copy(stderr, errf) }()
	if stdin != nil {
		go func() { _, _ = io.Copy(in, stdin); _ = in.Close() }()
	} else {
		_ = in.Close()
	}

	type exitStatus struct {
		code uint32
		err  error
	}
	exited := make(chan exitStatus, 1)
	go func() {
		for {
			c, werr := core.ProcessWait(proc.Handle, 100)
			if werr != nil {
				exited <- exitStatus{err: werr}
				return
			}
			if c != nil {
				exited <- exitStatus{code: *c}
				return
			}
		}
	}()
	finish := func() {
		done := make(chan struct{})
		go func() { wg.Wait(); close(done) }()
		select {
		case <-done:
		case <-time.After(2 * time.Second):
		}
		_ = out.Close()
		_ = errf.Close()
		_ = core.ProcessClose(proc.Handle)
	}
	kill := func() {
		_ = core.ProcessKillTree(proc.PID)
		select {
		case <-exited:
		case <-time.After(3 * time.Second):
		}
	}
	var timer <-chan time.Time
	if timeout > 0 {
		t := time.NewTimer(timeout)
		defer t.Stop()
		timer = t.C
	}
	select {
	case st := <-exited:
		finish()
		if st.err != nil {
			return 1, false, st.err
		}
		return int(st.code), false, nil
	case <-timer:
		kill()
		finish()
		return 1, true, nil
	case <-ctx.Done():
		kill()
		finish()
		return 130, false, ctx.Err()
	}
}

func isPolicyRefusal(err error) bool {
	if err == nil {
		return false
	}
	if errors.Is(err, ErrInvalidInput) {
		return true
	}
	msg := strings.ToLower(err.Error())
	for _, needle := range []string{"policy", "denied", "dangerous", "refus", "not authorized", "forbidden"} {
		if strings.Contains(msg, needle) {
			return true
		}
	}
	return false
}
