package learn

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"
	"time"

	yaml "go.yaml.in/yaml/v4"
)

// Each testdata/*.json is recorded from the Python by
// scripts/record_learn_fixtures.py: turns in, outcome and every file out.

type fixtureStep struct {
	Tool       string  `json:"tool"`
	OK         bool    `json:"ok"`
	Result     string  `json:"result"`
	Error      string  `json:"error"`
	DurationMS float64 `json:"duration_ms"`
}

type fixtureTurn struct {
	Message string        `json:"message"`
	Session string        `json:"session"`
	Steps   []fixtureStep `json:"steps"`
	Aborted bool          `json:"aborted"`
	Skills  []string      `json:"skills"`
	Now     string        `json:"now"`
}

type fixtureExpect struct {
	Learned bool              `json:"learned"`
	Skill   string            `json:"skill"`
	Merged  bool              `json:"merged"`
	Reason  *string           `json:"reason"`
	Graded  map[string]bool   `json:"graded"`
	Files   map[string]string `json:"files"`
}

type fixture struct {
	Name   string          `json:"name"`
	Turns  []fixtureTurn   `json:"turns"`
	Expect []fixtureExpect `json:"expect"`
}

var (
	reUUID  = regexp.MustCompile(`[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}`)
	reFront = regexp.MustCompile(`(?s)^---\n(.*?)\n---\n\n(.*)$`)
)

