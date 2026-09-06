package gateway

import (
	"context"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

var bfServiceHostSuffixes = []string{
	".botframework.com",
	".botframework.us",
	".botframework.azure.cn",
}

var bfIssSuffixes = []string{
	"sts.windows.net",
	"login.microsoftonline.com",
	"login.microsoft.com",
	"api.botframework.com",
}

// TeamsChannel: Bot Framework webhook inbound + connector outbound.
type TeamsChannel struct {
	gateway     *Gateway
	appID       string
	appPassword string
	tenantID    string
	allowed     map[string]struct{}
	allowAll    bool
	client      *http.Client

	mu                 sync.Mutex
	running            bool
	token              string
	tokenExp           time.Time
	// serviceURLs maps conversation id → last trusted Bot Framework serviceUrl.
	// Replies must use the conversation's own host (regions / tenants differ).
	serviceURLs        map[string]string
	lastConversationID string
}

// TeamsConfig configures a Teams Bot Framework adapter.
type TeamsConfig struct {
	AppID       string
	AppPassword string
	TenantID    string
	AllowIDs    any
	AllowAll    bool
}

// NewTeams builds a Teams channel bound to a gateway hub.
func NewTeams(g *Gateway, cfg TeamsConfig) *TeamsChannel {
	tenant := strings.TrimSpace(cfg.TenantID)
	if tenant == "" {
		tenant = "botframework.com"
	}
	return &TeamsChannel{
		gateway:     g,
		appID:       strings.TrimSpace(cfg.AppID),
		appPassword: strings.TrimSpace(cfg.AppPassword),
		tenantID:    tenant,
		allowed:     ParseIDs(cfg.AllowIDs),
		allowAll:    cfg.AllowAll,
		client:      &http.Client{Timeout: 30 * time.Second},
		serviceURLs: make(map[string]string),
	}
}

func (c *TeamsChannel) Kind() ChannelKind { return ChannelTeams }

func (c *TeamsChannel) Running() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.running
}

func (c *TeamsChannel) Start(ctx context.Context) error {
	_ = ctx
	c.mu.Lock()
	c.running = true
	c.mu.Unlock()
	if c.appID != "" && c.appPassword != "" {
		log.Printf("teams: active (inbound=webhook, outbound=connector)")
	} else {
		log.Printf("teams: stub mode (missing app_id/password)")
	}
	return nil
}

func (c *TeamsChannel) Stop(ctx context.Context) error {
	_ = ctx
	c.mu.Lock()
	c.running = false
	c.mu.Unlock()
	return nil
}

func isAllowedBotFrameworkServiceURL(raw string) bool {
	u := strings.TrimSpace(raw)
	if !strings.HasPrefix(strings.ToLower(u), "https://") {
		return false
	}
	parsed, err := url.Parse(u)
	if err != nil {
		return false
	}
	host := strings.ToLower(parsed.Hostname())
	if host == "" {
		return false
	}
	if host == "smba.trafficmanager.net" || host == "directline.botframework.com" {
		return true
	}
	for _, s := range bfServiceHostSuffixes {
		if host == strings.TrimPrefix(s, ".") || strings.HasSuffix(host, s) {
			return true
		}
	}
	return false
}

// JWTClaimsStructurallyValid fail-closed aud/exp/nbf/iss checks (no crypto).
func JWTClaimsStructurallyValid(claims map[string]any, appID string, now time.Time) bool {
	appID = strings.TrimSpace(appID)
	if appID == "" || claims == nil {
		return false
	}
	var auds []string
	switch v := claims["aud"].(type) {
	case string:
		if strings.TrimSpace(v) != "" {
			auds = []string{v}
		}
	case []any:
		for _, a := range v {
			if s := anyString(a); s != "" {
				auds = append(auds, s)
			}
		}
	}
	if len(auds) == 0 {
		return false
	}
	okAud := false
	for _, a := range auds {
		if a == appID || a == "api://"+appID {
			okAud = true
			break
		}
	}
	if !okAud {
		return false
	}

	ts := now.Unix()
	if now.IsZero() {
		ts = time.Now().Unix()
	}
	exp, ok := anyFloat(claims["exp"])
	if !ok {
		return false
	}
	if float64(ts) >= exp+60 {
		return false
	}
	if nbf, ok := anyFloat(claims["nbf"]); ok {
		if float64(ts)+60 < nbf {
			return false
		}
	}
	iss := strings.TrimSpace(anyString(claims["iss"]))
	if iss != "" {
		host := ""
		if parsed, err := url.Parse(iss); err == nil {
			host = strings.ToLower(parsed.Hostname())
		}
		if host == "" {
			host = strings.ToLower(strings.Split(iss, "/")[0])
		}
		okIss := false
		for _, s := range bfIssSuffixes {
			if host == s || strings.HasSuffix(host, "."+s) {
				okIss = true
				break
			}
		}
		if !okIss {
			return false
		}
	}
	return true
}

