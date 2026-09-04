package gateway

import (
	"crypto/rand"
	"encoding/hex"
	"time"
)

// ChannelKind is a messenger / internal transport id.
type ChannelKind string

const (
	ChannelCLI        ChannelKind = "cli"
	ChannelWeb        ChannelKind = "web"
	ChannelAPI        ChannelKind = "api"
	ChannelTelegram   ChannelKind = "telegram"
	ChannelDiscord    ChannelKind = "discord"
	ChannelSlack      ChannelKind = "slack"
	ChannelMattermost ChannelKind = "mattermost"
	ChannelWhatsApp   ChannelKind = "whatsapp"
	ChannelTeams      ChannelKind = "teams"
	ChannelMatrix     ChannelKind = "matrix"
	ChannelGoogleChat ChannelKind = "google_chat"
	ChannelSignal     ChannelKind = "signal"
)

// EventKind classifies a gateway event.
type EventKind string

const (
	EventMessage   EventKind = "message"
	EventHeartbeat EventKind = "heartbeat"
	EventHealth    EventKind = "health"
	EventPing      EventKind = "ping"
)

// Event is a normalized inbound (or system) gateway event.
type Event struct {
	ID        string         `json:"id"`
	Kind      EventKind      `json:"kind"`
	Channel   ChannelKind    `json:"channel"`
	SourceID  string         `json:"source_id"`
	SessionID string         `json:"session_id,omitempty"`
	Payload   map[string]any `json:"payload,omitempty"`
	Raw       string         `json:"raw,omitempty"`
	At        time.Time      `json:"at"`
}

// NewEventID returns a short random event id.
func NewEventID() string {
	var b [8]byte
	_, _ = rand.Read(b[:])
	return hex.EncodeToString(b[:])
}

// IsMessenger reports whether channel is an external messenger (not cli/web/api).
func IsMessenger(ch ChannelKind) bool {
	switch ChannelKind(normalizeID(string(ch))) {
	case ChannelTelegram, ChannelDiscord, ChannelSlack, ChannelMattermost,
		ChannelWhatsApp, ChannelTeams, ChannelMatrix, ChannelGoogleChat, ChannelSignal:
		return true
	default:
		return false
	}
}

func normalizeID(s string) string {
	out := make([]byte, 0, len(s))
	for i := 0; i < len(s); i++ {
		c := s[i]
		if c >= 'A' && c <= 'Z' {
			c += 'a' - 'A'
		}
		if (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' {
			out = append(out, c)
		}
	}
	return string(out)
}
