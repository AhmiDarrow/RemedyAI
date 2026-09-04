package httpapi

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/events"
)

func (s *Server) openEventBus(home string) error {
	home = strings.TrimSpace(home)
	path := ""
	if home != "" {
		path = filepath.Join(home, "events.log")
	} else {
		dir, err := os.MkdirTemp("", "remedy-events-*")
		if err != nil {
			return err
		}
		s.ephemeralEventDir = dir
		path = filepath.Join(dir, "events.log")
	}
	bus, err := events.Open(path)
	if err != nil {
		return err
	}
	s.bus = bus
	s.eventLogPath = path
	return nil
}

func (s *Server) closeEventBus() {
	if s == nil {
		return
	}
	if s.bus != nil {
		_ = s.bus.Close()
		s.bus = nil
	}
	if s.ephemeralEventDir != "" {
		_ = os.RemoveAll(s.ephemeralEventDir)
		s.ephemeralEventDir = ""
	}
}

func (s *Server) publishBusEvent(ev events.Event) {
	if s == nil || s.bus == nil {
		return
	}
	published, err := s.bus.Publish(ev)
	if err != nil {
		return
	}
	if s.sched != nil {
		s.sched.HandleEvent(published)
	}
}

// handleEventsReplay serves GET /api/events?from=&limit= (durable bus ownership).
func (s *Server) handleEventsReplay(w http.ResponseWriter, r *http.Request) {
	if s.bus == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{
			"ok":    false,
			"error": "event bus unavailable",
		})
		return
	}
	from := uint64(1)
	if raw := strings.TrimSpace(r.URL.Query().Get("from")); raw != "" {
		n, err := strconv.ParseUint(raw, 10, 64)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "invalid from"})
			return
		}
		from = n
	}
	limit := 1000
	if raw := strings.TrimSpace(r.URL.Query().Get("limit")); raw != "" {
		n, err := strconv.Atoi(raw)
		if err != nil || n < 0 {
			writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "invalid limit"})
			return
		}
		limit = n
	}
	rows := s.bus.Replay(from, limit)
	out := make([]map[string]any, 0, len(rows))
	for _, ev := range rows {
		out = append(out, eventToMap(ev))
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":     true,
		"events": out,
		"count":  len(out),
	})
}

// handleEventsStream serves GET /api/events/stream — live durable-bus SSE.
func (s *Server) handleEventsStream(w http.ResponseWriter, r *http.Request) {
	if s.bus == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{
			"ok":    false,
			"error": "event bus unavailable",
		})
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming unsupported", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)

	hello, _ := json.Marshal(map[string]any{"ok": true, "ts": float64(time.Now().UnixNano()) / 1e9})
	_, _ = fmt.Fprintf(w, "event: hello\ndata: %s\n\n", hello)
	flusher.Flush()

	typesFilter := map[string]bool{}
	if raw := strings.TrimSpace(r.URL.Query().Get("type")); raw != "" {
		for _, part := range strings.Split(raw, ",") {
			part = strings.TrimSpace(part)
			if part != "" {
				typesFilter[part] = true
			}
		}
	}
	source := strings.TrimSpace(r.URL.Query().Get("source"))
	sub := s.bus.Subscribe(events.Filter{Types: typesFilter, Source: source}, 64, events.DropOldest)
	defer sub.Close()

	ctx := r.Context()
	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			_, _ = fmt.Fprintf(w, ": ping\n\n")
			flusher.Flush()
		case ev, ok := <-sub.C:
			if !ok {
				return
			}
			payload, err := json.Marshal(eventToMap(ev))
			if err != nil {
				continue
			}
			etype := ev.Type
			if etype == "" {
				etype = "event"
			}
			_, _ = fmt.Fprintf(w, "event: %s\ndata: %s\n\n", etype, payload)
			flusher.Flush()
		}
	}
}

func eventToMap(ev events.Event) map[string]any {
	m := map[string]any{
		"sequence": ev.Sequence,
		"type":     ev.Type,
		"source":   ev.Source,
		"at":       ev.At.UTC().Format(time.RFC3339Nano),
	}
	if len(ev.Data) > 0 {
		var raw any
		if json.Unmarshal(ev.Data, &raw) == nil {
			m["data"] = raw
		} else {
			m["data"] = json.RawMessage(append([]byte(nil), ev.Data...))
		}
	}
	return m
}
