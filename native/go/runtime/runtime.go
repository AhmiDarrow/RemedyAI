// Package runtime provides the supervised lifecycle for the persistent Remedy runtime.
package runtime

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"sync/atomic"
	"time"
)

var (
	ErrAlreadyStarted = errors.New("remedy runtime is already started")
	ErrNotRunning     = errors.New("remedy runtime is not running")
	ErrWorkerExists   = errors.New("worker name is already active")
)

type State uint32

const (
	StateNew State = iota
	StateRunning
	StateStopping
	StateStopped
)

type Failure struct {
	Worker  string
	Attempt int
	Err     error
	Panic   bool
	Fatal   bool
	At      time.Time
}

func (f Failure) Error() string {
	kind := "failed"
	if f.Panic {
		kind = "panicked"
	}
	return fmt.Sprintf("worker %q %s on attempt %d: %v", f.Worker, kind, f.Attempt, f.Err)
}

type RestartPolicy struct {
	MaxRestarts int
	Backoff     time.Duration
}

type WorkerSpec struct {
	Name    string
	Run     func(context.Context) error
	Restart RestartPolicy
	Fatal   bool
}

type Runtime struct {
	state    atomic.Uint32
	mu       sync.Mutex
	ctx      context.Context
	cancel   context.CancelCauseFunc
	wg       sync.WaitGroup
	workers  map[string]struct{}
	failures chan Failure
}

func New() *Runtime {
	r := &Runtime{workers: make(map[string]struct{}), failures: make(chan Failure, 64)}
	r.state.Store(uint32(StateNew))
	return r
}

func (r *Runtime) State() State { return State(r.state.Load()) }

func (r *Runtime) Start(parent context.Context) error {
	if parent == nil {
		parent = context.Background()
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.State() != StateNew {
		return ErrAlreadyStarted
	}
	r.ctx, r.cancel = context.WithCancelCause(parent)
	r.state.Store(uint32(StateRunning))
	return nil
}

func (r *Runtime) Context() (context.Context, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.State() != StateRunning || r.ctx == nil {
		return nil, ErrNotRunning
	}
	return r.ctx, nil
}

func (r *Runtime) Go(name string, fn func(context.Context) error) error {
	return r.Supervise(WorkerSpec{Name: name, Run: fn})
}

func (r *Runtime) Supervise(spec WorkerSpec) error {
	if spec.Name == "" || spec.Run == nil || spec.Restart.MaxRestarts < 0 || spec.Restart.Backoff < 0 {
		return errors.New("invalid worker specification")
	}
	r.mu.Lock()
	if r.State() != StateRunning || r.ctx == nil {
		r.mu.Unlock()
		return ErrNotRunning
	}
	if _, exists := r.workers[spec.Name]; exists {
		r.mu.Unlock()
		return ErrWorkerExists
	}
	r.workers[spec.Name] = struct{}{}
	ctx := r.ctx
	r.wg.Add(1)
	r.mu.Unlock()
	go r.runWorker(ctx, spec)
	return nil
}

func (r *Runtime) runWorker(ctx context.Context, spec WorkerSpec) {
	defer r.wg.Done()
	defer func() {
		r.mu.Lock()
		delete(r.workers, spec.Name)
		r.mu.Unlock()
	}()
	for attempt := 1; ; attempt++ {
		err, panicked := invoke(ctx, spec.Run)
		if err == nil || errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			return
		}
		failure := Failure{Worker: spec.Name, Attempt: attempt, Err: err, Panic: panicked, Fatal: spec.Fatal, At: time.Now().UTC()}
		r.report(failure)
		if spec.Fatal {
			r.cancel(failure)
			return
		}
		if attempt > spec.Restart.MaxRestarts || !waitContext(ctx, spec.Restart.Backoff) {
			return
		}
	}
}

func invoke(ctx context.Context, fn func(context.Context) error) (err error, panicked bool) {
	defer func() {
		if recovered := recover(); recovered != nil {
			err = fmt.Errorf("%v", recovered)
			panicked = true
		}
	}()
	return fn(ctx), false
}

func waitContext(ctx context.Context, duration time.Duration) bool {
	if duration == 0 {
		return ctx.Err() == nil
	}
	timer := time.NewTimer(duration)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}

func (r *Runtime) report(failure Failure) {
	select {
	case r.failures <- failure:
	default:
	}
}

func (r *Runtime) Failures() <-chan Failure { return r.failures }

func (r *Runtime) Stop() error { return r.StopContext(context.Background(), context.Canceled) }

func (r *Runtime) StopContext(wait context.Context, cause error) error {
	r.mu.Lock()
	if r.State() != StateRunning {
		r.mu.Unlock()
		return ErrNotRunning
	}
	r.state.Store(uint32(StateStopping))
	cancel := r.cancel
	r.mu.Unlock()
	cancel(cause)
	done := make(chan struct{})
	go func() {
		r.wg.Wait()
		r.state.CompareAndSwap(uint32(StateStopping), uint32(StateStopped))
		close(done)
	}()
	select {
	case <-done:
		return nil
	case <-wait.Done():
		return wait.Err()
	}
}
