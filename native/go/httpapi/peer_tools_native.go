package httpapi

// Native ports of the peer.list / peer.ask / peer.delegate / peer.status tools.
// Spec: _peer_* in src/remedy/runtime/rmdy_tool_worker.py plus
// src/remedy/interop/{runtime,orchestrate,a2a,peers,audit,redact,language}.py.
//
// Faithful to the Python: the outbound path is A2A SendMessage only (an
// "mcp" peer is contacted the same way), no bearer token is ever sent, and
// there is no outbound trust/consent/rate check.

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/google/uuid"
)

// peerTimeout mirrors A2AClient(timeout=30.0).
var peerTimeout = 30 * time.Second

const (
	peerAuditMaxBytes = 512 * 1024
	peerAuditMaxLines = 800
)

var (
	peerBearerRe  = regexp.MustCompile(`(?i)(Bearer)\s+\S+`)
	peerKeyRe     = regexp.MustCompile(`(?i)(sk-[A-Za-z0-9_-]{12,}|REMEDY_INTEROP_TOKEN=\S+)`)
	peerRilRe     = regexp.MustCompile(`(?i)^\s*ril1\b`)
	peerRilScrub  = regexp.MustCompile(`ril1\s+\S+`)
	peerTokenRe   = regexp.MustCompile(`(?i)[a-z0-9][a-z0-9+.#-]{1,32}`)
	peerSecretKey = map[string]bool{
		"authorization": true, "api_key": true, "apikey": true, "token": true,
		"access_token": true, "refresh_token": true, "secret": true,
		"password": true, "inbound_token": true, "bearer": true,
	}
)

// --- small Python-semantics helpers ---------------------------------------

func peerPyStr(v any) string {
	switch t := v.(type) {
	case nil:
		return ""
	case string:
		return t
	}
	return fmt.Sprint(v)
}

func peerTruthy(v any) bool {
	switch t := v.(type) {
	case nil:
		return false
	case bool:
		return t
	case string:
		return t != ""
	case float64:
		return t != 0
	case int:
		return t != 0
	case int64:
		return t != 0
	case []any:
		return len(t) > 0
	case map[string]any:
		return len(t) > 0
	}
	return true
}

// peerOr is Python's `a or b` followed by str().
func peerOrStr(vals ...any) string {
	for _, v := range vals {
		if peerTruthy(v) {
			return peerPyStr(v)
		}
	}
	return ""
}

func peerRedactText(s string) string {
	if s == "" {
		return s
	}
	return peerKeyRe.ReplaceAllString(peerBearerRe.ReplaceAllString(s, "${1} ***"), "***")
}

func peerRedact(v any) any {
	switch t := v.(type) {
	case string:
		return peerRedactText(t)
	case map[string]any:
		out := make(map[string]any, len(t))
		for k, item := range t {
			lk := strings.ToLower(k)
			if peerSecretKey[lk] || strings.HasSuffix(lk, "_token") {
				if peerTruthy(item) {
					out[k] = "***"
				} else {
					out[k] = item
				}
			} else {
				out[k] = peerRedact(item)
			}
		}
		return out
	case []any:
		out := make([]any, len(t))
		for i, item := range t {
			out[i] = peerRedact(item)
		}
		return out
	}
	return v
}

// peerForPeer is language.for_peer minus the inner-language decoder, which Go
// does not have: ril1 text takes the Python's own fail-open scrub branch.
func peerForPeer(text string) (string, map[string]any) {
	raw := strings.TrimSpace(text)
	if raw == "" {
		return "", nil
	}
	head := raw
	if r := []rune(raw); len(r) > 200 {
		head = string(r[:200])
	}
	if !peerRilRe.MatchString(raw) && !strings.Contains(head, "ril1 ") {
		if raw[0] != '{' && raw[0] != '[' {
			return raw, nil
		}
		var v any
		if json.Unmarshal([]byte(raw), &v) != nil {
			return raw, nil
		}
		if m, ok := v.(map[string]any); ok {
			return raw, m
		}
		return raw, map[string]any{"value": v}
	}
	return peerRilScrub.ReplaceAllString(raw, "[inner note]"), nil
}

