// Package hive supervises lightweight scoped Remedy agents.
package hive

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"
)

var (
	ErrQuota                = errors.New("hive agent quota reached")
	ErrAgentExists          = errors.New("agent already exists")
	ErrAgentNotFound        = errors.New("agent not found")
	ErrCapabilityEscalation = errors.New("child capability exceeds parent delegation")
	ErrMailboxFull          = errors.New("agent mailbox is full")
)

type Status string

const (
	Starting  Status = "starting"
	Running   Status = "running"
	Completed Status = "completed"
	Failed    Status = "failed"
	Canceled  Status = "canceled"
)

type Message struct {
	From    string
	To      string
	Type    string
	Payload []byte
	At      time.Time
}
type Spec struct {
	ID           string
	Parent       string
	Goals        []string
	MemoryScope  string
	Capabilities []string
	MailboxSize  int
	Run          func(context.Context, *Agent) error
}
type Snapshot struct {
	ID           string
	Parent       string
	Goals        []string
	MemoryScope  string
	Capabilities []string
	Status       Status
	Error        string
}
type Agent struct {
	id    string
	inbox <-chan Message
	send  func(Message) error
}

func (a *Agent) ID() string                 { return a.id }
func (a *Agent) Inbox() <-chan Message      { return a.inbox }
func (a *Agent) Send(message Message) error { message.From = a.id; return a.send(message) }

type managed struct {
	spec      Spec
	status    Status
	lastError string
	inbox     chan Message
	cancel    context.CancelFunc
}
type Manager struct {
	mu        sync.RWMutex
	ctx       context.Context
	cancel    context.CancelFunc
	agents    map[string]*managed
	maxAgents int
	wg        sync.WaitGroup
}

func New(parent context.Context, maxAgents int) *Manager {
	if parent == nil {
		parent = context.Background()
	}
	if maxAgents <= 0 {
		maxAgents = 64
	}
	ctx, cancel := context.WithCancel(parent)
	return &Manager{ctx: ctx, cancel: cancel, agents: make(map[string]*managed), maxAgents: maxAgents}
}
func (m *Manager) Spawn(spec Spec) error {
	if spec.ID == "" || spec.Run == nil {
		return errors.New("invalid agent")
	}
	if spec.MailboxSize <= 0 {
		spec.MailboxSize = 32
	}
	m.mu.Lock()
	active := 0
	for _, existing := range m.agents {
		if existing.status == Starting || existing.status == Running {
			active++
		}
	}
	if active >= m.maxAgents {
		m.mu.Unlock()
		return ErrQuota
	}
	if _, ok := m.agents[spec.ID]; ok {
		m.mu.Unlock()
		return ErrAgentExists
	}
	if spec.Parent != "" {
		parent := m.agents[spec.Parent]
		if parent == nil {
			m.mu.Unlock()
			return ErrAgentNotFound
		}
		if !subset(spec.Capabilities, parent.spec.Capabilities) {
			m.mu.Unlock()
			return ErrCapabilityEscalation
		}
		if spec.MemoryScope == "" || spec.MemoryScope != parent.spec.MemoryScope {
			m.mu.Unlock()
			return ErrCapabilityEscalation
		}
	}
	spec.Goals = append([]string(nil), spec.Goals...)
	spec.Capabilities = append([]string(nil), spec.Capabilities...)
	ctx, cancel := context.WithCancel(m.ctx)
	agent := &managed{spec: spec, status: Starting, inbox: make(chan Message, spec.MailboxSize), cancel: cancel}
	m.agents[spec.ID] = agent
	m.wg.Add(1)
	m.mu.Unlock()
	go m.run(ctx, agent)
	return nil
}
func (m *Manager) run(ctx context.Context, managed *managed) {
	defer m.wg.Done()
	m.mu.Lock()
	managed.status = Running
	m.mu.Unlock()
	agent := &Agent{id: managed.spec.ID, inbox: managed.inbox, send: m.Send}
	err := invoke(ctx, func() error { return managed.spec.Run(ctx, agent) })
	m.mu.Lock()
	defer m.mu.Unlock()
	if err == nil {
		managed.status = Completed
	} else if errors.Is(err, context.Canceled) {
		managed.status = Canceled
	} else {
		managed.status = Failed
		managed.lastError = err.Error()
	}
}
func (m *Manager) Send(message Message) error {
	message.Payload = append([]byte(nil), message.Payload...)
	message.At = time.Now().UTC()
	m.mu.RLock()
	target := m.agents[message.To]
	m.mu.RUnlock()
	if target == nil {
		return ErrAgentNotFound
	}
	select {
	case target.inbox <- message:
		return nil
	default:
		return ErrMailboxFull
	}
}
func (m *Manager) Cancel(id string) error {
	m.mu.RLock()
	agent := m.agents[id]
	m.mu.RUnlock()
	if agent == nil {
		return ErrAgentNotFound
	}
	agent.cancel()
	return nil
}
func (m *Manager) Snapshot(id string) (Snapshot, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	agent := m.agents[id]
	if agent == nil {
		return Snapshot{}, ErrAgentNotFound
	}
	return Snapshot{ID: agent.spec.ID, Parent: agent.spec.Parent, Goals: append([]string(nil), agent.spec.Goals...), MemoryScope: agent.spec.MemoryScope, Capabilities: append([]string(nil), agent.spec.Capabilities...), Status: agent.status, Error: agent.lastError}, nil
}
func (m *Manager) Shutdown() { m.cancel(); m.wg.Wait() }
func subset(child, parent []string) bool {
	allowed := make(map[string]bool, len(parent))
	for _, capability := range parent {
		allowed[capability] = true
	}
	for _, capability := range child {
		if !allowed[capability] {
			return false
		}
	}
	return true
}
func invoke(ctx context.Context, run func() error) (err error) {
	defer func() {
		if recovered := recover(); recovered != nil {
			err = fmt.Errorf("agent panic: %v", recovered)
		}
	}()
	select {
	case <-ctx.Done():
		return ctx.Err()
	default:
		return run()
	}
}
