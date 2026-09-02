// Package workers supervises replaceable Python/ML capability processes.
package workers

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"
)

const ProtocolVersion = 1

var (
	ErrNoCapability     = errors.New("no worker supplies capability")
	ErrProtocolMismatch = errors.New("worker protocol version mismatch")
)

type Capability string

const (
	Model    Capability = "model"
	Vision   Capability = "vision"
	Speech   Capability = "speech"
	Research Capability = "research"
)

type Spec struct {
	ID           string
	Capabilities []Capability
	MaxRestarts  int
	CallTimeout  time.Duration
}
type Health struct {
	Protocol     int
	Ready        bool
	Capabilities []Capability
}
type Request struct {
	ID         string
	Capability Capability
	Operation  string
	Payload    []byte
}
type Response struct {
	ID      string
	Payload []byte
	Final   bool
	Error   string
}
type Worker interface {
	Health(context.Context) (Health, error)
	Call(context.Context, Request) (Response, error)
	Stream(context.Context, Request) (<-chan Response, error)
	Close() error
}
type Factory interface {
	Connect(context.Context, Spec) (Worker, error)
}
type FactoryFunc func(context.Context, Spec) (Worker, error)

func (f FactoryFunc) Connect(ctx context.Context, spec Spec) (Worker, error) { return f(ctx, spec) }

type managed struct {
	mu       sync.Mutex
	spec     Spec
	worker   Worker
	restarts int
}
type Manager struct {
	mu      sync.RWMutex
	factory Factory
	workers map[string]*managed
	routes  map[Capability]string
}

func New(factory Factory) *Manager {
	return &Manager{factory: factory, workers: make(map[string]*managed), routes: make(map[Capability]string)}
}
func (m *Manager) Register(spec Spec) error {
	if spec.ID == "" || len(spec.Capabilities) == 0 || spec.MaxRestarts < 0 {
		return errors.New("invalid worker specification")
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, exists := m.workers[spec.ID]; exists {
		return errors.New("worker already registered")
	}
	spec.Capabilities = append([]Capability(nil), spec.Capabilities...)
	m.workers[spec.ID] = &managed{spec: spec}
	for _, capability := range spec.Capabilities {
		if _, exists := m.routes[capability]; !exists {
			m.routes[capability] = spec.ID
		}
	}
	return nil
}
func (m *Manager) Call(ctx context.Context, request Request) (Response, error) {
	worker, err := m.route(request.Capability)
	if err != nil {
		return Response{}, err
	}
	return worker.call(ctx, m.factory, request)
}
func (m *Manager) Stream(ctx context.Context, request Request) (<-chan Response, error) {
	worker, err := m.route(request.Capability)
	if err != nil {
		return nil, err
	}
	return worker.stream(ctx, m.factory, request)
}
func (m *Manager) Close() error {
	m.mu.Lock()
	defer m.mu.Unlock()
	var joined error
	for _, worker := range m.workers {
		worker.mu.Lock()
		if worker.worker != nil {
			joined = errors.Join(joined, worker.worker.Close())
			worker.worker = nil
		}
		worker.mu.Unlock()
	}
	return joined
}
func (m *Manager) route(capability Capability) (*managed, error) {
	m.mu.RLock()
	id := m.routes[capability]
	worker := m.workers[id]
	m.mu.RUnlock()
	if worker == nil {
		return nil, ErrNoCapability
	}
	return worker, nil
}
func (w *managed) call(ctx context.Context, factory Factory, request Request) (Response, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	var last error
	for attempt := 0; attempt <= w.spec.MaxRestarts; attempt++ {
		worker, err := w.ready(ctx, factory)
		if err != nil {
			last = err
		} else {
			callCtx, cancel := bounded(ctx, w.spec.CallTimeout)
			response, callErr := worker.Call(callCtx, request)
			cancel()
			if callErr == nil {
				return response, nil
			}
			last = callErr
			_ = worker.Close()
			w.worker = nil
			w.restarts++
		}
	}
	return Response{}, fmt.Errorf("worker %q exhausted restarts: %w", w.spec.ID, last)
}
func (w *managed) stream(ctx context.Context, factory Factory, request Request) (<-chan Response, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	worker, err := w.ready(ctx, factory)
	if err != nil {
		return nil, err
	}
	streamCtx, _ := bounded(ctx, w.spec.CallTimeout)
	stream, err := worker.Stream(streamCtx, request)
	if err != nil {
		_ = worker.Close()
		w.worker = nil
		w.restarts++
	}
	return stream, err
}
func (w *managed) ready(ctx context.Context, factory Factory) (Worker, error) {
	if w.worker == nil {
		worker, err := factory.Connect(ctx, w.spec)
		if err != nil {
			return nil, err
		}
		w.worker = worker
	}
	health, err := w.worker.Health(ctx)
	if err != nil {
		_ = w.worker.Close()
		w.worker = nil
		return nil, err
	}
	if health.Protocol != ProtocolVersion {
		_ = w.worker.Close()
		w.worker = nil
		return nil, ErrProtocolMismatch
	}
	if !health.Ready {
		return nil, errors.New("worker is not ready")
	}
	return w.worker, nil
}
func bounded(parent context.Context, timeout time.Duration) (context.Context, context.CancelFunc) {
	if timeout <= 0 {
		return context.WithCancel(parent)
	}
	return context.WithTimeout(parent, timeout)
}