func peerExtractText(msg map[string]any) string {
	if msg == nil {
		return ""
	}
	var chunks []string
	if parts, ok := msg["parts"].([]any); ok {
		for _, p := range parts {
			part, ok := p.(map[string]any)
			if !ok {
				continue
			}
			if _, has := part["text"]; has || part["kind"] == "text" {
				chunks = append(chunks, peerPyStr(part["text"]))
			}
		}
	}
	if len(chunks) == 0 && peerTruthy(msg["text"]) {
		chunks = append(chunks, peerPyStr(msg["text"]))
	}
	english, _ := peerForPeer(strings.TrimSpace(strings.Join(chunks, "\n")))
	return english
}

// --- store paths ----------------------------------------------------------

func peerDir(s *Server, input map[string]any) string {
	home := strings.TrimSpace(peerPyStr(input["home_dir"]))
	if home == "" {
		home = s.homeDir
	}
	dir := filepath.Join(ResolveHomeDir(home), "interop")
	_ = os.MkdirAll(dir, 0o700)
	return dir
}

func peerHome(s *Server, input map[string]any) string {
	if h := strings.TrimSpace(peerPyStr(input["home_dir"])); h != "" {
		return h
	}
	return s.homeDir
}

// peerStore is peers.load: any read or shape problem is an empty store.
func peerStore(dir string) map[string]any {
	raw, err := os.ReadFile(filepath.Join(dir, "peers.json"))
	if err != nil {
		return map[string]any{}
	}
	var data map[string]any
	if json.Unmarshal(raw, &data) != nil {
		return map[string]any{}
	}
	peers, _ := data["peers"].(map[string]any)
	if peers == nil {
		return map[string]any{}
	}
	return peers
}

// peerList is peers.list_peers: dict entries only, sorted by name or id.
func peerList(dir string, enabledOnly bool) []map[string]any {
	var out []map[string]any
	for _, item := range peerStore(dir) {
		p, ok := item.(map[string]any)
		if !ok {
			continue
		}
		if enabledOnly {
			if v, has := p["enabled"]; has && !peerTruthy(v) {
				continue
			}
		}
		out = append(out, p)
	}
	sort.SliceStable(out, func(i, j int) bool {
		return peerOrStr(out[i]["name"], out[i]["id"]) < peerOrStr(out[j]["name"], out[j]["id"])
	})
	return out
}

// --- audit ----------------------------------------------------------------

func peerAudit(s *Server, input map[string]any, direction, protocol string, peerID any, action, summary string, extra map[string]any) {
	cfg := LoadConfig(peerHome(s, input))
	if !coerceBool(resolveAdvanced(advancedByKey()["interop_audit"], cfg), true) {
		return
	}
	path := filepath.Join(peerDir(s, input), "audit.jsonl")
	entry := map[string]any{
		"ts":        float64(time.Now().UnixNano()) / 1e9,
		"direction": direction,
		"protocol":  protocol,
		"peer_id":   peerID,
		"action":    action,
		"summary":   peerRedactText(summary),
	}
	if len(extra) > 0 {
		entry["extra"] = peerRedact(extra)
	}
	line, err := json.Marshal(entry)
	if err != nil {
		return
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return
	}
	_, werr := f.Write(append(line, '\n'))
	_ = f.Close()
	if werr == nil {
		peerAuditRotate(path)
	}
}

