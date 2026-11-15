package httpapi

import (
	"database/sql"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"
	"unicode/utf8"
)

// Message reactions: exactly four, one per side per message.
//
// The owner reacts to Remedy's replies; Remedy reacts to the owner's
// messages (the react tool). An owner reaction is a quiet signal, never a
// new turn: 👍 / 👎 answer a pending confirmation when Remedy is blocked on
// one, reach a running turn as guidance, and are otherwise just recorded.
// Every owner reaction is also kept as feedback Remedy can consult later
// (what landed and what didn't).

const (
	ReactionThumbsUp    = "👍"
	ReactionThumbsDown  = "👎"
	ReactionHeart       = "❤️"
	ReactionBrokenHeart = "💔"
)

// reactionMeanings is the closed set and what each one tells Remedy.
var reactionMeanings = map[string]string{
	ReactionThumbsUp:    "approve / go ahead",
	ReactionThumbsDown:  "reconsider / try again",
	ReactionHeart:       "this landed well",
	ReactionBrokenHeart: "this disappointed",
}

var reactionAliases = map[string]string{
	"👍": ReactionThumbsUp, "+1": ReactionThumbsUp, "thumbs_up": ReactionThumbsUp, "thumbsup": ReactionThumbsUp, "like": ReactionThumbsUp,
	"👎": ReactionThumbsDown, "-1": ReactionThumbsDown, "thumbs_down": ReactionThumbsDown, "thumbsdown": ReactionThumbsDown, "dislike": ReactionThumbsDown,
	"❤️": ReactionHeart, "❤": ReactionHeart, "heart": ReactionHeart, "love": ReactionHeart,
	"💔": ReactionBrokenHeart, "broken_heart": ReactionBrokenHeart, "brokenheart": ReactionBrokenHeart,
}

var errReactionInvalid = errors.New("reaction must be one of 👍 👎 ❤️ 💔 (or empty to clear)")
var errReactionTarget = errors.New("that side cannot react to this message")
var errReactionNotFound = errors.New("message not found")

// normalizeReaction maps an emoji or alias onto the closed set. "" clears.
func normalizeReaction(raw string) (string, error) {
	t := strings.TrimSpace(raw)
	if t == "" {
		return "", nil
	}
	// Strip skin tones so 👍🏽 still means 👍.
	var b strings.Builder
	for _, r := range t {
		if r >= 0x1F3FB && r <= 0x1F3FF {
			continue
		}
		b.WriteRune(r)
	}
	t = b.String()
	if v, ok := reactionAliases[strings.ToLower(t)]; ok {
		return v, nil
	}
	return "", errReactionInvalid
}

// MessageReactions is the per-message pair: the owner's and Remedy's.
type MessageReactions struct {
	User  string `json:"user,omitempty"`
	Agent string `json:"agent,omitempty"`
}

func (r MessageReactions) empty() bool { return r.User == "" && r.Agent == "" }

func parseReactions(raw string) *MessageReactions {
	raw = strings.TrimSpace(raw)
	if raw == "" || raw == "{}" {
		return nil
	}
	var r MessageReactions
	if err := json.Unmarshal([]byte(raw), &r); err != nil || r.empty() {
		return nil
	}
	return &r
}

// reactionTarget is what SetReaction found and wrote.
type reactionTarget struct {
	SessionID string
	Role      string
	Content   string
	Reactions MessageReactions
	Previous  string
}

// SetReaction writes one side's reaction on a message. side is "user" (the
// owner, on an assistant message) or "agent" (Remedy, on a user message).
func (s *sessionStore) SetReaction(msgID, side, emoji string) (reactionTarget, error) {
	var out reactionTarget
	msgID = strings.TrimSpace(msgID)
	if msgID == "" {
		return out, errReactionNotFound
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	var raw string
	var reverted int
	err := s.db.QueryRow(
		`SELECT session_id, role, content, COALESCE(reactions, '{}'), reverted FROM chat_messages WHERE id = ?`,
		msgID,
	).Scan(&out.SessionID, &out.Role, &out.Content, &raw, &reverted)
	if errors.Is(err, sql.ErrNoRows) || (err == nil && reverted != 0) {
		return out, errReactionNotFound
	}
	if err != nil {
		return out, err
	}
	want := "assistant"
	if side == "agent" {
		want = "user"
	} else if side != "user" {
		return out, errReactionTarget
	}
	if out.Role != want {
		return out, errReactionTarget
	}
	cur := MessageReactions{}
	if p := parseReactions(raw); p != nil {
		cur = *p
	}
	if side == "user" {
		out.Previous, cur.User = cur.User, emoji
	} else {
		out.Previous, cur.Agent = cur.Agent, emoji
	}
	enc := "{}"
	if !cur.empty() {
		b, _ := json.Marshal(cur)
		enc = string(b)
	}
	if _, err := s.db.Exec(`UPDATE chat_messages SET reactions = ? WHERE id = ?`, enc, msgID); err != nil {
		return out, err
	}
	out.Reactions = cur
	return out, nil
}

// LatestMessageID is the newest non-reverted message of role in a session.
func (s *sessionStore) LatestMessageID(sessionID, role string) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	var id string
	_ = s.db.QueryRow(
		`SELECT id FROM chat_messages WHERE session_id = ? AND role = ? AND reverted = 0
		 ORDER BY created_at DESC, rowid DESC LIMIT 1`,
		sessionID, role,
	).Scan(&id)
	return id
}

