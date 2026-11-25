package httpapi

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
)

const (
	a2aProtocolVersion     = "1.0"
	interopWellKnownPath   = "/.well-known/agent-card.json"
	interopWellKnownLegacy = "/.well-known/agent.json"
	interopTokenEnv        = "REMEDY_INTEROP_TOKEN"
	interopTokenFile       = "inbound_token"
)

var a2aMethods = map[string]string{
	"SendMessage":                        "SendMessage",
	"message/send":                       "SendMessage",
	"SendStreamingMessage":               "SendStreamingMessage",
	"message/stream":                     "SendStreamingMessage",
	"GetTask":                            "GetTask",
	"tasks/get":                          "GetTask",
	"ListTasks":                          "ListTasks",
	"tasks/list":                         "ListTasks",
	"CancelTask":                         "CancelTask",
	"tasks/cancel":                       "CancelTask",
	"GetExtendedAgentCard":               "GetExtendedAgentCard",
	"agent/getAuthenticatedExtendedCard": "GetExtendedAgentCard",
	"SubscribeToTask":                    "SubscribeToTask",
	"tasks/resubscribe":                  "SubscribeToTask",
}

var interopMu sync.Mutex

func (s *Server) mountInteropRoutes() {
	s.mux.HandleFunc("GET /.well-known/agent-card.json", s.handleInteropAgentCard)
	s.mux.HandleFunc("GET /.well-known/agent.json", s.handleInteropAgentCard)
	s.mux.HandleFunc("GET /extendedAgentCard", s.handleInteropExtendedCard)
	s.mux.HandleFunc("POST /a2a", s.handleInteropA2A)
	s.mux.HandleFunc("POST /a2a/v1", s.handleInteropA2A)
	s.mux.HandleFunc("POST /mcp", s.handleInteropMCP)
	s.mux.HandleFunc("POST /interop/turn", s.handleInteropTurn)
}

func (s *Server) interopCfg() ConfigMap {
	return LoadConfig(s.homeDir)
}

func (s *Server) interopInboundOn() bool {
	return coerceBool(resolveAdvanced(advancedByKey()["interop_inbound"], s.interopCfg()), false)
}

func (s *Server) interopA2AOn() bool {
	return coerceBool(resolveAdvanced(advancedByKey()["interop_a2a"], s.interopCfg()), true)
}

func (s *Server) interopMCPOn() bool {
	return coerceBool(resolveAdvanced(advancedByKey()["interop_mcp"], s.interopCfg()), true)
}

func (s *Server) interopBind() string {
	v := strings.ToLower(fmt.Sprint(resolveAdvanced(advancedByKey()["interop_bind"], s.interopCfg())))
	switch v {
	case "localhost", "loopback", "off", "":
		return "127.0.0.1"
	default:
		return v
	}
}

func interopLoopbackBind(bind string) bool {
	switch strings.ToLower(strings.TrimSpace(bind)) {
	case "127.0.0.1", "localhost", "::1", "loopback", "off", "":
		return true
	}
	return false
}

func (s *Server) interopDir() string {
	dir := filepath.Join(ResolveHomeDir(s.homeDir), "interop")
	_ = os.MkdirAll(dir, 0o700)
	return dir
}

func (s *Server) interopToken() string {
	if env := strings.TrimSpace(os.Getenv(interopTokenEnv)); env != "" {
		return env
	}
	raw, err := os.ReadFile(filepath.Join(s.interopDir(), interopTokenFile))
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(raw))
}

func (s *Server) ensureInteropToken() string {
	if tok := s.interopToken(); tok != "" {
		return tok
	}
	tok := uuid.NewString() + uuid.NewString()
	path := filepath.Join(s.interopDir(), interopTokenFile)
	_ = os.WriteFile(path, []byte(tok), 0o600)
	return tok
}

func bearerMatches(header, want string) bool {
	got := strings.TrimSpace(header)
	if strings.HasPrefix(strings.ToLower(got), "bearer ") {
		got = strings.TrimSpace(got[7:])
	}
	if want == "" || got == "" || len(got) != len(want) {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(got), []byte(want)) == 1
}

