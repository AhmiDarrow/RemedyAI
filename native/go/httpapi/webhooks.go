package httpapi

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/gateway"
)

const webhookMaxBytes = 2 * 1024 * 1024

func (s *Server) ensureMessengerGateway() *gateway.Gateway {
	if s == nil {
		return nil
	}
	if s.messengerGW == nil {
		s.startMessengerGateway()
	}
	return s.messengerGW
}

func (s *Server) webhookChannel(kind gateway.ChannelKind) gateway.Channel {
	gw := s.ensureMessengerGateway()
	if gw == nil {
		return nil
	}
	return gw.GetChannel(kind)
}

func readBodyCapped(r *http.Request, maxBytes int64) ([]byte, error) {
	if cl := r.ContentLength; cl > 0 && cl > maxBytes {
		return nil, errPayloadTooLarge
	}
	limited := io.LimitReader(r.Body, maxBytes+1)
	raw, err := io.ReadAll(limited)
	if err != nil {
		return nil, err
	}
	if int64(len(raw)) > maxBytes {
		return nil, errPayloadTooLarge
	}
	return raw, nil
}

var errPayloadTooLarge = errors.New("payload too large")

func writeWebhookError(w http.ResponseWriter, status int, detail string) {
	writeJSON(w, status, map[string]any{"detail": detail})
}

func (s *Server) handleWhatsAppVerify(w http.ResponseWriter, r *http.Request) {
	ch, ok := s.webhookChannel(gateway.ChannelWhatsApp).(*gateway.WhatsAppChannel)
	if !ok || ch == nil {
		writeWebhookError(w, http.StatusServiceUnavailable, "WhatsApp channel not active")
		return
	}
	q := r.URL.Query()
	challenge, ok := ch.VerifyWebhookChallenge(
		q.Get("hub.mode"),
		q.Get("hub.verify_token"),
		q.Get("hub.challenge"),
	)
	if !ok {
		writeWebhookError(w, http.StatusForbidden, "verify failed")
		return
	}
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(challenge))
}

func (s *Server) handleWhatsAppEvents(w http.ResponseWriter, r *http.Request) {
	ch, ok := s.webhookChannel(gateway.ChannelWhatsApp).(*gateway.WhatsAppChannel)
	if !ok || ch == nil {
		writeWebhookError(w, http.StatusServiceUnavailable, "WhatsApp channel not active")
		return
	}
	body, err := readBodyCapped(r, webhookMaxBytes)
	if errors.Is(err, errPayloadTooLarge) {
		writeWebhookError(w, http.StatusRequestEntityTooLarge, "payload too large")
		return
	}
	if err != nil {
		writeWebhookError(w, http.StatusBadRequest, "invalid body")
		return
	}
	sig := r.Header.Get("X-Hub-Signature-256")
	if !ch.VerifySignature(body, sig) {
		writeWebhookError(w, http.StatusForbidden, "bad signature")
		return
	}
	data, err := gateway.DecodeJSONObject(body)
	if err != nil {
		writeWebhookError(w, http.StatusBadRequest, "invalid json")
		return
	}
	n := ch.HandleWebhookPayload(r.Context(), data)
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "handled": n})
}

func (s *Server) handleTeamsActivity(w http.ResponseWriter, r *http.Request) {
	ch, ok := s.webhookChannel(gateway.ChannelTeams).(*gateway.TeamsChannel)
	if !ok || ch == nil {
		writeWebhookError(w, http.StatusServiceUnavailable, "Teams channel not active")
		return
	}
	auth := r.Header.Get("Authorization")
	if !ch.VerifyInboundAuth(auth) {
		writeWebhookError(w, http.StatusUnauthorized, "teams auth failed")
		return
	}
	body, err := readBodyCapped(r, webhookMaxBytes)
	if errors.Is(err, errPayloadTooLarge) {
		writeWebhookError(w, http.StatusRequestEntityTooLarge, "payload too large")
		return
	}
	if err != nil {
		writeWebhookError(w, http.StatusBadRequest, "invalid body")
		return
	}
	activity, err := gateway.DecodeJSONObject(body)
	if err != nil {
		writeWebhookError(w, http.StatusBadRequest, "invalid json")
		return
	}
	handled := ch.HandleActivity(r.Context(), activity)
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "handled": handled})
}

func (s *Server) handleGoogleChatEvent(w http.ResponseWriter, r *http.Request) {
	ch, ok := s.webhookChannel(gateway.ChannelGoogleChat).(*gateway.GoogleChatChannel)
	if !ok || ch == nil {
		writeWebhookError(w, http.StatusServiceUnavailable, "Google Chat channel not active")
		return
	}
	body, err := readBodyCapped(r, webhookMaxBytes)
	if errors.Is(err, errPayloadTooLarge) {
		writeWebhookError(w, http.StatusRequestEntityTooLarge, "payload too large")
		return
	}
	if err != nil {
		writeWebhookError(w, http.StatusBadRequest, "invalid body")
		return
	}
	data, err := gateway.DecodeJSONObject(body)
	if err != nil {
		writeWebhookError(w, http.StatusBadRequest, "invalid json")
		return
	}
	etype := strings.ToUpper(strings.TrimSpace(firstString(data["type"], data["eventType"])))
	isURLVerification := etype == "URL_VERIFICATION" || etype == "URL_VERIFICATION_EVENT"
	// Match Python truthiness: skip auth only for challenge-only shapes, never for MESSAGE bodies
	// that happen to include a challenge key.
	challengePresent := data["challenge"] != nil
	isChallengeOnly := !jsonTruthy(data["message"]) && challengePresent &&
		(etype == "" || etype == "CHALLENGE" || etype == "URL_VERIFICATION" || etype == "URL_VERIFICATION_EVENT")
	if isURLVerification || isChallengeOnly {
		challenge := data["challenge"]
		if !jsonTruthy(challenge) {
			challenge = data["token"]
		}
		if !jsonTruthy(challenge) {
			challenge = "ok"
		}
		writeJSON(w, http.StatusOK, map[string]any{"challenge": challenge})
		return
	}
	auth := r.Header.Get("Authorization")
	if !ch.VerifyInboundAuth(auth) {
		writeWebhookError(w, http.StatusUnauthorized, "google_chat auth failed")
		return
	}
	handled := ch.HandleEvent(r.Context(), data)
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "handled": handled})
}

