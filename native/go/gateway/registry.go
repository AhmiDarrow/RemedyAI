package gateway

import (
	"fmt"
	"log"
	"os"
	"strings"
)

// SecretLookup resolves channel secrets (secure store / legacy / env).
type SecretLookup func(channel, field string) string

// RegisterFromConfig registers enabled messenger adapters onto gw.
// Returns the list of registered messenger ids.
func RegisterFromConfig(gw *Gateway, cfg map[string]any, home string, secrets SecretLookup) []string {
	if gw == nil {
		return nil
	}
	if secrets == nil {
		secrets = func(channel, field string) string {
			return resolveSecret(cfg, home, channel, field)
		}
	}
	enabled := enabledSet(cfg)
	registered := make([]string, 0, 8)

	if _, on := enabled["telegram"]; on {
		tok := secrets("telegram", "bot_token")
		sec := section(cfg, "telegram")
		if tok != "" {
			gw.RegisterChannel(NewTelegram(gw, TelegramConfig{
				BotToken:     tok,
				AllowChatIDs: firstAny(sec["allow_chat_ids"], sec["chat_ids"]),
				AllowAll:     asBool(sec["allow_all"]),
				HomeDir:      home,
			}))
			registered = append(registered, "telegram")
		} else {
			log.Printf("telegram enabled but no bot_token")
		}
	}

	if _, on := enabled["discord"]; on {
		tok := secrets("discord", "bot_token")
		sec := section(cfg, "discord")
		if tok != "" {
			gw.RegisterChannel(NewDiscord(gw, DiscordConfig{
				BotToken:  tok,
				ChannelID: cfgString(sec, "channel_id"),
				GuildID:   cfgString(sec, "guild_id"),
				AllowIDs:  firstAny(sec["allow_ids"], sec["allow_chat_ids"]),
				AllowAll:  asBool(sec["allow_all"]),
				HomeDir:   home,
			}))
			registered = append(registered, "discord")
		} else {
			log.Printf("discord enabled but no bot_token")
		}
	}

	if _, on := enabled["slack"]; on {
		tok := secrets("slack", "bot_token")
		sec := section(cfg, "slack")
		if tok != "" {
			gw.RegisterChannel(NewSlack(gw, SlackConfig{
				BotToken:  tok,
				AppToken:  secrets("slack", "app_token"),
				ChannelID: cfgString(sec, "channel_id"),
				AllowIDs:  firstAny(sec["allow_ids"], sec["allow_chat_ids"]),
				AllowAll:  asBool(sec["allow_all"]),
				HomeDir:   home,
			}))
			registered = append(registered, "slack")
		} else {
			log.Printf("slack enabled but no bot_token")
		}
	}

	if _, on := enabled["mattermost"]; on {
		sec := section(cfg, "mattermost")
		tok := secrets("mattermost", "bot_token")
		base := cfgString(sec, "base_url")
		if tok != "" && base != "" {
			gw.RegisterChannel(NewMattermost(gw, MattermostConfig{
				BotToken:  tok,
				BaseURL:   base,
				ChannelID: cfgString(sec, "channel_id"),
				TeamID:    cfgString(sec, "team_id"),
				AllowIDs:  firstAny(sec["allow_ids"], sec["allow_chat_ids"]),
				AllowAll:  asBool(sec["allow_all"]),
				HomeDir:   home,
			}))
			registered = append(registered, "mattermost")
		} else {
			log.Printf("mattermost enabled but missing bot_token or base_url")
		}
	}

	if _, on := enabled["matrix"]; on {
		sec := section(cfg, "matrix")
		tok := secrets("matrix", "access_token")
		hs := cfgString(sec, "homeserver")
		if tok != "" && hs != "" {
			gw.RegisterChannel(NewMatrix(gw, MatrixConfig{
				AccessToken: tok,
				Homeserver:  hs,
				UserID:      cfgString(sec, "user_id"),
				RoomID:      cfgString(sec, "room_id"),
				AllowIDs:    firstAny(sec["allow_ids"], sec["allow_chat_ids"], sec["allow_room_ids"]),
				AllowAll:    asBool(sec["allow_all"]),
				HomeDir:     home,
			}))
			registered = append(registered, "matrix")
		} else {
			log.Printf("matrix enabled but missing access_token or homeserver")
		}
	}

	if _, on := enabled["whatsapp"]; on {
		sec := section(cfg, "whatsapp")
		tok := secrets("whatsapp", "access_token")
		phone := cfgString(sec, "phone_number_id")
		if tok != "" && phone != "" {
			verify := secrets("whatsapp", "verify_token")
			if verify == "" {
				verify = cfgString(sec, "verify_token")
			}
			gw.RegisterChannel(NewWhatsApp(gw, WhatsAppConfig{
				AccessToken:   tok,
				PhoneNumberID: phone,
				VerifyToken:   verify,
				AppSecret:     secrets("whatsapp", "app_secret"),
				AllowFrom:     sec["allow_from"],
				AllowAll:      asBool(sec["allow_all"]),
			}))
			registered = append(registered, "whatsapp")
		} else {
			log.Printf("whatsapp enabled but missing access_token or phone_number_id")
		}
	}

	if _, on := enabled["teams"]; on {
		sec := section(cfg, "teams")
		appID := cfgString(sec, "app_id")
		pwd := secrets("teams", "app_password")
		if appID != "" && pwd != "" {
			gw.RegisterChannel(NewTeams(gw, TeamsConfig{
				AppID:       appID,
				AppPassword: pwd,
				TenantID:    cfgString(sec, "tenant_id"),
				AllowIDs:    firstAny(sec["allow_ids"], sec["allow_chat_ids"]),
				AllowAll:    asBool(sec["allow_all"]),
			}))
			registered = append(registered, "teams")
		} else {
			log.Printf("teams enabled but missing app_id or app_password")
		}
	}

	if _, on := enabled["google_chat"]; on {
		sec := section(cfg, "google_chat")
		tok := secrets("google_chat", "access_token")
		refresh := secrets("google_chat", "refresh_token")
		clientID := secrets("google_chat", "oauth_client_id")
		if clientID == "" {
			clientID = cfgString(sec, "oauth_client_id")
		}
		clientSecret := secrets("google_chat", "oauth_client_secret")
		if tok != "" || (refresh != "" && clientID != "" && clientSecret != "") {
			gw.RegisterChannel(NewGoogleChat(gw, GoogleChatConfig{
				AccessToken:  tok,
				RefreshToken: refresh,
				ClientID:     clientID,
				ClientSecret: clientSecret,
				SpaceID:      cfgString(sec, "space_id"),
				AllowIDs:     firstAny(sec["allow_ids"], sec["allow_chat_ids"]),
				AllowAll:     asBool(sec["allow_all"]),
			}))
			registered = append(registered, "google_chat")
		} else {
			log.Printf("google_chat enabled but missing access_token (or refresh_token + oauth client)")
		}
	}

	if _, on := enabled["signal"]; on {
		sec := section(cfg, "signal")
		cli := cfgString(sec, "cli_path")
		if cli == "" {
			cli = "signal-cli"
		}
		acct := cfgString(sec, "account")
		resolved := resolveSignalCLI(cli)
		if resolved == "" {
			if managed := LookupManagedSignalCLI(home); managed != "" {
				resolved = managed
				cli = managed
			}
		}
		if resolved == "" || acct == "" {
			log.Printf("signal enabled but missing signal-cli binary or account (cli_ok=%v account_ok=%v)", resolved != "", acct != "")
		} else {
			gw.RegisterChannel(NewSignal(gw, SignalConfig{
				CLIPath:   cli,
				Account:   acct,
				AllowFrom: firstAny(sec["allow_from"], sec["allow_ids"]),
				AllowAll:  asBool(sec["allow_all"]),
				HomeDir:   home,
			}))
			registered = append(registered, "signal")
		}
	}

	return registered
}