func loopbackAddr(r *http.Request) bool {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

func originOK(origin, bind string) bool {
	if strings.TrimSpace(origin) == "" {
		return true
	}
	// Very small allowlist: loopback hosts when bound to loopback.
	o := strings.ToLower(origin)
	if interopLoopbackBind(bind) {
		return strings.Contains(o, "127.0.0.1") || strings.Contains(o, "localhost") || strings.Contains(o, "[::1]")
	}
	return strings.Contains(o, "127.0.0.1") || strings.Contains(o, "localhost") || strings.Contains(o, strings.ToLower(bind))
}

// interopGate returns an HTTP status and message when the request should be
// refused. 0 means continue.
func (s *Server) interopGate(w http.ResponseWriter, r *http.Request, kind string, publicCard bool) bool {
	if !s.interopInboundOn() {
		http.Error(w, `{"error":{"message":"inbound interop is off"}}`, http.StatusNotFound)
		return false
	}
	if kind == "a2a" && !s.interopA2AOn() {
		http.Error(w, `{"error":{"message":"A2A is disabled"}}`, http.StatusNotFound)
		return false
	}
	if kind == "mcp" && !s.interopMCPOn() {
		http.Error(w, `{"error":{"message":"MCP is disabled"}}`, http.StatusNotFound)
		return false
	}
	bind := s.interopBind()
	if interopLoopbackBind(bind) && !loopbackAddr(r) {
		http.Error(w, `{"error":{"message":"loopback only"}}`, http.StatusForbidden)
		return false
	}
	if !originOK(r.Header.Get("Origin"), bind) {
		http.Error(w, `{"error":{"message":"invalid Origin"}}`, http.StatusForbidden)
		return false
	}
	if publicCard && interopLoopbackBind(bind) {
		return true
	}
	if !bearerMatches(r.Header.Get("Authorization"), s.interopToken()) {
		w.Header().Set("WWW-Authenticate", "Bearer")
		http.Error(w, `{"error":{"message":"missing or invalid bearer token"}}`, http.StatusUnauthorized)
		return false
	}
	return true
}

func (s *Server) interopPublicURL() string {
	port := s.apiListenPort
	if port == 0 {
		port = 7400
	}
	return fmt.Sprintf("http://127.0.0.1:%d/a2a", port)
}

func (s *Server) interopAgentCard(extended bool) map[string]any {
	version := s.version
	if version == "" {
		version = Version
	}
	desc := "A partner on this computer. She finishes the goal you set and waits for the owner before money, send, or delete. She talks with other bots as a peer over A2A and MCP — never as a muscle they run."
	if extended {
		desc += " Authenticated peers may ask questions, delegate tasks, share context, and poll task status, subject to per-peer permissions and the owner's consent policy."
	}
	skills := []map[string]any{
		{"id": "ask", "name": "Ask Remedy", "description": "Ask Remedy a question in plain language. She answers as a peer.", "tags": []string{"ask", "chat", "help"}, "examples": []string{"What did we decide about the Friday deploy?"}, "inputModes": []string{"text/plain", "application/json"}, "outputModes": []string{"text/plain", "application/json"}},
		{"id": "delegate", "name": "Delegate a task", "description": "Hand Remedy a task to track. Money, sending messages, and deleting things still need the owner.", "tags": []string{"task", "delegate", "orchestrate"}, "examples": []string{"Summarize yesterday's notes and list open questions."}, "inputModes": []string{"text/plain", "application/json"}, "outputModes": []string{"text/plain", "application/json"}},
		{"id": "share_context", "name": "Share context", "description": "Share structured context Remedy may use for a task. Not a grant of new permissions.", "tags": []string{"context", "share"}, "examples": []string{"Here is the current sprint goal and the three open PRs."}, "inputModes": []string{"text/plain", "application/json"}, "outputModes": []string{"text/plain", "application/json"}},
	}
	return map[string]any{
		"name":        "Remedy",
		"description": desc,
		"version":     version,
		"supportedInterfaces": []map[string]any{
			{"url": s.interopPublicURL(), "protocolBinding": "JSONRPC", "protocolVersion": a2aProtocolVersion},
		},
		"capabilities": map[string]any{
			"streaming":         true,
			"pushNotifications": false,
			"extendedAgentCard": true,
		},
		"securitySchemes": map[string]any{
			"bearer": map[string]any{
				"httpAuthSecurityScheme": map[string]any{"scheme": "bearer"},
			},
		},
		"securityRequirements": []map[string]any{{"bearer": []any{}}},
		"defaultInputModes":    []string{"text/plain", "application/json"},
		"defaultOutputModes":   []string{"text/plain", "application/json"},
		"skills":               skills,
		"provider":             map[string]any{"organization": "RemedyAI", "url": "https://github.com/AhmiDarrow/RemedyAI"},
		"documentationUrl":     "https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/AGENT_INTEROP.md",
	}
}

func (s *Server) handleInteropAgentCard(w http.ResponseWriter, r *http.Request) {
	if !s.interopGate(w, r, "a2a", true) {
		return
	}
	writeJSON(w, http.StatusOK, s.interopAgentCard(false))
}

func (s *Server) handleInteropExtendedCard(w http.ResponseWriter, r *http.Request) {
	if !s.interopGate(w, r, "a2a", false) {
		return
	}
	writeJSON(w, http.StatusOK, s.interopAgentCard(true))
}

func (s *Server) handleInteropA2A(w http.ResponseWriter, r *http.Request) {
	if !s.interopGate(w, r, "a2a", false) {
		return
	}
	raw, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		writeJSON(w, http.StatusOK, rpcErr(nil, -32700, "Parse error"))
		return
	}
	var body map[string]any
	if err := json.Unmarshal(raw, &body); err != nil {
		writeJSON(w, http.StatusOK, rpcErr(nil, -32700, "Parse error"))
		return
	}
	id := body["id"]
	method, _ := body["method"].(string)
	canon := a2aMethods[method]
	params, _ := body["params"].(map[string]any)
	if params == nil {
		params = map[string]any{}
	}
	ver := strings.TrimSpace(r.Header.Get("A2A-Version"))
	if ver == "" {
		if v, ok := params["A2A-Version"].(string); ok {
			ver = v
		}
	}
	if ver != "" && !strings.HasPrefix(ver, "0.") && !strings.HasPrefix(ver, "1.") {
		writeJSON(w, http.StatusOK, rpcErr(id, -32009, "VersionNotSupportedError: "+ver))
		return
	}
	if canon == "" {
		writeJSON(w, http.StatusOK, rpcErr(id, -32601, "Method not found: "+method))
		return
	}
	accept := r.Header.Get("Accept")
	wantStream := (canon == "SendStreamingMessage" || canon == "SubscribeToTask") && strings.Contains(accept, "text/event-stream")

	switch canon {
	case "SendMessage", "SendStreamingMessage":
		task, err := s.interopRecordInbound("a2a", params)
		if err != nil {
			writeJSON(w, http.StatusOK, rpcErr(id, -32602, err.Error()))
			return
		}
		if wantStream {
			s.interopStreamLive(w, r, id, "a2a", task)
			return
		}
		if s.interopShouldLive(task) {
			if interopBlocking(params) {
				task = s.interopRunLive(r.Context(), "a2a", task, nil)
			} else {
				go s.interopRunLive(context.Background(), "a2a", task, nil)
				if fresh := s.interopGetTask(stringVal(task, "id")); fresh != nil {
					task = fresh
				}
			}
		}
		writeJSON(w, http.StatusOK, map[string]any{"jsonrpc": "2.0", "id": id, "result": map[string]any{"task": task}})
		return
	case "SubscribeToTask":
		tid := stringVal(params, "id", "taskId")
		if tid == "" {
			writeJSON(w, http.StatusOK, rpcErr(id, -32602, "id is required"))
			return
		}
		task := s.interopGetTask(tid)
		if task == nil {
			writeJSON(w, http.StatusOK, rpcErr(id, -32001, "TaskNotFoundError"))
			return
		}
		if wantStream {
			s.interopSubscribeStream(w, r, id, task)
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"jsonrpc": "2.0", "id": id, "result": task})
		return
	}

	result, code, msg := s.dispatchA2A(canon, params)
	if msg != "" {
		writeJSON(w, http.StatusOK, rpcErr(id, code, msg))
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"jsonrpc": "2.0", "id": id, "result": result})
}

