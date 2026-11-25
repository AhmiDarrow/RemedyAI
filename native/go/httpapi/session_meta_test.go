package httpapi

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestSessionFilingIsSharedThroughTheAPI(t *testing.T) {
	home := t.TempDir()
	const token = "session-meta-token-xx!"
	base, shutdown := startTestServer(t, Config{Token: token, HomeDir: home})
	defer shutdown()
	client := &http.Client{Timeout: 3 * time.Second}
	call := func(method, path, body string) (int, map[string]any, []map[string]any) {
		req, err := http.NewRequest(method, base+path, strings.NewReader(body))
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Authorization", "Bearer "+token)
		req.Header.Set("Content-Type", "application/json")
		resp, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		var raw json.RawMessage
		_ = json.NewDecoder(resp.Body).Decode(&raw)
		var obj map[string]any
		var list []map[string]any
		_ = json.Unmarshal(raw, &obj)
		_ = json.Unmarshal(raw, &list)
		if list == nil && obj != nil {
			if rows, ok := obj["sessions"].([]any); ok {
				for _, r := range rows {
					if m, ok := r.(map[string]any); ok {
						list = append(list, m)
					}
				}
			}
		}
		return resp.StatusCode, obj, list
	}
	code, made, _ := call(http.MethodPost, "/api/sessions", `{"title":"Port"}`)
	id, _ := made["id"].(string)
	if code != http.StatusOK || id == "" {
		t.Fatalf("create: %d %v", code, made)
	}
	row := func() map[string]any {
		_, _, list := call(http.MethodGet, "/api/sessions?limit=50", "")
		for _, r := range list {
			if r["id"] == id {
				return r
			}
		}
		t.Fatalf("session %s not in the list", id)
		return nil
	}
	// Unfiled: false, false, and an empty list (never null).
	if r := row(); r["pinned"] != false || r["archived"] != false || r["tags"] == nil {
		t.Fatalf("unfiled row: %v", r)
	}
	code, patched, _ := call(http.MethodPatch, "/api/sessions/"+id, `{"pinned":true,"tags":[" ui ","Portal","ui",""]}`)
	if code != http.StatusOK || patched["pinned"] != true {
		t.Fatalf("patch: %d %v", code, patched)
	}
	r := row()
	tags, _ := r["tags"].([]any)
	if r["pinned"] != true || r["archived"] != false || len(tags) != 2 || tags[0] != "Portal" || tags[1] != "ui" {
		t.Fatalf("after pin and tags: %v", r)
	}
	// A patch that names one field leaves the others, and a rename leaves them all.
	call(http.MethodPatch, "/api/sessions/"+id, `{"archived":true}`)
	call(http.MethodPatch, "/api/sessions/"+id, `{"title":"Port to RemedyUI"}`)
	_, one, _ := call(http.MethodGet, "/api/sessions/"+id, "")
	oneTags, _ := one["tags"].([]any)
	if one["pinned"] != true || one["archived"] != true || len(oneTags) != 2 || one["title"] != "Port to RemedyUI" {
		t.Fatalf("after archive and rename: %v", one)
	}
	if code, _, _ := call(http.MethodDelete, "/api/sessions/"+id, ""); code != http.StatusOK {
		t.Fatalf("delete: %d", code)
	}
	// A new session that happens to reuse nothing must not inherit old filing.
	_, again, _ := call(http.MethodPost, "/api/sessions", `{"title":"Fresh"}`)
	fresh, _ := again["id"].(string)
	_, got, _ := call(http.MethodGet, "/api/sessions/"+fresh, "")
	if got["pinned"] != false || got["archived"] != false {
		t.Fatalf("fresh session: %v", got)
	}
}
