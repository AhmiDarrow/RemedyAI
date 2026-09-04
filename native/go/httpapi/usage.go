package httpapi

import (
	"database/sql"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	_ "modernc.org/sqlite"
)

const usageEventsSchema = `
CREATE TABLE IF NOT EXISTS usage_events (
    id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    session_id TEXT,
    provider TEXT,
    model TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'estimate',
    run_id TEXT,
    turn_index INTEGER,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_events(ts);
CREATE INDEX IF NOT EXISTS idx_usage_session ON usage_events(session_id);
CREATE INDEX IF NOT EXISTS idx_usage_provider ON usage_events(provider);
`

func usageDBPath(homeDir string) string {
	home := ResolveHomeDir(homeDir)
	if home == "" {
		return ""
	}
	return filepath.Join(home, "usage.db")
}

func openUsageDB(homeDir string) (*sql.DB, error) {
	home := ResolveHomeDir(homeDir)
	if home == "" {
		return nil, fmt.Errorf("remedy home required")
	}
	if err := os.MkdirAll(home, 0o700); err != nil {
		return nil, err
	}
	path := filepath.Join(home, "usage.db")
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	if _, err := db.Exec(usageEventsSchema); err != nil {
		_ = db.Close()
		return nil, err
	}
	_, _ = db.Exec("PRAGMA journal_mode=WAL")
	_, _ = db.Exec("PRAGMA synchronous=NORMAL")
	_, _ = db.Exec("PRAGMA busy_timeout=5000")
	return db, nil
}

func parseRangeDays(raw string, def float64) float64 {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return def
	}
	v, err := strconv.ParseFloat(raw, 64)
	if err != nil {
		return def
	}
	if v < 0.01 {
		v = 0.01
	}
	if v > 3650 {
		v = 3650
	}
	return v
}

func (s *Server) handleUsageSummary(w http.ResponseWriter, r *http.Request) {
	rangeDays := parseRangeDays(r.URL.Query().Get("range_days"), 7)
	sessionID := strings.TrimSpace(r.URL.Query().Get("session_id"))
	out, err := usageSummary(s.homeDir, rangeDays, sessionID)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) handleUsageSeries(w http.ResponseWriter, r *http.Request) {
	rangeDays := parseRangeDays(r.URL.Query().Get("range_days"), 30)
	group := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("group")))
	if group != "model" {
		group = "provider"
	}
	out, err := usageSeries(s.homeDir, rangeDays, group)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) handleUsageExport(w http.ResponseWriter, r *http.Request) {
	rangeDays := parseRangeDays(r.URL.Query().Get("range_days"), 30)
	format := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("format")))
	if format == "" {
		format = "csv"
	}
	if format == "json" {
		summ, err := usageSummary(s.homeDir, rangeDays, "")
		if err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
			return
		}
		ser, err := usageSeries(s.homeDir, rangeDays, "provider")
		if err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"summary": summ, "series": ser})
		return
	}
	ser, err := usageSeries(s.homeDir, rangeDays, "provider")
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	var b strings.Builder
	b.WriteString("day,provider,total_tokens,estimated_cost_usd,events\n")
	switch points := ser["points"].(type) {
	case []map[string]any:
		for _, p := range points {
			b.WriteString(fmt.Sprintf("%v,%v,%v,%v,%v\n",
				p["day"], p["group"], p["total_tokens"], p["estimated_cost_usd"], p["events"]))
		}
	case []any:
		for _, raw := range points {
			if p, ok := asStringMap(raw); ok {
				b.WriteString(fmt.Sprintf("%v,%v,%v,%v,%v\n",
					p["day"], p["group"], p["total_tokens"], p["estimated_cost_usd"], p["events"]))
			}
		}
	}
	filename := fmt.Sprintf("remedy-usage-%dd.csv", int(rangeDays))
	w.Header().Set("Content-Type", "text/csv")
	w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s"`, filename))
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(b.String()))
}