func (s *Server) dispatchA2A(method string, params map[string]any) (any, int, string) {
	switch method {
	case "SendMessage", "SendStreamingMessage":
		task, err := s.interopRecordInbound("a2a", params)
		if err != nil {
			return nil, -32602, err.Error()
		}
		return map[string]any{"task": task}, 0, ""
	case "GetTask", "SubscribeToTask":
		tid := stringVal(params, "id", "taskId")
		if tid == "" {
			return nil, -32602, "id is required"
		}
		task := s.interopGetTask(tid)
		if task == nil {
			return nil, -32001, "TaskNotFoundError"
		}
		return task, 0, ""
	case "ListTasks":
		page, token := s.interopListTasks(params)
		out := map[string]any{"tasks": page}
		if token != "" {
			out["nextPageToken"] = token
		}
		return out, 0, ""
	case "CancelTask":
		tid := stringVal(params, "id", "taskId")
		if tid == "" {
			return nil, -32602, "id is required"
		}
		task, err := s.interopCancelTask(tid)
		if err == "not_found" {
			return nil, -32001, "TaskNotFoundError"
		}
		if err == "not_cancelable" {
			return nil, -32002, "TaskNotCancelableError"
		}
		return task, 0, ""
	case "GetExtendedAgentCard":
		return s.interopAgentCard(true), 0, ""
	}
	return nil, -32601, "Method not found"
}