func anyFloat(v any) (float64, bool) {
	switch t := v.(type) {
	case nil:
		return 0, false
	case bool:
		return 0, false
	case string:
		if strings.TrimSpace(t) == "" {
			return 0, false
		}
		var n json.Number = json.Number(strings.TrimSpace(t))
		f, err := n.Float64()
		return f, err == nil
	case json.Number:
		f, err := t.Float64()
		return f, err == nil
	case float64:
		return t, true
	case float32:
		return float64(t), true
	case int:
		return float64(t), true
	case int64:
		return float64(t), true
	default:
		return 0, false
	}
}

// VerifyInboundAuth gates Bot Framework JWT (claims + RS256 JWKS).
func (c *TeamsChannel) VerifyInboundAuth(authorization string) bool {
	if envTruthy("REMEDY_TEAMS_SKIP_JWT") {
		return true
	}
	if c.appID == "" {
		return false
	}
	auth := strings.TrimSpace(authorization)
	if !strings.HasPrefix(strings.ToLower(auth), "bearer ") {
		log.Printf("teams: webhook missing Bearer Authorization")
		return false
	}
	token := strings.TrimSpace(auth[7:])
	claims := DecodeJWTPayloadUnverified(token)
	if claims == nil {
		log.Printf("teams: webhook Authorization is not a JWT")
		return false
	}
	if !JWTClaimsStructurallyValid(claims, c.appID, time.Now()) {
		log.Printf("teams: JWT claim check failed (aud/exp/nbf/iss fail-closed)")
		return false
	}
	if envTruthy("REMEDY_TEAMS_SKIP_JWKS") {
		log.Printf("teams: JWT JWKS signature skipped (REMEDY_TEAMS_SKIP_JWKS)")
		return true
	}
	if !VerifyJWTRS256JWKS(token, true) {
		log.Printf("teams: JWT RS256/JWKS verification failed")
		return false
	}
	return true
}

