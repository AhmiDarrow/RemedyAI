package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/events"
	"github.com/AhmiDarrow/RemedyAI/native/go/scheduler"
)

type schedulerJobBody struct {
	ID           string   `json:"id"`
	Trigger      string   `json:"trigger"`
	NextRun      string   `json:"next_run"`
	IntervalMS   int64    `json:"interval_ms"`
	EventType    string   `json:"event_type"`
	GoalID       string   `json:"goal_id"`
	Dependencies []string `json:"dependencies"`
	Priority     int      `json:"priority"`
	Deadline     string   `json:"deadline"`
	MaxRuns      int      `json:"max_runs"`
	MaxRuntimeMS int64    `json:"max_runtime_ms"`
}

func (s *Server) initScheduler(home string) {
	exec := scheduler.ExecutorFunc(func(ctx context.Context, job scheduler.Job) error {
		payload, _ := json.Marshal(map[string]any{
			"job_id": job.ID,
			"status": "running",
		})
		s.publishBusEvent(events.Event{
			Type:   "scheduler.job",
			Source: "scheduler",
			Data:   payload,
		})
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
			return nil
		}
	})
	path := ""
	home = strings.TrimSpace(home)
	if home != "" {
		path = filepath.Join(home, "scheduler.json")
	}
	s.schedPath = path
	if path != "" {
		if raw, err := os.ReadFile(path); err == nil && len(raw) > 0 {
			restored, rerr := scheduler.Restore(raw, exec, time.Now)
			if rerr == nil {
				s.sched = restored
				return
			}
			// Keep the unreadable snapshot for inspection instead of letting
			// the next persist overwrite the only copy of the owner's jobs.
			quarantine := path + ".corrupt-" + time.Now().UTC().Format("20060102T150405Z")
			if mvErr := os.Rename(path, quarantine); mvErr != nil {
				log.Printf("scheduler: snapshot %s unreadable (%v) and could not be quarantined: %v", path, rerr, mvErr)
			} else {
				log.Printf("scheduler: snapshot unreadable (%v); moved to %s", rerr, quarantine)
			}
		}
	}
	s.sched = scheduler.New(exec, time.Now)
}

func (s *Server) persistScheduler() {
	if s == nil || s.sched == nil || strings.TrimSpace(s.schedPath) == "" {
		return
	}
	raw, err := s.sched.Snapshot()
	if err != nil {
		return
	}
	if err := writeFileSynced(s.schedPath, raw, 0o600); err != nil {
		log.Printf("scheduler: persist %s: %v", s.schedPath, err)
	}
}

// writeFileSynced writes data to a sibling temp file, fsyncs it, and renames
// it over path so a crash leaves either the old or the new snapshot.
func writeFileSynced(path string, data []byte, perm os.FileMode) error {
	tmp := path + ".tmp"
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, perm)
	if err != nil {
		return err
	}
	if _, err := f.Write(data); err != nil {
		_ = f.Close()
		_ = os.Remove(tmp)
		return err
	}
	if err := f.Sync(); err != nil {
		_ = f.Close()
		_ = os.Remove(tmp)
		return err
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return nil
}

func (s *Server) startSchedulerLoop(ctx context.Context) {
	if s == nil || s.sched == nil {
		return
	}
	tickCtx, cancel := context.WithCancel(ctx)
	s.schedCancel = cancel
	ticks := time.NewTicker(time.Second)
	go func() {
		defer ticks.Stop()
		for {
			select {
			case <-tickCtx.Done():
				return
			case now := <-ticks.C:
				_ = s.sched.Tick(tickCtx, now)
				s.persistScheduler()
			}
		}
	}()
}

func (s *Server) stopSchedulerLoop() {
	if s == nil {
		return
	}
	if s.schedCancel != nil {
		s.schedCancel()
		s.schedCancel = nil
	}
	s.persistScheduler()
}

func (s *Server) handleSchedulerList(w http.ResponseWriter, _ *http.Request) {
	if s.sched == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "error": "scheduler unavailable"})
		return
	}
	jobs := s.sched.Jobs()
	out := make([]map[string]any, 0, len(jobs))
	for _, job := range jobs {
		out = append(out, schedulerJobToMap(job))
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":    true,
		"jobs":  out,
		"count": len(out),
	})
}

func (s *Server) handleSchedulerAdd(w http.ResponseWriter, r *http.Request) {
	if s.sched == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "error": "scheduler unavailable"})
		return
	}
	var body schedulerJobBody
	dec := json.NewDecoder(r.Body)
	dec.UseNumber()
	if err := dec.Decode(&body); err != nil && err != io.EOF {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "invalid JSON body"})
		return
	}
	job, err := schedulerJobFromBody(body)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if err := s.sched.Add(job); err != nil {
		status := http.StatusBadRequest
		if errors.Is(err, scheduler.ErrJobExists) {
			status = http.StatusConflict
		}
		writeJSON(w, status, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	s.persistScheduler()
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "job": schedulerJobToMap(job)})
}

