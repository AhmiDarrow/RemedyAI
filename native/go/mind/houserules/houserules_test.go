package houserules

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/pelletier/go-toml/v2"
)

// The fixtures in testdata/ are recorded from the Python module by
// scripts/record_house_rules_fixtures.py. Each test replays one recorded
// sequence on a temp home and compares every result and every file after
// every step (parsed content, timestamps masked).

type recStep struct {
	Tool   string                     `json:"tool"`
	Input  map[string]any             `json:"input"`
	Result map[string]any             `json:"result"`
	Files  map[string]json.RawMessage `json:"files"`
}

type recCase struct {
	Name   string         `json:"name"`
	Config map[string]any `json:"config"`
	Steps  []recStep      `json:"steps"`
}

var (
	tsRe = regexp.MustCompile(`^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$`)
	hrRe = regexp.MustCompile(`^hr-\d+$`)
)

const fakeKey = "sk-FAKE-not-a-real-key"

// mask makes v comparable: JSON round trip, timestamps and pending ids
// replaced, the home path replaced. A null pending_id is dropped (see the
// package notes: the tool's output schema says string).
func mask(v any, home string) any {
	b, _ := json.Marshal(v)
	s := string(b)
	for _, form := range []string{home, filepath.ToSlash(home)} {
		enc, _ := json.Marshal(form)
		s = strings.ReplaceAll(s, string(enc[1:len(enc)-1]), "<home>")
	}
	var out any
	_ = json.Unmarshal([]byte(s), &out)
	var walk func(any) any
	walk = func(x any) any {
		switch t := x.(type) {
		case string:
			if tsRe.MatchString(t) {
				return "<ts>"
			}
			if hrRe.MatchString(t) {
				return "<hr>"
			}
		case []any:
			for i := range t {
				t[i] = walk(t[i])
			}
		case map[string]any:
			for k, e := range t {
				if k == "pending_id" && e == nil {
					delete(t, k)
					continue
				}
				t[k] = walk(e)
			}
		}
		return x
	}
	return walk(out)
}

func readFile(t *testing.T, home, name string) any {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(home, name))
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(data), fakeKey) {
		t.Fatalf("%s holds a secret", name)
	}
	switch {
	case strings.HasSuffix(name, ".toml"):
		var m map[string]any
		if err := toml.Unmarshal(data, &m); err != nil {
			t.Fatalf("%s does not parse: %v\n%s", name, err, data)
		}
		return m
	case strings.HasSuffix(name, ".jsonl"):
		var rows []any
		for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
			var m any
			if err := json.Unmarshal([]byte(line), &m); err != nil {
				t.Fatalf("audit line: %v", err)
			}
			rows = append(rows, m)
		}
		return rows
	}
	var m any
	if err := json.Unmarshal(data, &m); err != nil {
		t.Fatalf("%s does not parse: %v", name, err)
	}
	return m
}

// merge is what the host's config writer does with an Apply patch.
func merge(cfg, patch map[string]any) {
	for k, v := range patch {
		if sub, ok := v.(map[string]any); ok && k == "advanced" {
			adv, _ := cfg[k].(map[string]any)
			if adv == nil {
				adv = map[string]any{}
				cfg[k] = adv
			}
			for ak, av := range sub {
				adv[ak] = av
			}
			continue
		}
		cfg[k] = v
	}
}

var fixedNow = func() time.Time { return time.Date(2026, 11, 25, 12, 0, 0, 0, time.UTC) }

