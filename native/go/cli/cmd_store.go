package cli

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"

	"github.com/AhmiDarrow/RemedyAI/native/go/httpapi"
)

// The store commands (`remedy memory|user|session|handoff`) work on
// <home>/memory.db through httpapi.CLIStore, which reads and writes it the
// way Python's MemoryStore does. Like Python they open the store directly:
// no running runtime is needed.

// storePanel prints what Python drew as a Rich Panel: a titled block.
func storePanel(c *Ctx, title, body string) {
	c.Printf("── %s ──\n%s\n\n", title, body)
}

// storeCut is text[:n] plus "..." when it was longer.
func storeCut(s string, n int) string {
	if utf8.RuneCountInString(s) > n {
		return clip(s, n) + "..."
	}
	return s
}

// storeTags is [t.strip() for t in tags.split(",") if t.strip()].
func storeTags(raw string) []string {
	out := []string{}
	for _, t := range strings.Split(raw, ",") {
		if t = strings.TrimSpace(t); t != "" {
			out = append(out, t)
		}
	}
	return out
}

func storeOpen(c *Ctx) (*httpapi.CLIStore, bool) {
	s, err := httpapi.OpenCLIStore(c.DBPath())
	if err != nil {
		c.Errorf("Error: cannot open memory store %s: %v\n", c.DBPath(), err)
		return nil, false
	}
	return s, true
}

// storeFail reports an error the Python CLI raised as an exception (exit 1).
func storeFail(c *Ctx, err error) int {
	c.Errorf("Error: %v\n", err)
	return 1
}

func storePrintEntries(c *Ctx, entries []*httpapi.CLIMemoryEntry) {
	if len(entries) == 0 {
		c.Println("No entries found.")
		return
	}
	for _, e := range entries {
		storePanel(c, "Memory Entry", fmt.Sprintf("%s\n%s\n\nID: %s | Type: %s | Importance: %.1f | %s",
			e.Title, storeCut(e.Content, 200), e.ID, e.EntryType, e.Importance, e.CreatedISO()))
	}
}

func storePrintHandoffs(c *Ctx, notes []*httpapi.CLIHandoffNote) {
	if len(notes) == 0 {
		c.Println("No handoff notes found.")
		return
	}
	for _, h := range notes {
		ack := "pending"
		if h.Acknowledged {
			ack = "acknowledged"
		}
		storePanel(c, "Handoff Note", fmt.Sprintf("%s\n%s\n\nID: %s | %s | %s",
			h.Title, storeCut(h.Content, 300), h.ID, ack, h.CreatedISO()))
	}
}

func cmdMemory(c *Ctx) int {
	s, ok := storeOpen(c)
	if !ok {
		return 1
	}
	defer s.Close()
	switch c.P.Sub(1) {
	case "search":
		entries, err := s.Search(c.P.Str("query"), c.P.Int("limit"))
		if err != nil {
			return storeFail(c, err)
		}
		storePrintEntries(c, entries)
	case "list":
		var (
			entries []*httpapi.CLIMemoryEntry
			err     error
		)
		if t := c.P.Str("type"); t != "" {
			entries, err = s.ListByType(t, c.P.Int("limit"))
		} else {
			entries, err = s.ListRecent(c.P.Int("limit"))
		}
		if err != nil {
			return storeFail(c, err)
		}
		storePrintEntries(c, entries)
	case "add":
		e := httpapi.NewCLIMemoryEntry(c.P.Str("type"), c.P.Str("title"), c.P.Str("content"),
			storeTags(c.P.Str("tags")), c.P.Float("importance"))
		if err := e.Validate(); err != nil {
			return storeFail(c, err)
		}
		if err := s.Upsert(e); err != nil {
			return storeFail(c, err)
		}
		c.Printf("Memory entry saved: %s\n", e.ID)
	case "consolidate":
		sid := c.P.Str("session_id")
		res, err := s.Consolidate(sid, c.P.Int("max-entries"))
		if err != nil {
			return storeFail(c, err)
		}
		if res == nil {
			c.Println("Not enough entries to consolidate.")
			return 0
		}
		c.Printf("Consolidated session %s: %s\n", sid, res.ID)
	case "repair":
		info, err := s.CheckIntegrity()
		if err != nil {
			return storeFail(c, err)
		}
		c.Println("Memory Store Integrity")
		for _, it := range info {
			c.Printf("  %s: %s\n", it.Key, it.Text())
		}
		if c.P.Bool("vacuum") {
			_, _, reclaimed, err := s.Vacuum()
			if err != nil {
				return storeFail(c, err)
			}
			c.Printf("\nVacuumed: reclaimed %d bytes\n", reclaimed)
		}
	case "backup":
		path, err := s.Backup()
		if err != nil {
			return storeFail(c, err)
		}
		c.Printf("Backup created: %s\n", path)
	}
	return 0
}