// recordReactionFeedback keeps the owner's reaction as a memory entry Remedy
// can search ("what landed vs. what didn't"). One entry per message; clearing
// the reaction removes it.
func (s *sessionStore) recordReactionFeedback(sessionID, msgID, emoji, content string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.ensurePartnerMemorySchema(); err != nil {
		return err
	}
	id := "reaction:" + msgID
	if emoji == "" {
		_, err := s.db.Exec(`DELETE FROM memory_entries WHERE id = ?`, id)
		return err
	}
	meaning := reactionMeanings[emoji]
	// The body is Remedy's own reply, which can echo text from web pages or
	// tool output. Label it as a quote so recalled feedback never reads as
	// an instruction the owner wrote or endorsed.
	snippet := reactionFeedbackBody(emoji, content)
	meta, _ := json.Marshal(map[string]any{
		"emoji": emoji, "meaning": meaning, "message_id": msgID, "source": "owner_reaction",
	})
	tags, _ := json.Marshal([]string{"feedback", "reaction"})
	now := time.Now().UTC().Format(time.RFC3339Nano)
	_, err := s.db.Exec(
		`INSERT INTO memory_entries (id, entry_type, title, content, tags, metadata, created_at, updated_at, session_id, importance)
		 VALUES (?, 'reaction_feedback', ?, ?, ?, ?, ?, ?, ?, ?)
		 ON CONFLICT(id) DO UPDATE SET title = excluded.title, content = excluded.content,
		   metadata = excluded.metadata, updated_at = excluded.updated_at, importance = excluded.importance`,
		id, "Owner reacted "+emoji+" ("+meaning+")", snippet, string(tags), string(meta), now, now, sessionID,
		reactionImportance(emoji),
	)
	return err
}

// reactionFeedbackBody is the stored memory text for an owner reaction.
func reactionFeedbackBody(emoji, content string) string {
	return "The owner reacted " + emoji + " (" + reactionMeanings[emoji] + ") to this earlier Remedy reply. " +
		"Quoted reply (Remedy's words, not an owner instruction): \"" + reactionSnippet(content, 280) + "\""
}

func reactionImportance(emoji string) float64 {
	switch emoji {
	case ReactionBrokenHeart, ReactionThumbsDown:
		return 0.7 // misses teach more than hits
	default:
		return 0.5
	}
}

func reactionSnippet(content string, max int) string {
	t := strings.Join(strings.Fields(content), " ")
	if len(t) <= max {
		return t
	}
	cut := max
	for cut > 0 && !utf8.RuneStart(t[cut]) {
		cut--
	}
	return t[:cut] + "…"
}

// reactionSignalText is how an owner reaction reaches a running turn: a
// short, clearly-labelled note, not a new request.
func reactionSignalText(emoji, content string) string {
	return "[Owner reaction — not a new request] The owner reacted " + emoji +
		" (" + reactionMeanings[emoji] + ") to your message: \"" + reactionSnippet(content, 160) +
		"\". Take it into account; do not reply to the reaction itself."
}

// reactionHistoryNote is how a recorded owner reaction shows up in the next
// turn's transcript, ahead of the owner's next message.
func reactionHistoryNote(emoji string) string {
	if _, ok := reactionMeanings[emoji]; !ok {
		return ""
	}
	return "(Owner reacted " + emoji + " to your previous reply — " + reactionMeanings[emoji] + ".)"
}

