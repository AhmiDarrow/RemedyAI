package drives

import (
	"errors"
	"path/filepath"

	"github.com/AhmiDarrow/RemedyAI/native/go/mind/dream"
	sf "github.com/AhmiDarrow/RemedyAI/native/go/mind/internal/statefile"
	"github.com/AhmiDarrow/RemedyAI/native/go/mind/soul"
)

// JournalFile is the vigil journal; the vigil package appends to it and
// compact trims it, both under the same per-file lock.
const JournalFile = "vigil_journal.jsonl"

// Dream runs one dream cycle and, when it was not skipped, folds the result
// into her beliefs and autobiography (dream_cycle -> consolidate_from_dream).
func Dream(env soul.Env, force bool) (dream.Result, int, error) {
	res, err := dream.Cycle(env, force)
	if err != nil || res.Skipped {
		return res, 0, err
	}
	n, err := ConsolidateFromDream(env, res.Field)
	return res, n, err
}

// Compacted says which on-disk stores compact trimmed.
type Compacted struct {
	Soul    bool `json:"soul"`
	Residue bool `json:"residue"`
	Journal bool `json:"journal"`
}

// Any is true when something was trimmed.
func (c Compacted) Any() bool { return c.Soul || c.Residue || c.Journal }

// Compact bounds the soul field, leftover residue dumps and the vigil
// journal (compact_personhood). Every sub-step runs; errors are joined.
func Compact(env soul.Env) (Compacted, error) {
	var c Compacted
	var errs []error
	var err error
	if c.Soul, err = soul.Compact(env, sf.Int(env.Knobs, "soul_max_bytes", 64*1024)); err != nil {
		errs = append(errs, err)
	}
	for _, name := range []string{"residue.jsonl", "cas_residue.jsonl"} {
		trimmed, err := sf.TrimTail(filepath.Join(env.Home, name), sf.Int(env.Knobs, "residue_max_bytes", 256*1024))
		if err != nil {
			errs = append(errs, err)
		}
		c.Residue = c.Residue || trimmed
	}
	if c.Journal, err = sf.TrimTail(env.Path(JournalFile), sf.Int(env.Knobs, "journal_max_bytes", 64*1024)); err != nil {
		errs = append(errs, err)
	}
	return c, errors.Join(errs...)
}

// Report is one organism tick.
type Report struct {
	Dreamed      bool          `json:"dreamed"`
	Dream        *dream.Result `json:"dream,omitempty"`
	BeliefsAdded int           `json:"beliefs_added"`
	Compact      Compacted     `json:"compact"`
	Idle         string        `json:"idle"`
	Changed      bool          `json:"changed"`
}

// ChangedMap is the learn-log "changed" field: only what really changed.
func (r Report) ChangedMap() map[string]any {
	out := map[string]any{}
	if r.Dreamed {
		out["dreamed"] = true
		if r.Dream != nil {
			out["dream"] = map[string]any{
				"top": r.Dream.Top(), "threads": r.Dream.PromotedThreads, "pledges": r.Dream.PledgesAdded,
				"habits": r.Dream.HabitsAdded, "dreams": r.Dream.FutureDreams, "beliefs": r.BeliefsAdded,
			}
		}
	}
	if r.Compact.Any() {
		out["compacted"] = r.Compact
	}
	if r.Idle != "" {
		out["idle"] = r.Idle
	}
	return out
}

// OrganismTick is one unattended learning tick: dream when due, compact,
// drift the drives. Changed is true only when a dream ran or a store was
// trimmed (a cooldown skip is not a change), so the learn clock backs off.
// Every sub-step runs even when another fails; errors are joined.
func OrganismTick(env soul.Env) (Report, error) {
	var r Report
	var errs []error
	f, err := soul.Load(env)
	if err != nil {
		errs = append(errs, err)
	}
	if f != nil && len(f.Episodes) >= 4 {
		if due, err := dream.ShouldDream(env); err != nil {
			errs = append(errs, err)
		} else if due {
			res, beliefs, err := Dream(env, false)
			if err != nil {
				errs = append(errs, err)
			}
			if !res.Skipped && res.Field != nil {
				r.Dreamed, r.Dream, r.BeliefsAdded = true, &res, beliefs
			}
		}
	}
	if r.Compact, err = Compact(env); err != nil {
		errs = append(errs, err)
	}
	in := TickInput{Heavy: r.Dreamed, Dreamed: r.Dreamed}
	if f != nil && f.Relational.LastUserTS > 0 {
		h := (sf.Unix(env.Now()) - f.Relational.LastUserTS) / 3600
		in.HoursSinceUser = &h
	}
	tick, err := Tick(env, in)
	if err != nil {
		errs = append(errs, err)
	}
	r.Idle = tick.Idle
	r.Changed = r.Dreamed || r.Compact.Any()
	return r, errors.Join(errs...)
}