func (s *Server) handleInteropMCP(w http.ResponseWriter, r *http.Request) {
	if !s.interopGate(w, r, "mcp", false) {
		return
	}
	raw, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, rpcErr(nil, -32700, "Parse error"))
		return
	}
	var body map[string]any
	if err := json.Unmarshal(raw, &body); err != nil {
		writeJSON(w, http.StatusBadRequest, rpcErr(nil, -32700, "Parse error"))
		return
	}
	id := body["id"]
	method, _ := body["method"].(string)
	if id == nil && method != "" {
		w.WriteHeader(http.StatusAccepted)
		return
	}
	result := s.dispatchMCP(r.Context(), id, method, body)
	writeJSON(w, http.StatusOK, result)
}

func (s *Server) dispatchMCP(ctx context.Context, id any, method string, body map[string]any) map[string]any {
	params, _ := body["params"].(map[string]any)
	if params == nil {
		params = map[string]any{}
	}
	switch method {
	case "initialize":
		ver, _ := params["protocolVersion"].(string)
		if ver == "" {
			ver = "2026-07-28"
		}
		return map[string]any{"jsonrpc": "2.0", "id": id, "result": map[string]any{
			"protocolVersion": ver,
			"capabilities":    map[string]any{"tools": map[string]any{}},
			"serverInfo":      map[string]any{"name": "remedy", "version": s.version},
		}}
	case "ping":
		return map[string]any{"jsonrpc": "2.0", "id": id, "result": map[string]any{}}
	case "tools/list":
		return map[string]any{"jsonrpc": "2.0", "id": id, "result": map[string]any{"tools": interopMCPTools()}}
	case "tools/call":
		name, _ := params["name"].(string)
		args, _ := params["arguments"].(map[string]any)
		if args == nil {
			args = map[string]any{}
		}
		text, err := s.callInteropMCPTool(ctx, name, args)
		if err != nil {
			return map[string]any{"jsonrpc": "2.0", "id": id, "error": map[string]any{"code": -32601, "message": err.Error()}}
		}
		return map[string]any{"jsonrpc": "2.0", "id": id, "result": map[string]any{
			"content": []map[string]any{{"type": "text", "text": text}},
			"isError": false,
		}}
	case "resources/list":
		return map[string]any{"jsonrpc": "2.0", "id": id, "result": map[string]any{"resources": []any{}}}
	case "prompts/list":
		return map[string]any{"jsonrpc": "2.0", "id": id, "result": map[string]any{"prompts": []any{}}}
	default:
		return rpcErr(id, -32601, "Method not found: "+method)
	}
}

