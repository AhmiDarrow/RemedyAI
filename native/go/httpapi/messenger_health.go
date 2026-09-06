package httpapi

import (
	"fmt"
	"net/url"
	"os"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/gateway"
)

// publicHTTPSTunnelConfigured reports whether the owner has declared a public
// HTTPS base URL for webhook messengers (WhatsApp / Teams / Google Chat).
// The local API stays loopback-only; inbound webhooks need an external tunnel.
func publicHTTPSTunnelConfigured() bool {
	for _, key := range []string{"REMEDY_PUBLIC_BASE_URL", "REMEDY_WEBHOOK_PUBLIC_URL"} {
		raw := strings.TrimSpace(os.Getenv(key))
		if raw == "" {
			continue
		}
		u, err := url.Parse(raw)
		if err != nil {
			continue
		}
		if strings.EqualFold(u.Scheme, "https") && u.Host != "" {
			return true
		}
	}
	return false
}

func secretPresent(keysSet map[string]bool, section map[string]any, channel, field string) bool {
	if keysSet["ch:"+channel+":"+field] {
		return true
	}
	if section == nil {
		return false
	}
	v := strings.TrimSpace(fmt.Sprint(section[field]))
	return v != "" && v != "<nil>"
}

func sectionString(section map[string]any, key string) string {
	if section == nil {
		return ""
	}
	v := strings.TrimSpace(fmt.Sprint(section[key]))
	if v == "<nil>" {
		return ""
	}
	return v
}

// liveMessengerHealth overrides catalog "partial" with ready | needs_setup and
// a plain-language reason. Never returns "partial".
func liveMessengerHealth(
	entry messengerCatalogEntry,
	enabled bool,
	tokenSet bool,
	section map[string]any,
	keysSet map[string]bool,
	home string,
) (status, reason string, health map[string]any) {
	id := entry.ID
	catalog := strings.TrimSpace(entry.Status)
	if catalog == "planned" {
		return "planned", "Not available yet.", nil
	}

	switch id {
	case "whatsapp":
		tok := secretPresent(keysSet, section, id, "access_token")
		phone := sectionString(section, "phone_number_id")
		if !tok || phone == "" {
			return "needs_setup", "Add the Cloud API access token and phone number ID.", nil
		}
		if !publicHTTPSTunnelConfigured() {
			return "needs_setup", "Needs a public HTTPS tunnel (local API is loopback-only). Use Expose messenger webhooks in Settings, or set REMEDY_PUBLIC_BASE_URL.", nil
		}
		return "ready", "", nil

	case "teams":
		appID := sectionString(section, "app_id")
		pwd := secretPresent(keysSet, section, id, "app_password")
		if appID == "" || !pwd {
			return "needs_setup", "Add the Teams app (client) ID and app password.", nil
		}
		if !publicHTTPSTunnelConfigured() {
			return "needs_setup", "Needs a public HTTPS tunnel (local API is loopback-only). Use Expose messenger webhooks in Settings, or set REMEDY_PUBLIC_BASE_URL.", nil
		}
		return "ready", "", nil

	case "google_chat":
		tok := secretPresent(keysSet, section, id, "access_token")
		refresh := secretPresent(keysSet, section, id, "refresh_token")
		clientID := secretPresent(keysSet, section, id, "oauth_client_id") || sectionString(section, "oauth_client_id") != ""
		clientSecret := secretPresent(keysSet, section, id, "oauth_client_secret")
		oauthClient := clientID && clientSecret
		health = map[string]any{
			"token_set":    tok,
			"refresh_set":  refresh,
			"oauth_client": oauthClient,
		}
		if !tok && !(refresh && oauthClient) {
			return "needs_setup", "Add a Google Chat access token, or a refresh token plus OAuth client ID and secret.", health
		}
		if !publicHTTPSTunnelConfigured() {
			return "needs_setup", "Needs a public HTTPS tunnel (local API is loopback-only). Use Expose messenger webhooks in Settings, or set REMEDY_PUBLIC_BASE_URL.", health
		}
		if refresh && !oauthClient {
			return "needs_setup", "Refresh token is set — add OAuth client ID and secret so Remedy can renew the access token.", health
		}
		if !refresh || !oauthClient {
			return "needs_setup", "Access token works briefly; add refresh token + OAuth client ID/secret for lasting setup.", health
		}
		return "ready", "", health

	case "signal":
		cliPath := sectionString(section, "cli_path")
		if cliPath == "" {
			cliPath = "signal-cli"
		}
		cliOK := gateway.ResolveSignalCLIForHealth(cliPath) != "" || gateway.LookupManagedSignalCLI(home) != ""
		accountOK := sectionString(section, "account") != ""
		needsJava := gateway.SignalCLINeedsJava()
		javaOK := gateway.SignalCLIJavaOK()
		health = map[string]any{
			"cli_ok":     cliOK,
			"account_ok": accountOK,
			"needs_java": needsJava,
			"java_ok":    javaOK,
		}
		if url := gateway.SignalCLIDownloadURL(); url != "" {
			health["download_url"] = url
		}
		health["managed"] = gateway.LookupManagedSignalCLI(home) != ""
		if !cliOK {
			if needsJava {
				return "needs_setup", "signal-cli is missing. Use Install signal-cli in Settings (needs Java 21+), or set cli_path.", health
			}
			return "needs_setup", "signal-cli is missing. Use Install signal-cli in Settings, or set cli_path.", health
		}
		if needsJava && !javaOK {
			return "needs_setup", "Install Java 21+ (Temurin) and restart Remedy so signal-cli can run.", health
		}
		if !accountOK {
			return "needs_setup", "Set the Signal account phone number.", health
		}
		return "ready", "", health

	case "mattermost":
		tok := secretPresent(keysSet, section, id, "bot_token")
		base := sectionString(section, "base_url")
		if enabled && (!tok || base == "") {
			return "needs_setup", "Add the Mattermost server URL and bot token.", nil
		}
		return "ready", "", nil

	case "matrix":
		tok := secretPresent(keysSet, section, id, "access_token")
		hs := sectionString(section, "homeserver")
		if enabled && (!tok || hs == "") {
			return "needs_setup", "Add the Matrix homeserver URL and access token.", nil
		}
		return "ready", "", nil

	case "slack":
		bot := secretPresent(keysSet, section, id, "bot_token")
		app := secretPresent(keysSet, section, id, "app_token")
		if enabled && (!bot || !app) {
			return "needs_setup", "Add both the Slack bot token and app-level token (Socket Mode).", nil
		}
		return "ready", "", nil

	default:
		// telegram, discord, and other ready adapters
		if enabled && !tokenSet {
			return "needs_setup", "Add the bot token to finish setup.", nil
		}
		// Never leak catalog "partial" to the UI.
		if catalog == "partial" || catalog == "" {
			return "ready", "", nil
		}
		return "ready", "", nil
	}
}