func firstString(vals ...any) string {
	for _, v := range vals {
		if v == nil {
			continue
		}
		if s, ok := v.(string); ok {
			if strings.TrimSpace(s) != "" {
				return s
			}
			continue
		}
		s := strings.TrimSpace(fmt.Sprint(v))
		if s != "" && s != "<nil>" {
			return s
		}
	}
	return ""
}

// jsonTruthy mirrors Python bool(value) for common JSON shapes.
func jsonTruthy(v any) bool {
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
	default:
		return true
	}
}

// genericWebhookPayload mirrors Python WebhookPayload (api_models.py).
type genericWebhookPayload struct {
	Source    string         `json:"source"`
	Event     string         `json:"event"`
	Data      map[string]any `json:"data"`
	Signature *string        `json:"signature"`
}

// handleGenericWebhook serves POST /api/webhook/{source} (CI / generic inject).
// Middleware skips Bearer so X-Remedy-Webhook-Secret can reach us; when
// AuthEnabled we fail closed (Bearer or webhook secret), matching Python.
func (s *Server) handleGenericWebhook(w http.ResponseWriter, r *http.Request) {
	gw := s.ensureMessengerGateway()
	if gw == nil {
		writeWebhookError(w, http.StatusServiceUnavailable, "Gateway not available")
		return
	}
	if status, detail := s.authorizeGenericWebhook(r); status != 0 {
		writeWebhookError(w, status, detail)
		return
	}

	source := strings.TrimSpace(r.PathValue("source"))
	if source == "" {
		writeWebhookError(w, http.StatusUnprocessableEntity, "invalid webhook payload: source required")
		return
	}

	body, err := readBodyCapped(r, webhookMaxBytes)
	if errors.Is(err, errPayloadTooLarge) {
		writeWebhookError(w, http.StatusRequestEntityTooLarge, "payload too large")
		return
	}
	if err != nil {
		writeWebhookError(w, http.StatusBadRequest, "invalid body")
		return
	}

	payload, err := parseGenericWebhookPayload(body)
	if err != nil {
		writeWebhookError(w, http.StatusUnprocessableEntity, "invalid webhook payload: "+err.Error())
		return
	}

	raw := truncateRunes(strings.ToValidUTF8(string(body), "\uFFFD"), 1000)
	ev := gateway.Event{
		ID:       gateway.NewEventID(),
		Kind:     gateway.EventWebhook,
		Channel:  gateway.ChannelAPI,
		SourceID: source,
		Payload: map[string]any{
			"source": source,
			"event":  payload.Event,
			"data":   payload.Data,
			"raw":    raw,
		},
		At: time.Now().UTC(),
	}
	gw.Enqueue(ev)
	writeJSON(w, http.StatusOK, map[string]any{"status": "accepted", "source": source})
}

// authorizeGenericWebhook mirrors memory.py receive_webhook auth (S-MSG-03).
// Returns (0, "") when allowed.
func (s *Server) authorizeGenericWebhook(r *http.Request) (int, string) {
	if !AuthEnabled() {
		return 0, ""
	}
	expected := ""
	if s != nil {
		expected = strings.TrimSpace(s.token)
	}
	if expected == "" {
		expected = strings.TrimSpace(os.Getenv("REMEDY_API_KEY"))
	}
	if expected == "" {
		expected = strings.TrimSpace(os.Getenv("REMEDY_WEBHOOK_SECRET"))
	}
	webhookSecret := strings.TrimSpace(os.Getenv("REMEDY_WEBHOOK_SECRET"))
	if expected == "" && webhookSecret == "" {
		return http.StatusServiceUnavailable,
			"Webhook auth not configured (set REMEDY_WEBHOOK_SECRET or enable local API token)"
	}
	auth := r.Header.Get("Authorization")
	secretHdr := r.Header.Get("X-Remedy-Webhook-Secret")
	bearerOK := expected != "" && secretEquals(auth, "Bearer "+expected)
	secretOK := secretHdr != "" && (
		(expected != "" && secretEquals(secretHdr, expected)) ||
			(webhookSecret != "" && secretEquals(secretHdr, webhookSecret)))
	if !(bearerOK || secretOK) {
		return http.StatusUnauthorized, "Webhook auth required"
	}
	return 0, ""
}

func parseGenericWebhookPayload(body []byte) (genericWebhookPayload, error) {
	var payload genericWebhookPayload
	dec := json.NewDecoder(strings.NewReader(string(body)))
	dec.UseNumber()
	if err := dec.Decode(&payload); err != nil {
		return genericWebhookPayload{}, err
	}
	// Reject trailing junk (Python model_validate_json is strict on the document).
	if err := dec.Decode(&struct{}{}); err != io.EOF {
		if err == nil {
			return genericWebhookPayload{}, errors.New("trailing data")
		}
		return genericWebhookPayload{}, err
	}
	if strings.TrimSpace(payload.Source) == "" {
		return genericWebhookPayload{}, errors.New("source required")
	}
	if payload.Event == "" {
		payload.Event = "default"
	}
	if payload.Data == nil {
		payload.Data = map[string]any{}
	}
	return payload, nil
}
