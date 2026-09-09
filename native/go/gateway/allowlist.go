package gateway

import (
	"fmt"
	"log"
	"strings"
)

// ParseIDs splits a list or comma/semicolon-separated string into a set.
func ParseIDs(raw any) map[string]struct{} {
	out := map[string]struct{}{}
	switch t := raw.(type) {
	case nil:
		return out
	case []string:
		for _, x := range t {
			if s := strings.TrimSpace(x); s != "" {
				out[s] = struct{}{}
			}
		}
	case []any:
		for _, x := range t {
			if s := strings.TrimSpace(fmt.Sprint(x)); s != "" && s != "<nil>" {
				out[s] = struct{}{}
			}
		}
	case string:
		repl := strings.ReplaceAll(t, ";", ",")
		for _, p := range strings.Split(repl, ",") {
			if s := strings.TrimSpace(p); s != "" {
				out[s] = struct{}{}
			}
		}
	}
	return out
}

// IsAllowed is the legacy any-candidate allowlist check. It is kept for
// callers that only have a single identity to test (Signal / WhatsApp phone
// numbers). Messenger adapters with separate user and room ids must use
// Access.Permit so a room id can never stand in for a user identity.
func IsAllowed(allowlist map[string]struct{}, allowAll bool, candidates ...string) bool {
	if allowAll {
		return true
	}
	if len(allowlist) == 0 {
		return false
	}
	for _, c := range candidates {
		c = strings.TrimSpace(c)
		if c == "" {
			continue
		}
		if _, ok := allowlist[c]; ok {
			return true
		}
	}
	return false
}

// Access is the per-channel inbound policy.
//
// Users is the identity allowlist: when an event carries a user id, that id
// MUST be present here (unless AllowAll). Scopes restricts where the bot may
// respond (chat / room / space / guild ids); an empty Scopes means any scope.
// Scope ids are never sufficient identity on their own, so a configured
// channel_id / room_id / space_id is not copied into Users.
type Access struct {
	Users    map[string]struct{}
	Scopes   map[string]struct{}
	AllowAll bool
}

// NewAccess builds an Access from raw config values.
func NewAccess(users any, allowAll bool, scopes ...string) Access {
	a := Access{Users: ParseIDs(users), Scopes: map[string]struct{}{}, AllowAll: allowAll}
	for _, s := range scopes {
		if s = strings.TrimSpace(s); s != "" {
			a.Scopes[s] = struct{}{}
		}
	}
	return a
}

// Permit decides whether a message from userID inside the given scope ids may
// be handled. The reason is a short token for a single log line.
//
// Rules (fail closed):
//   - a scope restriction applies whenever Scopes is non-empty;
//   - AllowAll admits any user inside the permitted scope;
//   - an empty Users set denies everyone (secure default);
//   - a missing user id (channel posts, anonymous admins) is denied;
//   - otherwise userID must be in Users.
func (a Access) Permit(userID string, scopeIDs ...string) (bool, string) {
	if len(a.Scopes) > 0 {
		inScope := false
		for _, sc := range scopeIDs {
			sc = strings.TrimSpace(sc)
			if sc == "" {
				continue
			}
			if _, ok := a.Scopes[sc]; ok {
				inScope = true
				break
			}
		}
		if !inScope {
			return false, "scope"
		}
	}
	if a.AllowAll {
		return true, ""
	}
	if len(a.Users) == 0 {
		return false, "empty-allowlist"
	}
	userID = strings.TrimSpace(userID)
	if userID == "" {
		return false, "no-user-id"
	}
	if _, ok := a.Users[userID]; ok {
		return true, ""
	}
	return false, "user-not-allowlisted"
}

// Empty reports whether nothing could ever be admitted (no users, no allow_all).
func (a Access) Empty() bool {
	return !a.AllowAll && len(a.Users) == 0
}

// FirstUser returns an arbitrary allowlisted user id (used as a default
// outbound target when a channel has no configured scope).
func (a Access) FirstUser() string {
	for id := range a.Users {
		return id
	}
	return ""
}

// LogAccessSummary writes the one startup line that tells the owner who this
// channel will answer. A scope id (channel / room / space / team) is never an
// identity, so a channel configured with only a scope answers nobody — say so
// plainly instead of going quiet.
func LogAccessSummary(channel ChannelKind, a Access, scope string) {
	if a.Empty() {
		log.Printf("%s: active (%s) but no allow list — every inbound message will be ignored. "+
			"Add the sender's user id to allow_ids (a channel/room id is scope, not identity), "+
			"or set allow_all = true.", channel, scope)
		return
	}
	log.Printf("%s: active (%s allowlist=%d allow_all=%v)", channel, scope, len(a.Users), a.AllowAll)
}

// logDeny writes the single deny line for a rejected inbound message.
func logDeny(channel ChannelKind, reason, userID, scopeID string) {
	log.Printf("%s: ignore user_id=%s chat_id=%s (%s)", channel, userID, scopeID, reason)
}
