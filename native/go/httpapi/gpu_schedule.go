package httpapi

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/providers"
	"github.com/AhmiDarrow/RemedyAI/native/go/sysres"
)

func lastCoresidenceMap(home string) map[string]any {
	raw, err := os.ReadFile(rmbJSONPath(home))
	if err != nil {
		return nil
	}
	var state map[string]any
	if json.Unmarshal(raw, &state) != nil {
		return nil
	}
	last, _ := state["last_coresidence"].(map[string]any)
	return last
}

func lastCoresidenceReuseURL(home string) string {
	last := lastCoresidenceMap(home)
	if last == nil {
		return ""
	}
	s, _ := last["reuse_base_url"].(string)
	return strings.TrimSpace(s)
}

func rmbGPUSchedule(home string) string {
	last := lastCoresidenceMap(home)
	if last == nil {
		return "overlap"
	}
	s, _ := last["schedule"].(string)
	s = strings.ToLower(strings.TrimSpace(s))
	if s == "serialize" || s == "overlap" {
		return s
	}
	return "overlap"
}

func acquireGPUSchedule(home string, model cognition.Model) func() {
	if strings.TrimSpace(home) == "" || rmbGPUSchedule(home) != "serialize" {
		return func() {}
	}
	oc, ok := model.(*providers.OpenAICompat)
	if !ok || oc == nil || !isLocalURL(oc.BaseURL) {
		return func() {}
	}
	path := filepath.Join(rmbHome(home), "gpu_schedule.lock")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return func() {}
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o644)
	if err != nil {
		return func() {}
	}
	if err := sysres.LockFile(f); err != nil {
		_ = f.Close()
		return func() {}
	}
	return func() {
		_ = sysres.UnlockFile(f)
		_ = f.Close()
	}
}

type gpuScheduledModel struct {
	inner cognition.Model
	home  string
}

func (m *gpuScheduledModel) ContextWindow() int { return m.inner.ContextWindow() }

func (m *gpuScheduledModel) Stream(ctx context.Context, turn cognition.Turn) (<-chan cognition.ModelEvent, error) {
	unlock := acquireGPUSchedule(m.home, m.inner)
	src, err := m.inner.Stream(ctx, turn)
	if err != nil {
		unlock()
		return nil, err
	}
	out := make(chan cognition.ModelEvent, 16)
	go func() {
		defer unlock()
		defer close(out)
		for ev := range src {
			select {
			case <-ctx.Done():
				go drainEvents(src)
				return
			case out <- ev:
			}
		}
	}()
	return out, nil
}

func wrapGPUSchedule(home string, model cognition.Model) cognition.Model {
	if model == nil || rmbGPUSchedule(home) != "serialize" {
		return model
	}
	if _, ok := model.(*gpuScheduledModel); ok {
		return model
	}
	return &gpuScheduledModel{inner: model, home: home}
}
