package gateway

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"
)

// RESTOutChannel is an outbound-capable messenger without a live inbound loop.
// Used for Slack/Mattermost/Matrix/WhatsApp/Teams/Google Chat/Signal until
// their inbound transports are ported; desktop→messenger mirror still works.
type RESTOutChannel struct {
	kind     ChannelKind
	sendFn   func(ctx context.Context, client *http.Client, message, target string) (bool, error)
	client   *http.Client
	mu       sync.Mutex
	running  bool
	defaultT string
}

func newRESTOut(kind ChannelKind, defaultTarget string, send func(ctx context.Context, client *http.Client, message, target string) (bool, error)) *RESTOutChannel {
	return &RESTOutChannel{
		kind:     kind,
		sendFn:   send,
		defaultT: strings.TrimSpace(defaultTarget),
		client:   &http.Client{Timeout: 30 * time.Second},
	}
}

func (c *RESTOutChannel) Kind() ChannelKind { return c.kind }

func (c *RESTOutChannel) Running() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.running
}

func (c *RESTOutChannel) Start(ctx context.Context) error {
	_ = ctx
	c.mu.Lock()
	c.running = true
	c.mu.Unlock()
	log.Printf("%s: outbound-ready (inbound not yet ported to Go)", c.kind)
	return nil
}

func (c *RESTOutChannel) Stop(ctx context.Context) error {
	_ = ctx
	c.mu.Lock()
	c.running = false
	c.mu.Unlock()
	return nil
}

func (c *RESTOutChannel) Send(ctx context.Context, message, target string) (bool, error) {
	if c.sendFn == nil {
		return false, nil
	}
	t := strings.TrimSpace(target)
	if t == "" {
		t = c.defaultT
	}
	if t == "" {
		return false, nil
	}
	return c.sendFn(ctx, c.client, message, t)
}

func jsonPOST(ctx context.Context, client *http.Client, url string, headers map[string]string, body any) (int, error) {
	raw, err := json.Marshal(body)
	if err != nil {
		return 0, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(raw))
	if err != nil {
		return 0, err
	}
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := client.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<20))
	return resp.StatusCode, nil
}

// NewSlackOut builds Slack chat.postMessage outbound.
func NewSlackOut(botToken, channelID string) Channel {
	tok := strings.TrimSpace(botToken)
	return newRESTOut(ChannelSlack, channelID, func(ctx context.Context, client *http.Client, message, target string) (bool, error) {
		if tok == "" {
			return true, nil
		}
		status, err := jsonPOST(ctx, client, "https://slack.com/api/chat.postMessage",
			map[string]string{"Authorization": "Bearer " + tok},
			map[string]any{"channel": target, "text": trimRunes(message, 3000)},
		)
		return status == 200, err
	})
}

// NewMattermostOut builds Mattermost REST posts.
func NewMattermostOut(baseURL, botToken, channelID string) Channel {
	base := strings.TrimRight(strings.TrimSpace(baseURL), "/")
	tok := strings.TrimSpace(botToken)
	return newRESTOut(ChannelMattermost, channelID, func(ctx context.Context, client *http.Client, message, target string) (bool, error) {
		if tok == "" || base == "" {
			return true, nil
		}
		status, err := jsonPOST(ctx, client, base+"/api/v4/posts",
			map[string]string{"Authorization": "Bearer " + tok},
			map[string]any{"channel_id": target, "message": trimRunes(message, 4000)},
		)
		return status == 201 || status == 200, err
	})
}

// NewMatrixOut builds Matrix room send outbound.
func NewMatrixOut(homeserver, accessToken, roomID string) Channel {
	hs := strings.TrimRight(strings.TrimSpace(homeserver), "/")
	tok := strings.TrimSpace(accessToken)
	return newRESTOut(ChannelMatrix, roomID, func(ctx context.Context, client *http.Client, message, target string) (bool, error) {
		if tok == "" || hs == "" {
			return true, nil
		}
		txn := NewEventID()
		url := hs + "/_matrix/client/v3/rooms/" + target + "/send/m.room.message/" + txn
		status, err := jsonPOST(ctx, client, url,
			map[string]string{"Authorization": "Bearer " + tok},
			map[string]any{"msgtype": "m.text", "body": trimRunes(message, 4000)},
		)
		return status == 200, err
	})
}

// NewWhatsAppOut builds WhatsApp Cloud API outbound.
func NewWhatsAppOut(accessToken, phoneNumberID string) Channel {
	tok := strings.TrimSpace(accessToken)
	phone := strings.TrimSpace(phoneNumberID)
	return newRESTOut(ChannelWhatsApp, "", func(ctx context.Context, client *http.Client, message, target string) (bool, error) {
		if tok == "" || phone == "" {
			return true, nil
		}
		status, err := jsonPOST(ctx, client,
			"https://graph.facebook.com/v18.0/"+phone+"/messages",
			map[string]string{"Authorization": "Bearer " + tok},
			map[string]any{
				"messaging_product": "whatsapp",
				"to":                target,
				"type":              "text",
				"text":              map[string]any{"body": trimRunes(message, 4096)},
			},
		)
		return status == 200, err
	})
}

// NewGoogleChatOut builds Google Chat spaces messages outbound.
func NewGoogleChatOut(accessToken, spaceID string) Channel {
	tok := strings.TrimSpace(accessToken)
	return newRESTOut(ChannelGoogleChat, spaceID, func(ctx context.Context, client *http.Client, message, target string) (bool, error) {
		if tok == "" {
			return true, nil
		}
		space := target
		if !strings.HasPrefix(space, "spaces/") {
			space = "spaces/" + space
		}
		status, err := jsonPOST(ctx, client,
			"https://chat.googleapis.com/v1/"+space+"/messages",
			map[string]string{"Authorization": "Bearer " + tok},
			map[string]any{"text": trimRunes(message, 4096)},
		)
		return status == 200, err
	})
}

// NewTeamsOut is a stub outbound (Bot Framework token exchange not ported yet).
func NewTeamsOut(appID, appPassword, defaultConv string) Channel {
	_ = appPassword
	return newRESTOut(ChannelTeams, defaultConv, func(ctx context.Context, client *http.Client, message, target string) (bool, error) {
		_ = ctx
		_ = client
		_ = message
		_ = target
		if strings.TrimSpace(appID) == "" {
			return true, nil
		}
		log.Printf("teams: outbound stub (Bot Framework token exchange not yet ported)")
		return false, nil
	})
}

// NewSignalOut is a stub (signal-cli requires process exec; Zig/Python path).
func NewSignalOut(account string) Channel {
	return newRESTOut(ChannelSignal, account, func(ctx context.Context, client *http.Client, message, target string) (bool, error) {
		_ = ctx
		_ = client
		_ = message
		_ = target
		log.Printf("signal: outbound stub (signal-cli exec stays outside Go boundaries)")
		return false, nil
	})
}
