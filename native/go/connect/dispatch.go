package connect

import (
	"encoding/json"
	"fmt"
	"strings"
	"sync"
)

const (
	// MaxFragments caps half-received inner HTTP messages (Python MAX_FRAGMENTS).
	MaxFragments = 16
	// MaxFragBytes caps assembled inner HTTP payload (Python MAX_FRAG_BYTES).
	MaxFragBytes = 256 * 1024
	// MaxInnerTasks caps concurrent inner HTTP handlers (Python MAX_INNER_TASKS).
	MaxInnerTasks = 8
)

// InnerMuxState holds per-session fragment buffers and inflight count.
type InnerMuxState struct {
	mu          sync.Mutex
	fragments   map[uint32]*fragmentBuf
	inflight    int
	Panes       map[string]bool
	SidecarPort int
	APIKey      string
}

type fragmentBuf struct {
	data []byte
}

func newInnerMuxState(cfg SessionConfig) *InnerMuxState {
	panes := DefaultPanes()
	if cfg.Panes != nil {
		panes = NormalizePanes(cfg.Panes)
	} else if cfg.Config != nil {
		panes = PanesFromConfig(cfg.Config)
	}
	port := cfg.SidecarPort
	if port <= 0 {
		port = 7400
	}
	return &InnerMuxState{
		fragments:   make(map[uint32]*fragmentBuf),
		Panes:       panes,
		SidecarPort: port,
		APIKey:      cfg.APIKey,
	}
}

func jsonError(status int, reason, code string) []byte {
	body, _ := json.Marshal(map[string]string{"error": code, "reason": reason})
	phrases := map[int]string{
		400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
		413: "Payload Too Large", 429: "Too Many Requests", 502: "Bad Gateway",
	}
	phrase := phrases[status]
	if phrase == "" {
		if status >= 500 {
			phrase = "Error"
		} else {
			phrase = "OK"
		}
	}
	return EncodeHTTPError(status, phrase, body)
}

func jsonOK(obj any) []byte {
	body, err := json.Marshal(obj)
	if err != nil {
		return jsonError(500, "json", "json")
	}
	return EncodeHTTPError(200, "OK", body)
}

func meBody(device Device, panes map[string]bool, paused bool) map[string]any {
	reachable := "lan"
	if paused {
		reachable = "paused"
	}
	return map[string]any{
		"panes":      panes,
		"paused":     paused,
		"reachable":  reachable,
		"device_id":  device.ID,
		"session_id": nil,
		"device": map[string]any{
			"id":   device.ID,
			"name": device.Name,
		},
	}
}

// IterRequestHTTP applies deny gates and either answers locally or proxies loopback.
func IterRequestHTTP(req HTTPRequest, device Device, state *InnerMuxState, home string, emit func([]byte) error) error {
	if state == nil {
		return fmt.Errorf("%w: nil mux state", ErrSession)
	}
	panes := state.Panes
	if panes == nil {
		panes = DefaultPanes()
	}
	target := req.Target
	if target == "" {
		target = "/"
	}
	path, query, _ := strings.Cut(target, "?")
	if path == "" {
		path = "/"
	}
	safe, ok := SanitizeOriginPath(path)
	if !ok {
		return emit(jsonError(400, "path", "path"))
	}
	path = safe
	req.Target = path

	method := strings.ToUpper(strings.TrimSpace(req.Method))
	if method == "" {
		method = "GET"
	}

	if (method == "PUT" || method == "PATCH") && strings.HasPrefix(path, "/api/settings") {
		if locked := SettingsWriteLocked(req.Body); locked != "" {
			return emit(jsonError(403, locked, "forbidden"))
		}
		if SettingsBodySafeProvider(req.Body) {
			panes = NormalizePanes(panes)
			panes["settings_write"] = true
		}
	}

	if path == "/connect/me" || path == "/api/connect/me" {
		return emit(jsonOK(meBody(device, panes, IsPaused(home))))
	}

	if path == "/connect/preview" || path == "/api/connect/preview" {
		if !panes["computer_preview"] {
			return emit(jsonError(403, "pane:computer_preview", "forbidden"))
		}
		method = "POST"
		path = "/api/computer/capture"
		req.Method = method
		req.Target = path
	}

	// Self-revoke only (never another device).
	if method == "POST" && strings.HasPrefix(path, "/api/connect/devices/") && strings.HasSuffix(path, "/revoke") {
		parts := strings.Split(path, "/")
		// /api/connect/devices/{id}/revoke → index 4
		if len(parts) < 6 {
			return emit(jsonError(403, "connect:mgmt", "forbidden"))
		}
		meID := strings.ToLower(strings.TrimSpace(device.ID))
		revokeID := strings.ToLower(strings.TrimSpace(parts[4]))
		if meID == "" || revokeID != meID {
			return emit(jsonError(403, "connect:mgmt", "forbidden"))
		}
		if _, err := RevokeDevice(revokeID, home); err != nil {
			return emit(jsonError(500, "revoke", "revoke failed"))
		}
		return emit(jsonOK(map[string]any{"ok": true, "id": revokeID, "revoked": true}))
	}

	if reason := ConnectForbidden(method, path, query, panes); reason != "" {
		return emit(jsonError(403, reason, "forbidden"))
	}

	return IterProxyResponse(ProxyRequest{
		Method:      method,
		Path:        path,
		Query:       query,
		Headers:     req.Headers,
		Body:        req.Body,
		SidecarPort: state.SidecarPort,
		APIKey:      state.APIKey,
	}, emit)
}

