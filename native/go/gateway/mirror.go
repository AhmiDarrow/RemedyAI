package gateway

import (
	"context"
	"log"
	"strings"
)

// MirrorOutbound pushes a desktop assistant reply to the session's origin messenger.
// Best-effort; never returns a hard error to the chat stream.
func MirrorOutbound(ctx context.Context, g *Gateway, channel, target, text string) bool {
	if g == nil || strings.TrimSpace(text) == "" {
		return false
	}
	ch := ChannelKind(normalizeID(channel))
	target = strings.TrimSpace(target)
	if !IsMessenger(ch) || target == "" {
		return false
	}
	okAny := false
	for _, part := range SplitMessage(text, ch) {
		ok, err := g.SendTo(ctx, ch, part, target)
		if err != nil {
			log.Printf("gateway: mirror send %s: %s", ch, SafeErr(err))
			continue
		}
		okAny = okAny || ok
	}
	if okAny {
		log.Printf("gateway: mirrored desktop reply to %s chat_id=%s (%d chars)", ch, target, len(text))
	} else {
		log.Printf("gateway: mirror to %s chat_id=%s failed (channel down or send error)", ch, target)
	}
	return okAny
}
