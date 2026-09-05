package httpapi

import (
	"fmt"
	"strings"
)

// messengerFieldSchema is one Settings SPA field on a messenger connector.
type messengerFieldSchema struct {
	Key         string `json:"key"`
	Label       string `json:"label"`
	Kind        string `json:"kind"` // secret | text | bool | list | url
	Placeholder string `json:"placeholder,omitempty"`
	Help        string `json:"help,omitempty"`
	Required    bool   `json:"required,omitempty"`
}

type messengerCatalogEntry struct {
	ID            string
	Name          string
	Description   string
	Status        string // ready | partial | planned
	Inbound       bool
	Outbound      bool
	DocsURL       string
	Badge         string
	MaxReplyChars int
	Fields        []messengerFieldSchema
}

// messengerCatalog mirrors Python remedy.gateway.messengers.MESSENGERS field schema.
var messengerCatalog = []messengerCatalogEntry{
	{
		ID: "telegram", Name: "Telegram",
		Description:   "Bot API long-poll inbound and sendMessage outbound.",
		Status:        "ready", Inbound: true, Outbound: true,
		DocsURL: "https://core.telegram.org/bots#how-do-i-create-a-bot", Badge: "Telegram",
		MaxReplyChars: 4096,
		Fields: []messengerFieldSchema{
			{Key: "bot_token", Label: "Bot token", Kind: "secret", Placeholder: "123456:ABC…", Help: "From @BotFather", Required: true},
			{Key: "allow_chat_ids", Label: "Allowed chat IDs", Kind: "list", Placeholder: "123456789, -100…", Help: "Comma-separated. Empty + allow_all off = ignore all chats."},
			{Key: "allow_all", Label: "Allow all chats", Kind: "bool", Help: "Dangerous for public bots. Prefer an allowlist."},
		},
	},
	{
		ID: "discord", Name: "Discord",
		Description:   "Gateway WS inbound + REST outbound. Allowlist channels/users.",
		Status:        "ready", Inbound: true, Outbound: true,
		DocsURL: "https://discord.com/developers/docs/getting-started", Badge: "Discord",
		MaxReplyChars: 2000,
		Fields: []messengerFieldSchema{
			{Key: "bot_token", Label: "Bot token", Kind: "secret", Required: true},
			{Key: "channel_id", Label: "Default channel ID", Kind: "text"},
			{Key: "guild_id", Label: "Guild ID", Kind: "text"},
			{Key: "allow_ids", Label: "Allowed IDs", Kind: "list", Help: "Channel, user, or guild ids. Empty + allow_all off = ignore all."},
			{Key: "allow_all", Label: "Allow all", Kind: "bool"},
		},
	},
	{
		ID: "slack", Name: "Slack",
		Description:   "Socket Mode inbound + chat.postMessage. Needs bot + app token.",
		Status:        "ready", Inbound: true, Outbound: true,
		DocsURL: "https://api.slack.com/apis/connections/socket", Badge: "Slack",
		MaxReplyChars: 3000,
		Fields: []messengerFieldSchema{
			{Key: "bot_token", Label: "Bot token", Kind: "secret", Placeholder: "xoxb-…", Required: true},
			{Key: "app_token", Label: "App-level token", Kind: "secret", Placeholder: "xapp-…", Help: "Socket Mode (connections:write)."},
			{Key: "channel_id", Label: "Default channel ID", Kind: "text"},
			{Key: "allow_ids", Label: "Allowed channel/user IDs", Kind: "list"},
			{Key: "allow_all", Label: "Allow all", Kind: "bool"},
		},
	},
	{
		ID: "mattermost", Name: "Mattermost",
		Description:   "WebSocket inbound + REST posts (self-hosted).",
		Status:        "ready", Inbound: true, Outbound: true,
		DocsURL: "https://developers.mattermost.com/integrate/reference/bot-accounts/", Badge: "Mattermost",
		MaxReplyChars: 4000,
		Fields: []messengerFieldSchema{
			{Key: "base_url", Label: "Server URL", Kind: "url", Placeholder: "https://chat.example.com", Required: true},
			{Key: "bot_token", Label: "Bot token", Kind: "secret", Required: true},
			{Key: "team_id", Label: "Team ID", Kind: "text"},
			{Key: "channel_id", Label: "Default channel ID", Kind: "text"},
			{Key: "allow_ids", Label: "Allowed channel/user IDs", Kind: "list"},
			{Key: "allow_all", Label: "Allow all", Kind: "bool"},
		},
	},
	{
		ID: "whatsapp", Name: "WhatsApp",
		Description:   "Cloud API outbound; inbound via /api/webhooks/whatsapp (public HTTPS).",
		Status:        "partial", Inbound: true, Outbound: true,
		DocsURL: "https://developers.facebook.com/docs/whatsapp/cloud-api", Badge: "WhatsApp",
		MaxReplyChars: 4096,
		Fields: []messengerFieldSchema{
			{Key: "access_token", Label: "Access token", Kind: "secret", Required: true},
			{Key: "phone_number_id", Label: "Phone number ID", Kind: "text", Required: true},
			{Key: "verify_token", Label: "Webhook verify token", Kind: "secret"},
			{Key: "app_secret", Label: "App secret (HMAC)", Kind: "secret"},
			{Key: "allow_from", Label: "Allowed phone numbers", Kind: "list", Help: "E.164 numbers, comma-separated."},
			{Key: "allow_all", Label: "Allow all senders", Kind: "bool"},
		},
	},
	{
		ID: "teams", Name: "Microsoft Teams",
		Description:   "Bot Framework connector; inbound via /api/webhooks/teams.",
		Status:        "partial", Inbound: true, Outbound: true,
		DocsURL: "https://learn.microsoft.com/en-us/microsoftteams/platform/bots/what-are-bots", Badge: "Teams",
		MaxReplyChars: 28000,
		Fields: []messengerFieldSchema{
			{Key: "app_id", Label: "App (client) ID", Kind: "text", Required: true},
			{Key: "app_password", Label: "App password / secret", Kind: "secret", Required: true},
			{Key: "tenant_id", Label: "Tenant ID", Kind: "text", Help: "Default botframework.com"},
			{Key: "allow_ids", Label: "Allowed conversation/user IDs", Kind: "list"},
			{Key: "allow_all", Label: "Allow all", Kind: "bool"},
		},
	},
	{
		ID: "matrix", Name: "Matrix",
		Description:   "Client-Server /sync inbound + room send.",
		Status:        "ready", Inbound: true, Outbound: true,
		DocsURL: "https://spec.matrix.org/latest/client-server-api/", Badge: "Matrix",
		MaxReplyChars: 4000,
		Fields: []messengerFieldSchema{
			{Key: "homeserver", Label: "Homeserver URL", Kind: "url", Placeholder: "https://matrix.org", Required: true},
			{Key: "access_token", Label: "Access token", Kind: "secret", Required: true},
			{Key: "user_id", Label: "Bot user ID", Kind: "text", Placeholder: "@bot:example.com"},
			{Key: "room_id", Label: "Default room ID", Kind: "text"},
			{Key: "allow_ids", Label: "Allowed room/user IDs", Kind: "list"},
			{Key: "allow_all", Label: "Allow all rooms", Kind: "bool"},
		},
	},
	{
		ID: "google_chat", Name: "Google Chat",
		Description:   "Chat API outbound; inbound via /api/webhooks/google_chat.",
		Status:        "partial", Inbound: true, Outbound: true,
		DocsURL: "https://developers.google.com/chat", Badge: "GChat",
		MaxReplyChars: 4096,
		Fields: []messengerFieldSchema{
			{Key: "access_token", Label: "Access token", Kind: "secret", Required: true},
			{Key: "space_id", Label: "Default space ID", Kind: "text"},
			{Key: "allow_ids", Label: "Allowed space/user IDs", Kind: "list"},
			{Key: "allow_all", Label: "Allow all", Kind: "bool"},
		},
	},
	{
		ID: "signal", Name: "Signal",
		Description:   "Optional signal-cli receive/send (local binary required).",
		Status:        "partial", Inbound: true, Outbound: true,
		DocsURL: "https://github.com/AsamK/signal-cli", Badge: "Signal",
		MaxReplyChars: 2000,
		Fields: []messengerFieldSchema{
			{Key: "cli_path", Label: "signal-cli path", Kind: "text", Placeholder: "signal-cli"},
			{Key: "account", Label: "Account number", Kind: "text", Placeholder: "+1…", Required: true},
			{Key: "allow_from", Label: "Allowed numbers", Kind: "list"},
			{Key: "allow_all", Label: "Allow all", Kind: "bool"},
		},
	},
}

