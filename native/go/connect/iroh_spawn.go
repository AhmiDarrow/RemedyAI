package connect

import (
	"context"
	"io"
	"path/filepath"
	"sync"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// IrohProcess is a started remedy-iroh sidecar: its pipes and lifecycle.
type IrohProcess struct {
	Stdin  io.WriteCloser
	Stdout io.ReadCloser
	// Wait blocks until the process exits (or Kill ran) and releases it.
	Wait func() error
	// Kill stops the process tree. Safe to call more than once.
	Kill func()
}

// IrohStarter starts the sidecar at bin. Production spawns through Zig
// remedy_core (authorized, hidden, no os/exec in Go); tests may inject another.
type IrohStarter func(ctx context.Context, home, bin string) (*IrohProcess, error)

var (
	irohStarterMu sync.Mutex
	irohStarter   IrohStarter = zigIrohStarter
)

// SetIrohStarterForTest swaps the sidecar starter and returns a restore func.
func SetIrohStarterForTest(s IrohStarter) func() {
	irohStarterMu.Lock()
	prev := irohStarter
	irohStarter = s
	irohStarterMu.Unlock()
	return func() {
		irohStarterMu.Lock()
		irohStarter = prev
		irohStarterMu.Unlock()
	}
}

func currentIrohStarter() IrohStarter {
	irohStarterMu.Lock()
	defer irohStarterMu.Unlock()
	return irohStarter
}

// zigIrohStarter spawns the sidecar with the same capability-token contract
// as the shell and Signal runners. argv carries no secrets: the iroh key and
// hop token go over stdin.
func zigIrohStarter(_ context.Context, home, bin string) (*IrohProcess, error) {
	key, err := secret.EnsureHostSigningKey(home)
	if err != nil {
		return nil, err
	}
	if err := core.EnsureSigningKey(key); err != nil {
		return nil, err
	}
	argv := []string{filepath.Clean(bin)}
	token, nowMS, err := core.IssueProcessSpawnToken(argv, nil, false, false)
	if err != nil {
		return nil, err
	}
	var proc core.PipedProcess
	if err := core.WithWriteJail(nil, func() error {
		var err error
		proc, err = core.ProcessSpawnPipedAuthorized(argv, "", nil, false, token, "", "", false, nowMS)
		return err
	}); err != nil {
		return nil, err
	}
	stdin, stdout, stderr := proc.Files()
	go func() { _, _ = io.Copy(io.Discard, stderr); _ = stderr.Close() }()

	var killOnce, closeOnce sync.Once
	killed := make(chan struct{})
	kill := func() {
		killOnce.Do(func() {
			_ = core.ProcessKillTree(proc.PID)
			close(killed)
		})
	}
	wait := func() error {
		defer closeOnce.Do(func() {
			_ = stdin.Close()
			_ = stdout.Close()
			_ = core.ProcessClose(proc.Handle)
		})
		grace := 0
		for {
			code, err := core.ProcessWait(proc.Handle, 500)
			if err != nil || code != nil {
				return err
			}
			select {
			case <-killed:
				// Bounded: a tree that ignores the kill must not wedge Stop.
				if grace++; grace >= 10 {
					return nil
				}
			default:
			}
		}
	}
	return &IrohProcess{Stdin: stdin, Stdout: stdout, Wait: wait, Kill: kill}, nil
}
