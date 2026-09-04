package httpapi

import (
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"

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
