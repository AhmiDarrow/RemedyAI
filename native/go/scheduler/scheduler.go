// Package scheduler runs durable, bounded Remedy jobs from time, event, and goal triggers.
package scheduler

import (
	"context"
	"encoding/json"
	"errors"
	"sort"
	"sync"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/events"
)

var (
	ErrJobExists   = errors.New("job already exists")
	ErrCycle       = errors.New("job dependency cycle")
	ErrJobNotFound = errors.New("job not found")
)

type Trigger uint8

const (
	OneShot Trigger = iota
	Recurring
	OnEvent
	OnGoal
)

type Status string

const (
	Pending   Status = "pending"
	Running   Status = "running"
	Completed Status = "completed"
	Failed    Status = "failed"
	Canceled  Status = "canceled"
	Exhausted Status = "exhausted"
)

type Budget struct {
	MaxRuns    int           `json:"max_runs"`
	MaxRuntime time.Duration `json:"max_runtime"`
}
type Job struct {
	ID           string        `json:"id"`
	Trigger      Trigger       `json:"trigger"`
	NextRun      time.Time     `json:"next_run"`
	Interval     time.Duration `json:"interval"`
	EventType    string        `json:"event_type"`
	GoalID       string        `json:"goal_id"`
	Dependencies []string      `json:"dependencies"`
	Priority     int           `json:"priority"`
	Deadline     time.Time     `json:"deadline"`
	Budget       Budget        `json:"budget"`
	Runs         int           `json:"runs"`
	Runtime      time.Duration `json:"runtime"`
	Status       Status        `json:"status"`
	Ready        bool          `json:"ready"`
	LastError    string        `json:"last_error,omitempty"`
}
type Executor interface {
	Execute(context.Context, Job) error
}
type ExecutorFunc func(context.Context, Job) error

func (f ExecutorFunc) Execute(ctx context.Context, job Job) error { return f(ctx, job) }

type Snapshot struct {
	Version int   `json:"version"`
	Jobs    []Job `json:"jobs"`
}
type Scheduler struct {
	mu       sync.Mutex
	jobs     map[string]*Job
	executor Executor
	now      func() time.Time
}

func New(executor Executor, now func() time.Time) *Scheduler {
	if now == nil {
		now = time.Now
	}
	return &Scheduler{jobs: make(map[string]*Job), executor: executor, now: now}
}
func (s *Scheduler) Add(job Job) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if job.ID == "" {
		return errors.New("invalid job")
	}
	if _, ok := s.jobs[job.ID]; ok {
		return ErrJobExists
	}
	if job.Status == "" {
		job.Status = Pending
	}
	job.Dependencies = append([]string(nil), job.Dependencies...)
	s.jobs[job.ID] = &job
	if s.hasCycle() {
		delete(s.jobs, job.ID)
		return ErrCycle
	}
	return nil
}
func (s *Scheduler) Cancel(id string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	job := s.jobs[id]
	if job == nil {
		return ErrJobNotFound
	}
	job.Status = Canceled
	return nil
}
func (s *Scheduler) HandleEvent(event events.Event) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, job := range s.jobs {
		if job.Trigger == OnEvent && job.EventType == event.Type && job.Status == Pending {
			job.Ready = true
		}
	}
}
func (s *Scheduler) GoalReady(goalID string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, job := range s.jobs {
		if job.Trigger == OnGoal && job.GoalID == goalID && job.Status == Pending {
			job.Ready = true
		}
	}
}
func (s *Scheduler) Tick(ctx context.Context, now time.Time) []Job {
	s.mu.Lock()
	ready := make([]*Job, 0)
	for _, job := range s.jobs {
		if s.runnable(job, now) {
			job.Status = Running
			ready = append(ready, job)
		}
	}
	sort.SliceStable(ready, func(i, j int) bool { return ready[i].Priority > ready[j].Priority })
	s.mu.Unlock()
	completed := make([]Job, 0, len(ready))
	for _, job := range ready {
		start := s.now()
		err := s.executor.Execute(ctx, clone(*job))
		elapsed := s.now().Sub(start)
		s.mu.Lock()
		job.Runs++
		job.Runtime += elapsed
		job.Ready = false
		if err != nil {
			job.Status = Failed
			job.LastError = err.Error()
		} else if job.Trigger == Recurring {
			job.Status = Pending
			job.NextRun = now.Add(job.Interval)
		} else {
			job.Status = Completed
		}
		if (job.Budget.MaxRuns > 0 && job.Runs >= job.Budget.MaxRuns) || (job.Budget.MaxRuntime > 0 && job.Runtime >= job.Budget.MaxRuntime) {
			if job.Status == Pending {
				job.Status = Exhausted
			}
		}
		completed = append(completed, clone(*job))
		s.mu.Unlock()
	}
	return completed
}
func (s *Scheduler) Run(ctx context.Context, ticks <-chan time.Time) error {
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case tick, ok := <-ticks:
			if !ok {
				return nil
			}
			s.Tick(ctx, tick)
		}
	}
}
func (s *Scheduler) Snapshot() ([]byte, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	jobs := make([]Job, 0, len(s.jobs))
	for _, job := range s.jobs {
		jobs = append(jobs, clone(*job))
	}
	sort.Slice(jobs, func(i, j int) bool { return jobs[i].ID < jobs[j].ID })
	return json.Marshal(Snapshot{Version: 1, Jobs: jobs})
}
func Restore(raw []byte, executor Executor, now func() time.Time) (*Scheduler, error) {
	var snapshot Snapshot
	if err := json.Unmarshal(raw, &snapshot); err != nil {
		return nil, err
	}
	if snapshot.Version != 1 {
		return nil, errors.New("unsupported scheduler snapshot")
	}
	scheduler := New(executor, now)
	for _, job := range snapshot.Jobs {
		if job.Status == Running {
			job.Status = Pending
		}
		if err := scheduler.Add(job); err != nil {
			return nil, err
		}
	}
	return scheduler, nil
}
func (s *Scheduler) runnable(job *Job, now time.Time) bool {
	if job.Status != Pending {
		return false
	}
	if !job.Deadline.IsZero() && now.After(job.Deadline) {
		job.Status = Failed
		job.LastError = "deadline exceeded"
		return false
	}
	if job.Budget.MaxRuns > 0 && job.Runs >= job.Budget.MaxRuns {
		job.Status = Exhausted
		return false
	}
	for _, id := range job.Dependencies {
		dep := s.jobs[id]
		if dep == nil || dep.Status != Completed {
			return false
		}
	}
	switch job.Trigger {
	case OneShot, Recurring:
		return !now.Before(job.NextRun)
	case OnEvent, OnGoal:
		return job.Ready
	}
	return false
}
func (s *Scheduler) hasCycle() bool {
	visiting := make(map[string]bool)
	visited := make(map[string]bool)
	var visit func(string) bool
	visit = func(id string) bool {
		if visiting[id] {
			return true
		}
		if visited[id] {
			return false
		}
		visiting[id] = true
		if job := s.jobs[id]; job != nil {
			for _, dep := range job.Dependencies {
				if visit(dep) {
					return true
				}
			}
		}
		visiting[id] = false
		visited[id] = true
		return false
	}
	for id := range s.jobs {
		if visit(id) {
			return true
		}
	}
	return false
}
func clone(job Job) Job { job.Dependencies = append([]string(nil), job.Dependencies...); return job }
