package httpapi

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

const (
	hiveCadenceForager = "forager"
	hiveCadencePost    = "post"

	hiveStatusPending   = "pending"
	hiveStatusRunning   = "running"
	hiveStatusReported  = "reported"
	hiveStatusAsleep    = "asleep"
	hiveStatusBlocked   = "blocked"
	hiveStatusRetired   = "retired"
	hiveStatusCancelled = "cancelled"

	hiveDefaultBudgetSteps = 8
	hiveMaxBudgetSteps     = 16
	hiveDefaultPosts       = 4
	hiveDefaultLivePulses  = 8
	hiveMinPulseS          = 30
	hiveDefaultPostPulseS  = 120
	hiveRosterCap          = 40
)

type hivePacket struct {
	Goal           string   `json:"goal,omitempty"`
	Done           bool     `json:"done,omitempty"`
	Outcome        string   `json:"outcome,omitempty"`
	Evidence       []string `json:"evidence,omitempty"`
	Artifacts      []string `json:"artifacts,omitempty"`
	Blockers       []string `json:"blockers,omitempty"`
	OpenQuestions  []string `json:"open_questions,omitempty"`
	Confidence     float64  `json:"confidence,omitempty"`
}

type hiveDaughter struct {
	ID              string         `json:"id"`
	Cadence         string         `json:"cadence"`
	Status          string         `json:"status"`
	Goal            string         `json:"goal"`
	SessionID       string         `json:"session_id"`
	ParentSessionID string         `json:"parent_session_id,omitempty"`
	BudgetSteps     int            `json:"budget_steps"`
	Packet          *hivePacket    `json:"packet,omitempty"`
	ProjectPath     string         `json:"project_path,omitempty"`
	ApprovalMode    string         `json:"approval_mode,omitempty"`
	CreatedAt       string         `json:"created_at"`
	UpdatedAt       string         `json:"updated_at"`
	PulseS          int            `json:"pulse_s"`
	NextPulseAt     string         `json:"next_pulse_at,omitempty"`
	Journal         map[string]any `json:"journal,omitempty"`
}

func (d hiveDaughter) rosterLine() map[string]any {
	done := false
	outcome := ""
	blockers := []string{}
	if d.Packet != nil {
		done = d.Packet.Done
		outcome = trimRunes(d.Packet.Outcome, 200)
		blockers = append([]string(nil), d.Packet.Blockers...)
	}
	pulseCount := 0
	if d.Journal != nil {
		switch v := d.Journal["pulse_count"].(type) {
		case float64:
			pulseCount = int(v)
		case int:
			pulseCount = v
		}
	}
	return map[string]any{
		"id":            d.ID,
		"cadence":       d.Cadence,
		"status":        d.Status,
		"goal":          trimRunes(d.Goal, 240),
		"done":          done,
		"outcome":       outcome,
		"blockers":      blockers,
		"updated_at":    d.UpdatedAt,
		"pulse_s":       d.PulseS,
		"pulse_count":   pulseCount,
		"next_pulse_at": d.NextPulseAt,
	}
}

type hiveStore struct {
	mu   sync.Mutex
	root string
}

func openHiveStore(home string) *hiveStore {
	home = strings.TrimSpace(home)
	if home == "" {
		return &hiveStore{}
	}
	root := filepath.Join(home, "hive")
	for _, sub := range []string{"episodes", "posts"} {
		_ = os.MkdirAll(filepath.Join(root, sub), 0o700)
	}
	return &hiveStore{root: root}
}

func (h *hiveStore) ready() bool {
	return h != nil && strings.TrimSpace(h.root) != ""
}

func hiveSessionID(id string) string {
	id = strings.TrimSpace(id)
	if id == "" {
		return "hive_"
	}
	if strings.HasPrefix(id, "hive_") {
		return id
	}
	return "hive_" + id
}

func hiveSubdir(cadence string) string {
	if cadence == hiveCadencePost {
		return "posts"
	}
	return "episodes"
}

func (h *hiveStore) pathFor(d hiveDaughter) string {
	return filepath.Join(h.root, hiveSubdir(d.Cadence), d.ID+".json")
}

func (h *hiveStore) pathID(id string) string {
	id = strings.TrimSpace(id)
	if id == "" || !h.ready() {
		return ""
	}
	for _, sub := range []string{"episodes", "posts"} {
		p := filepath.Join(h.root, sub, id+".json")
		if st, err := os.Stat(p); err == nil && !st.IsDir() {
			return p
		}
	}
	return ""
}

