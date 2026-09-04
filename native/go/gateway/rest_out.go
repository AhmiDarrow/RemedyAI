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
// WhatsApp/Teams/Google Chat own dedicated webhook adapters;
// Slack/Mattermost/Matrix own poll/WS adapters. Signal owns signal.go.
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
	log.Printf("%s: outbound-ready (webhook/exec inbound outside this adapter)", c.kind)
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
