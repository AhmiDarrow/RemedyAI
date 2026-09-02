// Package state persists the long-lived Remedy runtime state independently of UI sessions.
package state

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"

	"github.com/AhmiDarrow/RemedyAI/native/go/events"
)

const Version = 1

var ErrUnsupportedVersion = errors.New("unsupported runtime-state version")

type Goal struct {
	ID          string `json:"id"`
	Description string `json:"description"`
	Status      string `json:"status"`
}
type Task struct {
	ID          string `json:"id"`
	GoalID      string `json:"goal_id"`
	Description string `json:"description"`
	Status      string `json:"status"`
}
type Snapshot struct {
	Version       int               `json:"version"`
	LastEvent     uint64            `json:"last_event"`
	Goals         map[string]Goal   `json:"goals"`
	Tasks         map[string]Task   `json:"tasks"`
	Environment   map[string]string `json:"environment"`
	Relationships map[string]string `json:"relationships"`
	Self          map[string]string `json:"self"`
}
type Mutation struct {
	Area  string          `json:"area"`
	Key   string          `json:"key"`
	Value json.RawMessage `json:"value"`
}
type Store struct {
	mu       sync.RWMutex
	path     string
	snapshot Snapshot
}

func Open(path string) (*Store, error) {
	store := &Store{path: path, snapshot: empty()}
	payload, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return store, nil
	}
	if err != nil {
		return nil, err
	}
	if err := json.Unmarshal(payload, &store.snapshot); err != nil {
		return nil, err
	}
	if store.snapshot.Version != Version {
		return nil, ErrUnsupportedVersion
	}
	store.ensureMaps()
	return store, nil
}
func (s *Store) Snapshot() Snapshot { s.mu.RLock(); defer s.mu.RUnlock(); return clone(s.snapshot) }

func (s *Store) Apply(event events.Event) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if event.Sequence <= s.snapshot.LastEvent {
		return nil
	}
	var mutation Mutation
	if err := json.Unmarshal(event.Data, &mutation); err != nil {
		return err
	}
	switch mutation.Area {
	case "goal":
		var value Goal
		if json.Unmarshal(mutation.Value, &value) != nil || value.ID == "" {
			return errors.New("invalid goal mutation")
		}
		s.snapshot.Goals[mutation.Key] = value
	case "task":
		var value Task
		if json.Unmarshal(mutation.Value, &value) != nil || value.ID == "" {
			return errors.New("invalid task mutation")
		}
		s.snapshot.Tasks[mutation.Key] = value
	case "environment":
		var value string
		if json.Unmarshal(mutation.Value, &value) != nil {
			return errors.New("invalid environment mutation")
		}
		s.snapshot.Environment[mutation.Key] = value
	case "relationship":
		var value string
		if json.Unmarshal(mutation.Value, &value) != nil {
			return errors.New("invalid relationship mutation")
		}
		s.snapshot.Relationships[mutation.Key] = value
	case "self":
		var value string
		if json.Unmarshal(mutation.Value, &value) != nil {
			return errors.New("invalid self mutation")
		}
		s.snapshot.Self[mutation.Key] = value
	default:
		return fmt.Errorf("unknown mutation area %q", mutation.Area)
	}
	s.snapshot.LastEvent = event.Sequence
	return nil
}

func (s *Store) Checkpoint() error {
	s.mu.RLock()
	payload, err := json.Marshal(s.snapshot)
	s.mu.RUnlock()
	if err != nil {
		return err
	}
	dir := filepath.Dir(s.path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	temp, err := os.CreateTemp(dir, ".remedy-state-*.tmp")
	if err != nil {
		return err
	}
	tempPath := temp.Name()
	defer os.Remove(tempPath)
	if err := temp.Chmod(0o600); err != nil {
		temp.Close()
		return err
	}
	if _, err := temp.Write(payload); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Sync(); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	return replaceFile(tempPath, s.path)
}
func (s *Store) Replay(stream []events.Event) error {
	for _, event := range stream {
		if err := s.Apply(event); err != nil {
			return err
		}
	}
	return nil
}
func (s *Store) ensureMaps() {
	if s.snapshot.Goals == nil {
		s.snapshot.Goals = make(map[string]Goal)
	}
	if s.snapshot.Tasks == nil {
		s.snapshot.Tasks = make(map[string]Task)
	}
	if s.snapshot.Environment == nil {
		s.snapshot.Environment = make(map[string]string)
	}
	if s.snapshot.Relationships == nil {
		s.snapshot.Relationships = make(map[string]string)
	}
	if s.snapshot.Self == nil {
		s.snapshot.Self = make(map[string]string)
	}
}
func empty() Snapshot {
	return Snapshot{Version: Version, Goals: make(map[string]Goal), Tasks: make(map[string]Task), Environment: make(map[string]string), Relationships: make(map[string]string), Self: make(map[string]string)}
}
func clone(in Snapshot) Snapshot {
	out := empty()
	out.LastEvent = in.LastEvent
	for k, v := range in.Goals {
		out.Goals[k] = v
	}
	for k, v := range in.Tasks {
		out.Tasks[k] = v
	}
	for k, v := range in.Environment {
		out.Environment[k] = v
	}
	for k, v := range in.Relationships {
		out.Relationships[k] = v
	}
	for k, v := range in.Self {
		out.Self[k] = v
	}
	return out
}
