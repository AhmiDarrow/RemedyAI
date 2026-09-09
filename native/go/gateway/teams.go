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

// BotFrameworkIssuer is the only issuer accepted for production channel traffic.
const BotFrameworkIssuer = "https://api.botframework.com"

// bfDevIssuerHosts are additionally accepted only with teams_dev_emulator.
var bfDevIssuerHosts = []string{
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
	access      Access
	devEmulator bool
	client      *http.Client

	mu       sync.Mutex
	running  bool
	token    string
	tokenExp time.Time
	// serviceURLs maps conversation id to the last trusted Bot Framework
	// serviceUrl. Replies must use the conversation host (regions differ).
	serviceURLs        map[string]string
	lastConversationID string
}

// TeamsConfig configures a Teams Bot Framework adapter. AllowIDs holds AAD
// user ids (from.id / aadObjectId); conversation ids are scope only.
// DevEmulator widens the accepted token issuers for the Bot Framework
// Emulator and must stay off in production.
type TeamsConfig struct {
	AppID       string
	AppPassword string
	TenantID    string
	AllowIDs    any
	AllowAll    bool
	DevEmulator bool
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
		access:      NewAccess(cfg.AllowIDs, cfg.AllowAll),
		devEmulator: cfg.DevEmulator,
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
		log.Printf("teams: active (inbound=webhook, outbound=connector, dev_emulator=%v)", c.devEmulator)
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

// JWTClaimsStructurallyValid applies fail-closed aud / exp / nbf / iss checks
// (no crypto). iss is required. In production only BotFrameworkIssuer is
// accepted; devEmulator additionally admits the Azure AD / emulator issuers.
func JWTClaimsStructurallyValid(claims map[string]any, appID string, now time.Time, devEmulator bool) bool {
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

	if now.IsZero() {
		now = time.Now()
	}
	ts := now.Unix()
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
	iss := strings.TrimRight(strings.TrimSpace(anyString(claims["iss"])), "/")
	if iss == "" {
		return false
	}
	if iss == BotFrameworkIssuer {
		return true
	}
	if !devEmulator {
		return false
	}
	host := ""
	if parsed, err := url.Parse(iss); err == nil {
		host = strings.ToLower(parsed.Hostname())
	}
	if host == "" {
		host = strings.ToLower(strings.Split(iss, "/")[0])
	}
	for _, s := range bfDevIssuerHosts {
		if host == s || strings.HasSuffix(host, "."+s) {
			return true
		}
	}
	return false
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

// VerifyInboundAuth gates the Bot Framework JWT (claims + RS256 JWKS) and
// returns the verified claims for HandleActivity. Claims are nil only when a
// remedydev build skips verification.
func (c *TeamsChannel) VerifyInboundAuth(authorization string) (map[string]any, bool) {
	if devSkipTeamsJWT() {
		return nil, true
	}
	if c.appID == "" {
		return nil, false
	}
	auth := strings.TrimSpace(authorization)
	if !strings.HasPrefix(strings.ToLower(auth), "bearer ") {
		log.Printf("teams: webhook missing Bearer Authorization")
		return nil, false
	}
	token := strings.TrimSpace(auth[7:])
	claims := DecodeJWTPayloadUnverified(token)
	if claims == nil {
		log.Printf("teams: webhook Authorization is not a JWT")
		return nil, false
	}
	if !JWTClaimsStructurallyValid(claims, c.appID, time.Now(), c.devEmulator) {
		log.Printf("teams: JWT claim check failed (aud/exp/nbf/iss fail-closed)")
		return nil, false
	}
	if devSkipTeamsJWKS() {
		return claims, true
	}
	if !VerifyJWTRS256JWKS(token, true) {
		log.Printf("teams: JWT RS256/JWKS verification failed")
		return nil, false
	}
	return claims, true
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

// serviceURLMatchesClaim enforces the Bot Framework rule that the token
// serviceurl claim equals the activity serviceUrl.
func serviceURLMatchesClaim(claims map[string]any, serviceURL string) bool {
	want := strings.ToLower(strings.TrimRight(strings.TrimSpace(anyString(claims["serviceurl"])), "/"))
	got := strings.ToLower(strings.TrimRight(strings.TrimSpace(serviceURL), "/"))
	return want != "" && got != "" && want == got
}

// HandleActivity processes a Bot Framework activity JSON from the webhook.
// claims are the verified JWT claims from VerifyInboundAuth; nil is accepted
// only when a remedydev build skipped verification. Every check (service
// URL, claim binding, allowlist) runs before any state is mutated.
func (c *TeamsChannel) HandleActivity(ctx context.Context, activity map[string]any, claims map[string]any) bool {
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
	aadID := anyString(from["aadObjectId"])
	serviceURL := strings.TrimRight(anyString(activity["serviceUrl"]), "/")
	if serviceURL == "" || !isAllowedBotFrameworkServiceURL(serviceURL) {
		log.Printf("teams: ignored untrusted serviceUrl: %s", trimRunes(serviceURL, 120))
		return false
	}
	if claims == nil {
		if !devSkipTeamsJWT() {
			log.Printf("teams: activity without verified claims rejected")
			return false
		}
	} else if !serviceURLMatchesClaim(claims, serviceURL) {
		log.Printf("teams: serviceurl claim does not match activity serviceUrl")
		return false
	}

	identity := fromID
	ok, reason := c.access.Permit(identity, convID)
	if !ok && aadID != "" {
		ok, reason = c.access.Permit(aadID, convID)
	}
	if !ok {
		logDeny(ChannelTeams, reason, fromID, convID)
		c.gateway.recordDenied(ChannelTeams, reason, fromID, convID)
		return false
	}

	if convID != "" {
		c.mu.Lock()
		if c.serviceURLs == nil {
			c.serviceURLs = make(map[string]string)
		}
		c.serviceURLs[convID] = serviceURL
		c.lastConversationID = convID
		c.mu.Unlock()
	}
	chatID := convID
	if chatID == "" {
		chatID = fromID
	}
	username := anyString(from["name"])
	ev := Event{
		ID:        NewEventID(),
		Kind:      EventMessage,
		Channel:   ChannelTeams,
		SourceID:  fromID,
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