func peerAuditRotate(path string) {
	st, err := os.Stat(path)
	if err != nil || st.Size() <= peerAuditMaxBytes {
		return
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return
	}
	var lines []string
	for _, l := range strings.Split(string(raw), "\n") {
		if strings.TrimSpace(l) != "" {
			lines = append(lines, l)
		}
	}
	if len(lines) <= peerAuditMaxLines/2 {
		return
	}
	keep := lines[len(lines)/2:]
	if len(lines) > peerAuditMaxLines {
		keep = lines[len(lines)-peerAuditMaxLines:]
	}
	tmp := path + ".tmp"
	if os.WriteFile(tmp, []byte(strings.Join(keep, "\n")+"\n"), 0o600) == nil {
		_ = os.Rename(tmp, path)
	}
}

// peerAuditEndpoint drops userinfo and query so a credential in a peer URL
// never reaches the log (the Python logs the URL verbatim).
func peerAuditEndpoint(endpoint any) any {
	s, ok := endpoint.(string)
	if !ok {
		return endpoint
	}
	u, err := url.Parse(s)
	if err != nil || u.Host == "" {
		return peerRedactText(s)
	}
	u.User, u.RawQuery, u.Fragment = nil, "", ""
	return u.String()
}

// --- outbound A2A ---------------------------------------------------------