func cmdUser(c *Ctx) int {
	s, ok := storeOpen(c)
	if !ok {
		return 1
	}
	defer s.Close()
	switch c.P.Sub(1) {
	case "show":
		p, err := s.Profile()
		if err != nil {
			return storeFail(c, err)
		}
		name := p.DisplayName
		if name == "" {
			name = p.UserID
		}
		var b strings.Builder
		fmt.Fprintf(&b, "User: %s\nSessions: %s\nActive since: %s\nLast active: %s\n\nTraits:\n", name, p.SessionsText, p.CreatedISO, p.LastISO)
		traits := make([]string, len(p.Traits))
		for i, t := range p.Traits {
			traits[i] = fmt.Sprintf("  %s: %s (confidence: %.1f)", t.Key, t.Value, t.Confidence)
		}
		b.WriteString(strings.Join(traits, "\n"))
		fmt.Fprintf(&b, "\n\nFacts (%d):\n", len(p.Facts))
		shown := p.Facts
		if len(shown) > 10 {
			shown = shown[:10]
		}
		facts := make([]string, len(shown))
		for i, f := range shown {
			facts[i] = fmt.Sprintf("  [%s] %s", f.Category, f.Fact)
		}
		b.WriteString(strings.Join(facts, "\n"))
		storePanel(c, "User Profile", b.String())
		if len(p.Facts) > 10 {
			c.Printf("  ... and %d more facts\n", len(p.Facts)-10)
		}
	case "facts":
		facts, err := s.SearchUserFacts(c.P.Str("query"), c.P.Int("limit"))
		if err != nil {
			return storeFail(c, err)
		}
		if len(facts) == 0 {
			c.Println("No facts found.")
			return 0
		}
		for _, f := range facts {
			c.Printf("  [%s] %s (ref: %d)\n", f.Category, f.Fact, f.ReferenceCount)
		}
	}
	return 0
}

// storeSessionMarker is <home>/cli_active_session, next to memory.db.
func storeSessionMarker(c *Ctx) string {
	return filepath.Join(filepath.Dir(c.DBPath()), "cli_active_session")
}

func cmdSession(c *Ctx) int {
	s, ok := storeOpen(c)
	if !ok {
		return 1
	}
	defer s.Close()
	marker := storeSessionMarker(c)
	switch c.P.Sub(1) {
	case "start":
		sid, pending, err := s.StartSession()
		if err != nil {
			return storeFail(c, err)
		}
		if err := os.WriteFile(marker, []byte(sid+"\n"), 0o600); err != nil {
			return storeFail(c, err)
		}
		c.Printf("Session started: %s\n", sid)
		if len(pending) > 0 {
			c.Printf("%d pending handoff(s) from previous sessions:\n", len(pending))
			for _, h := range pending {
				c.Printf("  %s: %s...\n", h.Title, clip(h.Content, 80))
			}
		}
	case "end":
		sid := ""
		if st, err := os.Stat(marker); err == nil && st.Mode().IsRegular() {
			raw, err := os.ReadFile(marker)
			if err != nil {
				return storeFail(c, err)
			}
			sid = strings.TrimSpace(string(raw))
		}
		if sid == "" {
			c.Println("No active session to end.")
			return 0
		}
		h, err := s.EndSession(sid)
		if err != nil {
			return storeFail(c, err)
		}
		_ = os.Remove(marker) // Python suppresses OSError here
		c.Printf("Session ended. Handoff created: %s\n", h.ID)
		storePanel(c, "Auto-Handoff", h.Title+"\n"+clip(h.Content, 300))
	}
	return 0
}

func cmdHandoff(c *Ctx) int {
	s, ok := storeOpen(c)
	if !ok {
		return 1
	}
	defer s.Close()
	switch c.P.Sub(1) {
	case "create":
		h := httpapi.NewCLIHandoffNote(c.P.Str("title"), c.P.Str("content"), storeTags(c.P.Str("tags")))
		if err := s.CreateHandoff(h); err != nil {
			return storeFail(c, err)
		}
		c.Printf("Handoff created: %s\n", h.ID)
	case "list":
		notes, err := s.ListHandoffs(c.P.Int("limit"))
		if err != nil {
			return storeFail(c, err)
		}
		storePrintHandoffs(c, notes)
	case "search":
		notes, err := s.RelevantHandoffs(c.P.Str("query"), c.P.Int("limit"))
		if err != nil {
			return storeFail(c, err)
		}
		storePrintHandoffs(c, notes)
	case "show":
		id := c.P.Str("id")
		h, err := s.GetHandoff(id)
		if err != nil {
			return storeFail(c, err)
		}
		if h == nil {
			c.Errorf("Handoff not found: %s\n", id)
			return 1
		}
		c.Println(h.JSON())
	}
	return 0
}