func (c *TeamsChannel) bearer(ctx context.Context) string {
	c.mu.Lock()
	if c.token != "" && time.Now().Before(c.tokenExp.Add(-60*time.Second)) {
		tok := c.token
		c.mu.Unlock()
		return tok
	}
	c.mu.Unlock()

	form := url.Values{}
	form.Set("grant_type", "client_credentials")
	form.Set("client_id", c.appID)
	form.Set("client_secret", c.appPassword)
	form.Set("scope", "https://api.botframework.com/.default")
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		"https://login.microsoftonline.com/"+c.tenantID+"/oauth2/v2.0/token",
		strings.NewReader(form.Encode()))
	if err != nil {
		return ""
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	resp, err := c.client.Do(req)
	if err != nil {
		log.Printf("teams: token request failed: %v", err)
		return ""
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	var body map[string]any
	if err := json.Unmarshal(raw, &body); err != nil {
		return ""
	}
	tok := anyString(body["access_token"])
	if tok == "" {
		log.Printf("teams: token failed: %s", trimRunes(string(raw), 160))
		return ""
	}
	expiresIn := 3600.0
	if v, ok := anyFloat(body["expires_in"]); ok {
		expiresIn = v
	}
	c.mu.Lock()
	c.token = tok
	c.tokenExp = time.Now().Add(time.Duration(expiresIn) * time.Second)
	c.mu.Unlock()
	return tok
}

func (c *TeamsChannel) conversationRef(target string) (conv, service string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	conv = strings.TrimSpace(target)
	if conv == "" {
		conv = c.lastConversationID
	}
	if conv != "" && c.serviceURLs != nil {
		service = strings.TrimRight(c.serviceURLs[conv], "/")
	}
	return conv, service
}

func (c *TeamsChannel) Send(ctx context.Context, message, target string) (bool, error) {
	if c.appID == "" || c.appPassword == "" {
		return true, nil
	}
	conv, service := c.conversationRef(target)
	if conv == "" || service == "" {
		log.Printf("teams: send: no conversation reference yet")
		return false, nil
	}
	token := c.bearer(ctx)
	if token == "" {
		return false, nil
	}
	status, err := jsonPOST(ctx, c.client,
		service+"/v3/conversations/"+conv+"/activities",
		map[string]string{"Authorization": "Bearer " + token},
		map[string]any{"type": "message", "text": trimRunes(message, 28000)},
	)
	if err != nil {
		log.Printf("teams: send failed: %v", err)
		return false, err
	}
	return status == 200 || status == 201 || status == 202, nil
}

// SendTyping posts a typing activity when a conversation reference exists.
func (c *TeamsChannel) SendTyping(ctx context.Context, target string) error {
	conv, service := c.conversationRef(target)
	if conv == "" || service == "" || c.appID == "" || c.appPassword == "" {
		return nil
	}
	token := c.bearer(ctx)
	if token == "" {
		return nil
	}
	_, _ = jsonPOST(ctx, c.client,
		service+"/v3/conversations/"+conv+"/activities",
		map[string]string{"Authorization": "Bearer " + token},
		map[string]any{"type": "typing"},
	)
	return nil
}

// HandleActivity processes a Bot Framework activity JSON from the webhook.
func (c *TeamsChannel) HandleActivity(ctx context.Context, activity map[string]any) bool {
	if anyString(activity["type"]) != "message" {
		return false
	}
	text := strings.TrimSpace(anyString(activity["text"]))
	if text == "" {
		return false
	}
	conv, _ := activity["conversation"].(map[string]any)
	if conv == nil {
		conv = map[string]any{}
	}
	convID := anyString(conv["id"])
	from, _ := activity["from"].(map[string]any)
	if from == nil {
		from = map[string]any{}
	}
	fromID := anyString(from["id"])
	serviceURL := strings.TrimRight(anyString(activity["serviceUrl"]), "/")
	if serviceURL != "" && isAllowedBotFrameworkServiceURL(serviceURL) {
		if convID != "" {
			c.mu.Lock()
			if c.serviceURLs == nil {
				c.serviceURLs = make(map[string]string)
			}
			c.serviceURLs[convID] = serviceURL
			c.lastConversationID = convID
			c.mu.Unlock()
		}
	} else if serviceURL != "" {
		log.Printf("teams: ignored untrusted serviceUrl host: %s", trimRunes(serviceURL, 120))
		c.mu.Lock()
		have := convID != "" && c.serviceURLs != nil && c.serviceURLs[convID] != ""
		c.mu.Unlock()
		if !have {
			return false
		}
	}
	if convID != "" {
		c.mu.Lock()
		c.lastConversationID = convID
		c.mu.Unlock()
	}
	if len(c.allowed) == 0 && !c.allowAll {
		log.Printf("teams: ignore (empty allowlist, allow_all=false) conv=%s", firstNonEmpty(convID, fromID))
		return false
	}
	if !IsAllowed(c.allowed, c.allowAll, convID, fromID) {
		return false
	}
	chatID := convID
	if chatID == "" {
		chatID = fromID
	}
	sourceID := fromID
	if sourceID == "" {
		sourceID = convID
	}
	username := anyString(from["name"])
	ev := Event{
		ID:        NewEventID(),
		Kind:      EventMessage,
		Channel:   ChannelTeams,
		SourceID:  sourceID,
		SessionID: chatID,
		Payload: map[string]any{
			"message":     text,
			"chat_id":     chatID,
			"channel_id":  chatID,
			"user_id":     fromID,
			"username":    username,
			"service_url": serviceURL,
		},
		Raw: trimRunes(text, 500),
		At:  time.Now().UTC(),
	}
	if c.gateway != nil && c.gateway.Running() {
		c.gateway.Enqueue(ev)
	} else if c.gateway != nil {
		_ = c.gateway.Emit(ctx, ev)
	}
	return true
}
