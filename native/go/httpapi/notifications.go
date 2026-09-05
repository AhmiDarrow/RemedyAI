package httpapi

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const notificationsOutboxName = "notifications.json"
const notificationsOutboxCap = 200

var notificationsMu sync.Mutex

type notification struct {
	ID         string   `json:"id"`
	Text       string   `json:"text"`
	CreatedTS  float64  `json:"created_ts"`
	Source     string   `json:"source"`
	SourceRef  string   `json:"source_ref"`
	Importance string   `json:"importance"`
	Read       bool     `json:"read"`
	Channels   []string `json:"channels"`
}

type notificationsFile struct {
	Notifications []notification `json:"notifications"`
}

func notificationsPath(homeDir string) string {
	return filepath.Join(ResolveHomeDir(homeDir), notificationsOutboxName)
}

func readNotifications(homeDir string) []notification {
	path := notificationsPath(homeDir)
	var raw notificationsFile
	if err := readJSONFile(path, &raw); err != nil {
		return nil
	}
	out := make([]notification, 0, len(raw.Notifications))
	for _, n := range raw.Notifications {
		n = normalizeNotification(n)
		if n.Text == "" {
			continue
		}
		out = append(out, n)
	}
	return out
}

func writeNotifications(homeDir string, items []notification) error {
	if len(items) > notificationsOutboxCap {
		items = items[len(items)-notificationsOutboxCap:]
	}
	home := ResolveHomeDir(homeDir)
	if err := os.MkdirAll(home, 0o700); err != nil {
		return err
	}
	payload := notificationsFile{Notifications: items}
	return writeJSONAtomic(notificationsPath(homeDir), payload)
}

func normalizeNotification(n notification) notification {
	if strings.TrimSpace(n.ID) == "" {
		n.ID = "n" + strconv.FormatInt(time.Now().UnixMilli(), 16)
	}
	n.Text = trimRunes(strings.TrimSpace(n.Text), 500)
	if n.CreatedTS <= 0 {
		n.CreatedTS = float64(time.Now().UnixNano()) / 1e9
	}
	if strings.TrimSpace(n.Source) == "" {
		n.Source = "reminder"
	}
	n.SourceRef = trimRunes(strings.TrimSpace(n.SourceRef), 200)
	if strings.TrimSpace(n.Importance) == "" {
		n.Importance = "normal"
	}
	if n.Channels == nil {
		n.Channels = []string{}
	}
	return n
}

func listNotifications(homeDir string, unreadOnly bool, limit int) []notification {
	items := readNotifications(homeDir)
	if unreadOnly {
		filtered := make([]notification, 0, len(items))
		for _, n := range items {
			if !n.Read {
				filtered = append(filtered, n)
			}
		}
		items = filtered
	}
	sort.SliceStable(items, func(i, j int) bool {
		return items[i].CreatedTS > items[j].CreatedTS
	})
	if limit < 1 {
		limit = 1
	}
	if len(items) > limit {
		items = items[:limit]
	}
	return items
}

func unreadNotificationCount(homeDir string) int {
	n := 0
	for _, item := range readNotifications(homeDir) {
		if !item.Read {
			n++
		}
	}
	return n
}

func markNotificationsRead(homeDir string, ids []string, all bool) int {
	want := map[string]struct{}{}
	for _, id := range ids {
		id = strings.TrimSpace(id)
		if id != "" {
			want[id] = struct{}{}
		}
	}
	notificationsMu.Lock()
	defer notificationsMu.Unlock()
	items := readNotifications(homeDir)
	changed := 0
	for i := range items {
		if items[i].Read {
			continue
		}
		if all {
			items[i].Read = true
			changed++
			continue
		}
		if _, ok := want[items[i].ID]; ok {
			items[i].Read = true
			changed++
		}
	}
	if changed > 0 {
		_ = writeNotifications(homeDir, items)
	}
	return changed
}

func (s *Server) handleListNotifications(w http.ResponseWriter, r *http.Request) {
	unreadOnly := false
	switch strings.ToLower(strings.TrimSpace(r.URL.Query().Get("unread_only"))) {
	case "1", "true", "yes", "on":
		unreadOnly = true
	}
	limit := 50
	if raw := strings.TrimSpace(r.URL.Query().Get("limit")); raw != "" {
		if n, err := strconv.Atoi(raw); err == nil {
			limit = n
		}
	}
	items := listNotifications(s.homeDir, unreadOnly, limit)
	out := make([]map[string]any, 0, len(items))
	for _, n := range items {
		out = append(out, map[string]any{
			"id":          n.ID,
			"text":        n.Text,
			"created_ts":  n.CreatedTS,
			"source":      n.Source,
			"source_ref":  n.SourceRef,
			"importance":  n.Importance,
			"read":        n.Read,
			"channels":    n.Channels,
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"notifications": out,
		"unread":        unreadNotificationCount(s.homeDir),
		"count":         len(out),
	})
}

func (s *Server) handleMarkNotificationsRead(w http.ResponseWriter, r *http.Request) {
	defer r.Body.Close()
	var body struct {
		IDs []any `json:"ids"`
		All bool  `json:"all"`
	}
	_ = json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body)
	ids := make([]string, 0, len(body.IDs))
	for _, raw := range body.IDs {
		if raw == nil {
			continue
		}
		ids = append(ids, strings.TrimSpace(fmt.Sprint(raw)))
	}
	changed := markNotificationsRead(s.homeDir, ids, body.All)
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":     true,
		"marked": changed,
		"unread": unreadNotificationCount(s.homeDir),
	})
}