func usageSummary(homeDir string, rangeDays float64, sessionID string) (map[string]any, error) {
	db, err := openUsageDB(homeDir)
	if err != nil {
		return nil, err
	}
	defer db.Close()

	since := time.Now().Unix() - int64(maxFloat(0.01, rangeDays)*86400)
	where := "ts >= ?"
	args := []any{float64(since)}
	if sessionID != "" {
		where += " AND session_id = ?"
		args = append(args, sessionID)
	}

	rows, err := db.Query(`
		SELECT provider, model,
		       SUM(prompt_tokens), SUM(completion_tokens),
		       SUM(total_tokens), SUM(estimated_cost_usd), COUNT(*)
		FROM usage_events
		WHERE `+where+`
		GROUP BY provider, model
		ORDER BY SUM(total_tokens) DESC`, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	byProvider := map[string]map[string]any{}
	byModel := make([]map[string]any, 0)
	for rows.Next() {
		var provider, model sql.NullString
		var pt, ct, tt, n sql.NullInt64
		var cost sql.NullFloat64
		if err := rows.Scan(&provider, &model, &pt, &ct, &tt, &cost, &n); err != nil {
			return nil, err
		}
		prov := "unknown"
		if provider.Valid && strings.TrimSpace(provider.String) != "" {
			prov = provider.String
		}
		mod := "unknown"
		if model.Valid && strings.TrimSpace(model.String) != "" {
			mod = model.String
		}
		entry := map[string]any{
			"provider":           prov,
			"model":              mod,
			"prompt_tokens":      int(pt.Int64),
			"completion_tokens":  int(ct.Int64),
			"total_tokens":       int(tt.Int64),
			"estimated_cost_usd": round6(cost.Float64),
			"events":             int(n.Int64),
		}
		byModel = append(byModel, entry)
		bucket := byProvider[prov]
		if bucket == nil {
			bucket = map[string]any{
				"provider":           prov,
				"prompt_tokens":      0,
				"completion_tokens":  0,
				"total_tokens":       0,
				"estimated_cost_usd": 0.0,
				"events":             0,
			}
			byProvider[prov] = bucket
		}
		bucket["prompt_tokens"] = bucket["prompt_tokens"].(int) + entry["prompt_tokens"].(int)
		bucket["completion_tokens"] = bucket["completion_tokens"].(int) + entry["completion_tokens"].(int)
		bucket["total_tokens"] = bucket["total_tokens"].(int) + entry["total_tokens"].(int)
		bucket["estimated_cost_usd"] = round6(bucket["estimated_cost_usd"].(float64) + entry["estimated_cost_usd"].(float64))
		bucket["events"] = bucket["events"].(int) + entry["events"].(int)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}

	var tpt, tct, ttt, tn sql.NullInt64
	var tcost sql.NullFloat64
	err = db.QueryRow(`
		SELECT SUM(prompt_tokens), SUM(completion_tokens),
		       SUM(total_tokens), SUM(estimated_cost_usd), COUNT(*)
		FROM usage_events WHERE `+where, args...).Scan(&tpt, &tct, &ttt, &tcost, &tn)
	if err != nil {
		return nil, err
	}

	provList := make([]map[string]any, 0, len(byProvider))
	for _, v := range byProvider {
		provList = append(provList, v)
	}
	var session any
	if sessionID != "" {
		session = sessionID
	}
	return map[string]any{
		"range_days":  rangeDays,
		"session_id":  session,
		"totals": map[string]any{
			"prompt_tokens":      int(tpt.Int64),
			"completion_tokens":  int(tct.Int64),
			"total_tokens":       int(ttt.Int64),
			"estimated_cost_usd": round6(tcost.Float64),
			"events":             int(tn.Int64),
		},
		"by_provider": provList,
		"by_model":    byModel,
	}, nil
}

func usageSeries(homeDir string, rangeDays float64, group string) (map[string]any, error) {
	db, err := openUsageDB(homeDir)
	if err != nil {
		return nil, err
	}
	defer db.Close()

	groupCol := "provider"
	if group == "model" {
		groupCol = "model"
	}
	since := time.Now().Unix() - int64(maxFloat(0.01, rangeDays)*86400)
	q := fmt.Sprintf(`
		SELECT date(ts, 'unixepoch', 'localtime') AS day,
		       COALESCE(%s, 'unknown') AS grp,
		       SUM(total_tokens), SUM(estimated_cost_usd), COUNT(*)
		FROM usage_events
		WHERE ts >= ?
		GROUP BY day, grp
		ORDER BY day ASC`, groupCol)
	rows, err := db.Query(q, float64(since))
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	points := make([]map[string]any, 0)
	for rows.Next() {
		var day, grp string
		var tt, n sql.NullInt64
		var cost sql.NullFloat64
		if err := rows.Scan(&day, &grp, &tt, &cost, &n); err != nil {
			return nil, err
		}
		points = append(points, map[string]any{
			"day":                day,
			"group":              grp,
			"total_tokens":       int(tt.Int64),
			"estimated_cost_usd": round6(cost.Float64),
			"events":             int(n.Int64),
		})
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return map[string]any{
		"range_days": rangeDays,
		"group":      groupCol,
		"points":     points,
	}, nil
}

func maxFloat(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

func round6(v float64) float64 {
	return float64(int64(v*1e6+0.5)) / 1e6
}

// seedUsageEvent inserts one ledger row (tests).
func seedUsageEvent(homeDir string, ts float64, sessionID, provider, model string, prompt, completion, total int, cost float64) error {
	db, err := openUsageDB(homeDir)
	if err != nil {
		return err
	}
	defer db.Close()
	id := fmt.Sprintf("evt-%d-%s-%s", int64(ts*1000), provider, model)
	_, err = db.Exec(`
		INSERT INTO usage_events (
			id, ts, session_id, provider, model,
			prompt_tokens, completion_tokens, total_tokens,
			estimated_cost_usd, source, meta
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'estimate', '{}')`,
		id, ts, nullIfEmpty(sessionID), nullIfEmpty(provider), nullIfEmpty(model),
		prompt, completion, total, cost,
	)
	return err
}