func interopMCPTools() []map[string]any {
	return []map[string]any{
		{"name": "remedy_ask", "description": "Ask Remedy a question in plain English. She answers as a peer agent.", "inputSchema": map[string]any{"type": "object", "properties": map[string]any{"question": map[string]any{"type": "string"}, "peer_id": map[string]any{"type": "string"}}, "required": []string{"question"}}},
		{"name": "remedy_delegate", "description": "Delegate a task to Remedy. Returns a task id you can poll with remedy_task_status.", "inputSchema": map[string]any{"type": "object", "properties": map[string]any{"task": map[string]any{"type": "string"}, "peer_id": map[string]any{"type": "string"}, "context": map[string]any{"type": "string"}}, "required": []string{"task"}}},
		{"name": "remedy_task_status", "description": "Look up the status of a delegated task.", "inputSchema": map[string]any{"type": "object", "properties": map[string]any{"task_id": map[string]any{"type": "string"}}, "required": []string{"task_id"}}},
		{"name": "remedy_share_context", "description": "Share context Remedy may use for a task. Not a grant of new permissions.", "inputSchema": map[string]any{"type": "object", "properties": map[string]any{"context": map[string]any{"type": "string"}, "task_id": map[string]any{"type": "string"}, "peer_id": map[string]any{"type": "string"}}, "required": []string{"context"}}},
	}
}

func (s *Server) callInteropMCPTool(ctx context.Context, name string, args map[string]any) (string, error) {
	switch name {
	case "remedy_ask":
		task, err := s.interopRecordText("mcp", "ask", stringVal(args, "question"), stringVal(args, "peer_id"), "", args)
		if err != nil {
			return "", err
		}
		if s.interopShouldLive(task) {
			task = s.interopRunLive(ctx, "mcp", task, nil)
		}
		return interopMCPToolResult(task), nil
	case "remedy_delegate":
		text := stringVal(args, "task")
		if extra := stringVal(args, "context"); extra != "" {
			text = text + "\n\nContext:\n" + extra
		}
		task, err := s.interopRecordText("mcp", "delegate", text, stringVal(args, "peer_id"), "", args)
		if err != nil {
			return "", err
		}
		if s.interopShouldLive(task) {
			task = s.interopRunLive(ctx, "mcp", task, nil)
		}
		return interopMCPToolResult(task), nil
	case "remedy_share_context":
		task, err := s.interopRecordText("mcp", "share_context", stringVal(args, "context"), stringVal(args, "peer_id"), stringVal(args, "task_id"), args)
		if err != nil {
			return "", err
		}
		return interopMCPToolResult(task), nil
	case "remedy_task_status":
		tid := stringVal(args, "task_id")
		task := s.interopGetTask(tid)
		if task == nil {
			return interopMustJSON(map[string]any{"ok": false, "error": "unknown task"}), nil
		}
		return interopMustJSON(map[string]any{"ok": true, "task": task}), nil
	default:
		return "", fmt.Errorf("Unknown tool: %s", name)
	}
}