// canon turns parsed YAML/JSON into comparable data: uuids masked (they are
// random), paths slash-normalised with the home folder masked.
func canon(v any, homes []string) any {
	switch t := v.(type) {
	case string:
		t = strings.ReplaceAll(t, `\`, "/")
		for _, h := range homes {
			t = strings.ReplaceAll(t, strings.ReplaceAll(h, `\`, "/"), "<home>")
		}
		return reUUID.ReplaceAllString(t, "<uuid>")
	case map[string]any:
		out := map[string]any{}
		for k, x := range t {
			out[k] = canon(x, homes)
		}
		return out
	case []any:
		out := make([]any, len(t))
		for i, x := range t {
			out[i] = canon(x, homes)
		}
		return out
	case int:
		return float64(t)
	case int64:
		return float64(t)
	}
	return v
}

func sortedTools(m any) {
	if d, ok := m.(map[string]any); ok {
		// Python builds tools from a set; order is not part of the format.
		if l, ok := d["tools"].([]any); ok {
			sort.Slice(l, func(i, j int) bool { return l[i].(string) < l[j].(string) })
		}
	}
}

func compareFile(t *testing.T, name, want, got string, homes []string) {
	t.Helper()
	if strings.HasSuffix(name, ".json") {
		var w, g any
		if err := json.Unmarshal([]byte(want), &w); err != nil {
			t.Fatalf("%s: bad fixture json: %v", name, err)
		}
		if err := json.Unmarshal([]byte(got), &g); err != nil {
			t.Fatalf("%s: bad output json: %v\n%s", name, err, got)
		}
		if !reflect.DeepEqual(canon(w, homes), canon(g, homes)) {
			t.Errorf("%s differs\nwant %s\ngot  %s", name, want, got)
		}
		return
	}
	wm, gm := reFront.FindStringSubmatch(want), reFront.FindStringSubmatch(got)
	if wm == nil || gm == nil {
		t.Fatalf("%s: no frontmatter\nwant %q\ngot  %q", name, want, got)
	}
	var wy, gy any
	if err := yaml.Unmarshal([]byte(wm[1]), &wy); err != nil {
		t.Fatalf("%s: fixture yaml: %v", name, err)
	}
	if err := yaml.Unmarshal([]byte(gm[1]), &gy); err != nil {
		t.Fatalf("%s: output yaml: %v\n%s", name, err, gm[1])
	}
	wy, gy = canon(wy, homes), canon(gy, homes)
	sortedTools(wy)
	sortedTools(gy)
	if !reflect.DeepEqual(wy, gy) {
		t.Errorf("%s frontmatter differs\nwant %v\ngot  %v", name, wy, gy)
	}
	if wm[2] != gm[2] {
		t.Errorf("%s body differs\nwant %q\ngot  %q", name, wm[2], gm[2])
	}
}

func readTree(t *testing.T, root string) map[string]string {
	t.Helper()
	files := map[string]string{}
	_ = filepath.WalkDir(root, func(p string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		b, err := os.ReadFile(p)
		if err != nil {
			t.Fatal(err)
		}
		rel, _ := filepath.Rel(root, p)
		files[filepath.ToSlash(rel)] = string(b)
		return nil
	})
	return files
}

func TestReplayPython(t *testing.T) {
	paths, _ := filepath.Glob("testdata/*.json")
	if len(paths) < 10 {
		t.Fatalf("expected the recorded fixtures, found %d", len(paths))
	}
	for _, p := range paths {
		raw, err := os.ReadFile(p)
		if err != nil {
			t.Fatal(err)
		}
		var fx fixture
		if err := json.Unmarshal(raw, &fx); err != nil {
			t.Fatalf("%s: %v", p, err)
		}
		t.Run(fx.Name, func(t *testing.T) {
			home := t.TempDir()
			homes := []string{home}
			if r, err := filepath.EvalSymlinks(home); err == nil {
				homes = append(homes, r)
			}
			for i, turn := range fx.Turns {
				now := time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC)
				if turn.Now != "" {
					if now, err = time.Parse(time.RFC3339Nano, turn.Now); err != nil {
						t.Fatal(err)
					}
				}
				session := turn.Session
				if session == "" {
					session = "sess"
				}
				var steps []Step
				for _, s := range turn.Steps {
					steps = append(steps, Step{s.Tool, s.OK, s.Result, s.Error, s.DurationMS})
				}
				got, err := FromTurn(Turn{Home: home, SessionID: session, Message: turn.Message, Steps: steps,
					Aborted: turn.Aborted, Skills: turn.Skills, Now: func() time.Time { return now }})
				if err != nil {
					t.Fatalf("turn %d: %v", i, err)
				}
				want := fx.Expect[i]
				if got.Learned != want.Learned || got.Skill != want.Skill || got.Merged != want.Merged {
					t.Errorf("turn %d: got learned=%v skill=%q merged=%v, want %v %q %v (%s)",
						i, got.Learned, got.Skill, got.Merged, want.Learned, want.Skill, want.Merged, got.Reason)
				}
				if want.Reason != nil && got.Reason != *want.Reason {
					t.Errorf("turn %d: reason %q, want %q", i, got.Reason, *want.Reason)
				}
				if len(got.Graded) != len(want.Graded) {
					t.Errorf("turn %d: graded %v, want %v", i, got.Graded, want.Graded)
				}
				for k, v := range want.Graded {
					if got.Graded[k] != v {
						t.Errorf("turn %d: graded %v, want %v", i, got.Graded, want.Graded)
					}
				}
				files := readTree(t, home)
				for name := range want.Files {
					if _, ok := files[name]; !ok {
						t.Errorf("turn %d: %s was not written", i, name)
					}
				}
				for name, text := range files {
					w, ok := want.Files[name]
					if !ok {
						t.Errorf("turn %d: unexpected file %s", i, name)
						continue
					}
					compareFile(t, name, w, text, homes)
				}
			}
		})
	}
}

func TestSecretsAndTitles(t *testing.T) {
	for _, s := range []string{
		"password is hunter2", "use sk-ant-api03-abcdefghijklmnop", "Authorization: Bearer abc",
		"ghp_" + strings.Repeat("a", 20), strings.Repeat("A1", 25),
	} {
		if !looksLikeSecret(s) {
			t.Errorf("%q should look like a secret", s)
		}
	}
	for _, s := range []string{"task-tracking-app", "risk-tolerance is low", "fix the build"} {
		if looksLikeSecret(s) {
			t.Errorf("%q should not look like a secret", s)
		}
	}
	if got := sanitizeTitleLine("Fix C:\\Users\\bob\\x and /etc/passwd now"); got != "Fix and now" {
		t.Errorf("title %q", got)
	}
	if got := safeLearnDescription("login with password=hunter2 please"); got != "login with password=[redacted] please" {
		t.Errorf("description %q", got)
	}
}

func TestNeverLearnsFromAbortedOrTinyTurns(t *testing.T) {
	home := t.TempDir()
	steps := []Step{{Tool: "file_read", OK: true}, {Tool: "file_edit", OK: true}}
	if o, err := FromTurn(Turn{Home: home, Steps: steps}); err != nil || o.Learned {
		t.Fatalf("tiny turn: %+v %v", o, err)
	}
	if o, err := FromTurn(Turn{Home: "", Steps: steps}); err != nil || o.Learned {
		t.Fatalf("no home: %+v %v", o, err)
	}
}