func (s *Server) handleSchedulerCancel(w http.ResponseWriter, r *http.Request) {
	if s.sched == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "error": "scheduler unavailable"})
		return
	}
	id := strings.TrimSpace(r.PathValue("id"))
	if id == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "id required"})
		return
	}
	if err := s.sched.Cancel(id); err != nil {
		status := http.StatusBadRequest
		if errors.Is(err, scheduler.ErrJobNotFound) {
			status = http.StatusNotFound
		}
		writeJSON(w, status, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	s.persistScheduler()
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "id": id, "status": string(scheduler.Canceled)})
}

func (s *Server) handleSchedulerGoalReady(w http.ResponseWriter, r *http.Request) {
	if s.sched == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "error": "scheduler unavailable"})
		return
	}
	var body struct {
		GoalID string `json:"goal_id"`
	}
	dec := json.NewDecoder(r.Body)
	if err := dec.Decode(&body); err != nil && err != io.EOF {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "invalid JSON body"})
		return
	}
	gid := strings.TrimSpace(body.GoalID)
	if gid == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "goal_id required"})
		return
	}
	s.sched.GoalReady(gid)
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "goal_id": gid})
}

func schedulerJobFromBody(body schedulerJobBody) (scheduler.Job, error) {
	id := strings.TrimSpace(body.ID)
	if id == "" {
		return scheduler.Job{}, errors.New("id required")
	}
	trigger, err := parseSchedulerTrigger(body.Trigger)
	if err != nil {
		return scheduler.Job{}, err
	}
	job := scheduler.Job{
		ID:           id,
		Trigger:      trigger,
		EventType:    strings.TrimSpace(body.EventType),
		GoalID:       strings.TrimSpace(body.GoalID),
		Dependencies: append([]string(nil), body.Dependencies...),
		Priority:     body.Priority,
		Budget: scheduler.Budget{
			MaxRuns:    body.MaxRuns,
			MaxRuntime: time.Duration(body.MaxRuntimeMS) * time.Millisecond,
		},
		Status: scheduler.Pending,
	}
	if body.IntervalMS > 0 {
		job.Interval = time.Duration(body.IntervalMS) * time.Millisecond
	}
	if raw := strings.TrimSpace(body.NextRun); raw != "" {
		t, err := time.Parse(time.RFC3339Nano, raw)
		if err != nil {
			t, err = time.Parse(time.RFC3339, raw)
		}
		if err != nil {
			return scheduler.Job{}, errors.New("invalid next_run")
		}
		job.NextRun = t.UTC()
	} else if trigger == scheduler.OneShot || trigger == scheduler.Recurring {
		job.NextRun = time.Now().UTC()
	}
	if raw := strings.TrimSpace(body.Deadline); raw != "" {
		t, err := time.Parse(time.RFC3339Nano, raw)
		if err != nil {
			t, err = time.Parse(time.RFC3339, raw)
		}
		if err != nil {
			return scheduler.Job{}, errors.New("invalid deadline")
		}
		job.Deadline = t.UTC()
	}
	return job, nil
}

func parseSchedulerTrigger(raw string) (scheduler.Trigger, error) {
	switch strings.ToLower(strings.TrimSpace(raw)) {
	case "", "one_shot", "oneshot", "once":
		return scheduler.OneShot, nil
	case "recurring", "interval", "cron":
		return scheduler.Recurring, nil
	case "on_event", "event":
		return scheduler.OnEvent, nil
	case "on_goal", "goal":
		return scheduler.OnGoal, nil
	default:
		return 0, errors.New("invalid trigger")
	}
}

func schedulerTriggerName(t scheduler.Trigger) string {
	switch t {
	case scheduler.Recurring:
		return "recurring"
	case scheduler.OnEvent:
		return "on_event"
	case scheduler.OnGoal:
		return "on_goal"
	default:
		return "one_shot"
	}
}

func schedulerJobToMap(job scheduler.Job) map[string]any {
	m := map[string]any{
		"id":             job.ID,
		"trigger":        schedulerTriggerName(job.Trigger),
		"event_type":     job.EventType,
		"goal_id":        job.GoalID,
		"dependencies":   job.Dependencies,
		"priority":       job.Priority,
		"max_runs":       job.Budget.MaxRuns,
		"max_runtime_ms": job.Budget.MaxRuntime.Milliseconds(),
		"runs":           job.Runs,
		"runtime_ms":     job.Runtime.Milliseconds(),
		"status":         string(job.Status),
		"ready":          job.Ready,
		"interval_ms":    job.Interval.Milliseconds(),
	}
	if !job.NextRun.IsZero() {
		m["next_run"] = job.NextRun.UTC().Format(time.RFC3339Nano)
	}
	if !job.Deadline.IsZero() {
		m["deadline"] = job.Deadline.UTC().Format(time.RFC3339Nano)
	}
	if job.LastError != "" {
		m["last_error"] = job.LastError
	}
	if job.Dependencies == nil {
		m["dependencies"] = []string{}
	}
	return m
}
