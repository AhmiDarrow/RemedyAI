package gateway

import (
	"crypto"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"io"
	"log"
	"math/big"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

// jwksSource is one issuer family with its own key cache. Keys from one
// source are never used to verify tokens attributed to another.
type jwksSource struct {
	name string
	// openID metadata documents whose jwks_uri is followed (optional).
	openID []string
	// direct JWKS documents fetched after metadata discovery.
	direct []string
	// hosts allowed for any JWKS / metadata fetch of this source.
	hosts       map[string]struct{}
	hostSuffix  []string
	mu          sync.Mutex
	keys        map[string]*rsa.PublicKey
	fetchedAt   time.Time
	logPrefix   string
	verifyLabel string
}

var (
	// teamsJWKS covers Bot Framework / Azure AD issued tokens.
	teamsJWKS = &jwksSource{
		name: "teams",
		openID: []string{
			"https://login.botframework.com/v1/.well-known/openidconfiguration",
			"https://login.microsoftonline.com/botframework.com/v2.0/.well-known/openid-configuration",
		},
		direct: []string{
			"https://login.botframework.com/v1/.well-known/keys",
			"https://login.microsoftonline.com/common/discovery/v2.0/keys",
		},
		hosts: map[string]struct{}{
			"login.botframework.com":    {},
			"login.microsoftonline.com": {},
			"login.microsoft.com":       {},
			"login.windows.net":         {},
		},
		hostSuffix: []string{".microsoftonline.com", ".windows.net"},
		keys:       map[string]*rsa.PublicKey{},
		logPrefix:  "teams",
	}

	// googleChatJWKS covers tokens minted by the Google Chat service account.
	googleChatJWKS = &jwksSource{
		name:      "google_chat",
		direct:    []string{googleChatJWKSURL},
		hosts:     map[string]struct{}{"www.googleapis.com": {}},
		keys:      map[string]*rsa.PublicKey{},
		logPrefix: "google_chat",
	}

	jwksTTL  = 6 * time.Hour
	jwksHTTP = &http.Client{Timeout: 4 * time.Second}

	jwksTestMu   sync.Mutex
	jwksTestKeys = map[string]*rsa.PublicKey{}
)

// GoogleChatIssuer is the service account Google Chat signs webhook JWTs with.
const GoogleChatIssuer = "chat@system.gserviceaccount.com"

const googleChatJWKSURL = "https://www.googleapis.com/service_accounts/v1/jwk/" + GoogleChatIssuer

// ClearJWKSCache drops cached network keys (keeps test injects).
func ClearJWKSCache() {
	for _, src := range []*jwksSource{teamsJWKS, googleChatJWKS} {
		src.mu.Lock()
		src.keys = map[string]*rsa.PublicKey{}
		src.fetchedAt = time.Time{}
		src.mu.Unlock()
	}
}

// InjectTestRSAKey registers a public key for unit tests (no network).
func InjectTestRSAKey(kid, nB64, eB64 string) {
	if eB64 == "" {
		eB64 = "AQAB"
	}
	nBytes, err := b64URLDecode(nB64)
	if err != nil {
		return
	}
	eBytes, err := b64URLDecode(eB64)
	if err != nil {
		return
	}
	pub := &rsa.PublicKey{
		N: new(big.Int).SetBytes(nBytes),
		E: int(new(big.Int).SetBytes(eBytes).Int64()),
	}
	jwksTestMu.Lock()
	jwksTestKeys[kid] = pub
	jwksTestMu.Unlock()
}

// ClearTestRSAKeys removes injected test keys.
func ClearTestRSAKeys() {
	jwksTestMu.Lock()
	defer jwksTestMu.Unlock()
	jwksTestKeys = map[string]*rsa.PublicKey{}
}

func b64URLDecode(data string) ([]byte, error) {
	s := data
	switch len(s) % 4 {
	case 2:
		s += "=="
	case 3:
		s += "="
	}
	return base64.URLEncoding.DecodeString(s)
}

// DecodeJWTPayloadUnverified decodes the JWT payload without signature verify.
func DecodeJWTPayloadUnverified(token string) map[string]any {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return nil
	}
	raw, err := b64URLDecode(parts[1])
	if err != nil {
		return nil
	}
	var data map[string]any
	if err := json.Unmarshal(raw, &data); err != nil {
		return nil
	}
	return data
}