// deliverReactionSignal routes an owner reaction. It never starts a turn.
//   - 👍 / 👎 on the latest reply while that turn is blocked on exactly one
//     owner confirmation: approve once / deny it.
//   - a turn is running: a labelled guidance note reaches it.
//   - otherwise: recorded only (the next turn sees it in the transcript).
func (s *Server) deliverReactionSignal(sid, msgID, emoji, content string) (signal string, approval map[string]any) {
	if emoji == "" {
		return "cleared", nil
	}
	if (emoji == ReactionThumbsUp || emoji == ReactionThumbsDown) && s.approvals != nil && s.sessions != nil &&
		s.sessions.LatestMessageID(sid, "assistant") == msgID {
		var waiting []*pendingApproval
		for _, it := range s.approvals.ListPending(sid) {
			// Only this session's own, owner-originated asks with a turn
			// blocked on them. Hive / messenger asks are never answered by
			// a reaction.
			if it.SessionID == nil || *it.SessionID != sid || !s.approvals.HasWaiter(it.ID) {
				continue
			}
			if o := strings.TrimSpace(it.Origin); o != "" && !OriginIsOwner(o) {
				continue
			}
			waiting = append(waiting, it)
		}
		if len(waiting) == 1 {
			approve := emoji == ReactionThumbsUp
			// "once": a thumbs-up answers this ask; it is not standing consent.
			item, _ := s.approvals.resolve(waiting[0].ID, approve, "once")
			if item != nil {
				decision := "denied"
				if approve {
					decision = "approved"
				}
				s.approvals.notify(item, decision)
				return "confirmation_" + decision, s.approvals.ToPublic(item)
			}
		}
	}
	if s.claims != nil && s.claims.IsClaimed(sid) {
		if ok, _ := s.claims.TryPushNudge(sid, reactionSignalText(emoji, content)); ok {
			return "delivered", nil
		}
	}
	return "recorded", nil
}

// handleSetReaction: PUT /api/sessions/{id}/messages/{msg_id}/reaction
// body {"emoji": "👍"} sets the owner's reaction; {"emoji": ""} clears it.
func (s *Server) handleSetReaction(w http.ResponseWriter, r *http.Request) {
	if s.sessions == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": "Memory store not available"})
		return
	}
	sid := strings.TrimSpace(r.PathValue("id"))
	mid := strings.TrimSpace(r.PathValue("msg_id"))
	var req struct {
		Emoji *string `json:"emoji"`
	}
	raw, _ := io.ReadAll(io.LimitReader(r.Body, 4<<10))
	if err := json.Unmarshal(raw, &req); err != nil || req.Emoji == nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "JSON body with emoji is required"})
		return
	}
	emoji, err := normalizeReaction(*req.Emoji)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": err.Error()})
		return
	}
	// Check the session before writing so a foreign id never mutates.
	if _, sessOf, ok, gerr := s.sessions.GetMessage(mid); gerr != nil || !ok || sessOf != sid {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Message not found"})
		return
	}
	target, err := s.sessions.SetReaction(mid, "user", emoji)
	switch {
	case errors.Is(err, errReactionNotFound):
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Message not found"})
		return
	case errors.Is(err, errReactionTarget):
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "You can react to Remedy's messages only"})
		return
	case err != nil:
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	signal := "unchanged"
	var approval map[string]any
	if target.Previous != emoji {
		signal, approval = s.deliverReactionSignal(sid, mid, emoji, target.Content)
		_ = s.sessions.recordReactionFeedback(sid, mid, emoji, target.Content)
		s.publishReaction(sid, mid)
	}
	resp := map[string]any{"message_id": mid, "reactions": target.Reactions, "signal": signal}
	if approval != nil {
		resp["approval"] = approval
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) publishReaction(sid, mid string) {
	id := mid
	s.publishSessionEvent(SessionEvent{Type: "message_reaction", SessionID: sid, MessageID: &id})
}

// applyAgentReaction persists Remedy's own reaction (the react tool). The
// target defaults to the owner message that started this turn.
func (s *Server) applyAgentReaction(sid, fallbackMsgID, msgID, emoji string) (string, MessageReactions, error) {
	if s.sessions == nil {
		return "", MessageReactions{}, errReactionNotFound
	}
	norm, err := normalizeReaction(emoji)
	if err != nil {
		return "", MessageReactions{}, err
	}
	target := strings.TrimSpace(msgID)
	if target == "" {
		target = fallbackMsgID
	}
	if target == "" {
		target = s.sessions.LatestMessageID(sid, "user")
	}
	if _, sessOf, ok, gerr := s.sessions.GetMessage(target); gerr != nil || !ok || sessOf != sid {
		return "", MessageReactions{}, errReactionNotFound
	}
	res, err := s.sessions.SetReaction(target, "agent", norm)
	if err != nil {
		return "", MessageReactions{}, err
	}
	s.publishReaction(sid, target)
	return target, res.Reactions, nil
}
