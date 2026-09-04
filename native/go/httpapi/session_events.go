package httpapi

import (
	"encoding/json"
	"fmt"
	"net/http"
	"sync"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/events"
)

// SessionEvent matches Python interfaces.session_events.SessionEvent.to_dict.
// All keys are always present (nulls encoded as JSON null).
type SessionEvent struct {
	Type           string  `json:"type"`
	SessionID      string  `json:"session_id"`
	OriginChannel  *string `json:"origin_channel"`
	MessageID      *string `json:"message_id"`
	Title          *string `json:"title"`
	MessageCount   *int    `json:"message_count"`
	Role           *string `json:"role"`
	TS             float64 `json:"ts"`
}

const sessionEventQueueSize = 256

type sessionEventHub struct {
	mu   sync.Mutex
	subs map[chan SessionEvent]struct{}
}

func newSessionEventHub() *sessionEventHub {
	return &sessionEventHub{subs: make(map[chan SessionEvent]struct{})}
}

func (h *sessionEventHub) subscribe() chan SessionEvent {
	ch := make(chan SessionEvent, sessionEventQueueSize)
	h.mu.Lock()
	h.subs[ch] = struct{}{}
	h.mu.Unlock()
	return ch
}

func (h *sessionEventHub) unsubscribe(ch chan SessionEvent) {
	h.mu.Lock()
	delete(h.subs, ch)
	h.mu.Unlock()
	// Do not close ch: publish may still hold a snapshot and send.
}

// publish fans out with drop-oldest then drop-subscriber (Python hub parity).
func (h *sessionEventHub) publish(ev SessionEvent) {
	if ev.SessionID == "" {
		return
	}
	if ev.TS == 0 {
		ev.TS = float64(time.Now().UnixNano()) / 1e9
	}
	h.mu.Lock()
	subs := make([]chan SessionEvent, 0, len(h.subs))
	for ch := range h.subs {
		subs = append(subs, ch)
	}
	h.mu.Unlock()

	var dead []chan SessionEvent
	for _, ch := range subs {
		select {
		case ch <- ev:
		default:
			// Drop oldest, retry once.
			select {
			case <-ch:
			default:
			}
			select {
			case ch <- ev:
			default:
				dead = append(dead, ch)
			}
		}
	}
	if len(dead) == 0 {
		return
	}
	h.mu.Lock()
	for _, ch := range dead {
		delete(h.subs, ch)
	}
	h.mu.Unlock()
}

func (s *Server) publishSessionEvent(ev SessionEvent) {
	if s == nil || s.events == nil {
		return
	}
	s.events.publish(ev)
	if s.bus == nil {
		return
	}
	payload, err := json.Marshal(ev)
	if err != nil {
		return
	}
	s.publishBusEvent(events.Event{
		Type:   ev.Type,
		Source: "session",
		Data:   payload,
	})
}

func (s *Server) handleSessionEvents(w http.ResponseWriter, r *http.Request) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeJSON(w, http.StatusInternalServerError, map[string]string{
			"detail": "streaming unsupported",
		})
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)

	hello, _ := json.Marshal(map[string]any{
		"type": "hello",
		"ts":   float64(time.Now().UnixNano()) / 1e9,
	})
	_, _ = fmt.Fprintf(w, "event: hello\ndata: %s\n\n", hello)
	flusher.Flush()

	ch := s.events.subscribe()
	defer s.events.unsubscribe(ch)

	ping := time.NewTicker(25 * time.Second)
	defer ping.Stop()

	for {
		select {
		case <-r.Context().Done():
			return
		case <-ping.C:
			_, _ = fmt.Fprintf(w, ": ping\n\n")
			flusher.Flush()
		case ev, ok := <-ch:
			if !ok {
				return
			}
			payload, err := json.Marshal(ev)
			if err != nil {
				continue
			}
			etype := ev.Type
			if etype == "" {
				etype = "session_updated"
			}
			_, _ = fmt.Fprintf(w, "event: %s\ndata: %s\n\n", etype, payload)
			flusher.Flush()
		}
	}
}