func peerRPCSend(ctx context.Context, endpoint, text string, metadata map[string]any) (map[string]any, error) {
	english, structured := peerForPeer(text)
	parts := []any{map[string]any{"text": english}}
	if structured != nil {
		parts = append(parts, map[string]any{"data": structured})
	}
	message := map[string]any{"messageId": uuid.NewString(), "role": "user", "parts": parts}
	if len(metadata) > 0 {
		message["metadata"] = metadata
	}
	payload := map[string]any{
		"jsonrpc": "2.0",
		"id":      uuid.NewString(),
		"method":  "SendMessage",
		"params": map[string]any{
			"message":       message,
			"configuration": map[string]any{"return_immediately": true},
		},
	}
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(payload); err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(ctx, peerTimeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, &buf)
	if err != nil {
		return nil, fmt.Errorf("A2A request failed: %s", peerRedactText(err.Error()))
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	req.Header.Set("A2A-Version", a2aProtocolVersion)
	client := &http.Client{
		Timeout: peerTimeout,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	resp, err := client.Do(req)
	if err != nil {
		var ue *url.Error
		if errors.As(err, &ue) {
			if ue.Timeout() {
				return nil, errors.New("A2A request failed: timed out")
			}
			err = ue.Err
		}
		return nil, fmt.Errorf("A2A request failed: %s", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("A2A HTTP %d", resp.StatusCode)
	}
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, errors.New("timed out")
	}
	var body map[string]any
	if json.Unmarshal(raw, &body) != nil || body == nil {
		return nil, errors.New("invalid JSON-RPC response")
	}
	if e := body["error"]; peerTruthy(e) {
		if em, ok := e.(map[string]any); ok {
			return nil, errors.New(peerOrStr(em["message"], em))
		}
		return nil, errors.New(peerPyStr(e))
	}
	if r, ok := body["result"].(map[string]any); ok {
		return r, nil
	}
	return body, nil
}

// peerDelegate is orchestrate.delegate_to_peer.
func peerDelegate(ctx context.Context, peer map[string]any, text string) map[string]any {
	peerID := peerPyStr(peer["id"])
	endpoint := strings.TrimSpace(peerPyStr(peer["endpoint"]))
	if endpoint == "" {
		return map[string]any{"peer_id": peerID, "ok": false, "error": "peer has no endpoint"}
	}
	result, err := peerRPCSend(ctx, endpoint, text, map[string]any{"hop": 1, "from": "remedy"})
	if err != nil {
		return map[string]any{"peer_id": peerID, "ok": false, "error": err.Error()}
	}
	out := ""
	var taskID any
	if task, ok := result["task"].(map[string]any); ok {
		taskID = task["id"]
		out = peerStatusText(task)
	} else if msg, ok := result["message"].(map[string]any); ok {
		out = peerExtractText(msg)
	} else if peerTruthy(result["id"]) && peerTruthy(result["status"]) {
		taskID = result["id"]
		out = peerStatusText(result)
	} else {
		out = peerExtractText(result)
		if out == "" {
			out = peerPyStr(result["text"])
		}
	}
	return map[string]any{"peer_id": peerID, "ok": true, "text": out, "task_id": taskID}
}

func peerStatusText(task map[string]any) string {
	st, _ := task["status"].(map[string]any)
	msg, _ := st["message"].(map[string]any)
	return peerExtractText(msg)
}

// peerMerge is orchestrate.merge_results.
func peerMerge(results []map[string]any) (string, []any) {
	var lines []string
	parts := []any{}
	for _, item := range results {
		peerID := peerOrStr(item["peer_id"], item["name"])
		if peerID == "" {
			peerID = "peer"
		}
		if e := item["error"]; peerTruthy(e) {
			lines = append(lines, fmt.Sprintf("%s: error — %s", peerID, peerPyStr(e)))
			parts = append(parts, map[string]any{"peer_id": peerID, "ok": false, "error": peerPyStr(e)})
			continue
		}
		english, structured := peerForPeer(strings.TrimSpace(peerPyStr(item["text"])))
		if english != "" {
			lines = append(lines, peerID+": "+english)
		} else {
			lines = append(lines, peerID+": (empty)")
		}
		entry := map[string]any{"peer_id": peerID, "ok": true, "text": english}
		if structured != nil {
			entry["data"] = structured
		}
		if peerTruthy(item["task_id"]) {
			entry["task_id"] = item["task_id"]
		}
		parts = append(parts, entry)
	}
	return strings.TrimSpace(strings.Join(lines, "\n")), parts
}

// --- peer picking (orchestrate.pick_peers / score_peer) -------------------

func peerTokenize(text string) map[string]bool {
	out := map[string]bool{}
	for _, m := range peerTokenRe.FindAllString(text, -1) {
		out[strings.ToLower(m)] = true
	}
	return out
}

func peerScore(peer map[string]any, query string) float64 {
	list, _ := peer["skills"].([]any)
	var skills []string
	for _, s := range list {
		skills = append(skills, strings.ToLower(peerPyStr(s)))
	}
	if len(skills) == 0 {
		return 0
	}
	q := peerTokenize(query)
	if len(q) == 0 {
		return 0
	}
	skillTokens := map[string]bool{}
	for _, s := range skills {
		for t := range peerTokenize(s) {
			skillTokens[t] = true
		}
		skillTokens[s] = true
	}
	overlap := 0
	for t := range q {
		if skillTokens[t] {
			overlap++
		}
	}
	ql := strings.ToLower(query)
	if overlap == 0 {
		hits := 0
		for _, s := range skills {
			if s != "" && strings.Contains(ql, s) {
				hits++
			}
		}
		return float64(hits) * 0.5
	}
	score := float64(overlap)
	for _, s := range skills {
		if strings.Contains(ql, s) { // empty skill matches, as in Python
			return score + 0.25
		}
	}
	return score
}

func peerPick(dir, query string, limit int) []map[string]any {
	type ranked struct {
		score float64
		peer  map[string]any
	}
	var rs []ranked
	for _, p := range peerList(dir, true) {
		rs = append(rs, ranked{peerScore(p, query), p})
	}
	sort.SliceStable(rs, func(i, j int) bool {
		if rs[i].score != rs[j].score {
			return rs[i].score > rs[j].score
		}
		return peerOrStr(rs[i].peer["name"]) < peerOrStr(rs[j].peer["name"])
	})
	var out []map[string]any
	for _, r := range rs {
		if r.score > 0 && len(out) < limit {
			out = append(out, r.peer)
		}
	}
	return out
}

// --- tools ----------------------------------------------------------------

func toolPeerList(_ context.Context, s *Server, input map[string]any) (map[string]any, error) {
	public := []any{}
	for _, p := range peerList(peerDir(s, input), false) {
		skills := p["skills"]
		if !peerTruthy(skills) {
			skills = []any{}
		}
		enabled, has := p["enabled"]
		if !has {
			enabled = true
		}
		public = append(public, map[string]any{
			"id": p["id"], "name": p["name"], "protocol": p["protocol"],
			"skills": skills, "enabled": enabled,
		})
	}
	return map[string]any{"ok": true, "peers": public, "total": len(public)}, nil
}

func peerDispatch(ctx context.Context, s *Server, input map[string]any, field string) (map[string]any, error) {
	text := strings.TrimSpace(peerOrStr(input[field], input["question"], input["task"]))
	if text == "" {
		return map[string]any{"ok": false, "error": field + " is required", "results": []any{}, "summary": ""}, nil
	}
	dir := peerDir(s, input)
	if peerID := strings.TrimSpace(peerPyStr(input["peer_id"])); peerID != "" {
		peer, _ := peerStore(dir)[peerID].(map[string]any)
		if peer == nil {
			return map[string]any{"ok": false, "error": "unknown peer", "results": []any{}, "summary": ""}, nil
		}
		// The Python contacted a named peer even when the owner had turned
		// it off, and wrote no audit line for it. A disabled peer is never
		// contacted, and every outbound message is audited.
		if v, has := peer["enabled"]; has && !peerTruthy(v) {
			return map[string]any{"ok": false, "error": "peer is disabled", "results": []any{}, "summary": ""}, nil
		}
		english, _ := peerForPeer(text)
		one := peerDelegate(ctx, peer, english)
		peerAudit(s, input, "out", "a2a", peer["id"], "delegate", english,
			map[string]any{"endpoint": peerAuditEndpoint(peer["endpoint"])})
		summary, parts := peerMerge([]map[string]any{one})
		ok, _ := one["ok"].(bool)
		return map[string]any{"ok": ok, "summary": summary, "results": parts}, nil
	}
	// rt.orchestrate: pick enabled peers by skill overlap, up to 3.
	english, _ := peerForPeer(text)
	chosen := peerPick(dir, english, 3)
	if len(chosen) == 0 {
		return map[string]any{"ok": false, "error": "no matching peer", "results": []any{}, "summary": ""}, nil
	}
	var results []map[string]any
	for _, p := range chosen {
		results = append(results, peerDelegate(ctx, p, english))
		peerAudit(s, input, "out", "a2a", p["id"], "delegate", english,
			map[string]any{"endpoint": peerAuditEndpoint(p["endpoint"])})
	}
	summary, parts := peerMerge(results)
	return map[string]any{"ok": true, "summary": summary, "results": parts}, nil
}

func toolPeerAsk(ctx context.Context, s *Server, input map[string]any) (map[string]any, error) {
	return peerDispatch(ctx, s, input, "question")
}

func toolPeerDelegate(ctx context.Context, s *Server, input map[string]any) (map[string]any, error) {
	return peerDispatch(ctx, s, input, "task")
}

// toolPeerStatus reads only the local {home}/interop/tasks.json (inbound tasks);
// the Python never records tasks it delegated, nor asks the peer.
func toolPeerStatus(_ context.Context, s *Server, input map[string]any) (map[string]any, error) {
	tid := strings.TrimSpace(peerPyStr(input["task_id"]))
	if tid == "" {
		return map[string]any{"ok": false, "error": "task_id is required"}, nil
	}
	var task map[string]any
	if raw, err := os.ReadFile(filepath.Join(peerDir(s, input), "tasks.json")); err == nil {
		var wrap struct {
			Tasks map[string]any `json:"tasks"`
		}
		if json.Unmarshal(raw, &wrap) == nil {
			task, _ = wrap.Tasks[tid].(map[string]any)
		}
	}
	if task == nil {
		return map[string]any{"ok": false, "error": "unknown task"}, nil
	}
	peerAudit(s, input, "in", "mcp", nil, "task_status", tid, nil)
	return map[string]any{"ok": true, "task": task}, nil
}
