package httpapi

import (
	"bufio"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

const selfInjectLedgerName = "self_inject_ledger.jsonl"

func selfInjectLedgerPath(homeDir string) string {
	return filepath.Join(ResolveHomeDir(homeDir), selfInjectLedgerName)
}

func readSelfInjectLedger(homeDir string) []map[string]any {
	path := selfInjectLedgerPath(homeDir)
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	out := make([]map[string]any, 0)
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var row map[string]any
		if json.Unmarshal([]byte(line), &row) != nil || row == nil {
			continue
		}
		out = append(out, row)
	}
	return out
}

func (s *Server) serveStartedUTC() string {
	t := s.started
	if t.IsZero() {
		t = time.Now()
	}
	return t.UTC().Format("2006-01-02T15:04:05-07:00")
}

func (s *Server) selfInjectLiveState(r map[string]any) string {
	if anyString(r["status"]) != "applied" {
		return ""
	}
	if anyString(r["tree"]) == "desktop" {
		return "live"
	}
	finished := anyString(r["finished_utc"])
	detail, _ := r["detail"].(map[string]any)
	requested := true
	if detail != nil {
		if v, ok := detail["sidecar_restart_requested"]; ok {
			requested = anyBoolDef(v, true)
		}
	}
	started := s.serveStartedUTC()
	if finished != "" && finished <= started {
		return "live"
	}
	if requested {
		return "awaiting_restart"
	}
	return "not_loaded"
}

func (s *Server) handleSelfInjectRounds(w http.ResponseWriter, r *http.Request) {
	limit := 20
	if raw := strings.TrimSpace(r.URL.Query().Get("limit")); raw != "" {
		if n, err := strconv.Atoi(raw); err == nil {
			limit = n
		}
	}
	if limit < 1 {
		limit = 1
	}
	if limit > 100 {
		limit = 100
	}
	all := readSelfInjectLedger(s.homeDir)
	// newest first, take last N then reverse
	if len(all) > limit {
		all = all[len(all)-limit:]
	}
	rounds := make([]map[string]any, 0, len(all))
	for i := len(all) - 1; i >= 0; i-- {
		row := all[i]
		clone := cloneMap(row)
		delete(clone, "diff")
		clone["live_state"] = s.selfInjectLiveState(row)
		rounds = append(rounds, clone)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"serve_started_utc": s.serveStartedUTC(),
		"rounds":            rounds,
	})
}
