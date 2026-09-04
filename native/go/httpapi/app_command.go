package httpapi

import (
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	appCmdMaxQueue = 32
	appCmdMaxAgeS  = 90.0
)

type appControlBus struct {
	mu  sync.Mutex
	q   []map[string]any
	seq int
}

func newAppControlBus() *appControlBus {
	return &appControlBus{q: make([]map[string]any, 0, 8)}
}

func (b *appControlBus) pruneLocked(now float64) {
	for len(b.q) > 0 {
		ts, _ := b.q[0]["ts"].(float64)
		if now-ts <= appCmdMaxAgeS {
			break
		}
		b.q = b.q[1:]
	}
}

func (b *appControlBus) peek() map[string]any {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.pruneLocked(float64(time.Now().UnixNano()) / 1e9)
	if len(b.q) == 0 {
		return nil
	}
	return cloneMap(b.q[0])
}

func (b *appControlBus) take() map[string]any {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.pruneLocked(float64(time.Now().UnixNano()) / 1e9)
	if len(b.q) == 0 {
		return nil
	}
	cmd := cloneMap(b.q[0])
	b.q = b.q[1:]
	return cmd
}

// Enqueue is used by tools/tests; Desktop polls take/peek only.
func (b *appControlBus) Enqueue(action string, params map[string]any) map[string]any {
	act := strings.TrimSpace(action)
	if act == "" {
		return map[string]any{"ok": false, "error": "unknown app action ''"}
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	b.seq++
	clean := map[string]any{}
	for k, v := range params {
		if v != nil {
			clean[k] = v
		}
	}
	cmd := map[string]any{
		"id":     "app-" + strconv.Itoa(b.seq),
		"action": act,
		"params": clean,
		"ts":     float64(time.Now().UnixNano()) / 1e9,
	}
	if len(b.q) >= appCmdMaxQueue {
		b.q = b.q[1:]
	}
	b.q = append(b.q, cmd)
	return map[string]any{"ok": true, "command": cloneMap(cmd)}
}

func (s *Server) handleAppCommand(w http.ResponseWriter, r *http.Request) {
	if s.appCmd == nil {
		s.appCmd = newAppControlBus()
	}
	q := r.URL.Query()
	take := q.Get("take") == "1" || strings.EqualFold(q.Get("take"), "true")
	var cmd map[string]any
	if take {
		cmd = s.appCmd.take()
	} else {
		cmd = s.appCmd.peek()
	}
	writeJSON(w, http.StatusOK, map[string]any{"command": cmd})
}