func decodeJWTHeader(token string) map[string]any {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return nil
	}
	raw, err := b64URLDecode(parts[0])
	if err != nil {
		return nil
	}
	var data map[string]any
	if err := json.Unmarshal(raw, &data); err != nil {
		return nil
	}
	return data
}

func (src *jwksSource) urlAllowed(raw string) bool {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || !strings.EqualFold(u.Scheme, "https") {
		return false
	}
	host := strings.ToLower(u.Hostname())
	if host == "" {
		return false
	}
	if _, ok := src.hosts[host]; ok {
		return true
	}
	for _, suf := range src.hostSuffix {
		if strings.HasSuffix(host, suf) {
			return true
		}
	}
	return false
}

func (src *jwksSource) getJSON(rawURL string) map[string]any {
	if !src.urlAllowed(rawURL) {
		log.Printf("%s: JWKS URL refused (host not allowlisted): %s", src.logPrefix, trimRunes(rawURL, 80))
		return nil
	}
	req, err := http.NewRequest(http.MethodGet, rawURL, nil)
	if err != nil {
		return nil
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "RemedyAI-JWT/1")
	resp, err := jwksHTTP.Do(req)
	if err != nil {
		log.Printf("%s: JWKS fetch failed %s: %v", src.logPrefix, trimRunes(rawURL, 80), err)
		return nil
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 2<<20))
	if err != nil {
		return nil
	}
	var data map[string]any
	if err := json.Unmarshal(raw, &data); err != nil {
		return nil
	}
	return data
}

// ingestJWKSet parses RSA keys from a JWK set into dst.
func ingestJWKSet(dst map[string]*rsa.PublicKey, doc map[string]any) int {
	keys, _ := doc["keys"].([]any)
	nAdded := 0
	for _, raw := range keys {
		jwk, _ := raw.(map[string]any)
		if jwk == nil {
			continue
		}
		if !strings.EqualFold(anyString(jwk["kty"]), "RSA") {
			continue
		}
		kid := strings.TrimSpace(anyString(jwk["kid"]))
		nB64 := strings.TrimSpace(anyString(jwk["n"]))
		eB64 := strings.TrimSpace(anyString(jwk["e"]))
		if eB64 == "" {
			eB64 = "AQAB"
		}
		if kid == "" || nB64 == "" {
			continue
		}
		nBytes, err := b64URLDecode(nB64)
		if err != nil {
			continue
		}
		eBytes, err := b64URLDecode(eB64)
		if err != nil {
			continue
		}
		dst[kid] = &rsa.PublicKey{
			N: new(big.Int).SetBytes(nBytes),
			E: int(new(big.Int).SetBytes(eBytes).Int64()),
		}
		nAdded++
	}
	return nAdded
}

// refresh fetches the JWKS documents for this source into its cache.
func (src *jwksSource) refresh(force bool) bool {
	now := time.Now()
	src.mu.Lock()
	if !force && len(src.keys) > 0 && now.Sub(src.fetchedAt) < jwksTTL {
		src.mu.Unlock()
		return true
	}
	src.mu.Unlock()

	uris := make([]string, 0, 8)
	for _, metaURL := range src.openID {
		meta := src.getJSON(metaURL)
		if meta == nil {
			continue
		}
		juri := strings.TrimSpace(anyString(meta["jwks_uri"]))
		if src.urlAllowed(juri) {
			uris = append(uris, juri)
		}
	}
	uris = append(uris, src.direct...)
	seen := map[string]struct{}{}
	docs := make([]map[string]any, 0, len(uris))
	for _, uri := range uris {
		if _, ok := seen[uri]; ok {
			continue
		}
		seen[uri] = struct{}{}
		if doc := src.getJSON(uri); doc != nil {
			docs = append(docs, doc)
		}
	}
	src.mu.Lock()
	defer src.mu.Unlock()
	total := 0
	for _, doc := range docs {
		total += ingestJWKSet(src.keys, doc)
	}
	if total > 0 {
		src.fetchedAt = time.Now()
		log.Printf("%s: JWKS loaded: %d RSA keys", src.logPrefix, len(src.keys))
		return true
	}
	return len(src.keys) > 0
}

