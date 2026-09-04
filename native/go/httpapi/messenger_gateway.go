package httpapi

import (
	"context"
	"fmt"
	"log"
	"os"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/gateway"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// startMessengerGateway builds/refreshes the messenger hub from on-disk settings.
func (s *Server) startMessengerGateway() {
	if s == nil {
		return
	}
	if s.messengerGW == nil {
		s.messengerGW = gateway.New(gateway.Config{
			HomeDir:           ResolveHomeDir(s.homeDir),
			HeartbeatInterval: 60 * time.Second,
			RateLimitPerMin:   60,
		})
		s.messengerGW.RegisterHandler(s.handleMessengerEvent)
	}
	s.reloadMessengerChannels()
	if !s.messengerGW.Running() {
		ctx := context.Background()
		if err := s.messengerGW.Start(ctx); err != nil {
			log.Printf("messenger gateway start: %v", err)
		}
	}
}

func (s *Server) stopMessengerGateway() {
	if s == nil || s.messengerGW == nil {
		return
	}
	_ = s.messengerGW.Stop(context.Background())
}

func (s *Server) refreshMessengerAfterSettings() {
	if s == nil {
		return
	}
	if s.messengerGW == nil {
		s.startMessengerGateway()
		return
	}
	s.reloadMessengerChannels()
}

func (s *Server) reloadMessengerChannels() {
	if s == nil || s.messengerGW == nil {
		return
	}
	home := ResolveHomeDir(s.homeDir)
	cfg := map[string]any(LoadConfig(s.homeDir))
	wasRunning := s.messengerGW.Running()

	gateway.ClearMessengers(s.messengerGW, func(ch gateway.Channel) error {
		return ch.Stop(context.Background())
	})
	registered := gateway.RegisterFromConfig(s.messengerGW, cfg, home, func(channel, field string) string {
		return resolveMessengerSecret(cfg, home, channel, field)
	})
	if len(registered) > 0 {
		log.Printf("messenger channels active: %s", strings.Join(registered, ", "))
	}
	if wasRunning {
		for _, kind := range s.messengerGW.Channels() {
			if !gateway.IsMessenger(kind) {
				continue
			}
			ch := s.messengerGW.GetChannel(kind)
			if ch != nil && !ch.Running() {
				if err := ch.Start(context.Background()); err != nil {
					log.Printf("messenger start %s after reload: %v", kind, err)
				}
			}
		}
	}
}

func resolveMessengerSecret(cfg map[string]any, home, channel, field string) string {
	ch := strings.ToLower(strings.TrimSpace(channel))
	fk := strings.ToLower(strings.TrimSpace(field))
	storeKey := "ch:" + ch + ":" + fk
	if v := strings.TrimSpace(secret.GetProviderSecret(home, storeKey)); v != "" {
		return v
	}
	if sec, ok := cfg[ch].(map[string]any); ok {
		if v := strings.TrimSpace(fmt.Sprint(sec[fk])); v != "" && v != "<nil>" {
			return v
		}
	}
	envKey := "REMEDY_" + strings.ToUpper(ch) + "__" + strings.ToUpper(fk)
	return strings.TrimSpace(lookupEnv(envKey))
}

func lookupEnv(key string) string {
	return strings.TrimSpace(os.Getenv(key))
}

// handleMessengerEvent bridges inbound messenger traffic into sessions + turns
// and publishes desktop session-events SSE (Python session_bridge parity).
func (s *Server) handleMessengerEvent(ctx context.Context, ev gateway.Event) error {
	if ev.Kind != gateway.EventMessage {
		return nil
	}
	if !gateway.IsMessenger(ev.Channel) {
		return nil
	}
	msg, _ := ev.Payload["message"].(string)
	msg = strings.TrimSpace(msg)
	if msg == "" {
		return nil
	}
	chatID, _ := ev.Payload["chat_id"].(string)
	if chatID == "" {
		chatID, _ = ev.Payload["channel_id"].(string)
	}
	if chatID == "" {
		chatID = ev.SessionID
	}
	username, _ := ev.Payload["username"].(string)

	sess, err := s.resolveMessengerSession(string(ev.Channel), chatID, username, msg)
	if err != nil || sess.ID == "" {
		return err
	}
	if s.sessions != nil {
		if _, err := s.sessions.AddMessage(sess.ID, "user", msg, nil, nil); err != nil {
			return err
		}
		title := sess.Title
		count := sess.MessageCount + 1
		role := "user"
		oc := string(ev.Channel)
		s.publishSessionEvent(SessionEvent{
			Type:          "message_added",
			SessionID:     sess.ID,
			OriginChannel: &oc,
			Title:         &title,
			MessageCount:  &count,
			Role:          &role,
		})
	}

	if s.runner == nil {
		return nil
	}
	epoch, claimCtx, claimed := s.claims.TryClaim(sess.ID)
	if !claimed {
		_, _ = s.messengerGW.SendTo(ctx, ev.Channel,
			"I'm still working on your last message — hang tight.", chatID)
		return nil
	}
	defer s.claims.Release(sess.ID, &epoch)

	runCtx := claimCtx
	if runCtx == nil {
		runCtx = ctx
	}
	s.messengerGW.SendTyping(runCtx, ev.Channel, chatID)

	var reply strings.Builder
	lastTyping := time.Now()
	turnErr := s.runner.RunTurn(runCtx, TurnRequest{
		SessionID: sess.ID,
		Prompt:    msg,
		Model:     sess.Model,
		Provider:  sess.LLMProvider,
	}, func(token string) error {
		if strings.HasPrefix(token, "@@") {
			return nil
		}
		reply.WriteString(token)
		if time.Since(lastTyping) > 4*time.Second {
			lastTyping = time.Now()
			s.messengerGW.SendTyping(runCtx, ev.Channel, chatID)
		}
		return nil
	})
	text := strings.TrimSpace(reply.String())
	if turnErr != nil && text == "" {
		log.Printf("messenger turn error: %v", turnErr)
		return nil
	}
	if text == "" {
		text = "Processed."
	}
	if s.sessions != nil {
		if _, err := s.sessions.AddMessage(sess.ID, "assistant", text, sess.Model, nil); err == nil {
			if fresh, ok, _ := s.sessions.Get(sess.ID); ok {
				title := fresh.Title
				count := fresh.MessageCount
				role := "assistant"
				oc := string(ev.Channel)
				s.publishSessionEvent(SessionEvent{
					Type:          "message_added",
					SessionID:     sess.ID,
					OriginChannel: &oc,
					Title:         &title,
					MessageCount:  &count,
					Role:          &role,
				})
			}
		}
	}
	for _, part := range gateway.SplitMessage(text, ev.Channel) {
		_, _ = s.messengerGW.SendTo(runCtx, ev.Channel, part, chatID)
	}
	return nil
}

func (s *Server) resolveMessengerSession(channel, chatID, username, firstMessage string) (ChatSession, error) {
	if s.sessions == nil {
		return ChatSession{}, fmt.Errorf("no session store")
	}
	sid := gateway.ExternalSessionID(channel, chatID, "")
	if existing, ok, err := s.sessions.Get(sid); err != nil {
		return ChatSession{}, err
	} else if ok {
		return existing, nil
	}
	if byExt, ok, err := s.sessions.FindByOrigin(channel, chatID); err != nil {
		return ChatSession{}, err
	} else if ok {
		return byExt, nil
	}
	title := gateway.HeuristicSessionTitle(gateway.ChannelKind(channel), username, "", firstMessage)
	oc := channel
	ext := chatID
	var user *string
	if strings.TrimSpace(username) != "" {
		u := strings.TrimSpace(username)
		if len(u) > 120 {
			u = u[:120]
		}
		user = &u
	}
	sess, err := s.sessions.CreateMessenger(sid, title, oc, ext, user)
	if err != nil {
		return ChatSession{}, err
	}
	s.publishSessionEvent(SessionEvent{
		Type:          "session_created",
		SessionID:     sess.ID,
		OriginChannel: &oc,
		Title:         &title,
	})
	return sess, nil
}

// mirrorDesktopReply pushes an assistant reply to the session origin messenger.
func (s *Server) mirrorDesktopReply(sess ChatSession, text string) {
	if s == nil || s.messengerGW == nil || sess.OriginChannel == nil || sess.ExternalChatID == nil {
		return
	}
	ch := strings.TrimSpace(*sess.OriginChannel)
	target := strings.TrimSpace(*sess.ExternalChatID)
	if ch == "" || target == "" {
		return
	}
	gateway.MirrorOutbound(context.Background(), s.messengerGW, ch, target, text)
}