func (s *Server) interopRecordInbound(protocol string, params map[string]any) (map[string]any, error) {
	msg, _ := params["message"].(map[string]any)
	text := extractA2AText(msg)
	if text == "" {
		text = stringVal(params, "text")
	}
	meta, _ := params["metadata"].(map[string]any)
	if meta == nil {
		meta = map[string]any{}
	}
	if msg != nil {
		if mm, ok := msg["metadata"].(map[string]any); ok {
			for k, v := range mm {
				if _, exists := meta[k]; !exists {
					meta[k] = v
				}
			}
		}
	}
	peerID := stringVal(meta, "peer_id", "from")
	action := stringVal(meta, "action")
	if action == "" {
		action = "ask"
	}
	if cid := stringVal(params, "contextId"); cid != "" {
		if _, ok := meta["contextId"]; !ok {
			meta["contextId"] = cid
		}
	}
	if msg != nil {
		if cid := stringVal(msg, "contextId"); cid != "" {
			if _, ok := meta["contextId"]; !ok {
				meta["contextId"] = cid
			}
		}
	}
	return s.interopRecordText(protocol, action, text, peerID, "", meta)
}

func (s *Server) interopRecordText(protocol, action, text, peerID, existingID string, meta map[string]any) (map[string]any, error) {
	action = interopNormalizeAction(action)
	switch action {
	case "ask", "delegate", "share_context", "task_status":
	default:
		// spend/send/delete and anything else still go through decide().
	}
	state := "TASK_STATE_SUBMITTED"
	reason := "queued"
	trust := "unverified"
	// Guards first — they take interopMu themselves.
	if err := s.interopGuards(peerID, text, meta); err != "" {
		state = "TASK_STATE_REJECTED"
		reason = err
	} else {
		peer := s.interopLoadPeer(peerID)
		trust = interopTrustLevel(peer, meta)
		decision := interopDecide(action, peer, trust, interopTrustPolicy(s.interopCfg()))
		action = decision.Action
		reason = decision.Reason
		trust = decision.Trust
		switch decision.Outcome {
		case "need_approval":
			state = "TASK_STATE_INPUT_REQUIRED"
		case "deny":
			state = "TASK_STATE_REJECTED"
		default:
			state = "TASK_STATE_SUBMITTED"
		}
	}
	interopMu.Lock()
	defer interopMu.Unlock()
	store := s.loadInteropTasks()
	if existingID != "" {
		if task, ok := store[existingID]; ok {
			history, _ := task["history"].([]any)
			history = append(history, a2aUserMessage(text))
			task["history"] = history
			store[existingID] = task
			s.saveInteropTasks(store)
			s.appendInteropAudit(protocol, "in", peerID, action, text, existingID)
			return task, nil
		}
	}
	id := uuid.NewString()
	ctx := uuid.NewString()
	if cid := stringVal(meta, "contextId", "context_id"); cid != "" {
		ctx = cid
	}
	now := time.Now().UTC().Format("2006-01-02T15:04:05.000Z")
	msg := a2aUserMessage(text)
	task := map[string]any{
		"id":        id,
		"contextId": ctx,
		"status":    map[string]any{"state": state, "timestamp": now, "message": msg},
		"history":   []any{msg},
		"artifacts": []any{},
		"metadata": map[string]any{
			"peer_id":    peerID,
			"action":     action,
			"created_ts": float64(time.Now().Unix()),
			"reason":     reason,
			"trust":      trust,
			"hop":        interopHopCount(meta),
		},
	}
	store[id] = task
	s.saveInteropTasks(store)
	s.appendInteropAudit(protocol, "in", peerID, action, text, id)
	s.appendInteropInbox(id, peerID, action, state, text)
	return task, nil
}

