// Package runtime provides the supervised lifecycle for the persistent Remedy runtime.
package runtime

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"sync/atomic"
)

var (
	ErrAlreadyStarted = errors.New("remedy runtime is already started")
	ErrNotStarted     = errors.New("remedy runtime is not started")
)

type State uint32

const (
	StateNew State = iota
	StateRunning
	StateStopping
	StateStopped
)

type Runtime struct {
	state  atomic.Uint32
	mu     sync.Mutex
	ctx    context.Context
	cancel context.CancelFunc
	wg     sync.WaitGroup
	errs   chan error
}

func New() *Runtime {
	r := &Runtime{errs: make(chan error, 32)}
	r.state.Store(uint32(StateNew))
	return r
}

func (r *Runtime) State() State { return State(r.state.Load()) }

func (r *Runtime) Start(parent context.Context) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.State() != StateNew {
		return ErrAlreadyStarted
	}
	r.ctx, r.cancel = context.WithCancel(parent)
	r.state.Store(uint32(StateRunning))
	return nil
}

func (r *Runtime) Go(name string, fn func(context.Context) error) error {
	r.mu.Lock()
	if r.State() != StateRunning || r.ctx == nil {
		r.mu.Unlock()
		return ErrNotStarted
	}
	ctx := r.ctx
	r.wg.Add(1)
	r.mu.Unlock()
	go func() {
		defer r.wg.Done()
		defer func() {
			if recovered := recover(); recovered != nil {
				r.report(fmt.Errorf("worker %q panicked: %v", name, recovered))
			}
		}()
		if err := fn(ctx); err != nil && !errors.Is(err, context.Canceled) {
			r.report(fmt.Errorf("worker %q: %w", name, err))
		}
	}()
	return nil
}

func (r *Runtime) report(err error) {
	select {
	case r.errs <- err:
	default:
	}
}

func (r *Runtime) Errors() <-chan error { return r.errs }

func (r *Runtime) Stop() error {
	r.mu.Lock()
	if r.State() != StateRunning {
		r.mu.Unlock()
		return ErrNotStarted
	}
	r.state.Store(uint32(StateStopping))
	cancel := r.cancel
	r.mu.Unlock()
	cancel()
	r.wg.Wait()
	r.state.Store(uint32(StateStopped))
	return nil
}