// ClearMessengers stops and removes messenger adapters (keeps internal channels).
func ClearMessengers(gw *Gateway, ctxStop func(Channel) error) {
	if gw == nil {
		return
	}
	for _, kind := range gw.Channels() {
		if !IsMessenger(kind) {
			continue
		}
		ch := gw.GetChannel(kind)
		if ch != nil && ctxStop != nil {
			_ = ctxStop(ch)
		}
		gw.RemoveChannel(kind)
	}
}

func enabledSet(cfg map[string]any) map[string]struct{} {
	out := map[string]struct{}{}
	raw, _ := cfg["enabled_channels"]
	switch t := raw.(type) {
	case []string:
		for _, x := range t {
			if s := normalizeID(x); s != "" {
				out[s] = struct{}{}
			}
		}
	case []any:
		for _, x := range t {
			if s := normalizeID(fmt.Sprint(x)); s != "" {
				out[s] = struct{}{}
			}
		}
	case string:
		for _, p := range strings.Split(t, ",") {
			if s := normalizeID(p); s != "" {
				out[s] = struct{}{}
			}
		}
	}
	return out
}

func section(cfg map[string]any, key string) map[string]any {
	raw, ok := cfg[key]
	if !ok || raw == nil {
		return map[string]any{}
	}
	if m, ok := raw.(map[string]any); ok {
		return m
	}
	return map[string]any{}
}

func cfgString(sec map[string]any, key string) string {
	v, ok := sec[key]
	if !ok || v == nil {
		return ""
	}
	s := strings.TrimSpace(fmt.Sprint(v))
	if s == "<nil>" {
		return ""
	}
	return s
}

func asBool(v any) bool {
	switch t := v.(type) {
	case bool:
		return t
	case string:
		switch strings.ToLower(strings.TrimSpace(t)) {
		case "1", "true", "yes", "on":
			return true
		}
	case float64:
		return t != 0
	case int:
		return t != 0
	}
	return false
}

func firstAny(vals ...any) any {
	for _, v := range vals {
		if v == nil {
			continue
		}
		switch t := v.(type) {
		case string:
			if strings.TrimSpace(t) != "" {
				return t
			}
		case []any:
			if len(t) > 0 {
				return t
			}
		case []string:
			if len(t) > 0 {
				return t
			}
		default:
			return v
		}
	}
	return nil
}

func resolveSecret(cfg map[string]any, home, channel, field string) string {
	ch := normalizeID(channel)
	fk := strings.ToLower(strings.TrimSpace(field))
	// Env override: REMEDY_TELEGRAM__BOT_TOKEN
	envKey := "REMEDY_" + strings.ToUpper(ch) + "__" + strings.ToUpper(fk)
	if v := strings.TrimSpace(os.Getenv(envKey)); v != "" {
		return v
	}
	sec := section(cfg, ch)
	if v := strings.TrimSpace(fmt.Sprint(sec[fk])); v != "" && v != "<nil>" {
		return v
	}
	_ = home
	return ""
}
