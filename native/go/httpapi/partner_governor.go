package httpapi

import (
	"fmt"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/providers"
	"github.com/AhmiDarrow/RemedyAI/native/go/sysres"
)

// The governor decides how much background work this PC can carry right now.
// Remedy reads the same numbers (children.list, the heartbeat prompt) so she
// plans inside them from the start instead of the owner having to rein her in.
//
// Rules, in order:
//  1. Low memory pauses new background work entirely. Running work finishes.
//  2. A local model (llama.cpp, Ollama, RMB) serves one turn well at a time:
//     one background slot, and none while the owner is mid-turn.
//  3. A cloud model is limited by this machine's tools (browser, shell,
//     builds): a quarter of the logical CPUs, between 1 and 3 slots.

const (
	partnerMinFreeBytes    = 2 << 30 // 2 GiB
	partnerMinFreeFraction = 0.12
	partnerMaxSlots        = 3
)

type partnerCapacity struct {
	Slots       int    `json:"slots"`
	Busy        int    `json:"busy"`
	Queued      int    `json:"queued"`
	LocalModel  bool   `json:"local_model"`
	OwnerBusy   bool   `json:"owner_busy"`
	MemoryKnown bool   `json:"memory_known"`
	FreeMemPct  int    `json:"free_mem_pct"`
	FreeMemGB   string `json:"free_mem_gb,omitempty"`
	CPUs        int    `json:"cpus"`
	Paused      bool   `json:"paused"`
	Reason      string `json:"reason"`
}

// Free reports how many more background runs may start now.
func (c partnerCapacity) Free() int {
	if c.Paused {
		return 0
	}
	if n := c.Slots - c.Busy; n > 0 {
		return n
	}
	return 0
}

// Sentence is the plain-language line Remedy and the owner both see.
func (c partnerCapacity) Sentence() string {
	if c.Paused {
		return c.Reason
	}
	free := c.Free()
	switch {
	case free == 0:
		return fmt.Sprintf("All %d background slot(s) are in use; new work waits its turn. %s", c.Slots, c.Reason)
	default:
		return fmt.Sprintf("%d of %d background slot(s) free. %s", free, c.Slots, c.Reason)
	}
}

// computePartnerCapacity is the pure rule set (tested directly).
func computePartnerCapacity(snap sysres.Snapshot, localModel, ownerBusy bool, busy, queued int) partnerCapacity {
	c := partnerCapacity{
		Busy:        busy,
		Queued:      queued,
		LocalModel:  localModel,
		OwnerBusy:   ownerBusy,
		MemoryKnown: snap.Known(),
		CPUs:        snap.LogicalCPUs,
		FreeMemPct:  100,
	}
	if snap.Known() {
		c.FreeMemPct = int(snap.AvailableFraction()*100 + 0.5)
		c.FreeMemGB = fmt.Sprintf("%.1f", float64(snap.AvailableBytes)/float64(1<<30))
	}
	slots := snap.LogicalCPUs / 4
	if slots < 1 {
		slots = 1
	}
	if slots > partnerMaxSlots {
		slots = partnerMaxSlots
	}
	c.Reason = "Sized to this PC's " + fmt.Sprint(snap.LogicalCPUs) + " CPU threads."
	if localModel {
		slots = 1
		c.Reason = "The model runs on this PC, so one job at a time."
		if ownerBusy {
			c.Paused = true
			c.Reason = "Waiting while you talk to Remedy — the local model is busy with you."
		}
	}
	c.Slots = slots
	if snap.Known() && (snap.AvailableBytes < partnerMinFreeBytes || snap.AvailableFraction() < partnerMinFreeFraction) {
		c.Paused = true
		c.Reason = fmt.Sprintf("Paused: only %s GB of memory is free. Background work resumes when the PC has room.", c.FreeMemGB)
	}
	return c
}

// partnerUsesLocalModel reports whether the active model runs on this PC.
func (s *Server) partnerUsesLocalModel() bool {
	cfg := LoadConfig(s.homeDir)
	switch strings.ToLower(strings.TrimSpace(cfgString(cfg, "llm_provider", ""))) {
	case "ollama", "rmb", "llamacpp", "local":
		return true
	}
	if base := strings.TrimSpace(cfgString(cfg, "llm_base_url", "")); base != "" {
		return providers.IsLocalBaseURL(base)
	}
	return false
}

// partnerOwnerBusy reports a live turn the owner started (any claimed
// session that is not running background work).
func (s *Server) partnerOwnerBusy() bool {
	if s.claims == nil {
		return false
	}
	ids := s.claims.ActiveSessionIDs()
	if len(ids) == 0 {
		return false
	}
	h := s.partner
	if h == nil {
		return true
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	for _, id := range ids {
		if _, mine := h.running[id]; !mine {
			return true
		}
	}
	return false
}

func (s *Server) partnerCapacity() partnerCapacity {
	h := s.partner
	if h == nil {
		return partnerCapacity{Paused: true, Reason: "Background work is not available."}
	}
	local := s.partnerUsesLocalModel()
	ownerBusy := s.partnerOwnerBusy()
	h.mu.Lock()
	busy, queued := len(h.running), len(h.queue)
	read := h.readRes
	h.mu.Unlock()
	return computePartnerCapacity(read(), local, ownerBusy, busy, queued)
}