func TestReplayRecordedPython(t *testing.T) {
	paths, _ := filepath.Glob("testdata/*.json")
	if len(paths) == 0 {
		t.Fatal("no fixtures")
	}
	total := 0
	for _, p := range paths {
		raw, err := os.ReadFile(p)
		if err != nil {
			t.Fatal(err)
		}
		var rc recCase
		if err := json.Unmarshal(raw, &rc); err != nil {
			t.Fatalf("%s: %v", p, err)
		}
		t.Run(rc.Name, func(t *testing.T) {
			home := t.TempDir()
			if keep := os.Getenv("HOUSERULES_KEEP"); keep != "" {
				home = filepath.Join(keep, rc.Name)
				_ = os.MkdirAll(home, 0o755)
			}
			cfg := map[string]any{}
			if rc.Config != nil {
				cfg = mask2(rc.Config, home).(map[string]any)
			}
			env := Env{
				Home: home, Now: fixedNow, Config: cfg,
				Apply:       func(patch map[string]any) error { merge(cfg, patch); return nil },
				StoreSecret: func(provider, key string) error { return nil },
			}
			// HOUSERULES_UPDATE=1 records what the tool returns now, after a
			// deliberate change of behaviour. Read the diff before committing it.
			update := os.Getenv("HOUSERULES_UPDATE") != ""
			if update {
				defer func() {
					out, _ := json.MarshalIndent(rc, "", " ")
					_ = os.WriteFile(p, append(out, '\n'), 0o644)
				}()
			}
			for i, st := range rc.Steps {
				got, _ := Dispatch(env, st.Input)
				if update {
					rc.Steps[i].Result, _ = mask(got, home).(map[string]any)
					for name := range st.Files {
						var have any
						if name == "config.toml" {
							if len(cfg) > 0 {
								have = cfg
							}
						} else {
							have = readFile(t, home, name)
						}
						raw, _ := json.Marshal(mask(have, home))
						rc.Steps[i].Files[name] = raw
					}
					total++
					continue
				}
				if g, w := mask(got, home), mask(st.Result, home); !reflect.DeepEqual(g, w) {
					t.Fatalf("step %d %s %v: result differs\n got  %s\n want %s", i, st.Tool, st.Input, js(g), js(w))
				}
				for name, wantRaw := range st.Files {
					var want any
					_ = json.Unmarshal(wantRaw, &want)
					var have any
					if name == "config.toml" {
						if len(cfg) > 0 {
							have = cfg
						}
					} else {
						have = readFile(t, home, name)
					}
					if g, w := mask(have, home), mask(want, home); !reflect.DeepEqual(g, w) {
						t.Fatalf("step %d %s %v: %s differs\n got  %s\n want %s", i, st.Tool, st.Input, name, js(g), js(w))
					}
				}
				total++
			}
		})
	}
	t.Logf("%d recorded steps match", total)
}

func js(v any) string { b, _ := json.MarshalIndent(v, "", " "); return string(b) }

// mask2 puts the real home back for the recorded <home> placeholder.
func mask2(v any, home string) any {
	b, _ := json.Marshal(v)
	enc, _ := json.Marshal(filepath.ToSlash(home))
	s := strings.ReplaceAll(string(b), "<home>", string(enc[1:len(enc)-1]))
	var out any
	_ = json.Unmarshal([]byte(s), &out)
	return out
}

// Without Apply nothing may claim success and the rules stay unconfirmed.
func TestNoApplyIsHonest(t *testing.T) {
	home := t.TempDir()
	env := Env{Home: home, Now: fixedNow}
	for _, in := range []map[string]any{{"action": "skip_all"}, {"action": "confirm"}, {"action": "propose", "patch": map[string]any{"persona": "fun"}}} {
		got, err := Dispatch(env, in)
		if err == nil || got["ok"] != false || got["error"] != "house rules action failed" {
			t.Fatalf("%v: %v %v", in, got, err)
		}
	}
	rules := readFile(t, home, "house_rules.toml")
	if rules != nil && rules.(map[string]any)["status"] == "complete" {
		t.Fatal("rules marked complete without applying")
	}
	// Reads and draft edits still work.
	got, _ := Dispatch(env, map[string]any{"action": "record", "answers": map[string]any{"name": "Remy"}})
	if got["ok"] != true {
		t.Fatal(got)
	}
}

// A secret with no secure store is reported, never recorded as set.
func TestSecretWithoutStore(t *testing.T) {
	home := t.TempDir()
	env := Env{Home: home, Now: fixedNow}
	got, _ := Dispatch(env, map[string]any{"action": "record", "answers": map[string]any{"llm_api_key": fakeKey, "llm_model": "m"}})
	if got["ok"] != true || !reflect.DeepEqual(got["secrets_not_stored"], []any{"llm_api_key"}) {
		t.Fatalf("%v", got)
	}
	sess := readFile(t, home, "setup_session.json").(map[string]any)
	if s, _ := sess["secrets_set"].([]any); len(s) != 0 {
		t.Fatalf("secret recorded as stored: %v", sess)
	}
	_ = readFile(t, home, "house_rules_audit.jsonl") // fails if the key leaked
}

// The caller's Origin wins over a caller_origin in the input.
func TestEnvOriginWins(t *testing.T) {
	env := Env{Home: t.TempDir(), Now: fixedNow, Origin: "peer-1"}
	got, _ := Dispatch(env, map[string]any{"action": "record", "caller_origin": "desktop", "answers": map[string]any{"name": "X"}})
	if got["error"] != errPeers {
		t.Fatal(got)
	}
}