func (h *hiveStore) hire(goal, cadence, parentSID, projectPath, approvalMode string, budget, pulseS int) (hiveDaughter, error) {
	if !h.ready() {
		return hiveDaughter{}, errHiveHomeMissing
	}
	goal = trimRunes(strings.TrimSpace(goal), 800)
	cadence = strings.ToLower(strings.TrimSpace(cadence))
	if cadence != hiveCadencePost {
		cadence = hiveCadenceForager
	}
	if budget < 1 {
		budget = hiveDefaultBudgetSteps
	}
	if budget > hiveMaxBudgetSteps {
		budget = hiveMaxBudgetSteps
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	id := newHiveID()
	d := hiveDaughter{
		ID:              id,
		Cadence:         cadence,
		Status:          hiveStatusPending,
		Goal:            goal,
		SessionID:       hiveSessionID(id),
		ParentSessionID: strings.TrimSpace(parentSID),
		BudgetSteps:     budget,
		ProjectPath:     strings.TrimSpace(projectPath),
		ApprovalMode:    strings.TrimSpace(approvalMode),
		CreatedAt:       now,
		UpdatedAt:       now,
		PulseS:          pulseS,
		Journal:         map[string]any{},
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if err := h.writeLocked(d); err != nil {
		return hiveDaughter{}, err
	}
	return d, nil
}

func (h *hiveStore) save(d hiveDaughter) error {
	if !h.ready() {
		return errHiveHomeMissing
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	d.UpdatedAt = time.Now().UTC().Format(time.RFC3339Nano)
	return h.writeLocked(d)
}

func (h *hiveStore) writeLocked(d hiveDaughter) error {
	path := h.pathFor(d)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	raw, err := json.MarshalIndent(d, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o600); err != nil {
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return nil
}

func (h *hiveStore) get(id string) (hiveDaughter, bool, error) {
	if !h.ready() {
		return hiveDaughter{}, false, nil
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	path := h.pathID(id)
	if path == "" {
		return hiveDaughter{}, false, nil
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return hiveDaughter{}, false, err
	}
	var d hiveDaughter
	if err := json.Unmarshal(raw, &d); err != nil {
		return hiveDaughter{}, false, err
	}
	if d.Journal == nil {
		d.Journal = map[string]any{}
	}
	return d, true, nil
}

func (h *hiveStore) listAll() ([]hiveDaughter, error) {
	if !h.ready() {
		return nil, nil
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	out := make([]hiveDaughter, 0)
	for _, sub := range []string{"episodes", "posts"} {
		dir := filepath.Join(h.root, sub)
		entries, err := os.ReadDir(dir)
		if err != nil {
			if os.IsNotExist(err) {
				continue
			}
			return nil, err
		}
		for _, ent := range entries {
			if ent.IsDir() || !strings.HasSuffix(ent.Name(), ".json") {
				continue
			}
			raw, err := os.ReadFile(filepath.Join(dir, ent.Name()))
			if err != nil {
				continue
			}
			var d hiveDaughter
			if json.Unmarshal(raw, &d) != nil || strings.TrimSpace(d.ID) == "" {
				continue
			}
			if d.Journal == nil {
				d.Journal = map[string]any{}
			}
			out = append(out, d)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].UpdatedAt > out[j].UpdatedAt })
	return out, nil
}

func (h *hiveStore) livePosts() ([]hiveDaughter, error) {
	all, err := h.listAll()
	if err != nil {
		return nil, err
	}
	out := make([]hiveDaughter, 0)
	for _, d := range all {
		if d.Cadence == hiveCadencePost && d.Status != hiveStatusRetired && d.Status != hiveStatusCancelled {
			out = append(out, d)
		}
	}
	return out, nil
}

func (h *hiveStore) livePulses() ([]hiveDaughter, error) {
	all, err := h.listAll()
	if err != nil {
		return nil, err
	}
	out := make([]hiveDaughter, 0)
	for _, d := range all {
		if d.Cadence == hiveCadenceForager && (d.Status == hiveStatusPending || d.Status == hiveStatusRunning) {
			out = append(out, d)
		}
	}
	return out, nil
}

func newHiveID() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	return hex.EncodeToString(b[:])
}

func clampHivePulseS(pulseS int) int {
	if pulseS <= 0 {
		pulseS = hiveDefaultPostPulseS
	}
	if pulseS < hiveMinPulseS {
		return hiveMinPulseS
	}
	return pulseS
}

func trimRunes(s string, max int) string {
	if max <= 0 || s == "" {
		return s
	}
	r := []rune(s)
	if len(r) <= max {
		return s
	}
	return string(r[:max])
}