func (s *Server) interopGuards(peerID, text string, meta map[string]any) string {
	cfg := s.interopCfg()
	hopLimit := resolveAdvancedInt(cfg, "interop_hop_limit")
	if hopLimit <= 0 {
		hopLimit = 3
	}
	if hops := interopHopCount(meta); hops >= hopLimit {
		return fmt.Sprintf("hop limit %d exceeded", hopLimit)
	}
	rateLimit := resolveAdvancedInt(cfg, "interop_rate_per_min")
	if rateLimit <= 0 {
		rateLimit = 30
	}
	key := peerID
	if key == "" {
		key = "_unknown"
	}
	if !interopAllowRate(s.interopDir(), key, rateLimit) {
		return fmt.Sprintf("rate limit: %d requests per minute", rateLimit)
	}
	sum := sha256.Sum256([]byte(key + "\n" + strings.ToLower(strings.TrimSpace(text))))
	loopKey := hex.EncodeToString(sum[:])[:24]
	if !interopAllowLoop(s.interopDir(), loopKey, 3) {
		return "ping-pong guard: identical payload repeated"
	}
	_ = hopLimit
	return ""
}

func interopAllowRate(dir, peerID string, limit int) bool {
	return interopTouchWindow(filepath.Join(dir, "rate.json"), "rates", peerID, 60, limit)
}

func interopAllowLoop(dir, key string, repeats int) bool {
	return interopTouchWindow(filepath.Join(dir, "rate.json"), "loops", key, 120, repeats-1)
}

func interopTouchWindow(path, bucket, key string, windowS int, limit int) bool {
	interopMu.Lock()
	defer interopMu.Unlock()
	data := map[string]any{}
	if raw, err := os.ReadFile(path); err == nil {
		_ = json.Unmarshal(raw, &data)
	}
	table, _ := data[bucket].(map[string]any)
	if table == nil {
		table = map[string]any{}
		data[bucket] = table
	}
	entry, _ := table[key].(map[string]any)
	if entry == nil {
		entry = map[string]any{}
	}
	now := time.Now().Unix()
	var times []float64
	if raw, ok := entry["times"].([]any); ok {
		for _, v := range raw {
			f, ok := interopAsFloat(v)
			if ok && now-int64(f) <= int64(windowS) {
				times = append(times, f)
			}
		}
	}
	if len(times) >= limit {
		return false
	}
	times = append(times, float64(now))
	arr := make([]any, len(times))
	for i, f := range times {
		arr[i] = f
	}
	entry["times"] = arr
	table[key] = entry
	raw, _ := json.Marshal(data)
	_ = os.WriteFile(path, raw, 0o600)
	return true
}

func (s *Server) loadInteropTasks() map[string]map[string]any {
	path := filepath.Join(s.interopDir(), "tasks.json")
	raw, err := os.ReadFile(path)
	out := map[string]map[string]any{}
	if err != nil {
		return out
	}
	var wrap struct {
		Tasks map[string]map[string]any `json:"tasks"`
	}
	if json.Unmarshal(raw, &wrap) != nil || wrap.Tasks == nil {
		return out
	}
	return wrap.Tasks
}

func (s *Server) saveInteropTasks(store map[string]map[string]any) {
	path := filepath.Join(s.interopDir(), "tasks.json")
	payload, _ := json.Marshal(map[string]any{"version": 1, "tasks": store})
	_ = os.WriteFile(path, payload, 0o600)
}

func (s *Server) interopGetTask(id string) map[string]any {
	interopMu.Lock()
	defer interopMu.Unlock()
	if task, ok := s.loadInteropTasks()[id]; ok {
		return task
	}
	return nil
}

func (s *Server) interopListTasks(params map[string]any) ([]map[string]any, string) {
	interopMu.Lock()
	defer interopMu.Unlock()
	store := s.loadInteropTasks()
	items := make([]map[string]any, 0, len(store))
	for _, t := range store {
		items = append(items, t)
	}
	pageSize := 20
	if n, ok := interopAsInt(params["pageSize"]); ok && n > 0 && n <= 100 {
		pageSize = n
	}
	if end := pageSize; end > len(items) {
		end = len(items)
	}
	page := items
	if len(page) > pageSize {
		page = page[:pageSize]
	}
	return page, ""
}

