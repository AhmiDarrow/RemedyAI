package httpapi

import (
	"sync"
	"time"
)

// Remedy's mood is one value, decided here from what she is doing, so her face
// reads the same in Grove, in Studio and on a paired phone. A surface used to
// work it out from its own conversation (what was typed there, how its last
// turn went), and two surfaces showed two moods.
//
// The rule, first match wins:
//
//	a turn is running, anywhere
//	    a tool is running, and it is at least the third step   focused
//	    a tool is running                                       working
//	    she is thinking                                         thinking
//	    otherwise                                               working
//	she is busy on her own: learning / a night act              dreaming / watchful
//	the last turn ended within moodAfterglow
//	    every tool call in it failed                            error
//	    some failed                                             concerned
//	    three or more succeeded                                 proud
//	otherwise                                                   idle
//
// "idle" is the resting face; a surface that cannot reach the runtime shows its
// own error face, which is not a mood.
const moodAfterglow = 10 * time.Minute

type moodPhase struct {
	tools    int // tool calls running now
	steps    int // tool calls started this turn
	ok, err  int // finished tool calls
	thinking bool
}

type moodState struct {
	mu   sync.Mutex
	live map[string]*moodPhase
	// The last finished turn's tool outcomes and when it ended.
	lastOK, lastErr int
	lastAt          time.Time
	shown           string
}

func (m *moodState) phase(sid string) *moodPhase {
	if m.live == nil {
		m.live = map[string]*moodPhase{}
	}
	p := m.live[sid]
	if p == nil {
		p = &moodPhase{}
		m.live[sid] = p
	}
	return p
}

// moodNote records a change in a running turn and tells clients when the mood
// it gives is a new one.
func (s *Server) moodNote(sid string, change func(*moodPhase)) {
	if s == nil {
		return
	}
	s.mood.mu.Lock()
	change(s.mood.phase(sid))
	s.mood.mu.Unlock()
	s.moodPublish(sid)
}

// moodEnd closes a turn's phase and keeps how it went.
func (s *Server) moodEnd(sid string) {
	if s == nil {
		return
	}
	s.mood.mu.Lock()
	if p := s.mood.live[sid]; p != nil {
		if p.steps > 0 {
			s.mood.lastOK, s.mood.lastErr, s.mood.lastAt = p.ok, p.err, time.Now()
		}
		delete(s.mood.live, sid)
	}
	s.mood.mu.Unlock()
	s.moodPublish(sid)
}

// currentMood is the rule above.
func (s *Server) currentMood() string {
	if s == nil {
		return "idle"
	}
	running := 0
	if s.claims != nil {
		running = len(s.claims.ActiveSessions())
	}
	s.mood.mu.Lock()
	defer s.mood.mu.Unlock()
	if running > 0 || len(s.mood.live) > 0 {
		tools, steps, thinking := 0, 0, false
		for _, p := range s.mood.live {
			tools += p.tools
			steps = max(steps, p.steps)
			thinking = thinking || p.thinking
		}
		switch {
		case tools > 0 && steps >= 3:
			return "focused"
		case tools > 0:
			return "working"
		case thinking:
			return "thinking"
		}
		return "working"
	}
	switch act, _ := s.innerAct.Load().(string); act {
	case "learning":
		return "dreaming"
	case "vigil":
		return "watchful"
	}
	if !s.mood.lastAt.IsZero() && time.Since(s.mood.lastAt) < moodAfterglow {
		switch {
		case s.mood.lastErr > 0 && s.mood.lastOK == 0:
			return "error"
		case s.mood.lastErr > 0:
			return "concerned"
		case s.mood.lastOK >= 3:
			return "proud"
		}
	}
	return "idle"
}

// moodPublish sends a "mood" session event when the mood changed, so an open
// window follows at once. A client that only polls reads it from the head
// overview ("inner.mood").
func (s *Server) moodPublish(sid string) {
	now := s.currentMood()
	s.mood.mu.Lock()
	changed := now != s.mood.shown
	s.mood.shown = now
	s.mood.mu.Unlock()
	if !changed || sid == "" {
		return
	}
	s.publishSessionEvent(SessionEvent{Type: "mood", SessionID: sid, Title: &now})
}