func publicFieldsFromSection(entry messengerCatalogEntry, section map[string]any) map[string]any {
	out := map[string]any{}
	if section == nil {
		return out
	}
	for _, f := range entry.Fields {
		if f.Kind == "secret" {
			continue
		}
		val, ok := section[f.Key]
		if !ok {
			continue
		}
		switch f.Kind {
		case "list":
			out[f.Key] = messengerPublicList(val)
		case "bool":
			out[f.Key] = coerceBool(val, false)
		default:
			if val == nil {
				out[f.Key] = ""
			} else {
				out[f.Key] = strings.TrimSpace(fmt.Sprint(val))
			}
		}
	}
	return out
}

// messengerPublicList preserves case (chat IDs / phone numbers are case-sensitive).
func messengerPublicList(val any) []string {
	switch t := val.(type) {
	case []string:
		out := make([]string, 0, len(t))
		for _, x := range t {
			if s := strings.TrimSpace(x); s != "" {
				out = append(out, s)
			}
		}
		return out
	case []any:
		out := make([]string, 0, len(t))
		for _, x := range t {
			if s := strings.TrimSpace(fmt.Sprint(x)); s != "" && s != "<nil>" {
				out = append(out, s)
			}
		}
		return out
	case string:
		parts := strings.FieldsFunc(t, func(r rune) bool { return r == ',' || r == ';' })
		out := make([]string, 0, len(parts))
		for _, p := range parts {
			if s := strings.TrimSpace(p); s != "" {
				out = append(out, s)
			}
		}
		return out
	default:
		return []string{}
	}
}

func fieldSchemaMaps(fields []messengerFieldSchema) []map[string]any {
	out := make([]map[string]any, 0, len(fields))
	for _, f := range fields {
		row := map[string]any{
			"key":      f.Key,
			"label":    f.Label,
			"kind":     f.Kind,
			"required": f.Required,
		}
		if f.Placeholder != "" {
			row["placeholder"] = f.Placeholder
		}
		if f.Help != "" {
			row["help"] = f.Help
		}
		out = append(out, row)
	}
	return out
}