// SendInnerHTTPRes fragments HTTP/1.1 response bytes as TYPE_HTTP_RES records.
func (s *Session) SendInnerHTTPRes(msgID uint32, piece []byte, fin bool) error {
	frames, err := FragmentInner(TypeHTTPRes, msgID, piece, MaxPlaintext, fin)
	if err != nil {
		return err
	}
	for _, frame := range frames {
		if err := s.SendPlainInner(frame); err != nil {
			return err
		}
	}
	return nil
}

// HandleInner demuxes control frames and assembles/dispatches HTTP requests.
func HandleInner(sess *Session, plain []byte, state *InnerMuxState) error {
	if sess == nil || sess.Crypto == nil {
		return fmt.Errorf("%w: nil session", ErrSession)
	}
	if len(plain) == 0 || plain[0] != InnerVersion {
		return nil
	}
	sess.Crypto.Inner = true
	frame, err := DecodeInner(plain)
	if err != nil {
		return err
	}
	switch frame.Type {
	case TypeRekey:
		return sess.Crypto.RekeyRecv()
	case TypePing:
		pong, err := EncodeInner(TypePong, frame.ID, nil, true)
		if err != nil {
			return err
		}
		return sess.SendPlainInner(pong)
	case TypePong:
		return nil
	case TypeHTTPReq:
		return handleHTTPReqFrame(sess, frame, state)
	default:
		return nil
	}
}

// HandleInnerControl keeps the prior control-only entry point (tests + callers).
func HandleInnerControl(sess *Session, plain []byte) error {
	return HandleInner(sess, plain, nil)
}

func handleHTTPReqFrame(sess *Session, frame InnerFrame, state *InnerMuxState) error {
	if state == nil {
		// No mux state yet — ignore HTTP until RunSession wires one.
		return nil
	}
	state.mu.Lock()
	buf := state.fragments[frame.ID]
	if buf == nil {
		if len(state.fragments) >= MaxFragments {
			// Evict oldest partial.
			for id := range state.fragments {
				delete(state.fragments, id)
				break
			}
			state.mu.Unlock()
			return sess.SendInnerHTTPRes(frame.ID, jsonError(429, "fragments", "busy"), true)
		}
		buf = &fragmentBuf{}
		state.fragments[frame.ID] = buf
	}
	if len(buf.data)+len(frame.Payload) > MaxFragBytes {
		delete(state.fragments, frame.ID)
		state.mu.Unlock()
		return sess.SendInnerHTTPRes(frame.ID, jsonError(413, "too large", "too_large"), true)
	}
	buf.data = append(buf.data, frame.Payload...)
	if !frame.Fin() {
		state.mu.Unlock()
		return nil
	}
	joined := append([]byte(nil), buf.data...)
	delete(state.fragments, frame.ID)
	if state.inflight >= MaxInnerTasks {
		state.mu.Unlock()
		return sess.SendInnerHTTPRes(frame.ID, jsonError(429, "busy", "busy"), true)
	}
	state.inflight++
	state.mu.Unlock()

	req, err := DecodeHTTPRequest(joined)
	if err != nil {
		state.mu.Lock()
		state.inflight--
		state.mu.Unlock()
		return sess.SendInnerHTTPRes(frame.ID, jsonError(400, "http", "http"), true)
	}

	sentAny := false
	emitErr := IterRequestHTTP(req, sess.Device, state, sess.Home, func(piece []byte) error {
		if len(piece) == 0 {
			return nil
		}
		sentAny = true
		return sess.SendInnerHTTPRes(frame.ID, piece, false)
	})
	state.mu.Lock()
	state.inflight--
	state.mu.Unlock()
	if emitErr != nil {
		_ = sess.SendInnerHTTPRes(frame.ID, jsonError(502, "pipe", "pipe"), true)
		return nil
	}
	if !sentAny {
		return sess.SendInnerHTTPRes(frame.ID, jsonError(502, "empty", "empty"), true)
	}
	return sess.SendInnerHTTPRes(frame.ID, nil, true)
}
