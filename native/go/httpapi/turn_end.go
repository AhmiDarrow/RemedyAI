package httpapi

import (
	"log"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/mind/drives"
	"github.com/AhmiDarrow/RemedyAI/native/go/mind/learn"
)

// The rest of the old prompt.turn_end (production_learn.py
// _finish_production_turn_inner and agent_post_turn.schedule_post_turn_prep)
// that wrote something a reader still uses: the turn's outcome in alive.json,
// the learned-skill lifecycle tick, and session memory consolidation in
// memory.db. Each runs on owner turns only, as skill learning does: they
// change her inner state, her standing skills and the owner's profile.

// ownerTurn is an owner turn outside a hive pulse.
func ownerTurn(req TurnRequest) bool {
	return OriginIsOwner(req.Origin) && !isHiveSessionID(req.SessionID)
}

// noteOutcomeAfterTurn is alive.note_turn_outcome: what the owner's message
// says about the last suggestion (accepted, unhappy, fix held) goes into
// alive.json "outcomes", which shape her affect when she dreams.
func (r *CognitionTurnRunner) noteOutcomeAfterTurn(req TurnRequest, out cognition.Outcome) {
	home := strings.TrimSpace(r.HomeDir)
	if home == "" || !ownerTurn(req) {
		return
	}
	steps := toolStepsFromOutcome(out)
	ok := make([]bool, 0, len(steps))
	for _, s := range steps {
		v, has := s["ok"].(bool)
		ok = append(ok, v || !has)
	}
	started := time.Now()
	_, err := drives.NoteTurnOutcome(mindEnv(home), req.Prompt, ok)
	r.noteLearnCall("turn_end", started, nil, err)
}

// tickSkillsAfterTurn is the LearningLoop.tick_learned_skills that ran after
// every turn: promote, demote or retire learned skills from their stats.
func (r *CognitionTurnRunner) tickSkillsAfterTurn(req TurnRequest) {
	home := strings.TrimSpace(r.HomeDir)
	if home == "" || !ownerTurn(req) {
		return
	}
	started := time.Now()
	changes, err := learn.TickLearnedSkills(ResolveHomeDir(home), time.Now(), 0)
	r.noteLearnCall("turn_end", started, nil, err)
	for _, c := range changes {
		logSkillChange(c)
	}
}

// consolidateMinNotes is how many of a session's memory notes (of the latest
// 24) make the turn end fold them into one consolidation entry.
const consolidateMinNotes = 8

// consolidateMemoryAfterTurn is the MemoryConsolidator pass the old turn end
// ran: once a session holds 8 memory notes, they are summarized into one
// "Consolidation" entry, the sources are demoted, and preferences in them are
// distilled into the owner profile. It needs the server's memory.db.
func (r *CognitionTurnRunner) consolidateMemoryAfterTurn(req TurnRequest) {
	home := strings.TrimSpace(r.HomeDir)
	dbPath := strings.TrimSpace(r.MemoryDBPath)
	sid := strings.TrimSpace(req.SessionID)
	if home == "" || dbPath == "" || dbPath == ":memory:" || sid == "" || !ownerTurn(req) {
		return
	}
	if !cfgBool(LoadConfig(home), "soul_field_enabled", true) {
		return
	}
	started := time.Now()
	store, err := OpenCLIStore(dbPath)
	if err != nil {
		r.noteLearnCall("turn_end", started, nil, err)
		return
	}
	defer store.Close()
	notes, err := store.listBySession(sid, 24)
	if err == nil && len(notes) >= consolidateMinNotes {
		_, err = store.Consolidate(sid, 100)
	}
	r.noteLearnCall("turn_end", started, nil, err)
}

func logSkillChange(c learn.SkillChange) {
	log.Printf("skill lifecycle: %s %s -> %s (%s)", c.Name, c.From, c.To, c.Action)
}