func (s *Server) interopCancelTask(id string) (map[string]any, string) {
	interopMu.Lock()
	defer interopMu.Unlock()
	store := s.loadInteropTasks()
	task, ok := store[id]
	if !ok {
		return nil, "not_found"
	}
	state := statusState(task)
	switch state {
	case "TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED":
		return task, "not_cancelable"
	}
	now := time.Now().UTC().Format("2006-01-02T15:04:05.000Z")
	task["status"] = map[string]any{"state": "TASK_STATE_CANCELED", "timestamp": now}
	store[id] = task
	s.saveInteropTasks(store)
	s.interopCancelLive(id)
	return task, ""
}

func (s *Server) appendInteropAudit(protocol, direction, peerID, action, summary, taskID string) {
	s.appendInteropAuditExtra(protocol, direction, peerID, action, summary, map[string]any{"task_id": taskID})
}

func (s *Server) appendInteropAuditExtra(protocol, direction, peerID, action, summary string, extra map[string]any) {
	cfg := s.interopCfg()
	if !coerceBool(resolveAdvanced(advancedByKey()["interop_audit"], cfg), true) {
		return
	}
	if extra == nil {
		extra = map[string]any{}
	}
	path := filepath.Join(s.interopDir(), "audit.jsonl")
	entry, _ := json.Marshal(map[string]any{
		"ts":        float64(time.Now().Unix()),
		"direction": direction,
		"protocol":  protocol,
		"peer_id":   peerID,
		"action":    action,
		"summary":   redactInteropText(summary),
		"extra":     extra,
	})
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return
	}
	defer f.Close()
	_, _ = f.Write(append(entry, '\n'))
}

func (s *Server) appendInteropInbox(taskID, peerID, action, state, text string) {
	path := filepath.Join(s.interopDir(), "inbox.jsonl")
	entry, _ := json.Marshal(map[string]any{
		"ts":      float64(time.Now().Unix()),
		"task_id": taskID,
		"peer_id": peerID,
		"action":  action,
		"state":   state,
		"text":    interopTruncate(text, 2000),
	})
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return
	}
	defer f.Close()
	_, _ = f.Write(append(entry, '\n'))
}

func a2aUserMessage(text string) map[string]any {
	return map[string]any{
		"messageId": uuid.NewString(),
		"role":      "user",
		"parts":     []any{map[string]any{"text": text}},
	}
}

func extractA2AText(msg map[string]any) string {
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
			if t, ok := part["text"].(string); ok {
				chunks = append(chunks, t)
			}
		}
	}
	if len(chunks) == 0 {
		if t, ok := msg["text"].(string); ok {
			chunks = append(chunks, t)
		}
	}
	return strings.TrimSpace(strings.Join(chunks, "\n"))
}

func rpcErr(id any, code int, message string) map[string]any {
	return map[string]any{"jsonrpc": "2.0", "id": id, "error": map[string]any{"code": code, "message": message}}
}

func stringVal(m map[string]any, keys ...string) string {
	for _, k := range keys {
		if v, ok := m[k]; ok && v != nil {
			if s, ok := v.(string); ok {
				return strings.TrimSpace(s)
			}
		}
	}
	return ""
}

func statusState(task map[string]any) string {
	if st, ok := task["status"].(map[string]any); ok {
		if s, ok := st["state"].(string); ok {
			return s
		}
	}
	return ""
}

func interopMustJSON(v any) string {
	b, _ := json.Marshal(v)
	return string(b)
}

func interopAsInt(v any) (int, bool) {
	switch n := v.(type) {
	case int:
		return n, true
	case int64:
		return int(n), true
	case float64:
		return int(n), true
	case json.Number:
		i, err := n.Int64()
		return int(i), err == nil
	}
	return 0, false
}

func interopAsFloat(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	case json.Number:
		f, err := n.Float64()
		return f, err == nil
	}
	return 0, false
}

func interopTruncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}

func redactInteropText(s string) string {
	// Never echo bearer tokens or env assignments in the audit log.
	// Replacing an empty string would put *** between every character.
	if tok := os.Getenv(interopTokenEnv); tok != "" {
		s = strings.ReplaceAll(s, tok, "***")
	}
	return s
}