func (src *jwksSource) lookup(kid string) *rsa.PublicKey {
	jwksTestMu.Lock()
	if k, ok := jwksTestKeys[kid]; ok {
		jwksTestMu.Unlock()
		return k
	}
	jwksTestMu.Unlock()
	src.mu.Lock()
	defer src.mu.Unlock()
	return src.keys[kid]
}

// verifyToken checks alg/kid, resolves the key (network when allowed) and
// verifies the RS256 signature.
func (src *jwksSource) verifyToken(token string, allowNetwork bool) bool {
	header := decodeJWTHeader(token)
	if header == nil {
		return false
	}
	alg := strings.ToUpper(anyString(header["alg"]))
	if alg != "RS256" {
		log.Printf("%s: JWT alg not RS256: %s", src.logPrefix, alg)
		return false
	}
	kid := strings.TrimSpace(anyString(header["kid"]))
	if kid == "" {
		log.Printf("%s: JWT missing kid", src.logPrefix)
		return false
	}
	key := src.lookup(kid)
	if key == nil && allowNetwork {
		src.refresh(false)
		key = src.lookup(kid)
		if key == nil {
			src.refresh(true)
			key = src.lookup(kid)
		}
	}
	if key == nil {
		log.Printf("%s: JWT kid not in JWKS: %s", src.logPrefix, trimRunes(kid, 40))
		return false
	}
	if !verifyRS256(token, key) {
		log.Printf("%s: JWT RS256 signature invalid", src.logPrefix)
		return false
	}
	return true
}

// RefreshJWKS fetches Bot Framework / Azure AD JWKS into the cache.
func RefreshJWKS(force bool) bool {
	return teamsJWKS.refresh(force)
}

func verifyRS256(token string, pub *rsa.PublicKey) bool {
	parts := strings.Split(token, ".")
	if len(parts) != 3 || pub == nil {
		return false
	}
	sig, err := b64URLDecode(parts[2])
	if err != nil {
		return false
	}
	signingInput := parts[0] + "." + parts[1]
	sum := sha256.Sum256([]byte(signingInput))
	return rsa.VerifyPKCS1v15(pub, crypto.SHA256, sum[:], sig) == nil
}

// VerifyJWTRS256JWKS verifies a Bot Framework JWT with RS256 against the
// Teams JWKS cache (network fetch when allowNetwork).
func VerifyJWTRS256JWKS(token string, allowNetwork bool) bool {
	return teamsJWKS.verifyToken(token, allowNetwork)
}

// VerifyGoogleChatJWT verifies a Google Chat webhook bearer token:
// RS256 signature against the chat service account JWKS, iss equal to that
// account, aud equal to the configured Cloud project number, and exp in the
// future (60 s skew). Fail closed on any missing claim.
func VerifyGoogleChatJWT(token, projectNumber string, now time.Time, allowNetwork bool) bool {
	projectNumber = strings.TrimSpace(projectNumber)
	if projectNumber == "" || strings.TrimSpace(token) == "" {
		return false
	}
	claims := DecodeJWTPayloadUnverified(token)
	if claims == nil {
		return false
	}
	if iss := strings.TrimSpace(anyString(claims["iss"])); iss != GoogleChatIssuer {
		log.Printf("google_chat: JWT iss rejected: %s", trimRunes(iss, 80))
		return false
	}
	audOK := false
	switch v := claims["aud"].(type) {
	case string:
		audOK = strings.TrimSpace(v) == projectNumber
	case []any:
		for _, a := range v {
			if anyString(a) == projectNumber {
				audOK = true
				break
			}
		}
	}
	if !audOK {
		log.Printf("google_chat: JWT aud does not match project_number")
		return false
	}
	if now.IsZero() {
		now = time.Now()
	}
	exp, ok := anyFloat(claims["exp"])
	if !ok || float64(now.Unix()) >= exp+60 {
		log.Printf("google_chat: JWT expired or missing exp")
		return false
	}
	if nbf, ok := anyFloat(claims["nbf"]); ok && float64(now.Unix())+60 < nbf {
		return false
	}
	return googleChatJWKS.verifyToken(token, allowNetwork)
}
