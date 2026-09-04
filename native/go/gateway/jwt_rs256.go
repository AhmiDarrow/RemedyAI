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

// Bot Framework / Azure AD OpenID metadata (JWKS discovery).
var defaultOpenIDURLs = []string{
	"https://login.botframework.com/v1/.well-known/openidconfiguration",
	"https://login.microsoftonline.com/botframework.com/v2.0/.well-known/openid-configuration",
}

var jwksHosts = map[string]struct{}{
	"login.botframework.com":    {},
	"login.microsoftonline.com": {},
	"login.microsoft.com":       {},
	"login.windows.net":         {},
}

var (
	jwksMu        sync.Mutex
	jwksKeys      = map[string]*rsa.PublicKey{}
	jwksFetchedAt time.Time
	jwksTTL       = 6 * time.Hour
	jwksTestKeys  = map[string]*rsa.PublicKey{}
	jwksHTTP      = &http.Client{Timeout: 4 * time.Second}
)

// ClearJWKSCache drops cached network keys (keeps test injects).
func ClearJWKSCache() {
	jwksMu.Lock()
	defer jwksMu.Unlock()
	jwksKeys = map[string]*rsa.PublicKey{}
	jwksFetchedAt = time.Time{}
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
	jwksMu.Lock()
	jwksTestKeys[kid] = pub
	jwksMu.Unlock()
}

// ClearTestRSAKeys removes injected test keys.
func ClearTestRSAKeys() {
	jwksMu.Lock()
	defer jwksMu.Unlock()
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

func jwksURLAllowed(raw string) bool {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || !strings.EqualFold(u.Scheme, "https") {
		return false
	}
	host := strings.ToLower(u.Hostname())
	if host == "" {
		return false
	}
	if _, ok := jwksHosts[host]; ok {
		return true
	}
	return strings.HasSuffix(host, ".microsoftonline.com") || strings.HasSuffix(host, ".windows.net")
}

func httpGetJSON(rawURL string) map[string]any {
	if !jwksURLAllowed(rawURL) {
		log.Printf("teams: JWKS URL refused (host not allowlisted): %s", trimRunes(rawURL, 80))
		return nil
	}
	req, err := http.NewRequest(http.MethodGet, rawURL, nil)
	if err != nil {
		return nil
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "RemedyAI-TeamsJWT/1")
	resp, err := jwksHTTP.Do(req)
	if err != nil {
		log.Printf("teams: JWKS fetch failed %s: %v", trimRunes(rawURL, 80), err)
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

func ingestJWKSet(doc map[string]any) int {
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
		jwksKeys[kid] = &rsa.PublicKey{
			N: new(big.Int).SetBytes(nBytes),
			E: int(new(big.Int).SetBytes(eBytes).Int64()),
		}
		nAdded++
	}
	return nAdded
}

// RefreshJWKS fetches Bot Framework / Azure AD JWKS into the cache.
func RefreshJWKS(force bool) bool {
	now := time.Now()
	jwksMu.Lock()
	if !force && len(jwksKeys) > 0 && now.Sub(jwksFetchedAt) < jwksTTL {
		jwksMu.Unlock()
		return true
	}
	jwksMu.Unlock()

	jwksURIs := make([]string, 0, 8)
	for _, metaURL := range defaultOpenIDURLs {
		meta := httpGetJSON(metaURL)
		if meta == nil {
			continue
		}
		juri := strings.TrimSpace(anyString(meta["jwks_uri"]))
		if jwksURLAllowed(juri) {
			jwksURIs = append(jwksURIs, juri)
		}
	}
	jwksURIs = append(jwksURIs,
		"https://login.botframework.com/v1/.well-known/keys",
		"https://login.microsoftonline.com/common/discovery/v2.0/keys",
	)
	seen := map[string]struct{}{}
	docs := make([]map[string]any, 0, len(jwksURIs))
	for _, uri := range jwksURIs {
		if _, ok := seen[uri]; ok {
			continue
		}
		seen[uri] = struct{}{}
		if doc := httpGetJSON(uri); doc != nil {
			docs = append(docs, doc)
		}
	}
	jwksMu.Lock()
	defer jwksMu.Unlock()
	total := 0
	for _, doc := range docs {
		total += ingestJWKSet(doc)
	}
	if total > 0 {
		jwksFetchedAt = time.Now()
		log.Printf("teams: JWKS loaded: %d RSA keys", len(jwksKeys))
		return true
	}
	return len(jwksKeys) > 0
}

func lookupJWKSKey(kid string) *rsa.PublicKey {
	jwksMu.Lock()
	defer jwksMu.Unlock()
	if k, ok := jwksTestKeys[kid]; ok {
		return k
	}
	return jwksKeys[kid]
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

// VerifyJWTRS256JWKS verifies a JWT with RS256 against cached/fetched JWKS.
func VerifyJWTRS256JWKS(token string, allowNetwork bool) bool {
	header := decodeJWTHeader(token)
	if header == nil {
		return false
	}
	alg := strings.ToUpper(anyString(header["alg"]))
	if alg != "RS256" {
		log.Printf("teams: JWT alg not RS256: %s", alg)
		return false
	}
	kid := strings.TrimSpace(anyString(header["kid"]))
	if kid == "" {
		log.Printf("teams: JWT missing kid")
		return false
	}
	key := lookupJWKSKey(kid)
	if key == nil && allowNetwork {
		RefreshJWKS(false)
		key = lookupJWKSKey(kid)
		if key == nil {
			RefreshJWKS(true)
			key = lookupJWKSKey(kid)
		}
	}
	if key == nil {
		log.Printf("teams: JWT kid not in JWKS: %s", trimRunes(kid, 40))
		return false
	}
	if !verifyRS256(token, key) {
		log.Printf("teams: JWT RS256 signature invalid")
		return false
	}
	return true
}
